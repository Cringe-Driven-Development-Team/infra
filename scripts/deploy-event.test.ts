import { afterEach, beforeEach, expect, test } from 'bun:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const repository = 'Cringe-Driven-Development-Team/infra';
const api = `https://api.github.com/repos/${repository}/deployments`;
const infraSha = 'a'.repeat(40);
const sourceSha = 'b'.repeat(40);
const validatedRelease = {
    service: 'webhook-alerts', source_sha: sourceSha, publisher_run_id: 15, publisher_run_attempt: 1,
    image_ref: `ghcr.io/cringe-driven-development-team/webhook-alerts@sha256:${'a'.repeat(64)}`,
};
const token = 'fake-private-github-token';
const statusFields = {
    environment: 'production',
    log_url: `https://github.com/${repository}/actions/runs/7/attempts/2`,
};

let temp: string;

beforeEach(async () => {
    temp = mkdtempSync(join(tmpdir(), 'infra-deploy-event-'));
    await Bun.write(join(temp, 'transport.ts'), `
        import { appendFileSync } from 'node:fs';
        import { join } from 'node:path';
        let calls = 0;
        globalThis.fetch = async (url, request) => {
            const authorization = new Headers(request.headers).get('authorization');
            appendFileSync(join(process.env.RUNNER_TEMP, 'requests.jsonl'), JSON.stringify({
                url, method: request.method, authorization, body: JSON.parse(request.body),
            }) + '\\n');
            if (String(++calls) === process.env.TEST_FAILED_CALL) {
                return Response.json({ message: process.env.GH_TOKEN }, { status: 403 });
            }
            return Response.json({ id: 42 }, { status: 201 });
        };
    `);
});

afterEach(() => rmSync(temp, { recursive: true, force: true }));

async function run(args: string[], env: Record<string, string> = {}) {
    const output = join(temp, 'output');
    const result = Bun.spawnSync({
        cmd: [process.execPath, '--preload', join(temp, 'transport.ts'), join(import.meta.dir, 'deploy-event.ts'), ...args],
        env: {
            ...process.env,
            GH_TOKEN: token,
            GITHUB_REPOSITORY: repository,
            GITHUB_REF: 'refs/heads/main',
            GITHUB_SHA: infraSha,
            GITHUB_RUN_ID: '7',
            GITHUB_RUN_ATTEMPT: '2',
            GITHUB_OUTPUT: output,
            RUNNER_TEMP: temp,
            ...env,
        },
    });
    const requestsFile = Bun.file(join(temp, 'requests.jsonl'));
    const requests = await requestsFile.exists() ? (await requestsFile.text()).trim().split('\n').map(line => JSON.parse(line)) : [];
    const stderr = result.stderr.toString();
    const stdout = result.stdout.toString();
    return { exitCode: result.exitCode, stderr, stdout, requests, output: await Bun.file(output).exists() ? await Bun.file(output).text() : '' };
}

test('infra start registers the exact SHA and marks the deployment in progress for this attempt', async () => {
    const { exitCode, stderr, requests, output } = await run(['start', 'infra']);
    expect(stderr).toBe('');
    expect(exitCode).toBe(0);
    expect(output).toBe('deployment_id=42\n');
    expect(requests).toEqual([
        { url: api, method: 'POST', authorization: `Bearer ${token}`, body: {
            ref: infraSha, task: 'infra-alerts', environment: 'production', auto_merge: false, required_contexts: [],
            payload: { schema: 1, kind: 'infra', run_id: 7, run_attempt: 2 },
        } },
        { url: `${api}/42/statuses`, method: 'POST', authorization: `Bearer ${token}`, body: {
            ...statusFields, state: 'in_progress',
        } },
    ]);
});

test.each([
    { operation: 'deploy', release: validatedRelease, image: { service: 'webhook-alerts', source_sha: sourceSha } },
    { operation: 'initialize', release: [validatedRelease, {
        service: 'githubbot', source_sha: 'c'.repeat(40), publisher_run_id: 16, publisher_run_attempt: 1,
        image_ref: `ghcr.io/cringe-driven-development-team/githubbot@sha256:${'b'.repeat(64)}`,
    }], image: {} },
])('VPS3 $operation sends only the applicable validated release metadata', async ({ operation, release, image }) => {
    await Bun.write(join(temp, 'release.json'), JSON.stringify(release));
    const { exitCode, stderr, requests } = await run(['start', 'auxiliary'], {
        DEPLOY_OPERATION: operation,
        DEPLOY_SERVICE: 'githubbot',
        DEPLOY_SHA: 'c'.repeat(40),
    });
    expect(stderr).toBe('');
    expect(exitCode).toBe(0);
    expect(requests[0].body).toMatchObject({ ref: infraSha });
    expect(requests[0].body.payload).toEqual({ schema: 1, kind: 'auxiliary', run_id: 7, run_attempt: 2, operation, ...image });
});

test('VPS3 deploy rejects invalid source metadata before creating a deployment', async () => {
    await Bun.write(join(temp, 'release.json'), JSON.stringify({ service: 'githubbot', source_sha: 'main' }));
    const { exitCode, stderr, requests } = await run(['start', 'auxiliary'], { DEPLOY_OPERATION: 'deploy' });
    expect(exitCode).toBe(1);
    expect(stderr).toContain('release.json');
    expect(requests).toEqual([]);
});

test.each([['success', 'success'], ['failure', 'failure'], ['cancelled', 'error']])(
    'finish maps %s to %s for the existing deployment', async (jobStatus, state) => {
        const { exitCode, stderr, requests } = await run(['finish', '42', jobStatus]);
        expect(stderr).toBe('');
        expect(exitCode).toBe(0);
        expect(requests).toEqual([{ url: `${api}/42/statuses`, method: 'POST', authorization: `Bearer ${token}`, body: {
            ...statusFields, state, auto_inactive: false,
        } }]);
    },
);

test.each([['1', ''], ['2', 'deployment_id=42\n']])(
    'API failure on call %s reports HTTP status safely and keeps any created deployment ID', async (failedCall, expectedOutput) => {
        const { exitCode, stderr, stdout, requests, output } = await run(['start', 'infra'], { TEST_FAILED_CALL: failedCall });
        expect(exitCode).toBe(1);
        expect(stderr).toContain('403');
        expect(stderr + stdout).not.toContain(token);
        expect(requests).toHaveLength(Number(failedCall));
        expect(output).toBe(expectedOutput);
    },
);
