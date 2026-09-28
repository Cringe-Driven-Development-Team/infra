#!/usr/bin/env bash
# Проверяет S3-ключ стека напрямую, без awscli: curl сам подписывает запрос (SigV4)
# и ходит через системное доверие macOS. Секрет передаётся curl'у через stdin.
#   export PULUMI_CONFIG_PASSPHRASE=...
#   ./scripts/s3-check.sh [stack]
set -euo pipefail
cd "$(dirname "$0")/.."

STACK="${1:-$(pulumi stack --show-name)}"
POOL="$(pulumi config get infra:s3Pool -s "$STACK")"
BUCKET="$(pulumi config get infra:s3Bucket -s "$STACK")"
ENDPOINT="https://s3.${POOL}.storage.selcloud.ru"

STATE="$(mktemp)"
BODY="$(mktemp)"
trap 'rm -f "$STATE" "$BODY"' EXIT
pulumi stack export --show-secrets -s "$STACK" > "$STATE"

CREDS="$(python3 - "$STATE" <<'PY'
import json, sys
res = json.load(open(sys.argv[1]))["deployment"]["resources"]
cred = next((r for r in res if r["type"].endswith("IamS3CredentialsV1")), None)
if not cred:
    sys.exit("В стейте нет IamS3CredentialsV1")
out = cred.get("outputs", {})

def unwrap(v):
    # секреты Pulumi: {"4dabf18193072939515e22adb298388d": "...", "plaintext": "\"значение\""}
    if isinstance(v, dict):
        if "plaintext" in v:
            return json.loads(v["plaintext"])
        if "ciphertext" in v:
            sys.exit("Секрет зашифрован — задайте PULUMI_CONFIG_PASSPHRASE")
    return v

def pick(*names):
    for n in names:
        v = unwrap(out.get(n))
        if isinstance(v, str) and v:
            return v
    sys.exit("Не нашёл ключ в outputs: " + ", ".join(sorted(out)))

print(pick("accessKey", "access_key"))
print(pick("secretKey", "secret_key"))
PY
)"
AK="$(printf '%s\n' "$CREDS" | sed -n 1p)"
SK="$(printf '%s\n' "$CREDS" | sed -n 2p)"

echo "endpoint: $ENDPOINT"
echo "ключ из стейта: ${AK:0:6}… (access ${#AK} символов, secret ${#SK})"

probe() {  # probe <метод> <регион подписи> <путь>
  : > "$BODY"
  printf 'user = "%s:%s"\n' "$AK" "$SK" | curl -sS -X "$1" -o "$BODY" -w 'HTTP %{http_code}\n' \
    --aws-sigv4 "aws:amz:$2:s3" -K - $([ "$1" = HEAD ] && echo -I) "$ENDPOINT/$3" || true
  head -c 400 "$BODY"; echo
}

echo; echo "== ListBuckets, регион подписи $POOL";  probe GET "$POOL" ""
echo; echo "== ListBuckets, регион подписи ru-1";   probe GET ru-1 ""
echo; echo "== HEAD бакета $BUCKET";               probe HEAD "$POOL" "$BUCKET"
