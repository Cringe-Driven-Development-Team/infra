#!/usr/bin/env bash
# Тесты devops-skills.sh: хук UserPromptSubmit, который по словам запроса напоминает про devops-скиллы.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FAILS=0
fail() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

# $1 — текст запроса, $2 — cwd сессии (необязательно); печатает вывод хука
run() {
  printf '{"session_id":"0a1b2c","transcript_path":"/home/u/.claude/projects/-work-infra-pulumi/t.jsonl","cwd":"%s","permission_mode":"default","hook_event_name":"UserPromptSubmit","prompt":"%s"}' \
    "${2:-/work/infra}" "$1" | bash "$HERE/devops-skills.sh"
}

test_pulumi_prompt_names_pulumi_skills() {
  local out; out=$(run 'запусти pulumi preview')
  grep -q 'pulumi-typescript' <<<"$out" || fail "нет pulumi-typescript: $out"
  grep -q 'pulumi-cli' <<<"$out" || fail "нет pulumi-cli: $out"
  grep -q 'selectel-ops' <<<"$out" && fail "лишний selectel-ops: $out"
}

test_bucket_prompt_names_selectel_skill() {
  local out; out=$(run 'проверь политику бакета ноутбуков')
  grep -q 'selectel-ops' <<<"$out" || fail "нет selectel-ops: $out"
}

test_capitalized_cyrillic_matches() {
  local out; out=$(run 'Бакет аватарок недоступен')
  grep -q 'selectel-ops' <<<"$out" || fail "заглавная буква не распознана: $out"
}

test_ansible_prompt_names_ansible_skill() {
  local out; out=$(run 'добавь ключ в vault')
  grep -q 'ansible-org' <<<"$out" || fail "нет ansible-org: $out"
}

test_several_topics_name_all_skills() {
  local out; out=$(run 'после pulumi up прогони ansible-playbook')
  grep -q 'pulumi-cli' <<<"$out" || fail "нет pulumi-cli: $out"
  grep -q 'ansible-org' <<<"$out" || fail "нет ansible-org: $out"
}

test_output_is_hook_json() {
  local out; out=$(run 'selectel dns')
  grep -q '"hookEventName":"UserPromptSubmit"' <<<"$out" || fail "нет hookEventName: $out"
  grep -q '"additionalContext":"' <<<"$out" || fail "нет additionalContext: $out"
  if command -v jq >/dev/null 2>&1; then
    jq -e '.hookSpecificOutput.additionalContext | length > 0' <<<"$out" >/dev/null || fail "вывод не JSON: $out"
  fi
}

test_unrelated_prompt_is_silent() {
  local out; out=$(run 'поправь опечатку в README')
  [ -z "$out" ] || fail "хук сработал на посторонний запрос: $out"
}

test_session_path_does_not_trigger() {
  local out; out=$(run 'поправь опечатку в README' '/work/infra/pulumi')
  [ -z "$out" ] || fail "хук сработал на путь каталога: $out"
}

test_windows_path_does_not_trigger() {
  local out; out=$(run 'поправь опечатку в README' 'F:\\\\Github\\\\infra\\\\ansible')
  [ -z "$out" ] || fail "хук сработал на путь Windows: $out"
}

test_c_locale_still_matches() {
  local out; out=$(LC_ALL=C run 'проверь бакет')
  grep -q 'selectel-ops' <<<"$out" || fail "в локали C кириллица не распознана: $out"
}

test_always_exits_zero() {
  run 'pulumi' >/dev/null; [ $? -eq 0 ] || fail "код не 0 на запросе по теме"
  run 'привет' >/dev/null; [ $? -eq 0 ] || fail "код не 0 на постороннем запросе"
  printf 'это не json' | bash "$HERE/devops-skills.sh" >/dev/null; [ $? -eq 0 ] || fail "код не 0 на битом вводе"
  bash "$HERE/devops-skills.sh" </dev/null >/dev/null; [ $? -eq 0 ] || fail "код не 0 на пустом вводе"
}

test_broken_input_is_silent() {
  local out; out=$(printf 'это не json' | bash "$HERE/devops-skills.sh")
  [ -z "$out" ] || fail "хук что-то напечатал на битый ввод: $out"
}

for t in $(declare -F | awk '$3 ~ /^test_/ {print $3}'); do
  echo "$t"
  "$t"
done
echo
if [ "$FAILS" -gt 0 ]; then echo "провалов: $FAILS"; exit 1; fi
echo "все тесты прошли"
