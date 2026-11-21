type Env = Record<string, string | undefined>;
type Deployment = 'infra' | 'auxiliary';
type Status = 'started' | 'success' | 'failure' | 'cancelled';

const statusLabels = {
    started: ['⏳', 'началась'],
    success: ['✅', 'завершилась успешно'],
    failure: ['❌', 'завершилась с ошибкой'],
    cancelled: ['⚪', 'отменена'],
} satisfies Record<Status, [string, string]>;

function escapeHtml(text: string) {
    return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

async function associatedPullRequest(repository: string, sha: string, env: Env, request: typeof fetch) {
    const { GH_TOKEN: token, GITHUB_API_URL: api } = env;
    if (!token || !api) return;

    try {
        const response = await request(`${api}/repos/${repository}/commits/${sha}/pulls?per_page=100`, {
            headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${token}` },
            signal: AbortSignal.timeout(5_000),
            redirect: 'error',
        });
        if (!response.ok || response.headers.get('link')?.includes('rel="next"')) return;

        const pulls = await response.json() as {
            base: { ref: string };
            merged_at: string | null;
            merge_commit_sha: string | null;
            html_url: string;
        }[];
        const matches = pulls.filter((pr) => pr.base.ref === 'main' && pr.merged_at && pr.merge_commit_sha === sha);
        if (matches.length === 1) return matches[0].html_url;
    } catch {
        // Сбой поиска PR не должен задерживать отправку результата.
    }
}

export async function sendDeployAlert(kind: Deployment, status: Status, env: Env = process.env, send = fetch): Promise<void> {
    const { TELEGRAM_BOT_TOKEN: token, TELEGRAM_CHAT_ID: chat } = env;
    if (!token || !chat) throw new Error('TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы');
    const topic = Number(env.TELEGRAM_TOPIC_ID);
    if (!Number.isSafeInteger(topic) || topic <= 0) throw new Error('TELEGRAM_TOPIC_ID должен быть ID темы');

    const isService = kind === 'auxiliary' && env.DEPLOY_OPERATION === 'deploy';
    const service = env.DEPLOY_SERVICE;
    const sha = (isService ? env.DEPLOY_SHA : env.GITHUB_SHA) ?? '';
    const hasSha = /^[0-9a-f]{40}$/i.test(sha);
    let label = 'Выкатка инфры';
    if (kind === 'auxiliary') {
        if (env.DEPLOY_OPERATION === 'initialize') label = 'Инициализация VPS3';
        else if (service === 'githubbot' || service === 'webhook-alerts') label = `Выкатка ${service}`;
        else label = 'Операция на VPS3';
    }

    const [icon, outcome] = statusLabels[status];
    const actor = env.GITHUB_TRIGGERING_ACTOR || env.GITHUB_ACTOR;
    const lines = [
        `<b>${icon} ${label} ${outcome}</b>`,
        hasSha ? `production · <code>${sha.slice(0, 7)}</code>` : 'production',
    ];
    if (status !== 'started') {
        if (env.DEPLOY_FAILED_STEP) lines.push(`Этап сбоя — ${escapeHtml(env.DEPLOY_FAILED_STEP)}`);
        if (env.DEPLOY_HEALTH === 'success') lines.push('Health Check ✅');
        if (env.DEPLOY_HEALTH === 'failure') lines.push('Health Check ❌');
    }
    if (actor) lines.push(`Запустил: ${escapeHtml(actor)}`);

    const run = `${env.GITHUB_SERVER_URL}/${env.GITHUB_REPOSITORY}/actions/runs/${env.GITHUB_RUN_ID}`;
    const logUrl = env.GITHUB_RUN_ATTEMPT ? `${run}/attempts/${env.GITHUB_RUN_ATTEMPT}` : run;
    const keyboard = [[{ text: 'Логи выкатки', url: logUrl }]];
    const source = isService ? 'Cringe-Driven-Development-Team/.github' : env.GITHUB_REPOSITORY;
    if (status !== 'started' && hasSha && source && (kind === 'infra' || isService)) {
        const pr = await associatedPullRequest(source, sha, env, send);
        if (pr) keyboard.unshift([{ text: 'PR', url: pr }]);
    }

    const response = await send(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
            chat_id: chat,
            message_thread_id: topic,
            text: lines.join('\n'),
            parse_mode: 'HTML',
            link_preview_options: { is_disabled: true },
            reply_markup: { inline_keyboard: keyboard },
        }),
        signal: AbortSignal.timeout(15_000),
        redirect: 'error',
    }).catch(() => {
        throw new Error('Не удалось подключиться к Telegram');
    });

    const result = await response.json().catch(() => null) as { ok?: boolean } | null;
    if (!response.ok || !result?.ok) throw new Error(`Telegram ответил ${response.status}`);
}

if (import.meta.main) {
    const [kind, status] = process.argv.slice(2);
    if ((kind !== 'infra' && kind !== 'auxiliary') || !status || !Object.hasOwn(statusLabels, status)) {
        throw new Error('usage: deploy-alert.ts <infra|auxiliary> <started|success|failure|cancelled>');
    }
    await sendDeployAlert(kind, status as Status);
}
