# Pulumi: одна VPS + S3 + DNS в Selectel

Создаёт: проект, сервисного пользователя проекта (+ S3-ключи), keypair, приватную сеть с роутером
(нужна для floating IP), VPS (floating IP, роль gateway: Caddy, позже Go API и Postgres в Docker Compose),
S3-бакеты через @pulumi/aws (фронт и статика под CDN, аватарки — см. «Бакеты»), A-запись домена
на VPS, отдельного сервисного пользователя с S3-ключом для Go API.

Двухсерверная схема (gateway + backend) заморожена в git в варианте `bff` документации; стек — одна VPS.

## Структура программы

Один project `infra` и один стек `prod`. `index.ts` только собирает модули и экспортирует outputs;
каждый модуль — функция с типизированными входом и результатом, ресурсы создаются один раз.

| Модуль | Ответственность |
| --- | --- |
| `config.ts` | Чтение и валидация конфига, переключатели и их совместимость, выбор образа и флейвора |
| `foundation.ts` | Проект, сервисный пользователь стека и его S3-ключ, провайдеры OpenStack/S3, инициализация S3, сеть/подсеть/роутер, keypair |
| `gateway.ts` | Порт, диск, VPS gateway и её floating IP |
| `backend.ts` | VPS2 (`infra:backend`): security group, порт, диск, сервер |
| `storage.ts` | Бакеты продукта, их тип и политики, пользователь Go API и его ключ, CDN-ресурсы |
| `auxiliary.ts` | Вспомогательная VPS (`infra:auxiliary`), её floating IP, бакет restic со своим пользователем и провайдером пула |
| `runtime.ts` | Runtime-VPS (`infra:runtime`): пользователь provisioner, security group, SSH-keypair, CA/клиентский сертификат, ключ шифрования |
| `domains.ts` | DNS-провайдер, зоны и записи app/hooks/docs/CDN/аватарок, подтверждение GitHub Pages, `CdnDomain`/`BucketDomain` |
| `selectel-storage.ts` | Dynamic-ресурсы API хранилища и CDN Selectel |

Порядок: foundation → gateway, backend, storage, auxiliary → runtime и domains. CDN-ресурс создаёт
storage, DNS-запись и привязку его домена — domains. Тесты на моках: `config.test.ts` (комбинации
переключателей), `index.test.ts` (сборка модулей, outputs, DNS, runtime), `selectel-storage.test.ts`.

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
# Публичный бакет аватарок в том же пуле
pulumi config set infra:avatarsBucket <имя-бакета>
```

Приватная VPS2 под Go API, Postgres и Redis (infra#59) — без публичного IP, в той же приватной сети;
security group пускает SSH и 8080 только с приватного адреса gateway:

```bash
pulumi config set infra:backend true
pulumi config set infra:backendFlavorName SL1.2-4096
# pulumi config set infra:backendVolumeSize 40   # по умолчанию 40 ГБ
```

Выходы `backendPrivateIp` и `gatewayPrivateIp` — для inventory Ansible (`app_gateway_address`, группа
`backend` в `inventory/ci.yml`).

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

Бакеты в одном пуле (`infra:s3Pool`):

| Бакет | Конфиг | Для чего | Тип | CDN | Кто пишет |
|---|---|---|---|---|---|
| фронт | `infra:s3Bucket` | статика фронтенда (релизы) | публичный | да, источник CDN-ресурса (`infra:cdn`) | пользователь стека (`s3AccessKey`) |
| статика | `infra:staticBucket` | статические файлы продукта (картинки, шрифты) | публичный | да, источник своего CDN-ресурса | пользователь стека (`s3AccessKey`), CI репозитория `static` |
| аватарки | `infra:avatarsBucket` | аватарки пользователей, бэкенд | публичный | нет | Go API (`avatarsAccessKey`) |

- Фронт отдаётся через CDN (`cdnDefaultDomain`, свой домен — `cdnCustomDomain`) или напрямую с
  `https://<s3PublicDomain>/<ключ>`; проверка — ниже, «Проверка S3».
- Статика отдаётся через свой CDN-ресурс (`staticCdnDefaultDomain`, свой домен — `staticCustomDomain`)
  — «Бакет статики».
- Аватарки отдаются напрямую с `https://<avatarsPublicDomain>/<ключ>` — «Бакет аватарок».

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
флагов упадёт на защищённых бакетах аватарок и бэкапов и не удалит ничего — нужен
`pulumi destroy --exclude-protected` (`RUNBOOK.md`, п.7).

