import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined
import yaml

driver = Path(__file__).resolve().parents[1] / 'roles/microservices/files/deploy.py'


def load_driver():
    spec = importlib.util.spec_from_file_location('deploy', driver)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def version(service, digest='a', revision='b', migration=1):
    return {
        'image_ref': f'ghcr.io/cringe-driven-development-team/cellestial-{service}@sha256:{digest * 64}',
        'source_sha': 'c' * 40,
        'protocol_revision': revision * 40,
        'migration_version': migration,
    }


class PreflightTests(TestCase):
    def test_partial_selection_preserves_neighbours(self):
        module = load_driver()
        before = {service: version(service) for service in module.SERVICES}
        desired = {**before, 'identity': version('identity', 'd')}
        module.validate(['identity'], before, desired, False)
        with self.assertRaisesRegex(ValueError, 'unselected'):
            module.validate(['identity'], before, {**desired, 'workspace': version('workspace', 'd')}, False)

    def test_refuses_mixed_protocols_and_partial_cutover(self):
        module = load_driver()
        desired = {service: version(service) for service in module.SERVICES}
        with self.assertRaisesRegex(ValueError, 'all'):
            module.validate(['identity'], {}, desired, True)
        before = desired.copy()
        desired['identity'] = version('identity', revision='d')
        with self.assertRaisesRegex(ValueError, 'protocol'):
            module.validate(['identity'], before, desired, False)

    def test_cutover_with_active_schemas_requires_explicit_restore(self):
        module = load_driver()
        desired = {service: version(service) for service in module.SERVICES}
        inputs = {'selected': list(module.SERVICES), 'before': {}, 'desired': desired,
                  'cutover': True, 'action': 'deploy'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'v2-active').touch()
            with self.assertRaisesRegex(ValueError, 'restore'):
                module.Deployment(root, inputs).first_cutover()

    def test_clean_start_refuses_monolith_database_and_redis_index(self):
        module = load_driver()
        for database, redis_db in (('app_db', 1), ('app_db_services', 0)):
            with tempfile.TemporaryDirectory() as temp:
                deployment = self.clean_deployment(module, Path(temp), database, redis_db)
                with patch.object(deployment, 'psql') as sql:
                    with self.assertRaisesRegex(ValueError, 'separate'):
                        deployment.clean_start()
                    sql.assert_not_called()

    def test_clean_start_resumes_same_target_without_recreating_it(self):
        module = load_driver()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'roles.sql').write_text('fixture role provisioning')
            deployment = self.clean_deployment(module, root)
            with (patch.object(module, 'run', return_value=b''),
                  patch.object(deployment, 'require_empty_redis'),
                  patch.object(deployment, 'psql', side_effect=[b'', b'', b'']) as sql):
                deployment.clean_start()
                self.assertTrue((root / 'v2-active').is_file())
                self.assertIn('CREATE DATABASE "app_db_services"', str(sql.call_args_list))
            with (patch.object(module, 'run', return_value=b''),
                  patch.object(deployment, 'require_empty_redis') as redis,
                  patch.object(deployment, 'psql', side_effect=[b'1', b'']) as sql):
                deployment.clean_start()
                self.assertNotIn('CREATE DATABASE', str(sql.call_args_list))
                redis.assert_not_called()
            incompatible = self.clean_deployment(module, root, 'another_database')
            with patch.object(module, 'run', return_value=b''), self.assertRaisesRegex(ValueError, 'differs'):
                incompatible.clean_start()
            with (patch.object(module, 'run', return_value=b''),
                  patch.object(deployment, 'psql', return_value=b'') as sql,
                  self.assertRaisesRegex(ValueError, 'missing')):
                deployment.clean_start()
                sql.assert_called_once()

    def test_clean_start_refuses_an_existing_unrecorded_target(self):
        module = load_driver()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deployment = self.clean_deployment(module, root)
            with (patch.object(module, 'run', return_value=b''),
                  patch.object(deployment, 'psql', return_value=b'1') as sql,
                  self.assertRaisesRegex(ValueError, 'already exists')):
                deployment.clean_start()
                sql.assert_called_once()
            self.assertFalse((root / 'clean-start.json').exists())

    def test_clean_start_refuses_running_monolith_without_database_changes(self):
        module = load_driver()
        with tempfile.TemporaryDirectory() as temp:
            deployment = self.clean_deployment(module, Path(temp))
            with (patch.object(module, 'run', return_value=b'running-api\n'),
                  patch.object(deployment, 'psql') as sql):
                with self.assertRaisesRegex(ValueError, 'monolith must be stopped'):
                    deployment.clean_start()
                sql.assert_not_called()

    @staticmethod
    def clean_deployment(module, root, database='app_db_services', redis_db=1):
        return module.Deployment(root, {
            'selected': list(module.SERVICES), 'before': {},
            'desired': {service: version(service) for service in module.SERVICES},
            'cutover': True, 'clean_start': True, 'action': 'deploy',
            'database': database, 'legacy_database': 'app_db', 'redis_db': redis_db,
        })

    def test_partial_switch_records_failed_readiness_and_keeps_neighbours(self):
        module = load_driver()
        before = {service: version(service) for service in module.SERVICES}
        desired = {**before, 'workspace': version('workspace', 'd')}
        inputs = {'selected': ['workspace'], 'before': before, 'desired': desired,
                  'cutover': False, 'action': 'deploy'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'v2-active').touch()
            deployment = module.Deployment(root, inputs)
            running = dict(before)

            def compose(*argv):
                self.assertEqual(argv, ('up', '-d', '--no-deps', '--force-recreate', 'workspace'))
                running['workspace'] = desired['workspace']

            with (patch.object(deployment, 'pull') as pull, patch.object(deployment, 'migrate') as migrate,
                  patch.object(deployment, 'schema_version', return_value=1),
                  patch.object(deployment, 'compose', side_effect=compose),
                  patch.object(deployment, 'observed', side_effect=lambda: dict(running)),
                  patch.object(module, 'run', side_effect=RuntimeError('probe failed'))):
                deployment.deploy()
                pull.assert_called_once_with('workspace')
                migrate.assert_called_once_with('workspace')
            self.assertEqual(running, desired)
            self.assertEqual(deployment.result['checks'], {
                'workspace': {'image': 'passed', 'ready': 'failed', 'smoke': 'unknown'},
            })

    def test_first_onlineconf_install_keeps_running_services(self):
        module = load_driver()
        self.assertIn('onlineconf', module.SERVICES)
        before = {service: version(service) for service in module.SERVICES if service != 'onlineconf'}
        desired = {**before, 'onlineconf': version('onlineconf')}
        module.validate(['onlineconf'], before, desired, False)
        # Отсутствующий в inventory OnlineConf нельзя оставить невыбранным (infra#220)
        with self.assertRaisesRegex(ValueError, 'inventory'):
            module.validate(['identity'], before, {**desired, 'identity': version('identity', 'd')}, False)
        with self.assertRaisesRegex(ValueError, 'unselected'):
            module.validate(['identity', 'onlineconf'], before,
                            {**desired, 'workspace': version('workspace', 'd')}, False)
        with self.assertRaisesRegex(ValueError, 'inventory'):
            module.validate(['identity', 'onlineconf'], {k: v for k, v in before.items() if k != 'identity'},
                            desired, False)
        inputs = {'selected': ['onlineconf'], 'before': before, 'desired': desired,
                  'cutover': False, 'action': 'deploy'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'v2-active').touch()
            (root / 'roles.sql').write_text('fixture role provisioning')
            deployment = module.Deployment(root, inputs)
            running = dict(before)

            def compose(*argv):
                self.assertEqual(argv, ('up', '-d', '--no-deps', '--force-recreate', 'onlineconf'))
                running['onlineconf'] = desired['onlineconf']

            with (patch.object(deployment, 'pull') as pull, patch.object(deployment, 'migrate') as migrate,
                  patch.object(deployment, 'psql') as sql,
                  patch.object(deployment, 'schema_version', return_value=1) as schema,
                  patch.object(deployment, 'compose', side_effect=compose),
                  patch.object(deployment, 'observed', side_effect=lambda: dict(running)),
                  patch.object(module, 'run', return_value=b'')):
                deployment.deploy()
                pull.assert_called_once_with('onlineconf')
                migrate.assert_called_once_with('onlineconf')
                sql.assert_called_once_with('fixture role provisioning')
                self.assertNotIn('onlineconf', [call.args[0] for call in schema.call_args_list])
            self.assertEqual(running, desired)
            self.assertEqual(deployment.result['checks']['onlineconf'],
                             {'image': 'passed', 'ready': 'passed', 'smoke': 'unknown'})

    def test_compose_uses_private_binds_and_only_environments_gets_runtime_credentials(self):
        command = shlex.split(os.environ.get('COMPOSE_CLI', 'docker compose'))
        if not command or shutil.which(command[0]) is None:
            self.skipTest('Compose CLI is required')
        role = driver.parents[1]
        variables = yaml.safe_load((role / 'defaults/main.yml').read_text())
        variables.update(
            microservices_desired={name: version(name) for name in variables['microservices_order']},
            app_backend_address='192.168.199.10', microservices_metrics_scraper_address='192.168.199.20',
            app_api_proxy_image='nginx:1.28-alpine', microservices_runtime_secrets_dir='/srv/runtime-secrets',
            microservices_runtime_dir='/srv/runtime-playbook',
        )
        jinja = Environment(undefined=StrictUndefined)
        jinja.filters['to_json'] = json.dumps
        template = jinja.from_string((role / 'templates/compose.yml.j2').read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in variables['microservices_order']:
                (root / (name + '.env')).write_text('TEST_CONFIG=test\n')
            (root / 'compose.yml').write_text(template.render(variables))
            completed = subprocess.run([*command, 'config', '--format', 'json'], check=True, cwd=root,
                                       capture_output=True, text=True, timeout=30)
        config = json.loads(completed.stdout)
        services = config['services']
        self.assertEqual(set(services), {'identity', 'workspace', 'environments', 'onlineconf', 'metrics'})
        # У OnlineConf нет gRPC: публикуются только HTTPS на приватном адресе и admin на loopback
        self.assertEqual({(port['host_ip'], port['target']) for port in services['onlineconf']['ports']},
                         {('192.168.199.10', 8080), ('127.0.0.1', 9091)})
        self.assertEqual(config['networks']['existing']['name'], 'cellestial_app')
        for name, expected in {'identity': {'192.168.199.10', '127.0.0.1'},
                               'workspace': {'192.168.199.10', '127.0.0.1'},
                               'environments': {'127.0.0.1'}, 'metrics': {'192.168.199.10'}}.items():
            self.assertEqual({port['host_ip'] for port in services[name]['ports']}, expected)
        for name in ('identity', 'workspace', 'onlineconf'):
            self.assertNotIn('/run/secrets/runtime', {v['target'] for v in services[name]['volumes']})
        self.assertIn('/run/secrets/runtime', {v['target'] for v in services['environments']['volumes']})

    def test_prometheus_inventory_contains_private_paths_and_can_be_disabled(self):
        jinja = Environment(undefined=StrictUndefined)
        jinja.filters['to_json'] = json.dumps
        template = jinja.from_string((driver.parents[1] / 'templates/prometheus-targets.json.j2').read_text())
        values = dict(app_backend_address='192.168.199.10', microservices_order=['identity', 'workspace', 'environments'])
        self.assertEqual(json.loads(template.render(values)), [])
        enabled = json.loads(template.render(values, microservices_metrics_scraper_address='192.168.199.20'))
        self.assertEqual(enabled, [
            {'targets': ['192.168.199.10:9101'], 'labels': {'service': name, '__metrics_path__': f'/{name}/metrics'}}
            for name in values['microservices_order']
        ])


class Readiness(TestCase):
    def test_service_without_grpc_is_ready_by_admin_readyz(self):
        spec = importlib.util.spec_from_file_location('probe', driver.with_name('probe.py'))
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'ports.json').write_text(json.dumps(
                {'onlineconf': {'http': 8446, 'admin': 49091}, 'identity': {'http': 8444, 'grpc': 19090, 'admin': 19091}}))
            for code, expected in ((0, 0), (22, 1)):
                with (self.subTest(code=code), patch('sys.argv', ['probe.py', temp, 'onlineconf', 'ready']),
                      patch.object(probe, 'grpc_ready') as grpc, patch.object(probe.time, 'sleep'),
                      patch.object(probe.time, 'monotonic', side_effect=[0, 1, 100]),
                      patch.object(probe.subprocess, 'run',
                                   return_value=subprocess.CompletedProcess([], code)) as run):
                    self.assertEqual(probe.main(), expected)
                    grpc.assert_not_called()
                    self.assertEqual(run.call_args.args[0][-1], 'http://127.0.0.1:49091/readyz')
            with (patch('sys.argv', ['probe.py', temp, 'identity', 'ready']),
                  patch.object(probe, 'grpc_ready', return_value=True) as grpc):
                self.assertEqual(probe.main(), 0)
                grpc.assert_called_once_with(Path(temp), 'identity', 19090)
