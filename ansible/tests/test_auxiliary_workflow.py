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
        step_indexes = {step.get('id'): index for index, step in enumerate(steps)}
        prepare = step_indexes['prepare']
        deploy = step_indexes['deploy']
        self.assertIn('start', step_indexes)
        self.assertLess(prepare, step_indexes['start'])
        self.assertLess(step_indexes['start'], deploy)
        self.assertIn('check-order', steps[prepare]['run'])
        self.assertIn('deploy-auxiliary.yml', steps[deploy]['run'])
        self.assertIn('inventory/ci.yml', steps[deploy]['run'])
        self.assertIn('initialize-auxiliary-releases.yml',infra['jobs']['checks']['steps'][-2]['run'])
        self.assertIn('AUXILIARY_INITIAL_RELEASES', str(infra['jobs']['preview']))
        self.assertNotIn('AUXILIARY_INITIAL_RELEASES', str(infra['jobs']['apply']))
        self.assertNotIn('AUXILIARY_INITIAL_RELEASES', str(auxiliary))

    def test_deployment_write_permissions_and_production_job_names(self):
        for name, production, job_name in [('infra', 'apply', 'Выкатка на прод'),
                                           ('auxiliary-deploy', 'production', 'production')]:
            with self.subTest(workflow=name):
                workflow = yaml.safe_load((ROOT / f'.github/workflows/{name}.yml').read_text())
                self.assertNotIn('deployments', workflow['permissions'])
                for key, job in workflow['jobs'].items():
                    permissions = job.get('permissions', {})
                    if key == production:
                        self.assertEqual(permissions.get('deployments'), 'write')
                        self.assertEqual(job.get('name', key), job_name)
                    else:
                        self.assertNotIn('deployments', permissions)
        steps = yaml.safe_load((ROOT / '.github/workflows/infra.yml').read_text())['jobs']['apply']['steps']
        step_indexes = {step.get('id'): index for index, step in enumerate(steps)}
        self.assertIn('start', step_indexes)
        self.assertLess(step_indexes['start'], step_indexes['pulumi_up'])
        self.assertEqual(steps[step_indexes['verify']]['name'], 'verify.yml')

    def test_deployment_publication_failure_warns_and_does_not_fail_workflow(self):
        stubs = r"""
bun() {
    printf '%s\n' "$*"
    if [[ "$2" == start ]]; then
        echo 'deployment_id=42' >> "$GITHUB_OUTPUT"
    fi
    return 1
}
npx() {
    shift 2
    bun "$@"
}
"""
        for workflow, job, kind in [('infra', 'apply', 'infra'), ('auxiliary-deploy', 'production', 'auxiliary')]:
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as directory:
                config = yaml.safe_load((ROOT / f'.github/workflows/{workflow}.yml').read_text())
                steps = {step.get('id'): step for step in config['jobs'][job]['steps']}
                self.assertIn('start', steps.keys())
                self.assertIn('finish', steps.keys())
                self.assertEqual(steps['finish']['if'], "always() && steps.start.outputs.deployment_id != ''")
                output = Path(directory) / 'output'
                env = os.environ | {
                    'GITHUB_OUTPUT': str(output), 'DEPLOYMENT_ID': '42', 'DEPLOY_STATUS': 'failure',
                }
                for step_id, args in [('start', f'start {kind}'), ('finish', 'finish 42 failure')]:
                    result = subprocess.run(['bash', '-e', '-c', stubs + steps[step_id]['run']], cwd=directory,
                                            env=env, capture_output=True, text=True, check=True)
                    self.assertIn(f'scripts/deploy-event.ts {args}\n', result.stdout)
                    self.assertIn('::warning::', result.stdout)
                self.assertEqual(output.read_text(), 'deployment_id=42\n')

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
