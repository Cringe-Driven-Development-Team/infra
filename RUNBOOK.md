# RUNBOOK: поднять стенд с нуля

Порядок: панель Selectel (руками) → Pulumi → Ansible → проверки.
Все команды — с управляющей машины (macOS), из корня репозитория.

## 0. Инструменты (один раз)

```bash
brew install pulumi/tap/pulumi bun awscli pipx
# ansible-core ≥ 2.18: этого требует community.general 13.x (apt в Ubuntu 24.04 даёт 2.16 — не подходит)
pipx install 'ansible-core>=2.18'
# openstacksdk — в venv Ansible, а не в системный python: обычный pip поставит мимо (pipx ≥ 1.4)
pipx inject ansible-core -r ansible/requirements.txt
ansible-galaxy collection install -r ansible/requirements.yml
ansible --version | head -1                         # core 2.18 или новее
```

Ключ стенда (если нет):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/selectel_release -N ""
```

## 1. Руками в панели Selectel

1. **Сервисный пользователь аккаунта** (Управление доступом → Сервисные пользователи):
   роли `member` + `iam.admin` **на аккаунт**. Записать: логин (hex-строка) и пароль.
2. **Зона DNS** `cellestial.ru.` и **бакет стейта** `cdd-infra-state` живут в проекте `infra-shared`
   и создаются bootstrap-стеком, руками их не заводят — см. `pulumi/bootstrap/README.md`.
   `dnsProjectId` для п.3 — `pulumi -C pulumi/bootstrap stack output dnsProjectId --stack main`.
3. **Личный доступ к стейту** — один раз на человека, по `pulumi/bootstrap/README.md`
   («Один раз на человека»): `~/.config/selectel.env` (`init-env.sh`) и личный S3-ключ
   (`bun state-key.ts`). Общих ключей стейта нет.
4. Если в зоне `cellestial.ru.` уже есть **рукописная A-запись** `cellestial.ru.` — удалить:
   запись будет под управлением Pulumi, иначе конфликт.

## 2. Pulumi: backend и стек

Стейт основного стека — в бакете `cdd-infra-state`, префикс `prod/`, стек `prod`. Бэкенд прибит в
`pulumi/Pulumi.yaml` (`backend.url`), `pulumi login` не нужен. Проверка: `pulumi whoami -v` в `pulumi/`
показывает `s3://cdd-infra-state/prod…`.

```bash
source pulumi/bootstrap/env.sh       # личный ключ стейта (AWS_*), passphrase, OS_* — п.1.3
cd pulumi
pulumi install                       # генерирует sdks/selectel, ставит deps через bun
pulumi stack select prod             # стек уже есть; с нуля: pulumi stack init prod --secrets-provider passphrase
```

## 3. Pulumi: конфиг

Конфиг прода — `pulumi/Pulumi.prod.yaml` в репо: для работы со стеком ничего вводить не нужно.
**Файл появится в репо после переезда стейта** (`pulumi/bootstrap/README.md`, «Переезд»); до этого
без него `pulumi preview` падает на `cfg.require("pool")` — конфиг есть только у того, кто вёл стек.
Команды ниже — только для стека с нуля. Логин и пароль Selectel в конфиг **не** кладутся:
они из `selectel.env` (п.1.3), у каждого свои.

```bash
pulumi config set selectel:domainName '<номер аккаунта>'
pulumi config set selectel:authUrl    https://cloud.api.selcloud.ru/identity/v3/
pulumi config set selectel:authRegion ru-9

pulumi config set infra:name              pulumi-cellestial   # уникально в общем аккаунте
pulumi config set infra:serviceUserName   cellestialSystemUser
pulumi config set infra:pool              ru-9
pulumi config set infra:zone              ru-9a
pulumi config set infra:volumeType        fast.ru-9a
# Флейвор единственной VPS — не меньше SL1.2-8192 (Caddy, позже Go API и Postgres);
# актуальные флейворы пула: ./scripts/list-flavors.sh
pulumi config set infra:gatewayFlavorName SL1.2-8192
# Boot-диск (под будущий Postgres), по умолчанию 20 ГБ:
# pulumi config set infra:gatewayVolumeSize 20
pulumi config set infra:imageName        'Ubuntu 24.04 LTS 64-bit'
pulumi config set infra:sshPublicKey     "$(cat ~/.ssh/selectel_release.pub)"
# Публичные ключи команды — кладутся root через cloud-init при первой загрузке
# (keypair остаётся основным). Смена списка пересоздаёт сервер!
pulumi config set --path 'infra:sshPublicKeys[0]' 'ssh-ed25519 AAAA... <имя-владельца>'
pulumi config set --path 'infra:sshPublicKeys[1]' 'ssh-ed25519 AAAA... <имя-владельца>'

pulumi config set infra:domain       cellestial.ru
pulumi config set infra:dnsZone      cellestial.ru.
pulumi config set infra:dnsProjectId '<project id из п.1.2>'

pulumi config set infra:s3Pool   ru-7
pulumi config set infra:s3Bucket '<имя бакета релизов, глобально уникальное>'
pulumi config set infra:notebooksBucket '<имя приватного бакета ноутбуков, глобально уникальное>'
pulumi config set infra:avatarsBucket '<имя публичного бакета аватарок, глобально уникальное>'
pulumi config set infra:staticBucket '<имя публичного бакета статики, глобально уникальное>'
pulumi config set infra:s3PublicRead true   # публичное чтение объектов (политика бакета), нужно для DoD
```

## 4. Pulumi: создать инфраструктуру

```bash
pulumi preview        # должно быть: 1 server, 1 volume, сеть, бакет, rrset, ...
pulumi up             # подтвердить, ~5-10 минут
pulumi stack output   # projectId, publicIp, s3Endpoint, s3Bucket, s3AccessKey, s3SecretKey, domain, notebooks*, avatars*
```

Ключ Go API к бакету ноутбуков (`notebooksAccessKey`, `notebooksSecretKey`, оба — с `--show-secrets`)
после первого `up` и после пересоздания стека переносится в vault: `ansible-vault edit`, переменные
`vault_notebooks_s3_access_key` и `vault_notebooks_s3_secret_key` (п.5, «Секреты»). Проверка доступа —
`pulumi/README.md`, «Бакет ноутбуков».

## 5. Ansible: креды и запуск

```bash
cd ansible
# Пароль vault (взять лично у Дениса или менторов) — один раз, в файл вне репо:
(umask 077; printf '%s\n' '<пароль vault>' > ~/.config/cdd-vault-pass)

. ./env.sh                                    # OS_* из selectel.env + projectId/pool прод-стека,
                                              # ANSIBLE_VAULT_PASSWORD_FILE=~/.config/cdd-vault-pass
# или вместо env.sh: cp clouds.yaml.example clouds.yaml и руками username/password/user_domain_name
# из п.1.1, project_id = `pulumi stack output projectId`, region_name ru-9 (оба сразу — нельзя);
# тогда и export ANSIBLE_VAULT_PASSWORD_FILE=~/.config/cdd-vault-pass — руками

ansible-vault view inventory/group_vars/all/vault.yml   # пароль подходит: видны vault_postgres_password, vault_jwt_secret, vault_csrf_secret

ansible-inventory -i inventory --graph        # должен появиться 1 хост: gateway
# ключи всех, кто заходит на стенд, — в files/authorized_keys/*.pub (коммитятся в репо;
# роль users кладёт их все в deploy с exclusive: true)
ansible-playbook bootstrap.yml                # root:22 → python3 + пользователь deploy
ansible-playbook site.yml                     # базовая настройка + Caddy (получит сертификат LE)
ansible-playbook site.yml                     # повтор: expected changed=0
ansible-playbook verify.yml                   # проверки DoD
```

`bootstrap.yml` — только для хоста с новым диском (после `destroy` или замены boot-volume): после
`site.yml` вход под root закрыт, тогда `ansible-playbook bootstrap.yml --limit gateway`. Сервер,
пересозданный Pulumi'ем (смена ключей в `infra:sshPublicKeys`), сохраняет диск — ему хватает
`site.yml`. Подробнее — `ansible/README.md`, «Запуск».

### Секреты (ansible-vault)

Секреты выкатки — в `ansible/inventory/group_vars/all/vault.yml`, зашифрованы и закоммичены (репо
публичный). Пароль нужен любому playbook'у (`bootstrap.yml`, `site.yml`, `verify.yml`): файл лежит в
`group_vars/all`. Подробности и шаг для CI — `ansible/README.md`, «Секреты».

```bash
ansible-vault view inventory/group_vars/all/vault.yml    # посмотреть
ansible-vault edit inventory/group_vars/all/vault.yml    # поправить (не decrypt → encrypt)
```

- **Смена пароля**: `ansible-vault rekey --new-vault-password-file <новый файл>
  inventory/group_vars/all/vault.yml`, заменить `~/.config/cdd-vault-pass`, закоммитить `vault.yml`,
  обновить секрет `ANSIBLE_VAULT_PASSWORD` в CI, раздать новый пароль лично.
- **Утечка пароля**: `rekey` не помогает — старый шифротекст остаётся в истории git и открывается
  утёкшим паролем. Меняются сами секреты: новый пароль vault, новые значения в `vault.yml`
  (`openssl rand -base64 32`), выкатка (пароль Postgres — и в самой БД), обновить секрет в CI.
  S3-ключ Go API (`vault_notebooks_s3_*`) случайной строкой не заменить — он перевыпускается в Pulumi:
  `pulumi up --replace '<URN notebooks-s3>'` (URN — из `pulumi stack --show-urns`), новые
  `notebooksAccessKey` / `notebooksSecretKey` — в `vault.yml`, выкатка, затем проверить запросом, что
  старый ключ больше не действует. По шагам — `ansible/README.md`, «Утечка пароля».

## 6. Проверки руками (DoD)

