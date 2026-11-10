import { expect, test } from 'bun:test';
import { sendDeployAlert } from './deploy-alert';

const env = {
    TELEGRAM_BOT_TOKEN: 't',
    TELEGRAM_CHAT_ID: '-1',
    GITHUB_SHA: 'abcdef123456',
    GITHUB_SERVER_URL: 'https://github.com',
    GITHUB_REPOSITORY: 'o/infra',
    GITHUB_RUN_ID: '7',
};

function stub(reply: object) {
    const calls: { url: string; body: URLSearchParams }[] = [];
    const send = (async (url: string, init: RequestInit) => {
        calls.push({ url, body: init.body as URLSearchParams });
        return Response.json(reply);
    }) as unknown as typeof fetch;
    return { calls, send };
}

test('sends status, commit and run link to the topic', async () => {
    const { calls, send } = stub({ ok: true });
    await sendDeployAlert('Infra', 'failure', { ...env, TELEGRAM_TOPIC_ID: '5' }, send);
    expect(calls[0].url).toBe('https://api.telegram.org/bott/sendMessage');
    expect(Object.fromEntries(calls[0].body)).toEqual({
        chat_id: '-1',
        text: 'Infra production: failure\nCommit: abcdef1\nhttps://github.com/o/infra/actions/runs/7',
        message_thread_id: '5',
    });
});

test('omits topic when not set', async () => {
    const { calls, send } = stub({ ok: true });
    await sendDeployAlert('Auxiliary', 'success', env, send);
    expect(calls[0].body.has('message_thread_id')).toBe(false);
});

test('fails without credentials or on Telegram error', async () => {
    const { send } = stub({ ok: false, description: 'Bad Request' });
    await expect(sendDeployAlert('Infra', 'success', { ...env, TELEGRAM_CHAT_ID: '' }, send)).rejects.toThrow('не заданы');
    await expect(sendDeployAlert('Infra', 'success', env, send)).rejects.toThrow('Bad Request');
});
