# Pulumi: две VPS + S3 + DNS в Selectel

Создаёт: проект, сервисного пользователя проекта (+ S3-ключи), keypair, приватную сеть с роутером,
VPS 1 (floating IP, роль gateway), VPS 2 (только приватная сеть, роль backend), S3-бакет через @pulumi/aws
(публичное чтение — при `infra:s3PublicRead=true`), A-запись домена на VPS 1.

## Установка

Нужны [Pulumi CLI](https://www.pulumi.com/docs/install/) и [bun](https://bun.sh).

```bash
cd pulumi
pulumi install     # генерирует SDK провайдера selectel в sdks/ и ставит зависимости через bun
```

`packagemanager: bun` зафиксирован в `Pulumi.yaml`: без него на чистом клоне `pulumi install`
выберет npm и создаст `package-lock.json`. `sdks/`, `node_modules/` — в `.gitignore`.

## Учётные данные

Нужен **сервисный пользователь аккаунта** (панель → Управление доступом → Сервисные пользователи)
с ролями `member` (на аккаунт) и `iam.admin` — второй нужен, чтобы Pulumi создал сервисного
пользователя проекта.

## Backend и стек

Состояние стека хранится в S3-бакете Selectel (Object Storage). Ключи и бакет — отдельный
пользователь Object Storage, не путать с сервисным пользователем аккаунта:

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
pulumi login "s3://<state-bucket>?region=<s3-pool>"   # endpoint — через AWS_ENDPOINT_URL
pulumi stack init dev         # спросит passphrase для секретов — сохраните её
```

`Pulumi.<stack>.yaml` содержит логин и зашифрованный пароль, поэтому в `.gitignore`;
в репозитории только `Pulumi.dev.yaml.example`.

## Конфиг

```bash
pulumi config set selectel:domainName <номер-аккаунта>
pulumi config set selectel:username   <сервисный-пользователь-аккаунта>
pulumi config set --secret selectel:password '<пароль>'
pulumi config set selectel:authUrl    https://cloud.api.selcloud.ru/identity/v3/
pulumi config set selectel:authRegion ru-9

pulumi config set infra:name             pulumi-<ваш-ник>   # уникально в общем аккаунте
pulumi config set infra:serviceUserName  cellestialSystemUser # сервисный пользователь проекта
pulumi config set infra:pool             ru-9
pulumi config set infra:zone             ru-9a
pulumi config set infra:volumeType       fast.ru-9a
pulumi config set infra:gatewayFlavorName SL1.2-4096
pulumi config set infra:backendFlavorName SL2.2-8192
pulumi config set infra:imageName        "Ubuntu 24.04 LTS 64-bit"
pulumi config set infra:sshPublicKey     "$(cat ~/.ssh/selectel_release.pub)"

# DNS: A-запись domain → publicIp VPS 1
pulumi config set infra:domain       cellestial.ru
pulumi config set infra:dnsZone      cellestial.ru.
pulumi config set infra:dnsProjectId <id проекта с зоной>

# Object Storage
pulumi config set infra:s3Pool   ru-1
pulumi config set infra:s3Bucket <имя-бакета>
```

`pool`, `zone` и `volumeType` — из одного региона VPS (`ru-9` / `ru-9a` / `fast.ru-9a`).
`s3Pool` задаёт endpoint `https://s3.<pool>.storage.selcloud.ru` и region подписи.

## Запуск

```bash
pulumi preview
pulumi up
pulumi stack output            # projectId, publicIp, privateIp, s3*, domain
```

A-запись домена находится под управлением Pulumi: созданную вручную запись нужно удалить
до `pulumi up` (иначе конфликт имён в зоне).

## Проверка S3 (из DoD)

Публичное чтение объектов включается `pulumi config set infra:s3PublicRead true` (по умолчанию выключено).

```bash
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
```

`pulumi destroy` удаляет бакет вместе с объектами (`forceDestroy: true`).

## Передача в Ansible

- `projectId` → `project_id` в `ansible/clouds.yaml` (dynamic inventory найдёт серверы по `metadata.role`);
- `domain` → `app_domain` в `ansible/inventory/group_vars/all/vars.yml`;
- `privateIp` использовать руками не нужно — inventory сам подставит его как `ansible_host` VPS 2.

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

Логические имена ресурсов (`"release"`, `"gateway"`, `"backend"`, `"product-releases"`) не меняйте: это пересоздание ресурсов. Если переименовать всё-таки нужно, добавляйте `aliases` со старым именем — так сделано для бывших `"study"`.
