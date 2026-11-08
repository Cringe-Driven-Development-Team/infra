"""Pure release-state validation and transitions; Ansible owns persistence."""
from copy import deepcopy
import re

SERVICES = {'githubbot', 'webhook-alerts'}
RELEASE_FIELDS = {'service', 'image_ref', 'source_sha', 'publisher_run_id', 'publisher_run_attempt'}


def _release(release, service):
    if not isinstance(release, dict) or set(release) != RELEASE_FIELDS:
        raise ValueError('Release must contain exactly the five release fields')
    if not isinstance(service, str) or release['service'] != service or service not in SERVICES:
        raise ValueError('Release service does not match state service')
    repository = f'ghcr.io/cringe-driven-development-team/{service}'
    if not isinstance(release['image_ref'], str) or not re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', release['image_ref']):
        raise ValueError('Release image_ref must use the service repository and registry digest')
    if not isinstance(release['source_sha'], str) or not re.fullmatch('[0-9a-f]{40}', release['source_sha']):
        raise ValueError('Release source_sha must be a full lowercase SHA')
    for key in ['publisher_run_id', 'publisher_run_attempt']:
        if not isinstance(release[key], int) or isinstance(release[key], bool) or release[key] <= 0:
            raise ValueError(f'Release {key} must be a positive integer')


def _same_source(left, right):
    if left['source_sha'] == right['source_sha'] and left['image_ref'] != right['image_ref']:
        raise ValueError('The same source SHA cannot select different digests')


def hooks_release_validate(state):
    if not isinstance(state, dict) or set(state) != {'version', 'services'}:
        raise ValueError('Unsupported release-state schema')
    if not isinstance(state['version'], int) or isinstance(state['version'], bool) or state['version'] != 1:
        raise ValueError('Unsupported release-state schema')
    if not isinstance(state['services'], dict) or set(state['services']) != SERVICES:
        raise ValueError('Release state must contain both services')
    for name, entry in state['services'].items():
        if not isinstance(entry, dict) or set(entry) != {'desired', 'confirmed'}:
            raise ValueError('Service state must contain desired and confirmed')
        _release(entry['desired'], name)
        if entry['confirmed'] is not None:
            _release(entry['confirmed'], name)
            _same_source(entry['desired'], entry['confirmed'])
    return deepcopy(state)


def hooks_release_select(state, release):
    selected = hooks_release_validate(state)
    if not isinstance(release, dict):
        raise ValueError('Release must be a mapping')
    service = release.get('service')
    _release(release, service)
    entry = selected['services'][service]
    for previous in [entry['desired'], entry['confirmed']]:
        if previous is not None:
            _same_source(previous, release)
    entry['desired'] = deepcopy(release)
    return selected


def hooks_release_confirm(state, service):
    confirmed = hooks_release_validate(state)
    if not isinstance(service, str) or service not in SERVICES:
        raise ValueError('Unknown release service')
    confirmed['services'][service]['confirmed'] = deepcopy(confirmed['services'][service]['desired'])
    return confirmed


class FilterModule:
    def filters(self):
        return {name: globals()[name] for name in ['hooks_release_validate', 'hooks_release_select', 'hooks_release_confirm']}
