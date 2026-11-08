#!/usr/bin/env python3
"""Fail-closed publisher provenance and release ordering; JSON is never shell code."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import urllib.request

SOURCE = 'Cringe-Driven-Development-Team/.github'
WORKFLOWS = {'githubbot': 'githubbot-image.yml', 'webhook-alerts': 'webhook-alerts-image.yml'}
spec = importlib.util.spec_from_file_location('hooks_release', Path(__file__).resolve().parents[1] / 'ansible/filter_plugins/hooks_release.py')
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


def validate_publication(release, api, registry):
    state._release(release, release.get('service'))
    run = api.get(f"/repos/{SOURCE}/actions/runs/{release['publisher_run_id']}/attempts/{release['publisher_run_attempt']}")
    expected = {'id': release['publisher_run_id'], 'run_attempt': release['publisher_run_attempt'],
                'event': 'push', 'head_branch': 'main', 'head_sha': release['source_sha'],
                'status': 'completed', 'conclusion': 'success',
                'path': '.github/workflows/' + WORKFLOWS[release['service']]}
    if any(run.get(key) != value for key, value in expected.items()) or any(
            run.get(key, {}).get('full_name') != SOURCE for key in ['repository', 'head_repository']):
        raise ValueError('Publisher attempt is not a trusted successful main push')
    if api.get(f"/repos/{SOURCE}/compare/{release['source_sha']}...main")['status'] not in ['ahead', 'identical']:
        raise ValueError('Publisher source is not reachable from main')
    if registry is not None:
        publication = registry.inspect(release)
        if publication != {'digest': release['image_ref'].split('@')[1], 'source': 'https://github.com/' + SOURCE}:
            raise ValueError('Tag digest or OCI source mismatch')


def validate_request(request, api):
    fields = {'operation', 'service', 'image_ref', 'source_sha', 'request_id', 'initial_releases_json'}
    if not isinstance(request, dict) or set(request) != fields or any(not isinstance(v,str) for v in request.values()):
        raise ValueError('Invalid request fields')
    if request['operation'] == 'initialize':
        if any(request[key] for key in ['service', 'image_ref', 'source_sha', 'request_id']):
            raise ValueError('Mixed initialize and deploy fields')
        releases = json.loads(request['initial_releases_json'])
        if not isinstance(releases,list) or len(releases) != 2 or {r.get('service') for r in releases} != set(WORKFLOWS):
            raise ValueError('Initialize requires exactly both explicit releases')
        for release in releases: validate_publication(release,api,None)
        return releases
    if request['operation'] != 'deploy' or request['initial_releases_json']:
        raise ValueError('Invalid operation or mixed fields')
    match = re.fullmatch(r'(githubbot|webhook-alerts):([1-9][0-9]*):([1-9][0-9]*):([1-9][0-9]*):([1-9][0-9]*)',request['request_id'])
    if not match or match[1] != request['service']:
        raise ValueError('Invalid request_id')
    release = {key: request[key] for key in ['service','image_ref','source_sha']}
    release.update(publisher_run_id=int(match[2]),publisher_run_attempt=int(match[3]))
    validate_publication(release,api,None)
    return release


def check_order(current, candidate, api):
    for release in [current,candidate]: state._release(release,release.get('service'))
    if current['service'] != candidate['service']:
        raise ValueError('Cannot compare different services')
    if current['source_sha'] == candidate['source_sha']:
        if current['image_ref'] != candidate['image_ref']:
            raise ValueError('Same source cannot change digest')
        return
    if api.get(f"/repos/{SOURCE}/compare/{current['source_sha']}...{candidate['source_sha']}")['status'] != 'ahead':
        raise ValueError('Candidate must descend from selected release')


class GitHub:
    def get(self,path):
        request=urllib.request.Request('https://api.github.com'+path,headers={
            'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10',
            'Authorization':'Bearer '+os.environ['GH_TOKEN']})
        with urllib.request.urlopen(request,timeout=30) as response: return json.load(response)


class Registry:
    def inspect(self,release):
        repository='cringe-driven-development-team/'+release['service']
        with urllib.request.urlopen('https://ghcr.io/token?service=ghcr.io&scope=repository:'+repository+':pull',timeout=30) as response:
            token=json.load(response)['token']
        headers={'Authorization':'Bearer '+token, 'Accept': ', '.join([
            'application/vnd.oci.image.index.v1+json','application/vnd.oci.image.manifest.v1+json',
            'application/vnd.docker.distribution.manifest.list.v2+json','application/vnd.docker.distribution.manifest.v2+json'])}
        def fetch(kind,ref):
            request=urllib.request.Request('https://ghcr.io/v2/'+repository+'/'+kind+'/'+ref,headers=headers)
            with urllib.request.urlopen(request,timeout=30) as response: raw=response.read()
            if kind == 'blobs' or ref.startswith('sha256:'):
                if 'sha256:'+hashlib.sha256(raw).hexdigest() != ref: raise ValueError('Registry content digest mismatch')
            return raw,json.loads(raw)
        raw,manifest=fetch('manifests','sha-'+release['source_sha'])
        digest='sha256:'+hashlib.sha256(raw).hexdigest()
        if digest != release['image_ref'].split('@')[1]: raise ValueError('Tag digest mismatch')
        if 'manifests' in manifest:
            matches=[entry for entry in manifest['manifests'] if entry.get('platform',{}) == {'architecture':'amd64','os':'linux'}]
            if len(matches)!=1: raise ValueError('Expected one linux/amd64 manifest')
            _,manifest=fetch('manifests',matches[0]['digest'])
        _,config=fetch('blobs',manifest['config']['digest'])
        return {'digest':digest,'source':config.get('config',{}).get('Labels',{}).get('org.opencontainers.image.source')}


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    validate=sub.add_parser('validate'); validate.add_argument('--request',required=True); validate.add_argument('--output',required=True)
    order=sub.add_parser('check-order'); order.add_argument('--state',required=True); order.add_argument('--release',required=True)
    args=parser.parse_args(); api=GitHub()
    if args.command=='validate':
        raw=Path(args.request).read_bytes()
        if len(raw)>32768: raise ValueError('Request too large')
        request=json.loads(raw)
        if len(request.get('initial_releases_json','').encode())>16384: raise ValueError('Initial releases too large')
        release=validate_request(request,api)
        for item in release if isinstance(release,list) else [release]: validate_publication(item,api,Registry())
        Path(args.output).write_text(json.dumps(release)+'\n')
    else:
        saved=state.hooks_release_validate(json.loads(Path(args.state).read_text()))
        release=json.loads(Path(args.release).read_text())
        for previous in saved['services'][release['service']].values():
            if previous is not None: check_order(previous,release,api)

if __name__=='__main__': main()
