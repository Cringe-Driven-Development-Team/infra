# Pulumi: одна VPS + S3 + DNS в Selectel

Создаёт: проект, сервисного пользователя проекта (+ S3-ключи), keypair, приватную сеть с роутером
(нужна для floating IP), VPS (floating IP, роль gateway: Caddy, позже Go API и Postgres в Docker Compose),
три S3-бакета через @pulumi/aws (фронт под CDN, аватарки, ноутбуки — см. «Бакеты»), A-запись домена
на VPS, отдельного сервисного пользователя с S3-ключом для Go API.

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

## Бакеты

Три бакета в одном пуле (`infra:s3Pool`):

| Бакет | Конфиг | Для чего | Тип | CDN | Кто пишет |
|---|---|---|---|---|---|
| фронт | `infra:s3Bucket` | статика фронтенда (релизы) | публичный | да, источник CDN-ресурса (`infra:cdn`) | пользователь стека (`s3AccessKey`) |
| аватарки | `infra:avatarsBucket` | аватарки пользователей, бэкенд | публичный | нет | Go API (`notebooksAccessKey`) |
| ноутбуки | `infra:notebooksBucket` | `.ipynb` пользователей, бэкенд | приватный | нет | Go API (`notebooksAccessKey`) |

- Фронт отдаётся через CDN (`cdnDefaultDomain`, свой домен — `cdnCustomDomain`) или напрямую с
  `https://<s3PublicDomain>/<ключ>`; проверка — ниже, «Проверка S3».
- Аватарки отдаются напрямую с `https://<avatarsPublicDomain>/<ключ>` — «Бакет аватарок».
- Ноутбуки читает и пишет только Go API своим ключом — «Бакет ноутбуков».

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

При сносе стека бакет удаляется вместе с объектами (`forceDestroy: true`). Сам `pulumi destroy` без
флагов упадёт на защищённых бакетах ноутбуков и аватарок и не удалит ничего — нужен
`pulumi destroy --exclude-protected` (`RUNBOOK.md`, п.7).

## Бакет ноутбуков

`infra:notebooksBucket` — данные пользователей (`.ipynb`), доступ только у Go API:

- тип бакета `private` (`BucketAccess`), публичного домена `selstorage.ru` нет, источником CDN не служит;
- сервисный пользователь `infra:notebooksUserName` с ролью `s3.bucket.user` и свой S3-ключ. Ключ
  пользователя стека (`s3AccessKey`) бэку не отдаётся: у того `member` на весь проект;
- политика бакета: пользователю бэка — `GetObject`, `PutObject`, `DeleteObject`, `ListBucket` только
  здесь; пользователю стека — `s3:*` (с политикой роли проекта не действуют, без этого правила Pulumi
  получил бы `403` на `GetBucketPolicy`). В `infra:s3Bucket` пользователя бэка нет — там `AccessDenied`;
- `protect: true`, без `forceDestroy`: `pulumi destroy` с защищённым бакетом в стейте падает на плане
  и не удаляет ничего (снос остального — `--exclude-protected`). Удалить осознанно — опустошить бакет
  и `pulumi state unprotect 'urn:pulumi:prod::infra::aws:s3/bucket:Bucket::notebooks'`.

Выходы: `notebooksBucket`, `notebooksAccessKey`, `notebooksSecretKey` (оба ключа — только с
`--show-secrets`). Endpoint и регион те же: `s3Endpoint`, `infra:s3Pool`. Ключи переносятся в
`ansible/inventory/group_vars/all/vault.yml` (`vault_notebooks_s3_access_key`,
`vault_notebooks_s3_secret_key`) через `ansible-vault edit` — `ansible/README.md`, «Секреты».

## Бакет аватарок

`infra:avatarsBucket` — аватарки пользователей, читают все, пишет Go API:

- тип бакета `public` (`BucketAccess`): объект читается без авторизации по ключу —
  `https://<avatarsPublicDomain>/<ключ>` (`<uuid>.selstorage.ru`). Через S3 API
  (`<s3Endpoint>/<бакет>/<ключ>`) анонимный запрос получает `403`: политика бакета в Selectel
  действует только на авторизованные запросы, анонимного чтения она не открывает. Источником CDN
  не служит;
- отдельного пользователя нет: пишет пользователь бэка `infra:notebooksUserName` тем же ключом
  (`notebooksAccessKey`, `notebooksSecretKey`);
