#!/usr/bin/env bash
# Показывает флейворы пула VPS: id, имя, vcpus, ram, disk.
#   source pulumi/bootstrap/env.sh && ./scripts/list-flavors.sh [stack]
# Учётка — сервисный пользователь аккаунта из selectel.env (OS_USERNAME/OS_PASSWORD/OS_DOMAIN_NAME).
# Проект — projectId стека; стека ещё нет или он пустой — проект стейта из env.sh (OS_PROJECT_ID):
# публичные флейворы в пуле одни для всех проектов. Пул — infra:pool стека, иначе POOL (по умолчанию ru-9).
set -euo pipefail
cd "$(dirname "$0")/.."

for v in OS_USERNAME OS_PASSWORD OS_DOMAIN_NAME; do
  [[ -n "${!v:-}" ]] || { echo "Нет $v: выполните source pulumi/bootstrap/env.sh" >&2; exit 1; }
done

STACK="${1:-$(pulumi stack --show-name 2>/dev/null || true)}"
PROJECT_ID=""
REGION=""
if [[ -n "$STACK" ]]; then
  PROJECT_ID="$(pulumi stack output projectId -s "$STACK" 2>/dev/null || true)"
  if [[ -z "$PROJECT_ID" ]]; then
    # После неудачного up outputs пустые — ищем проект в стейте (у нового стека ресурсов нет вовсе)
    PROJECT_ID="$(pulumi stack export -s "$STACK" 2>/dev/null | python3 -c '
import json, sys
try:
    res = json.load(sys.stdin).get("deployment", {}).get("resources") or []
except ValueError:
    res = []
print(next((r.get("id", "") for r in res if r.get("type", "").endswith("VpcProjectV2")), ""))
' || true)"
  fi
  REGION="$(pulumi config get infra:pool -s "$STACK" 2>/dev/null || true)"
fi
if [[ -z "$PROJECT_ID" ]]; then
  PROJECT_ID="${OS_PROJECT_ID:-}"
  [[ -n "$PROJECT_ID" ]] || { echo "Нет projectId ни в стеке, ни в OS_PROJECT_ID (env.sh без SELECTEL_PROJECT)" >&2; exit 1; }
  echo "Проект стека не найден — берём проект стейта $PROJECT_ID" >&2
fi
REGION="${REGION:-${POOL:-ru-9}}"
AUTH_URL="${OS_AUTH_URL:-https://cloud.api.selcloud.ru/identity/v3/}"
AUTH_URL="${AUTH_URL%/}"

RESP="$(mktemp)"
trap 'rm -f "$RESP"' EXIT
# Тело собирает python: корректное JSON-экранирование пароля с " и \.
# Пароль берётся из окружения и уходит в curl через stdin — в ps его не видно.
AUTH_BODY="$(python3 - "$PROJECT_ID" <<'PY'
import json, os, sys
print(json.dumps({"auth": {
    "identity": {"methods": ["password"], "password": {"user": {
        "name": os.environ["OS_USERNAME"], "domain": {"name": os.environ["OS_DOMAIN_NAME"]},
        "password": os.environ["OS_PASSWORD"]}}},
    "scope": {"project": {"id": sys.argv[1]}},
}}))
PY
)"
TOKEN="$(printf '%s' "$AUTH_BODY" | curl -sS -D - -o "$RESP" -H 'Content-Type: application/json' \
  --data-binary @- "$AUTH_URL/auth/tokens" \
  | awk -F': ' 'tolower($1)=="x-subject-token"{print $2}' | tr -d '\r')"
unset AUTH_BODY

if [[ -z "$TOKEN" ]]; then
  echo "Не получен токен. Ответ Keystone:" >&2
  cat "$RESP" >&2
  exit 1
fi

NOVA="$(python3 - "$RESP" "$REGION" <<'PY'
import json, sys
catalog = json.load(open(sys.argv[1]))["token"]["catalog"]
region = sys.argv[2]
for svc in catalog:
    if svc["type"] == "compute":
        for ep in svc["endpoints"]:
            if ep["interface"] == "public" and ep["region"] == region:
                print(ep["url"]); raise SystemExit
raise SystemExit("compute endpoint для пула %s не найден" % region)
PY
)"

echo "Пул $REGION, проект $PROJECT_ID" >&2
PRINT_FLAVORS="$(cat <<'PY'
import json, sys
flavors = json.load(sys.stdin)["flavors"]
print(f'{"id":<8} {"name":<24} {"vcpus":>5} {"ram":>7} {"disk":>5}')
for f in sorted(flavors, key=lambda f: (f["vcpus"], f["ram"])):
    print(f'{f["id"]:<8} {f["name"]:<24} {f["vcpus"]:>5} {f["ram"]:>7} {f["disk"]:>5}')
PY
)"
# Токен — через stdin (-K -), а не аргументом: в ps его не видно
curl -sS -K - "$NOVA/flavors/detail" <<<"header = \"X-Auth-Token: $TOKEN\"" | python3 -c "$PRINT_FLAVORS"
