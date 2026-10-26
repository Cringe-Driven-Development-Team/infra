"""Deployment checks without SSH, production secrets, or a Docker daemon.

Run with the deployment Python environment and COMPOSE_CLI pointing to a Compose
command: COMPOSE_CLI='docker compose' python -m unittest discover -s tests -v
"""

import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

from ansible.parsing.vault import VaultLib, VaultSecret
from ansible.plugins.filter.core import to_bool, to_json
from jinja2 import Environment, StrictUndefined
import yaml

ANSIBLE = Path(__file__).resolve().parents[1]
# Секреты BFF в формате, который принимает validate.yml
# Образ BFF по digest и полный SHA релиза в формате, который принимает deploy-bff.yml (infra#74)
BFF_REF = "ghcr.io/cringe-driven-development-team/bff@sha256:" + "a" * 64
BFF_SHA = "b" * 40
BFF_VAULT = b"vault_session_key: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\nvault_bff_api_key: ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff\n"


class DeploymentChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cdd-deploy-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = dict(os.environ)
        # Do not inherit real registry credentials into tests.
        self.environment["GHCR_USERNAME"] = "test-ghcr-user"
        self.environment["GHCR_TOKEN"] = "fake-ghcr-token-test-only"
        self.environment["ANSIBLE_LOCAL_TEMP"] = str(self.root / "ansible-tmp")
        self.environment["ANSIBLE_REMOTE_TEMP"] = str(self.root / "remote-tmp")
        self.environment.pop("ANSIBLE_VAULT_PASSWORD", None)
        self.environment.pop("ANSIBLE_VAULT_PASSWORD_FILE", None)
        self.inventory = self.root / "ci.yml"
        self.inventory.write_text(
            yaml.safe_dump(
                {
                    "all": {
                        "children": {
                            "gateway": {
                                "hosts": {
                                    "test-vps": {
                                        "ansible_host": "127.0.0.1",
                                        "ansible_connection": "local",
                                    }
                                }
                            }
                        }
                    }
                }
            )
        )
        variables = self.root / "group_vars/all"
        variables.mkdir(parents=True)
        (variables / "vars.yml").write_text(
            'postgres_password: "{{ vault_postgres_password }}"\n'
            'jwt_secret: "{{ vault_jwt_secret }}"\n'
            'csrf_secret: "{{ vault_csrf_secret }}"\n'
            'redis_password: "{{ vault_redis_password }}"\n'
            'session_key: "{{ vault_session_key }}"\n'
            'bff_api_key: "{{ vault_bff_api_key }}"\n'
            'notebooks_s3_access_key: fake-key\nnotebooks_s3_secret_key: fake-secret\n'
            's3_region: ru-7\ns3_endpoint: https://s3.example.org\n'
            'notebooks_bucket: notebooks\navatars_bucket: avatars\n'
            'avatars_public_domain: avatars.example.org\n'
            'app_cors_allowed_origins: https://app.example.org\n'
            'app_backend_split: false\napp_backend_address: ""\n'
        )
        password = b"test-vault-password-only"
        vault = VaultLib([("default", VaultSecret(password))])
        (variables / "vault.yml").write_bytes(
            vault.encrypt(
                b"vault_postgres_password: fake-database-password\nvault_jwt_secret: fake-jwt-secret\n"
                b"vault_csrf_secret: fake-csrf-secret\nvault_redis_password: fake-redis-password-0123\n"
                + BFF_VAULT
            )
        )
        password_file = self.root / "vault-pass"
        password_file.write_bytes(password)
        password_file.chmod(0o600)
        self.environment["ANSIBLE_VAULT_PASSWORD_FILE"] = str(password_file)

    def validate(self, tag=None, playbook="deploy-backend.yml", variable="backend_image_tag",
                 extra=None):
        command = [
            shutil.which("ansible-playbook") or "ansible-playbook",
            "-i",
            str(self.inventory),
            str(ANSIBLE / playbook),
            "--tags",
            "validation",
        ]
        if tag is not None:
            command += ["-e", json.dumps({variable: tag})]
        if extra:
            command += ["-e", json.dumps(extra)]
        return subprocess.run(
            command,
            cwd=ANSIBLE,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_valid_tag_and_vault(self):
        result = self.validate("sha-1234567")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_or_invalid_tag_is_rejected(self):
        for tag in [None, "", "main", "sha-xyz", "sha-1234567;echo bad"]:
            with self.subTest(tag=tag):
                result = self.validate(tag)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("sha-", result.stdout + result.stderr)

    def validate_bff(self, ref=BFF_REF, sha=BFF_SHA, extra=None):
        values = {key: value for key, value in
                  {"bff_image_ref": ref, "bff_release_sha": sha}.items() if value is not None}
        return self.validate(None, "deploy-bff.yml", extra=dict(values, **(extra or {})))

    def test_bff_deploy_requires_a_digest_and_a_full_release_sha(self):
        result = self.validate_bff()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        image = "ghcr.io/cringe-driven-development-team/bff"
        for ref in [None, "", image + ":sha-1234567", image + ":latest", image,
                    image + "@sha256:" + "a" * 63, image + "@sha256:" + "A" * 64,
                    "ghcr.io/cringe-driven-development-team/backend@sha256:" + "a" * 64,
                    "docker.io/library/bff@sha256:" + "a" * 64,
                    image + "@sha256:" + "a" * 64 + ";echo bad"]:
            with self.subTest(ref=ref):
                result = self.validate_bff(ref=ref)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("bff_image_ref=" + image + "@sha256:", result.stdout + result.stderr)
        for sha in [None, "", "b" * 7, "sha-" + "b" * 40, "B" * 40, "b" * 41]:
            with self.subTest(sha=sha):
                result = self.validate_bff(sha=sha)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("bff_release_sha=<40 hex>", result.stdout + result.stderr)
        # package не подменить переопределением app_bff_image
        alpine = "docker.io/library/alpine"
        result = self.validate_bff(ref=alpine + "@sha256:" + "a" * 64, extra={"app_bff_image": alpine})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("app_bff_image is not overridable", result.stdout + result.stderr)
        # тег бэка необязателен, но если передан — проверяется
        result = self.validate_bff(extra={"backend_image_tag": "main"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backend_image_tag=sha-", result.stdout + result.stderr)

    def test_bff_deploy_needs_no_registry_credentials(self):
        for name in ["GHCR_USERNAME", "GHCR_TOKEN"]:
            self.environment.pop(name)
        result = self.validate_bff()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_backend_deploy_does_not_change_the_bff(self):
        result = self.validate("sha-1234567", extra={"bff_image_ref": BFF_REF,
                                                      "bff_release_sha": BFF_SHA})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only by deploy-bff.yml", result.stdout + result.stderr)

    def test_invalid_bff_secrets_are_rejected_without_leaks(self):
        vault = VaultLib([("default", VaultSecret(b"test-vault-password-only"))])
        base = (b"vault_postgres_password: fake-database-password\nvault_jwt_secret: fake-jwt-secret\n"
                b"vault_csrf_secret: fake-csrf-secret\nvault_redis_password: fake-redis-password-0123\n")
        for secrets in [
            b"vault_session_key: c2hvcnQ=\nvault_bff_api_key: " + b"f" * 64 + b"\n",
            b"vault_session_key: " + b"A" * 43 + b"=\nvault_bff_api_key: short-leaky-key\n",
        ]:
            with self.subTest(secrets=secrets):
                (self.root / "group_vars/all/vault.yml").write_bytes(vault.encrypt(base + secrets))
                result = self.validate_bff()
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("vault_session_key must be base64", output)
                self.assertNotIn("short-leaky-key", output)

    def test_deployment_keeps_the_other_deployed_image(self):
        playbook = self.root / "tags-test.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": "Exercise the real tag selection", "hosts": "gateway", "gather_facts": False,
            "vars": {"app_compose_dir": str(self.root),
                     "app_bff_image": "ghcr.io/cringe-driven-development-team/bff",
                     "app_backend_split": False,
                     "ansible_python_interpreter": sys.executable},
            "tasks": [
                {"name": "Choose tags",
                 "ansible.builtin.import_tasks": str(ANSIBLE / "roles/app/tasks/tags.yml")},
                {"name": "Show tags", "ansible.builtin.debug": {
                    "msg": "TAGS {{ app_backend_tag }} {{ app_bff_ref | default('-', true) }}"
                           " {{ app_bff_release | default('-', true) }}"}},
            ],
        }]))

        def run(env_file, **tags):
            env_path = self.root / ".env"
            env_path.unlink(missing_ok=True)
            if env_file is not None:
                env_path.write_text(env_file)
            command = [shutil.which("ansible-playbook") or "ansible-playbook",
                       "-i", str(self.inventory), str(playbook)]
            if tags:
                command += ["-e", json.dumps(tags)]
            return subprocess.run(command, cwd=ANSIBLE, env=self.environment,
                                  capture_output=True, text=True, timeout=30)

        old_ref = BFF_REF.replace("a" * 64, "c" * 64)
        old_sha = "d" * 40
        deployed = (f"BACKEND_IMAGE_TAG=sha-aaaaaaa\nBFF_IMAGE_REF={old_ref}\n"
                    f"BFF_RELEASE_SHA={old_sha}\nJWT_SECRET=\"leaky\"\n")
        cases = [
            (deployed, {"bff_image_ref": BFF_REF, "bff_release_sha": BFF_SHA},
             f"TAGS sha-aaaaaaa {BFF_REF} {BFF_SHA}"),
            (deployed, {"backend_image_tag": "sha-ddddddd"},
             f"TAGS sha-ddddddd {old_ref} {old_sha}"),
            ("BACKEND_IMAGE_TAG=sha-aaaaaaa\n", {"backend_image_tag": "sha-ddddddd"},
             "TAGS sha-ddddddd - -"),
            (None, {"backend_image_tag": "sha-ddddddd"}, "TAGS sha-ddddddd - -"),
        ]
        for env_file, tags, expected in cases:
            with self.subTest(tags=tags, deployed=env_file):
                result = run(env_file, **tags)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                self.assertIn(expected, output)
                self.assertNotIn("leaky", output)
        # BFF без выкаченного бэка не выкатывается
        result = run(None, bff_image_ref=BFF_REF, bff_release_sha=BFF_SHA)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deploy-backend.yml", result.stdout + result.stderr)

    def test_missing_registry_credentials_are_rejected_without_leaks(self):
        for name in ["GHCR_USERNAME", "GHCR_TOKEN"]:
            with self.subTest(name=name):
                previous = self.environment.pop(name)
                result = self.validate("sha-1234567")
                self.environment[name] = previous
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Set " + name, result.stdout + result.stderr)
                self.assertNotIn("fake-ghcr-token-test-only", result.stdout + result.stderr)

    def test_multiple_hosts_are_rejected(self):
        data = yaml.safe_load(self.inventory.read_text())
        data["all"]["children"]["gateway"]["hosts"]["second-vps"] = {
            "ansible_host": "127.0.0.2",
            "ansible_connection": "local",
        }
        self.inventory.write_text(yaml.safe_dump(data))
        result = self.validate("sha-1234567")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one gateway", result.stdout + result.stderr)

    def test_missing_or_wrong_vault_password_is_rejected(self):
        for password in [None, b"wrong-password"]:
            with self.subTest(password_present=password is not None):
                if password is None:
                    self.environment.pop("ANSIBLE_VAULT_PASSWORD_FILE", None)
                else:
                    path = self.root / "wrong-pass"
                    path.write_bytes(password)
                    self.environment["ANSIBLE_VAULT_PASSWORD_FILE"] = str(path)
                result = self.validate("sha-1234567")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("decrypt", (result.stdout + result.stderr).lower())

    def test_missing_csrf_secret_is_rejected(self):
        vault = VaultLib([("default", VaultSecret(b"test-vault-password-only"))])
        (self.root / "group_vars/all/vault.yml").write_bytes(
            vault.encrypt(
                b"vault_postgres_password: fake-database-password\nvault_jwt_secret: fake-jwt-secret\n"
                b"vault_redis_password: fake-redis-password-0123\n" + BFF_VAULT
            )
        )
        result = self.validate("sha-1234567")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vault_csrf_secret", result.stdout + result.stderr)

    def test_unsafe_redis_password_is_rejected(self):
        vault = VaultLib([("default", VaultSecret(b"test-vault-password-only"))])
        for password in [b"short", b"has space in redis password", b"quote'in-redis-password"]:
            with self.subTest(password=password):
                (self.root / "group_vars/all/vault.yml").write_bytes(
                    vault.encrypt(
                        b"vault_postgres_password: fake-database-password\nvault_jwt_secret: fake-jwt-secret\n"
                        b"vault_csrf_secret: fake-csrf-secret\nvault_redis_password: \"" + password + b"\"\n"
                        + BFF_VAULT
                    )
                )
                result = self.validate("sha-1234567")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("vault_redis_password", result.stdout + result.stderr)

    def test_registry_logout_on_success_and_login_or_pull_failure(self):
        binary_directory = self.root / "bin"
        binary_directory.mkdir()
        docker = binary_directory / "docker"
        shutil.copyfile(ANSIBLE / "tests/docker_stub.py", docker)
        docker.chmod(0o700)
        (self.root / "compose.yml").write_text("services: {}\n")
        playbook = self.root / "registry-test.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": "Exercise the real registry-auth tasks with a Docker CLI substitute",
            "hosts": "gateway", "gather_facts": False,
            "vars": {"app_compose_dir": str(self.root), "app_deploy_service": "api",
                     "ansible_python_interpreter": sys.executable},
            "tasks": [{"name": "Run the production auth block",
                       "ansible.builtin.import_tasks": str(
                           ANSIBLE / "roles/app/tasks/pull-image.yml")}],
        }]))
        self.environment["PATH"] = str(binary_directory) + os.pathsep + self.environment["PATH"]
        self.environment["TEST_DOCKER_ROOT"] = str(self.root)
        events_path = self.root / "docker-events.jsonl"
        for failure in ["", "login", "pull"]:
            with self.subTest(failure=failure):
                events_path.unlink(missing_ok=True)
                self.environment["TEST_DOCKER_FAILURE"] = failure
                result = subprocess.run(
                    [shutil.which("ansible-playbook") or "ansible-playbook",
                     "-i", str(self.inventory), str(playbook)],
                    cwd=ANSIBLE, env=self.environment, capture_output=True,
                    text=True, timeout=30,
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode == 0, failure == "", output)
                events = [json.loads(line) for line in events_path.read_text().splitlines()]
                commands = [event["command"] for event in events]
                self.assertEqual(commands, ["login", "logout"] if failure == "login"
                                 else ["login", "compose", "logout"])
                self.assertNotIn("ghcr.io", json.loads(
                    (self.root / "docker-config.json").read_text())["auths"])
                self.assertNotIn("fake-ghcr-token-test-only", output)
                self.assertNotIn("fake-ghcr-token-test-only", events_path.read_text())
                if failure == "pull":
                    self.assertIn("controlled-pull-failure", output)

    def run_with_docker_stub(self, tasks, variables):
        binary_directory = self.root / "bin"
        binary_directory.mkdir(exist_ok=True)
        docker = binary_directory / "docker"
        shutil.copyfile(ANSIBLE / "tests/docker_stub.py", docker)
        docker.chmod(0o700)
        (self.root / "docker-config.json").write_text(json.dumps({"auths": {}}))
        playbook = self.root / "stub-test.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": "Exercise the real tasks with a Docker CLI substitute",
            "hosts": "gateway", "gather_facts": False,
            "vars": dict({"app_compose_dir": str(self.root),
                          "ansible_python_interpreter": sys.executable}, **variables),
            "tasks": [{"name": "Run the tasks", "ansible.builtin.import_tasks": str(
                ANSIBLE / "roles/app/tasks" / tasks)}],
        }]))
        self.environment["PATH"] = str(binary_directory) + os.pathsep + self.environment["PATH"]
        self.environment["TEST_DOCKER_ROOT"] = str(self.root)
        (self.root / "docker-events.jsonl").unlink(missing_ok=True)
        result = subprocess.run(
            [shutil.which("ansible-playbook") or "ansible-playbook", "-i", str(self.inventory),
             str(playbook)],
            cwd=ANSIBLE, env=self.environment, capture_output=True, text=True, timeout=30)
        events_path = self.root / "docker-events.jsonl"
        events = ([json.loads(line) for line in events_path.read_text().splitlines()]
                  if events_path.exists() else [])
        return result, events

    def test_bff_image_is_pulled_anonymously_and_checked_before_changes(self):
        for name in ["GHCR_USERNAME", "GHCR_TOKEN"]:
            self.environment.pop(name)
        cases = [("", BFF_SHA, True, None),
                 ("pull", BFF_SHA, False, "must be public"),
                 ("", "e" * 40, False, "not bff_release_sha=" + BFF_SHA),
                 ("", "", False, "not bff_release_sha=" + BFF_SHA)]
        for failure, revision, succeeds, message in cases:
            with self.subTest(failure=failure, revision=revision):
                self.environment["TEST_DOCKER_FAILURE"] = failure
                self.environment["TEST_DOCKER_REVISION"] = revision
                result, events = self.run_with_docker_stub(
                    "pull-bff.yml", {"bff_image_ref": BFF_REF, "bff_release_sha": BFF_SHA})
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode == 0, succeeds, output)
                commands = [event["command"] for event in events]
                self.assertNotIn("login", commands)
                self.assertEqual(commands[0], "pull")
                pull = events[0]["args"]
                self.assertEqual(pull[-1], BFF_REF)
                # временный пустой config удалён после скачивания
                self.assertFalse(Path(pull[pull.index("--config") + 1]).exists())
                if message:
                    self.assertIn(message, output)
                    self.assertIn("Nothing on the VPS is changed", output)

    def test_running_bff_must_be_the_deployed_image_and_healthy(self):
        image_id = "sha256:" + "1" * 64
        self.environment["TEST_DOCKER_IMAGE_ID"] = image_id
        cases = [("cid", f"{BFF_REF} {image_id} healthy", True),
                 ("", "", False),
                 ("cid", f"{BFF_REF} sha256:{'2' * 64} healthy", False),
                 ("cid", f"{BFF_REF.replace('a' * 64, 'c' * 64)} {image_id} healthy", False),
                 ("cid", f"{BFF_REF} {image_id} unhealthy", False),
                 ("cid", f"{BFF_REF} {image_id} none", False)]
        for container, running, succeeds in cases:
            with self.subTest(container=container, running=running):
                self.environment["TEST_DOCKER_CONTAINER"] = container
                self.environment["TEST_DOCKER_RUNNING"] = running
                result, _ = self.run_with_docker_stub("check-bff-image.yml",
                                                      {"bff_check_ref": BFF_REF})
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode == 0, succeeds, output)
                if not succeeds:
                    self.assertIn("Expected a healthy BFF from " + BFF_REF, output)

    def test_compose_preserves_secrets_and_internal_ports(self):
        compose = shlex.split(os.environ.get("COMPOSE_CLI", "docker compose"))
        if not compose or shutil.which(compose[0]) is None:
            self.skipTest(
                "Set COMPOSE_CLI to a Compose binary (no Docker daemon needed)"
            )
        variables = yaml.safe_load(
            (ANSIBLE / "roles/app/defaults/main.yml").read_text()
        )
        variables.update(
            yaml.safe_load((ANSIBLE / "roles/caddy/defaults/main.yml").read_text())
        )
        variables.update(app_backend_split=False, app_backend_address="",
                         app_bff_go_api_url="http://api:8080/api/v1",
                         app_services=["caddy", "api", "postgres", "redis", "bff"])
        variables.update(app_backend_tag="sha-1234567", app_bff_ref=BFF_REF, app_bff_release=BFF_SHA,
                         app_domain="app.example.org", s3_region="ru-7",
                         s3_endpoint="https://s3.example.org", notebooks_bucket="notebooks",
                         avatars_bucket="avatars", avatars_public_domain="avatars.example.org",
                         app_cors_allowed_origins="https://app.example.org")
        templates = Environment(undefined=StrictUndefined)
        templates.filters["to_json"] = to_json
        templates.filters["bool"] = to_bool
        compose_template = templates.from_string(
            (ANSIBLE / "roles/app/templates/compose.yml.j2").read_text()
        )
        env_template = templates.from_string(
            (ANSIBLE / "roles/app/templates/env.j2").read_text()
        )
        (self.root / "compose.yml").write_text(compose_template.render(variables))
        for secret in [
            "base64/secret+value=",
            "has$interpolation",
            "single'quote",
            "back\\slash",
            "back\\'quote",
            "space # value",
            'double"quote',
            "${ENV}$value",
        ]:
            with self.subTest(secret=secret):
                variables.update(postgres_password=secret, jwt_secret=secret, csrf_secret=secret,
                                 redis_password=secret, session_key=secret, bff_api_key=secret,
                                 notebooks_s3_access_key=secret, notebooks_s3_secret_key=secret)
                (self.root / ".env").write_text(env_template.render(variables))
                compose_environment = dict(os.environ)
                for line in (self.root / ".env").read_text().splitlines():
                    if "=" in line and not line.startswith("#"):
                        compose_environment.pop(line.split("=", 1)[0], None)
                result = subprocess.run(
                    [
                        *compose,
                        "-f",
                        str(self.root / "compose.yml"),
                        "config",
                        "--format",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                    env=compose_environment,
                )
                services = json.loads(result.stdout)["services"]
                if secret == "base64/secret+value=":
                    rollback_environment = dict(compose_environment,
                        ROLLBACK_BACKEND_IMAGE="ghcr.io/go-park-mail-ru/2026_2_cringe_driven_development:sha-7654321")
                    recovery = subprocess.run(
                        [*compose, "-f", str(self.root / "compose.yml"), "-f",
                         str(ANSIBLE / "files/backend-rollback.override.yml"), "config", "--format", "json"],
                        capture_output=True, text=True, timeout=30, check=True,
                        env=rollback_environment)
                    expected_recovery = json.loads(result.stdout)
                    expected_recovery["services"]["api"].update(
                        image=rollback_environment["ROLLBACK_BACKEND_IMAGE"], pull_policy="never")
                    self.assertEqual(json.loads(recovery.stdout), expected_recovery)
                    missing_image_environment = dict(compose_environment)
                    missing_image_environment.pop("ROLLBACK_BACKEND_IMAGE", None)
                    missing_image = subprocess.run(
                        [*compose, "-f", str(self.root / "compose.yml"), "-f",
                         str(ANSIBLE / "files/backend-rollback.override.yml"), "config", "--format", "json"],
                        capture_output=True, text=True, timeout=30,
                        env=missing_image_environment)
                    self.assertNotEqual(missing_image.returncode, 0)
                    self.assertIn("ROLLBACK_BACKEND_IMAGE", missing_image.stderr)
                # Canonical Compose output escapes literal dollars for reuse as YAML.
                for service in ["api", "postgres"]:
                    self.assertEqual(
                        services[service]["environment"]["POSTGRES_PASSWORD"].replace(
                            "$$", "$"
                        ),
                        secret,
                    )
                    self.assertFalse(services[service].get("ports"))
                # Redis: пароль доходит до API и до клиента healthcheck, порт не публикуется
                self.assertEqual(
                    services["api"]["environment"]["REDIS_PASSWORD"].replace("$$", "$"), secret
                )
                self.assertEqual(
                    services["redis"]["environment"]["REDISCLI_AUTH"].replace("$$", "$"), secret
                )
                self.assertEqual(services["api"]["environment"]["REDIS_HOST"], "redis")
                self.assertFalse(services["redis"].get("ports"))
                # каталог, а не файл: замена redis.conf шаблоном видна в контейнере
                self.assertIn("/usr/local/etc/redis",
                              [volume["target"] for volume in services["redis"]["volumes"]])
                self.assertEqual(
                    services["api"]["depends_on"]["redis"]["condition"], "service_healthy"
                )
                for name in ["JWT_SECRET", "CSRF_SECRET"]:
                    self.assertEqual(
                        services["api"]["environment"][name].replace("$$", "$"), secret
                    )
                for name in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]:
                    self.assertEqual(
                        services["api"]["environment"][name].replace("$$", "$"), secret
                    )
                self.assertEqual(services["api"]["environment"]["S3_AVATARS_PUBLIC_URL"],
                                 "https://avatars.example.org")
                self.assertEqual(services["api"]["environment"]["CORS_ALLOWED_ORIGINS"],
                                 "https://app.example.org")
                # BFF: образ по digest без скачивания Compose, ключи из Vault, один S2S-ключ с Go,
                # порт не публикуется
                bff = services["bff"]
                self.assertEqual(bff["image"], BFF_REF)
                self.assertEqual(bff["pull_policy"], "never")
                for name in ["SESSION_KEY", "BFF_KEY"]:
                    self.assertEqual(bff["environment"][name].replace("$$", "$"), secret)
                self.assertEqual(services["api"]["environment"]["BFF_API_KEY"].replace("$$", "$"),
                                 secret)
                self.assertEqual(bff["environment"]["APP_ORIGIN"], "https://app.example.org")
                self.assertEqual(bff["environment"]["GO_API_URL"], "http://api:8080/api/v1")
                self.assertNotIn("PORT", bff["environment"])
                self.assertEqual(bff["depends_on"]["api"]["condition"], "service_healthy")
                self.assertFalse(bff.get("ports"))
        # до первой выкатки BFF сервиса нет, Compose поднимает остальное
        self.assertIn(f"BFF_RELEASE_SHA={BFF_SHA}\n", (self.root / ".env").read_text())
        variables.update(app_bff_ref="", app_bff_release="",
                         app_services=["caddy", "api", "postgres", "redis"])
        (self.root / "compose.yml").write_text(compose_template.render(variables))
        (self.root / ".env").write_text(env_template.render(variables))
        result = subprocess.run(
            [*compose, "-f", str(self.root / "compose.yml"), "config", "--format", "json"],
            capture_output=True, text=True, timeout=30, check=True, env=compose_environment)
        self.assertNotIn("bff", json.loads(result.stdout)["services"])
        self.assertNotIn("BFF_IMAGE_REF", (self.root / ".env").read_text())

    def render_host(self, services, split):
        """compose.yml и .env одного хоста по реальным шаблонам роли app; Compose проверяет результат."""
        variables = yaml.safe_load((ANSIBLE / "roles/app/defaults/main.yml").read_text())
        variables.update(yaml.safe_load((ANSIBLE / "roles/caddy/defaults/main.yml").read_text()))
        address = "192.168.199.10"
        variables.update(
            app_backend_split=split, app_backend_address=address if split else "",
            app_bff_go_api_url=f"http://{address}:8080/api/v1" if split else "http://api:8080/api/v1",
            app_services=services, app_backend_tag="sha-1234567", app_bff_ref=BFF_REF,
            app_bff_release=BFF_SHA, app_domain="app.example.org", s3_region="ru-7",
            s3_endpoint="https://s3.example.org", notebooks_bucket="notebooks",
            avatars_bucket="avatars", avatars_public_domain="avatars.example.org",
            app_cors_allowed_origins="https://app.example.org", postgres_password="pg-secret",
            jwt_secret="jwt-secret", csrf_secret="csrf-secret", redis_password="redis-secret",
            session_key="session-secret", bff_api_key="s2s-secret",
            notebooks_s3_access_key="s3-key", notebooks_s3_secret_key="s3-secret")
        templates = Environment(undefined=StrictUndefined)
        templates.filters["to_json"] = to_json
        templates.filters["bool"] = to_bool
        compose = templates.from_string(
            (ANSIBLE / "roles/app/templates/compose.yml.j2").read_text()).render(variables)
        env = templates.from_string((ANSIBLE / "roles/app/templates/env.j2").read_text()).render(variables)
        (self.root / "compose.yml").write_text(compose)
        (self.root / ".env").write_text(env)
        command = shlex.split(os.environ.get("COMPOSE_CLI", "docker compose"))
        if not command or shutil.which(command[0]) is None:
            self.skipTest("Set COMPOSE_CLI to a Compose binary (no Docker daemon needed)")
        clean = {k: v for k, v in os.environ.items() if not k.isupper() or k in ("PATH", "HOME")}
        result = subprocess.run([*command, "-f", str(self.root / "compose.yml"), "config", "--format",
                                 "json"], capture_output=True, text=True, timeout=30, check=True,
                                env=clean)
        return json.loads(result.stdout), env

    def test_split_layout_gateway_runs_only_caddy_and_bff(self):
        config, env = self.render_host(["caddy", "bff"], split=True)
        self.assertEqual(config["name"], "cellestial")
        self.assertEqual(set(config["services"]), {"caddy", "bff"})
        self.assertEqual(set(config["volumes"]), {"caddy_data", "caddy_config"})
        bff = config["services"]["bff"]
        self.assertEqual(bff["environment"]["GO_API_URL"], "http://192.168.199.10:8080/api/v1")
        self.assertNotIn("depends_on", bff)
        self.assertEqual(bff["environment"]["BFF_KEY"], "s2s-secret")
        # секреты Go, Postgres, Redis и S3 на публичной машине не лежат
        for secret in ["pg-secret", "jwt-secret", "csrf-secret", "redis-secret", "s3-secret"]:
            self.assertNotIn(secret, env)
        self.assertNotIn("BACKEND_IMAGE_TAG", env)

    def test_split_layout_backend_publishes_the_api_only_on_the_private_address(self):
        config, env = self.render_host(["api", "postgres", "redis"], split=True)
        self.assertEqual(config["name"], "cellestial")
        self.assertEqual(set(config["services"]), {"api", "postgres", "redis"})
        self.assertEqual(set(config["volumes"]), {"postgres_data", "redis_data"})
        ports = config["services"]["api"]["ports"]
        self.assertEqual([(p["host_ip"], p["published"], p["target"]) for p in ports],
                         [("192.168.199.10", "8080", 8080)])
        for service in ["postgres", "redis"]:
            self.assertFalse(config["services"][service].get("ports"))
        self.assertEqual(config["services"]["api"]["environment"]["BFF_API_KEY"], "s2s-secret")
        # ключ сессии BFF и образ BFF на VPS2 не нужны
        self.assertNotIn("session-secret", env)
        self.assertNotIn("BFF_IMAGE_REF", env)

    def test_services_of_each_host(self):
        data = yaml.safe_load(self.inventory.read_text())
        data["all"]["children"]["backend"] = {"hosts": {"test-backend": {
            "ansible_host": "127.0.0.1", "ansible_connection": "local"}}}
        self.inventory.write_text(yaml.safe_dump(data))
        playbook = self.root / "services-test.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": "Exercise the real service selection", "hosts": "gateway:backend",
            "gather_facts": False,
            "vars": {"app_compose_dir": str(self.root / "{{ inventory_hostname }}"),
                     "app_bff_image": "ghcr.io/cringe-driven-development-team/bff",
                     "app_deploy_service": "api", "backend_image_tag": "sha-1234567",
                     "ansible_python_interpreter": sys.executable},
            "tasks": [
                {"name": "Choose", "ansible.builtin.import_tasks": str(ANSIBLE / "roles/app/tasks/tags.yml")},
                {"name": "Show", "ansible.builtin.debug": {
                    "msg": "SERVICES {{ inventory_hostname }} {{ app_services | join(',') }}"}},
            ],
        }]))
        for host in ["test-vps", "test-backend"]:
            (self.root / host).mkdir(exist_ok=True)
            (self.root / host / ".env").write_text(f"BFF_IMAGE_REF={BFF_REF}\nBFF_RELEASE_SHA={BFF_SHA}\n")
        cases = {
            False: {"test-vps": "caddy,api,postgres,redis,bff", "test-backend": "caddy,api,postgres,redis,bff"},
            True: {"test-vps": "caddy,bff", "test-backend": "api,postgres,redis"},
        }
        # и строкой, как из -e app_backend_split=false: непустая строка "false" не должна быть истиной
        runs = [(split, json.dumps({"app_backend_split": split})) for split in cases]
        runs += [(split, f"app_backend_split={str(split).lower()}") for split in cases]
        for split, extra in runs:
            expected = cases[split]
            with self.subTest(extra=extra):
                result = subprocess.run(
                    [shutil.which("ansible-playbook") or "ansible-playbook", "-i", str(self.inventory),
                     str(playbook), "-e", extra],
                    cwd=ANSIBLE, env=self.environment, capture_output=True, text=True, timeout=60)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                for host, services in expected.items():
                    self.assertIn(f"SERVICES {host} {services}", output)

    def test_split_layout_needs_one_backend_host(self):
        result = self.validate("sha-1234567", extra={"app_backend_split": True})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one backend VPS", result.stdout + result.stderr)

    def test_registry_switch_and_explicit_rollback_image(self):
        variables = yaml.safe_load((ANSIBLE / "roles/app/defaults/main.yml").read_text())
        variables.update(yaml.safe_load((ANSIBLE / "roles/caddy/defaults/main.yml").read_text()))
        environment = Environment(undefined=StrictUndefined)
        environment.filters["bool"] = to_bool
        template = environment.from_string(
            (ANSIBLE / "roles/app/templates/compose.yml.j2").read_text())
        expected = "ghcr.io/cringe-driven-development-team/backend"
        for override in [None, "ghcr.io/go-park-mail-ru/2026_2_cringe_driven_development"]:
            with self.subTest(override=override):
                values = dict(variables, app_bff_ref="", app_backend_split=False,
                              app_backend_address="", app_services=["caddy", "api", "postgres", "redis"])
                if override is not None:
                    values["app_backend_image"] = override
                rendered = template.render(values).replace(
                    "${BACKEND_IMAGE_TAG:?backend tag is required}", "sha-1234567")
                data = yaml.safe_load(rendered)
                self.assertEqual(data["services"]["api"]["image"],
                                 (override or expected) + ":sha-1234567")
                self.assertEqual(set(data["volumes"]),
                                 {"caddy_data", "caddy_config", "postgres_data", "redis_data"})
                self.assertFalse(data["services"]["api"].get("ports"))

    def render_caddy(self, **values):
        templates = Environment(undefined=StrictUndefined)
        templates.filters["bool"] = to_bool
        template = templates.from_string(
            (ANSIBLE / "roles/caddy/templates/Caddyfile.j2").read_text()
        )
        return template.render(dict(app_domain="cellestial.ru",
            frontend_cdn_domain="cdn.example.org",
            frontend_static_domain="static.example.org",
            avatars_public_domain="avatars.example.org"), **values)

    def test_caddy_routes_to_bff_once_it_is_deployed(self):
        csp = ("default-src 'none'; script-src https://cdn.example.org; "
               "style-src https://cdn.example.org; "
               "img-src https://cdn.example.org https://static.example.org "
               "https://avatars.example.org data:; "
               "font-src https://static.example.org; connect-src 'self'; base-uri 'none'; "
               "form-action 'self'; frame-ancestors 'none'")
        # caddy_bff_enabled — из роли app, caddy_bff_deployed — из .env в роли caddy
        for values, deployed in [({"caddy_bff_deployed": False}, False),
                                 ({"caddy_bff_deployed": True}, True),
                                 ({"caddy_bff_deployed": False, "caddy_bff_enabled": "True"}, True),
                                 ({"caddy_bff_deployed": True, "caddy_bff_enabled": False}, False)]:
            with self.subTest(values=values):
                rendered = self.render_caddy(**values)
                self.assertEqual("reverse_proxy bff:3000" in rendered, deployed)
                self.assertEqual('respond "BFF is not deployed" 503' in rendered, not deployed)
                # Go и бакет релизов наружу больше не проксируются, внутренний /health закрыт
                self.assertNotIn("api:8080", rendered)
                self.assertNotIn("selstorage", rendered)
                self.assertNotIn("header_up -Cookie", rendered)
                self.assertIn("handle /health {\n\t\trespond 404\n\t}", rendered)
                self.assertIn("\tX-Content-Type-Options nosniff\n", rendered)
                self.assertEqual(
                    f'header_down Content-Security-Policy-Report-Only "{csp}"' in rendered, deployed)
                # блок с политикой не съедает перевод строки перед адресом сайта
                self.assertIn("\ncellestial.ru {\n", rendered)

    def test_caddy_enforces_csp_when_report_only_is_off(self):
        # строка "false" — значение из -e caddy_csp_report_only=false
        for report_only in [False, "false"]:
            rendered = self.render_caddy(caddy_csp_report_only=report_only,
                                         caddy_bff_deployed=True)
            self.assertIn('header_down Content-Security-Policy "default-src ', rendered)
            self.assertNotIn("Content-Security-Policy-Report-Only", rendered)

if __name__ == "__main__":
    unittest.main()
