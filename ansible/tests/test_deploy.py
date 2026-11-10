"""Deployment checks without SSH, production secrets, or a Docker daemon.

Run with the deployment Python environment and COMPOSE_CLI pointing to a Compose
command: COMPOSE_CLI='docker compose' python -m unittest discover -s tests -v
"""

import base64
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ansible.parsing.vault import VaultLib, VaultSecret
from ansible.plugins.filter.core import to_bool, to_json
from jinja2 import Environment, StrictUndefined, UndefinedError
import yaml

ANSIBLE = Path(__file__).resolve().parents[1]
# Секреты BFF в формате, который принимает validate.yml
# Образ BFF по digest и полный SHA релиза в формате, который принимает deploy-bff.yml (infra#74)
BFF_REF = "ghcr.io/cringe-driven-development-team/bff@sha256:" + "a" * 64
BACKEND_REF = "ghcr.io/cringe-driven-development-team/backend@sha256:" + "d" * 64
BFF_SHA = "b" * 40
TOTP_KEY = base64.b64encode(bytes(range(32))).decode()
TOTP_VAULT = f"vault_totp_encryption_key: {TOTP_KEY}\n".encode()
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
                            },
                            "backend": {"hosts": {"test-backend": {
                                "ansible_host": "127.0.0.1", "ansible_connection": "local"}}}
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
            'totp_encryption_key: "{{ vault_totp_encryption_key }}"\n'
            'redis_password: "{{ vault_redis_password }}"\n'
            'session_key: "{{ vault_session_key }}"\n'
            'bff_api_key: "{{ vault_bff_api_key }}"\n'
            'avatars_s3_access_key: fake-key\navatars_s3_secret_key: fake-secret\n'
            's3_region: ru-7\ns3_endpoint: https://s3.example.org\n'
            'avatars_bucket: avatars\n'
            'avatars_public_domain: avatars.example.org\n'
            'app_cors_allowed_origins: https://app.example.org\n'
            'app_backend_address: "127.0.0.1"\n'
            + yaml.safe_dump(RUNTIME_OPEN)
            + "".join(f'{k}: "{{{{ vault_{k} }}}}"\n' for k in RUNTIME_SECRETS)
        )
        password = b"test-vault-password-only"
        vault = VaultLib([("default", VaultSecret(password))])
        (variables / "vault.yml").write_bytes(
            vault.encrypt(
                b"vault_postgres_password: fake-database-password\nvault_jwt_secret: fake-jwt-secret\n"
                b"vault_csrf_secret: fake-csrf-secret\nvault_redis_password: fake-redis-password-0123\n"
                + BFF_VAULT + TOTP_VAULT + RUNTIME_VAULT.encode()
            )
        )
        password_file = self.root / "vault-pass"
        password_file.write_bytes(password)
        password_file.chmod(0o600)
        self.environment["ANSIBLE_VAULT_PASSWORD_FILE"] = str(password_file)

    def validate(self, tag=None, playbook="deploy-backend.yml", variable="backend_image_ref",
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

    def test_valid_digest_and_vault(self):
        result = self.validate(BACKEND_REF)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_totp_key_validation_rejects_invalid_values_without_leaks(self):
        path = self.root / "group_vars/all/vault.yml"
        vault = VaultLib([("default", VaultSecret(b"test-vault-password-only"))])
        original = yaml.safe_load(vault.decrypt(path.read_bytes()))
        cases = [None, "", 123, False, [], {}, "short-leaky-totp-key",
                 "!" * 43 + "=", "_" * 43 + "=", TOTP_KEY + "\n",
                 TOTP_KEY[:20] + "\n" + TOTP_KEY[20:]]
        cases += [base64.b64encode(bytes(n)).decode() for n in (16, 31, 33, 64)]
        for key in cases:
            with self.subTest(key_type=type(key).__name__, length=len(str(key))):
                values = dict(original, vault_totp_encryption_key=key)
                if key is None:
                    values.pop("vault_totp_encryption_key")
                path.write_bytes(vault.encrypt(yaml.safe_dump(values).encode()))
                result = self.validate(BACKEND_REF)
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, output)
                self.assertIn("Check the TOTP encryption key", output)
                self.assertNotIn("Gather server facts", output)
                self.assertNotIn("Write the application environment", output)
                if isinstance(key, str) and len(key) > 10:
                    self.assertNotIn(key, output)
                self.assertNotIn(TOTP_KEY, output)

    def test_backend_requires_exact_package_digest(self):
        image = "ghcr.io/cringe-driven-development-team/backend"
        for ref in [None, "", "main", image + ":sha-1234567", image + "@sha256:" + "a" * 63,
                    image + "@sha256:" + "A" * 64, image + "@sha256:" + "g" * 64,
                    BFF_REF, BACKEND_REF + ";echo bad", BACKEND_REF + "\n"]:
            with self.subTest(ref=ref):
                result = self.validate(ref)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("backend_image_ref=", result.stdout + result.stderr)
        result = self.validate(BFF_REF, extra={"app_backend_image": "ghcr.io/cringe-driven-development-team/bff"})
        self.assertNotEqual(result.returncode, 0)

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
        result = self.validate_bff(extra={"backend_image_ref": "main"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only by deploy-backend.yml", result.stdout + result.stderr)

    def test_bff_deploy_needs_no_registry_credentials(self):
        for name in ["GHCR_USERNAME", "GHCR_TOKEN"]:
            self.environment.pop(name)
        result = self.validate_bff()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_backend_deploy_does_not_change_the_bff(self):
        result = self.validate(BACKEND_REF, extra={"bff_image_ref": BFF_REF,
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
                (self.root / "group_vars/all/vault.yml").write_bytes(vault.encrypt(base + TOTP_VAULT + secrets))
                result = self.validate_bff()
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("vault_session_key must be base64", output)
                self.assertNotIn("short-leaky-key", output)

    def test_deployment_keeps_the_other_deployed_image(self):
        playbook = self.root / "tags-test.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": "Exercise the real tag selection", "hosts": "backend", "gather_facts": False,
            "vars": {"app_compose_dir": str(self.root),
                     "app_bff_image": "ghcr.io/cringe-driven-development-team/bff",
                     "app_deploy_service": "api",
                     "ansible_python_interpreter": sys.executable},
            "tasks": [
                {"name": "Choose tags",
                 "ansible.builtin.import_tasks": str(ANSIBLE / "roles/app/tasks/tags.yml")},
                {"name": "Show tags", "ansible.builtin.debug": {
                    "msg": "TAGS {{ app_backend_ref }} {{ app_bff_ref | default('-', true) }}"
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
        old_backend = BACKEND_REF.replace("d" * 64, "e" * 64)
        deployed = (f"BACKEND_IMAGE_REF={old_backend}\nBFF_IMAGE_REF={old_ref}\n"
                    f"BFF_RELEASE_SHA={old_sha}\nJWT_SECRET=\"leaky\"\n")
        cases = [
            (deployed, {"bff_image_ref": BFF_REF, "bff_release_sha": BFF_SHA},
             f"TAGS {old_backend} {BFF_REF} {BFF_SHA}"),
            (deployed, {"backend_image_ref": BACKEND_REF},
             f"TAGS {BACKEND_REF} {old_ref} {old_sha}"),
            (f"BACKEND_IMAGE_REF={old_backend}\n", {"backend_image_ref": BACKEND_REF},
             f"TAGS {BACKEND_REF} - -"),
            (None, {"backend_image_ref": BACKEND_REF}, f"TAGS {BACKEND_REF} - -"),
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

    def test_backend_needs_no_registry_credentials(self):
        for name in ["GHCR_USERNAME", "GHCR_TOKEN"]:
            self.environment.pop(name, None)
        result = self.validate(BACKEND_REF)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_multiple_hosts_are_rejected(self):
        data = yaml.safe_load(self.inventory.read_text())
        data["all"]["children"]["gateway"]["hosts"]["second-vps"] = {
            "ansible_host": "127.0.0.2",
            "ansible_connection": "local",
        }
        self.inventory.write_text(yaml.safe_dump(data))
        result = self.validate(BACKEND_REF)
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
                result = self.validate(BACKEND_REF)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("decrypt", (result.stdout + result.stderr).lower())

    def test_missing_csrf_secret_is_rejected(self):
        vault = VaultLib([("default", VaultSecret(b"test-vault-password-only"))])
        (self.root / "group_vars/all/vault.yml").write_bytes(
            vault.encrypt(
                b"vault_postgres_password: fake-database-password\nvault_jwt_secret: fake-jwt-secret\n"
                b"vault_redis_password: fake-redis-password-0123\n" + BFF_VAULT + TOTP_VAULT
            )
        )
        result = self.validate(BACKEND_REF)
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
                        + BFF_VAULT + TOTP_VAULT
                    )
                )
                result = self.validate(BACKEND_REF)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("vault_redis_password", result.stdout + result.stderr)

    def test_backend_pull_ignores_saved_credentials(self):
        for name in ["GHCR_USERNAME", "GHCR_TOKEN"]:
            self.environment.pop(name, None)
        for failure in ["", "pull"]:
            with self.subTest(failure=failure):
                self.environment["TEST_DOCKER_FAILURE"] = failure
                (self.root / ".env").write_text("old-environment")
                (self.root / "compose.yml").write_text("old-compose")
                config = self.root / "docker-config.json"
                config.write_text(json.dumps({"auths": {"ghcr.io": {"auth": "saved-test-token"}}}))
                result, events = self.run_with_docker_stub("pull-image.yml", {"backend_image_ref": BACKEND_REF})
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode == 0, failure == "", output)
                self.assertEqual([e["command"] for e in events], ["pull"])
                argv = events[0]["args"]
                self.assertEqual(argv[-1], BACKEND_REF)
                self.assertFalse(Path(argv[argv.index("--config") + 1]).exists())
                self.assertEqual((self.root / ".env").read_text(), "old-environment")
                self.assertEqual((self.root / "compose.yml").read_text(), "old-compose")
                self.assertIn("saved-test-token", config.read_text())
                self.assertNotIn("saved-test-token", output)

    def run_with_docker_stub(self, tasks, variables):
        binary_directory = self.root / "bin"
        binary_directory.mkdir(exist_ok=True)
        docker = binary_directory / "docker"
        shutil.copyfile(ANSIBLE / "tests/docker_stub.py", docker)
        docker.chmod(0o700)
        if not (self.root / "docker-config.json").exists():
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

    def test_backend_pull_failure_keeps_project_and_container(self):
        # Exercise the real pre_tasks ordering; replace deployment with a local write marker.
        production = next(play for play in yaml.safe_load((ANSIBLE / 'deploy-backend.yml').read_text()) if 'post_tasks' in play)
        production['become'] = False
        production['roles'] = []
        production['vars'].update(app_backend_image='ghcr.io/cringe-driven-development-team/backend',
                                  app_compose_dir=str(self.root),
                                  ansible_python_interpreter=sys.executable)
        for task in production['pre_tasks']:
            if 'ansible.builtin.import_tasks' in task:
                task['ansible.builtin.import_tasks'] = str(ANSIBLE / task['ansible.builtin.import_tasks'])
        production['post_tasks'] = [{'name': 'Simulate configuration and container switch',
            'ansible.builtin.copy': {'content': '{{ backend_image_ref }}',
                                    'dest': str(self.root / 'switch-marker'), 'mode': '0600'}}]
        playbook = self.root / 'preflight.yml'
        playbook.write_text(yaml.safe_dump([production]))
        binary = self.root / 'bin'
        binary.mkdir()
        shutil.copyfile(ANSIBLE / 'tests/docker_stub.py', binary / 'docker')
        (binary / 'docker').chmod(0o700)
        self.environment.update(PATH=str(binary) + os.pathsep + self.environment['PATH'],
                                TEST_DOCKER_ROOT=str(self.root))
        for ref, failure, succeeds in [(BACKEND_REF, 'pull', False), ('main', '', False),
                                        (BACKEND_REF, '', True)]:
            with self.subTest(ref=ref, failure=failure):
                (self.root / '.env').write_text('old-env')
                (self.root / 'compose.yml').write_text('old-compose')
                (self.root / 'switch-marker').unlink(missing_ok=True)
                self.environment['TEST_DOCKER_FAILURE'] = failure
                result = subprocess.run(['ansible-playbook', '-i', str(self.inventory), str(playbook),
                                         '-e', json.dumps({'backend_image_ref': ref})], cwd=ANSIBLE,
                                        env=self.environment, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
                marker = self.root / 'switch-marker'
                self.assertEqual(marker.exists(), succeeds)
                if succeeds:
                    self.assertEqual(marker.read_text(), BACKEND_REF)
                self.assertEqual((self.root / '.env').read_text(), 'old-env')
                self.assertEqual((self.root / 'compose.yml').read_text(), 'old-compose')

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
        for secret in ["base64/secret+value=", "has$interpolation", "single'quote", "back\\slash",
                       "back\\'quote", "space # value", 'double"quote', "${ENV}$value"]:
            with self.subTest(secret=secret):
                values = {name: secret for name in ('postgres_password', 'jwt_secret', 'csrf_secret',
                          'redis_password', 'session_key', 'bff_api_key', 'avatars_s3_access_key',
                          'avatars_s3_secret_key')}
                config, env = self.render_host(['api', 'postgres', 'redis'], secrets=values)
                services = config['services']
                self.assertEqual(set(services), {'api', 'postgres', 'redis'})
                for service in ('api', 'postgres'):
                    self.assertEqual(services[service]['environment']['POSTGRES_PASSWORD'].replace('$$', '$'), secret)
                for name in ('REDIS_PASSWORD', 'JWT_SECRET', 'CSRF_SECRET', 'AWS_ACCESS_KEY_ID',
                             'AWS_SECRET_ACCESS_KEY', 'BFF_API_KEY'):
                    self.assertEqual(services['api']['environment'][name].replace('$$', '$'), secret)
                self.assertEqual(services['redis']['environment']['REDISCLI_AUTH'].replace('$$', '$'), secret)
                self.assertFalse(services['redis'].get('ports'))
                self.assertFalse(services['postgres'].get('ports'))
                self.assertEqual(services['api']['ports'][0]['host_ip'], '192.168.199.10')
                self.assertEqual(services['api']['depends_on']['redis']['condition'], 'service_healthy')
                self.assertIn('/usr/local/etc/redis', [v['target'] for v in services['redis']['volumes']])
                self.assertEqual(services['api']['environment']['REDIS_HOST'], 'redis')
                self.assertEqual(services['api']['environment']['CORS_ALLOWED_ORIGINS'], 'https://app.example.org')
                self.assertEqual(services['api']['environment']['S3_AVATARS_PUBLIC_URL'], 'https://avatars.example.org')
                config, env = self.render_host(['caddy', 'bff'], secrets=values)
                self.assertEqual(set(config['services']), {'caddy', 'bff'})
                bff = config['services']['bff']
                self.assertEqual(bff['image'], BFF_REF)
                self.assertEqual(bff['pull_policy'], 'never')
                for name in ('SESSION_KEY', 'BFF_KEY'):
                    self.assertEqual(bff['environment'][name].replace('$$', '$'), secret)
                self.assertEqual(bff['environment']['GO_API_URL'], 'http://192.168.199.10:8080/api/v1')
                self.assertEqual(bff['environment']['APP_ORIGIN'], 'https://app.example.org')
                self.assertNotIn('depends_on', bff)
                self.assertNotIn('PORT', bff['environment'])
                self.assertFalse(bff.get('ports'))

    def render_host(self, services, runtime=None, secrets=None):
        """compose.yml и .env одного хоста по реальным шаблонам роли app; Compose проверяет результат."""
        variables = yaml.safe_load((ANSIBLE / "roles/app/defaults/main.yml").read_text())
        variables.update(app_compose_dir="/opt/cellestial",
                         app_runtime_secrets_dir="/opt/cellestial/runtime-secrets",
                         app_runtime_playbook_dir="/opt/cellestial/runtime-playbook")
        variables.update(self.runtime_values())
        variables.update(runtime or {})
        variables.update(yaml.safe_load((ANSIBLE / "roles/caddy/defaults/main.yml").read_text()))
        address = "192.168.199.10"
        variables.update(
            app_backend_address=address,
            app_bff_go_api_url=f"http://{address}:8080/api/v1",
            app_services=services, app_backend_ref=BACKEND_REF, app_bff_ref=BFF_REF,
            app_bff_release=BFF_SHA, app_domain="app.example.org", s3_region="ru-7",
            s3_endpoint="https://s3.example.org",
            avatars_bucket="avatars", avatars_public_domain="avatars.example.org",
            app_cors_allowed_origins="https://app.example.org", postgres_password="pg-secret",
            jwt_secret="jwt-secret", csrf_secret="csrf-secret", redis_password="redis-secret",
            session_key="session-secret", bff_api_key="s2s-secret",
            totp_encryption_key=TOTP_KEY,
            avatars_s3_access_key="s3-key", avatars_s3_secret_key="s3-secret")
        variables.update(secrets or {})
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

    def test_totp_and_avatar_configuration(self):
        services = ["api", "postgres", "redis"]
        config, env = self.render_host(services)
        api = config["services"]["api"]["environment"]
        self.assertEqual(api["TOTP_ENCRYPTION_KEY"], TOTP_KEY)
        self.assertNotIn("S3_NOTEBOOKS_BUCKET", api)
        self.assertNotIn("S3_NOTEBOOKS_BUCKET", env)
        self.assertEqual(api["AWS_ACCESS_KEY_ID"], "s3-key")
        self.assertEqual(api["AWS_SECRET_ACCESS_KEY"], "s3-secret")
        self.assertEqual(api["AWS_REGION"], "ru-7")
        self.assertEqual(api["AWS_ENDPOINT_URL_S3"], "https://s3.example.org")
        self.assertEqual(api["S3_AVATARS_BUCKET"], "avatars")
        self.assertEqual(api["S3_AVATARS_PUBLIC_URL"], "https://avatars.example.org")
        again, again_env = self.render_host(services)
        self.assertEqual(again_env, env)
        self.assertEqual(again["services"]["api"]["environment"], api)
        for name, service in config["services"].items():
            if name != "api":
                self.assertNotIn("TOTP_ENCRYPTION_KEY", service.get("environment", {}))
        config, env = self.render_host(["caddy", "bff"])
        self.assertNotIn(TOTP_KEY, env)
        self.assertNotIn("TOTP_ENCRYPTION_KEY", config["services"]["bff"]["environment"])

    def test_gateway_runs_only_caddy_and_bff(self):
        config, env = self.render_host(["caddy", "bff"])
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
        self.assertNotIn("BACKEND_IMAGE_REF", env)

    def test_backend_publishes_the_api_only_on_the_private_address(self):
        config, env = self.render_host(["api", "postgres", "redis"])
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
        playbook = self.root / "services-test.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": "Exercise fixed service placement", "hosts": "gateway:backend", "gather_facts": False,
            "vars_files": [str(ANSIBLE / "roles/app/defaults/main.yml")],
            "vars": {"app_compose_dir": str(self.root / "{{ inventory_hostname }}"),
                     "app_deploy_service": "api", "backend_image_ref": BACKEND_REF,
                     "ansible_python_interpreter": sys.executable},
            "tasks": [
                {"name": "Choose", "ansible.builtin.import_tasks": str(ANSIBLE / "roles/app/tasks/tags.yml")},
                {"name": "Show", "ansible.builtin.debug": {"msg":
                    "SERVICES {{ inventory_hostname }} {{ app_services | join(',') }} URL {{ app_bff_go_api_url }}"}},
            ],
        }]))
        for has_bff in (False, True):
            with self.subTest(has_bff=has_bff):
                for host in ("test-vps", "test-backend"):
                    directory = self.root / host
                    directory.mkdir(exist_ok=True)
                    (directory / ".env").write_text(
                        f"BFF_IMAGE_REF={BFF_REF}\nBFF_RELEASE_SHA={BFF_SHA}\n" if has_bff else "")
                result = subprocess.run(["ansible-playbook", "-i", str(self.inventory), str(playbook)],
                                        cwd=ANSIBLE, env=self.environment, capture_output=True, text=True, timeout=60)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                gateway = "caddy,bff" if has_bff else "caddy"
                self.assertIn(f"SERVICES test-vps {gateway} URL http://127.0.0.1:8080/api/v1", output)
                self.assertIn("SERVICES test-backend api,postgres,redis URL http://127.0.0.1:8080/api/v1", output)

    def test_verify_accepts_the_backend_vps_in_the_inventory(self):
        def run(groups):
            data = {"all": {"children": {group: {"hosts": {host: {
                "ansible_host": "127.0.0.1", "ansible_connection": "local"} for host in hosts}}
                for group, hosts in groups.items()}}}
            inventory = self.root / "verify-inventory.yml"
            inventory.write_text(yaml.safe_dump(data))
            return subprocess.run(
                [shutil.which("ansible-playbook") or "ansible-playbook", "-i", str(inventory),
                 str(ANSIBLE / "verify.yml"), "--tags", "inventory"],
                cwd=ANSIBLE, env=self.environment, capture_output=True, text=True, timeout=60)

        allowed = {"gateway": ["gw"], "backend": ["vps2"], "auxiliary": ["aux"]}
        result = run(allowed)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for groups in [dict(allowed, backend=[]), dict(allowed, backend=["vps2", "vps3"]), dict(allowed, other=["stray"])]:
            with self.subTest(groups=groups):
                result = run(groups)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ровно одна VPS2", result.stdout + result.stderr)

    def test_deployment_needs_one_backend_host(self):
        data = yaml.safe_load(self.inventory.read_text())
        del data['all']['children']['backend']
        self.inventory.write_text(yaml.safe_dump(data))
        result = self.validate(BACKEND_REF)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one backend VPS", result.stdout + result.stderr)

    def test_backend_deploy_selects_only_backend_hosts(self):
        result = subprocess.run(["ansible-playbook", "-i", str(self.inventory),
                                 str(ANSIBLE / "deploy-backend.yml"), "--list-hosts"],
                                cwd=ANSIBLE, env=self.environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("test-vps", result.stdout)
        self.assertIn("test-backend", result.stdout)

    def test_multiple_backend_hosts_are_rejected(self):
        data = yaml.safe_load(self.inventory.read_text())
        data['all']['children']['backend']['hosts']['second-backend'] = {
            'ansible_host': '127.0.0.3', 'ansible_connection': 'local'}
        self.inventory.write_text(yaml.safe_dump(data))
        result = self.validate(BACKEND_REF)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('one backend VPS', result.stdout + result.stderr)

    def test_backend_compose_preserves_digest(self):
        config, env = self.render_host(["api", "postgres", "redis"])
        self.assertEqual(config["services"]["api"]["image"], BACKEND_REF)
        self.assertEqual(config["services"]["api"]["pull_policy"], "never")
        self.assertIn("BACKEND_IMAGE_REF=" + BACKEND_REF, env)

    def test_running_backend_must_match_image_id_and_be_healthy(self):
        image_id = "sha256:" + "1" * 64
        self.environment["TEST_DOCKER_IMAGE_ID"] = image_id
        cases = [("cid", f"{image_id} true healthy", True),
                 ("", "", False), ("one\ntwo", "", False),
                 ("cid", f"sha256:{'2' * 64} true healthy", False),
                 ("cid", f"{image_id} false healthy", False),
                 ("cid", f"{image_id} true unhealthy", False),
                 ("cid", f"{image_id} true none", False)]
        for container, state, succeeds in cases:
            with self.subTest(container=container, state=state):
                self.environment["TEST_DOCKER_CONTAINER"] = container
                self.environment["TEST_DOCKER_STATE"] = state
                result, _ = self.run_with_docker_stub("check-backend-image.yml", {"backend_check_ref": BACKEND_REF})
                self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)

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
               "style-src 'self' https://cdn.example.org; "
               "img-src 'self' https://cdn.example.org https://static.example.org "
               "https://avatars.example.org data:; "
               "font-src 'self' https://static.example.org; connect-src 'self'; manifest-src 'self'; "
               "worker-src 'self'; base-uri 'none'; "
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

    def runtime_values(self):
        """Значения runtime_* в формате выходов Pulumi; секреты — заглушки, не настоящие ключи."""
        return dict(RUNTIME_OPEN, **RUNTIME_SECRETS)

    def test_api_render_requires_the_runtime_configuration(self):
        # Выключателя runtime нет: без обязательного runtime_* шаблон Go API не рендерится
        for name in ["runtime_security_group_id", "runtime_os_password"]:
            with self.subTest(name=name):
                values = self.runtime_values()
                values.pop(name)
                with patch.object(self, "runtime_values", return_value=values):
                    with self.assertRaises(UndefinedError):
                        self.render_host(["api", "postgres", "redis"])

    def test_gateway_gets_no_runtime_configuration(self):
        config, env = self.render_host(["caddy", "bff"])
        self.assertNotIn("RUNTIME_", env)
        self.assertNotIn("RUNTIME_", json.dumps(config))
        for secret in ["ssh-key-body", "ca-key-body", "client-key-body", RUNTIME_SECRETS["runtime_secrets_key"]]:
            self.assertNotIn(secret, env)

    def test_runtime_passes_env_and_mounts_secrets_read_only(self):
        values = dict(self.runtime_values(), runtime_extra_env={"RUNTIME_MAX_ACTIVE": 3})
        config, env = self.render_host(["api", "postgres", "redis"], runtime=values)
        api = config["services"]["api"]["environment"]
        expected = {
            "RUNTIME_PROVIDER": "openstack",
            "RUNTIME_OS_AUTH_URL": "https://cloud.api.selcloud.ru/identity/v3",
            "RUNTIME_OS_USERNAME": "cellestialRuntimeProvisioner",
            "RUNTIME_OS_PASSWORD": RUNTIME_SECRETS["runtime_os_password"],
            "RUNTIME_OS_USER_DOMAIN_NAME": "123456", "RUNTIME_OS_PROJECT_ID": "a" * 32,
            "RUNTIME_OS_REGION": "ru-9", "RUNTIME_AVAILABILITY_ZONE": "ru-9a",
            "RUNTIME_NETWORK_ID": UUID, "RUNTIME_SUBNET_ID": UUID, "RUNTIME_SECURITY_GROUP_ID": UUID,
            "RUNTIME_IMAGE_ID": UUID, "RUNTIME_FLAVOR_ID": "1012", "RUNTIME_VOLUME_TYPE": "fast.ru-9a",
            "RUNTIME_SSH_KEY_NAME": "runtime-provisioner", "RUNTIME_SSH_USER": "root",
            "RUNTIME_SSH_PRIVATE_KEY_FILE": "/run/secrets/runtime/ssh_key",
            "RUNTIME_TLS_CA_CERT_FILE": "/run/secrets/runtime/ca.crt",
            "RUNTIME_TLS_CA_KEY_FILE": "/run/secrets/runtime/ca.key",
            "RUNTIME_TLS_CLIENT_CERT_FILE": "/run/secrets/runtime/client.crt",
            "RUNTIME_TLS_CLIENT_KEY_FILE": "/run/secrets/runtime/client.key",
            "RUNTIME_WORK_DIR": "/var/lib/cellestial/runtime",
            "RUNTIME_BLOCKED_PUBLIC_IPS": "203.0.113.1,203.0.113.2",
            "RUNTIME_SECRETS_KEY": RUNTIME_SECRETS["runtime_secrets_key"],
            "RUNTIME_ENV_NAME": "production",
            "RUNTIME_MAX_ACTIVE": "3",
        }
        self.assertEqual({k: v.replace("$$", "$") for k, v in api.items() if k.startswith("RUNTIME_")},
                         expected)
        mounts = {(v["source"], v["target"], v.get("read_only", False))
                  for v in config["services"]["api"]["volumes"]}
        self.assertEqual(mounts, {
            ("/opt/cellestial/runtime-secrets", "/run/secrets/runtime", True),
            ("/opt/cellestial/runtime-playbook", "/app/deploy/runtime", True),
            ("/var/lib/cellestial/runtime", "/var/lib/cellestial/runtime", False),
        })
        # PEM-ключи — только файлами, не в .env
        for secret in ["ssh-key-body", "ca-key-body", "client-key-body"]:
            self.assertNotIn(secret, env)
        self.assertNotIn("RUNTIME_ENABLED", env)

    def test_runtime_validation(self):
        open_values = dict(RUNTIME_OPEN, app_backend_address="192.168.199.10")
        vault = RUNTIME_VAULT
        self.write_runtime_vault(vault)
        data = yaml.safe_load(self.inventory.read_text())
        data["all"]["children"]["backend"] = {"hosts": {"test-backend": {
            "ansible_host": "127.0.0.1", "ansible_connection": "local"}}}
        self.inventory.write_text(yaml.safe_dump(data))
        result = self.validate(BACKEND_REF, extra=open_values)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # id security group ещё не заполнен после pulumi up
        result = self.validate(BACKEND_REF, extra=dict(open_values, runtime_security_group_id=""))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime_*", result.stdout + result.stderr)
        # битый ключ шифрования не попадает в вывод
        broken = vault.replace(RUNTIME_SECRETS["runtime_secrets_key"], "short-leaked-key")
        self.write_runtime_vault(broken)
        result = self.validate(BACKEND_REF, extra=open_values)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("short-leaked-key", result.stdout + result.stderr)
        self.assertIn("vault_runtime_", result.stdout + result.stderr)
        # пропущенный секрет останавливает backend-деплой на проверке, до изменения сервера
        self.write_runtime_vault(vault.replace("vault_runtime_ca_key", "unused_ca_key"))
        result = self.validate(BACKEND_REF, extra=open_values)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Check the runtime VPS secrets", output)
        self.assertNotIn("Pull the backend", output)
        self.assertNotIn("ca-key-body", output)

    def test_bff_deploy_needs_no_runtime_configuration(self):
        variables = self.root / "group_vars/all/vars.yml"
        text = variables.read_text()
        for key in [*RUNTIME_OPEN, *RUNTIME_SECRETS]:
            text = "".join(line for line in text.splitlines(keepends=True)
                           if not line.startswith(f"{key}:"))
        variables.write_text(text)
        self.write_runtime_vault("")
        self.assertNotIn("runtime_", variables.read_text())
        result = self.validate_bff()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.validate(BACKEND_REF)
        self.assertNotEqual(result.returncode, 0)

    def write_runtime_vault(self, runtime_vault):
        password = b"test-vault-password-only"
        vault = VaultLib([("default", VaultSecret(password))])
        (self.root / "group_vars/all/vault.yml").write_bytes(vault.encrypt(
            b"vault_postgres_password: fake-database-password\nvault_jwt_secret: fake-jwt-secret\n"
            b"vault_csrf_secret: fake-csrf-secret\nvault_redis_password: fake-redis-password-0123\n"
            + BFF_VAULT + TOTP_VAULT + runtime_vault.encode()))


UUID = "01234567-89ab-cdef-0123-456789abcdef"
# Открытые runtime_* (vars.yml) в формате выходов Pulumi
RUNTIME_OPEN = {
    "runtime_provider": "openstack", "runtime_os_auth_url": "https://cloud.api.selcloud.ru/identity/v3",
    "runtime_os_username": "cellestialRuntimeProvisioner", "runtime_os_user_domain_name": "123456",
    "runtime_os_project_id": "a" * 32, "runtime_os_region": "ru-9", "runtime_availability_zone": "ru-9a",
    "runtime_network_id": UUID, "runtime_subnet_id": UUID, "runtime_security_group_id": UUID,
    "runtime_image_id": UUID, "runtime_flavor_id": "1012", "runtime_volume_type": "fast.ru-9a",
    "runtime_ssh_key_name": "runtime-provisioner", "runtime_ssh_user": "root",
    "runtime_env_name": "production",
    "runtime_blocked_public_ips": "203.0.113.1,203.0.113.2", "runtime_extra_env": {},
}
# Секреты runtime (vault.yml) — заглушки нужного формата
RUNTIME_SECRETS = {
    "runtime_os_password": "pass$word#with'quote",
    "runtime_secrets_key": "B" * 43 + "=",
    "runtime_ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nssh-key-body\n-----END OPENSSH PRIVATE KEY-----\n",
    "runtime_ca_cert": "-----BEGIN CERTIFICATE-----\nca-cert-body\n-----END CERTIFICATE-----\n",
    "runtime_ca_key": "-----BEGIN PRIVATE KEY-----\nca-key-body\n-----END PRIVATE KEY-----\n",
    "runtime_client_cert": "-----BEGIN CERTIFICATE-----\nclient-cert-body\n-----END CERTIFICATE-----\n",
    "runtime_client_key": "-----BEGIN PRIVATE KEY-----\nclient-key-body\n-----END PRIVATE KEY-----\n",
}
RUNTIME_VAULT = "".join(f"vault_{k}: {json.dumps(v)}\n" for k, v in RUNTIME_SECRETS.items())


if __name__ == "__main__":
    unittest.main()
