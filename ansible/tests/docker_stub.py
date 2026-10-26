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
                if value in ["login", "logout", "compose", "version", "pull", "image", "inspect"]), "")

if command == "version":
    print(json.dumps({"Client": {"Version": "28.0.0"}, "Server": {"ApiVersion": "1.48"}}))
elif command == "compose" and "version" in args:
    print(json.dumps({"version": "2.29.0"}))
else:
    with (root / "docker-events.jsonl").open("a") as stream:
        stream.write(json.dumps({"command": command, "args": args}) + "\n")
    if command == "login":
        token = sys.stdin.read().strip()
        config_path.write_text(json.dumps({"auths": {"ghcr.io": {"auth": token}}}))
        # Deliberately echo the fake token: Ansible must hide login output.
        print(token)
        sys.exit(1 if os.environ.get("TEST_DOCKER_FAILURE") == "login" else 0)
    elif command == "logout":
        config_path.write_text(json.dumps({"auths": {}}))
    elif command == "compose" and "pull" in args:
        anonymous_pull = os.environ.get("TEST_DOCKER_ANONYMOUS_PULL") == "1"
        if not anonymous_pull and "ghcr.io" not in json.loads(config_path.read_text())["auths"]:
            print("Error no registry authentication", file=sys.stderr)
            sys.exit(1)
        if os.environ.get("TEST_DOCKER_FAILURE") == "pull":
            print("Error controlled-pull-failure", file=sys.stderr)
            sys.exit(1)
    elif command == "pull":
        # анонимная выкатка BFF: свой пустой --config, без входа в реестр
        config = Path(args[args.index("--config") + 1]) if "--config" in args else None
        if config is None or any(config.iterdir()):
            print("Error pull without an empty --config", file=sys.stderr)
            sys.exit(1)
        if os.environ.get("TEST_DOCKER_FAILURE") == "pull":
            print("Error response from daemon: denied", file=sys.stderr)
            sys.exit(1)
        print("Status: Downloaded newer image for " + args[-1])
    elif command == "image" and "inspect" in args:
        fmt = args[args.index("--format") + 1]
        if "revision" in fmt:
            print(os.environ.get("TEST_DOCKER_REVISION", ""))
        else:
            print(os.environ.get("TEST_DOCKER_IMAGE_ID", "sha256:" + "1" * 64))
    elif command == "compose" and "ps" in args:
        print(os.environ.get("TEST_DOCKER_CONTAINER", ""))
    elif command == "inspect":
        print(os.environ.get("TEST_DOCKER_RUNNING", ""))
    else:
        print("Unexpected Docker CLI call", file=sys.stderr)
        sys.exit(1)