```bash
curl -I https://cellestial.ru/                                  # 200
echo | openssl s_client -connect cellestial.ru:443 -servername cellestial.ru 2>/dev/null | grep issuer

cd ../pulumi
# Ключи продукта читаем в локальные переменные и отдаём только команде aws:
# AWS_* в окружении — это ключи backend'а стейта (п.2), перетирать их нельзя,
# иначе следующие pulumi stack output не прочитают стейт.
S3_ENDPOINT=$(pulumi stack output s3Endpoint)
S3_BUCKET=$(pulumi stack output s3Bucket)
S3_AK=$(pulumi stack output s3AccessKey)
S3_SK=$(pulumi stack output s3SecretKey --show-secrets)    # без --show-secrets будет "[secret]"

echo hello > /tmp/hello.txt
# awscli не доверяет цепочке сертификатов Selectel из коробки — нужен их корневой сертификат,
# см. https://docs.selectel.ru/en/s3/tools/aws-cli (ca_bundle / AWS_CA_BUNDLE)
AWS_ACCESS_KEY_ID="$S3_AK" AWS_SECRET_ACCESS_KEY="$S3_SK" \
  aws --endpoint-url "$S3_ENDPOINT" --region "$(pulumi config get infra:s3Pool)" \
  s3 cp /tmp/hello.txt "s3://$S3_BUCKET/"

# публичный URL объекта = <s3Endpoint>/<s3Bucket>/<key>; без авторизации отвечает 200,
# только если включено infra:s3PublicRead (п.3)
curl -I "$S3_ENDPOINT/$S3_BUCKET/hello.txt"   # 200

# Публичные порты стенда — только 22/80/443 (проверяет и verify.yml). Остальные,
# в т.ч. порты Docker в обход ufw, должны быть закрыты:
IP=$(pulumi stack output publicIp)
for p in 22 80 443;   do nc -z -G3 "$IP" $p && echo "$p open"; done
for p in 5432 8080 2375 2376; do nc -z -G3 "$IP" $p && echo "$p OPEN — так не надо"; done
```

CDN: CDN-ресурс с бакетом источником создаёт Pulumi (`infra:cdn: true`) — файлы отдаются с
`pulumi stack output cdnDefaultDomain`. Свои домены тоже делает Pulumi: `infra:cdnDomain` — CNAME в зоне
`cellestial.ru.`, `infra:staticDomain` — такой же CNAME на второй CDN-ресурс (источник — бакет
статики), `infra:avatarsDomain` — отдельная зона `avatars.cellestial.ru.`, оба с привязкой к
CDN-ресурсу и бакету аватарок. Сертификаты к ним — руками в панели (CDN → ресурс → сертификаты; S3 →
SSL-сертификаты); после выпуска:

```bash
curl -I "https://$(pulumi stack output cdnCustomDomain)/index.html"
curl -I "https://$(pulumi stack output avatarsCustomDomain)/<ключ аватарки>"
```

Что автоматом, а что руками — `SUMMARY.md`.

## 7. Снос всего

Бакеты ноутбуков и аватарок защищены (`protect: true`, без `forceDestroy`). Pulumi проверяет `protect`
при построении плана: обычный `pulumi destroy` упадёт с `unable to delete resource … marked for
protection` и **не удалит ничего** — VPS, сеть и бакет релизов останутся и продолжат тарифицироваться.

Снести всё, кроме данных пользователей:

```bash
cd pulumi && pulumi destroy --exclude-protected   # бакет релизов удалится с объектами (forceDestroy: true)
pulumi stack --show-urns                          # проверить, что осталось в стейте
```

Снести совсем — сохранить или удалить объекты обоих бакетов, затем:

```bash
pulumi state unprotect 'urn:pulumi:prod::infra::aws:s3/bucket:Bucket::notebooks'
pulumi state unprotect 'urn:pulumi:prod::infra::aws:s3/bucket:Bucket::avatars'
pulumi destroy
```

## Частые грабли

| Симптом | Причина |
|---|---|
| `pulumi whoami` падает с `no EC2 IMDS role found` | Не выполнен `source pulumi/bootstrap/env.sh` (нет личного ключа стейта `AWS_*`) — п.2 |
| `pulumi stack select prod`: стек не найден | Команда запущена не из `pulumi/` (бэкенд берётся из `Pulumi.yaml` каталога) или задана `PULUMI_BACKEND_URL` — `pulumi whoami -v` должен показать `s3://cdd-infra-state/prod…` — п.2 |
| `409 already_exists` | Имя занято в общем аккаунте → сменить `infra:name` / `infra:serviceUserName` / `infra:s3Bucket` |
| `Your query returned no results` на зоне | `infra:dnsZone`/`infra:dnsProjectId` не совпадают с реальностью |
| `ExternalGatewayForFloatingIPNotFound` | Уже обработан (`dependsOn`), повторить `pulumi up` |
| `Host key verification failed` / `REMOTE HOST IDENTIFICATION HAS CHANGED` | Сервер пересоздан (новые host keys на том же адресе), `accept-new` старую запись не заменит: `ssh-keygen -R $(pulumi stack output publicIp)` |
| `Too many authentication failures` | ssh перебрал ключи агента раньше ключа стенда (`MaxAuthTries 4`). В `ansible.cfg` уже `IdentitiesOnly=yes`; при ручном ssh добавлять `-o IdentitiesOnly=yes` |
| `Attempting to decrypt but no vault secrets found` | Не задан `ANSIBLE_VAULT_PASSWORD_FILE` (не выполнен `. ./env.sh`) — п.5 |
| `The vault password file … was not found` / `Decryption failed` | Нет `~/.config/cdd-vault-pass` или в нём не тот пароль — взять лично у Дениса или менторов |
| Caddy не получает сертификат | A-запись ещё не указала на `publicIp` — `dig cellestial.ru`, подождать TTL 300s |

## Выкатка Go API (infra#30)

