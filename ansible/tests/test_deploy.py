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
from ansible.plugins.filter.core import to_json
from jinja2 import Environment, StrictUndefined
import yaml

ANSIBLE = Path(__file__).resolve().parents[1]


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
                b"vault_csrf_secret: fake-csrf-secret\n"
            )
        )
        password_file = self.root / "vault-pass"
        password_file.write_bytes(password)
        password_file.chmod(0o600)
        self.environment["ANSIBLE_VAULT_PASSWORD_FILE"] = str(password_file)

    def validate(self, tag=None):
        command = [
            shutil.which("ansible-playbook") or "ansible-playbook",
            "-i",
            str(self.inventory),
            str(ANSIBLE / "deploy-backend.yml"),
            "--tags",
            "validation",
        ]
        if tag is not None:
            command += ["-e", json.dumps({"backend_image_tag": tag})]
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
            )
        )
        result = self.validate("sha-1234567")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("vault_csrf_secret", result.stdout + result.stderr)

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
            "vars": {"app_compose_dir": str(self.root),
                     "ansible_python_interpreter": sys.executable},
            "tasks": [{"name": "Run the production auth block",
                       "ansible.builtin.import_tasks": str(
                           ANSIBLE / "roles/app/tasks/pull-api.yml")}],
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
        variables.update(backend_image_tag="sha-1234567", s3_region="ru-7",
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
                # Canonical Compose output escapes literal dollars for reuse as YAML.
                for service in ["api", "postgres"]:
                    self.assertEqual(
                        services[service]["environment"]["POSTGRES_PASSWORD"].replace(
                            "$$", "$"
                        ),
                        secret,
                    )
                    self.assertFalse(services[service].get("ports"))
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

    def test_caddy_preserves_frontend_in_both_states(self):
        templates = Environment(undefined=StrictUndefined)
        template = templates.from_string(
            (ANSIBLE / "roles/caddy/templates/Caddyfile.j2").read_text()
        )
        for deployed in [False, True]:
            rendered = template.render(app_domain="cellestial.ru",
                frontend_s3_domain="frontend.example.org",
                caddy_app_environment={"stat": {"exists": deployed}})
            self.assertIn("reverse_proxy https://frontend.example.org", rendered)
            self.assertIn("header_up -Cookie", rendered)
            self.assertIn("header_up -Authorization", rendered)
            self.assertIn("rewrite * /index.html?", rendered)
            self.assertEqual("reverse_proxy api:8080" in rendered, deployed)
            self.assertEqual('respond "API is not deployed" 503' in rendered, not deployed)


if __name__ == "__main__":
    unittest.main()