- политика бакета: пользователю бэка — `GetObject`, `PutObject`, `DeleteObject` (без листинга);
  пользователю стека — `s3:*`. Правила для всех (`PublicRead`) нет — анонимное чтение даёт тип бакета;
- `protect: true`, без `forceDestroy`, как у ноутбуков: `pulumi destroy` без `--exclude-protected`
  падает на плане. Удалить осознанно — опустошить бакет и
  `pulumi state unprotect 'urn:pulumi:prod::infra::aws:s3/bucket:Bucket::avatars'`.

Выходы: `avatarsBucket`, `avatarsPublicDomain`, `avatarsCustomDomain` (свой домен — «Свои домены»; до
выпуска сертификата в панели бэк отдаёт ссылки на `avatarsPublicDomain`).

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

## Свои домены

`infra:cdnDomain` и `infra:avatarsDomain` — свои домены CDN-ресурса и бакета аватарок
(`infra:avatarsBucket`); у бакета релизов своего домена нет. Привязка — dynamic-ресурсы `CdnDomain` и
`BucketDomain` (`selectel-storage.ts`). Сертификатов Pulumi не выпускает.

| | CDN (`cdn.cellestial.ru`) | Бакет аватарок (`avatars.cellestial.ru`) |
|---|---|---|
| DNS | CNAME в зоне `infra:dnsZone` на `<id>.selcdn.net.` | своя зона `avatars.cellestial.ru.` (проект `infra:dnsProjectId`), в ней ALIAS на `access.<infra:s3Pool>.storage.selcloud.ru.` |
| Привязка | `PATCH /cdn/v3/resources/<id>` — `names`, сверка через `GET` | `PUT /v2/containers/<бакет>/domains` |
| Сертификат (руками) | панель → CDN → ресурс → сертификаты | панель → S3 → SSL-сертификаты |

- CDN: Selectel сам проверяет CNAME при привязке, поэтому на ней короткий повтор (до трёх минут); не
  успело — `up` падает с понятной ошибкой, повторный `up` продолжает. Распространения DNS `up` не ждёт.
- Аватарки: на вершине зоны CNAME невозможен, поэтому в зоне ALIAS. Привязка домена бакета через API
  проверяет именно CNAME и на ALIAS отвечает `domain_cname_invalid`: `BucketDomain` не трогает уже
  привязанный домен, а слетевшую привязку возвращают в панели (S3 → бакет → Домены).
- Зона уже существует (создана руками) — первый `up` берёт её в стейт:
  `pulumi config set infra:avatarsZoneImport avatars.cellestial.ru.`, после `up` ключ убрать.
- `access.<пул>.storage.selcloud.ru` — адрес хранилища пула для своих доменов: бакет выбирается по
  `Host`. Проверка, что домен ведёт в бакет (до сертификата — с `-k`): ответ с заголовками
  `x-container-storage-policy-*`, как у технического домена; у непривязанного `Host` их нет.

  ```bash
  curl -skI "https://$(pulumi stack output avatarsCustomDomain)/x"
  curl -sI  "https://$(pulumi stack output avatarsPublicDomain)/x"
  ```
- Без сертификата свой домен бакета по HTTPS отвечает сертификатом `*.<пул>.storage.selcloud.ru`, а
  HTTP перенаправляет на HTTPS — бэк остаётся на техническом домене `<uuid>.selstorage.ru`.
- Отвязка домена бакета (`pulumi destroy`, смена домена) сверяется через `GET`; не вышло — ошибка с
  подсказкой отвязать в панели.
- Тесты функций API: `bun test selectel-storage.test.ts`.

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

Правка кода dynamic-ресурсов (`selectel-storage.ts`) попадает в стейт только через `pulumi up`:
`refresh` и `delete` исполняют код провайдера из стейта. `BucketAccess`, `CdnDomain` и `BucketDomain` показывают такую
правку как `update` (те же значения выставляются повторно).

Логические имена ресурсов (`"release"`, `"gateway"`, `"product-releases"`, `"notebooks"`, `"avatars"`) не меняйте: это пересоздание ресурсов. Если переименовать всё-таки нужно, добавляйте `aliases` со старым именем — так сделано для бывших `"study"`. Ресурсы backend-сервера (Instance/Port/Volume `"backend"`) удалены при переходе на одну VPS — возвращать их прежним именем нельзя до проверки стейта.
