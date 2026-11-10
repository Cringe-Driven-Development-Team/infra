#!/usr/bin/env bash
# Алерт о выкатке в Telegram: deploy-alert.sh <что выкатывали> <job.status>. Формат — как у backend/cd.yml.
set -euo pipefail
[[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]] || { echo "TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы" >&2; exit 1; }
text="$1 production: $2
Commit: ${GITHUB_SHA:0:7}
$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
args=(--data-urlencode "chat_id=$TELEGRAM_CHAT_ID" --data-urlencode "text=$text")
[[ -z "${TELEGRAM_TOPIC_ID:-}" ]] || args+=(--data-urlencode "message_thread_id=$TELEGRAM_TOPIC_ID")
curl --silent --show-error --fail --max-time 15 "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" "${args[@]}" \
  | jq -e '.ok == true' > /dev/null
