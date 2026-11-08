"""Exercise production Ansible tasks with local HTTP/HTTPS; never contact production."""
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
VALID_LOGIN = {'error': {'data': {'code': 'UNAUTHORIZED', 'appCode': 'invalid_credentials'}}}


class BackendProbes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='cdd-http-tests-')
        cls.root = Path(cls.directory.name)
        cls.cert, key = cls.root / 'cert.pem', cls.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                        '-out', str(cls.cert), '-days', '1', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True)
        cls.requests = []
        cls.health = (200, {'status': 'alive'})
        cls.login = (401, VALID_LOGIN)
        cls.hidden = (404, 'not found')
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
                if self.path == '/api/trpc/auth.login':
                    status, value = cls.login
                elif self.path == '/health' and self.server is cls.http:
                    status, value = cls.sequence.pop(0) if cls.sequence else cls.health
                elif self.path == '/login':
                    status, value = 200, ('<html><meta name="release" content="' + RELEASE + '">'
                        '<script id="bootstrap" type="application/json">{"user":null}</script></html>')
                    headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
                               'Content-Security-Policy-Report-Only': "default-src 'none'"}
                elif self.path == '/api/trpc/users.me':
                    status, value = 401, {'error': {'data': {'code': 'UNAUTHORIZED'}}}
                else:
                    status, value = cls.hidden
                data = json.dumps(value).encode() if isinstance(value, dict) else str(value).encode()
                self.send_response(status)
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

        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
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
        type(self).login = (401, VALID_LOGIN)
        type(self).hidden = (404, 'not found')
        self.environment = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(self.root / 'tmp'),
                                ANSIBLE_REMOTE_TEMP=str(self.root / 'remote-tmp'))
        for name in ('ANSIBLE_VAULT_PASSWORD_FILE', 'ANSIBLE_VAULT_PASSWORD'):
            self.environment.pop(name, None)

    def tasks(self, name):
        path = ANSIBLE / 'roles/app/tasks' / name
        self.assertTrue(path.is_file(), 'Missing production probe: ' + name)
        tasks = copy.deepcopy(yaml.safe_load(path.read_text()))
        for task in tasks:
            # Only shorten delays and remap the private HTTP port to an ephemeral local fixture.
            if 'delay' in task:
                task['delay'] = 0
            uri = task.get('ansible.builtin.uri', {})
            uri['url'] = uri.get('url', '').replace(':8080/health', f':{self.http.server_port}/health')
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
                     'app_domain': f'localhost:{self.https.server_port}', 'bff_probe_release': RELEASE,
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
        tasks = self.tasks('probe-bff.yml')
        cases = [(401, VALID_LOGIN, True), (401, {'error': {'data': {'code': 'UNAUTHORIZED'}}}, False),
                 (401, {'error': {'data': {'code': 'FORBIDDEN', 'appCode': 'invalid_credentials'}}}, False),
                 (401, {'error': {'data': {'code': 'UNAUTHORIZED', 'appCode': 'invalid_bff_key'}}}, False),
                 (403, {'error': {'data': {'code': 'FORBIDDEN', 'appCode': 'csrf'}}}, False),
                 (401, '<html>denied</html>', False), (401, '{bad-json', False),
                 (500, {}, False), (200, VALID_LOGIN, False), (302, 'redirect', False)]
        previous_login = None
        for status, body, passes in cases:
            with self.subTest(status=status, body=body):
                type(self).requests = []
                type(self).login = status, body
                result = self.run_tasks(tasks)
                self.assertEqual(result.returncode == 0, passes, result.stdout + result.stderr)
                calls = [r for r in self.requests if r[0] == '/api/trpc/auth.login']
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

    def test_socket_timeouts_fail_with_at_most_six_requests(self):
        for filename, path in [('probe-backend.yml', '/health'), ('probe-bff.yml', '/api/trpc/auth.login')]:
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
            tasks[0]['ansible.builtin.uri']['url'] = f'http://127.0.0.1:{unused.getsockname()[1]}/health'
            result = self.run_tasks(tasks)
        self.assertNotEqual(result.returncode, 0)

    def test_failed_internal_probe_does_not_run_external(self):
        type(self).health = 503, {}
        result = self.run_tasks(self.tasks('probe-backend.yml') + self.tasks('probe-bff.yml'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(r[0] == '/api/trpc/auth.login' for r in self.requests))

    def test_failed_external_probe_preserves_previous_success_in_log(self):
        type(self).login = 500, {}
        result = self.run_tasks(self.tasks('probe-backend.yml') + self.tasks('probe-bff.yml'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Backend internal HTTP check', result.stdout)
        self.assertIn('ok: [backend-test -> gateway-test', result.stdout)
        self.assertTrue(any(r[0] == '/api/trpc/auth.login' for r in self.requests))

    def test_real_backend_post_tasks_read_gateway_release_and_keep_stage_results(self):
        # Keep the actual production post_tasks and imported task arguments. Only map external
        # dependencies to local TLS/HTTP fixtures and the existing Docker CLI substitute.
        production = yaml.safe_load((ANSIBLE / 'deploy-backend.yml').read_text())[0]
        tasks = copy.deepcopy(production['post_tasks'])
        for task in tasks:
            imported = task.pop('ansible.builtin.import_tasks', None)
            if imported:
                task['block'] = self.tasks(Path(imported).name)
        gateway = self.root / 'gateway'
        backend = self.root / 'backend'
        for directory in (gateway, backend):
            directory.mkdir(exist_ok=True)
        gateway_env = gateway / '.env'
        gateway_env.write_text(f'BFF_RELEASE_SHA={RELEASE}\nBFF_API_KEY=private-fixture-key\n')
        (backend / '.env').write_text('BFF_RELEASE_SHA=' + 'c' * 40 + '\n')
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
                     # Force the real set_fact and slurp to supply these values.
                     'bff_probe_release': None}
        # Extra variables here are play vars, so set_fact must replace the stale address.
        variables['backend_probe_address'] = '127.0.0.99'
        for scenario in ('success', 'wrong-image', 'internal-failure', 'external-failure', 'missing-release'):
            with self.subTest(scenario=scenario):
                type(self).requests = []
                type(self).health = (200, {'status': 'wrong' if scenario == 'internal-failure' else 'alive'})
                type(self).login = (500, {}) if scenario == 'external-failure' else (401, VALID_LOGIN)
                selected_id = 'sha256:' + '2' * 64 if scenario == 'wrong-image' else image_id
                self.environment['TEST_DOCKER_STATE'] = f'{selected_id} true healthy'
                gateway_env.write_text(('' if scenario == 'missing-release' else f'BFF_RELEASE_SHA={RELEASE}\n')
                                       + 'BFF_API_KEY=private-fixture-key\n')
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
                if scenario == 'internal-failure':
                    self.assertFalse(any(r[0] == '/api/trpc/auth.login' for r in self.requests))
                else:
                    self.assertRegex(output, r'TASK \[Backend internal HTTP check\][^\n]*\nok: \[backend-test -> gateway-test')
                    if scenario == 'missing-release':
                        self.assertFalse(any(r[0] == '/api/trpc/auth.login' for r in self.requests))
                    else:
                        self.assertTrue(any(r[0] == '/api/trpc/auth.login' for r in self.requests))
