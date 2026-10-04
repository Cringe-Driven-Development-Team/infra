#!/usr/bin/env python3
"""Docker CLI substitute for temporary-registry-auth tests; never contacts Docker."""
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["TEST_DOCKER_ROOT"])
config_path = root / "docker-config.json"
args = sys.argv[1:]
command = next((value for value in args if value in ["login", "logout", "compose", "version"]), "")

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
        if "ghcr.io" not in json.loads(config_path.read_text())["auths"]:
            print("Error no registry authentication", file=sys.stderr)
            sys.exit(1)
        if os.environ.get("TEST_DOCKER_FAILURE") == "pull":
            print("Error controlled-pull-failure", file=sys.stderr)
            sys.exit(1)
    else:
        print("Unexpected Docker CLI call", file=sys.stderr)
        sys.exit(1)
