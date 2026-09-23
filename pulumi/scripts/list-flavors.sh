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
PROJECT_ID="$(pulumi stack output projectId -s "$STACK")"
REGION="$(pulumi config get infra:pool -s "$STACK")"
AUTH_URL="https://cloud.api.selcloud.ru/identity/v3"

RESP="$(mktemp)"
TOKEN="$(curl -sS -D - -o "$RESP" -H 'Content-Type: application/json' \
  -d "{\"auth\":{\"identity\":{\"methods\":[\"password\"],\"password\":{\"user\":{\"name\":\"$USERNAME\",\"domain\":{\"name\":\"$DOMAIN\"},\"password\":\"$PASSWORD\"}}},\"scope\":{\"project\":{\"id\":\"$PROJECT_ID\"}}}}" \
  "$AUTH_URL/auth/tokens" | awk -F': ' 'tolower($1)=="x-subject-token"{print $2}' | tr -d '\r')"

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
