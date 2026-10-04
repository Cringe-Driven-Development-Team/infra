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

ansible-vault view inventory/group_vars/all/vault.yml   # пароль подходит: видны vault_postgres_password, vault_jwt_secret

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
`cellestial.ru.`, `infra:avatarsDomain` — отдельная зона `avatars.cellestial.ru.`, оба с привязкой к
CDN-ресурсу и бакету аватарок. Сертификаты к ним — руками в панели (CDN → ресурс → сертификаты; S3 →
SSL-сертификаты); после выпуска:

```bash
curl -I "https://$(pulumi stack output cdnCustomDomain)/index.html"
curl -I "https://$(pulumi stack output avatarsCustomDomain)/<ключ аватарки>"
```

Что автоматом, а что руками — `CLAUDE.md`.

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
