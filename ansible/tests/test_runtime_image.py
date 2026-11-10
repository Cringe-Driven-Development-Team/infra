"""Образ runtime-VPS: id совпадает в Pulumi и Ansible (infra#130), состав и плейбук — одной версии (infra#134)."""
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class RuntimeImageTest(unittest.TestCase):
    def test_vars_match_pulumi(self):
        pulumi = yaml.safe_load((ROOT / 'pulumi/Pulumi.prod.yaml').read_text())['config']
        ansible = yaml.safe_load((ROOT / 'ansible/inventory/group_vars/all/vars.yml').read_text())
        self.assertRegex(pulumi['infra:runtimeImageId'], r'^[0-9a-f-]{36}$')
        self.assertEqual(ansible['runtime_image_id'], pulumi['infra:runtimeImageId'])

    def test_image_composition_is_pinned(self):
        env = dict(re.findall(r'^([A-Z_]+)="?([^"\n]*)"?$', (ROOT / 'runtime-image/versions.env').read_text(), re.M))
        defaults = yaml.safe_load((ROOT / 'ansible/runtime/roles/runtime_host/defaults/main.yml').read_text())
        self.assertEqual(env['IMAGE_VERSION'], defaults['runtime_host_image_version'])
        self.assertRegex(env['UBUNTU_SNAPSHOT'], r'^[0-9]{8}T[0-9]{6}Z$')
        for package in (env['APT_PACKAGES'] + ' ' + env['DOCKER_PACKAGES']).split():
            self.assertRegex(package, r'^[a-z0-9.+-]+=[^=\s]+$', package)
        for ref in ('PYTHON_KERNEL_IMAGE', 'BACKEND_IMAGE'):
            self.assertRegex(env[ref], r'@sha256:[0-9a-f]{64}$')


if __name__ == '__main__':
    unittest.main()
