"""Trust decisions exercise adapters without network or production credentials."""
import hashlib
import io
import json
from unittest.mock import patch
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('executor', Path(__file__).resolve().parents[2] / 'scripts/auxiliary_release.py')
executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(executor)
from test_hooks_release import release

class API:
    def __init__(self):
        self.run = {'id': 1, 'run_attempt': 2, 'event': 'push', 'head_branch': 'main',
            'head_sha': 'b'*40, 'status': 'completed', 'conclusion': 'success',
            'path': '.github/workflows/githubbot-image.yml',
            'repository': {'full_name': executor.SOURCE}, 'head_repository': {'full_name': executor.SOURCE}}
        self.status = 'ahead'
    def get(self, path):
        if '/attempts/' in path: return self.run
        if '/compare/' in path: return {'status': self.status}
        raise AssertionError(path)

class TrustChecks(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.release = release('githubbot') | {'publisher_run_id': 1, 'publisher_run_attempt': 2}
        self.request = {'operation': 'deploy', 'service': 'githubbot', 'image_ref': self.release['image_ref'],
                        'source_sha': self.release['source_sha'], 'request_id': 'githubbot:1:2:3:1', 'initial_releases_json': ''}
    def test_valid_request(self):
        self.assertEqual(executor.validate_request(self.request, self.api), self.release)
    def test_untrusted_attempt_rejected(self):
        for key, value in [('event','pull_request'), ('head_branch','other'), ('status','in_progress'),
                           ('conclusion','cancelled'), ('conclusion','failure'), ('head_sha','c'*40),
                           ('path','.github/workflows/evil.yml'), ('run_attempt',3), ('id',2),
                           ('head_repository',{'full_name':'fork/repo'}), ('repository',{'full_name':'fork/repo'})]:
            with self.subTest(key=key, value=value):
                api = API(); api.run[key] = value
                with self.assertRaises(ValueError): executor.validate_request(self.request, api)
    def test_bad_inputs_and_mixed_operation(self):
        for patch in [{'service':'other'}, {'source_sha':'x'}, {'request_id':'githubbot:0:2:3:1'},
                      {'initial_releases_json':'[]'}, {'image_ref':self.release['image_ref'].replace('/githubbot@','/evil@')}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                executor.validate_request(self.request | patch, self.api)
    def test_initialize_exact_explicit_attempts_without_run_listing(self):
        second=release('webhook-alerts') | {'publisher_run_id':1,'publisher_run_attempt':2}
        original=self.api.get
        # Each explicit old attempt is fetched directly; there is no paginated run search.
        class ExplicitAPI:
            def get(inner,path):
                result=original(path)
                if '/attempts/' in path:
                    result=dict(result)
                    result['path']='.github/workflows/'+executor.WORKFLOWS[inner.selected]
                    inner.selected='webhook-alerts'
                return result
            selected='githubbot'
        request=dict(operation='initialize',service='',image_ref='',source_sha='',request_id='',
                     initial_releases_json=json.dumps([self.release,second]))
        self.assertEqual(executor.validate_request(request,ExplicitAPI()),[self.release,second])
        with self.assertRaises(ValueError): executor.validate_request(request | {'service':'githubbot'},ExplicitAPI())
        class Down:
            def get(self,path): raise OSError('API unavailable')
        with self.assertRaises(OSError): executor.validate_request(self.request,Down())

    def test_non_main_ancestor_rejected(self):
        self.api.status = 'diverged'
        with self.assertRaises(ValueError): executor.validate_request(self.request,self.api)
    def test_order(self):
        old = release('githubbot','b','c')
        executor.check_order(self.release,self.release,self.api)
        executor.check_order(old,self.release,self.api)
        for status in ['behind','diverged']:
            self.api.status=status
            with self.assertRaises(ValueError): executor.check_order(old,self.release,self.api)
        with self.assertRaises(ValueError): executor.check_order(self.release,self.release | {'image_ref':old['image_ref']},self.api)
    def test_registry_provenance_and_tag_digest(self):
        class Registry:
            result = {'digest': self.release['image_ref'].split('@')[1], 'source': 'https://github.com/'+executor.SOURCE}
            def inspect(self, release): return self.result
        registry=Registry()
        executor.validate_publication(self.release,self.api,registry)
        for patch in [{'digest':'sha256:'+'f'*64},{'source':'https://github.com/evil/repo'}]:
            registry.result=Registry.result | patch
            with self.assertRaises(ValueError): executor.validate_publication(self.release,self.api,registry)
        class Down:
            def inspect(self, release): raise OSError('offline')
        with self.assertRaises(OSError): executor.validate_publication(self.release,self.api,Down())

class AdapterChecks(unittest.TestCase):
    def test_anonymous_registry_checks_real_content_hash_and_config(self):
        config=json.dumps({'config':{'Labels':{'org.opencontainers.image.source':'https://github.com/'+executor.SOURCE}}}).encode()
        config_digest='sha256:'+hashlib.sha256(config).hexdigest()
        manifest=json.dumps({'config':{'digest':config_digest}}).encode()
        digest='sha256:'+hashlib.sha256(manifest).hexdigest()
        chosen=release('githubbot') | {'image_ref':'ghcr.io/cringe-driven-development-team/githubbot@'+digest}
        seen=[]
        def fetch(request,timeout):
            url=request if isinstance(request,str) else request.full_url
            seen.append(url)
            if '/token?' in url: return io.BytesIO(b'{"token":"anonymous"}')
            if '/manifests/' in url: return io.BytesIO(manifest)
            if '/blobs/' in url: return io.BytesIO(config)
            raise AssertionError(url)
        with patch.object(executor.urllib.request,'urlopen',side_effect=fetch):
            self.assertEqual(executor.Registry().inspect(chosen),{'digest':digest,'source':'https://github.com/'+executor.SOURCE})
            with self.assertRaises(ValueError): executor.Registry().inspect(release('githubbot'))
        self.assertIn('/manifests/sha-'+chosen['source_sha'],seen[1])
    def test_api_uses_current_version_and_exact_attempt_path(self):
        with patch.dict(executor.os.environ,{'GH_TOKEN':'fixture-token'}), patch.object(executor.urllib.request,'urlopen',return_value=io.BytesIO(b'{}')) as fetch:
            executor.GitHub().get('/repos/'+executor.SOURCE+'/actions/runs/1/attempts/2')
            request=fetch.call_args.args[0]
            self.assertEqual(request.get_header('X-github-api-version'),'2026-03-10')
            self.assertTrue(request.full_url.endswith('/runs/1/attempts/2'))
