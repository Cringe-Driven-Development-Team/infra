"""GitHubBot role checks use synthetic secrets; optional local image tests stay offline."""
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from ansible.plugins.filter.core import to_json
from jinja2 import Environment, StrictUndefined
import yaml

from test_hooks_release import state as release_state

ANSIBLE = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/cringe-driven-development-team/githubbot@sha256:" + "a" * 64
SOURCE = "ghcr.io/cringe-driven-development-team/githubbot:sha-" + "b" * 40
CONFIG = {"chatId": "-1001", "notificationBotIds": ["900"],
          "repositories": [{"name": "backend", "defaultPrefix": "Backend.", "prefixes": ["Backend."]}],
          "topics": [{"threadId": 11, "repository": "backend"}],
          "users": [{"telegramId": "101", "username": "Alice", "githubLogin": "octo"}]}
ENV = {"DATABASE_PATH": "/data/githubbot.sqlite", "TELEGRAM_PROXY_URL": "http://telegram-proxy:2080",
       "TELEGRAM_BOT_TOKEN": "private-test-telegram", "GITHUB_TOKEN": 'test-$value-"x"',
       "GITHUBBOT_CONFIG_JSON": json.dumps(CONFIG)}


def render(variables):
    templates = Environment(undefined=StrictUndefined)
    templates.filters["to_json"] = to_json
    return yaml.safe_load(templates.from_string(
        (ANSIBLE / "roles/hooks/templates/compose.yml.j2").read_text()).render(variables))


class GithubBotChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cdd-githubbot-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.environment = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(self.root / "tmp"),
                                ANSIBLE_REMOTE_TEMP=str(self.root / "remote-tmp"))
        self.environment.pop("ANSIBLE_VAULT_PASSWORD_FILE", None)
        self.environment.pop("ANSIBLE_VAULT_PASSWORD", None)
        self.variables = yaml.safe_load((ANSIBLE / "roles/hooks/defaults/main.yml").read_text()) | {
            "hooks_githubbot_image": IMAGE, "hooks_githubbot_image_source_tag": SOURCE,
            "hooks_githubbot_env": ENV.copy(), "hooks_domain": "hooks.example.invalid", "caddy_image": "caddy:test",
            "hooks_env": {"DATABASE_PATH": "/data/queue.sqlite", "PORT": "8080",
                          "TELEGRAM_PROXY_URL": "http://telegram-proxy:2080", "GITHUB_WEBHOOK_SECRET": "fake",
                          "APIDOG_SIGNATURE_KEY": "fake", "APIDOG_SIGNATURE_HEADER": "X-Test"},
            "vault_hooks_proxy_config": {
                "inbounds": [{"listen": "0.0.0.0", "port": 2080, "protocol": "http", "settings": {}}],
                "outbounds": [{"tag": "blocked", "protocol": "blackhole"}, {
                    "tag": "telegram-vless", "protocol": "vless",
                    "settings": {"vnext": [{"address": "example.invalid", "port": 443, "users": [{
                        "id": "00000000-0000-4000-8000-000000000000", "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
                    "streamSettings": {"network": "raw", "security": "reality", "realitySettings": {
                        "fingerprint": "chrome", "serverName": "example.invalid", "password": "A" * 43, "shortId": ""}}}],
                "routing": {"rules": [{"type": "field", "domain": ["full:api.telegram.org"], "outboundTag": "telegram-vless"}]}}}
        self.variables['hooks_release_state'] = release_state()
        self.variables['hooks_image'] = self.variables['hooks_release_state']['services']['webhook-alerts']['desired']['image_ref']
        self.variables['hooks_image_source_tag'] = 'ghcr.io/cringe-driven-development-team/webhook-alerts:sha-' + 'b' * 40
        self.inventory = self.root / "inventory.yml"
        self.inventory.write_text(yaml.safe_dump({"all": {"hosts": {"local": {
            "ansible_connection": "local", "ansible_python_interpreter": sys.executable}}}}))

    def play(self, tasks, variables, check=False):
        path = self.root / "play.yml"
        path.write_text(yaml.safe_dump([{"name": "Exercise GitHubBot tasks", "hosts": "all", "gather_facts": False,
                                        "vars": variables, "tasks": tasks}]))
        return subprocess.run([shutil.which("ansible-playbook"), "-i", str(self.inventory), str(path),
                               *(["--check"] if check else []), "--diff"], cwd=ANSIBLE, env=self.environment,
                              text=True, capture_output=True, timeout=45)

    def run_tasks(self, relative_path, variables):
        return self.play([{"name": "Run production tasks", "ansible.builtin.import_tasks": str(ANSIBLE / relative_path)}], variables)

    def fake_docker(self):
        binary = self.root / "bin"
        binary.mkdir()
        shutil.copyfile(ANSIBLE / "tests/docker_stub.py", binary / "docker")
        (binary / "docker").chmod(0o700)
        self.environment.update(PATH=str(binary) + os.pathsep + self.environment["PATH"],
                                TEST_DOCKER_ROOT=str(self.root), TEST_DOCKER_ANONYMOUS_PULL="1")
        (self.root / "compose.yml").write_text("services: {}\n")

    def test_compose_contract(self):
        compose = render(self.variables)
        service = compose["services"]["githubbot"]
        self.assertNotIn("ports", service)
        self.assertEqual(service["env_file"], "githubbot.env")
        self.assertEqual(service["volumes"], ["githubbot_data:/data"])
        self.assertEqual(service["networks"], ["telegram_proxy"])
        self.assertEqual(service["depends_on"], ["telegram-proxy"])
        self.assertEqual(service["restart"], "unless-stopped")
        self.assertEqual(service["image"], IMAGE)
        self.assertNotIn("command", service)
        self.assertNotIn("deploy", service)
        self.assertEqual(service["healthcheck"], {"test": ["CMD", "bun", "run", "src/main.ts", "--health"],
                         "interval": "30s", "timeout": "5s", "retries": 3, "start_period": "40s"})
        self.assertIn("githubbot_data", compose["volumes"])

    def test_preflight_rejects_missing_or_invalid_values_before_changes(self):
        cases = [{"hooks_githubbot_image": ""}, {"hooks_githubbot_image": "githubbot:latest"},
                 {"hooks_githubbot_image": IMAGE.replace("ghcr.io", "ghcrXio")},
                 {"hooks_githubbot_image_source_tag": ""}, {"hooks_githubbot_env": []}]
        for key in ["TELEGRAM_BOT_TOKEN", "GITHUB_TOKEN", "GITHUBBOT_CONFIG_JSON"]:
            cases.append({"hooks_githubbot_env": {k: v for k, v in ENV.items() if k != key}})
        for key, value in [("GITHUBBOT_CONFIG_JSON", "{bad-json"), ("GITHUBBOT_CONFIG_JSON", "[]"),
                           ("GITHUB_TOKEN", "line\nbreak"), ("GITHUB_TOKEN", "trailing\n"), ("GITHUB_TOKEN", None),
                           ("DATABASE_PATH", "/tmp/wrong"),
                           ("TELEGRAM_PROXY_URL", "http://other:2080")]:
            cases.append({"hooks_githubbot_env": ENV | {key: value}})
        for case in cases:
            with self.subTest(case=list(case)):
                result = self.run_tasks("roles/hooks/tasks/validate.yml", self.variables | case)
                output = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0, output)
                self.assertNotIn("TASK [Каталог проекта Compose]", output)
                env = case.get("hooks_githubbot_env", ENV)
                for key in ["GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "GITHUBBOT_CONFIG_JSON"]:
                    value = env.get(key) if isinstance(env, dict) else None
                    if isinstance(value, str) and len(value) > 3:
                        self.assertNotIn(value, output)

    def test_shared_token_preflight_and_defaults(self):
        defaults = yaml.safe_load((ANSIBLE / "roles/hooks/defaults/main.yml").read_text())
        for legacy in [None, "conflicting-old-token"]:
            with self.subTest(legacy_token=legacy):
                variables = self.variables | {"hooks_githubbot_env": defaults["hooks_githubbot_env"],
                    "vault_githubbot_env": {k: ENV[k] for k in ["GITHUB_TOKEN", "GITHUBBOT_CONFIG_JSON"]}}
                variables["hooks_env"] = self.variables["hooks_env"] | {"TELEGRAM_BOT_TOKEN": "shared-effective-token"}
                if legacy:
                    variables["vault_githubbot_env"]["TELEGRAM_BOT_TOKEN"] = legacy
                tasks = [{"name": "Check shared token and fixed runtime paths", "ansible.builtin.assert": {"that": [
                    "hooks_githubbot_env.TELEGRAM_BOT_TOKEN == hooks_env.TELEGRAM_BOT_TOKEN",
                    "hooks_githubbot_env.DATABASE_PATH == '/data/githubbot.sqlite'",
                    "hooks_githubbot_env.TELEGRAM_PROXY_URL == 'http://telegram-proxy:2080'"]}}]
                result = self.play(tasks, variables)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = self.run_tasks("roles/hooks/tasks/validate.yml", variables)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for token in [None, ""]:
                    with self.subTest(shared_token=token):
                        env = variables["hooks_env"].copy()
                        env.pop("TELEGRAM_BOT_TOKEN")
                        if token is not None:
                            env["TELEGRAM_BOT_TOKEN"] = token
                        result = self.run_tasks("roles/hooks/tasks/validate.yml", variables | {"hooks_env": env})
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("GitHubBot", result.stdout + result.stderr)
                        self.assertNotIn("conflicting-old-token", result.stdout + result.stderr)

    def test_secret_template_preserves_compose_environment_without_disclosure(self):
        tasks = yaml.safe_load((ANSIBLE / "roles/hooks/tasks/main.yml").read_text())
        tasks = next(t["block"] for t in tasks if "block" in t)
        task = copy.deepcopy(next(t for t in tasks if t.get("ansible.builtin.template", {}).get("src") == "githubbot.env.j2"))
        self.assertTrue(task["no_log"])
        self.assertFalse(task["diff"])
        self.assertEqual(task["ansible.builtin.template"]["mode"], "0600")
        task["ansible.builtin.template"]["src"] = str(ANSIBLE / "roles/hooks/templates/githubbot.env.j2")
        variables = self.variables | {"hooks_compose_dir": str(self.root)}
        result = self.play([task], variables)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for value in ["test-$value", ENV["TELEGRAM_BOT_TOKEN"], "Alice"]:
            self.assertNotIn(value, result.stdout + result.stderr)
        self.assertEqual((self.root / "githubbot.env").stat().st_mode & 0o777, 0o600)
        (self.root / ".env").write_text("GITHUB_WEBHOOK_SECRET=separate-webhook\n")
        (self.root / "compose.yml").write_text(yaml.safe_dump(render(self.variables)))
        result = subprocess.run([*shlex.split(os.environ.get("COMPOSE_CLI", "docker compose")), "-f",
                                 str(self.root / "compose.yml"), "config", "--format", "json"],
                                capture_output=True, text=True, check=True)
        service = json.loads(result.stdout)["services"]["githubbot"]
        # Compose config escapes dollars for its YAML/JSON output; runtime is checked below.
        self.assertEqual({key: value.replace("$$", "$") for key, value in service["environment"].items()}, ENV)
        self.assertNotIn("GITHUB_WEBHOOK_SECRET", service["environment"])

    def test_pull_or_checker_failure_prevents_up(self):
        self.fake_docker()
        main = yaml.safe_load((ANSIBLE / "roles/hooks/tasks/main.yml").read_text())
        main = next(t["block"] for t in main if "block" in t)
        prepare_index = next(i for i, t in enumerate(main) if t.get("ansible.builtin.import_tasks") == "prepare-githubbot.yml")
        up_index = next(i for i, t in enumerate(main) if "community.docker.docker_compose_v2" in t)
        self.assertLess(prepare_index, up_index)
        tasks = [{"name": "Prepare GitHubBot", "ansible.builtin.import_tasks": str(ANSIBLE / "roles/hooks/tasks/prepare-githubbot.yml")}, main[up_index]]
        for failure in ["pull", "check", ""]:
            with self.subTest(failure=failure):
                events_path = self.root / "docker-events.jsonl"
                events_path.unlink(missing_ok=True)
                self.environment["TEST_DOCKER_FAILURE"] = failure
                result = self.play(tasks, self.variables | {"hooks_compose_dir": str(self.root)})
                self.assertEqual(result.returncode == 0, failure == "", result.stdout + result.stderr)
                events = [json.loads(line)["args"] for line in events_path.read_text().splitlines()]
                self.assertEqual(sum("up" in e for e in events), int(not failure))
                self.assertEqual(sum("--check-config" in e for e in events), int(failure != "pull"))
                pull = next(e for e in events if "pull" in e)
                self.assertEqual(pull[-1], IMAGE)
                self.assertIn("--config", pull)
                self.assertFalse(Path(pull[pull.index("--config") + 1]).exists())
                self.assertNotIn("hooks", pull)
                for event in events:
                    self.assertNotIn("down", event)
                for value in ["test-$value", ENV["TELEGRAM_BOT_TOKEN"], "Alice"]:
                    self.assertNotIn(value, result.stdout + result.stderr)

    def test_first_deployment_preview(self):
        main = yaml.safe_load((ANSIBLE / "roles/hooks/tasks/main.yml").read_text())
        self.assertEqual([t["ansible.builtin.import_tasks"] for t in main[:2]], ["release-state.yml", "validate.yml"])
        preview = next(t for t in main if t.get("ansible.builtin.import_tasks") == "preview.yml")
        self.assertEqual(preview["when"], "ansible_check_mode")
        apply = next(t for t in main if "block" in t)
        self.assertEqual(apply["when"], "not ansible_check_mode")
        self.assertTrue(any("ansible.builtin.uri" in t for t in main))
        self.fake_docker()
        old = "name: hooks\nservices:\n  hooks:\n    image: old:deployed\n"
        (self.root / "compose.yml").write_text(old)
        for failure in ["manifest", "render", "preview", ""]:
            with self.subTest(failure=failure):
                events_path = self.root / "docker-events.jsonl"
                events_path.unlink(missing_ok=True)
                self.environment["TEST_DOCKER_FAILURE"] = failure
                variables = self.variables | {"hooks_compose_dir": str(self.root), "role_path": str(ANSIBLE / "roles/hooks")}
                if failure == "render":
                    variables["hooks_githubbot_env"] = []
                result = self.play([{"name": "Preview role", "ansible.builtin.import_tasks": str(ANSIBLE / "roles/hooks/tasks/preview.yml")}],
                                   variables, check=True)
                self.assertEqual(result.returncode == 0, not failure, result.stdout + result.stderr)
                events = [json.loads(line) for line in events_path.read_text().splitlines()]
                manifest = next(e["args"] for e in events if "manifest" in e["args"])
                self.assertEqual(manifest[-1], IMAGE)
                self.assertFalse(Path(manifest[manifest.index("--config") + 1]).exists())
                ups = [e for e in events if "up" in e["args"]]
                self.assertEqual(len(ups), int(failure not in ["manifest", "render"]))
                if ups:
                    args = ups[0]["args"]
                    self.assertIn("--dry-run", args)
                    self.assertIn("--renew-anon-volumes", args)
                    self.assertIn("--wait", args)
                    self.assertEqual(args[args.index("--wait-timeout") + 1], "180")
                    self.assertEqual(args[args.index("--project-directory") + 1], str(self.root))
                    self.assertEqual(ups[0]["compose"]["name"], "hooks")
                    self.assertEqual(ups[0]["compose"]["services"]["githubbot"]["image"], IMAGE)
                    self.assertEqual(ups[0]["compose"]["services"]["caddy"]["volumes"][0],
                                     "./Caddyfile:/etc/caddy/Caddyfile:ro")
                    self.assertFalse(Path(args[args.index("-f") + 1]).exists())
                self.assertFalse(any(e["command"] == "pull" or "run" in e["args"] for e in events))
                self.assertEqual((self.root / "compose.yml").read_text(), old)
                self.assertFalse((self.root / "githubbot.env").exists())
                for value in [ENV["GITHUB_TOKEN"], ENV["TELEGRAM_BOT_TOKEN"], "Alice"]:
                    self.assertNotIn(value, result.stdout + result.stderr)

    def test_native_preview_recreates_mounted_service_with_uncached_image_without_changes(self):
        if os.environ.get("COMPOSE_CLI") != "docker compose":
            self.skipTest("Set COMPOSE_CLI='docker compose' for the offline native Docker regression")
        project = "githubbot-preview-" + self.root.name.removeprefix("cdd-githubbot-test-").replace("_", "a")
        image = project + ":old"
        missing = "localhost:9/preview/missing@sha256:" + "f" * 64

        def docker(*args, check=True):
            return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=45)

        compose = ["compose", "--project-directory", str(self.root), "-f", str(self.root / "compose.yml")]
        (self.root / "marker").write_text("fixture-owned data\n")
        (self.root / "Dockerfile").write_text('FROM scratch\nCOPY marker /marker\nCMD ["/never-executed"]\n')
        docker("build", "--network=none", "--pull=false", "-t", image, str(self.root))
        self.addCleanup(lambda: docker("image", "rm", image, check=False))
        config = {"name": project, "services": {"githubbot": {
            "image": image, "network_mode": "none", "volumes": ["data:/data", "./marker:/bind-marker:ro"]}},
            "volumes": {"data": {}}}
        path = self.root / "compose.yml"
        path.write_text(yaml.safe_dump(config))
        self.addCleanup(lambda: docker(*compose, "down", "--volumes", check=False))
        docker(*compose, "create", "--pull", "never")
        container = docker(*compose, "ps", "--all", "--quiet", "githubbot").stdout.strip()
        self.assertTrue(container)
        docker("cp", str(self.root / "marker"), container + ":/data/marker")
        volume = project + "_data"
        self.assertNotEqual(docker("image", "inspect", missing, check=False).returncode, 0)
        config["services"]["githubbot"]["image"] = missing
        path.write_text(yaml.safe_dump(config))
        (self.root / "env").write_text("")
        (self.root / "docker").mkdir()
        tasks = yaml.safe_load((ANSIBLE / "roles/hooks/tasks/preview.yml").read_text())
        task = next(t for t in tasks[1]["block"] if "--dry-run" in t.get("ansible.builtin.command", {}).get("argv", []))
        template = Environment(undefined=StrictUndefined)
        argv = [template.from_string(arg).render(hooks_preview={"path": str(self.root)},
                                                hooks_compose_dir=str(self.root))
                for arg in task["ansible.builtin.command"]["argv"]]

        def snapshot():
            inspected = json.loads(docker("inspect", container).stdout)
            inspected[0]["Mounts"].sort(key=lambda mount: mount["Destination"])
            return (inspected,
                    sorted(docker("image", "ls", "--all", "--no-trunc", "--digests", "--format", "{{.ID}} {{.Repository}} {{.Tag}} {{.Digest}}").stdout.splitlines()),
                    json.loads(docker("volume", "inspect", volume).stdout),
                    subprocess.run(["docker", "cp", container + ":/data/marker", "-"],
                                   capture_output=True, check=True, timeout=45).stdout,
                    path.read_bytes(), (self.root / "marker").read_bytes())

        # Created-only scratch containers cannot satisfy --wait; retain the task's recreation argv.
        self.assertIn("--wait", argv)
        wait_timeout = argv.index("--wait-timeout")
        del argv[wait_timeout:wait_timeout + 2]
        argv.remove("--wait")
        before = snapshot()
        self.assertEqual(before[0][0]["State"]["Status"], "created")
        result = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        self.assertEqual(snapshot(), before, "dry-run mutated fixture resources")
        self.assertNotEqual(docker("image", "inspect", missing, check=False).returncode, 0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Recreate", result.stdout + result.stderr)

    def test_verify_requires_exactly_one_running_healthy_container(self):
        self.fake_docker()
        plays = yaml.safe_load((ANSIBLE / "verify.yml").read_text())
        verify = [p for p in plays if p["name"] == "Проверка GitHubBot на вспомогательной VPS"]
        self.assertEqual(len(verify), 1, "verify.yml must execute GitHubBot health checks on auxiliary")
        self.assertEqual(verify[0]["hosts"], "auxiliary")
        for containers, state, succeeds in [
            ("", {"Running": True, "Health": {"Status": "healthy"}}, False),
            ("first\nsecond", {"Running": True, "Health": {"Status": "healthy"}}, False),
            ("first", {"Running": False, "Health": {"Status": "healthy"}}, False),
            ("first", {"Running": True, "Health": {"Status": "unhealthy"}}, False),
            ("first", {"Running": True}, False),
            ("first", {"Running": True, "Health": {"Status": "healthy"}}, True),
        ]:
            with self.subTest(containers=containers, state=state):
                events_path = self.root / "docker-events.jsonl"
                events_path.unlink(missing_ok=True)
                self.environment.update(TEST_DOCKER_CONTAINER=containers, TEST_DOCKER_STATE=json.dumps(state))
                result = self.play(verify[0]["tasks"], self.variables | {"hooks_compose_dir": str(self.root)})
                self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
                events = [json.loads(line)["args"] for line in events_path.read_text().splitlines()]
                self.assertEqual(events[0], ["compose", "ps", "--all", "--quiet", "githubbot"])
                inspected = [e for e in events if "inspect" in e]
                self.assertEqual(len(inspected), int(containers == "first"))
                if inspected:
                    self.assertEqual(inspected[0], ["inspect", "--format", "{{json .State}}", "first"])

    def test_real_image_config_checker_and_root_owned_sqlite_survive_recreate(self):
        image = os.environ.get("GITHUBBOT_TEST_IMAGE")
        if not image:
            self.skipTest("Set GITHUBBOT_TEST_IMAGE to verify real Bun config/SQLite in an offline local image")
        project = "githubbot-test-" + self.root.name.removeprefix("cdd-githubbot-test-")
        (self.root / "compose.yml").write_text(yaml.safe_dump({"name": project, "services": {"githubbot": {
            "image": image, "init": True, "network_mode": "none", "env_file": "githubbot.env", "volumes": ["data:/data"]}}, "volumes": {"data": {}}}))
        template = Environment(undefined=StrictUndefined)
        template.filters["to_json"] = to_json
        (self.root / "githubbot.env").write_text(template.from_string(
            (ANSIBLE / "roles/hooks/templates/githubbot.env.j2").read_text()).render(self.variables))
        compose = ["docker", "compose", "-f", str(self.root / "compose.yml")]
        self.addCleanup(lambda: subprocess.run([*compose, "down", "--volumes"], capture_output=True))
        def run(*args):
            return subprocess.run([*compose, "run", "--rm", "--no-deps", "--pull", "never", *args], capture_output=True, text=True, timeout=40)
        result = run("githubbot", "bun", "-e", "console.log(JSON.stringify(Object.fromEntries(Object.entries(process.env).filter(([k])=>" + json.dumps(list(ENV)) + ".includes(k)))))")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), ENV)
        result = run("githubbot", "bun", "run", "src/main.ts", "--check-config")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        duplicate = copy.deepcopy(CONFIG)
        duplicate["users"].append(duplicate["users"][0])
        topic = copy.deepcopy(CONFIG)
        topic["topics"][0]["threadId"] = 0
        for config in ["{invalid", json.dumps(duplicate), json.dumps(topic)]:
            result = run("-e", "GITHUBBOT_CONFIG_JSON=" + config, "githubbot", "bun", "run", "src/main.ts", "--check-config")
            self.assertNotEqual(result.returncode, 0)
            for secret in ["test-$value", ENV["TELEGRAM_BOT_TOKEN"]]:
                self.assertNotIn(secret, result.stdout + result.stderr)
        result = run("--user", "root", "--entrypoint", "sh", "githubbot", "-c", "chown root:root /data && chmod 700 /data && touch /data/root-marker")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(run("githubbot", "sh", "-c", "touch /data/denied").returncode, 0)
        tasks = yaml.safe_load((ANSIBLE / "roles/hooks/tasks/prepare-githubbot.yml").read_text())
        prepare = next(t for t in tasks if "--user" in t.get("ansible.builtin.command", {}).get("argv", []))
        result = self.play([prepare], self.variables | {"hooks_compose_dir": str(self.root)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Kill a child PID (init:true) after commit to retain a real WAL for migration.
        create = "import {Database} from 'bun:sqlite'; const db=new Database('/data/githubbot.sqlite'); db.exec(\"PRAGMA journal_mode=WAL; CREATE TABLE pending (repo TEXT); INSERT INTO pending VALUES ('backend');\"); process.kill(process.pid, 'SIGKILL');"
        result = run("githubbot", "bun", "-e", create)
        self.assertEqual(result.returncode, 137, result.stdout + result.stderr)
        result = run("--user", "root", "--entrypoint", "sh", "githubbot", "-c",
                     "test -s /data/githubbot.sqlite-wal && chown -R root:root /data")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        subprocess.run([*compose, "down"], check=True, capture_output=True)
        result = self.play([prepare], self.variables | {"hooks_compose_dir": str(self.root)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        read = "import {Database} from 'bun:sqlite'; import {statSync} from 'node:fs'; statSync('/data/root-marker'); const db=new Database('/data/githubbot.sqlite'); if(db.query('SELECT repo FROM pending').get().repo!=='backend') process.exit(1); db.exec(\"INSERT INTO pending VALUES ('frontend')\"); if((statSync('/data').mode & 511)!==448) process.exit(2);"
        result = run("githubbot", "bun", "-e", read)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
