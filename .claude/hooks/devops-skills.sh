#!/usr/bin/env bash
# Хук UserPromptSubmit (.claude/settings.json): если запрос про Selectel, Pulumi или Ansible, добавляет
# в контекст напоминание вызвать devops-скиллы маркетплейса cdd. Запрос не по теме — молчит.
# Только bash, sed и grep: jq и python есть не на каждой машине (Git Bash). Код возврата всегда 0 —
# хук не должен блокировать запрос. Тесты: bash .claude/hooks/devops-skills_test.sh
input=$(cat 2>/dev/null)

# Поля с путями и служебные поля вырезаются: каталог сессии может называться …/infra/pulumi.
text=$(printf '%s' "$input" | sed -E \
  's/"(cwd|transcript_path|session_id|hook_event_name|permission_mode)"[[:space:]]*:[[:space:]]*"[^"]*"//g' 2>/dev/null)

# Кириллица — в обоих регистрах явно: grep -i в локали C её не сворачивает.
has() { printf '%s' "$text" | grep -qiE -- "$1" 2>/dev/null; }

skills=""
add() { skills="${skills:+$skills, }$1"; }
has 'selectel|selcloud|selstorage|openstack|s3|bucket|cdn|dns|[Сс]електел|[Бб]акет' && add 'selectel-ops'
has 'pulumi|[Сс]тейт|[Сс]тек' && add 'pulumi-typescript, pulumi-cli'
has 'ansible|playbook|vault|inventory|caddy|[Аа]нсибл|[Пп]лейбук' && add 'ansible-org'

if [ -n "$skills" ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Запрос касается инфраструктуры. До первой команды или правки по теме вызови скиллы: %s. Какой скилл для чего — раздел «Скиллы» в CLAUDE.md."}}\n' "$skills"
fi
exit 0
