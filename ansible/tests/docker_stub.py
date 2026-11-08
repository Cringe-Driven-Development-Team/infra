#!/usr/bin/env python3
"""Docker CLI substitute for registry pull/auth and image checks; never contacts Docker."""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["TEST_DOCKER_ROOT"])
config_path = root / "docker-config.json"
args = sys.argv[1:]
command = next((value for value in args
                if value in ["login", "logout", "compose", "version", "pull", "image", "inspect", "manifest", "network", "volume"]), "")

if command == "version":
    print(json.dumps({"Client": {"Version": "28.0.0"}, "Server": {"ApiVersion": "1.48"}}))
elif command == "compose" and "version" in args:
    print(json.dumps({"version": "2.29.0"}))
else:
    with (root / "docker-events.jsonl").open("a") as stream:
        event = {"command": command, "args": args}
        if command == "compose" and "--dry-run" in args and "up" in args:
            import yaml
            compose_path = Path(args[args.index("-f") + 1])
            event["compose"] = yaml.safe_load(compose_path.read_text())
            assert compose_path.stat().st_mode & 0o777 == 0o600
            for service in event["compose"]["services"].values():
                if "env_file" in service:
                    env = Path(service["env_file"])
                    assert env.exists() and env.stat().st_mode & 0o777 == 0o600
        stream.write(json.dumps(event) + "\n")
    if command == "login":
        token = sys.stdin.read().strip()
        config_path.write_text(json.dumps({"auths": {"ghcr.io": {"auth": token}}}))
        # Deliberately echo the fake token: Ansible must hide login output.
        print(token)
        sys.exit(1 if os.environ.get("TEST_DOCKER_FAILURE") == "login" else 0)
    elif command == "logout":
        config_path.write_text(json.dumps({"auths": {}}))
    elif command == "compose" and "pull" in args:
        if "githubbot" in args and "githubbot:" not in (root / "compose.yml").read_text():
            print("no such service: githubbot", file=sys.stderr)
            sys.exit(1)
        anonymous_pull = os.environ.get("TEST_DOCKER_ANONYMOUS_PULL") == "1"
        if not anonymous_pull and "ghcr.io" not in json.loads(config_path.read_text())["auths"]:
            print("Error no registry authentication", file=sys.stderr)
            sys.exit(1)
        if os.environ.get("TEST_DOCKER_FAILURE") == "pull":
            print("Error controlled-pull-failure", file=sys.stderr)
            sys.exit(1)
    elif command == "compose" and "config" in args:
        sys.exit(1 if os.environ.get("TEST_DOCKER_FAILURE") == "config" else 0)
    elif command == "compose" and "run" in args:
        if "--check-config" in args:
            # Echo a fake secret to prove no_log also hides a failing checker.
            print('test-$value-"x"')
            sys.exit(1 if os.environ.get("TEST_DOCKER_FAILURE") == "check" else 0)
        if "--user" not in args or "root" not in args or ("githubbot" in args and "--entrypoint" not in args):
            print("Unexpected preparation command", file=sys.stderr)
            sys.exit(1)
    elif command == "compose" and "up" in args:
        sys.exit(1 if os.environ.get("TEST_DOCKER_FAILURE") == "preview" else 0)
    elif command == "compose" and "images" in args:
        print("[]")
    elif command in ["pull", "manifest"]:
        # анонимная выкатка BFF: свой пустой --config, без входа в реестр
        config = Path(args[args.index("--config") + 1]) if "--config" in args else None
        if config is None or any(config.iterdir()):
            print("Error pull without an empty --config", file=sys.stderr)
            sys.exit(1)
        if os.environ.get("TEST_DOCKER_FAILURE") == ("manifest" if command == "manifest" else "pull"):
            print("Error response from daemon: denied", file=sys.stderr)
            sys.exit(1)
        print("Status: Downloaded newer image for " + args[-1])
    elif command in ["network", "volume"] and "inspect" in args:
        sys.exit(1 if os.environ.get("TEST_DOCKER_FAILURE") == command else 0)
    elif command == "image" and "inspect" in args:
        fmt = args[args.index("--format") + 1]
        if "revision" in fmt:
            print(os.environ.get("TEST_DOCKER_REVISION", ""))
        else:
            print(os.environ.get("TEST_DOCKER_IMAGE_ID", "sha256:" + "1" * 64))
    elif command == "compose" and "ps" in args:
        print(os.environ.get("TEST_DOCKER_PROXY", "proxy-container") if "telegram-proxy" in args else
              (args[-1] + "-container" if os.environ.get("TEST_DOCKER_DYNAMIC_INSPECT") else os.environ.get("TEST_DOCKER_CONTAINER", "")))
    elif command == "inspect" and "--format" in args and args[args.index("--format") + 1] == "{{.State.Running}}":
        print("true")
    elif command == "inspect" and os.environ.get("TEST_DOCKER_DYNAMIC_INSPECT"):
        import yaml
        service = args[-1].removesuffix("-container")
        compose = yaml.safe_load((root / "compose.yml").read_text())
        print(json.dumps([{"State": {"Running": True, "Health": {"Status": "healthy"}},
                          "Config": {"Image": compose["services"][service]["image"]},
                          "Image": "sha256:" + "1" * 64}]))
    elif command == "inspect":
        print(os.environ.get("TEST_DOCKER_STATE", os.environ.get("TEST_DOCKER_RUNNING", "")))
    else:
        print("Unexpected Docker CLI call", file=sys.stderr)
        sys.exit(1)
