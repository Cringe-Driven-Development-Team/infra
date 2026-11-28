"""Exercise production Ansible tasks with local mTLS/HTTPS; never contact production."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import yaml

ANSIBLE = Path(__file__).resolve().parents[1]
RELEASE = 'b' * 40
ADMIN_HTML = ('<html><head><title>OnlineConf · Cellestial</title><meta name="release" content="' + RELEASE + '">'
              '<script type="module" crossorigin src="https://{cdn}/releases/' + RELEASE + '/assets/index-a.js"></script>'
              '<link rel="stylesheet" href="https://{cdn}/releases/' + RELEASE + '/assets/index-b.css"></head></html>')
VALID_LOGIN = {'error': {'code': 'BAD_REQUEST', 'message': 'Неверный логин или пароль', 'appCode': 'invalid_credentials'}}


class VerifyBackendProbeScope(unittest.TestCase):
    def test_verify_limits_client_certificate_to_s2s_checks(self):
        plays = yaml.safe_load((ANSIBLE / 'verify.yml').read_text())
        probe = 'roles/app/tasks/probe-backend.yml'
        play = next(play for play in plays if any(
            task.get('ansible.builtin.import_tasks') == probe for task in play.get('tasks', [])
        ))
        uri = 'ansible.builtin.uri'
        certificate_args = {'client_cert', 'client_key'}
        self.assertTrue(certificate_args.isdisjoint(play.get('module_defaults', {}).get(uri, {})))

        s2s_check = next(task for task in play['tasks'] if 'block' in task)
        self.assertTrue(certificate_args.issubset(s2s_check['module_defaults'][uri]))
        self.assertEqual([task[uri]['status_code'] for task in s2s_check['block']], [403, 401])


class BackendProbes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='cdd-http-tests-')
        cls.root = Path(cls.directory.name)
        cls.cert, key = cls.root / 'cert.pem', cls.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                        '-out', str(cls.cert), '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
        # mTLS VPS2 (infra#160): CA, серверный сертификат с IP SAN и клиентский BFF — как раскладывает mtls-cert.yml
        cls.mtls = cls.root / 'mtls'
        cls.mtls.mkdir()
        def issue(name, subject, extensions):
            subprocess.run(['openssl', 'req', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                            '-keyout', str(cls.mtls / f'{name}.key'), '-out', str(cls.mtls / f'{name}.crt'),
                            '-subj', subject, '-days', '1', '-CA', str(cls.mtls / 'ca.crt'),
                            '-CAkey', str(cls.root / 'ca.key'), *sum((['-addext', e] for e in [
                                'basicConstraints=critical,CA:FALSE', 'keyUsage=critical,digitalSignature',
                                *extensions]), [])],
                           check=True, capture_output=True)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                        '-keyout', str(cls.root / 'ca.key'), '-out', str(cls.mtls / 'ca.crt'), '-days', '1',
                        '-subj', '/CN=Test BFF-Go CA', '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
                        '-addext', 'keyUsage=critical,keyCertSign,cRLSign'], check=True, capture_output=True)
        issue('server', '/CN=cellestial-go-api', ['subjectAltName=IP:127.0.0.1', 'extendedKeyUsage=serverAuth'])
        issue('client', '/CN=cellestial-bff', ['extendedKeyUsage=clientAuth'])
        cls.requests = []
        cls.health = (200, {'status': 'alive'})
        cls.login = (400, VALID_LOGIN)
        cls.hidden = (404, 'not found')
        cls.admin = ADMIN_HTML
        cls.asset_type = 'text/javascript'
        cls.cors = '*'
        cls.sequence = []
        cls.timeout_paths = set()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.do_GET()

            def do_GET(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                cls.requests.append((self.path, self.command, dict(self.headers), raw))
                if self.path in cls.timeout_paths:
                    time.sleep(6)
                headers = {}
                if self.path == '/api/auth/login':
                    status, value = cls.login
                elif self.path == '/health' and self.server is cls.http:
                    status, value = cls.sequence.pop(0) if cls.sequence else cls.health
                elif self.path == '/login':
                    status, value = 200, ('<html><meta name="release" content="' + RELEASE + '">'
                        '<script id="bootstrap" type="application/json">{"user":null}</script></html>')
                    headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                               'Strict-Transport-Security': 'max-age=31536000',
                               'Content-Security-Policy-Report-Only': "default-src 'none'"}
                elif self.path == '/onlineconf/keys':
                    status, value = 200, cls.admin.replace('{cdn}', f'localhost:{cls.https.server_port}')
                    headers = {'Cache-Control': 'private, no-store'}
                elif self.path.startswith('/releases/'):
                    status, value = 200, 'asset'
                    headers = {'Content-Type': cls.asset_type if self.path.endswith('.js') else 'text/css'}
                    if cls.cors:
                        headers['Access-Control-Allow-Origin'] = cls.cors
                elif self.path in ('/api/users/me', '/api/onlineconf/access'):
                    status, value = 401, {'error': {'code': 'UNAUTHORIZED', 'message': 'Нет сессии'}}
                else:
                    status, value = cls.hidden
                data = json.dumps(value).encode() if isinstance(value, dict) else str(value).encode()
                self.send_response(status)
                if 'Content-Type' not in headers:
                    self.send_header('Content-Type', 'application/json' if isinstance(value, dict) else 'text/html')
                if status in (301, 302, 307, 308):
                    self.send_header('Location', '/login')
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass  # Expected when a timeout fixture outlives the client socket.

        # cls.http — приватный Go за mTLS-прокси: без клиентского сертификата CA соединение обрывается
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        mtls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        mtls.load_cert_chain(cls.mtls / 'server.crt', cls.mtls / 'server.key')
        mtls.load_verify_locations(cls.mtls / 'ca.crt')
        mtls.verify_mode = ssl.CERT_REQUIRED
        cls.http.socket = mtls.wrap_socket(cls.http.socket, server_side=True)
        cls.https = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.https.socket = context.wrap_socket(cls.https.socket, server_side=True)
        for server in (cls.http, cls.https):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.http, cls.https):
            server.shutdown()
            server.server_close()
        cls.directory.cleanup()

    def setUp(self):
        type(self).requests, type(self).sequence = [], []
        type(self).timeout_paths = set()
        type(self).health = (200, {'status': 'alive'})
        type(self).login = (400, VALID_LOGIN)
        type(self).hidden = (404, 'not found')
        type(self).admin = ADMIN_HTML
        type(self).asset_type = 'text/javascript'
        type(self).cors = '*'
        self.environment = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(self.root / 'tmp'),
                                ANSIBLE_REMOTE_TEMP=str(self.root / 'remote-tmp'),
                                ANSIBLE_FORCE_COLOR='0')
        for name in ('ANSIBLE_VAULT_PASSWORD_FILE', 'ANSIBLE_VAULT_PASSWORD'):
            self.environment.pop(name, None)

    def tasks(self, name):
        path = ANSIBLE / 'roles/app/tasks' / name
        self.assertTrue(path.is_file(), 'Missing production probe: ' + name)
        tasks = copy.deepcopy(yaml.safe_load(path.read_text()))
        for task in tasks:
            # Only shorten delays, drop sudo and remap the private mTLS port to an ephemeral local fixture.
            if 'delay' in task:
                task['delay'] = 0
            task.pop('become', None)
            uri = task.get('ansible.builtin.uri', {})
            uri['url'] = uri.get('url', '').replace(':8443/health', f':{self.http.server_port}/health')
        return tasks

    def run_tasks(self, tasks, variables=None, host_variables=None):
        inventory = self.root / 'inventory.yml'
        data = {'all': {'children': {
            'gateway': {'hosts': {'gateway-test': {'ansible_connection': 'local', 'ansible_host': '127.0.0.2'}}},
            'backend': {'hosts': {'backend-test': {'ansible_connection': 'local', 'ansible_host': '127.0.0.1'}}}}}}
        for group, values in (host_variables or {}).items():
            host = next(iter(data['all']['children'][group]['hosts'].values()))
            host.update(values)
        inventory.write_text(yaml.safe_dump(data))
        play = self.root / 'test.yml'
        play.write_text(yaml.safe_dump([{'name': 'Exercise real probes', 'hosts': 'backend', 'gather_facts': False,
            'vars': {'ansible_python_interpreter': sys.executable, 'backend_probe_address': '127.0.0.1',
                     'app_mtls_dir': str(self.mtls),
                     'app_domain': f'localhost:{self.https.server_port}', 'bff_probe_release': RELEASE,
                     'frontend_cdn_domain': f'localhost:{self.https.server_port}',
                     **(variables or {})},
            'module_defaults': {'ansible.builtin.uri': {'ca_path': str(self.cert)}}, 'tasks': tasks}]))
        return subprocess.run(['ansible-playbook', '-i', str(inventory), str(play)], env=self.environment,
                              cwd=ANSIBLE, capture_output=True, text=True, timeout=90)

    def test_internal_probe_requires_alive_json(self):
        tasks = self.tasks('probe-backend.yml')
        for status, body, passes in [(200, {'status': 'alive'}, True), (200, {'status': 'wrong'}, False),
                                    (200, '{broken', False), (302, 'redirect', False),
                                    (503, {'status': 'alive'}, False)]:
            with self.subTest(status=status, body=body):
                type(self).requests = []
                type(self).health = status, body
                result = self.run_tasks(tasks)
                self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)
                self.assertEqual(len(self.requests), 1 if passes else 6)
                self.assertIn('backend-test -> gateway-test', result.stdout)

    def test_internal_probe_retries_full_json_predicate(self):
        type(self).sequence = [(200, {'status': 'wrong'}), (200, {'status': 'alive'})]
        result = self.run_tasks(self.tasks('probe-backend.yml'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.requests), 2)

    def test_login_probe_requires_invalid_credentials(self):
        tasks = self.tasks('probe-login.yml')
        cases = [(400, VALID_LOGIN, True), (401, VALID_LOGIN, False),
                 (400, {'error': {'code': 'BAD_REQUEST', 'message': 'x'}}, False),
                 (400, {'error': {'code': 'UNAUTHORIZED', 'appCode': 'invalid_credentials', 'message': 'x'}}, False),
                 (400, {'error': {'code': 'BAD_REQUEST', 'appCode': 'invalid_bff_key', 'message': 'x'}}, False),
                 (403, {'error': {'code': 'FORBIDDEN', 'appCode': 'csrf', 'message': 'x'}}, False),
                 (400, '<html>denied</html>', False), (400, '{bad-json', False),
                 (500, {}, False), (200, VALID_LOGIN, False), (302, 'redirect', False)]
        previous_login = None
        for status, body, passes in cases:
            with self.subTest(status=status, body=body):
                type(self).requests = []
                type(self).login = status, body
                result = self.run_tasks(tasks)
                self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)
                calls = [r for r in self.requests if r[0] == '/api/auth/login']
                self.assertEqual(len(calls), 1 if passes else 6)
                self.assertEqual(len({r[3] for r in calls}), 1)
                payload = json.loads(calls[0][3])
                self.assertRegex(payload['login'], r'^[0-9a-f]{32}$')
                self.assertNotEqual(payload['login'], previous_login)
                previous_login = payload['login']
                for _, method, headers, raw in calls:
                    payload = json.loads(raw)
                    self.assertEqual(method, 'POST')
                    self.assertEqual(payload['password'], 'deploy-probe-invalid-password')
                    headers = {k.lower(): v for k, v in headers.items()}
                    self.assertEqual(headers['origin'], f'https://localhost:{self.https.server_port}')
                    self.assertEqual(headers['x-csrf'], '1')
                    self.assertEqual(headers['content-type'], 'application/json')
                    self.assertNotIn('cookie', headers)
                    self.assertNotIn('x-bff-key', headers)

    def test_bff_probe_passes_when_backend_login_returns_502(self):
        type(self).login = 502, {'error': {'code': 'BAD_GATEWAY'}}
        result = self.run_tasks(self.tasks('probe-bff.yml'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('/login', {request[0] for request in self.requests})
        self.assertNotIn('/api/auth/login', {request[0] for request in self.requests})

    def test_hidden_routes_require_non_html_404(self):
        tasks = self.tasks('probe-bff.yml')
        for status, body, passes in [(404, 'not found', True), (404, '<HTML>app</HTML>', False),
                                    (200, 'public API', False), (302, 'redirect', False)]:
            with self.subTest(status=status, body=body):
                type(self).requests = []
                type(self).hidden = status, body
                result = self.run_tasks(tasks)
                self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)
                if passes:
                    self.assertTrue({'/api/v1/users/me', '/api/unknown', '/health'}.issubset(
                        {r[0] for r in self.requests}))

    def test_admin_spa_requires_its_html_and_cdn_assets(self):
        tasks = self.tasks('probe-bff.yml')
        product = ('<html><title>OnlineConf</title><meta name="release" content="' + RELEASE + '">'
                   '<script id="bootstrap" type="application/json">{"user":null}</script></html>')
        site = f'https://localhost:{self.https.server_port}'
        for admin, asset_type, cors, passes in [
                (ADMIN_HTML, 'text/javascript', '*', True), (ADMIN_HTML, 'text/javascript', site, True),
                (ADMIN_HTML, 'text/javascript', '', False), (ADMIN_HTML, 'text/javascript', 'https://evil', False),
                (ADMIN_HTML, 'text/html', '*', False), (product, 'text/javascript', '*', False),
                (ADMIN_HTML.replace(RELEASE, 'c' * 40), 'text/javascript', '*', False),
                (ADMIN_HTML.replace('https://{cdn}', ''), 'text/javascript', '*', False)]:
            with self.subTest(admin=admin, asset_type=asset_type, cors=cors):
                type(self).requests = []
                type(self).admin, type(self).asset_type, type(self).cors = admin, asset_type, cors
                result = self.run_tasks(tasks)
                self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)
                if passes:
                    paths = {r[0] for r in self.requests}
                    self.assertIn('/api/onlineconf/access', paths)
                    self.assertTrue({p for p in paths if p.startswith('/releases/')} == {
                        f'/releases/{RELEASE}/assets/index-a.js', f'/releases/{RELEASE}/assets/index-b.css'})

    def test_socket_timeouts_fail_with_at_most_six_requests(self):
        for filename, path in [('probe-backend.yml', '/health'), ('probe-login.yml', '/api/auth/login')]:
            with self.subTest(probe=filename):
                type(self).requests = []
                type(self).timeout_paths = {path}
                result = self.run_tasks(self.tasks(filename))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len([r for r in self.requests if r[0] == path]), 6)

    def test_unavailable_internal_api_fails(self):
        tasks = self.tasks('probe-backend.yml')
        # Reserve an unused local port without listening, so connection attempts are refused.
        with socket.socket() as unused:
            unused.bind(('127.0.0.1', 0))
            tasks[0]['ansible.builtin.uri']['url'] = f'https://127.0.0.1:{unused.getsockname()[1]}/health'
            result = self.run_tasks(tasks)
        self.assertNotEqual(result.returncode, 0)

    def test_failed_internal_probe_does_not_run_external(self):
        type(self).health = 503, {}
        result = self.run_tasks(self.tasks('probe-backend.yml') + self.tasks('probe-login.yml'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(r[0] == '/api/auth/login' for r in self.requests))

    def test_failed_external_probe_preserves_previous_success_in_log(self):
        type(self).login = 500, {}
        result = self.run_tasks(self.tasks('probe-backend.yml') + self.tasks('probe-login.yml'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Backend internal mTLS check', result.stdout)
        self.assertIn('ok: [backend-test -> gateway-test', result.stdout)
        self.assertTrue(any(r[0] == '/api/auth/login' for r in self.requests))

    def test_real_backend_post_tasks_succeed_without_a_bff_release(self):
        # Keep the actual production post_tasks and imported task arguments. Only map external
        # dependencies to local TLS/HTTP fixtures and the existing Docker CLI substitute.
        production = next(play for play in yaml.safe_load((ANSIBLE / 'deploy-backend.yml').read_text())
                          if 'post_tasks' in play)
        tasks = copy.deepcopy(production['post_tasks'])
        for task in tasks:
            imported = task.pop('ansible.builtin.import_tasks', None)
            if imported:
                task['block'] = self.tasks(Path(imported).name)
        gateway = self.root / 'gateway'
        backend = self.root / 'backend'
        for directory in (gateway, backend):
            directory.mkdir(exist_ok=True)
        binary = self.root / 'bin'
        binary.mkdir(exist_ok=True)
        shutil.copyfile(ANSIBLE / 'tests/docker_stub.py', binary / 'docker')
        (binary / 'docker').chmod(0o700)
        image_id = 'sha256:' + '1' * 64
        self.environment.update(PATH=str(binary) + os.pathsep + os.environ['PATH'],
                                TEST_DOCKER_ROOT=str(self.root), TEST_DOCKER_CONTAINER='cid',
                                TEST_DOCKER_IMAGE_ID=image_id)
        hosts = {'gateway': {'app_compose_dir': str(gateway)},
                 'backend': {'app_compose_dir': str(backend)}}
        variables = {'backend_image_ref': 'ghcr.io/cringe-driven-development-team/backend@sha256:' + 'd' * 64,
                     'app_backend_address': '{{ ansible_host }}',
                     'backend_probe_address': '127.0.0.99'}
        type(self).login = 502, {'error': {'code': 'BAD_GATEWAY'}}
        for scenario in ('success', 'wrong-image', 'internal-failure'):
            with self.subTest(scenario=scenario):
                type(self).requests = []
                type(self).health = (200, {'status': 'wrong' if scenario == 'internal-failure' else 'alive'})
                selected_id = 'sha256:' + '2' * 64 if scenario == 'wrong-image' else image_id
                self.environment['TEST_DOCKER_STATE'] = f'{selected_id} true healthy'
                result = self.run_tasks(tasks, variables, hosts)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode == 0, scenario == 'success', output)
                self.assertNotIn('private-fixture-key', output)
                if scenario == 'wrong-image':
                    self.assertFalse(self.requests)
                    continue
                self.assertIn('TASK [Backend image check]', output)
                self.assertRegex(output, r'TASK \[Backend image check\][^\n]*\nok: \[backend-test\]')
                internal = [r for r in self.requests if r[0] == '/health'
                            and r[2].get('Host') == f'127.0.0.1:{self.http.server_port}']
                self.assertEqual(len(internal), 6 if scenario == 'internal-failure' else 1)
                self.assertNotIn('/api/auth/login', {request[0] for request in self.requests})
                if scenario == 'success':
                    self.assertIn('backend-test -> gateway-test', output)
