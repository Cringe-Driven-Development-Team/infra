import { appendFileSync } from 'node:fs';
import { join } from 'node:path';

const repository = 'Cringe-Driven-Development-Team/infra';
const environment = 'production';
const shaPattern = /^[0-9a-f]{40}$/;

function positiveId(value: string | undefined, name: string) {
    const id = Number(value);
    if (!value || !/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(id)) {
        throw new Error(`${name} должен быть положительным целым числом`);
    }
    return id;
}

async function auxiliaryMetadata() {
    const { DEPLOY_OPERATION: operation, RUNNER_TEMP: temp } = process.env;
    if (operation !== 'deploy' && operation !== 'initialize') throw new Error('Некорректный DEPLOY_OPERATION');
    if (!temp) throw new Error('RUNNER_TEMP не задан');

    const release = await Bun.file(join(temp, 'release.json')).json().catch(() => null) as
        { service?: unknown; source_sha?: unknown } | unknown[] | null;
    if (operation === 'initialize' && Array.isArray(release)) return { operation };
    if (operation === 'deploy' && release && !Array.isArray(release)) {
        const { service, source_sha } = release;
        if ((service === 'githubbot' || service === 'webhook-alerts') && typeof source_sha === 'string' && shaPattern.test(source_sha)) {
            return { operation, service, source_sha };
        }
    }
    throw new Error('Некорректный валидированный release.json для операции VPS3');
}

async function main([command, value, jobStatus, extra]: string[]) {
    const { GH_TOKEN: token, GITHUB_REPOSITORY: repo, GITHUB_REF: ref, GITHUB_SHA: sha,
        GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, GITHUB_OUTPUT: output } = process.env;
    if (!token) throw new Error('GH_TOKEN не задан');
    if (repo !== repository || ref !== 'refs/heads/main') throw new Error('Публикация Deployment разрешена из main репозитория infra');
    const runId = positiveId(GITHUB_RUN_ID, 'GITHUB_RUN_ID');
    const runAttempt = positiveId(GITHUB_RUN_ATTEMPT, 'GITHUB_RUN_ATTEMPT');
    const statusFields = {
        environment,
        log_url: `https://github.com/${repository}/actions/runs/${runId}/attempts/${runAttempt}`,
    };

    async function post(path: string, body: object) {
        const response = await fetch(`https://api.github.com/repos/${repository}/deployments${path}`, {
            method: 'POST',
            headers: {
                Accept: 'application/vnd.github+json',
                Authorization: `Bearer ${token}`,
                'Content-Type': 'application/json',
                'X-GitHub-Api-Version': '2026-03-10',
            },
            body: JSON.stringify(body),
            signal: AbortSignal.timeout(5_000),
            redirect: 'error',
        }).catch(() => { throw new Error('Не удалось подключиться к GitHub Deployments API'); });
        if (response.status !== 201) throw new Error(`GitHub Deployments API ответил HTTP ${response.status}`);
        return response;
    }

    if (command === 'start' && (value === 'infra' || value === 'auxiliary') && jobStatus === undefined) {
        if (!output) throw new Error('GITHUB_OUTPUT не задан');
        if (!sha || !shaPattern.test(sha)) throw new Error('GITHUB_SHA должен быть полным SHA коммита infra');
        const metadata = value === 'auxiliary' ? await auxiliaryMetadata() : {};
        const response = await post('', {
            ref: sha, task: 'infra-alerts', environment, auto_merge: false, required_contexts: [],
            payload: { schema: 1, kind: value, run_id: runId, run_attempt: runAttempt, ...metadata },
        });
        const deployment = await response.json().catch(() => null) as { id?: unknown } | null;
        const id = positiveId(typeof deployment?.id === 'number' ? String(deployment.id) : undefined, 'Deployment ID');
        // Финализация должна знать ID даже при ошибке публикации in_progress.
        appendFileSync(output, `deployment_id=${id}\n`);
        await post(`/${id}/statuses`, { ...statusFields, state: 'in_progress' });
        return;
    }

    if (command === 'finish' && extra === undefined && (jobStatus === 'success' || jobStatus === 'failure' || jobStatus === 'cancelled')) {
        const id = positiveId(value, 'Deployment ID');
        const state = jobStatus === 'cancelled' ? 'error' : jobStatus;
        await post(`/${id}/statuses`, { ...statusFields, state, auto_inactive: false });
        return;
    }

    throw new Error('usage: deploy-event.ts start <infra|auxiliary> | finish <deployment_id> <success|failure|cancelled>');
}

await main(process.argv.slice(2)).catch((error: Error) => {
    console.error(error.message);
    process.exitCode = 1;
});
