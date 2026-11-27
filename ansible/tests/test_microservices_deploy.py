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
            with (patch.object(deployment, 'check_cutover_evidence'),
                  patch.object(deployment, 'require_empty_redis'),
                  patch.object(deployment, 'psql', side_effect=[b'', b'', b'']) as sql):
                deployment.clean_start()
                self.assertTrue((root / 'v2-active').is_file())
                self.assertIn('CREATE DATABASE "app_db_services"', str(sql.call_args_list))
            with (patch.object(deployment, 'check_cutover_evidence'),
                  patch.object(deployment, 'require_empty_redis') as redis,
                  patch.object(deployment, 'psql', side_effect=[b'1', b'']) as sql):
                deployment.clean_start()
                self.assertNotIn('CREATE DATABASE', str(sql.call_args_list))
                redis.assert_not_called()
            incompatible = self.clean_deployment(module, root, 'another_database')
            with patch.object(incompatible, 'check_cutover_evidence'), self.assertRaisesRegex(ValueError, 'differs'):
                incompatible.clean_start()
            with (patch.object(deployment, 'check_cutover_evidence'),
                  patch.object(deployment, 'psql', return_value=b'') as sql,
                  self.assertRaisesRegex(ValueError, 'missing')):
                deployment.clean_start()
                sql.assert_called_once()

    def test_clean_start_refuses_an_existing_unrecorded_target(self):
        module = load_driver()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            deployment = self.clean_deployment(module, root)
            with (patch.object(deployment, 'check_cutover_evidence'),
                  patch.object(deployment, 'psql', return_value=b'1') as sql,
                  self.assertRaisesRegex(ValueError, 'already exists')):
                deployment.clean_start()
                sql.assert_called_once()
            self.assertFalse((root / 'clean-start.json').exists())

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
        self.assertEqual(set(services), {'identity', 'workspace', 'environments', 'metrics'})
        self.assertEqual(config['networks']['existing']['name'], 'cellestial_app')
        for name, expected in {'identity': {'192.168.199.10', '127.0.0.1'},
                               'workspace': {'192.168.199.10', '127.0.0.1'},
                               'environments': {'127.0.0.1'}, 'metrics': {'192.168.199.10'}}.items():
            self.assertEqual({port['host_ip'] for port in services[name]['ports']}, expected)
        for name in ('identity', 'workspace'):
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
