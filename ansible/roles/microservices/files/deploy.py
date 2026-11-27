"""Управление контейнерами VPS2 по проверенному составу релиза."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SERVICES = ('identity', 'environments', 'workspace')
REGISTRY = 'ghcr.io/cringe-driven-development-team/cellestial-'


def validate(selected, before, desired, cutover):
    """Проверяет состав операции и совместимость протоколов."""
    all_services = set(SERVICES)
    if not selected or len(selected) != len(set(selected)) or set(selected) - all_services:
        raise ValueError('invalid service selection')
    if set(desired) != all_services or set(before) - all_services:
        raise ValueError('invalid deployment inventory')
    if cutover and (set(selected) != all_services or before):
        raise ValueError('first cutover must select all services with an empty inventory')
    if not cutover and set(before) != all_services:
        raise ValueError('service deployment requires the current inventory')
    for inventory in (before, desired):
        for service, version in inventory.items():
            image_pattern = re.escape(f'{REGISTRY}{service}@sha256:') + '[a-f0-9]{64}'
            if (not re.fullmatch(image_pattern, version.get('image_ref', ''))
                    or not re.fullmatch('[a-f0-9]{40}', version.get('source_sha', ''))
                    or not re.fullmatch('[a-f0-9]{40}', version.get('protocol_revision', ''))
                    or type(version.get('migration_version')) is not int or version['migration_version'] < 1):
                raise ValueError(f'invalid version for {service}')
    if any(desired[service] != before.get(service) for service in all_services - set(selected)):
        raise ValueError('unselected services must retain their version')
    if len({entry['protocol_revision'] for entry in desired.values()}) != 1:
        raise ValueError('mixed versions must declare the same protocol revision')


def run(*argv, input_data=None, capture=True, timeout=180):
    """Запускает команду без раскрытия аргументов и stderr в отчёте."""
    result = subprocess.run(argv, input=input_data, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} {argv[1]} returned {result.returncode}')
    return result.stdout


class Deployment:
    """Исполняет одну операцию и записывает факты наблюдения даже после ошибки."""

    def __init__(self, root, inputs):
        self.root = root
        self.inputs = inputs
        self.selected = inputs['selected']
        self.before = inputs['before']
        self.desired = inputs['desired']
        self.cutover = inputs['cutover']
        self.workers_paused = inputs.get('workers_paused', False)
        self.result_path = root / 'result.json'
        self.result = {'observed': {}, 'checks': {
            service: {'image': 'unknown', 'ready': 'unknown', 'smoke': 'unknown'}
            for service in self.selected
        }}

    def save_result(self):
        tmp = self.result_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.result) + '\n')
        tmp.replace(self.result_path)

    def compose(self, *args):
        return run('docker', 'compose', '--project-name', 'cellestial-services',
                   '--project-directory', str(self.root), '--file', str(self.root / 'compose.yml'), *args)

    def database_container(self):
        return run('docker', 'compose', '--project-name', 'cellestial',
                   '--project-directory', str(self.root.parent), 'ps', '-q', 'postgres').decode().strip()

    def version_matches(self, service, version, container):
        if not container:
            return False
        info, = json.loads(run('docker', 'inspect', container))
        labels = info['Config']['Labels'] or {}
        return (info['State']['Running'] and info['Config']['Image'] == version['image_ref']
                and labels.get('org.opencontainers.image.revision') == version['source_sha']
                and labels.get('org.opencontainers.image.source') == 'https://github.com/Cringe-Driven-Development-Team/backend'
                and (not version.get('image_id') or info['Image'] == version['image_id']))

    def observed(self):
        result = {}
        for service in SERVICES:
            try:
                container = self.compose('ps', '-q', service).decode().strip()
                for version in (self.desired[service], self.before.get(service)):
                    if version and self.version_matches(service, version, container):
                        result[service] = version
                        break
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                continue
        return result

    def schema_version(self, service):
        sql = f"SELECT COALESCE(max(version_id),0) FROM {service}.goose_db_version WHERE is_applied;"
        return int(self.psql(sql).decode().strip())

    def psql(self, sql, database=None):
        target = database if database is not None else self.inputs.get('database', '')
        return run('docker', 'exec', '-i', self.database_container(), 'sh', '-ec',
                   'exec psql -XAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${1:-$POSTGRES_DB}"',
                   'psql', target, input_data=sql.encode())

    def check_cutover_evidence(self):
        inventory_path, backup, checksum = (self.root / name for name in
                                            ('cutover.json', 'monolith.dump', 'monolith.dump.sha256'))
        inventory = json.loads(inventory_path.read_text())
        postgres = self.database_container()
        for flag in ('admission_stopped', 'queue_drained', 'processes_stopped', 'automation_stopped',
                     'cloud_reconciled', 'backup_verified'):
            if inventory.get(flag) is not True:
                raise ValueError(f'cutover evidence is missing: {flag}')
        for service in SERVICES:
            if inventory.get('roles', {}).get(service) != {
                'owner': f'{service}_owner', 'app': f'{service}_app'
            }:
                raise ValueError(f'cutover role mismatch for {service}')
        with backup.open('rb') as data:
            actual_sha = hashlib.file_digest(data, 'sha256').hexdigest()
        if checksum.read_text().strip() != actual_sha:
            raise ValueError('production PostgreSQL backup checksum mismatch')
        with backup.open('rb') as data:
            subprocess.run(['docker', 'exec', '-i', postgres, 'pg_restore', '--list'],
                           stdin=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True, timeout=60)
        monolith = run('docker', 'ps', '--quiet', '--filter', 'label=com.docker.compose.project=cellestial',
                       '--filter', 'label=com.docker.compose.service=api').strip()
        if monolith:
            raise ValueError('monolith must be stopped before first cutover')
        if self.inputs.get('clean_start', False):
            with (self.root / 'redis.rdb').open('rb') as data:
                redis_sha = hashlib.file_digest(data, 'sha256').hexdigest()
            if (self.root / 'redis.rdb.sha256').read_text().strip() != redis_sha:
                raise ValueError('production Redis backup checksum mismatch')
        return inventory_path

    def first_cutover(self):
        if (self.root / 'v2-active').exists() or (self.root / 'clean-start.json').exists():
            raise ValueError('service schemas are active; restore the monolith before a new cutover')
        inventory_path = self.check_cutover_evidence()
        self.psql((self.root / 'roles.sql').read_text())
        (self.root / 'v2-active').touch(exist_ok=True)
        for mode in ('--dry-run', '--apply'):
            run('docker', 'run', '--rm', '--user', '0', '--network', 'cellestial_app', '--env-file',
                str(self.root / 'transfer.env'), '--mount',
                f'type=bind,src={inventory_path},dst=/cutover.json,readonly',
                self.desired['identity']['image_ref'], './migrate', 'transfer-monolith',
                mode, '--inventory-file', '/cutover.json')
        for service in SERVICES:
            if self.schema_version(service) > self.desired[service]['migration_version']:
                raise ValueError(f'{service}: database schema is newer than the requested image')

    def require_empty_redis(self, index):
        keys = run('docker', 'compose', '--project-name', 'cellestial',
                   '--project-directory', str(self.root.parent),
                   'exec', '-T', 'redis', 'redis-cli', '-e', '-n', str(index), 'DBSIZE')
        if keys.strip() != b'0':
            raise ValueError('clean start requires an empty Redis database')

    def clean_start(self):
        database = self.inputs['database']
        redis_db = self.inputs['redis_db']
        if (not re.fullmatch('[a-z_][a-z0-9_]{0,62}', database)
                or database in (self.inputs['legacy_database'], 'postgres', 'template0', 'template1')
                or type(redis_db) is not int or redis_db <= 0):
            raise ValueError('clean start requires separate PostgreSQL and Redis databases')

        self.check_cutover_evidence()
        marker = self.root / 'clean-start.json'
        storage = {'database': database, 'redis_db': redis_db, 'desired': self.desired}
        retry = marker.is_file()
        if retry and json.loads(marker.read_text()) != storage:
            raise ValueError('clean start differs from the recorded first attempt')
        if not retry and (self.root / 'v2-active').exists():
            raise ValueError('service schemas are already active')
        exists = self.psql(f"SELECT 1 FROM pg_database WHERE datname = '{database}';", 'postgres').strip()
        if not exists and (self.root / 'v2-active').exists():
            raise ValueError('recorded clean start database is missing')
        if not retry:
            if exists:
                raise ValueError('clean start target database already exists')
            self.require_empty_redis(redis_db)
            pending = marker.with_suffix('.tmp')
            pending.write_text(json.dumps(storage) + '\n')
            pending.replace(marker)
        if not exists:
            self.psql(f'CREATE DATABASE "{database}" TEMPLATE template0;', 'postgres')
        self.psql((self.root / 'roles.sql').read_text())
        (self.root / 'v2-active').touch()

    def pull(self, service):
        version = self.desired[service]
        ref = version['image_ref']
        run('docker', 'pull', '--platform=linux/amd64', ref, timeout=300)
        image, = json.loads(run('docker', 'image', 'inspect', ref))
        labels = image['Config']['Labels'] or {}
        if (labels.get('org.opencontainers.image.revision') != version['source_sha']
                or labels.get('org.opencontainers.image.source') != 'https://github.com/Cringe-Driven-Development-Team/backend'
                or (version.get('image_id') and image['Id'] != version['image_id'])):
            raise ValueError(f'{service}: published image identity mismatch')

    def migrate(self, service):
        run('docker', 'run', '--rm', '--network', 'cellestial_app', '--env-file',
            str(self.root / f'{service}-owner.env'), self.desired[service]['image_ref'],
            './migrate', '--service', service, 'up')
        if self.schema_version(service) != self.desired[service]['migration_version']:
            raise ValueError(f'{service}: migrated database schema differs from manifest')
        self.psql((self.root / f'{service}-grants.sql').read_text())

    def deploy(self):
        validate(self.selected, self.before, self.desired, self.cutover)
        if self.inputs['action'] not in ('deploy', 'rollback') or self.cutover and self.inputs['action'] != 'deploy':
            raise ValueError('invalid deployment action')
        marker = self.root / 'clean-start.json'
        if not self.cutover and marker.is_file():
            storage = json.loads(marker.read_text())
            if any(self.inputs.get(key) != storage[key] for key in ('database', 'redis_db')):
                raise ValueError('deployment storage differs from the recorded clean start')
        for service in self.selected:
            self.pull(service)
        if self.cutover:
            if not self.workers_paused:
                raise ValueError('initial cutover requires paused workers')
            if self.inputs.get('clean_start', False):
                self.clean_start()
            else:
                self.first_cutover()
        else:
            observed = self.observed()
            if not (self.root / 'v2-active').is_file() or any(
                observed.get(service) != self.before[service] for service in SERVICES if service not in self.selected
            ):
                raise ValueError('observed unselected services differ from the operation baseline')
            if any(service not in observed for service in self.selected):
                raise ValueError('selected service image must match the baseline or desired version')
            for service in SERVICES:
                current_version = self.schema_version(service)
                if current_version not in (self.before[service]['migration_version'], self.desired[service]['migration_version']):
                    raise ValueError(f'{service}: observed schema differs from baseline')
                if current_version > self.desired[service]['migration_version']:
                    raise ValueError(f'{service}: rollback requires a schema-specific restore')
        for service in SERVICES:
            if service in self.selected:
                self.migrate(service)
        for service in SERVICES:
            if service in self.selected:
                self.compose('up', '-d', '--no-deps', '--force-recreate', service)
        if self.inputs.get('metrics'):
            self.compose('up', '-d', '--no-deps', '--force-recreate', 'metrics')
        for service in self.selected:
            checks = self.result['checks'][service]
            checks['image'] = 'passed' if self.observed().get(service) == self.desired[service] else 'failed'
            try:
                run('python3', str(self.root / 'probe.py'), str(self.root), service, 'ready')
                checks['ready'] = 'passed'
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                checks['ready'] = 'failed'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', type=Path)
    args = parser.parse_args()
    root = args.operation.parent
    os.umask(0o077)
    with (root / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        deployment = Deployment(root, json.loads(args.operation.read_text()))
        try:
            deployment.deploy()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as err:
            print(f'microservices deploy stopped: {err}', file=sys.stderr)
            return 1
        finally:
            deployment.result['observed'] = deployment.observed()
            deployment.save_result()
        return int(any(status != 'passed' for checks in deployment.result['checks'].values()
                       for status in (checks['image'], checks['ready'])))


if __name__ == '__main__':
    sys.exit(main())
