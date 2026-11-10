// Алерт о выкатке в Telegram: bun scripts/deploy-alert.ts <что выкатывали> <job.status>. Формат — как у backend/cd.yml.
type Env = Record<string, string | undefined>;

export async function sendDeployAlert(label: string, status: string, env: Env = process.env, send = fetch) {
    const { TELEGRAM_BOT_TOKEN: token, TELEGRAM_CHAT_ID: chat, TELEGRAM_TOPIC_ID: topic } = env;
    if (!token || !chat) throw new Error('TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы');
    const text = [
        `${label} production: ${status}`,
        `Commit: ${env.GITHUB_SHA?.slice(0, 7)}`,
        `${env.GITHUB_SERVER_URL}/${env.GITHUB_REPOSITORY}/actions/runs/${env.GITHUB_RUN_ID}`,
    ].join('\n');
    const body = new URLSearchParams({ chat_id: chat, text, ...(topic ? { message_thread_id: topic } : {}) });
    const response = await send(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: 'POST',
        body,
        signal: AbortSignal.timeout(15_000),
    });
    const result = (await response.json().catch(() => ({}))) as { ok?: boolean; description?: string };
    if (!result.ok) throw new Error(`Telegram ответил ${response.status}: ${result.description ?? 'без описания'}`);
}

if (import.meta.main) {
    const [label, status] = process.argv.slice(2);
    if (!label || !status) throw new Error('usage: deploy-alert.ts <label> <status>');
    await sendDeployAlert(label, status);
}
