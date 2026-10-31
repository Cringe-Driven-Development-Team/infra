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

Роль `hooks` разворачивает публичный GHCR image по registry manifest digest из
`ansible/roles/hooks/defaults/main.yml`. Соответствующий SHA-тег хранится в
`hooks_image_source_tag` для трассировки; Compose использует только `hooks_image`.
Локальный Docker image ID нельзя использовать вместо manifest digest.

Перед первой выкаткой:

1. Через `ansible-vault edit inventory/group_vars/all/vault.yml` добавить
   `vault_hooks_env`: непустые `GITHUB_WEBHOOK_SECRET`, `APIDOG_SIGNATURE_KEY`,
   `APIDOG_SIGNATURE_HEADER` (фактическое имя заголовка Apidog). Это обязательные
   параметры даже при выключенном sender. `DATABASE_PATH=/data/queue.sqlite`,
   `PORT=8080`, `WEBHOOK_ALERTS_SEND_ENABLED=false` заданы ролью. Значения env в
   Vault хранить строками; не включать worker до приёмки Task 10. Его дополнительные
   переменные и переключение описаны в [runbook сервиса](https://github.com/Cringe-Driven-Development-Team/.github/blob/main/services/webhook-alerts/README.md).

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

Схема после переключения: браузер → Caddy → BFF на gateway (публичный IP) → по приватной сети Go API на
VPS2 (`http://<backendPrivateIp>:8080`, Bearer и `X-BFF-Key`) → Postgres и Redis там же. У VPS2 нет
публичного IP; её security group пускает SSH и 8080 только с приватного адреса gateway
(`gatewayPrivateIp`), ufw на VPS2 — второй слой с тем же источником. На gateway API не публикуется:
её floating IP — 1:1 DNAT, опубликованный на приватном адресе порт был бы открыт в интернет.
Ansible ходит на VPS2 через gateway (`ProxyCommand` из `group_vars/backend`); sshd на gateway пускает
пересылку только на `VPS2:22` (`AllowTcpForwarding local`, `PermitOpen`).

**Данные PostgreSQL с gateway не переносятся.** На VPS2 — новые пустые тома Postgres и Redis, схему
создают миграции Go при первом старте. Пользователи и блокноты, созданные на gateway, в новой базе не
появляются; тома `cellestial_postgres_data` и `cellestial_redis_data` на gateway сохраняются для отката
и не удаляются.

Предусловия: backend#41 (проверка `X-BFF-Key` в Go) выкачен; BFF выкачен (`BFF_IMAGE_REF` в `.env`
gateway). Между шагами 5 и 6 сайт работает со старой базой на gateway, после шага 6 — с новой.

Переменную раскладки передавать JSON (`-e '{"app_backend_split": true}'`): так она — boolean. Роли и
плейбуки и так приводят её через `| bool`, поэтому `-e app_backend_split=true` тоже работает.

1. **VPS2.** `pulumi preview` показывает только создание: security group `backend` и её правила, порт,
   диск и сервер `backend`, без замены и удаления существующих ресурсов. `pulumi up` (на мерже в main
   это делает CI). `pulumi stack output backendPrivateIp gatewayPrivateIp`; `gatewayPrivateIp` совпадает
   с `app_gateway_address` в `group_vars/all/vars.yml`.
2. **Inventory.** Раскомментировать группу `backend` в `inventory/ci.yml` с `backendPrivateIp`
   (dynamic inventory найдёт VPS2 по `metadata.role` сам).
3. **Jump.** `ansible-playbook site.yml --limit gateway`: sshd на gateway разрешает пересылку на VPS2.
   Затем host key VPS2 — в `~/.ssh/known_hosts`, сверенный с консолью сервера в панели:
   `ssh -o ProxyCommand="ssh -W %h:%p deploy@cellestial.ru" root@<backendPrivateIp>` (первое подключение).
4. **Базовая настройка VPS2.** `ansible-playbook bootstrap.yml --limit backend`, затем
   `ansible-playbook site.yml --limit backend` (users, hardening, ufw, Docker). Проверка доступа снаружи:
   с управляющей машины `nc -vz <backendPrivateIp> 22` не проходит (нет маршрута), публичного IP у
   сервера в панели нет.
5. **Go на VPS2.** `ansible-playbook deploy-backend.yml -e '{"app_backend_split": true}'
   -e "backend_image_tag=$BACKEND_IMAGE_TAG"` с `GHCR_USERNAME`/`GHCR_TOKEN` (образ Go частный); тег — текущий
   `BACKEND_IMAGE_TAG` из `.env` gateway или новый. Плейбук выкатывает Go, Postgres и Redis на VPS2 с
   пустыми томами; gateway он не трогает (там Go продолжает работать со старой базой). Проверка с
   gateway: `curl -fsS http://<backendPrivateIp>:8080/health` отвечает, а в базе VPS2 только схема:
   `docker compose exec postgres psql -U postgres -d app_db -c '\dt'` на VPS2 — таблицы есть, в
   `users` ноль строк.