## Бакет статики

`infra:staticBucket` (`cellestial-static-0`) — статические файлы продукта, клиент берёт их по
`<staticCustomDomain>/<путь>`. Бакет необязательный: без `infra:staticBucket` нет ни его, ни его CDN.

- тип бакета `public` (`BucketAccess`), публичный домен `<uuid>.selstorage.ru` — выход
  `staticPublicDomain`;
- перед бакетом — второй CDN-ресурс `<infra:name>-static-cdn` (`infra:staticCdnName`), отдельный от
  CDN релизов и создаётся независимо от `infra:cdn`; выходы `staticCdnResourceId`,
  `staticCdnDefaultDomain`;
- политики бакета нет: пишет пользователь стека (`s3AccessKey`, роль `member`) — этим ключом
  выкладывает файлы CI репозитория `static`;
- `forceDestroy: true`, без `protect`, как у бакета релизов: содержимое CI выкладывает заново.

Выходы: `staticBucket`, `staticPublicDomain`, `staticCustomDomain` (свой домен — «Свои домены»).

## Бакет аватарок

`infra:avatarsBucket` — аватарки пользователей, читают все, пишет Go API:

- тип бакета `public` (`BucketAccess`): объект читается без авторизации по ключу —
  `https://<avatarsPublicDomain>/<ключ>` (`<uuid>.selstorage.ru`). Через S3 API
  (`<s3Endpoint>/<бакет>/<ключ>`) анонимный запрос получает `403`: политика бакета в Selectel
  действует только на авторизованные запросы, анонимного чтения она не открывает. Источником CDN
  не служит;
- пишет Go API ключом своего сервисного пользователя `cellestialAvatarsUser` (роль `s3.bucket.user`,
  ключ `avatars-s3`). Ключ пользователя стека (`s3AccessKey`) бэку не отдаётся: у того `member` на весь
  проект; `s3.bucket.user` без политики не даёт ничего — доступ только в этом бакете;
- политика бакета: пользователю бэка — `GetObject`, `PutObject`, `DeleteObject` объектов (без листинга);
  пользователю стека — `s3:*` (с политикой роли проекта не действуют, без этого правила Pulumi получил
  бы `403` на `GetBucketPolicy`). Правила для всех (`PublicRead`) нет — анонимное чтение даёт тип бакета;
- `protect: true`, без `forceDestroy`: `pulumi destroy` без `--exclude-protected` падает на плане. Удалить осознанно — опустошить бакет и
  `pulumi state unprotect 'urn:pulumi:prod::infra::aws:s3/bucket:Bucket::avatars'`.

Выходы: `avatarsBucket`, `avatarsPublicDomain`, `avatarsCustomDomain` (свой домен — «Свои домены»; его
бэк и отдаёт в ссылках на аватарки), `avatarsAccessKey`, `avatarsSecretKey` (оба ключа — только с
`--show-secrets`; endpoint и регион — `s3Endpoint`, `infra:s3Pool`). Ключи переносятся в
`ansible/inventory/group_vars/all/vault.yml` (`vault_avatars_s3_access_key`,
`vault_avatars_s3_secret_key`) через `ansible-vault edit` — `ansible/README.md`, «Секреты».

Проверка доступа (после `source bootstrap/env.sh`):

```bash
S3_ENDPOINT=$(pulumi stack output s3Endpoint)
S3_POOL=$(pulumi config get infra:s3Pool)
AV_BUCKET=$(pulumi stack output avatarsBucket)
AV_AK=$(pulumi stack output avatarsAccessKey --show-secrets)
AV_SK=$(pulumi stack output avatarsSecretKey --show-secrets)
av_aws() {   # ключ бэка — только этой команде, AWS_* оболочки (ключ стейта) не трогаем
  AWS_ACCESS_KEY_ID="$AV_AK" AWS_SECRET_ACCESS_KEY="$AV_SK" \
    AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null \
    aws --endpoint-url "$S3_ENDPOINT" --region "$S3_POOL" "$@"
}

echo check > /tmp/check.txt
av_aws s3 cp /tmp/check.txt "s3://$AV_BUCKET/check.txt"           # проходит
curl -sI "https://$(pulumi stack output avatarsCustomDomain)/check.txt" | head -1   # 200 без авторизации
av_aws s3 cp "s3://$AV_BUCKET/check.txt" /tmp/check-back.txt      # проходит
av_aws s3 ls "s3://$AV_BUCKET/"                                   # AccessDenied: листинга нет
av_aws s3 cp /tmp/check.txt "s3://$(pulumi stack output s3Bucket)/check.txt"   # AccessDenied
av_aws s3 rm "s3://$AV_BUCKET/check.txt"                          # проходит
```

