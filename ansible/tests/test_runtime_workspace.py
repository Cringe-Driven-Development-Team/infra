"""Проверки контракта дискового workspace."""
from pathlib import Path
import unittest

import yaml

ROLES = Path(__file__).resolve().parents[1] / "runtime/roles"


class RuntimeWorkspaceTest(unittest.TestCase):
    def test_workspace_precedes_agent(self):
        tasks = (ROLES / "runtime_host/tasks/main.yml").read_text()
        self.assertLess(tasks.index("Pull the Python kernel image"), tasks.index("import_tasks: workspace.yml"))
        dropin = (ROLES / "runtime_agent/templates/workspace-dropin.conf.j2").read_text()
        self.assertIn("RequiresMountsFor=/srv/cellestial/workspace", dropin)
        self.assertIn("ReadWritePaths=/srv/cellestial/workspace", dropin)

    def test_agent_prepares_shared_data_and_private_staging_before_start(self):
        tasks = yaml.safe_load((ROLES / "runtime_agent/tasks/main.yml").read_text())
        start = next(index for index, task in enumerate(tasks) if task["name"] == "Start the agent")
        directories = {
            task["ansible.builtin.file"]["path"]: task["ansible.builtin.file"]
            for task in tasks[:start]
            if "ansible.builtin.file" in task
        }
        data = directories["/srv/cellestial/workspace/data"]
        staging = directories["/srv/cellestial/workspace/staging"]
        self.assertEqual((data["owner"], data["group"], data["mode"]), ("{{ runtime_agent_user }}", "65532", "2770"))
        self.assertEqual((staging["owner"], staging["mode"]), ("{{ runtime_agent_user }}", "0700"))

    def test_agent_joins_workspace_group_and_checks_running_process(self):
        tasks = yaml.safe_load((ROLES / "runtime_agent/tasks/main.yml").read_text())
        groups = next(task["ansible.builtin.group"] for task in tasks if "ansible.builtin.group" in task)
        account = next(task["ansible.builtin.user"] for task in tasks if "ansible.builtin.user" in task)
        self.assertEqual(groups["gid"], 65532)
        self.assertIn(groups["name"], account["groups"])
        self.assertIn("docker", account["groups"])
        names = [task["name"] for task in tasks]
        self.assertLess(names.index("Start the agent"), names.index("Read the kernel credentials of the running agent"))
        self.assertEqual(names[-1], "Verify the running agent can use the kernel workspace group")

    def test_proxy_streams_parts_in_both_directions(self):
        proxy = (ROLES / "runtime_host/templates/nginx-agent.conf.j2").read_text()
        for directive in ("client_max_body_size 16m;", "proxy_request_buffering off;", "proxy_buffering off;"):
            self.assertIn(directive, proxy)
