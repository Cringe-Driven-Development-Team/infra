# Pulumi: одна VPS + S3 + DNS в Selectel

Создаёт: проект, сервисного пользователя проекта (+ S3-ключи), keypair, приватную сеть с роутером
(нужна для floating IP), VPS (floating IP, роль gateway: Caddy, позже Go API и Postgres в Docker Compose),
S3-бакет через @pulumi/aws (публичное чтение — при `infra:s3PublicRead=true`), A-запись домена на VPS,
приватный бакет ноутбуков пользователей с отдельным сервисным пользователем и S3-ключом для Go API.

Двухсерверная схема (gateway + backend) заморожена в git в варианте `bff` документации; стек — одна VPS.

## Установка

Нужны [Pulumi CLI](https://www.pulumi.com/docs/install/) и [bun](https://bun.sh).

```bash
cd pulumi
pulumi install     # генерирует SDK провайдера selectel в sdks/ и ставит зависимости через bun
```

`packagemanager: bun` зафиксирован в `Pulumi.yaml`: без него на чистом клоне `pulumi install`
выберет npm и создаст `package-lock.json`. `sdks/`, `node_modules/` — в `.gitignore`.

## Учётные данные

Нужен **свой сервисный пользователь аккаунта** (панель → Управление доступом → Сервисные пользователи)
с ролями `member` (на аккаунт) и `iam.admin` — второй нужен, чтобы Pulumi создал сервисного
пользователя проекта. Логин и пароль — в `~/.config/selectel.env` (`bootstrap/README.md`, «Один раз
на человека»); `source bootstrap/env.sh` отдаёт их провайдеру через `OS_USERNAME`/`OS_PASSWORD`.
Без `env.sh` программа сразу падает с подсказкой.

## Backend и стек

Стейт — в бакете `cdd-infra-state` (создаёт bootstrap-стек, `bootstrap/README.md`), префикс
`prod/`, стек `prod`. Доступ — личный S3-ключ стейта из `~/.config/selectel.env`.

Бэкенд прибит в `Pulumi.yaml` (`backend.url`): `pulumi` в этом каталоге всегда работает со стейтом
`s3://cdd-infra-state/prod`, глобальный `pulumi login` на него не влияет и не нужен. Перебивает
только `PULUMI_BACKEND_URL` — её не задавать. Проверка: `pulumi whoami -v`.

```bash
source bootstrap/env.sh       # AWS_* (личный ключ стейта), PULUMI_CONFIG_PASSPHRASE, OS_*
pulumi stack select prod      # с нуля: pulumi stack init prod --secrets-provider passphrase
```

`Pulumi.prod.yaml` коммитится: логина и пароля в нём нет, только несекретный конфиг и
`encryptionsalt`. Остальные `Pulumi.*.yaml` — в `.gitignore`; шаблон для стека с нуля —
`Pulumi.prod.yaml.example`.

## Конфиг

```bash
# только для стека с нуля; логин/пароль Selectel — из selectel.env, не из конфига
pulumi config set selectel:domainName <номер-аккаунта>
pulumi config set selectel:authUrl    https://cloud.api.selcloud.ru/identity/v3/
pulumi config set selectel:authRegion ru-9

pulumi config set infra:name             pulumi-<ваш-ник>   # уникально в общем аккаунте
pulumi config set infra:serviceUserName  cellestialSystemUser # сервисный пользователь проекта
pulumi config set infra:pool             ru-9
pulumi config set infra:zone             ru-9a
pulumi config set infra:volumeType       fast.ru-9a
# Флейвор единственной VPS — не меньше SL1.2-8192 (на ней Caddy, Go API и Postgres);
# актуальные флейворы пула: ./scripts/list-flavors.sh (из pulumi/, после source bootstrap/env.sh)
pulumi config set infra:gatewayFlavorName SL1.2-8192
# # Boot-диск VPS (под будущий Postgres), по умолчанию 20 ГБ:
# pulumi config set infra:gatewayVolumeSize 20
pulumi config set infra:imageName        "Ubuntu 24.04 LTS 64-bit"
pulumi config set infra:sshPublicKey     "$(cat ~/.ssh/selectel_release.pub)"

# DNS: A-запись domain → publicIp VPS
pulumi config set infra:domain       cellestial.ru
pulumi config set infra:dnsZone      cellestial.ru.
pulumi config set infra:dnsProjectId <id проекта с зоной>

# Object Storage
pulumi config set infra:s3Pool   ru-1
pulumi config set infra:s3Bucket <имя-бакета>
# Приватный бакет ноутбуков (.ipynb) в том же пуле и сервисный пользователь Go API
pulumi config set infra:notebooksBucket <имя-бакета>
# pulumi config set infra:notebooksUserName cellestialNotebooksUser   # по умолчанию; уникально в аккаунте
# Публичный бакет аватарок в том же пуле
pulumi config set infra:avatarsBucket <имя-бакета>
```

`pool`, `zone` и `volumeType` — из одного региона VPS (`ru-9` / `ru-9a` / `fast.ru-9a`).
`s3Pool` задаёт endpoint `https://s3.<pool>.storage.selcloud.ru` и region подписи.

## Запуск

```bash
pulumi preview
pulumi up
pulumi stack output            # projectId, publicIp, s3*, domain
```

A-запись домена находится под управлением Pulumi: созданную вручную запись нужно удалить
до `pulumi up` (иначе конфликт имён в зоне).

## Проверка S3 (из DoD)

Публичное чтение объектов включается `pulumi config set infra:s3PublicRead true` (по умолчанию выключено).

```bash
# Ключи продукта читаем в локальные переменные и отдаём только команде aws:
# AWS_* в окружении — это личный ключ стейта (см. «Backend и стек»), перетирать его нельзя,
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
```

`pulumi destroy` удаляет бакет вместе с объектами (`forceDestroy: true`).

## Бакет ноутбуков

`infra:notebooksBucket` — данные пользователей (`.ipynb`), доступ только у Go API:

- тип бакета `private` (`BucketAccess`), публичного домена `selstorage.ru` нет, источником CDN не служит;
- сервисный пользователь `infra:notebooksUserName` с ролью `s3.bucket.user` и свой S3-ключ. Ключ
  пользователя стека (`s3AccessKey`) бэку не отдаётся: у того `member` на весь проект;
- политика бакета: пользователю бэка — `GetObject`, `PutObject`, `DeleteObject`, `ListBucket` только
  здесь; пользователю стека — `s3:*` (с политикой роли проекта не действуют, без этого правила Pulumi
  получил бы `403` на `GetBucketPolicy`). В `infra:s3Bucket` пользователя бэка нет — там `AccessDenied`;
- `protect: true`, без `forceDestroy`: `pulumi destroy` на бакете остановится. Удалить осознанно —
  опустошить бакет и `pulumi state unprotect 'urn:pulumi:prod::infra::aws:s3/bucket:Bucket::notebooks'`.

Выходы: `notebooksBucket`, `notebooksAccessKey`, `notebooksSecretKey` (оба ключа — только с
`--show-secrets`). Endpoint и регион те же: `s3Endpoint`, `infra:s3Pool`. Ключи переносятся в
`ansible/inventory/group_vars/all/vault.yml` (`vault_notebooks_s3_access_key`,
`vault_notebooks_s3_secret_key`) через `ansible-vault edit` — `ansible/README.md`, «Секреты».

## Бакет аватарок

`infra:avatarsBucket` — аватарки пользователей, читают все, пишет Go API:

- тип бакета `public` (`BucketAccess`): объект доступен без авторизации по
  `https://<avatarsPublicDomain>/<ключ>` (`<uuid>.selstorage.ru`). Источником CDN не служит;
- отдельного пользователя нет: пишет пользователь бэка `infra:notebooksUserName` тем же ключом
  (`notebooksAccessKey`, `notebooksSecretKey`);
- политика бакета: пользователю бэка — `GetObject`, `PutObject`, `DeleteObject` (без листинга);
  пользователю стека — `s3:*`; всем — `GetObject`;
- `protect: true`, без `forceDestroy`, как у ноутбуков. Удалить осознанно — опустошить бакет и
  `pulumi state unprotect 'urn:pulumi:prod::infra::aws:s3/bucket:Bucket::avatars'`.

Выходы: `avatarsBucket`, `avatarsPublicDomain`.

Проверка доступа (после `source bootstrap/env.sh`):

```bash
S3_ENDPOINT=$(pulumi stack output s3Endpoint)
S3_POOL=$(pulumi config get infra:s3Pool)
NB_BUCKET=$(pulumi stack output notebooksBucket)
NB_AK=$(pulumi stack output notebooksAccessKey --show-secrets)
NB_SK=$(pulumi stack output notebooksSecretKey --show-secrets)
nb_aws() {   # ключ бэка — только этой команде, AWS_* оболочки (ключ стейта) не трогаем
  AWS_ACCESS_KEY_ID="$NB_AK" AWS_SECRET_ACCESS_KEY="$NB_SK" \
    AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null \
    aws --endpoint-url "$S3_ENDPOINT" --region "$S3_POOL" "$@"
}

echo '{}' > /tmp/check.ipynb
nb_aws s3 cp /tmp/check.ipynb "s3://$NB_BUCKET/check.ipynb"        # проходит
nb_aws s3 cp "s3://$NB_BUCKET/check.ipynb" /tmp/check-back.ipynb   # проходит
nb_aws s3 ls "s3://$NB_BUCKET/"                                    # check.ipynb
nb_aws s3 cp /tmp/check.ipynb "s3://$(pulumi stack output s3Bucket)/check.ipynb"   # AccessDenied
curl -I "$S3_ENDPOINT/$NB_BUCKET/check.ipynb"                      # 403 без авторизации
nb_aws s3 rm "s3://$NB_BUCKET/check.ipynb"
```

Новый ключ шлюз S3 признаёт не сразу: первые секунды (иногда минуты) — `InvalidAccessKeyId`.

## Передача в Ansible

- `projectId` — dynamic inventory ищет серверы в этом проекте по `metadata.role`: `ansible/env.sh` берёт
  его сам, для `ansible/clouds.yaml` — вписать в `project_id`;
- `domain` → `app_domain` в `ansible/inventory/group_vars/all/vars.yml`.

`privateIp` у стека больше нет: VPS одна, её приватный адрес inventory не используется.

## Разбор ошибок

| Симптом | Причина / что делать |
|---|---|
| `409 already_exists` на проекте, пользователе или keypair | Аккаунт общий, имя занято — задайте свой `infra:name` / `infra:serviceUserName` |
| `403` при создании сервисного пользователя | У пользователя аккаунта нет `iam.admin` |
| `ExternalGatewayForFloatingIPNotFound` | Привязка IP раньше подключения подсети к роутеру; исправлено `dependsOn: [routerInterface]` |
| `Your query returned no results` на флейворе | Неверное `infra:*FlavorName` для региона |
| `invalid character '<' looking for beginning of value` | DNS API Selectel временно отвечает `500` HTML — подождать и повторить `pulumi up` |
| Ошибка создания бакета провайдером aws | Проверить `infra:s3Pool`: endpoint `s3.<pool>.storage.selcloud.ru` должен существовать |
| `pulumi install` создал `package-lock.json` | Не установлен bun или старый `Pulumi.yaml` без `packagemanager: bun` |

Логические имена ресурсов (`"release"`, `"gateway"`, `"product-releases"`, `"notebooks"`, `"avatars"`) не меняйте: это пересоздание ресурсов. Если переименовать всё-таки нужно, добавляйте `aliases` со старым именем — так сделано для бывших `"study"`. Ресурсы backend-сервера (Instance/Port/Volume `"backend"`) удалены при переходе на одну VPS — возвращать их прежним именем нельзя до проверки стейта.