Новый ключ шлюз S3 признаёт не сразу: первые секунды (иногда минуты) — `InvalidAccessKeyId`.

## Свои домены

`infra:cdnDomain`, `infra:staticDomain` и `infra:avatarsDomain` — свои домены CDN-ресурса релизов,
CDN-ресурса статики и бакета аватарок (`infra:avatarsBucket`); у бакетов релизов и статики своего
домена нет — домены привязаны к их CDN. Привязка — dynamic-ресурсы `CdnDomain` и
`BucketDomain` (`selectel-storage.ts`). Сертификатов Pulumi не выпускает.

| | CDN (`cdn.cellestial.ru`, `static.cellestial.ru`) | Бакет аватарок (`avatars.cellestial.ru`) |
|---|---|---|
| DNS | CNAME в зоне `infra:dnsZone` на `<id>.selcdn.net.` | своя зона `avatars.cellestial.ru.` (проект `infra:dnsProjectId`), в ней ALIAS на публичный домен бакета `<uuid>.selstorage.ru.` (`avatarsPublicDomain`) |
| Привязка | `PATCH /cdn/v3/resources/<id>` — `names`, сверка через `GET` | `PUT /v2/containers/<бакет>/domains` |
| Сертификат (руками) | панель → CDN → ресурс → сертификаты | панель → S3 → SSL-сертификаты |

- CDN: Selectel сам проверяет CNAME при привязке, поэтому на ней короткий повтор (до трёх минут); не
  успело — `up` падает с понятной ошибкой, повторный `up` продолжает. Распространения DNS `up` не ждёт.
- Сертификат `static.cellestial.ru` выпущен в панели (CDN → ресурс статики → сертификаты), домен
  отвечает по HTTPS. После пересоздания CDN-ресурса статики сертификат выпускают заново: до этого по
  HTTPS CDN отвечает чужим сертификатом.
- Аватарки: на вершине зоны CNAME невозможен, поэтому в зоне ALIAS. Привязка домена бакета через API
  проверяет именно CNAME и на ALIAS отвечает `domain_cname_invalid`: `BucketDomain` не трогает уже
  привязанный домен, а слетевшую привязку возвращают в панели (S3 → бакет → Домены).
- Зону создаёт Pulumi. Созданную руками зону с тем же именем перед `up` удалить: вторую зону с этим
  именем Selectel не даст.
- ALIAS ведёт на тот же адрес хранилища, что и технический домен бакета; бакет хранилище выбирает по
  `Host`, поэтому домен должен быть привязан к бакету. Проверка, что домен ведёт в бакет (до сертификата — с `-k`): ответ с заголовками
  `x-container-storage-policy-*`, как у технического домена; у непривязанного `Host` их нет.

  ```bash
  curl -skI "https://$(pulumi stack output avatarsCustomDomain)/x"
  curl -sI  "https://$(pulumi stack output avatarsPublicDomain)/x"
  ```
- Без сертификата свой домен бакета по HTTPS отвечает сертификатом `*.<пул>.storage.selcloud.ru`, а
  HTTP перенаправляет на HTTPS. Бэк отдаёт ссылки на аватарки через свой домен
  (`avatars_public_domain` в Ansible) — сертификат должен быть выпущен до выкатки бэка.
- Отвязка домена бакета (`pulumi destroy`, смена домена) сверяется через `GET`; не вышло — ошибка с
  подсказкой отвязать в панели.
- Тесты функций API: `bun test selectel-storage.test.ts`.

### Сайт документации

