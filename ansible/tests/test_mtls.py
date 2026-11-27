"""mtls-cert.yml (infra#160) on a local directory with a test CA: issue, keep, renew, rotate."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ANSIBLE = Path(__file__).resolve().parents[1]


def openssl(*args, check=True):
    return subprocess.run(['openssl', *args], capture_output=True, text=True, check=check)


class MtlsCertificates(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='cdd-mtls-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.host = self.root / 'opt'
        self.ca = self.new_ca('ca1')
        self.environment = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(self.root / 'tmp'),
                                ANSIBLE_REMOTE_TEMP=str(self.root / 'remote-tmp'), ANSIBLE_FORCE_COLOR='0')
        for name in ('ANSIBLE_VAULT_PASSWORD_FILE', 'ANSIBLE_VAULT_PASSWORD'):
            self.environment.pop(name, None)

    def new_ca(self, name):
        key, cert = self.root / f'{name}.key', self.root / f'{name}.crt'
        openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256', '-out', str(key))
        openssl('req', '-x509', '-new', '-key', str(key), '-subj', f'/CN={name}', '-days', '2', '-out', str(cert),
                '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        return cert, key

    def run_role(self, name='server', **extra):
        uid = os.getuid()
        defaults = yaml.safe_load((ANSIBLE / 'roles/app/defaults/main.yml').read_text())
        certs = {k: dict(v, owner=uid, group=os.getgid()) for k, v in defaults['app_mtls_certs'].items()}
        play = self.root / 'play.yml'
        play.write_text(yaml.safe_dump([{
            'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
            'vars_files': [str(ANSIBLE / 'roles/app/defaults/main.yml')],
            'vars': {'ansible_python_interpreter': sys.executable, 'app_compose_dir': str(self.host),
                     'bff_mtls_ca_cert': self.ca[0].read_text(), 'bff_mtls_ca_key': self.ca[1].read_text()},
            'tasks': [{'ansible.builtin.include_tasks': str(ANSIBLE / 'roles/app/tasks/mtls-cert.yml')}],
            'handlers': [{'name': 'Apply new mTLS files', 'ansible.builtin.debug': {'msg': 'APPLIED {{ app_mtls_name }}'}}],
        }]))
        result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(play), '-e', json.dumps({
                                'app_backend_address': '192.168.199.8', 'app_mtls_certs': certs, 'app_mtls_name': name, **extra})],
                                cwd=ANSIBLE, env=self.environment, capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn(self.ca[1].read_text().splitlines()[1], output)
        return output

    def text(self, name, *fields):
        return openssl('x509', '-in', str(self.host / 'mtls' / f'{name}.crt'), '-noout', *fields).stdout

    def test_server_certificate_lifecycle(self):
        mtls = self.host / 'mtls'
        short = dict(app_mtls_cert_days=3, app_mtls_renew_days=2, app_mtls_alert_days=1)
        output = self.run_role(**short)
        self.assertIn('APPLIED server', output)
        # ключ CA на хост не попадает, файлы — только владельцу
        self.assertEqual(sorted(p.name for p in mtls.iterdir()), ['ca.crt', 'server.crt', 'server.key'])
        for path in mtls.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o400, path)
        self.assertEqual(mtls.stat().st_mode & 0o777, 0o700)
        details = self.text('server', '-subject', '-ext', 'subjectAltName,extendedKeyUsage,basicConstraints',
                            '-nameopt', 'RFC2253')
        self.assertIn('subject=CN=cellestial-go-api', details)
        self.assertIn('IP Address:192.168.199.8', details)
        self.assertIn('TLS Web Server Authentication', details)
        self.assertNotIn('Client Authentication', details)
        self.assertIn('CA:FALSE', details)
        openssl('verify', '-CAfile', str(mtls / 'ca.crt'), '-purpose', 'sslserver', '-verify_ip', '192.168.199.8',
                str(mtls / 'server.crt'))
        serial = self.text('server', '-serial')

        # действующий сертификат не трогается
        self.assertNotIn('APPLIED', self.run_role(**short))
        self.assertEqual(self.text('server', '-serial'), serial)

        # меньше app_mtls_renew_days до истечения — перевыпуск на app_mtls_cert_days с тем же назначением
        self.assertIn('APPLIED server', self.run_role())
        self.assertNotEqual(self.text('server', '-serial'), serial)
        self.assertEqual(openssl('x509', '-in', str(mtls / 'server.crt'), '-noout', '-checkend',
                                 str(29 * 86400), check=False).returncode, 0)

        # смена адреса VPS2 — новый IP SAN
        self.assertIn('APPLIED server', self.run_role(app_backend_address='192.168.199.9'))
        self.assertIn('IP Address:192.168.199.9', self.text('server', '-ext', 'subjectAltName'))

    def test_ca_rotation_and_forced_replacement(self):
        mtls = self.host / 'mtls'
        legacy_client = {
            'client': {'cn': 'cellestial-bff', 'usage': 'clientAuth',
                       'owner': os.getuid(), 'group': os.getgid()},
        }
        self.run_role('client', app_mtls_certs=legacy_client)
        self.assertIn('APPLIED client', self.run_role('client'))
        details = self.text('client', '-subject', '-ext', 'extendedKeyUsage', '-nameopt', 'RFC2253')
        self.assertIn('subject=CN=cellestial-bff', details)
        self.assertIn('TLS Web Client Authentication', details)
        self.assertIn('DNS:bff.internal.cellestial.ru', self.text('client', '-ext', 'subjectAltName'))
        self.assertNotIn('IP Address', self.text('client', '-text'))
        old_cert = (mtls / 'client.crt').read_text()
        old_key = openssl('pkey', '-in', str(mtls / 'client.key'), '-pubout').stdout

        # аварийная замена ключа без смены CA
        self.assertIn('APPLIED client', self.run_role('client', app_mtls_force=True))
        self.assertNotEqual(openssl('pkey', '-in', str(mtls / 'client.key'), '-pubout').stdout, old_key)

        # новый CA: перевыпуск, прежний сертификат новому CA больше не доверен
        self.ca = self.new_ca('ca2')
        self.assertIn('APPLIED client', self.run_role('client'))
        self.assertEqual((mtls / 'ca.crt').read_text(), self.ca[0].read_text())
        old = self.root / 'old.crt'
        old.write_text(old_cert)
        self.assertNotEqual(openssl('verify', '-CAfile', str(mtls / 'ca.crt'), str(old), check=False).returncode, 0)
        openssl('verify', '-CAfile', str(mtls / 'ca.crt'), '-purpose', 'sslclient', str(mtls / 'client.crt'))
        self.assertIn('DNS:bff.internal.cellestial.ru', self.text('client', '-ext', 'subjectAltName'))

    def test_local_playbook_issues_a_client_certificate_for_the_current_user(self):
        home = self.root / 'home'
        result = subprocess.run(
            ['ansible-playbook', '-i', 'localhost,', str(ANSIBLE / 'local-mtls.yml'), '-e', json.dumps({
                'ansible_python_interpreter': sys.executable,
                'bff_mtls_ca_cert': self.ca[0].read_text(), 'bff_mtls_ca_key': self.ca[1].read_text()})],
            cwd=ANSIBLE, env=dict(self.environment, HOME=str(home)), capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        mtls = home / '.config/cellestial/mtls'
        self.assertEqual(sorted(p.name for p in mtls.iterdir()), ['ca.crt', 'client.crt', 'client.key'])
        self.assertEqual((mtls / 'client.key').stat().st_uid, os.getuid())
        openssl('verify', '-CAfile', str(self.ca[0]), '-purpose', 'sslclient', str(mtls / 'client.crt'))

    def test_thresholds_must_be_ordered(self):
        play_output = None
        with self.assertRaises(AssertionError) as error:
            play_output = self.run_role(app_mtls_cert_days=10, app_mtls_renew_days=10)
        self.assertIsNone(play_output)
        self.assertIn('app_mtls_cert_days > app_mtls_renew_days > 0', str(error.exception))


if __name__ == '__main__':
    unittest.main()
