# GitHubBot: общий Telegram bot и реальные runtime inputs

## Решение владельца и границы

2026-11-08 владелец выбрал один Telegram bot для GitHub Monitoring и команд GitHubBot. Это заменяет предложение отдельного bot token в спеке и плане от 2026-11-07. Отдельными остаются сервисы и SQLite; только GitHubBot получает updates, webhook-alerts отправляет уведомления. Права runtime PAT: Issues и organization Projects read/write. Исполнение — ранее выбранный subagent-driven-development.

## Global Constraints

- Обязательная проверка GitHubBot и зависимость успешного infra CI от неё сохраняются.
- Единственный источник Telegram-токена — действующий hooks_env.TELEGRAM_BOT_TOKEN, сформированный из существующего Vault. vault_githubbot_env хранит только GITHUB_TOKEN и GITHUBBOT_CONFIG_JSON.
- Никаких plaintext secrets, реальных карт пользователей или полного Telegram payload в git/логах. Vault изменяется только зашифрованно; исходный файл не decrypt-in-place.
- Только один polling процесс; чужой webhook не удалять. Не отправлять команды/сообщения в Telegram без отдельного поручения.
- Не менять чужой PR116. Новый infra PR основан на main. Merge/deploy нового infra PR требует конкретного согласования после CI.

## Task 1: Общий Telegram credential и документация

Worktree: /home/oleg/2026_H2/.infra-githubbot-plan, branch fix/githubbot-runtime-inputs-20261108.

Изменить ansible/roles/hooks/defaults/main.yml: effective hooks_githubbot_env получает TELEGRAM_BOT_TOKEN из effective hooks_env, после объединения vault_githubbot_env, чтобы локальный override отдельного Telegram-токена не расходился с отправщиком уведомлений. Пустой общий токен должен проваливать существующий обязательный preflight. Остальные обязательные значения и постоянные пути сохранить.

Обновить соответствующие fixtures/callers в ansible/tests/test_hooks.py и test_githubbot.py. Регрессионный RED/GREEN проверяет общий токен, единый источник при conflicting legacy value и fail-closed при отсутствии общего токена. Не добавлять режим совместимости или флаг отключения. Выполнить focused tests, затем существующие Ansible tests/syntax/lint по проектным командам один раз.

Обновить ansible/README.md, RUNBOOK.md (включая offline config script, который должен брать token из vault_hooks_env), и добавить датированное уточнение в старый план deployment. Исторический журнал не переписывать как будто новые операции уже выполнены. Отдельный PAT сохраняется, отдельный Telegram bot больше не требуется. Не трогать реальные credentials, Vault или image pin в этой задаче. Self-review, commit, report; не push.

## Task 2: Реальный Vault, опубликованный digest, проверка

После успешного publisher для merge SHA 1c32d896059fd93a83c0b6f4622e2089d627cac0 контроллер передаёт исполнителю verified digest и source tag. В inventory/group_vars/auxiliary/vars.yml закрепить именно их. В encrypted inventory/group_vars/all/vault.yml добавить vault_githubbot_env с GITHUB_TOKEN из приватного /home/oleg/.config/cdd-githubbot.env и однострочным GITHUBBOT_CONFIG_JSON из /home/oleg/.config/cdd-githubbot-config.json. TELEGRAM_BOT_TOKEN в локальном input-файле устарел; НЕ использовать его. Токен Telegram берётся только из существующего Vault через role defaults.

Использовать ansible-vault edit с безопасным editor script либо эквивалентную in-memory decrypt/encrypt без plaintext output; не сохранять decrypted Vault в репозитории. До/после сравнить parsed mappings в памяти: все существующие значения обязаны сохраниться, новый namespace содержит только два указанных ключа. Сообщать только booleans/counts, не значения.

Проверить anonymous pull закреплённого образа, registry digest/source tag/OCI source; offline --check-config с реальными resolved env и --network none, временные env-файлы0600 удалить. Обновить docs/deployments/2026-11-07-githubbot.md датированным фактическим состоянием и ограничениями. Self-review, commit, report; не push/merge/deploy. Контроллер делает task review и final review, PR, проверяет actual CI. Реальная отправка команд в группу не входит.
