"""Проверки контракта дискового workspace."""
from pathlib import Path
import unittest

ROLES = Path(__file__).resolve().parents[1] / "runtime/roles"


class RuntimeWorkspaceTest(unittest.TestCase):
    def test_workspace_precedes_agent(self):
        tasks = (ROLES / "runtime_host/tasks/main.yml").read_text()
        self.assertLess(tasks.index("Pull the Python kernel image"), tasks.index("import_tasks: workspace.yml"))
        dropin = (ROLES / "runtime_agent/templates/workspace-dropin.conf.j2").read_text()
        self.assertIn("RequiresMountsFor=/srv/cellestial/workspace", dropin)
        self.assertIn("ReadWritePaths=/srv/cellestial/workspace", dropin)

    def test_proxy_streams_parts_in_both_directions(self):
        proxy = (ROLES / "runtime_host/templates/nginx-agent.conf.j2").read_text()
        for directive in ("client_max_body_size 16m;", "proxy_request_buffering off;", "proxy_buffering off;"):
            self.assertIn(directive, proxy)