6. **Переключение BFF.** `ansible-playbook deploy-bff.yml -e '{"app_backend_split": true}'
   -e "bff_image_ref=$BFF_IMAGE_REF" -e "bff_release_sha=$BFF_RELEASE_SHA"` (значения — `BFF_IMAGE_REF` и
   `BFF_RELEASE_SHA` из `.env` gateway): на gateway остаются Caddy и BFF с `GO_API_URL` на VPS2,
   контейнеры api, postgres и redis на gateway удаляются, их тома остаются.
7. **Проверка.** `verify.yml -e '{"app_backend_split": true}'`: ufw и sshd обеих машин, Redis на VPS2,
   с gateway Go отвечает с верным `X-BFF-Key` (401 без Bearer) и даёт `403 s2s_forbidden` без ключа и с
   неверным. Руками на cellestial.ru — регистрация (база пустая), вход, создание, чтение и изменение
   блокнота.
8. **Закрепить.** PR: `app_backend_split: true` в `group_vars/all/vars.yml` и группа `backend` в
   `inventory/ci.yml`. После него `deploy-backend.yml` (CD бэка) выкатывает Go на VPS2; CD бэка
   передаёт `-e jump_known_hosts=<файл>` и держит в `SSH_KNOWN_HOSTS` ещё и host key VPS2.

Откат — на сохранённые тома gateway:

- **До шага 6:** ничего делать не нужно — BFF ходит в Go на gateway. VPS2 можно очистить и повторить
  шаг 5.
- **После шага 6:** `ansible-playbook deploy-backend.yml -e '{"app_backend_split": false}'
  -e "backend_image_tag=$BACKEND_IMAGE_TAG"` с GHCR credentials. Плейбук идёт на gateway и поднимает там
  Go, Postgres и Redis на прежних томах, а BFF из `.env` gateway — с `GO_API_URL` на `api`; тег Go
  передаётся явно, потому что в `.env` gateway раздельной раскладки его нет. `deploy-bff.yml` не нужен.
  **Записи, созданные на VPS2 после переключения, в старую базу gateway автоматически не попадают**: на
  gateway снова данные на момент шага 6.
- Старые тома на gateway удалять только отдельным решением после стабильной работы на VPS2.

## Runtime-VPS сессий (infra#89)

Go API на VPS2 сам создаёт VPS на пользовательскую сессию (backend#57): порт в подсети
`192.168.199.0/24` с security group `runtime`, диск Debian 13, сервер с keypair `runtime-provisioner`, без
floating IP (наружу — SNAT роутера). Настраивает её плейбуком `ansible/runtime` по SSH на приватный адрес
и ходит к агенту по mTLS на `:8443`. Инфраструктура для этого — раздел «Runtime-VPS сессий» в
`pulumi/README.md`; выключатель — `runtime_enabled` в `group_vars/all/vars.yml` (по умолчанию `false`:
Go API получает только `RUNTIME_ENABLED=false`, ключей runtime на VPS2 нет).

**Квоты** проекта `pulumi-cellestial`, ru-9a (quota-manager API, 2026-10-29): `compute_cores` 600, занято 6;
`compute_ram` 5 000 GiB, занято 16 GiB; `volume_gigabytes_fast` 5 000 GB, занято 100; портов Neutron квота не
ограничивает. Три одновременные runtime-VPS (3 vCPU, 6 GiB, 3 × 20 GiB, 3 порта) проходят без изменений в
панели. Проверка заново — project-токен, `GET https://ru-9.cloud.api.selcloud.ru/quota-manager/v1/projects/<projectId>/quotas`.

### Включение

Предусловие: Go на VPS2 (`app_backend_split: true`, «Go и Postgres на VPS2») — security group `runtime`
пускает SSH и 8443 только с `backendPrivateIp`; при `runtime_enabled: true` без раскладки `validate.yml`
падает.

1. **Pulumi.** `pulumi preview` по `prod`: только создание — пользователь `runtime` и его пароль, security
   group `runtime` и три правила, ключи `runtime-ssh`/`runtime-ca`/`runtime-client`, сертификаты, keypair
   `runtime`, `runtime-secrets-key`; gateway, VPS2, сети и бакеты без изменений. `pulumi up` делает CI на
   мерже в main.
2. **Открытые значения** — `group_vars/all/vars.yml`, блок `runtime_*`: вписать
   `pulumi stack output runtimeSecurityGroupId`, сверить остальные с `pulumi stack output` (выход у каждой
   строки в комментарии; `runtimeImageId` меняется при обновлении образа Selectel).
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
