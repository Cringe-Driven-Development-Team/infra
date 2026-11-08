"""Offline release-state contract and loader checks."""
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'filter_plugins'))
from hooks_release import hooks_release_validate as validate, hooks_release_select as select, hooks_release_confirm as confirm


def release(service, digit='a', sha='b'):
    return dict(service=service, image_ref=f'ghcr.io/cringe-driven-development-team/{service}@sha256:' + digit * 64,
                source_sha=sha * 40, publisher_run_id=1, publisher_run_attempt=1)


def state():
    return dict(version=1, services={name: dict(desired=release(name), confirmed=release(name))
                                    for name in ['githubbot', 'webhook-alerts']})


class ReleaseChecks(unittest.TestCase):
    def test_select_preserves_neighbor_and_confirmed_without_mutation(self):
        original = state()
        before = copy.deepcopy(original)
        bot2 = release('githubbot', 'c', 'd')
        selected = select(original, bot2)
        self.assertEqual(selected['services']['webhook-alerts'], original['services']['webhook-alerts'])
        self.assertEqual(selected['services']['githubbot']['confirmed'], release('githubbot'))
        self.assertEqual(confirm(selected, 'githubbot')['services']['githubbot']['confirmed'], bot2)
        self.assertEqual(original, before)
        self.assertEqual(selected['services']['githubbot']['confirmed'], release('githubbot'))

    def test_reject_invalid_state(self):
        candidates = []
        for path, value in [(('version',), 2), (('version',), True),
                            (('services', 'githubbot', 'desired', 'image_ref'), 'ghcr.io/other/githubbot@sha256:' + 'a'*64),
                            (('services', 'githubbot', 'desired', 'source_sha'), 'A'*40),
                            (('services', 'githubbot', 'desired', 'image_ref'), 'ghcr.io/cringe-driven-development-team/githubbot@sha256:' + 'a'*63),
                            (('services', 'githubbot', 'desired', 'service'), 'webhook-alerts'),
                            (('services', 'githubbot', 'desired', 'publisher_run_id'), True),
                            (('services', 'githubbot', 'desired', 'publisher_run_attempt'), 0),
                            (('services', 'githubbot', 'desired', 'image_ref'), 'githubbot:latest')]:
            candidate = state()
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            candidates.append(candidate)
        missing = state()
        del missing['services']['webhook-alerts']
        candidates.append(missing)
        conflict = state()
        conflict['services']['githubbot']['confirmed'] = release('githubbot', 'c')
        candidates.append(conflict)
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                validate(candidate)

    def test_reject_malformed_service(self):
        with self.assertRaises(ValueError):
            select(state(), release('githubbot') | {'service': []})

    def test_reject_same_source_with_different_digest(self):
        with self.assertRaises(ValueError):
            select(state(), release('githubbot', 'c'))

    def test_bootstrap_has_null_confirmations(self):
        candidate = state()
        for service in candidate['services'].values():
            service['confirmed'] = None
        self.assertEqual(validate(candidate), candidate)


class LoaderChecks(unittest.TestCase):
    def setUp(self):
        import test_hooks
        self.fixture = test_hooks.HooksChecks()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.environment['ANSIBLE_FILTER_PLUGINS'] = str(Path(__file__).resolve().parents[1] / 'filter_plugins')
        self.root = self.fixture.root
        self.variables = self.fixture.variables | {'hooks_compose_dir': str(self.root)}
        self.tasks = self.fixture.import_task('roles/hooks/tasks/release-state.yml')

    def test_apply_reads_desired_even_with_old_confirmed_and_inventory(self):
        import json
        selected = select(state(), release('githubbot', 'c', 'd'))
        path = self.root / 'releases.json'
        path.write_text(json.dumps(selected))
        checks = [{'name': 'Assert both desired releases survive apply', 'ansible.builtin.assert': {'that': [
            "hooks_githubbot_image == '" + release('githubbot', 'c', 'd')['image_ref'] + "'",
            "hooks_image == '" + release('webhook-alerts')['image_ref'] + "'",
            "hooks_release_state.services.githubbot.confirmed.source_sha == '" + 'b'*40 + "'",
            "hooks_githubbot_image_source_tag == 'ghcr.io/cringe-driven-development-team/githubbot:sha-" + 'd'*40 + "'",
            'not hooks_release_state_needs_initialization']}}]
        before = path.read_bytes()
        for check in [False, True, False]:
            with self.subTest(check=check):
                result = self.fixture.play(self.tasks + checks, self.variables, check=check)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(path.read_bytes(), before)

    def test_bootstrap_is_explicit_validated_and_never_written(self):
        checks = [{'name': 'Assert bootstrap awaits persistence', 'ansible.builtin.assert': {'that': [
            'hooks_release_state_needs_initialization', 'hooks_release_state.services.githubbot.confirmed == none']}}]
        variables = self.variables | {'hooks_initial_releases': [release('githubbot'), release('webhook-alerts')]}
        for check in [False, True]:
            result = self.fixture.play(self.tasks + checks, variables, check=check)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((self.root / 'releases.json').exists())
        for initial in [None, [], [release('githubbot')]*2]:
            result = self.fixture.play(self.tasks, self.variables | {'hooks_initial_releases': initial})
            self.assertNotEqual(result.returncode, 0)
        (self.root / 'compose.yml').write_text('services: {}')
        result = self.fixture.play(self.tasks, variables)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('initialize', result.stdout)

    def test_missing_and_malformed_state_stop_apply(self):
        result = self.fixture.play(self.tasks, self.variables)
        self.assertNotEqual(result.returncode, 0)
        (self.root / 'releases.json').write_text('{"version": 2}')
        result = self.fixture.play(self.tasks, self.variables)
        self.assertNotEqual(result.returncode, 0)


    def test_migration_preview_requires_matching_compose_images_and_never_writes(self):
        import yaml
        initial = [release('githubbot'), release('webhook-alerts')]
        variables = self.variables | {'hooks_initial_releases': initial}
        path = self.root / 'compose.yml'
        env = self.root / '.env'
        env.write_text('UNCHANGED=fixture-value\n')
        for mismatch in [None, 'githubbot', 'hooks']:
            with self.subTest(mismatch=mismatch):
                services = {'githubbot': {'image': initial[0]['image_ref']},
                            'hooks': {'image': initial[1]['image_ref']}}
                if mismatch:
                    services[mismatch]['image'] = services[mismatch]['image'].replace('a'*64, 'c'*64)
                path.write_text(yaml.safe_dump({'name': 'migration-preview', 'services': services}))
                before = {file: file.read_bytes() for file in [path, env]}
                result = self.fixture.play(self.tasks, variables, check=True)
                self.assertEqual(result.returncode == 0, mismatch is None, result.stdout + result.stderr)
                for file, contents in before.items():
                    self.assertEqual(file.read_bytes(), contents)
                self.assertFalse((self.root / 'releases.json').exists())
        # Matching inputs still cannot initialize an existing installation by regular apply.
        path.write_text(yaml.safe_dump({'services': {'githubbot': {'image': initial[0]['image_ref']},
                                                    'hooks': {'image': initial[1]['image_ref']}}}))
        before = path.read_bytes()
        result = self.fixture.play(self.tasks, variables)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / 'releases.json').exists())