После мержа infra#22 и infra#30 установите CI-ключ через `site.yml`, настройте
секреты и ручное approval environment `production` бэка. Backend PR мержится
последним и запускает первый деплой. Команды первой выкатки, проверки и отката
по SHA-тегу — в [ansible/README.md](ansible/README.md#go-api-первая-выкатка-и-откат).
Pulumi из CI не запускается. Перед приёмкой проверьте pull приватного образа и logout GHCR,
healthy у API и Postgres, сохранность данных после отката и неизменность тега API
после `site.yml`; маршруты клиента и закрытые порты — через `verify.yml`.

Перенос backend в team/backend меняет default image на
`ghcr.io/cringe-driven-development-team/backend`. Этот infra PR принимается
только после публикации нового private package и остановки старого deploy,
который также читает infra@main. Затем включается новый production writer.
Полное окно и metadata gates — в
[runbook организации](https://github.com/Cringe-Driven-Development-Team/.github/blob/main/docs/migrations/team-repositories-runbook.md).
Откат к прежней организации требует явного
`app_backend_image=ghcr.io/go-park-mail-ru/2026_2_cringe_driven_development`,
старого SHA-тега и GHCR credential, читающего старый private package.
Команда с override — в ansible/README.md; volumes и БД при переносе не меняются.
Если старый private package недоступен текущему токену, подготовить до переключения
проверенный архив работающего image. Откат из него через отдельный Compose overlay
с `pull_policy: never` описан в
[ansible/README.md](ansible/README.md#откат-из-проверенного-архива-без-registry).
Архив, его SHA256 и результаты load-проверки хранить в журнале окна вне checkout;
повторный pull старого package остаётся отдельной операцией с прежним read credential.

## Выкатка BFF (infra#58)

Схема: браузер → Caddy → BFF (`bff:3000`) → Go (`api:8080`, только сеть `app`). Снаружи — HTML,
`/api/trpc` и публичные файлы через BFF; `/api/v1/*` и прочие `/api/*` — `404` от BFF (не HTML),
`/health` — `404` от Caddy. Чанки JS и CSS — с CDN каталога своего релиза. До первой выкатки BFF
домен отвечает `503 BFF is not deployed`. Подробно — [ansible/README.md](ansible/README.md#клиент-и-bff).

1. Go выкачен (`deploy-backend.yml`), в Vault есть `vault_session_key` и `vault_bff_api_key`,
   package `bff` в GHCR публичный.
2. `deploy-bff.yml -e "bff_image_ref=$BFF_IMAGE_REF" -e "bff_release_sha=$BFF_RELEASE_SHA"` с реальными
   ссылкой по digest и полным SHA в этих переменных, без GHCR credentials (команда —
   [ansible/README.md](ansible/README.md#bff-выкатка-проверка-и-откат); в CD её запускает workflow
   фронта, frontend#4). Playbook до изменений VPS скачивает образ анонимно и сверяет его метку
   `org.opencontainers.image.revision` с SHA, после запуска — что контейнер работает на этом образе и
   `healthy`, и снаружи — гостевой HTML `/login` с bootstrap и релизом этого SHA, `UNAUTHORIZED` у
   `users.me` без сессии и `404` на `/api/v1`, `/api/unknown`, `/health`.
3. Руками — пользовательский путь: вход, защищённый RPC (список блокнотов), прямое открытие
   `/notebooks/<id>`. Затем `verify.yml`.
4. Откат — шаг 2 с прежними `bff_image_ref` и `bff_release_sha`; после него те же проверки. `SESSION_KEY` не меняется, сессия
   остаётся. Go и БД откат BFF не трогает.
5. Недоступность Go: RPC отвечает tRPC `BAD_GATEWAY` (502), HTML — `503` без приложения, а не SPA.

## Вспомогательная VPS (infra#54)

Отдельная VPS под сервис вебхуков GitHub/Apidog → Telegram: `https://hooks.cellestial.ru`. Caddy и
контейнер сервиса — проект Compose `/opt/hooks`; порт сервиса наружу не публикуется, снаружи открыты
только 22/80/443 (security group). Прод-VPS её плейбук не трогает. Устройство — `pulumi/index.ts`
(«Вспомогательная VPS»), `ansible/auxiliary.yml`, роли `hooks` и `restic_backup`.

### Создание

1. Pulumi (`infra:auxiliary: "true"` в `Pulumi.prod.yaml`): после мержа это делает CI. Первый раз, до мержа, —
   из ветки только по новым ресурсам после `pulumi preview`.
2. Ключ бэкапа и пароль restic — в Vault:

   ```bash
   cd pulumi
   pulumi stack output auxiliaryBackupAccessKey --show-secrets
   pulumi stack output auxiliaryBackupSecretKey --show-secrets
   cd ../ansible && ansible-vault edit inventory/group_vars/all/vault.yml
   # vault_auxiliary_backup_access_key, vault_auxiliary_backup_secret_key,
   # vault_auxiliary_restic_password (openssl rand -base64 32; без него бэкапы не прочитать)
   ```

3. Хост: `. ./env.sh && ansible-playbook bootstrap.yml --limit auxiliary && ansible-playbook auxiliary.yml`.
   Бакета или ключей ещё нет — `ansible-playbook auxiliary.yml --skip-tags backup`.
4. Host key — в `SSH_KNOWN_HOSTS` environments `production` и `infra-preview` вместе с `cellestial.ru`
   (fingerprint сверить в консоли сервера: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`):

   ```bash
   for env in production infra-preview; do
     ssh-keyscan -t ed25519 cellestial.ru hooks.cellestial.ru | gh secret set SSH_KNOWN_HOSTS --env "$env"
   done
   ```

### Проверки

```bash
curl -s https://hooks.cellestial.ru/health                        # ok, валидный сертификат
for p in 22 80 443 8080; do nc -z -G3 hooks.cellestial.ru $p && echo "$p open" || echo "$p closed"; done
# открыты только 22, 80, 443
curl -sI https://cellestial.ru | head -1                          # прод отвечает как раньше
```

### Развёртывание сервиса (infra#70, .github#20)

Роль `hooks` читает оба выбранных Release из `/opt/hooks/releases.json` на VPS3.
`desired` задаёт digest образа и source SHA; `confirmed` хранит последний проверенный
релиз и может отставать после сбоя. Обычный apply всегда сохраняет обе версии
`desired`, даже если `confirmed` старее. Inventory больше не закрепляет образы приложений.
Локальный Docker image ID нельзя использовать вместо manifest digest.

Перед первой выкаткой:

1. Через `ansible-vault edit inventory/group_vars/all/vault.yml` добавить
   `vault_hooks_env`: непустые `GITHUB_WEBHOOK_SECRET`, `APIDOG_SIGNATURE_KEY`,
   `APIDOG_SIGNATURE_HEADER` (фактическое имя заголовка Apidog). Это обязательные
   параметры даже при выключенном sender. `DATABASE_PATH=/data/queue.sqlite`,
   `PORT=8080`, `WEBHOOK_ALERTS_SEND_ENABLED=false` заданы ролью. Значения env в
   Vault хранить строками; не включать worker до приёмки Task 10. Его дополнительные
   переменные и переключение описаны в [runbook сервиса](https://github.com/Cringe-Driven-Development-Team/.github/blob/main/services/webhook-alerts/README.md).

   Для пингов GitHub-инициаторов добавьте в `vault_hooks_env.TELEGRAM_USER_MAP`
   проверенную карту GitHub login → числовой Telegram ID из переменной организации
   `TELEGRAM_USER_MAP`. GitHub Actions vars автоматически не попадают в контейнер.
   После применения сверяйте карту в Vault, `/opt/hooks/.env` и окружении hooks
   без вывода значений; неизвестный аккаунт отображается обычным ником.

   Для включённого sender также нужны `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_APIDOG_BOT_TOKEN` (отдельный бот Apidog), `TELEGRAM_CHAT_ID`,
   `TELEGRAM_APIDOG_TOPIC_ID`, `APIDOG_ACCESS_TOKEN`, `APIDOG_TELEGRAM_USER_MAP`
   и `APIDOG_MR_REVIEWERS`.
   Токен чтения MR и карту хранить только в `vault_hooks_env`. Карта — строка JSON:
   `{"1066826":{"name":"Имя участника","telegramId":"123"}}` (пример с вымышленным
   Telegram ID). Ключ — Apidog user ID, `name` — проверенное отображаемое имя,
   `telegramId` — строка с положительным числовым Telegram ID. Начальные записи:
   `1066826` — Валентин Кунев (`MrDuckVC`), `1067537` — Александр Батовкин
   (`blackHATred`); Telegram IDs копировать из проверенной `TELEGRAM_USER_MAP`
   организации без вывода значений. При добавлении участников обновлять карту;
   неизвестный ID отображается без пинга, пустая карта `{}` допустима.
   Не передавать токен в argv, URL, логи или комментарии GitHub.

   Постоянную пару ревьюверов MR менять через `ansible-vault edit` в
   `vault_hooks_env.APIDOG_MR_REVIEWERS`: строка JSON с ровно двумя объектами
   `{ "name": "…", "telegramId": "…" }`, сначала Александр Батовкин, затем
   Ярослав Михалев. Имена непустые, Telegram ID — разные положительные числовые
   строки из проверенного источника; значения не выводить в логи. Порядок массива
   задаёт порядок упоминаний при открытии MR и не зависит от `reviewerIds` Apidog.

   Публичные defaults роли: `APIDOG_PROJECT_ID=1382426`,
   `APIDOG_TARGET_BRANCH_ID=1388508` (main),
   `APIDOG_PROJECT_NAME=2026_2_Cringe_Driven_Development`,
   `APIDOG_CONTRACT_URL=https://vb78fyael1.apidog.io/`.
   Preflight включённого sender проверяет наличие токена/карты, положительные
   числовые IDs, непустое имя и HTTP/HTTPS URL; сервис проверяет структуру JSON.
   При недоступном Apidog исходный merge-алерт отправляется с кнопкой контракта;
   однозначный MR добавляет упоминания и точную кнопку «Открыть MR».
2. Проверить, что package `webhook-alerts` опубликован как **public**, и
   анонимный pull закреплённого digest с VPS проходит с чистым Docker config.
   Публичность исходного `.github` репозитория сама по себе не делает GHCR package
   публичным. Изменение visibility package — отдельный шаг владельца;
   этот PR его не выполняет. Пока anonymous pull недоступен, выкатка не готова.
3. Проверить SSH host key по консоли сервера и сохранить доверенную запись в
   `SSH_KNOWN_HOSTS` обоих environments; не принимать ключ по одному `ssh-keyscan`.

`auxiliary.yml` проверяет обязательные webhook env до изменений VPS. Роль явно
скачивает закреплённый образ `hooks` без login перед подготовкой контейнера;
при ошибке pull playbook прекращается с указанием проверить visibility, digest
и доступность GHCR. Registry credentials и package permissions для auxiliary
не требуются. Caddy скачивается отдельно; запуск использует `pull: never`. Существующий
том от busybox перед запуском получает владельца `bun:bun` из нового образа;
`/data` и `/data/snapshots` получают права `0700`.

```bash
cd ansible
. ./env.sh
ansible-playbook auxiliary.yml --check --diff
ansible-playbook auxiliary.yml
```

Плейбук ждёт `200` на `https://hooks.cellestial.ru/health`; сервис работает от
`bun` и сохраняет очередь в `hooks_hooks_data`. Перед первым `verify.yml` выполнить
первый бэкап по процедуре ниже: проверка теперь обнаруживает отсутствие snapshot.
Обновление или откат образа — новый проверенный digest и соответствующий SHA-тег
в defaults роли, затем тот же playbook. Очередь не удалять. Логи:
`ssh deploy@hooks.cellestial.ru 'cd /opt/hooks && sudo docker compose logs -n 100 hooks'`.

### GitHubBot на auxiliary

Сервис, env и права: [Ansible README](ansible/README.md#githubbot-на-auxiliary-infra111).
Операции ниже выполняет владелец выкладки после подготовки реальных inputs.
Текущее выполнение фиксировать в [журнале](docs/deployments/2026-11-07-githubbot.md).

**Подготовка конфигурации.** Получить `chatId` из существующей org variable
`TELEGRAM_CHAT_ID` и сверить группу через Telegram metadata. Топики читать через
Telegram `list_topics`: брать `id` Backend/Frontend/UX, не IDs сообщений в других
топиках. Releases не добавлять в фиксированную маршрутизацию. Для
`notificationBotIds` получить ID автора настоящей карточки GitHub Monitoring
или участника-бота группы и подтвердить username/`is_bot` через профиль бота.
Имя само по себе не является проверкой доверия.

Взять GitHub login → Telegram ID из org `TELEGRAM_USER_MAP`, затем сопоставить
каждый ID с текущим username через список участников/профиль Telegram. Не
угадывать username по GitHub login; непроверенных пользователей не добавлять.
В owner-approved JSON вне Git собрать три repo с префиксами из Ansible README,
три topic mapping и проверенных пользователей. JSON и реальные maps не публиковать.

Использовать общего с GitHub Monitoring Telegram-бота: токен уже хранится в
`vault_hooks_env.TELEGRAM_BOT_TOKEN` и поступает через effective `hooks_env`.
В BotFather для общего бота `/setprivacy` → Disable; владелец подтверждает
`getMe.can_read_all_group_messages` и членство. Long polling выполняет только GitHubBot;
отправщик уведомлений использует тот же токен для отправки. Отдельный Telegram bot не нужен.
Получить сервисный PAT с Issues read/write для allowlist и organization Projects
read/write; проверить нужные approval/SSO. Существующий Actions secret
`ADD_TO_PROJECT_PAT` нельзя прочитать обратно из GitHub, его имя не даёт credentials.
Токены передавать только через согласованный закрытый канал, не argv/URL/логи.

В `ansible/` выполнить `ansible-vault edit inventory/group_vars/all/vault.yml`:
добавить словарь `vault_githubbot_env` с двумя **строковыми** значениями
`GITHUB_TOKEN`, `GITHUBBOT_CONFIG_JSON` (JSON в одну строку).
Имена сверять парсером YAML без вывода значений. Не использовать `decrypt`.

**Образ и локальный config gate.** Дождаться зелёных service checks и
`githubbot-image.yml` на полный исходный SHA. В доверенном успешном запуске
штатного publisher репозитория `.github` скачать `release.json` из артефакта
`release-githubbot-<run_attempt>`, сверить `source_sha` с SHA запуска и тегом `ghcr.io/cringe-driven-development-team/githubbot:sha-<полный SHA>`.
Проверить, что registry manifest digest этого тега и `Digest` анонимного pull
закреплённого образа совпадают с digest из `image_ref` того же `release.json`.
Проверить public pull с пустым Docker config и только OCI source
`https://github.com/Cringe-Driven-Development-Team/.github`; записать
проверенные Release в состояние через штатный executor (не в inventory).
`org.opencontainers.image.revision` унаследован от базового Bun image и не
идентифицирует исходный SHA GitHubBot; для этой сверки его не использовать.
Локальный Docker image ID не подставлять вместо registry manifest digest.
Следующий скрипт из `ansible/` делает anonymous pull, показывает только OCI source,
затем проверяет настоящий Vault config без сети и polling. Он создаёт закрытый
Docker env-файл и удаляет его даже при ошибке; это не Compose-encoded env-файл.
Нужны Python с PyYAML, Docker и настроенный `ANSIBLE_VAULT_PASSWORD_FILE`:

```bash
python3 - <<'PY'
import os, subprocess, tempfile
from pathlib import Path
import yaml

import json
# Локальный release.json — артефакт доверенного publisher, не inventory.
release = json.loads(Path('release.json').read_text())
image = release['image_ref']
with tempfile.TemporaryDirectory(prefix='githubbot-check-') as directory:
    docker_config = Path(directory, 'docker-config')
    docker_config.mkdir(mode=0o700)
    subprocess.run(['docker', '--config', str(docker_config), 'pull', image], check=True)
    subprocess.run(['docker', 'image', 'inspect', '--format',
                    '{{index .Config.Labels "org.opencontainers.image.source"}}', image], check=True)
    vault = subprocess.run(['ansible-vault', 'view', 'inventory/group_vars/all/vault.yml'],
                           check=True, capture_output=True, text=True)
    env = {'DATABASE_PATH': '/data/githubbot.sqlite',
           'TELEGRAM_PROXY_URL': 'http://telegram-proxy:2080'}
    secrets = yaml.safe_load(vault.stdout)
    env.update(secrets['vault_githubbot_env'])
    env['TELEGRAM_BOT_TOKEN'] = secrets['vault_hooks_env'].get('TELEGRAM_BOT_TOKEN', '')
    assert env['TELEGRAM_BOT_TOKEN'], 'Shared Telegram token is required'
    path = Path(directory, 'githubbot.env')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        for name, value in env.items():
            assert isinstance(value, str) and '\n' not in value and '\r' not in value
            stream.write(f'{name}={value}\n')
    subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '--network', 'none',
                    '--env-file', str(path), image, 'bun', 'run', 'src/main.ts', '--check-config'], check=True)
PY
```

Успех offline gate не проверяет токены, privacy и API permissions. Перед запуском
polling владелец проверяет `getMe`, `getWebhookInfo` и членство через закрытый API
клиент. Непустой webhook URL — ошибка конфигурации, его не удалять автоматически.
Для отдельного токена не должно быть другого `getUpdates` poller: выключить его
на прежнем месте до старта, не запускать диагностический `getUpdates` параллельно.

**Выкладка и проверка.** Infra PR содержит `Closes #111`, ссылку на service PR,
digest/source SHA и зелёные CI URLs; service PR — `Part of
Cringe-Driven-Development-Team/.github#60`. До merge проверить текущий head,
обязательные checks и конфликты. После разрешения на merge/apply использовать
штатный workflow `.github/workflows/infra.yml`; не запускать дополнительный
Pulumi ради контейнера. После CI apply выполнить из `ansible/`:

```bash
. ./env.sh
ansible-playbook -i inventory/openstack.yml verify.yml --limit auxiliary
ansible-playbook -i inventory/openstack.yml verify.yml --limit localhost
```

Второй вызов запускает controller-level проверки всего стенда, включая auxiliary
HTTPS/закрытые порты; первый — snapshot webhook-alerts и один healthy GitHubBot.
На auxiliary проверить без раскрытия env:

```bash
sudo stat -c '%a %U:%G' /opt/hooks/githubbot.env
sudo docker compose -f /opt/hooks/compose.yml ps --all githubbot
sudo docker compose -f /opt/hooks/compose.yml exec -T githubbot sh -c 'test -w /data && test -w /data/githubbot.sqlite'
sudo docker compose -f /opt/hooks/compose.yml exec -T githubbot bun run src/main.ts --health
sudo docker inspect --format '{{json .HostConfig.PortBindings}}' "$(sudo docker compose -f /opt/hooks/compose.yml ps --quiet githubbot)"
sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$(sudo docker compose -f /opt/hooks/compose.yml ps --quiet githubbot)"
```

Ожидается mode 600, ровно один running/healthy, writable SQLite, health exit 0;
`PortBindings` пустой (`{}`/`null`), mount — `hooks_githubbot_data`.
Полный `docker inspect`/`compose config` не сохранять. В безопасных логах не должно
быть конфликта polling HTTP409. Даже один healthy локальный контейнер не доказывает,
что тот же токен нигде больше не используется; это подтверждает владелец токена.

**Обновление, restart и recreate.** Новый проверенный public digest/source tag
и Vault config доставляются тем же infra workflow; `restart` не перечитывает env.
Для обычного рестарта на auxiliary: `sudo docker compose -f /opt/hooks/compose.yml
restart --no-deps githubbot`. Не удалять `hooks_githubbot_data`, SQLite/WAL/SHM,
не запускать `down -v`. Для проверки recreate владелец сначала создаёт
`waiting_repo` заявкой `/issue GitHubBot smoke` в топике без фиксированного repo.
Затем удаляет только контейнер: `sudo docker compose -f /opt/hooks/compose.yml
rm --stop --force githubbot` (без `-v`) и повторяет штатную роль
`ansible-playbook -i inventory/openstack.yml auxiliary.yml`. С остальными
неизменными параметрами роль восстанавливает отсутствующий `githubbot`;
сверить ID остальных контейнеров до/после. Выбор старой кнопки создаёт один issue,
повторный callback не создаёт второй; mount по-прежнему `hooks_githubbot_data`.

**Username и uncertain create.** Если username изменился, сверить Telegram ID
и новый alias с владельцем, исправить `users` через Vault и доставить config с
recreate. Не снимать защиту удалением SQLite: старая пара ID/alias остаётся
заблокированной; `/assignme` использует ID. Если бот сообщил неизвестный исход
создания, сверить repo, автора-сервисную учётку, время, заголовок и описание через
GitHub API/UI. Найденную задачу использовать по URL и проверить Project/Status/
Sprint; при дублях владелец выбирает нужную. Только убедившись в отсутствии
созданной задачи, владелец отправляет новую `/issue`. Не повторять POST, не
переводить SQLite journal вручную в `ready`, не удалять базу ради повтора.

**Живая приёмка с владельцем.** Владелец отправляет команды или отдельно разрешает
агенту отправку. Создавать только `GitHubBot smoke`, закрыть тестовые issues после
приёмки. В Backend/Frontend/UX сверить repo, prefix, no-assignee, Backlog/empty
Sprint; в другом топике проверить кнопки, отказ чужому автору и один issue после
выбора. `/assignme`, `/assign @username`, `/unassign @username`, `/tosprint`,
`/close` повторить reply к обеим настоящим карточкам и с URL. `/sprint` и
`/sprint @username` сверить с API, включая closed и unassigned. Настоящий reply
к GitHub Monitoring сохранить только обезличенным fixture в service repo и
запустить его regression test. Проверить recreate по процедуре выше.
Неопределённый POST проверять локальным fault injection service tests, без
искусственного сбоя GitHub на проде. В журнале разделять live, offline и fixture.

### Бэкап и изолированное восстановление

`restic-backup.timer` запускается ночью, хранит 7 дневных и 4 недельных снимка.
`ExecStartPre` вызывает в работающем контейнере:

```bash
docker compose -f /opt/hooks/compose.yml exec -T hooks bun run src/cli.ts snapshot --output /data/snapshots/queue.sqlite
```

CLI проверяет целостность и атомарно заменяет snapshot. Ошибка `ExecStartPre`
останавливает unit до `restic backup` и `forget`: старый файл не становится новым
бэкапом. В архив входят только:

- `/var/lib/docker/volumes/hooks_hooks_data/_data/snapshots/queue.sqlite`;
- `/var/lib/docker/volumes/hooks_caddy_data/_data` и `hooks_caddy_config/_data`;
- `/opt/hooks` (включая `.env`, поэтому Restic password нужен для восстановления).

Живые `queue.sqlite`, `queue.sqlite-wal`, `queue.sqlite-shm` исключены явным списком
путей. Для восстановления очереди нужен один snapshot, исходный WAL не нужен.

Первый бэкап и проверка журнала:

```bash
ssh deploy@hooks.cellestial.ru
sudo systemctl start restic-backup
sudo journalctl -u restic-backup -n 30
sudo bash -c 'set -a; . /etc/restic/env; restic snapshots --host auxiliary'
```

В журнале требуется успешный новый snapshot Restic. Затем восстановить в отдельный
каталог, сохранив ID выбранного бэкапа из `restic snapshots` в журнале приёмки:

```bash
restore_dir=$(sudo mktemp -d /var/tmp/hooks-restore.XXXXXX)
sudo bash -c 'set -a; . /etc/restic/env
  restic restore latest --host auxiliary --target "$1" \
    --include /var/lib/docker/volumes/hooks_hooks_data/_data/snapshots/queue.sqlite' bash "$restore_dir"
image=$(sudo docker inspect --format '{{.Config.Image}}' hooks-hooks-1)
# Запускается только Bun-скрипт: нет main.ts, сети и worker; отправка явно выключена.
sudo docker run --rm -i --network none --pull never --user root \
  -e WEBHOOK_ALERTS_SEND_ENABLED=false \
  -v "$restore_dir/var/lib/docker/volumes/hooks_hooks_data/_data/snapshots:/restore" \
  --entrypoint bun "$image" - <<'JS'
import { Database } from "bun:sqlite";
import { openStore } from "./src/store";
const path = "/restore/queue.sqlite";
const db = new Database(path, { readonly: true });
let row;
try {
  const integrity = db.query("PRAGMA integrity_check").get().integrity_check;
  if (integrity !== "ok") throw new Error("integrity_check failed");
  console.log("integrity_check:", integrity);
  console.log(db.query("SELECT id, source, delivery_id, prepared FROM deliveries ORDER BY id").all());
  console.log(db.query("SELECT id, origin_type, origin_id, status, telegram_message_id FROM outbox ORDER BY id").all());
  row = db.query("SELECT source, delivery_id, event, raw FROM deliveries WHERE dedupe_kind = ? LIMIT 1").get("id");
} finally { db.close(); }
if (!row) throw new Error("A stable-ID delivery is required for the duplicate check");
const store = openStore(path);
try {
  if (store.accept({source: row.source, deliveryId: row.delivery_id, event: row.event, raw: row.raw}) !== "duplicate")
    throw new Error("Restored delivery was not deduplicated");
} finally { store.close(); }
console.log("Restored stable-ID duplicate rejected");
JS
```

Сравнить ID и состояния `pending`/`sent`, `telegram_message_id` с исходной очередью
и ожидаемыми тестовыми доставками; пустая очередь не доказывает их сохранность.
Успешный `verify.yml` проверяет наличие непустого snapshot, но не заменяет эту
проверку содержимого Restic и восстановления. Не удалять проверочный каталог до
фиксации результата. Telegram при проверке не вызывается.

При реальном восстановлении: сначала в Vault выставить
`WEBHOOK_ALERTS_SEND_ENABLED: "false"`, применить `hooks`,
затем остановить сервис `docker compose -f /opt/hooks/compose.yml stop hooks`.
Сохранить текущие `queue.sqlite` и sidecar-файлы отдельно. Скопировать проверенный
snapshot в `_data/queue.sqlite`, убрать прежние WAL/SHM после сохранения, назначить
владельца `bun:bun` (UID/GID взять из закреплённого образа), права файла `0600`.
Запустить сервис с выключенным worker, проверить HTTPS health, очередь и dedupe.
Включение отправки и сверка уже отправленных сообщений — отдельная процедура Task 10.
Не восстанавливать весь `hooks_hooks_data` поверх работающей базы.

### Фактическая проверка infra#70, 2026-10-24 UTC

- Auxiliary VPS анонимно скачала образ с пустым Docker config:
  `ghcr.io/cringe-driven-development-team/webhook-alerts@sha256:30718ed06926bff66627502361185c2ca6ebca78b20b044ce4d8bba92f12e858`
  (исходный SHA `08e7968fa0d39dd5f25fe8495c58d78006a26653`).
  `auxiliary.yml` завершился `ok=45 changed=6 failed=0`; контейнер `healthy`,
  HTTPS `/health` вернул 200, `WEBHOOK_ALERTS_SEND_ENABLED=false`, `/opt/hooks/.env`
  имеет права `0600`. `verify.yml --limit auxiliary` подтвердил непустой snapshot.
- При выключенном sender в рабочей очереди созданы две **синтетические** строки
  `cdd-restore-bf4d07eb4fd8-sent` и `cdd-restore-bf4d07eb4fd8-pending`.
  Статус `sent` и `telegram_message_id=900000001` заданы тестовым скриптом:
  реального сообщения в Telegram не было. `systemctl start restic-backup`
  завершился `Result=success`, `ExecMainStatus=0`. Restic snapshot
  `d8b71852ca25c0c3bedd7faa0f4a47a20c41f20261972cf79785b99f75b8e20e`
  создан в `2026-10-24T21:33:58Z`.
- Этот ID восстановлен через `restic restore d8b71852ca25c0c3bedd7faa0f4a47a20c41f20261972cf79785b99f75b8e20e
  --host auxiliary --target /var/tmp/hooks-restore.K7QIjA --include
  /var/lib/docker/volumes/hooks_hooks_data/_data/snapshots/queue.sqlite`.
  Проверка внутри закреплённого образа с `--network none` и выключенным sender
  дала `PRAGMA integrity_check=ok`, сохранила `pending`/`sent` и тестовый ID,
  а повторный `Store.accept` с тем же delivery ID вернул `duplicate`.
- Подписанные тестовые HTTP-доставки GitHub и Apidog дали 401 при неверной подписи,
  202 при первой доставке, 200 при повторе; GitHub вернул 409 при том же ID
  и другом теле. Это проверка локально заданного заголовка Apidog; фактический
  заголовок настоящей доставки остаётся предметом Task 10.
- Тестовые строки удалены из рабочей очереди. Повторный `systemctl start
  restic-backup` создал snapshot
  `837743262111103e2929288a77b24cb46376c94ec3c71c4a55158abdfd7b1088`
  в `2026-10-24T21:37:06Z`. Его отдельное восстановление дало
  `integrity_check=ok`, ноль тестовых доставок и ноль строк outbox. Для
  обычного восстановления выбирать этот или более поздний чистый snapshot;
  первый тестовый snapshot содержит синтетическую строку `pending`.

На момент проверки 2026-10-24 GitHub organization webhook и настоящий Apidog
target ещё не были подключены. На 2026-10-25 оба настроены; доставка от настоящего
Apidog target ещё не подтверждена. Worker остаётся выключенным
(`WEBHOOK_ALERTS_SEND_ENABLED=false`), cutover ожидает отдельной приёмки Task 10.

## Telegram proxy на auxiliary

Секретная конфигурация VLESS Reality хранится как `vault_hooks_proxy_config`
в Ansible Vault. Не печатать URI, config JSON, `.env` и token-bearing URL.
На VPS `/opt/hooks/proxy/config.json` должен иметь режим 0600, каталог —
0700, оба принадлежат UID/GID 65532. Только `hooks` и `telegram-proxy` разделяют сеть `telegram_proxy`;
порт 2080 не публикуется. Разрешён ровно `api.telegram.org`, остальные
назначения попадают в первый blackhole outbound. Caddy и очередь сохраняют прежние сети и volumes.

Проверки с VPS без раскрытия конфигурации:

```bash
sudo docker compose -f /opt/hooks/compose.yml config --quiet
sudo docker compose -f /opt/hooks/compose.yml ps
sudo docker compose -f /opt/hooks/compose.yml run --rm --no-deps telegram-proxy run -c /etc/xray/config.json -test
sudo stat -c '%a %u:%g' /opt/hooks/proxy /opt/hooks/proxy/config.json
sudo docker compose -f /opt/hooks/compose.yml port telegram-proxy 2080
```

Ожидаются валидный config, работающий sidecar, 700/600 и UID/GID 65532, отсутствие опубликованного
порта. `getMe` и тестовое сообщение выполняются отдельным скриптом, который
читает токен из Vault и выводит только HTTP status и ожидаемую идентичность бота;
не передавать токен аргументом curl или в URL командной строки.
`WEBHOOK_ALERTS_SEND_ENABLED` остаётся `false` до сверки очереди и отдельной приёмки.

При ошибке preflight исправить Vault через `ansible-vault edit`, затем повторить
`auxiliary.yml`. При pull error проверить доступность публичного registry.
При timeout проверить TCP-доступность VLESS; при handshake error сверить параметры
Reality, не включать `insecure`. При ошибке proxy Telegram-доставка не должна
переключаться на прямое соединение; очередь остаётся для повторной доставки.

Откат конфигурации: вернуть предыдущую проверенную версию `vault_hooks_proxy_config`
через Vault и выполнить `ansible-playbook auxiliary.yml --skip-tags backup` из
`ansible/`. Это перезапустит proxy при изменении JSON. Если нужно убрать sidecar,
вернуть код роли и согласованный image webhook, затем повторить playbook;
удалить остановленный orphan `telegram-proxy` через `docker compose rm -sf telegram-proxy`
с прежним compose-файлом. Не выполнять `down -v`: volumes Caddy и `hooks_data`
сохраняются. После отката проверить `/health` и snapshot/backup по разделу выше.

Локальная проверка без VPS и реальных секретов (из `ansible/`):

```bash
python -m unittest discover -s tests -v
ansible-playbook -i inventory/ci.yml --syntax-check site.yml verify.yml deploy-backend.yml auxiliary.yml
ansible-lint auxiliary.yml roles/hooks roles/restic_backup tasks/verify-hooks-snapshot.yml verify.yml
```

## Go и Postgres на VPS2 (infra#59)

Раскладка закреплена: Caddy и BFF работают на gateway, Go API, PostgreSQL и Redis —
на приватной VPS2. BFF обращается к Go по `http://<backendPrivateIp>:8080/api/v1`.
У VPS2 нет публичного IP; security group и ufw пускают SSH и 8080 только с приватного
адреса gateway. На gateway Go API и базы не запускаются.

Ansible подключается к VPS2 через gateway: `ProxyCommand` задан в `group_vars/backend`,
sshd gateway разрешает пересылку только на `VPS2:22`. Ключи gateway и VPS2 должны быть
проверены через консоль провайдера и добавлены в используемый `known_hosts`.
Не отключайте проверку host key. CI не обращается к приватному HTTP напрямую.

### Подготовка и выкатка

1. Убедиться, что VPS2 создана Pulumi, в CI inventory есть ровно один хост группы
   `backend` и один `gateway`. `app_backend_address` берётся из `ansible_host` VPS2,
   `app_gateway_address` — из `gatewayPrivateIp`. При отсутствии или неоднозначности
   VPS2 playbook завершается на проверке inventory, без выкатки на gateway.
2. После добавления VPS2 или смены её адреса сначала выполнить
   `ansible-playbook -i inventory/ci.yml site.yml --limit gateway`: это обновляет
   разрешённый SSH jump-путь и `PermitOpen` до нового `VPS2:22`.
   Затем для новой VPS2 один раз выполнить `bootstrap.yml --limit backend` и
   `site.yml --limit backend` для пользователей, SSH-hardening, ufw и Docker.
   Полная команда выбора inventory и ключей — в [Ansible README](ansible/README.md).
   Эти действия не нужны для обычной выкатки образа на уже настроенную VPS2.
3. В рамках backend#42 перевести GHCR package backend в Public и подтвердить
   анонимное скачивание выбранного опубликованного digest с пустым Docker config.
   Для deploy/rollback не нужны GHCR username/token; CI-публикация сохраняет право записи.
4. Из `ansible/` запустить выкатку с полным ref (заменить `REPLACE_WITH_64_HEX`):

   ```bash
   export ANSIBLE_VAULT_PASSWORD_FILE="$HOME/.config/cdd-vault-pass"
   BACKEND_IMAGE_REF='ghcr.io/cringe-driven-development-team/backend@sha256:REPLACE_WITH_64_HEX'
   ansible-playbook -i inventory/ci.yml deploy-backend.yml \
     -e "backend_image_ref=$BACKEND_IMAGE_REF" \
     -e "deploy_key_path=$HOME/.ssh/selectel_release" \
     -e "jump_known_hosts=$HOME/.ssh/known_hosts" \
     --ssh-common-args="-o UserKnownHostsFile=$HOME/.ssh/known_hosts -o StrictHostKeyChecking=yes"
   ```

   Backend workflow получает digest опубликованного образа и передаёт тот же ref.
   Сначала playbook скачивает его анонимно, затем записывает `.env`/Compose и запускает
   API, Postgres и Redis на VPS2 с ожиданием healthy. При отказе pull конфигурация и
   контейнеры остаются прежними. Секреты из Vault не выводятся в лог.
5. После запуска playbook сверяет локальный image ID контейнера `api` с `.Id` образа
   по выбранному digest и состояние running/healthy; затем с gateway проверяет
   `/health` VPS2 (`200`, `status: alive`) и с runner — отрицательный login через BFF
   (`400`, `BAD_REQUEST`, `invalid_credentials`). Это контракт неверных логина/пароля;
   `401` у login относится к неверному TOTP. Проверки API/health снаружи требуют
   `404` без HTML. Ошибка любого обязательного этапа означает неуспех выкатки.
6. Если требуется обновить сам BFF, использовать отдельный `deploy-bff.yml` с его
   `bff_image_ref` по digest и `bff_release_sha`. Для выкатки backend менять BFF не нужно.
   Общая проверка стенда: `ansible-playbook -i inventory/ci.yml verify.yml` с теми же
   ключами и доверенными host keys. Она включает ufw/sshd, Redis на VPS2 и S2S-доступ Go.

Для первоначального создания данных миграции Go применяются к базе VPS2 при старте.
Обычная выкатка и откат версии не переносят данные между машинами и не очищают volumes.
Исторические тома на gateway не используются для возврата сервисов и этим сценарием
не удаляются. Проверки не доказывают успешный пользовательский вход, Redis или runtime-VPS;
пользовательские сценарии проверяются отдельно после готовности связанных задач.

### Откат версии на VPS2

Выбрать предыдущий успешный выпуск и его точный digest по GitHub Deployments в рамках
backend#42. Выполнить тот же `deploy-backend.yml`, заменив `BACKEND_IMAGE_REF` на этот
подтверждённый ref текущего package `ghcr.io/cringe-driven-development-team/backend`.
Отдельное SSH-чтение Docker до выбора цели не требуется.

Откат остаётся на VPS2 и проходит те же обязательные проверки после переключения.
Он не переносит Go/PostgreSQL/Redis на gateway, не откатывает миграции и данные БД,
не выполняет `goose Down` или `docker compose down -v`. Автоматического отката нет.
Если после переключения проверки не прошли, текущее состояние нельзя объявлять
подтверждённым: изучить этап ошибки и повторить выкатку проверенного рабочего digest.

## Runtime-VPS сессий (infra#89)

Go API на VPS2 сам создаёт VPS на пользовательскую сессию (backend#57): порт в подсети
`192.168.199.0/24` с security group `runtime`, диск из образа runtime («Образ runtime-VPS»), сервер с keypair `runtime-provisioner`, без
floating IP (наружу — SNAT роутера). Настраивает её плейбуком `ansible/runtime` по SSH на приватный адрес
и ходит к агенту по mTLS на `:8443`. Инфраструктура для этого — раздел «Runtime-VPS сессий» в
`pulumi/README.md`; выключатель — `runtime_enabled` в `group_vars/all/vars.yml` (на проде `true`; при `false`
Go API получает только `RUNTIME_ENABLED=false`, ключей runtime на VPS2 нет).

**Квоты** проекта `pulumi-cellestial`, ru-9a (quota-manager API, 2026-10-29): `compute_cores` 600, занято 6;
`compute_ram` 5 000 GiB, занято 16 GiB; `volume_gigabytes_fast` 5 000 GB, занято 100; портов Neutron квота не
ограничивает. Три одновременные runtime-VPS (3 vCPU, 6 GiB, 3 × 20 GiB, 3 порта) проходят без изменений в
панели. Проверка заново — project-токен, `GET https://ru-9.cloud.api.selcloud.ru/quota-manager/v1/projects/<projectId>/quotas`.

### Включение

Предусловие: Go работает на приватной VPS2 («Go и Postgres на VPS2»); security group `runtime`
пускает SSH и 8443 только с `backendPrivateIp`. При `runtime_enabled: true` нужны заполненные
настройки и секреты runtime-VPS; `validate.yml` проверяет их до запуска приложения.

1. **Pulumi.** `pulumi preview` по `prod`: только создание — пользователь `runtime` и его пароль, security
   group `runtime` и три правила, ключи `runtime-ssh`/`runtime-ca`/`runtime-client`, сертификаты, keypair
   `runtime`, `runtime-secrets-key`; gateway, VPS2, сети и бакеты без изменений. `pulumi up` делает CI на
   мерже в main.
2. **Открытые значения** — `group_vars/all/vars.yml`, блок `runtime_*`: вписать
   `pulumi stack output runtimeSecurityGroupId`, сверить остальные с `pulumi stack output` (выход у каждой
   строки в комментарии). Образ фиксирован: `runtime_image_id` равен `infra:runtimeImageId` в
   `Pulumi.prod.yaml`, новый образ — правкой обоих в одном PR (тест `test_runtime_image`).
3. **Секреты** — в `vault.yml` из выходов, не печатая их в терминал (повторный запуск заменяет прежние
   `vault_runtime_*`):

   ```bash
   cd ansible
   VAULT=inventory/group_vars/all/vault.yml
   { ansible-vault view "$VAULT" | grep -v '^vault_runtime_'
     (cd ../pulumi && pulumi stack output --json --show-secrets) | python3 -c '
   import json, sys
   o = json.load(sys.stdin)
   for name, out in [("os_password", "runtimeOsPassword"), ("secrets_key", "runtimeSecretsKey"),
                     ("ssh_private_key", "runtimeSshPrivateKey"), ("ca_cert", "runtimeCaCert"),
                     ("ca_key", "runtimeCaKey"), ("client_cert", "runtimeClientCert"),
                     ("client_key", "runtimeClientKey")]:
       print(f"vault_runtime_{name}: {json.dumps(o[out])}")'
   } | ansible-vault encrypt --output "$VAULT.new"
   ansible-vault view "$VAULT.new" | sed -E 's/:.*//'   # имена: прежние + 7 vault_runtime_*
   mv "$VAULT.new" "$VAULT"
   ```

   Значения в старом `vault.yml` однострочные (пароли и ключи), иначе `grep -v` оставил бы хвосты —
   тогда править `ansible-vault edit`.
4. **Включить** после мержа backend#57: `runtime_enabled: true` в `vars.yml` (PR вместе с `vault.yml`),
   затем выкатка Go (`deploy-backend.yml`, CD бэка). Роль `app` пишет на VPS2 `/opt/cellestial/runtime-secrets`
   (каталог `0500`, файлы `0400`, владелец uid 10001 — пользователь `app` образа), каталог заданий
   `/var/lib/cellestial/runtime` (`0700`, uid 10001) и плейбук `/opt/cellestial/runtime-playbook`; Compose
   монтирует их в контейнер Go API: `/run/secrets/runtime` и `/app/deploy/runtime` — read-only.
   `RUNTIME_ENV_NAME` — `runtime_env_name` (`production`), обязателен для openstack. Прочие `RUNTIME_*`
   backend (`RUNTIME_PYTHON_IMAGE`, `RUNTIME_MAX_ACTIVE`, таймауты) — `runtime_extra_env`.

### Проверка

```bash
# Локально: клиентский сертификат подписан runtime CA
cd pulumi
openssl verify -CAfile <(pulumi stack output runtimeCaCert) <(pulumi stack output runtimeClientCert)  # OK

# На VPS2 (cd /opt/cellestial): имена переменных — без значений; файлы только на чтение
docker compose exec api sh -c 'env | grep ^RUNTIME_ | cut -d= -f1 | sort'
docker compose exec api ls -l /run/secrets/runtime         # -r-------- app app
docker compose exec api sh -c 'touch /run/secrets/runtime/x'   # Read-only file system
```

Облако — учётными данными `runtime-provisioner` с VPS2 (`openstack` CLI в venv или контейнере; `OS_AUTH_URL`,
`OS_USERNAME`, `OS_PASSWORD`, `OS_USER_DOMAIN_NAME`/`OS_PROJECT_DOMAIN_NAME` = номер аккаунта,
`OS_PROJECT_ID`, `OS_REGION_NAME=ru-9` — из выходов `runtime*`):

```bash
openstack port create --network <runtimeNetworkId> --fixed-ip subnet=<runtimeSubnetId> \
  --security-group <runtimeSecurityGroupId> runtime-check
openstack volume create --size 20 --type fast.ru-9a --image <runtimeImageId> \
  --availability-zone ru-9a runtime-check
openstack server create --flavor <runtimeFlavorId> --key-name runtime-provisioner \
  --port runtime-check --volume runtime-check --availability-zone ru-9a --wait runtime-check
# с VPS2 — входит ключом provisioner; с gateway — таймаут (security group)
docker compose exec api ssh -i /run/secrets/runtime/ssh_key -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile=/dev/null root@<приватный IP> true
openstack server delete --wait runtime-check && openstack volume delete runtime-check \
  && openstack port delete runtime-check
```

Плейбук `ansible/runtime` руками не запускается: его вызывает Go API с `inventory.json`/`vars.json`
операции. Задачи с токеном, ключами и сертификатами в нём и в роли `app` — `no_log`.

### Ротация

Каждая ротация: `pulumi up --replace '<URN>'` (URN — `pulumi stack --show-urns`), затем шаг 3
«Включение» (новые значения в vault) и выкатка Go — хендлер роли `app` пересоздаёт контейнер API, чтобы
он перечитал файлы.

- **SSH-ключ provisioner** — `--replace` у `tls:index/privateKey:PrivateKey::runtime-ssh`: keypair
  `runtime-provisioner` пересоздаётся. Уже созданные runtime-VPS хранят старый публичный ключ — их
  повторная настройка не пройдёт; они живут одну сессию, удалить через backend.
- **CA** — `--replace` у `runtime-ca` (ключ): новый CA и клиентский сертификат. Агенты работающих
  VPS доверяют старому CA — делать без активных сессий (или удалить их VPS).
- **Клиентский сертификат** (2 года) — `--replace` у `runtime-client`; CA прежний, работающие VPS его
  принимают. Истёкший сертификат провайдер `tls` пересоздаёт на ближайшем `up` сам — после такого `up`
  тоже шаг 3.
- **Пароль пользователя** — `--replace` у `random:index/randomPassword:RandomPassword::runtime-serviceuser`.
- **`RUNTIME_SECRETS_KEY`** — `--replace` у `runtime-secrets-key`: токены агентов в БД, зашифрованные
  старым ключом, перестают читаться — только без активных окружений.
- **Утечка vault** — все пять пунктов выше.

### Образ runtime-VPS (infra#134)

Runtime-VPS создаются из приватного образа `cellestial-runtime-<IMAGE_VERSION>` на базе Selectel
`Ubuntu 26.04 LTS 64-bit` (Python 3.14; агенту нужен ≥ 3.13). В образе — всё, что не зависит от сессии:
apt-пакеты и Docker CE фиксированных версий; агент (wheel, `requirements.txt`, systemd unit) из `/app/agent`
образа backend `BACKEND_IMAGE` с venv `/opt/cellestial-agent/venv`; слои образов kernel Python и R; не
зависящие от сессии файлы ролей runtime; маркер `/etc/cellestial/runtime-image`. Состав — только в
`runtime-image/versions.env`; пакеты Ubuntu ставятся из `snapshot.ubuntu.com` на `UBUNTU_SNAPSHOT`, поэтому
повторная сборка даёт те же версии. Плейбук сессии проверяет маркер (`runtime_host_image_version`) и ставит
wheel и requirements агента, только если backend прислал другие.

**Пересборка** — при смене состава (новые версии пакетов, агент в новом релизе backend, образы kernel):

1. Поправить `runtime-image/versions.env`, поднять `IMAGE_VERSION`. Агент — `BACKEND_IMAGE` на digest
   выкаченного релиза backend (`BACKEND_IMAGE_REF` в `.env` VPS2), образы kernel — его
   `RUNTIME_PYTHON_IMAGE`/`RUNTIME_R_IMAGE`. В том же PR —
   `runtime_host_image_version` в `ansible/runtime/roles/runtime_host/defaults/main.yml` (тест
   `test_runtime_image`).
2. Собрать образ учёткой с правами на проект runtime (~10 мин):

   ```bash
   source pulumi/bootstrap/env.sh
   OS_REGION_NAME=ru-9 OS_PROJECT_ID=$(cd pulumi && pulumi stack output runtimeProjectId) \
     scripts/build-runtime-image.sh
   ```

   Скрипт создаёт временные том, сервер и keypair, ждёт выключения сервера после cloud-init, загружает
   том в образ и проверяет пробным томом создание в `ru-9a`; временные ресурсы удаляет сам. Ошибка
   сборки — строки `cellestial-image` из console log сервера.
3. Id образа — в `infra:runtimeImageId` (`Pulumi.prod.yaml`) и `runtime_image_id` (`vars.yml`) одним PR.
4. После мержа — выкатка Go (`deploy-backend.yml`): `RUNTIME_IMAGE_ID` и плейбук попадают на VPS2 только ею.
   Новые сессии создаются из нового образа; старый образ удалить после проверки сессии.


## Переключение уведомлений frontend на webhook

Подготовленная валидация не включает новый sender: граница release-activation
изначально отсутствует в SQLite. Установить TELEGRAM_RELEASES_TOPIC_ID и читающий
GITHUB_API_TOKEN через действующий Vault; бот, чат, proxy и Project PAT не переносить
из их текущих областей ответственности. Project PAT остаётся только у Board Actions.

Порядок после проверки и разрешения на merge/deploy:

1. Влить сервис и проверить опубликованный commit image. Получить реальный registry
   digest, проверить анонимный pull и закрепить его/source tag в hooks defaults.
   Не подставлять локальный Docker image ID вместо registry manifest digest.
2. Влить frontend producers/removal, проверить push/main CI, публикацию и course-sync.
   Проверить отсутствие выполняющихся старых release notification jobs и старых
   CD/Rollback send steps. Не отменять неизвестную доставку без проверки Telegram.
3. Снять существующий verified SQLite snapshot и обновить вспомогательную VPS
   существующим auxiliary.yml. Это обновление webhook-сервиса, не CD фронта.
   /health должен ответить 200; обычные GitHub/Apidog алерты продолжают работать.
4. У org hook 702764072 добавить deployment_status и workflow_job к текущему набору
   events, сохранив URL, secret, SSL verification и остальные подписки.
5. Зафиксировать текущую UTC-границу, затем в /opt/hooks выполнить:

   ```bash
   release_cutover_utc=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
   sudo docker compose exec -T hooks bun run src/cli.ts release-activate "$release_cutover_utc"
   ```

   Граница неизменяема; повтор того же значения идемпотентен. До неё release-события
   не рассылаются автоматически. Запоздавшие enqueue дополнительно проверяют live
   entry/main и время постановки; merged/closed/re-enqueued сообщения пропускаются.
6. Адресно сверить события промежутка между удалением старого sender и activation
   с прежним журналом и Telegram. Только для подтверждённо неотправленной source
   delivery разрешён release-recover <raw-delivery-id>; массового replay нет.
7. На разрешённом контрольном PR/run сопоставить GitHub delivery time, HTTP 202,
   outbox key, message_id и время принятия Telegram. Повтор delivery не должен
   отправлять второй результат. Отдельно проверить работу Board.

Release uncertain после timeout/5xx/ошибки записи не повторяется автоматически:
сначала проверить Telegram, затем при установленном отсутствии сообщения можно
вызвать существующий retry <outbox-id>. При release_skipped попытку не возобновлять.
Для выкатки фронта ради проверки требуется отдельное поручение владельца.

До публикации нового image этот runbook является подготовкой, действующий digest
в defaults не изменяется. Секреты и raw payload в отчёт о переключении не включать.

### Приёмка переключения 2026-11-01 (UTC)

Переключение выполнено: [.github#48](https://github.com/Cringe-Driven-Development-Team/.github/pull/48),
[frontend#136](https://github.com/Cringe-Driven-Development-Team/frontend/pull/136),
[infra#102](https://github.com/Cringe-Driven-Development-Team/infra/pull/102).
Действующий image — `ghcr.io/cringe-driven-development-team/webhook-alerts@sha256:103ea0dee401b9a65f52358b05911bf08083bc2228414ddfd460ecfd1cce6bb2`,
source tag — `sha-242507b884c31843bfcdfd6a8f490d2da768375f`. Проверены registry
manifest, анонимный pull на auxiliary и фактический image запущенного контейнера.

- Post-merge [.github Checks](https://github.com/Cringe-Driven-Development-Team/.github/actions/runs/41356812343)
  и [публикация image](https://github.com/Cringe-Driven-Development-Team/.github/actions/runs/41356812555) — success.
- [Frontend CI](https://github.com/Cringe-Driven-Development-Team/frontend/actions/runs/41357223302)
  — Check, Publish release и course-sync success. Team/course main совпали:
  `542a571db9fe7109c0961dc3680ce4297851c86a`.
- [Infra CI](https://github.com/Cringe-Driven-Development-Team/infra/actions/runs/41358553255)
  — тесты, apply (`auxiliary.yml`) и `verify.yml` success; контейнер healthy,
  публичный `/health` — HTTP 200. Предварительный `auxiliary.yml --check --diff`
  завершился с `ok=57 changed=5 failed=0`.
- До обновления создан verified snapshot
  `/data/snapshots/pre-release-webhooks-20261101.sqlite`. После миграции SQLite
  `2 → 3` проверены `integrity_check=ok` и все 613 прежних sent-карточек:
  HTML, тема, кнопки и Telegram message IDs совпали со snapshot.
- В Vault добавлены только `GITHUB_API_TOKEN` и `TELEGRAM_RELEASES_TOPIC_ID=3375`;
  прежние значения сверены и сохранены. Отдельный fine-grained PAT читает только
  frontend; его работа проверена из контейнера. `/opt/hooks/.env` имеет права
  `0600 root:root`; временный локальный файл передачи токена удалён.
- У org hook `702764072` добавлены `deployment_status` и `workflow_job`.
  Прежние события, URL, JSON content type и SSL verification сохранены;
  поле secret при обновлении не передавалось.
- Неизменяемая граница activation — **`2026-11-01T22:55:47Z`**
  (`1793573747000` ms). Перед ней проверено завершение старых sender jobs.

Для адресного восстановления готовности выпуска `542a571` проверены успешный
CI и отсутствие его уведомления в прежнем журнале `release-automation`.
Последний старый sender завершился в `22:27:10Z`, запись артефактов
`7480076686` создана в `22:28:28Z` новым producer. Её подтверждённый success
повторно зарегистрирован как status `21129679491` с `auto_inactive=false`;
это контроль доставки существующего выпуска, без сборки и CD фронта.

GitHub delivery `3851670442712547328`, GUID
`88ef9220-d647-11f1-9386-862eb90a5e84`, принята в `22:56:56.661Z`:
HTTP **202**, duration **0.59 s**. Первичная подготовка raw `1371` завершилась
без outbox, поскольку source предшествует activation. После этого выполнен
только `release-recover 1371` (`22:57:42.373911Z`).

Outbox **627** отправлен в `22:58:07.433335472Z` с HTTP 200,
Telegram **message_id 8866**, topic **3375**. Проверены читаемое название
`WEB-135` и кнопка PR #136. Ключ `ready` содержит SHA выпуска, а
`operation_key` — `result:Cringe-Driven-Development-Team/frontend:publish:41357223302:1`.
В этот момент [ручной Board run](https://github.com/Cringe-Driven-Development-Team/frontend/actions/runs/41359641614)
ещё выполнялся; затем завершился success. Карточка frontend#135 осталась
`Ready for release`.

Native redelivery `3851670746724040704` с тем же GUID принята в
`22:59:18.218Z`: HTTP **200**, duration **0.76 s**. Остались ровно один release
outbox и тот же message ID; дубля нет. После проверки обновлён verified
snapshot `/data/snapshots/queue.sqlite`. Реальные уведомления начала и результата
CD/rollback этой приёмкой не запускались; их локальные сценарии проверены тестами.

## Восстановление API: полный сброс БД приложения (infra#112)

Решение владельца (backend#79, ревью backend#80): старые тестовые блокноты из S3 не переносятся.
БД приложения на VPS2 сбрасывается **один раз** полностью: пользователи, блокноты, настройки 2FA,
runtime-сессии и прочие данные приложения не сохраняются, после восстановления регистрируемся
заново. S3-бакеты и объекты не удаляются. Сброс не входит в CD и не выполняется при старте API.
Что и почему сбрасывается —
[backend/docs/database-reset.md](https://github.com/Cringe-Driven-Development-Team/backend/blob/main/docs/database-reset.md).

Production-операции выполняет оператор с авторизованным SSH-доступом к VPS2 и паролем Vault.
Не менять SSH host key самостоятельно. Логи и `.env` содержат секреты: в отчёт переносить только
безопасные коды и выводы, не публиковать `.env`, полный `docker inspect` или `docker compose config`.

### Диагностика и постоянный ключ

На VPS2 от root, из `/opt/cellestial`:

```bash
cd /opt/cellestial
docker compose ps -a
docker inspect --format '{{.Config.Image}} {{.State.Status}}' cellestial-api-1
docker compose logs --tail=100 api
```

`startup_config` — отказ проверки настроек (в том числе отсутствующий `TOTP_ENCRYPTION_KEY`),
`totp_init` — отказ инициализации TOTP. После неудачного CD восстанавливаются `.env` и `compose.yml`,
но контейнеры не откатываются.

`vault_totp_encryption_key` лежит в зашифрованном `vault.yml` (добавлен в PR infra#113 впервые:
production с 2FA не стартовал, сохранённых TOTP-секретов не было). Это постоянный ключ: не
перевыпускать, хранить вместе с резервной копией БД. Сброс БД ключ не меняет.

### Остановка, облачные ресурсы и backup

```bash
set -eu
cd /opt/cellestial
docker compose stop api
```

Убедиться, что другого API или воркера, пишущего в ту же БД, нет. До конца операции не запускать
CD backend и frontend и не мержить в `main` backend и infra: параллельная выкладка поднимет API
посреди сброса. До удаления данных найти
облачные ресурсы runtime-сессий — после сброса таблица пустая, и reconcile их уже не удалит:

```bash
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
SELECT session_id, state, server_id, volume_id, port_id FROM runtime_environments
WHERE state <> 'DELETED' OR server_id IS NOT NULL OR volume_id IS NOT NULL OR port_id IS NOT NULL;
SQL
```

Удалить найденные ресурсы и проверить по меткам `cellestial_runtime=1`,
`cellestial_env=production`, что серверов, томов и портов не осталось — порядок и команды
`openstack` в разделе «Runtime-VPS сессий» и в
[backend/docs/runtime-provisioning.md, «Ручная очистка»](https://github.com/Cringe-Driven-Development-Team/backend/blob/main/docs/runtime-provisioning.md#ручная-очистка).

Резервная копия перед сбросом:

```bash
install -d -m 0700 backups
umask 077
reset_backup="backups/before-reset-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$reset_backup"
docker compose exec -T postgres pg_restore --list < "$reset_backup" > /dev/null
```

Пробное восстановление в отдельную БД: копия читается целиком, в ней есть таблицы приложения.

```bash
docker compose exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" reset_restore_check && pg_restore -U "$POSTGRES_USER" -d reset_restore_check --exit-on-error' < "$reset_backup"
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d reset_restore_check -Atc "SELECT count(*) FROM goose_db_version"'
docker compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" reset_restore_check'
```

Ошибка любой команды — остановиться, БД приложения не трогать.

### Сброс

```bash
# Пустая БД с прежним владельцем; схему создаст API миграциями goose при старте
docker compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"'
# Сессии, версии сессий и счётчики попыток входа: старые токены и cookie перестают действовать
# Пароль redis-cli берёт из REDISCLI_AUTH контейнера; API использует базу REDIS_DB=0.
# -e: ошибка Redis (WRONGPASS, NOAUTH) даёт ненулевой код и останавливает процедуру до запуска API
docker compose exec -T redis redis-cli -e -n 0 FLUSHDB
test "$(docker compose exec -T redis redis-cli -e -n 0 DBSIZE)" = 0
```

API остаётся остановленным до CD. Миграции Down не выполнять, старый API не запускать.

### CD и отчёт приёмки

После сброса БД и мержа backend#80 выполнить выкатку образа из `main` по полному
`backend_image_ref=ghcr.io/cringe-driven-development-team/backend@sha256:<64 hex>`
через `deploy-backend.yml` (раздел «Go и Postgres на VPS2»). Тег вместо digest не принимается.
Если CD backend ещё передаёт `backend_image_tag`, сначала подготовить его переход на
новый контракт (backend#42); не запускать такой CD в расчёте восстановить API.
После готовности Go повторно запустить CD frontend.
Дождаться успешного healthcheck и проверить:

- `docker compose ps`, `/health` по внутреннему маршруту API, в логах нет `startup_config`,
  `totp_init`, `database_migrate`;
- новая регистрация и вход; старый логин не работает;
- создание, редактирование и повторное открытие новых блокнотов;
- загрузка нового аватара и его отображение;
- облачных ресурсов прежних runtime-сессий нет.

В отчёте по задаче: дата, оператор, полный image ref по digest и исходный SHA, безопасный код прежнего отказа, место хранения
backup и результат пробного восстановления, удалённые облачные ресурсы, URL успешных CD backend и frontend, результаты проверок выше.
Сохранность прежних пользователей и блокнотов не проверяется: они удалены намеренно.


### Состояние релизов VPS3: первая установка и восстановление

`/opt/hooks/releases.json` имеет schema `version: 1`, два ключа `services.githubbot`
и `services.webhook-alerts`, у каждого `desired` и `confirmed` (Release либо null).
Release содержит `service`, `image_ref` (полный GHCR repository + `@sha256:...`),
`source_sha` (40 lowercase hex), положительные целые `publisher_run_id` и
`publisher_run_attempt`. Файл не содержит секретов; writer сохраняет его атомарно
через Ansible copy с root:root и 0600.

На пустой VPS `auxiliary.yml` принимает явно переданные `hooks_initial_releases` —
список ровно из двух проверенных Release, по одному для каждого сервиса. Loader
проверяет их и строит desired с confirmed=null в памяти, выставляя
`hooks_release_state_needs_initialization=true`. На существующей VPS loader читает
файл и выставляет false. `--check` читает состояние и планирует без записи.
Первичную запись после image/config gates и подтверждение контейнеров выполняет
executor; до включения этой части bootstrap не считается завершённым.

Если Compose уже существует, а состояния нет, обычный apply останавливается.
Для read-only migration preview разрешён `--check` с двумя явными
`hooks_initial_releases`: оба image_ref должны точно совпасть с образами сервисов
`githubbot` и `hooks` из текущего `docker compose config`. Несовпадение останавливает
preview; Compose и state не записываются.
Используйте отдельную `operation=initialize` штатного executor с двумя проверенными
Release; не удаляйте Compose для обхода проверки и не импортируйте версии неявно.
При повреждении состояния восстановите проверенный файл/выполните initialize через
тот же executor. Не подставляйте старые digest из Git и не используйте latest.
После неуспешного deploy desired остаётся выбранным релизом, confirmed — последним
проверенным; повторный apply использует desired, автоматического rollback нет.
Все записи выполняются через очередь executor `infra-production`; параллельный
локальный Ansible вне очереди не поддерживается.

## VPS3 releases

Use Actions → **Auxiliary deploy** on `main` for administrative operations; run no local
Ansible writer alongside the queue. `operation=deploy` takes `service`, immutable
`image_ref`, full `source_sha` and `request_id` in the form
`service:publisher_run_id:publisher_run_attempt:caller_run_id:caller_run_attempt`.
Keep `initial_releases_json` empty. Only successful push/main attempts of the allowlisted
publisher workflows and their public GHCR digest/source labels are accepted. Metadata
and registry failures stop the operation. Older or diverging source commits are rejected.

For an existing VPS3 with Compose and healthy running containers but no release state,
run `operation=initialize` with `initial_releases_json` containing exactly two explicit
Release objects (`service`, `image_ref`, `source_sha`, `publisher_run_id`,
`publisher_run_attempt`), one per service. Leave all deploy fields empty. Find the actual
image digests and successful publisher attempts first; no latest tag or guessed run is
accepted. The operation verifies Compose, container Config.Image, local ImageID, health
and HTTPS before writing desired=confirmed, root:root 0600. Existing state must match
completely. A new VPS without Compose uses explicit bootstrap via `hooks_initial_releases`
instead; initialize does not provision it.

For migration preview only, an administrator may set repository variable
`AUXILIARY_INITIAL_RELEASES` to the same explicit pair. The PR auxiliary `--check` step
validates publisher metadata/public registry and supplies it as `hooks_initial_releases`.
Remove this temporary variable after real initialization. Production never consumes it;
ordinary apply without saved state remains an error.

A failed deploy can leave desired ahead of confirmed. Inspect the workflow summary and
failed step, repair the cause, then dispatch the same release again through the executor.
No rollback occurs automatically. Caller cancellation does not cancel the dispatched
executor; inspect its own run before retrying. Shared networks, SQLite volumes and the
other service remain in place during a targeted deploy.
