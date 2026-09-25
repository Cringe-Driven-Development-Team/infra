#!/usr/bin/env bash
# Показывает флейворы, доступные проекту стека: id, имя, vcpus, ram, disk.
# Работает от сервисного пользователя аккаунта (selectel:username), проект — из outputs стека.
#   ./scripts/list-flavors.sh [stack]
set -euo pipefail
cd "$(dirname "$0")/.."

STACK="${1:-$(pulumi stack --show-name)}"
DOMAIN="$(pulumi config get selectel:domainName -s "$STACK")"
USERNAME="$(pulumi config get selectel:username -s "$STACK")"
PASSWORD="$(pulumi config get selectel:password -s "$STACK")"
# После неудачного up outputs пустые — берём id проекта прямо из стейта
PROJECT_ID="$(pulumi stack output projectId -s "$STACK" 2>/dev/null || true)"
if [[ -z "$PROJECT_ID" ]]; then
  PROJECT_ID="$(pulumi stack export -s "$STACK" \
    | python3 -c 'import json,sys; print(next((r["id"] for r in json.load(sys.stdin)["deployment"]["resources"] if r["type"].endswith("VpcProjectV2")), ""))')"
fi
[[ -n "$PROJECT_ID" ]] || { echo "Не нашёл projectId ни в outputs, ни в стейте" >&2; exit 1; }
REGION="$(pulumi config get infra:pool -s "$STACK")"
AUTH_URL="https://cloud.api.selcloud.ru/identity/v3"

RESP="$(mktemp)"
# Тело собирает python: корректное JSON-экранирование пароля с " и \.
# Пароль передаётся через окружение python и stdin curl, а не аргументами — его не видно в ps.
AUTH_BODY="$(SEL_PASSWORD="$PASSWORD" python3 - "$USERNAME" "$DOMAIN" "$PROJECT_ID" <<'PY'
import json, os, sys
user, domain, project = sys.argv[1:4]
print(json.dumps({"auth": {
    "identity": {"methods": ["password"], "password": {"user": {
        "name": user, "domain": {"name": domain}, "password": os.environ["SEL_PASSWORD"]}}},
    "scope": {"project": {"id": project}},
}}))
PY
)"
TOKEN="$(printf '%s' "$AUTH_BODY" | curl -sS -D - -o "$RESP" -H 'Content-Type: application/json' \
  --data-binary @- "$AUTH_URL/auth/tokens" \
  | awk -F': ' 'tolower($1)=="x-subject-token"{print $2}' | tr -d '\r')"

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
raise SystemExit("compute endpoint для региона %s не найден" % region)
PY
)"

curl -sS -H "X-Auth-Token: $TOKEN" "$NOVA/flavors/detail" | python3 - <<'PY'
import json, sys
flavors = json.load(sys.stdin)["flavors"]
print(f'{"id":<8} {"name":<24} {"vcpus":>5} {"ram":>7} {"disk":>5}')
for f in sorted(flavors, key=lambda f: (f["vcpus"], f["ram"])):
    print(f'{f["id"]:<8} {f["name"]:<24} {f["vcpus"]:>5} {f["ram"]:>7} {f["disk"]:>5}')
PY
rm -f "$RESP"
