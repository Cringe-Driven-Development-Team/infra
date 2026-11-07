# GitHubBot Auxiliary Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Развернуть GitHubBot на существующей auxiliary VPS и проверить команды команды CDD в Telegram.

**Architecture:** Роль `hooks` добавляет один контейнер `githubbot` в существующий Compose, отдельный persistent volume и отдельный env-файл. Исходящие Telegram-запросы используют `telegram-proxy:2080`; GitHub идёт напрямую. Код/image поставляет `.github#60`, инфраструктуру — эта sub-issue.

**Tech Stack:** Существующие Ansible/community.docker 5.3.0, Docker Compose v2, GHCR, Ansible Vault; GitHubBot image на Bun 1.4.4.

**Spec:** Канонический документ в repo `.github`: `docs/superpowers/specs/2026-11-07-githubbot-design.md`; текущий файл [спеки](/home/oleg/2026_H2/.github-githubbot-design/docs/superpowers/specs/2026-11-07-githubbot-design.md). План сервиса: `docs/superpowers/plans/2026-11-07-githubbot.md` в том же checkout. Читать оба перед работой. При другом workspace найти командный `.github` по origin, не полагаться на абсолютный путь.

Задача [infra#111](https://github.com/Cringe-Driven-Development-Team/infra/issues/111), родитель [.github#60](https://github.com/Cringe-Driven-Development-Team/.github/issues/60). Локальная разработка/test fixture независима от публикации image; для реального apply digest и credentials обязательны. `Blocked by` ради порядка выкладки не добавлять.

## Global Constraints

- Имя продукта **GitHubBot**, Compose service/image **githubbot**.
- «Код, тесты, контейнер и сборка образа — в `.github`; настройка сервиса, volume, секретов и исходящего Telegram-доступа — в `infra`».
- «Отдельный Telegram bot token и один процесс с long polling»; публичный endpoint и порты не добавлять.
- «SQLite на постоянном volume хранит cursor polling, заявки выбора repo и журнал команд»; volume сохраняется при restart/recreate.
- «Для требуемых коротких команд ответом на GitHub Monitoring у GitHubBot выключается privacy mode»; живой reply проверить, не считать доказанным по документации.
- «Временная недоступность при выкладке допустима»; не добавлять новый VPS, Pulumi-ресурсы, переключение трафика и uptime-условия.
- Секреты только Vault; env mode 0600, secret tasks `no_log: true`, `diff: false`. Не использовать `ansible-vault decrypt` на рабочем файле.
- Все переменные роли с префиксом `hooks_`. В YAML строки условий с `: ` заключать в кавычки; имена tasks с заглавной буквы.
- Реальные Telegram IDs не выводить из скриншота/названия: читать доступные источники и проверять. Отправка сообщений в группу требует явной инструкции пользователя либо выполняется владельцем.

## Review Focus

1. Env содержит `$`, кавычки, переносы и JSON: Compose передаёт точное значение, секрет не раскрывается — Task 1.
2. Volume создан root или пересоздан контейнер: bun может открыть ту же SQLite, выбор repo не теряется — Tasks 1–2.
3. Ошибка image pull/config checker: новый poller не стартует с неверными параметрами — Task 1.
4. Replies к другому боту/несколько получателей Telegram-команд: с отключённым privacy приходит нужный user update и цель задачи — Task 2.
5. Повторный deploy и восстановление queued команды: работает один экземпляр, повторного issue нет — Task 2.

---

## Файлы и контракт с сервисом

| Файл | Изменение |
| --- | --- |
| `ansible/roles/hooks/defaults/main.yml` | `hooks_githubbot_env`: defaults env + vault_githubbot_env. |
| `ansible/roles/hooks/tasks/validate.yml` | Проверка обязательных параметров GitHubBot до изменений на VPS. |
| `ansible/roles/hooks/tasks/main.yml` | Рендер env, pull, config check и подготовка отдельного volume до compose up. |
| `ansible/roles/hooks/templates/githubbot.env.j2` | Формат env как существующий env.j2, но другой словарь. |
| `ansible/roles/hooks/templates/compose.yml.j2` | service githubbot, env_file githubbot.env, volume githubbot_data, proxy network/depends_on. |
| `ansible/roles/hooks/tasks/prepare-githubbot.yml` | Pull + проверка конфига + установка прав volume. |
| `ansible/inventory/group_vars/auxiliary/vars.yml` | Реальный registry digest и source tag после публикации. |
| `ansible/inventory/group_vars/all/vault.yml` | Зашифрованный vault_githubbot_env при доставке. |
| `ansible/tests/test_hooks.py` | Обновление существующих fixture обязательными synthetic GitHubBot параметрами. |
| `ansible/tests/test_githubbot.py` | Локальные проверки роли, env, config checker и persistent volume. |
| `ansible/verify.yml` | Проверка githubbot container health/одного экземпляра на auxiliary. |
| `ansible/README.md`, `RUNBOOK.md` | Настройка, доставка, рестарт, unknown create outcome и приёмка. |

**Image contract:** публичный `ghcr.io/cringe-driven-development-team/githubbot@sha256:<digest>`; `hooks_githubbot_image_source_tag` указывает полный SHA tag для трассировки. В inventory не коммитить фиктивный digest: получить его перед подготовкой mergeable PR. Локальные тесты используют фиксированный synthetic digest.

**Runtime env:** `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`, `GITHUBBOT_CONFIG_JSON` из `vault_githubbot_env`; `DATABASE_PATH=/data/githubbot.sqlite`, `TELEGRAM_PROXY_URL=http://telegram-proxy:2080` из role defaults. JSON содержит `chatId`, `notificationBotIds`, `repositories:[{name,defaultPrefix,prefixes}]`, `topics:[{threadId,repository}]`, `users:[{telegramId,username,githubLogin}]`. Контракт должен совпадать с `loadConfig` сервиса.

**CLI:** working directory в образе `/app/services/githubbot`; `bun run src/main.ts --check-config` возвращает 0 без сети/БД; `--health` возвращает 0 при heartbeat `/data/health.json` не старше 120 секунд. Compose healthcheck вызывает этот CLI, interval 30s, timeout 5s, retries 3, start_period 40s. `restart: unless-stopped`, network `telegram_proxy`, no `ports`, no replicas. GitHub доступен через обычную сеть, proxy задаётся только Telegram fetch.

### Task 1: Ansible-доставка и проверяемый контейнерный контракт

**Files:** Все role/config/test файлы таблицы, кроме зашифрованных живых секретов, RUNBOOK и verify.yml.

**Interfaces:**
- Consumes image/env/CLI contract выше и существующую роль hooks.
- Produces `hooks_githubbot_image:string` (registry digest), `hooks_githubbot_image_source_tag:string`, `hooks_githubbot_env:dict`; отдельные `/opt/hooks/githubbot.env` и Compose volume `hooks_githubbot_data`.
- `prepare-githubbot.yml` выполняется после рендера compose/env до общего compose up; не создаёт второго long-polling процесса.
- Test-local helper в `test_githubbot.py`: `render(variables:dict)->dict` возвращает разобранный compose; `run_tasks(relative_path:str,variables:dict)->subprocess.CompletedProcess` запускает реальные Ansible tasks с fake docker CLI. Использовать подход из `test_hooks.py`, не копировать весь его TestCase. Fixture Docker image для проверки UID/volume берётся локально собранным из `.github`, через `GITHUBBOT_TEST_IMAGE`; тест без live image не заявляет проверку реального Bun.

- [ ] **Step 1: Написать failing tests** рендеринга с синтетическими secrets и image digest.

```python
service = render(variables)['services']['githubbot']
self.assertNotIn('ports', service)
self.assertEqual(service['env_file'], 'githubbot.env')
self.assertEqual(service['volumes'], ['githubbot_data:/data'])
self.assertIn('telegram_proxy', service['networks'])
self.assertEqual(service['restart'], 'unless-stopped')
self.assertEqual(service['healthcheck']['test'],
                 ['CMD', 'bun', 'run', 'src/main.ts', '--health'])
```

Дополнительные tests: отсутствующий token/config/image → preflight fails до template/up; invalid JSON/duplicate user IDs/некорректный topic → отказ config checker; `GITHUB_TOKEN='test-$value-"x"'` и JSON usernames доходят без изменения через Compose env; содержимого токенов нет в stdout/stderr/--diff. При fake docker pull failure не вызываются checker/up. Checker nonzero не вызывает up. В существующие HooksChecks fixture добавить dummy GitHubBot env/digest, чтобы все прежние проверки сохранили смысл.

- [ ] **Step 2:** из infra: `python -m unittest discover -s ansible/tests -p test_githubbot.py -v` → FAIL по отсутствующим production tasks/templates, не по отсутствующему Ansible. Зависимости — существующие `ansible/deploy-requirements.txt` и `requirements.yml`.
- [ ] **Step 3: Реализовать роль.** Валидировать структуру env и `ghcr.io/.../githubbot@sha256:[0-9a-f]{64}` до изменений; `GITHUBBOT_CONFIG_JSON` разбирается безопасно под no_log, окончательная семантическая проверка — CLI нового image. `.env` существующего webhook не менять. `githubbot.env.j2` использует уже существующее экранирование `$` → `$$` и `to_json(ensure_ascii=False)`. Pull через `community.docker.docker_compose_v2_pull services:[githubbot]`; при отказе fail, без fallback на старый/пустой digest.
- [ ] **Step 4: Реализовать prepare-githubbot.** `docker compose run --rm --no-deps --pull never githubbot bun run src/main.ts --check-config` без получения updates; secret task no_log/diff:false. Отдельный короткоживущий запуск с `--user root --entrypoint sh` устанавливает owner bun:bun и mode 0700 для `/data`, не удаляя SQLite/WAL. Затем общий `docker_compose_v2 state:present pull:never wait:true` стартует ровно один service. Не вызывать `compose down -v`.
- [ ] **Step 5: Проверить:** `COMPOSE_CLI='docker compose' python -m unittest discover -s ansible/tests -p 'test_*bot.py' -v`; `COMPOSE_CLI='docker compose' python -m unittest discover -s ansible/tests -p test_hooks.py -v`; из ansible `ansible-playbook -i inventory/ci.yml --syntax-check auxiliary.yml verify.yml`; `ansible-lint roles/hooks auxiliary.yml` → PASS. Дополнительно с `GITHUBBOT_TEST_IMAGE=githubbot:test` подтвердить writable SQLite после root-owned volume и recreate. Прежний `infra.yml` уже запускает discover по tests: отдельный CI workflow не нужен.
- [ ] **Step 6: Commit** только role/tests: `git add ansible/roles/hooks ansible/tests && git commit -m "feat: deploy GitHubBot in auxiliary Compose"`.

### Task 2: Реальные настройки, выкладка и сквозная приёмка

**Files:** Modify auxiliary vars, Vault, verify.yml, README/RUNBOOK; Create `docs/deployments/2026-11-07-githubbot.md` (журнал без секретов).

**Interfaces:** Consumes зелёный service CI, опубликованный digest из .github Task 7 и роль Task 1. Produces работающий один GitHubBot, проверенные repo/topic/user mappings и журнал проверок. Репозиторий/учётные данные для test issues выбирать в согласованном scope; тестовые задачи помечать `GitHubBot smoke` и закрывать после проверки, не менять рабочие задачи.

- [ ] **Step 1: Подготовить проверку verify.yml.** Локальный test запускает task с fake Docker inspect: zero/two containers, stopped и unhealthy → fail; один running+healthy → pass. Добавить `ansible/tests/test_githubbot.py` assertions для каждой формы. Run тест → FAIL, добавить auxiliary task `community.docker.docker_container_info`/inspect Compose service, повторить → PASS. Проверяется настоящая health status, не просто наличие YAML.
- [ ] **Step 2: Собрать runtime inputs.** Проверить digest анонимным pull и OCI source/версию; записать source tag. Найти актуальные group/topic IDs, ID GitHub Monitoring и GitHub→Telegram карту через существующие config/подключённые источники. Для usernames проверить mapping, заполнить реальные allowlists repo+prefix. Dedicated bot token и PAT получить из согласованного secret source; отсутствие этих данных блокирует только live-delivery, не локальные задачи. PAT должен писать Issues и organization Projects. Новые credential values никогда не печатать.
- [ ] **Step 3: Записать Vault и vars.** Через ansible-vault edit добавить `vault_githubbot_env`, не decrypt файла; имена сверить без вывода значений. В auxiliary vars добавить реальные `hooks_githubbot_image` и source tag. Подготовить локальный `--check-config` контейнера с приватным env-файлом, удалить временный файл после проверки. Существующий webhook у отдельного токена — ошибка настройки, не удалять автоматически. Owner отключает privacy mode в BotFather; подтвердить getMe.can_read_all_group_messages и членство в группе.
- [ ] **Step 4: Подготовить PR и проверку apply.** Infra PR: `Closes #111`, ссылка на кодовый PR и опубликованный digest. Кодовый PR: `Part of Cringe-Driven-Development-Team/.github#60`. Проверить обязательные CI/отсутствие конфликтов по текущему head. Мерж/деплой выполняется только в рамках выданного поручения на реализацию/выкладку; сам этот план не запускает CI production. Использовать штатный infra workflow, не повторять Pulumi вручную ради контейнера.
- [ ] **Step 5: После доставки проверить read-only состояние.** Один контейнер healthy; env 0600, volume writable, портов нет, GitHubBot getUpdates не конфликтует с другим poller. `ansible-playbook -i inventory/openstack.yml verify.yml --limit auxiliary` выполняет auxiliary host check (controller-level play отдельно по существующей инструкции). Записать digest, CI URLs и результаты без секретов.
- [ ] **Step 6: Выполнить живой набор приёмки вместе с человеком.** Если нет явного разрешения отправлять сообщения в группу, владелец отправляет команды, агент читает результаты/API. Последовательно: `/issue GitHubBot smoke` в Backend/Frontend/UX → правильные repo/Backlog/empty Sprint; в другом топике → buttons, чужой callback отказ, owner выбор создаёт один issue. Reply `/assignme`, `/assign @username`, `/unassign @username`, `/tosprint`, `/close` на карточку бота и GitHub Monitoring; повторить с URL. `/sprint` и `/sprint @username` сравнить с API; закрытые и unassigned не пропадают. Для настоящего cross-bot reply сохранить обезличенный fixture в тесты сервиса и запустить соответствующий regression test.
- [ ] **Step 7: Проверить сохранность при recreate.** Создать waiting_repo заявку, пересоздать только githubbot через штатную роль, завершить выбор и проверить один issue. Повторный callback не создаёт второй; SQLite тот же volume. Сценарий неопределённого POST проверяется локальным fault injection из service tests, не искусственным сбоем боевого GitHub. В журнале отдельно отметить, что было live, а что fixture.
- [ ] **Step 8: Обновить RUNBOOK/README и commit.** Описать получение IDs, privacy, env, image update, health, restart без удаления volume, stale username и ручную сверку uncertain create. `git diff --check`, full Ansible tests и syntax check → PASS; `git add ansible/inventory ansible/verify.yml ansible/tests ansible/README.md RUNBOOK.md docs/deployments && git commit -m "docs: verify GitHubBot deployment and Telegram commands"`. Secrets в staging должны оставаться Vault ciphertext.

## Завершение и self-review

- После общей приёмки #111 закрывается своим PR, #60 — по командному процессу после готовности обеих частей. Закрытие sub-issue не доказывает готовность parent.
- Покрытие: env/security/persistence/network/container → Task 1; settings/live reply/CI/recreate/runbook → Task 2. Review Focus имеет соответствующие проверки. Продуктовая логика и её unit tests не дублируются в infra.
- Контракт env/CLI/image согласован с сервисным планом; IDs и secrets — runtime inputs, не неизвестные решения дизайна.
- Self-review выполнен автором плана, без отдельного агента. Никаких live-команд, secret edits или deployment при написании плана не выполнялось.
