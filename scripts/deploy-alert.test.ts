import { expect, test } from 'bun:test';
import { sendDeployAlert } from './deploy-alert';

const env = {
    TELEGRAM_BOT_TOKEN: 'test-token',
    TELEGRAM_CHAT_ID: '-1001234567890',
    TELEGRAM_TOPIC_ID: '14389',
    GITHUB_SHA: 'abcdef123456abcdef123456abcdef123456abcd',
    GITHUB_SERVER_URL: 'https://github.com',
    GITHUB_API_URL: 'https://api.github.com',
    GITHUB_REPOSITORY: 'o/infra',
    GITHUB_RUN_ID: '7',
    GITHUB_RUN_ATTEMPT: '2',
    GITHUB_ACTOR: 'actor',
};

const logs = { text: 'Логи выкатки', url: 'https://github.com/o/infra/actions/runs/7/attempts/2' };

type Card = {
    text: string;
    reply_markup: { inline_keyboard: { text: string; url: string }[][] };
};

function stub(github?: () => Response, telegram = { ok: true }) {
    const calls: { url: string; headers: Headers; body?: Card }[] = [];
    const send = (async (url: string, init: RequestInit = {}) => {
        const body = init.body instanceof URLSearchParams ? Object.fromEntries(init.body) :
            typeof init.body === 'string' ? JSON.parse(init.body) : undefined;
        calls.push({ url, headers: new Headers(init.headers), body });
        return url.startsWith('https://api.telegram.org/') ? Response.json(telegram) : github!();
    }) as unknown as typeof fetch;
    return { calls, send };
}

test('sends the started card to Infra Alerts', async () => {
    const { calls, send } = stub();
    await sendDeployAlert('infra', 'started', { ...env, GITHUB_TRIGGERING_ACTOR: 'a<&' }, send);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('https://api.telegram.org/bottest-token/sendMessage');
    expect(calls[0].headers.get('content-type')).toBe('application/json');
    expect(calls[0].body).toMatchObject({
        chat_id: '-1001234567890',
        message_thread_id: 14389,
        parse_mode: 'HTML',
        link_preview_options: { is_disabled: true },
        reply_markup: { inline_keyboard: [[logs]] },
    });
    expect(calls[0].body?.text).toContain('<b>⏳ Выкатка инфры началась</b>');
    expect(calls[0].body?.text).toContain('production');
    expect(calls[0].body?.text).toContain('Запустил: a&lt;&amp;');
});

test('renders only known health and failure evidence', async () => {
    const { calls, send } = stub();
    await sendDeployAlert('infra', 'success', { ...env, DEPLOY_HEALTH: 'success' }, send);
    await sendDeployAlert('infra', 'failure', { ...env, DEPLOY_HEALTH: 'failure', DEPLOY_FAILED_STEP: 'pulumi up' }, send);
    await sendDeployAlert('infra', 'cancelled', { ...env, DEPLOY_HEALTH: 'skipped' }, send);
    expect(calls[0].body?.text).toContain('<b>✅ Выкатка инфры завершилась успешно</b>');
    expect(calls[0].body?.text).toContain('Health Check ✅');
    expect(calls[1].body?.text).toContain('<b>❌ Выкатка инфры завершилась с ошибкой</b>');
    expect(calls[1].body?.text).toContain('Этап сбоя — pulumi up');
    expect(calls[1].body?.text).toContain('Health Check ❌');
    expect(calls[2].body?.text).toContain('<b>⚪ Выкатка инфры отменена</b>');
    expect(calls[2].body?.text).not.toContain('Health Check');
});

test('links to the current attempt and selects the correct revision', async () => {
    const { calls, send } = stub();
    const release = { ...env, DEPLOY_SERVICE: 'webhook-alerts', DEPLOY_SHA: '9876543210abcdef9876543210abcdef98765432' };
    await sendDeployAlert('auxiliary', 'success', { ...release, DEPLOY_OPERATION: 'deploy' }, send);
    await sendDeployAlert('auxiliary', 'started', { ...release, DEPLOY_OPERATION: 'initialize' }, send);
    await sendDeployAlert('auxiliary', 'failure', { ...release, DEPLOY_OPERATION: 'deploy', DEPLOY_SHA: '', GITHUB_RUN_ATTEMPT: '' }, send);
    expect(calls[0].body?.text).toContain('Выкатка webhook-alerts');
    expect(calls[0].body?.text).toContain('<code>9876543</code>');
    expect(calls[0].body?.reply_markup.inline_keyboard).toEqual([[logs]]);
    expect(calls[1].body?.text).toContain('Инициализация VPS3');
    expect(calls[1].body?.text).toContain('<code>abcdef1</code>');
    expect(calls[2].body?.text).not.toContain('abcdef1');
    expect(calls[2].body?.reply_markup.inline_keyboard[0][0].url).toBe('https://github.com/o/infra/actions/runs/7');
});

test('includes a PR only for one matching merge', async () => {
    const pr = {
        html_url: 'https://github.com/o/infra/pull/168',
        merged_at: '2026-11-21T12:00:00Z',
        merge_commit_sha: env.GITHUB_SHA,
        base: { ref: 'main' },
    };
    const cases = [
        { response: () => Response.json([pr, { ...pr, merge_commit_sha: 'another' }]), keyboard: [[{ text: 'PR', url: pr.html_url }], [logs]] },
        { response: () => Response.json([pr, { ...pr, html_url: 'https://github.com/o/infra/pull/169' }]), keyboard: [[logs]] },
        { response: () => Response.json([pr], { headers: { link: '<https://api.github.com/page2>; rel="next"' } }), keyboard: [[logs]] },
        { response: () => Response.json({ message: 'Not Found' }, { status: 404 }), keyboard: [[logs]] },
        { response: () => { throw new Error('unavailable'); }, keyboard: [[logs]] },
    ];
    for (const { response, keyboard } of cases) {
        const { calls, send } = stub(response);
        await sendDeployAlert('infra', 'success', { ...env, GH_TOKEN: 'github-token' }, send);
        expect(calls[0].url).toStartWith('https://api.github.com/repos/o/infra/commits/' + env.GITHUB_SHA + '/pulls');
        expect(calls[0].headers.get('authorization')).toBe('Bearer github-token');
        expect(calls[1].headers.has('authorization')).toBe(false);
        expect(calls[1].body?.reply_markup.inline_keyboard).toEqual(keyboard);
    }
});

test.each(['', 'n/a', '0'])('requires the infra topic (%j)', async (topic) => {
    const { calls, send } = stub();
    await expect(sendDeployAlert('infra', 'started', { ...env, TELEGRAM_TOPIC_ID: topic }, send)).rejects.toThrow('TELEGRAM_TOPIC_ID');
    expect(calls).toHaveLength(0);
});

test('fails without credentials or on Telegram error', async () => {
    const { send } = stub(undefined, { ok: false });
    await expect(sendDeployAlert('infra', 'success', { ...env, TELEGRAM_CHAT_ID: '' }, send)).rejects.toThrow('TELEGRAM_CHAT_ID');
    await expect(sendDeployAlert('infra', 'success', env, send)).rejects.toThrow('Telegram ответил');
});
