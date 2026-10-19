#!/usr/bin/env bash
# Тесты devops-skills-session.sh: хук SessionStart, который в каждой сессии велит вызвать devops-скиллы.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FAILS=0
fail() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# $1 — источник сессии (startup, resume, clear, compact); печатает вывод хука
run() {
  printf '{"session_id":"0a1b2c","cwd":"/work/infra","hook_event_name":"SessionStart","source":"%s"}' \
    "${1:-startup}" | bash "$HERE/devops-skills-session.sh"
}

test_names_all_devops_skills() {
  local out s; out=$(run)
  for s in selectel-ops pulumi-typescript pulumi-cli ansible-org; do
    grep -q "$s" <<<"$out" || fail "нет $s: $out"
  done
}

test_every_source_gets_the_same_output() {
  local first s; first=$(run startup)
  for s in resume clear compact; do
    [ "$(run "$s")" = "$first" ] || fail "источник $s дал другой вывод"
  done
}

test_output_is_hook_json() {
  local out; out=$(run)
  grep -q '"hookEventName":"SessionStart"' <<<"$out" || fail "нет hookEventName: $out"
  grep -q '"additionalContext":"' <<<"$out" || fail "нет additionalContext: $out"
  if command -v jq >/dev/null 2>&1; then
    jq -e '.hookSpecificOutput.additionalContext | length > 0' <<<"$out" >/dev/null || fail "вывод не JSON: $out"
  fi
}

test_points_to_existing_file() {
  local out; out=$(run)
  grep -q 'SUMMARY.md' <<<"$out" || fail "нет ссылки на SUMMARY.md: $out"
  [ -f "$HERE/../../SUMMARY.md" ] || fail "SUMMARY.md нет в корне репозитория"
  grep -q '^## Скиллы' "$HERE/../../SUMMARY.md" || fail "в SUMMARY.md нет раздела «Скиллы»"
}

test_always_exits_zero() {
  run >/dev/null; [ $? -eq 0 ] || fail "код не 0"
  printf 'это не json' | bash "$HERE/devops-skills-session.sh" >/dev/null; [ $? -eq 0 ] || fail "код не 0 на битом вводе"
  bash "$HERE/devops-skills-session.sh" </dev/null >/dev/null; [ $? -eq 0 ] || fail "код не 0 на пустом вводе"
}

for t in $(declare -F | awk '$3 ~ /^test_/ {print $3}'); do
  echo "$t"
  "$t"
done
echo
if [ "$FAILS" -gt 0 ]; then echo "провалов: $FAILS"; exit 1; fi
echo "все тесты прошли"
