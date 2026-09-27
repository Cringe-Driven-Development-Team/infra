#!/usr/bin/env bash
# Тесты init-env.sh: создание ~/.config/selectel.env из скрытого ввода.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
FAILS=0
fail() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

test_creates_file_and_dir() {
  local f=$TMP/new/dir/selectel.env
  printf '%s\n%s\n' 'p w;$x&"' 'pass phrase' | SELECTEL_ENV=$f bash "$HERE/init-env.sh" pulumi-test >/dev/null 2>&1 || fail "скрипт упал"
  [ -f "$f" ] || { fail "файл не создан"; return; }
  [ "$(stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f")" = 600 ] || fail "права не 600"
  [ "$(cat "$f")" = "$(printf 'SELECTEL_USERNAME=pulumi-test\nSELECTEL_PASSWORD=p w;$x&"\nSELECTEL_DOMAIN_NAME=631994\nPULUMI_CONFIG_PASSPHRASE=pass phrase')" ] || fail "содержимое: $(cat "$f")"
}

test_refuses_to_overwrite() {
  local f=$TMP/exists.env
  echo 'AWS_ACCESS_KEY_ID=keep' > "$f"
  if printf 'a\nb\n' | SELECTEL_ENV=$f bash "$HERE/init-env.sh" u >/dev/null 2>&1; then fail "перезаписал существующий файл"; fi
  grep -q 'AWS_ACCESS_KEY_ID=keep' "$f" || fail "существующий файл испорчен"
}

test_requires_username() {
  if printf 'a\nb\n' | SELECTEL_ENV=$TMP/x.env bash "$HERE/init-env.sh" >/dev/null 2>&1; then fail "без имени пользователя должен упасть"; fi
  [ ! -e "$TMP/x.env" ] || fail "файл создан без имени пользователя"
}

test_empty_password_rejected() {
  if printf '\npass\n' | SELECTEL_ENV=$TMP/e.env bash "$HERE/init-env.sh" u >/dev/null 2>&1; then fail "пустой пароль принят"; fi
  [ ! -e "$TMP/e.env" ] || fail "файл создан с пустым паролем"
}

for t in $(declare -F | awk '$3 ~ /^test_/ {print $3}'); do
  echo "$t"
  "$t"
done
echo
if [ "$FAILS" -gt 0 ]; then echo "провалов: $FAILS"; exit 1; fi
echo "все тесты прошли"
