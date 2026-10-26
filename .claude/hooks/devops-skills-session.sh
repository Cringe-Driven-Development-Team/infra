#!/usr/bin/env bash
# Хук SessionStart (.claude/settings.json): в каждой сессии из этого репозитория добавляет в контекст
# указание вызвать devops-скиллы маркетплейса cdd — запрос может не содержать слов, по которым их
# напоминает devops-skills.sh (ссылка на задачу, «продолжай»). Вызвать скилл сам хук не может.
# Ввод не читается, вывод один для любого источника сессии. Код возврата всегда 0.
# Тесты: bash .claude/hooks/devops-skills-session_test.sh
printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Это репозиторий инфраструктуры. До первой команды или правки вызови скиллы: selectel-ops, pulumi-typescript, pulumi-cli, ansible-org — даже если в запросе нет слов Pulumi, Selectel или Ansible. Какой скилл для чего — раздел «Скиллы» в SUMMARY.md."}}\n'
exit 0
