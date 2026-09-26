#!/usr/bin/env bash
# Тесты env.sh: разбор ~/.config/selectel.env и переменные OS_* для провайдера Selectel.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Переменные, которые выставляет env.sh, не должны приходить из вызывающей оболочки.
unset SELECTEL_ENV SELECTEL_USERNAME SELECTEL_PASSWORD SELECTEL_DOMAIN_NAME PULUMI_CONFIG_PASSPHRASE \
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_CONFIG_FILE AWS_SHARED_CREDENTIALS_FILE \
  OS_USERNAME OS_PASSWORD OS_DOMAIN_NAME OS_AUTH_URL OS_REGION_NAME
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
FAILS=0
fail() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

cat > "$TMP/ok.env" <<'EOF'
SELECTEL_USERNAME=pulumi-test
SELECTEL_PASSWORD=p w;$HOME&|"x'
SELECTEL_DOMAIN_NAME=631994
PULUMI_CONFIG_PASSPHRASE=pass phrase
EOF

# $1 — оболочка, $2 — env-файл; печатает значения через разделитель \x1f
show() {
  SELECTEL_ENV=$2 "$1" -c '. "$0" && printf "%s\037%s\037%s\037%s\037%s\037%s" \
    "$OS_USERNAME" "$OS_PASSWORD" "$OS_DOMAIN_NAME" "$OS_AUTH_URL" "$OS_REGION_NAME" "$PULUMI_CONFIG_PASSPHRASE"' \
    "$HERE/env.sh"
}

expected=$(printf '%s\037%s\037%s\037%s\037%s\037%s' pulumi-test 'p w;$HOME&|"x'"'" 631994 \
  https://cloud.api.selcloud.ru/identity/v3/ ru-7 'pass phrase')

test_special_chars_preserved() {
  [ "$(show bash "$TMP/ok.env" 2>/dev/null)" = "$expected" ] || fail "bash: значения искажены"
}

test_posix_sh() {
  [ "$(show sh "$TMP/ok.env" 2>/dev/null)" = "$expected" ] || fail "sh: значения искажены"
}

test_missing_file_keeps_shell() {
  local out
  out=$(SELECTEL_ENV=$TMP/nope.env bash -c '. "$0"; echo "rc=$?"; echo alive' "$HERE/env.sh" 2>&1)
  grep -q 'nope.env' <<<"$out" || fail "в ошибке нет пути к файлу: $out"
  grep -q 'rc=1' <<<"$out" || fail "env.sh должен вернуть 1: $out"
  grep -q 'alive' <<<"$out" || fail "env.sh завершил вызывающую оболочку: $out"
}

test_missing_passphrase_warns() {
  grep -v PULUMI_CONFIG_PASSPHRASE "$TMP/ok.env" > "$TMP/nopass.env"
  local out
  out=$(SELECTEL_ENV=$TMP/nopass.env bash -c '. "$0"; echo "user=$OS_USERNAME"' "$HERE/env.sh" 2>&1)
  grep -q 'PULUMI_CONFIG_PASSPHRASE' <<<"$out" || fail "нет предупреждения о passphrase: $out"
  grep -q 'user=pulumi-test' <<<"$out" || fail "OS_USERNAME не выставлен: $out"
}

test_ignores_comments_and_unknown_keys() {
  { echo '# comment'; echo ''; echo 'OTHER=1'; cat "$TMP/ok.env"; } > "$TMP/extra.env"
  [ "$(show bash "$TMP/extra.env" 2>/dev/null)" = "$expected" ] || fail "комментарии/чужие ключи мешают"
  [ -z "$(SELECTEL_ENV=$TMP/extra.env bash -c '. "$0"; printf %s "${OTHER:-}"' "$HERE/env.sh")" ] ||
    fail "чужой ключ OTHER экспортирован"
}

test_isolates_personal_aws_config() {
  local out
  out=$(SELECTEL_ENV=$TMP/ok.env bash -c '. "$0"; printf "%s|%s" "$AWS_CONFIG_FILE" "$AWS_SHARED_CREDENTIALS_FILE"' "$HERE/env.sh" 2>/dev/null)
  [ "$out" = "/dev/null|/dev/null" ] || fail "личный ~/.aws не изолирован: '$out'"
}

test_exports_personal_state_key() {
  { cat "$TMP/ok.env"; echo 'AWS_ACCESS_KEY_ID=ak32'; echo 'AWS_SECRET_ACCESS_KEY=sk$32'; } > "$TMP/key.env"
  local out
  out=$(SELECTEL_ENV=$TMP/key.env bash -c '. "$0"; printf "%s|%s" "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY"' "$HERE/env.sh" 2>/dev/null)
  [ "$out" = 'ak32|sk$32' ] || fail "ключ стейта не экспортирован: '$out'"
}

for t in $(declare -F | awk '$3 ~ /^test_/ {print $3}'); do
  echo "$t"
  "$t"
done
echo
if [ "$FAILS" -gt 0 ]; then echo "провалов: $FAILS"; exit 1; fi
echo "все тесты прошли"
