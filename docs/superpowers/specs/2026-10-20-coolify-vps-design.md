# Отдельная VPS с Coolify для сервиса вебхуков — дизайн

Задача: [infra#54](https://github.com/Cringe-Driven-Development-Team/infra/issues/54). Дата: 2026-10-20.

## Зачем

Сервису уведомлений GitHub/Apidog → Telegram (TypeScript + Bun, код —
[.github#20](https://github.com/Cringe-Driven-Development-Team/.github/issues/20)) нужна площадка с
публичным HTTPS-адресом, отдельная от прод-стенда `cellestial.ru`. Деплоить сервис будет его автор
сам — через self-hosted Coolify, не трогая Ansible прода. Coolify — решение команды, не обсуждается.

На машине живёт **только этот небольшой сервис**. Стенды веток и стейджинг
([infra#48](https://github.com/Cringe-Driven-Development-Team/infra/issues/48)) под неё не
закладываем: ресурсов «на вырост» не берём.

## Цели

- Отдельная VPS с Coolify, поднимаемая Pulumi и Ansible воспроизводимо, без замены и переноса
  действующей прод-VPS и её ресурсов.
- Панель Coolify и сервис вебхуков — по HTTPS на своих доменах; снаружи открыты только 22, 80, 443.
- Данные Coolify и постоянное хранилище сервиса переживают пересоздание контейнеров, есть
  резервная копия и проверенное восстановление.
- Инструкция автору сервиса: как деплоить через Coolify, где секреты, как откатить.

## Не цели

- Код сервиса вебхуков, форматирование уведомлений (.github#20).
- Multi-branch стенды и стейджинг (infra#48), Go/Postgres на второй VPS (infra#59).
- Высокая доступность, мониторинг/алерты сверх health check.

## Анти-цели (провал, даже если «работает»)

- `pulumi up` трогает (заменяет, пересоздаёт) прод-VPS, её диск, floating IP, сеть или DNS прода.
- Панель Coolify хоть какое-то время доступна снаружи на `:8000` (или на любом порту, кроме 80/443
  через proxy) — в том числе на время первой настройки, когда регистрация администратора открыта.
- Прогон `site.yml` ставит Caddy/приложение прода на новую машину, или прогон плейбука Coolify
  меняет прод-VPS.
- Секреты сервиса (токен бота, webhook secret, ключ restic, S3-ключ бэкапа) попадают в Git, логи CI
  или выводы `pulumi`.

## Ограничения (из репозитория)

- Pulumi: стек `prod`, программа `pulumi/index.ts`, провайдеры `openstack` (сервер, сеть) и
  `selectel` (DNS через провайдер `dns` в проекте `infra:dnsProjectId`). Логические имена
  существующих ресурсов не менять.
- Ansible: dynamic inventory `inventory/openstack.yml` группирует хосты по `metadata.role`; сейчас
  `site.yml` и второй play `verify.yml` идут по `hosts: all`, а `verify.yml` требует ровно один хост
  в inventory.
- CI/CD — `.github/workflows/infra.yml`: на PR preview и `site.yml --check`, после мержа в `main` —
  `pulumi up`, `site.yml`, `verify.yml` по статическому `inventory/ci.yml` ключом `ci-infra`.
- Docker публикует порты в обход ufw (см. `ansible/README.md`, «Порты контейнеров и ufw»).
- Root-вход по SSH запрещён ролью `ssh_hardening`.

## Решения

### Инфраструктура (Pulumi) — решение агента, согласовано

- Тот же стек `prod`, тот же проект `pulumi-cellestial`, та же приватная сеть
  (`192.168.199.0/24`) и роутер. У новой VPS всё своё: порт, boot-диск, floating IP. Новые
  логические имена с префиксом `coolify` — существующие ресурсы в плане `preview` не меняются.
- Флейвор `SL1.2-4096` (2 vCPU, 4 ГБ), диск 40 ГБ `fast.ru-9a` с `enableOnlineResize`. Через
  конфиг `infra:coolifyFlavorName`, `infra:coolifyVolumeSize` (по образцу `gateway`).
- `metadata: { role: "coolify", env: "release" }` — группа `coolify` в dynamic inventory.
- Security group `coolify` на порту VPS: входящие 22/tcp, 80/tcp, 443/tcp из `0.0.0.0/0`, плюс
  ICMP; исходящие — всё. Port security в проекте включена (проверено:
  `port_security_enabled=True` у `gateway-port`). Это и есть закрытие портов панели и сервиса:
  что бы Coolify ни опубликовал через Docker, снаружи оно не доступно. ufw — вторым слоем, как у
  прода.
- DNS: две A-записи в зоне `cellestial.ru.` на floating IP новой VPS — `coolify.cellestial.ru`
  (панель) и `hooks.cellestial.ru` (сервис). Конфиг `infra:coolifyDomain`, `infra:hooksDomain`.
- Бэкапы: приватный бакет `infra:coolifyBackupBucket` (`cellestial-coolify-backups-0`, пул
  `infra:s3Pool`), `protect: true`, без `forceDestroy`; отдельный сервисный пользователь с ролью
  `s3.bucket.user` и S3-ключом, доступ только по политике этого бакета (плюс `s3:*` пользователю
  стека) — по образцу бакета ноутбуков.
- Выходы: `coolifyPublicIp`, `coolifyDomain`, `hooksDomain`, `coolifyBackupBucket`,
  `coolifyBackupAccessKey`, `coolifyBackupSecretKey` (секретом).

### Хост (Ansible) — решение агента, согласовано

- Новый плейбук `coolify.yml` на группу `coolify`: роли `users`, `common`, `ssh_hardening`,
  `firewall`, `docker` и новая роль `coolify`. Первый вход — `bootstrap.yml` с
  `--limit coolify`.
- `site.yml` и второй play `verify.yml` — с `hosts: all` на `hosts: gateway`; проверка «ровно
  одна VPS» в `verify.yml` — на `groups['gateway']`. Иначе прод-роли уедут на новую машину.
- Роль `coolify`: установка официальным скриптом `https://cdn.coollabs.io/coolify/install.sh`
  с зафиксированной версией (`coolify_version` в `group_vars`), идемпотентно — повторный прогон
  при той же версии ничего не делает. Обновление — смена `coolify_version` и прогон плейбука;
  автообновление Coolify выключено.
- Coolify управляет «своим» сервером по SSH под root. `ssh_hardening` для этой группы разрешает
  root только по ключу и только из сетей Docker на хосте (`Match Address` с подсетями Docker
  bridge/`coolify`); снаружи root по-прежнему запрещён. Ключ Coolify — тот, что генерирует
  установка, кладётся в `authorized_keys` root.
- Первая настройка без открытия панели: администратор регистрируется через SSH-туннель
  (`ssh -L 8000:localhost:8000 deploy@coolify.cellestial.ru`), сразу задаёт домен панели
  `https://coolify.cellestial.ru` и закрывает регистрацию. Шаги — в RUNBOOK.

### Бэкапы — решение агента, согласовано

- Ночной systemd-таймер на хосте: `restic backup` каталога `/data/coolify` и docker-томов
  приложений Coolify в бакет бэкапов (S3 Selectel), шифрование restic. Хранение: 7 дневных,
  4 недельных (`restic forget --prune`). Пароль restic и S3-ключ бэкапа — в Vault.
- `/data/coolify/source/.env` (в нём `APP_KEY`) — в снимке; без него восстановленная база Coolify
  не расшифруется.
- Восстановление в RUNBOOK: `restic restore` на чистую машину после `coolify.yml`, проверка, что
  панель и сервис поднялись с данными. Проверить один раз в рамках задачи.

### CI/CD — решение агента

- `inventory/ci.yml` получает группу `coolify` с хостом `coolify.cellestial.ru`; job `apply`
  после `site.yml`/`verify.yml` прогоняет `coolify.yml`, job `preview` — `coolify.yml --check --diff`.
- Host key новой VPS добавляется в секрет `SSH_KNOWN_HOSTS` обоих environments (ручной шаг,
  админ репозитория), fingerprint — из консоли сервера в панели.

### Сервис в Coolify — решение агента

- Источник — отдельный репозиторий сервиса (GitHub App или deploy key Coolify) либо готовый образ;
  выбирает автор сервиса. Домен `https://hooks.cellestial.ru`, health check `/health`.
- Секреты сервиса (GitHub webhook secret, секрет Apidog, токен бота, chat/topic IDs) — только в
  переменных окружения Coolify, помеченных secret; в Git и в CI их нет.
- Постоянное хранилище очереди/дедупликации — persistent volume Coolify (он попадает в restic).
  Если сервис хранит данные в SQLite — включить WAL, снимок restic берётся с живого файла.
- В рамках этой задачи площадка проверяется тестовым приложением: образ с `/health` и volume;
  файл в volume переживает redeploy.

### Порядок первой выкатки — решение агента

CI после мержа сразу прогонит `coolify.yml`, поэтому машина, Vault и секрет CI готовятся из ветки
PR, до мержа (как было с ключом `ci-infra`):

1. Из ветки: `pulumi up` только по новым ресурсам (`--target` на `coolify*`, бакет бэкапов, его
   пользователя и DNS-записи) после показанного `preview`.
2. Ключи бэкапа и пароль restic — в `vault.yml` (коммит в PR).
3. `bootstrap.yml --limit coolify`, затем `coolify.yml`; первая настройка админа через туннель.
4. Host key новой VPS — в `SSH_KNOWN_HOSTS` обоих environments.
5. Preview в PR зелёный (`pulumi preview` без изменений, `coolify.yml --check` без ошибок) →
   ревью → мерж; первый `apply` из CI идемпотентен.

Доступ автору сервиса — отдельная учётка в Coolify (приглашение от администратора), не
администраторская.

## Что оставлено исполнителю

- Точная версия Coolify (последняя стабильная v4 на момент работы) и подсети Docker для
  `Match Address` — посмотреть на установленной машине.
- Как именно роль `coolify` определяет «уже стоит эта версия» (например, по
  `/data/coolify/source/.env` или `docker inspect` образа).
- Тестовое приложение для приёмки (любой минимальный образ с `/health` и volume).
- Тексты README/RUNBOOK: создание и проверка VPS, первая настройка, обновление Coolify, бэкап и
  восстановление, диагностика доставки вебхуков, откат приложения (redeploy предыдущего деплоя в
  Coolify), инструкция автору сервиса.

## Приёмка (из задачи)

- `pulumi preview`: создаются только ресурсы `coolify*`, бакет бэкапов с пользователем и DNS-записи;
  прод-ресурсы без изменений. Обе VPS доступны независимо.
- Панель открывается по `https://coolify.cellestial.ru` под администратором; тестовое приложение
  отвечает на `https://hooks.cellestial.ru/health`.
- Данные в volume переживают пересоздание контейнера; бэкап и восстановление проверены по RUNBOOK.
- Внешняя проверка портов: на новой VPS открыты только 22/80/443; прод-стенд продолжает отвечать,
  `verify.yml` зелёный.
