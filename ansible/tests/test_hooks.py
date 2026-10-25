"""Exercise rendered backup paths with real Restic and anonymous pulls with Ansible.

RESTIC_CLI may point to an unpacked binary; tests never contact a VPS or registry.
"""
import configparser
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from ansible.plugins.filter.core import to_json
from jinja2 import Environment, StrictUndefined
import yaml

ANSIBLE = Path(__file__).resolve().parents[1]


class HooksChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cdd-hooks-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.environment = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(self.root / "ansible-tmp"),
                                ANSIBLE_REMOTE_TEMP=str(self.root / "remote-tmp"))
        self.environment.pop("GHCR_USERNAME", None)
        self.environment.pop("GHCR_TOKEN", None)
        self.environment.pop("ANSIBLE_VAULT_PASSWORD_FILE", None)
        self.environment.pop("ANSIBLE_VAULT_PASSWORD", None)
        self.inventory = self.root / "inventory.yml"
        self.inventory.write_text(yaml.safe_dump({"all": {"hosts": {"local": {
            "ansible_connection": "local", "ansible_python_interpreter": sys.executable}}}}))
        self.defaults = yaml.safe_load((ANSIBLE / "roles/hooks/defaults/main.yml").read_text())
        self.group_vars = yaml.safe_load((ANSIBLE / "inventory/group_vars/auxiliary/vars.yml").read_text())
        self.variables = self.defaults | self.group_vars
        self.variables["vault_hooks_env"] = {
            "GITHUB_WEBHOOK_SECRET": "fake-github-secret",
            "APIDOG_SIGNATURE_KEY": "fake-apidog-secret",
            "APIDOG_SIGNATURE_HEADER": "X-Test-Signature"}

    def play(self, tasks, variables=None, check=False):
        playbook = self.root / "test.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": "Exercise production tasks locally", "hosts": "all", "gather_facts": False,
            "vars": variables if variables is not None else self.variables, "tasks": tasks}]))
        return subprocess.run([shutil.which("ansible-playbook"), "-i", str(self.inventory),
                               str(playbook), *(["--check", "--diff"] if check else [])],
                              cwd=ANSIBLE, env=self.environment, text=True,
                              capture_output=True, timeout=40)

    def import_task(self, path):
        return [{"name": "Run production tasks", "ansible.builtin.import_tasks": str(ANSIBLE / path)}]

    def test_preflight_accepts_no_registry_credentials_and_rejects_invalid_webhook_configuration(self):
        tasks = self.import_task("roles/hooks/tasks/validate.yml")
        result = self.play(tasks)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for key in ["GITHUB_WEBHOOK_SECRET", "APIDOG_SIGNATURE_KEY", "APIDOG_SIGNATURE_HEADER"]:
            with self.subTest(missing=key):
                variables = self.variables | {"vault_hooks_env": self.variables["vault_hooks_env"].copy()}
                del variables["vault_hooks_env"][key]
                result = self.play(tasks, variables)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stdout + result.stderr)
                self.assertNotIn("fake-github-secret", result.stdout + result.stderr)
        for key, value in [("DATABASE_PATH", "/data/wrong.sqlite"), ("PORT", "9000"),
                           ("WEBHOOK_ALERTS_SEND_ENABLED", False)]:
            with self.subTest(invalid=key):
                variables = self.variables | {"vault_hooks_env": self.variables["vault_hooks_env"] | {key: value}}
                result = self.play(tasks, variables)
                self.assertNotEqual(result.returncode, 0)

    def test_anonymous_pull_runs_without_login_and_stops_deployment_on_registry_error(self):
        binary_directory = self.root / "bin"
        binary_directory.mkdir()
        docker = binary_directory / "docker"
        shutil.copyfile(ANSIBLE / "tests/docker_stub.py", docker)
        docker.chmod(0o700)
        self.environment.update(PATH=str(binary_directory) + os.pathsep + self.environment["PATH"],
                                TEST_DOCKER_ROOT=str(self.root), TEST_DOCKER_ANONYMOUS_PULL="1")
        (self.root / "compose.yml").write_text("services: {}\n")
        marker = self.root / "prepared"
        tasks = self.import_task("roles/hooks/tasks/pull-image.yml") + [{
            "name": "Mark that deployment reached container preparation",
            "ansible.builtin.copy": {"dest": str(marker), "content": "ready", "mode": "0600"}}]
        for failure in ["", "pull"]:
            with self.subTest(failure=failure):
                events_path = self.root / "docker-events.jsonl"
                events_path.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                self.environment["TEST_DOCKER_FAILURE"] = failure
                result = self.play(tasks, self.variables | {"hooks_compose_dir": str(self.root)})
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode == 0, failure == "", output)
                self.assertTrue(events_path.exists(), output)
                events = [json.loads(line) for line in events_path.read_text().splitlines()]
                self.assertEqual([event["command"] for event in events], ["compose"])
                self.assertIn("hooks", events[0]["args"])
                self.assertNotIn("caddy", events[0]["args"])
                self.assertFalse((self.root / "docker-config.json").exists())
                self.assertEqual(marker.exists(), failure == "")
                if failure:
                    self.assertIn("публичный", output)

    def test_compose_uses_image_entrypoint_and_preserves_webhook_secrets(self):
        templates = Environment(undefined=StrictUndefined)
        templates.filters["to_json"] = to_json
        variables = self.variables | {"hooks_env": {"GITHUB_WEBHOOK_SECRET": "space # dollar${FOO}'quote\\slash"},
                                      "hooks_telegram_api_ip": "149.154.167.220"}
        (self.root / "compose.yml").write_text(templates.from_string(
            (ANSIBLE / "roles/hooks/templates/compose.yml.j2").read_text()).render(variables))
        (self.root / ".env").write_text(templates.from_string(
            (ANSIBLE / "roles/hooks/templates/env.j2").read_text()).render(variables))
        result = subprocess.run([*shlex.split(os.environ.get("COMPOSE_CLI", "docker compose")),
                                 "-f", str(self.root / "compose.yml"), "config", "--format", "json"],
                                capture_output=True, text=True, check=True)
        service = json.loads(result.stdout)["services"]["hooks"]
        self.assertRegex(service["image"], r"^ghcr.io/cringe-driven-development-team/webhook-alerts@sha256:[0-9a-f]{64}$")
        self.assertIsNone(service.get("command"))
        self.assertFalse(service.get("ports"))
        self.assertEqual(service["extra_hosts"], ["api.telegram.org=149.154.167.220"])
        self.assertNotIn("extra_hosts", json.loads(result.stdout)["services"]["caddy"])
        self.assertEqual(service["environment"]["GITHUB_WEBHOOK_SECRET"].replace("$$", "$"),
                         "space # dollar${FOO}'quote\\slash")

    def test_rendered_unit_archives_snapshot_and_caddy_but_never_live_database(self):
        restic = os.environ.get("RESTIC_CLI") or shutil.which("restic")
        if not restic:
            self.skipTest("Install restic or set RESTIC_CLI to test real backup contents")
        variables = yaml.safe_load((ANSIBLE / "roles/restic_backup/defaults/main.yml").read_text())
        rendered_path = self.root / "restic-backup.service"
        result = self.play([{"name": "Render the production systemd unit with Ansible",
                             "ansible.builtin.template": {
                                 "src": str(ANSIBLE / "roles/restic_backup/templates/restic-backup.service.j2"),
                                 "dest": str(rendered_path), "mode": "0644"}}],
                           self.variables | variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        unit = configparser.ConfigParser(interpolation=None)
        unit.read_string(rendered_path.read_text())
        self.assertTrue(unit.has_option("Service", "ExecStartPre"), "A verified snapshot must precede restic backup")
        before = shlex.split(unit["Service"]["ExecStartPre"])
        self.assertEqual(before, ["/usr/bin/docker", "compose", "-f", "/opt/hooks/compose.yml", "exec", "-T",
                                  "hooks", "bun", "run", "src/cli.ts", "snapshot", "--output", "/data/snapshots/queue.sqlite"])
        # Exercise the rendered Restic arguments against a real repository. Docker snapshot
        # CLI semantics are covered in the service repo; seed its verified output here.
        data = self.root / "var/lib/docker/volumes/hooks_hooks_data/_data"
        (data / "snapshots").mkdir(parents=True)
        live = sqlite3.connect(data / "queue.sqlite")
        self.addCleanup(live.close)
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE deliveries (source TEXT, delivery_id TEXT UNIQUE, status TEXT)")
        live.executemany("INSERT INTO deliveries VALUES ('github', ?, ?)", [("pending-id", "pending"), ("sent-id", "sent")])
        live.commit()
        with sqlite3.connect(data / "snapshots/queue.sqlite") as snapshot:
            live.backup(snapshot)
        caddy = self.root / "var/lib/docker/volumes/hooks_caddy_data/_data"
        caddy.mkdir(parents=True)
        (caddy / "certificate").write_text("test certificate")
        (self.root / "var/lib/docker/volumes/hooks_caddy_config/_data").mkdir(parents=True)
        (self.root / "opt/hooks").mkdir(parents=True)
        (self.root / "opt/hooks/compose.yml").write_text("test compose")
        environment = self.environment | {"RESTIC_REPOSITORY": str(self.root / "repository"), "RESTIC_PASSWORD": "test-only"}
        subprocess.run([restic, "init"], env=environment, capture_output=True, check=True)
        arguments = shlex.split(unit["Service"]["ExecStart"])
        arguments[0] = restic
        arguments = [str(self.root / arg.lstrip("/")) if arg.startswith(("/var/lib/", "/opt/")) else arg for arg in arguments]
        subprocess.run(arguments, env=environment, capture_output=True, check=True)
        listing = subprocess.run([restic, "ls", "--json", "latest"], env=environment,
                                 capture_output=True, text=True, check=True)
        nodes = [json.loads(line)["path"] for line in listing.stdout.splitlines() if json.loads(line).get("struct_type") == "node"]
        self.assertIn(str(data / "snapshots/queue.sqlite"), nodes)
        self.assertIn(str(caddy / "certificate"), nodes)
        self.assertIn(str(self.root / "opt/hooks/compose.yml"), nodes)
        for suffix in ["queue.sqlite", "queue.sqlite-wal", "queue.sqlite-shm"]:
            self.assertNotIn(str(data / suffix), nodes)
        restored = self.root / "restore"
        subprocess.run([restic, "restore", "latest", "--target", str(restored)],
                       env=environment, capture_output=True, check=True)
        with sqlite3.connect(restored / str(data / "snapshots/queue.sqlite").lstrip("/")) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT delivery_id, status FROM deliveries ORDER BY delivery_id").fetchall(),
                             [("pending-id", "pending"), ("sent-id", "sent")])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO deliveries VALUES ('github', 'pending-id', 'pending')")

    def test_prepare_data_allows_bun_to_write_existing_root_owned_volume(self):
        image = os.environ.get("WEBHOOK_ALERTS_TEST_IMAGE")
        if not image:
            self.skipTest("Set WEBHOOK_ALERTS_TEST_IMAGE to a local webhook-alerts image for the ownership migration check")
        project = "hooks-test-" + self.root.name.replace("cdd-hooks-test-", "")
        compose_file = self.root / "compose.yml"
        compose_file.write_text(yaml.safe_dump({"name": project, "services": {"hooks": {
            "image": image, "volumes": ["data:/data"]}}, "volumes": {"data": {}}}))
        compose = ["docker", "compose", "-f", str(compose_file)]
        def run(*args):
            return subprocess.run([*compose, "run", "--rm", "--no-deps", "--pull", "never", *args],
                                  capture_output=True, text=True, timeout=40)
        self.addCleanup(lambda: subprocess.run([*compose, "down", "--volumes"], capture_output=True))
        result = run("--user", "root", "hooks", "sh", "-c", "chown root:root /data && chmod 700 /data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(run("hooks", "sh", "-c", "touch /data/write-test").returncode, 0)
        result = self.play(self.import_task("roles/hooks/tasks/prepare-data.yml"),
                           self.variables | {"hooks_compose_dir": str(self.root)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run("hooks", "sh", "-c", "test -d /data/snapshots && touch /data/write-test /data/snapshots/write-test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verification_fails_when_backup_snapshot_is_missing(self):
        snapshot = self.root / "snapshots/queue.sqlite"
        variables = {"restic_backup_snapshot_path": str(snapshot)}
        tasks = self.import_task("tasks/verify-hooks-snapshot.yml")
        result = self.play(tasks, variables)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restic-backup", result.stdout + result.stderr)
        snapshot.parent.mkdir()
        with sqlite3.connect(snapshot) as db:
            db.execute("CREATE TABLE deliveries (id INTEGER)")
        result = self.play(tasks, variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validation_and_templates_support_local_check_mode(self):
        tasks = self.import_task("roles/hooks/tasks/validate.yml")
        for name in ["compose.yml", "env"]:
            tasks.append({"name": "Check production template " + name,
                          "ansible.builtin.template": {
                              "src": str(ANSIBLE / "roles/hooks/templates" / (name + ".j2")),
                              "dest": str(self.root / name), "mode": "0600"},
                          "no_log": name == "env"})
        result = self.play(tasks, check=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "compose.yml").exists())
        self.assertFalse((self.root / "env").exists())
