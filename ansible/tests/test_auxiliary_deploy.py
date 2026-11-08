"""Offline exercises of the production VPS3 release transition tasks."""
import copy
import json
import ssl
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import subprocess
import unittest

import yaml
import test_githubbot as bot
from test_githubbot import render, ANSIBLE
from test_hooks_release import state, release


class AuxiliaryDeployChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = bot.GithubBotChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.fake_docker()
        self.fixture.environment['ANSIBLE_FILTER_PLUGINS'] = str(ANSIBLE / 'filter_plugins')
        self.variables = self.fixture.variables | {'hooks_compose_dir': str(self.root),
            'role_path': str(ANSIBLE / 'roles/hooks')}
        self.old = state()
        self.path = self.root / 'releases.json'
        self.path.write_text(json.dumps(self.old))
        (self.root / 'compose.yml').write_text(yaml.safe_dump(render(self.variables)))
        for name in ['.env', 'githubbot.env', 'Caddyfile']:
            (self.root / name).write_text('fixture')
        (self.root / 'proxy').mkdir()
        (self.root / 'proxy/config.json').write_text('{}')
        self.fixture.environment['TEST_DOCKER_CONTAINER'] = 'target-container'
        self.fixture.environment['TEST_DOCKER_STATE'] = json.dumps([{
            'State': {'Running': True, 'Health': {'Status': 'healthy'}},
            'Config': {'Image': release('githubbot', 'c', 'd')['image_ref']},
            'Image': 'sha256:' + '1' * 64}])

    def deploy(self, service='githubbot'):
        result = self.fixture.play([{'name': 'Deploy target', 'ansible.builtin.import_tasks':
            str(ANSIBLE / 'roles/hooks/tasks/deploy.yml')}],
            self.variables | {'auxiliary_release': release(service, 'c', 'd')})
        for secret in [bot.ENV['TELEGRAM_BOT_TOKEN'], bot.ENV['GITHUB_TOKEN'], bot.ENV['GITHUBBOT_CONFIG_JSON']]:
            self.assertNotIn(secret, result.stdout + result.stderr)
        if self.path.stat().st_uid == 0:
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            subprocess.run(['sudo', '-n', 'chown', str(os.getuid()), str(self.path)], check=True)
        return result

    def https_health(self):
        cert, key = self.root / 'cert.pem', self.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
            '-keyout', str(key), '-out', str(cert)], capture_output=True, check=True)
        class Health(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == '/health' else 404)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = HTTPServer(('localhost', 0), Health)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.variables['hooks_domain'] = 'localhost:' + str(server.server_port)
        self.fixture.environment['SSL_CERT_FILE'] = str(cert)
        return server

    def events(self):
        return [json.loads(line)['args'] for line in (self.root / 'docker-events.jsonl').read_text().splitlines()]

    def test_initialize_existing_project_and_exact_idempotency(self):
        self.https_health()
        self.fixture.environment['TEST_DOCKER_DYNAMIC_INSPECT'] = '1'
        self.path.unlink()
        releases = [entry['desired'] for entry in self.old['services'].values()]
        def initialize(selected=releases):
            result = self.fixture.play([{'name': 'Initialize existing project', 'ansible.builtin.import_tasks':
                str(ANSIBLE / 'roles/hooks/tasks/initialize-releases.yml')}],
                self.variables | {'auxiliary_initial_releases': selected})
            if self.path.exists() and self.path.stat().st_uid == 0:
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
                subprocess.run(['sudo', '-n', 'chown', str(os.getuid()), str(self.path)], check=True)
            return result
        result = initialize()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.path.read_text()), self.old)
        result = initialize()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        changed = copy.deepcopy(self.old)
        changed['services']['githubbot']['confirmed'] = None
        self.path.write_text(json.dumps(changed))
        result = initialize()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.path.read_text()), changed)
        self.path.unlink()
        (self.root / 'compose.yml').unlink()
        result = initialize()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.path.exists())

    def test_target_selection_preserves_neighbor_and_confirms(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['services']['githubbot']['confirmed'], release('githubbot', 'c', 'd'))
        self.assertEqual(saved['services']['webhook-alerts'], self.old['services']['webhook-alerts'])
        up = next(e for e in self.events() if 'up' in e)
        self.assertIn('--no-deps', up)
        self.assertIn('--wait', up)
        self.assertEqual(up[-1], 'githubbot')
        self.assertEqual(yaml.safe_load((self.root / 'compose.yml').read_text())['services']['hooks']['image'],
                         self.old['services']['webhook-alerts']['desired']['image_ref'])

    def test_checker_failure_preserves_state_and_compose(self):
        before = (self.root / 'compose.yml').read_bytes(), self.path.read_bytes()
        self.fixture.environment['TEST_DOCKER_FAILURE'] = 'check'
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, ((self.root / 'compose.yml').read_bytes(), self.path.read_bytes()))
        self.assertTrue(any('--check-config' in e for e in self.events()))
        self.assertFalse(any('up' in e for e in self.events()))

    def test_health_failure_retains_desired_then_retry_confirms(self):
        good = self.fixture.environment['TEST_DOCKER_STATE']
        self.fixture.environment['TEST_DOCKER_STATE'] = good.replace('healthy', 'unhealthy')
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['services']['githubbot']['desired'], release('githubbot', 'c', 'd'))
        self.assertEqual(saved['services']['githubbot']['confirmed'], release('githubbot'))
        self.fixture.environment['TEST_DOCKER_STATE'] = good
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.path.read_text())['services']['githubbot']['confirmed'], release('githubbot', 'c', 'd'))

    def test_missing_dependency_fails_before_mutation(self):
        before = self.path.read_bytes(), (self.root / 'compose.yml').read_bytes()
        (self.root / 'proxy/config.json').unlink()
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, (self.path.read_bytes(), (self.root / 'compose.yml').read_bytes()))
        self.assertFalse((self.root / 'docker-events.jsonl').exists())

    def test_alerts_targets_only_hooks_and_requires_https(self):
        self.https_health()
        self.fixture.environment['TEST_DOCKER_STATE'] = self.fixture.environment['TEST_DOCKER_STATE'].replace(
            '/githubbot@', '/webhook-alerts@')
        result = self.deploy('webhook-alerts')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['services']['githubbot'], self.old['services']['githubbot'])
        self.assertEqual(saved['services']['webhook-alerts']['confirmed'], release('webhook-alerts', 'c', 'd'))
        up = next(e for e in self.events() if 'up' in e)
        self.assertEqual(up[-1], 'hooks')
        self.assertIn('--no-deps', up)
        self.assertFalse(any('--check-config' in e for e in self.events()))

    def test_actual_image_reference_and_id_are_required_before_confirm(self):
        good = self.fixture.environment['TEST_DOCKER_STATE']
        for mismatch in [good.replace('sha256:' + '1' * 64, 'sha256:' + '2' * 64),
                         good.replace('/githubbot@', '/other@')]:
            with self.subTest(mismatch=mismatch):
                self.fixture.environment['TEST_DOCKER_STATE'] = mismatch
                result = self.deploy()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(self.path.read_text())['services']['githubbot']['confirmed'], release('githubbot'))

    def test_failed_network_or_volume_prevents_pull_and_desired_change(self):
        for dependency in ['network', 'volume']:
            with self.subTest(dependency=dependency):
                self.fixture.environment['TEST_DOCKER_FAILURE'] = dependency
                result = self.deploy()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(self.path.read_text()), self.old)
                self.assertFalse(any('pull' in e for e in self.events()))

    def test_neighbor_compose_image_is_preserved_when_desired_differs(self):
        compose = yaml.safe_load((self.root / 'compose.yml').read_text())
        compose['services']['hooks']['image'] = 'neighbor:actually-deployed'
        (self.root / 'compose.yml').write_text(yaml.safe_dump(compose))
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = yaml.safe_load((self.root / 'compose.yml').read_text())
        expected = copy.deepcopy(compose)
        expected['services']['githubbot']['image'] = release('githubbot', 'c', 'd')['image_ref']
        self.assertEqual(after, expected)

    def test_full_apply_validates_candidate_before_compose_and_confirms_both(self):
        self.https_health()
        # Execute the actual apply block, relocating its template lookups into the fixture.
        main = yaml.safe_load((ANSIBLE / 'roles/hooks/tasks/main.yml').read_text())
        block = copy.deepcopy(next(task['block'] for task in main if 'block' in task))
        for task in block:
            if 'ansible.builtin.import_tasks' in task:
                task['ansible.builtin.import_tasks'] = str(ANSIBLE / 'roles/hooks/tasks' / task['ansible.builtin.import_tasks'])
            if 'ansible.builtin.template' in task:
                task['ansible.builtin.template']['src'] = str(ANSIBLE / 'roles/hooks/templates' / task['ansible.builtin.template']['src'])
            if task.get('vars', {}).get('hooks_candidate_content'):
                task['vars']['hooks_candidate_content'] = "{{ lookup('ansible.builtin.template', role_path ~ '/templates/compose.yml.j2') }}"
            for module in ['ansible.builtin.file', 'ansible.builtin.template']:
                if module in task:
                    task[module].pop('owner', None)
                    task[module].pop('group', None)
            task.pop('notify', None)  # fixture has no proxy/Caddy reload handler
        self.variables['hooks_release_state_needs_initialization'] = True
        self.path.unlink()
        self.variables['hooks_release_state'] = copy.deepcopy(self.old)
        for entry in self.variables['hooks_release_state']['services'].values():
            entry['confirmed'] = None
        self.fixture.environment['TEST_DOCKER_DYNAMIC_INSPECT'] = '1'
        verify = {'name': 'Confirm full apply', 'ansible.builtin.import_tasks': str(ANSIBLE / 'roles/hooks/tasks/verify-release.yml'),
                  'vars': {'hooks_verify_services': ['githubbot', 'webhook-alerts']}}
        before = (self.root / 'compose.yml').read_bytes()
        self.fixture.environment['TEST_DOCKER_FAILURE'] = 'check'
        failed = self.fixture.play(block + [verify], self.variables)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.path.exists())
        self.assertEqual((self.root / 'compose.yml').read_bytes(), before)
        self.fixture.environment.pop('TEST_DOCKER_FAILURE')
        result = self.fixture.play(block + [verify], self.variables)
        subprocess.run(['sudo', '-n', 'chown', str(os.getuid()), str(self.path)], check=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(self.path.read_text())
        for name in ['githubbot', 'webhook-alerts']:
            self.assertEqual(saved['services'][name]['confirmed'], saved['services'][name]['desired'])
        events = self.events()
        self.assertLess(next(i for i,e in enumerate(events) if '--check-config' in e),
                        next(i for i,e in enumerate(events) if 'up' in e))
        self.assertFalse(any('bff' in e for e in events))

    def test_native_isolated_target_recovery_preserves_neighbor_and_sqlite(self):
        if os.environ.get('AUXILIARY_NATIVE_TEST') != '1':
            self.skipTest('Set AUXILIARY_NATIVE_TEST=1 for cached-image isolated native Docker acceptance')
        def docker(*args, check=True):
            return subprocess.run(['/usr/bin/docker', *args], capture_output=True, text=True,
                                  check=check, timeout=40)
        image = os.environ.get('AUXILIARY_NATIVE_BOT_IMAGE',
            'ghcr.io/cringe-driven-development-team/githubbot@sha256:3213c1aa70ed85221535eb898d20caa887c158d0fe23f62210af07d5607fceab')
        for cached in [image, 'githubbot:test', 'oven/bun:1.4.4-alpine']:
            docker('image', 'inspect', cached)
        project = 'auxiliary-native-' + self.root.name.removeprefix('cdd-githubbot-test-').replace('_', 'a')
        compose = render(self.variables)
        compose['networks'] = {'hooks': {'internal': True}, 'telegram_proxy': {'internal': True}}
        idle = ['bun', '-e', "process.on('SIGTERM',()=>process.exit(0));setInterval(()=>{},1000)"]
        healthy = {'test': ['CMD', 'bun', '-e', 'process.exit(0)'], 'interval': '200ms',
                   'timeout': '1s', 'retries': 1, 'start_period': '1s', 'start_interval': '200ms'}
        for name in ['hooks', 'telegram-proxy']:
            compose['services'][name]['image'] = 'oven/bun:1.4.4-alpine'
            compose['services'][name]['command'] = idle
            compose['services'][name]['healthcheck'] = healthy
        compose['services']['githubbot']['stop_grace_period'] = '1s'
        compose['services']['githubbot']['init'] = True
        compose['services']['githubbot']['image'] = 'githubbot:test'
        compose['services']['githubbot']['command'] = idle
        compose['services']['githubbot']['healthcheck'] = healthy
        path = self.root / 'compose.yml'
        path.write_text(yaml.safe_dump(compose))
        from jinja2 import Environment
        from ansible.plugins.filter.core import to_json
        template = Environment()
        template.filters['to_json'] = to_json
        (self.root / 'githubbot.env').write_text(template.from_string(
            (ANSIBLE / 'roles/hooks/templates/githubbot.env.j2').read_text()).render(self.variables))
        (self.root / '.env').write_text('FIXTURE_ONLY=true\n')
        prefix = ['compose', '--project-name', project, '--project-directory', str(self.root), '-f', str(path)]
        self.addCleanup(lambda: docker(*prefix, 'down', '--volumes', check=False))
        docker(*prefix, 'up', '--detach', '--no-deps', '--pull', 'never', '--wait',
               '--wait-timeout', '20', 'hooks', 'telegram-proxy', 'githubbot')
        neighbor = docker(*prefix, 'ps', '--all', '--quiet', 'hooks').stdout.strip()
        proxy = docker(*prefix, 'ps', '--all', '--quiet', 'telegram-proxy').stdout.strip()
        before = json.loads(docker('inspect', neighbor, proxy).stdout)
        neighbor_data = "import {Database} from 'bun:sqlite'; const db=new Database('/data/queue.sqlite'); db.exec(\"CREATE TABLE marker(value TEXT); INSERT INTO marker VALUES('preserved');\");"
        docker('exec', '--user', 'root', neighbor, 'bun', '-e', neighbor_data)
        create = "import {Database} from 'bun:sqlite'; import {writeFileSync} from 'node:fs'; writeFileSync('/data/marker','preserved'); const db=new Database('/data/githubbot.sqlite'); db.exec(\"PRAGMA journal_mode=WAL; CREATE TABLE pending(repo TEXT); INSERT INTO pending VALUES('backend');\"); process.kill(process.pid,'SIGKILL');"
        result = docker(*prefix, 'run', '--rm', '--no-deps', '--pull', 'never', '--user', 'root',
                        'githubbot', 'bun', '-e', create, check=False)
        self.assertEqual(result.returncode, 137, result.stdout + result.stderr)
        docker(*prefix, 'run', '--rm', '--no-deps', '--pull', 'never', '--user', 'root',
               'githubbot', 'sh', '-c', 'test -s /data/githubbot.sqlite-wal && chown -R root:root /data && chmod 700 /data')
        # A fixture-only CLI shim redirects project names and accepts cached pulls;
        # every Compose/up/checker/inspect command otherwise reaches real Docker.
        wrapper = self.root / 'bin/docker'
        wrapper.write_text('''#!/usr/bin/python3
import os, subprocess, sys
args=sys.argv[1:]
if '--config' in args:
    i=args.index('--config'); del args[i:i+2]
if args[0]=='pull':
    sys.exit(subprocess.run(['/usr/bin/docker','image','inspect',args[-1]],stdout=subprocess.DEVNULL).returncode)
if 'compose' in args:
    index=args.index('compose')
    args[index+1:index+1]=['--project-name',os.environ['TEST_NATIVE_PROJECT']]
    if '--wait-timeout' in args:
        args[args.index('--wait-timeout')+1]='10'
if args[0] in ['network','volume'] and args[1]=='inspect':
    args[-1]=args[-1].replace('hooks_',os.environ['TEST_NATIVE_PROJECT']+'_',1)
os.execv('/usr/bin/docker',['docker',*args])
''')
        wrapper.chmod(0o700)
        self.fixture.environment['TEST_NATIVE_PROJECT'] = project
        chosen = release('githubbot', 'c', 'd') | {'image_ref': image}
        compose['services']['githubbot']['healthcheck'] = healthy | {'test': ['CMD', 'bun', '-e', 'process.exit(1)']}
        path.write_text(yaml.safe_dump(compose))
        def deploy_native():
            result = self.fixture.play([{'name': 'Native target deploy', 'ansible.builtin.import_tasks':
                str(ANSIBLE / 'roles/hooks/tasks/deploy.yml')}], self.variables | {'auxiliary_release': chosen}, timeout=100)
            if self.path.stat().st_uid == 0:
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
                subprocess.run(['sudo', '-n', 'chown', str(os.getuid()), str(self.path)], check=True)
            return result
        result = deploy_native()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['services']['githubbot']['desired'], chosen)
        self.assertEqual(saved['services']['githubbot']['confirmed'], release('githubbot'))
        current = yaml.safe_load(path.read_text())
        current['services']['githubbot']['healthcheck'] = healthy
        path.write_text(yaml.safe_dump(current))
        result = deploy_native()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['services']['githubbot']['confirmed'], chosen)
        self.assertEqual(saved['services']['webhook-alerts'], self.old['services']['webhook-alerts'])
        after = json.loads(docker('inspect', neighbor, proxy).stdout)
        for old, new in zip(before, after):
            self.assertEqual(old['Id'], new['Id'])
            self.assertEqual(old['Image'], new['Image'])
            self.assertEqual(old['Mounts'], new['Mounts'])
            self.assertEqual(old['State']['StartedAt'], new['State']['StartedAt'])
        docker('exec', '--user', 'root', neighbor, 'bun', '-e', "import {Database} from 'bun:sqlite'; const db=new Database('/data/queue.sqlite'); if(db.query('SELECT value FROM marker').get().value!=='preserved') process.exit(1);")
        read = "import {Database} from 'bun:sqlite'; import {readFileSync} from 'node:fs'; const db=new Database('/data/githubbot.sqlite'); if(db.query('SELECT repo FROM pending').get().repo!=='backend'||readFileSync('/data/marker','utf8')!=='preserved') process.exit(1);"
        docker(*prefix, 'run', '--rm', '--no-deps', '--pull', 'never', 'githubbot', 'bun', '-e', read)


if __name__ == '__main__':
    unittest.main()
