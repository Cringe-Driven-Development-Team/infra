"""Queue, credential boundaries and shared helper contracts."""
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[2]

class WorkflowChecks(unittest.TestCase):
    def test_docker_actionlint_args_preserve_narrow_ignore(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/actionlint.yml').read_text())
        step=workflow['jobs']['actionlint']['steps'][-1]
        # Docker-action args are whitespace-split without shell quote processing.
        args=step['with']['args'].split()
        self.assertEqual(args[:2], ['-color', '-ignore'])
        self.assertEqual(len(args), 3)
        pattern=args[2]
        self.assertIsNotNone(re.search(pattern, 'unexpected key "queue" for "concurrency" section. expected one of...'))
        self.assertIsNone(re.search(pattern, 'unexpected key "queue" for "job" section.'))
        self.assertIsNone(re.search(pattern, 'unexpected key "other" for "concurrency" section.'))

    def test_run_name_correlates_dispatch_request(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/auxiliary-deploy.yml').read_text())
        self.assertEqual(workflow.get('run-name'), 'VPS3 ${{ inputs.request_id || inputs.operation }}')

    def test_queue_schema_and_secret_order(self):
        auxiliary=yaml.safe_load((ROOT/'.github/workflows/auxiliary-deploy.yml').read_text())
        infra=yaml.safe_load((ROOT/'.github/workflows/infra.yml').read_text())
        expected={'group':'infra-production','cancel-in-progress':False,'queue':'max'}
        for job in [auxiliary['jobs']['production'],infra['jobs']['apply']]:
            self.assertEqual(job['concurrency'],expected)
            self.assertEqual(job['timeout-minutes'],60)
        self.assertEqual(auxiliary['jobs']['production']['needs'],'validate')
        self.assertNotIn('environment',auxiliary['jobs']['validate'])
        steps=auxiliary['jobs']['production']['steps']
        self.assertIn('validate --request',steps[1]['run'])
        self.assertNotIn('secrets.',str(auxiliary['jobs']['validate']))
        self.assertNotIn('pulumi',str(auxiliary).lower())
        apply=next(s['run'] for s in steps if s.get('name')=='Check order and apply selected operation')
        self.assertLess(apply.index('check-order'),apply.index('deploy-auxiliary.yml'))
        self.assertIn('inventory/ci.yml',apply)
        self.assertIn('initialize-auxiliary-releases.yml',infra['jobs']['checks']['steps'][-2]['run'])
        self.assertIn('AUXILIARY_INITIAL_RELEASES', str(infra['jobs']['preview']))
        self.assertNotIn('AUXILIARY_INITIAL_RELEASES', str(infra['jobs']['apply']))
        self.assertNotIn('AUXILIARY_INITIAL_RELEASES', str(auxiliary))

    def test_shared_secret_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); key=root/'key'
            subprocess.run(['ssh-keygen','-t','ed25519','-N','','-f',str(key)],check=True,capture_output=True)
            host='hooks.cellestial.ru '+key.with_suffix('.pub').read_text()
            env=os.environ | {'RUNNER_TEMP':directory,'GITHUB_ENV':str(root/'env'),
                'DEPLOY_SSH_KEY':key.read_text(),'SSH_KNOWN_HOSTS':host,'ANSIBLE_VAULT_PASSWORD':'fake-private-vault'}
            def run(env,host='hooks.cellestial.ru'):
                return subprocess.run(['bash',str(ROOT/'scripts/ansible-ci-secrets.sh'),host],env=env,capture_output=True,text=True)
            result=run(env); self.assertEqual(result.returncode,0,result.stderr)
            for name in ['ci-key','known_hosts','vault-pass']:
                self.assertEqual((root/'infra'/name).stat().st_mode & 0o777,0o600)
            for changed in [env | {'DEPLOY_SSH_KEY':''},env | {'SSH_KNOWN_HOSTS':'unrelated invalid'}]:
                result=run(changed); self.assertNotEqual(result.returncode,0)
                self.assertNotIn(env['ANSIBLE_VAULT_PASSWORD'],result.stdout+result.stderr)
            self.assertNotEqual(run(env,'cellestial.ru').returncode,0)
