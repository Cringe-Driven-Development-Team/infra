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

    def test_bff_deploy_requires_its_own_tag(self):
        result = self.validate("sha-1234567", "deploy-bff.yml", "bff_image_tag")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for tag in [None, "", "latest", "sha-1234567;echo bad"]:
            with self.subTest(tag=tag):
                result = self.validate(tag, "deploy-bff.yml", "bff_image_tag")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("bff_image_tag=sha-", result.stdout + result.stderr)
        # тег бэка необязателен, но если передан — проверяется
        result = self.validate("sha-1234567", "deploy-bff.yml", "bff_image_tag",
                               {"backend_image_tag": "main"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backend_image_tag=sha-", result.stdout + result.stderr)

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
                result = self.validate("sha-1234567", "deploy-bff.yml", "bff_image_tag")
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("vault_session_key must be base64", output)
                self.assertNotIn("short-leaky-key", output)

    def test_deployment_keeps_the_other_deployed_tag(self):
        playbook = self.root / "tags-test.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": "Exercise the real tag selection", "hosts": "gateway", "gather_facts": False,
            "vars": {"app_compose_dir": str(self.root),
                     "ansible_python_interpreter": sys.executable},
            "tasks": [
                {"name": "Choose tags",
                 "ansible.builtin.import_tasks": str(ANSIBLE / "roles/app/tasks/tags.yml")},
                {"name": "Show tags", "ansible.builtin.debug": {
                    "msg": "TAGS {{ app_backend_tag }} {{ app_bff_tag | default('-', true) }}"}},
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

        deployed = "BACKEND_IMAGE_TAG=sha-aaaaaaa\nBFF_IMAGE_TAG=sha-bbbbbbb\nJWT_SECRET=\"leaky\"\n"
        cases = [
            (deployed, {"bff_image_tag": "sha-ccccccc"}, "TAGS sha-aaaaaaa sha-ccccccc"),
            (deployed, {"backend_image_tag": "sha-ddddddd"}, "TAGS sha-ddddddd sha-bbbbbbb"),
            ("BACKEND_IMAGE_TAG=sha-aaaaaaa\n", {"backend_image_tag": "sha-ddddddd"},
             "TAGS sha-ddddddd -"),
            (None, {"backend_image_tag": "sha-ddddddd"}, "TAGS sha-ddddddd -"),
        ]
        for env_file, tags, expected in cases:
            with self.subTest(tags=tags, deployed=env_file):
                result = run(env_file, **tags)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                self.assertIn(expected, output)
                self.assertNotIn("leaky", output)
        # BFF без выкаченного бэка не выкатывается
        result = run(None, bff_image_tag="sha-ccccccc")
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
            "vars": {"app_compose_dir": str(self.root), "app_deploy_service": "bff",
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
        variables.update(app_backend_tag="sha-1234567", app_bff_tag="sha-7654321",
                         app_domain="app.example.org", s3_region="ru-7",
                         s3_endpoint="https://s3.example.org", notebooks_bucket="notebooks",
                         avatars_bucket="avatars", avatars_public_domain="avatars.example.org",
                         app_cors_allowed_origins="https://app.example.org")
        templates = Environment(undefined=StrictUndefined)
        templates.filters["to_json"] = to_json
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
                # BFF: свой тег, ключи из Vault, один S2S-ключ с Go, порт не публикуется
                bff = services["bff"]
                self.assertEqual(bff["image"], "ghcr.io/cringe-driven-development-team/bff:sha-7654321")
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
        variables.update(app_bff_tag="")
        (self.root / "compose.yml").write_text(compose_template.render(variables))
        (self.root / ".env").write_text(env_template.render(variables))
        result = subprocess.run(
            [*compose, "-f", str(self.root / "compose.yml"), "config", "--format", "json"],
            capture_output=True, text=True, timeout=30, check=True, env=compose_environment)
        self.assertNotIn("bff", json.loads(result.stdout)["services"])
        self.assertNotIn("BFF_IMAGE_TAG", (self.root / ".env").read_text())

    def test_registry_switch_and_explicit_rollback_image(self):
        variables = yaml.safe_load((ANSIBLE / "roles/app/defaults/main.yml").read_text())
        variables.update(yaml.safe_load((ANSIBLE / "roles/caddy/defaults/main.yml").read_text()))
        template = Environment(undefined=StrictUndefined).from_string(
            (ANSIBLE / "roles/app/templates/compose.yml.j2").read_text())
        expected = "ghcr.io/cringe-driven-development-team/backend"
        for override in [None, "ghcr.io/go-park-mail-ru/2026_2_cringe_driven_development"]:
            with self.subTest(override=override):
                values = dict(variables, app_bff_tag="")
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
