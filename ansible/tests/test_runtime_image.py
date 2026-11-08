"""Образ runtime-VPS: Ansible передаёт backend тот же id, что зафиксирован в Pulumi (infra#130)."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class RuntimeImageTest(unittest.TestCase):
    def test_vars_match_pulumi(self):
        pulumi = yaml.safe_load((ROOT / 'pulumi/Pulumi.prod.yaml').read_text())['config']
        ansible = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())
        self.assertRegex(pulumi['infra:runtimeImageId'], r'^[0-9a-f-]{36}$')
        self.assertEqual(ansible['runtime_image_id'], pulumi['infra:runtimeImageId'])


if __name__ == '__main__':
    unittest.main()