`infra:docsDomain` (`docs.cellestial.ru`) — свой домен сайта документации. Сайт собирает и отдаёт
GitHub Pages репозитория [docs](https://github.com/Cringe-Driven-Development-Team/docs); Pulumi держит
только CNAME в зоне `infra:dnsZone` на `cringe-driven-development-team.github.io.` Сертификат выпускает
GitHub, когда домен выставлен в настройках Pages репозитория `docs` (Settings → Pages → Custom domain).

```bash
dig +short CNAME docs.cellestial.ru   # cringe-driven-development-team.github.io.
```

`infra:githubPagesChallenge` — значение TXT-записи
`_github-pages-challenge-cringe-driven-development-team.<infra:dnsZone>`, которой GitHub подтверждает
домен для организации. Пока домен подтверждён, чужой репозиторий не займёт его поддомен на Pages, даже
если сайт docs отключат. Порядок:

1. Владелец организации: Settings → Pages → Add a domain → `cellestial.ru`; GitHub покажет значение TXT.
2. `pulumi config set infra:githubPagesChallenge <значение>` и `pulumi up`.
3. Владелец организации нажимает Verify. Запись остаётся в стеке.

Ключи независимы: без `infra:githubPagesChallenge` создаётся только CNAME.

## Вспомогательная VPS

`infra:auxiliary: "true"` — отдельная VPS под сервис вебхуков (infra#54) в той же приватной сети, что и
gateway, но со своими портом, диском (`infra:auxiliaryVolumeSize`, 40 ГБ), флейвором
(`infra:auxiliaryFlavorName`) и floating IP; `metadata.role: auxiliary` — группа `auxiliary` в Ansible.

- Security group `auxiliary` на порту VPS: входящие только 22/80/443 tcp и ICMP. Docker публикует порты
  мимо ufw — закрывает их именно security group.
- A-запись `infra:hooksDomain` в `infra:dnsZone`; сертификат выпускает Caddy на VPS.
- Бакет бэкапов `infra:auxiliaryBackupBucket` в пуле `infra:auxiliaryBackupPool` (`ru-1`: в `ru-7` Selectel
  временно не создаёт бакеты): приватный, `protect: true`, пользователь
  `infra:auxiliaryBackupUserName` с ролью `s3.bucket.user` — доступ только к этому бакету по политике.

Выходы: `auxiliaryPublicIp`, `hooksDomain`, `auxiliaryBackupBucket`, `auxiliaryBackupAccessKey`,
`auxiliaryBackupSecretKey`. Дальше — RUNBOOK, «Вспомогательная VPS».

## Runtime-VPS сессий

`infra:runtime: "true"` (infra#89, нужен `infra:backend`) — то, что Go API на VPS2 сам себе не создаст,
чтобы создавать, настраивать и удалять VPS на пользовательскую сессию (backend#57). Сами runtime-VPS
стек не создаёт: их порты, диски и серверы — в проекте стека, в подсети `192.168.199.0/24` без floating
IP, наружу через SNAT роутера.

| Ресурс | Что это |
|---|---|
| сервисный пользователь `infra:runtimeUserName` (`cellestialRuntimeProvisioner`) | роль `member` на проект; от него Go API ходит в Nova/Cinder/Neutron. Отдельно от пользователя стека: отзыв одного не ломает другое |
| security group `runtime` | ingress только с `backendPrivateIp/32`: TCP 22 (Ansible) и 8443 (TLS-прокси агента); ICMP из подсети. Egress — по умолчанию Neutron: трафик kernel фильтрует nftables на VPS (`ansible/runtime`) |
| `tls.PrivateKey` ed25519 + keypair `runtime-provisioner` | SSH-ключ provisioner; keypair принадлежит пользователю runtime — Nova видит его только этим пользователем |
| `tls` CA (ECDSA P-256, 10 лет) + клиентский сертификат `cellestial-backend` (`client_auth`, 2 года) | mTLS backend ↔ агент. Серверные сертификаты VPS выпускает backend этим CA, поэтому ключ CA тоже уходит в backend |
| `random.RandomBytes` 32 байта | `RUNTIME_SECRETS_KEY`: шифрует токены агентов в БД |

Конфиг:

```bash
pulumi config set infra:runtime true
# 1 vCPU / 2 GiB. Поиск по vcpus/ram (infra:runtimeVcpus/infra:runtimeRam) в ru-9 неоднозначен —
# SL1.1-2048 и m1.small, поэтому имя; список — ./scripts/list-flavors.sh
pulumi config set infra:runtimeFlavorName SL1.1-2048
pulumi config set infra:runtimeVolumeType fast.ru-9a           # по умолчанию infra:volumeType
# Собственный образ runtime (RUNBOOK, «Образ runtime-VPS»); тот же id — runtime_image_id в ansible vars.yml
pulumi config set infra:runtimeImageId 8f57fbe7-2b6e-4e0b-a8ac-edf936815411
# pulumi config set infra:runtimeUserName cellestialRuntimeProvisioner    # по умолчанию; уникально в аккаунте
```

Выходы (секретные — только `pulumi stack output <имя> --show-secrets`) → Ansible
(`group_vars/all/vars.yml` / `vault.yml`) → env Go API:

| Выход | Секрет | Ansible | Go API |
|---|---|---|---|
| `runtimeOsUsername` | | `runtime_os_username` | `RUNTIME_OS_USERNAME` |
| `runtimeOsPassword` | да | `vault_runtime_os_password` | `RUNTIME_OS_PASSWORD` |
| `runtimeAccountId` | | `runtime_os_user_domain_name` | `RUNTIME_OS_USER_DOMAIN_NAME` |
| `runtimeProjectId` | | `runtime_os_project_id` | `RUNTIME_OS_PROJECT_ID` |
| `runtimeRegion`, `runtimeAvailabilityZone` | | `runtime_os_region`, `runtime_availability_zone` | `RUNTIME_OS_REGION`, `RUNTIME_AVAILABILITY_ZONE` |
| `runtimeNetworkId`, `runtimeSubnetId`, `runtimeSecurityGroupId` | | `runtime_network_id`, `runtime_subnet_id`, `runtime_security_group_id` | `RUNTIME_NETWORK_ID`, `RUNTIME_SUBNET_ID`, `RUNTIME_SECURITY_GROUP_ID` |
| `runtimeImageId`, `runtimeFlavorId`, `runtimeVolumeType` | | `runtime_image_id`, `runtime_flavor_id`, `runtime_volume_type` | `RUNTIME_IMAGE_ID`, `RUNTIME_FLAVOR_ID`, `RUNTIME_VOLUME_TYPE` |
| `runtimeKeypairName` | | `runtime_ssh_key_name` | `RUNTIME_SSH_KEY_NAME` |
| `runtimeBlockedPublicIps` | | `runtime_blocked_public_ips` | `RUNTIME_BLOCKED_PUBLIC_IPS` (floating IP gateway и auxiliary) |
| `runtimeSecretsKey` | да | `vault_runtime_secrets_key` | `RUNTIME_SECRETS_KEY` |
| `runtimeSshPrivateKey` | да | `vault_runtime_ssh_private_key` | файл `/run/secrets/runtime/ssh_key` |
| `runtimeCaCert`, `runtimeCaKey` | ключ | `vault_runtime_ca_cert`, `vault_runtime_ca_key` | `/run/secrets/runtime/ca.crt`, `ca.key` |
| `runtimeClientCert`, `runtimeClientKey` | ключ | `vault_runtime_client_cert`, `vault_runtime_client_key` | `/run/secrets/runtime/client.crt`, `client.key` |

`RUNTIME_OS_AUTH_URL` — константа `https://cloud.api.selcloud.ru/identity/v3`, `RUNTIME_ENV_NAME` —
`runtime_env_name: production` в `vars.yml`. Образ фиксирован: `runtimeImageId` — это
`infra:runtimeImageId`, равный `runtime_image_id` (тест `test_runtime_image`); новый образ — правкой обоих
в одном PR после проверки `openstack volume create` из RUNBOOK. Перенос в vault, проверка доступа и ротация — RUNBOOK, «Runtime-VPS сессий».

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
| `pulumi install`: GitHub `releases/latest`, `rate limit exceeded`, HTTP 403 | Загрузчик Pulumi использует `GITHUB_TOKEN` для запросов к GitHub Releases. В Actions шаги установки получают временный `${{ github.token }}` с `contents: read`, включая bootstrap, preview и apply через общий YAML anchor. Отдельный PAT не требуется; токен не записывается в файлы и логи. Если ошибка повторилась, проверьте лимит авторизованных запросов; для повторного запуска учитывайте, что main workflow после проверок может выполнить production-выкладку. |

Правка кода dynamic-ресурсов (`selectel-storage.ts`) попадает в стейт только через `pulumi up`:
`refresh` и `delete` исполняют код провайдера из стейта. `BucketAccess`, `CdnDomain` и `BucketDomain` показывают такую
правку как `update` (те же значения выставляются повторно).

Логические имена ресурсов (`"release"`, `"gateway"`, `"product-releases"`, `"avatars"`) не меняйте: это пересоздание ресурсов. Если переименовать всё-таки нужно, добавляйте `aliases` со старым именем — так сделано для бывших `"study"`. Ресурсы backend-сервера (Instance/Port/Volume `"backend"`) удалены при переходе на одну VPS — возвращать их прежним именем нельзя до проверки стейта.
