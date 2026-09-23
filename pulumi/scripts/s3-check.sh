#!/usr/bin/env bash
# Проверяет S3-ключи стека напрямую, без awscli: curl сам подписывает запрос (SigV4)
# и ходит через системное доверие macOS.
#   PULUMI_CONFIG_PASSPHRASE=... ./scripts/s3-check.sh [stack]
set -euo pipefail
cd "$(dirname "$0")/.."

STACK="${1:-$(pulumi stack --show-name)}"
POOL="$(pulumi config get infra:s3Pool -s "$STACK")"
BUCKET="$(pulumi config get infra:s3Bucket -s "$STACK")"
ENDPOINT="https://s3.${POOL}.storage.selcloud.ru"

STATE="$(mktemp)"
trap 'rm -f "$STATE"' EXIT
pulumi stack export --show-secrets -s "$STACK" > "$STATE"

eval "$(python3 - "$STATE" <<'PY'
import json, sys
res = json.load(open(sys.argv[1]))["deployment"]["resources"]
cred = next((r for r in res if r["type"].endswith("IamS3CredentialsV1")), None)
if not cred:
    sys.exit("В стейте нет IamS3CredentialsV1")
out = cred.get("outputs", {})
def pick(*names):
    for n in names:
        v = out.get(n)
        if isinstance(v, str) and v:
            return v
    sys.exit("Не нашёл ключ в outputs: " + ", ".join(sorted(out)))
print("AK=%s" % pick("accessKey", "access_key"))
print("SK=%s" % pick("secretKey", "secret_key"))
PY
)"

echo "endpoint: $ENDPOINT"
echo "ключ: ${AK:0:6}… (access ${#AK} символов, secret ${#SK})"

for REGION in "$POOL" ru-1; do
  echo
  echo "== ListBuckets, регион подписи $REGION"
  curl -sS -o /tmp/s3check.out -w 'HTTP %{http_code}\n' \
    --aws-sigv4 "aws:amz:${REGION}:s3" --user "$AK:$SK" "$ENDPOINT/"
  head -c 400 /tmp/s3check.out; echo
done

echo
echo "== CreateBucket $BUCKET, регион подписи $POOL"
curl -sS -X PUT -o /tmp/s3check.out -w 'HTTP %{http_code}\n' \
  --aws-sigv4 "aws:amz:${POOL}:s3" --user "$AK:$SK" "$ENDPOINT/$BUCKET"
head -c 400 /tmp/s3check.out; echo
rm -f /tmp/s3check.out
