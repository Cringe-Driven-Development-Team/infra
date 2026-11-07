# GitHubBot: журнал подготовки и приёмки 2026-11-07

**Статус: выполнена локальная подготовка; публикация, выкладка и живая приёмка не выполнены.**
Задачи: [infra#111](https://github.com/Cringe-Driven-Development-Team/infra/issues/111),
[.github#60](https://github.com/Cringe-Driven-Development-Team/.github/issues/60).
Ни закрытие задачи, ни готовность production этим журналом не подтверждаются.

## Артефакты и доступные inputs

| Параметр | Фактический результат |
| --- | --- |
| Service source для локального image | Коммит `6f8adfb` в `.github`; прочитан контракт README/CLI, код сервиса здесь не менялся |
| Локальный image | `githubbot:test`, ID `sha256:df1862818314055c03b1eda2b1566ea69387db3bf9c4626912027d5bad8db844`; это **не registry digest** |
| Production registry digest / source tag | Не получены и не записаны; никакого фиктивного значения в inventory |
| Публикация и CI URLs | Не выполнялись этим заданием; ссылки на успешные checks/publisher/apply должен добавить владелец после запуска |
| Vault | Шифротекст сохранён без изменений; пароль доступен локально, read-only парсинг подтвердил отсутствие `vault_githubbot_env` |
| Telegram config | Org `TELEGRAM_CHAT_ID`, `TELEGRAM_USER_MAP` доступны; группа/топики/участники прочитаны через Telegram metadata, без истории сообщений |
| Topics и users | Найдены Backend/Frontend/UX и GitHub Monitoring; все 6 записей GitHub→Telegram ID сопоставлены с текущими usernames участников; профиль notification bot подтверждает `is_bot` |
| Секреты GitHubBot | Dedicated bot token и отдельный runtime PAT в согласованном источнике не найдены; токен уведомлений не переиспользован |
| Существующие Actions secrets | Видны только имена `TELEGRAM_BOT_TOKEN` и `ADD_TO_PROJECT_PAT`, значения GitHub обратно не выдаёт; это не готовые runtime credentials GitHubBot |
| SSH readiness | Настроенный deploy key и запись auxiliary в known_hosts существуют; подключение и доверие ключа сервера заново не проверялись |

Реальные chat/user/bot IDs и карта usernames в этот публичный журнал не включены.
Подготовленная карта хранится вне Git в закрытом локальном файле; перед переносом
в Vault владелец сверяет scope пользователей и dedicated bot identity.
Наличие локального password/key-файла не доказывает доступ к боевому стенду.

## Выполненные локальные проверки

- Реальные tasks `verify.yml` выполняются на локальном Ansible с fake Docker CLI:
  zero/two containers, stopped, unhealthy и missing health завершаются ошибкой;
  один running+healthy проходит. Проверяется Docker inspect, включая `Running`
  и `Health.Status`, а не только содержимое Compose YAML.
- Собранный по прочитанным источникам JSON прошёл `--check-config` реального
  `githubbot:test` с synthetic tokens и `--network none`; ни polling, ни API
  с настоящими credentials не запускались.
- `GITHUBBOT_TEST_IMAGE=githubbot:test COMPOSE_CLI='docker compose' /home/oleg/.local/share/uv/tools/ansible-dev-tools/bin/python -m unittest discover -s ansible/tests -v`:
  **45 tests, OK (skipped=2)**, 84.997 с. Старые skips: Restic не установлен;
  `WEBHOOK_ALERTS_TEST_IMAGE` не задан. GitHubBot real-image test выполнен.
- Из `ansible/`: `ansible-playbook -i inventory/ci.yml --syntax-check site.yml verify.yml deploy-backend.yml deploy-bff.yml auxiliary.yml`:
  exit 0. Существующее deprecation warning `apt_repository` в роли Docker.
- Из `ansible/`: `ansible-lint auxiliary.yml roles/hooks roles/restic_backup tasks/verify-hooks-snapshot.yml verify.yml`:
  exit 0, production profile, 34 файла, 0 failures / 1 прежнее warning
  `args[module]` для `item.policy` в роли firewall; также обычное directory-expansion сообщение.
- `git diff --check`: exit 0. Это локальная проверка, live readiness не проверялась.

## Ожидающие шаги плана

| Шаг | Не выполнено / prerequisite | Ответственный и следующий шаг |
| --- | --- | --- |
| 2: image | Нужны public publication, remote manifest digest, source SHA и зелёные CI URLs | Владелец сервиса: довести service PR/publisher, проверить anonymous pull и OCI labels по [RUNBOOK](../../RUNBOOK.md#githubbot-на-auxiliary) |
| 2: credentials | Нужен отдельный BotFather token, runtime PAT Issues/Projects read/write; existing user map требует owner review | Владелец GitHubBot: согласовать сервисную учётку/доступ, получить dedicated credentials через закрытый источник |
| 3: Vault/vars/privacy | Vault и inventory не менялись; реальная проверка getMe/privacy/membership/webhook не выполнялась | Владелец GitHubBot/infra: `ansible-vault edit inventory/group_vars/all/vault.yml`, реальные image/source vars, offline gate из RUNBOOK, BotFather Disable privacy; webhook не удалять автоматически |
| 4: PR/apply | PR, публикация, merge, workflow dispatch и live apply вне локального поручения | Infra maintainer: PR с `Closes #111`, service PR/CI/digest; проверить текущий head/checks/conflicts, после разрешения штатный `infra.yml` |
| 5: host state | Реальное число контейнеров, health, env 0600, writable volume, отсутствие ports и конфликта poller неизвестны | Infra maintainer после apply: `ansible-playbook -i inventory/openstack.yml verify.yml --limit auxiliary`, отдельно `--limit localhost`; host-команды из RUNBOOK |
| 6: Telegram/Issues/Projects | Ни smoke-команд, ни GitHub мутаций, ни настоящего cross-bot reply fixture | Владелец отправляет команды либо явно поручает отправку; выполнить набор ниже и обезличенный fixture/regression test в service repo |
| 7: recreate/dedupe | Живая waiting_repo заявка и выбор после recreate не проверялись | Владелец/infra maintainer: только githubbot container remove без volume удаления, штатная роль, старый callback и повторный callback; записать issue URL и mount |

Команды Ansible выполняются из `ansible/` с настроенными inventory и Vault,
после отдельного разрешения на живые действия. Приведённые команды — процедура,
а не свидетельство выполненной выкатки. Ручной повтор Pulumi ради контейнера не нужен.

## Живой acceptance checklist — всё ожидает выполнения

| Проверка | Результат и свидетельство |
| --- | --- |
| getMe privacy, membership, empty webhook, единственный poller | Не выполнялась |
| Один healthy, env 0600, writable DB, без ports; digest/OCI SHA | Не выполнялась |
| `/issue GitHubBot smoke` в Backend / Frontend / UX → repo, prefix, no-assignee, Backlog, empty Sprint | Не выполнялась |
| Другой topic → buttons; чужой callback отказ; автор → ровно один issue | Не выполнялась |
| `/assignme`, `/assign @username`, `/unassign @username`, `/tosprint`, `/close` reply к GitHubBot | Не выполнялась |
| Те же пять команд reply к настоящему GitHub Monitoring; обезличенный fixture + regression test | Не выполнялась |
| Те же команды с явным issue URL | Не выполнялась |
| `/sprint`, `/sprint @username` сверены с GitHub API; closed и unassigned сохранены | Не выполнялась |
| waiting_repo переживает recreate на `hooks_githubbot_data`; повторный callback без второго issue | Не выполнялась |
| Все smoke issues закрыты, рабочие задачи не менялись | Smoke issues не создавались |

Uncertain POST моделируется локальными fault-injection тестами сервиса; здесь
боевые сбои GitHub не создавались и это не считается живой приёмкой. Локальный
infra real-image test отдельно проверяет сохранность SQLite/WAL после recreate,
но не является свидетельством настоящего Telegram callback или доставки reply.
