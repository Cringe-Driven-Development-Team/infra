# infra

Selectel: одна VPS, S3, DNS и CDN — Pulumi (`pulumi/`, стек `prod`; `pulumi/bootstrap/`, стек `main`)
и Ansible (`ansible/`). Пошагово с нуля — `RUNBOOK.md`.

## Что создаёт Pulumi, а что делается руками

Границу не размывать: то, что ниже помечено «руками», в код не добавлять — Pulumi с этим конфликтует
(записи, которые ставит сама панель или Selectel) или API для этого нет.

### Pulumi, bootstrap-стек (`pulumi/bootstrap/`, проект `infra-shared`)

- Проект `infra-shared`, бакет стейта `cdd-infra-state`, пользователь и ключи стейта.
- Зона DNS `cellestial.ru.` (id проекта — выход `dnsProjectId`, он же `infra:dnsProjectId`).

### Pulumi, основной стек (`pulumi/`, проект `pulumi-cellestial`)

- Проект `pulumi-cellestial`, сервисный пользователь, keypair, S3-ключ.
- Сеть, роутер, floating IP, boot-диск и VPS `pulumi-server-gateway`.
- A-запись `cellestial.ru.` → `publicIp` в зоне `cellestial.ru.` (проект `infra-shared`).
- Бакет `infra:s3Bucket`, **публичный** (`infra:s3Public`, по умолчанию `true`): тип бакета ставит
  dynamic-ресурс `BucketAccess` (`pulumi/selectel-storage.ts`), публичный домен
  `<uuid>.selstorage.ru` — выход `s3PublicDomain`.
- Бакет ноутбуков `infra:notebooksBucket`, **приватный** (`BucketAccess` с `type: "private"`, не источник
  CDN), `protect: true` и без `forceDestroy` — данные пользователей. Сервисный пользователь Go API
  (`infra:notebooksUserName`, роль `s3.bucket.user`) и его S3-ключ — выходы `notebooksAccessKey`,
  `notebooksSecretKey`; доступ ему даёт только политика этого бакета (в ней же `s3:*` пользователю стека).
- Бакет аватарок `infra:avatarsBucket`, **публичный** (`BucketAccess` с `type: "public"`, не источник
  CDN), `protect: true` и без `forceDestroy`. Пишет тот же пользователь Go API (политика бакета:
  объекты — ему, `s3:*` — пользователю стека); выходы `avatarsBucket`, `avatarsPublicDomain`.
  Анонимное чтение по ключу — `https://<avatarsPublicDomain>/<ключ>`, его даёт тип бакета. Правило
  `PublicRead` в политику не возвращать: политика Selectel действует только на авторизованные
  запросы, через S3 API анонимный запрос получает `403` при любой политике.
- При `infra:cdn: true` — CDN-ресурс `<infra:name>-cdn` с бакетом источником (dynamic-ресурс
  `CdnResource`); выходы `cdnResourceId`, `cdnDefaultDomain` (`<id>.selcdn.net`).
- Свои домены — **CNAME внутри зоны `cellestial.ru.`**, без зон-поддоменов (`pulumi/selectel-storage.ts`):
  - `infra:cdnDomain` (`cdn.cellestial.ru`) → CNAME на `cdnDefaultDomain`, dynamic-ресурс `CdnDomain`:
    домен в `names` CDN-ресурса и заказ Let's Encrypt через CDN API; выход `cdnCertificateStatus`
    (`accepted` → `processed`).
  - `infra:s3Domain` (`s3.cellestial.ru`) → CNAME на `access.<infra:s3Pool>.storage.selcloud.ru`,
    dynamic-ресурс `BucketDomain` у бакета `infra:s3Bucket`: привязка домена, выпуск Let's Encrypt
    Selectel (`api.selectel.ru/certs/le`, DNS-01) и загрузка сертификата в хранилище (`/v2/ssl`);
    выходы `s3CertificateStatus`, `s3CertificateUploadedVersion`.
  - Готовности сертификатов `up` не ждёт: пока сертификат не выпущен, ресурс остаётся «к обновлению»,
    следующий `pulumi up` доделывает. Selectel продлевает Let's Encrypt сам, но в хранилище новую версию
    кладёт `pulumi refresh && pulumi up`.

### Руками в панели Selectel

- Первый сервисный пользователь аккаунта и его роли (`member`, `iam.admin` на аккаунт).
- Личный доступ к стейту на человека — `pulumi/bootstrap/README.md`.
- Домены и сертификаты CDN и бакета — не в панели: их ведёт Pulumi (см. выше), ручная привязка
  разойдётся со стейтом.

### Чего не делать в коде (уже падало)

- NS-делегирование поддомена в `cellestial.ru.`: Selectel ставит его сам при создании зоны-поддомена,
  своя NS-запись — `this_rrset_is_already_exists`.
- Зону-поддомен под `cdn.cellestial.ru`/`s3.cellestial.ru` и ALIAS: на вершине зоны CNAME невозможен,
  ALIAS не принимают ни привязка домена бакета (`domain_cname_invalid`), ни CDN. Только CNAME-запись в
  `cellestial.ru.`.
- `names` в теле создания/изменения `CdnResource`: домен, который ещё не CNAME на CDN, API молча
  отбрасывает при `accept`. Привязка — отдельный `CdnDomain` после записи, со сверкой через `GET`.
- Зону-поддомен в проекте стека (`project.id`): Selectel отвечает `root_zone_already_belongs_to_another_user`
  (корень `cellestial.ru.` в `infra-shared`) — после `destroy` и нового проекта `up` падал на этом.
- Зоны-поддомены при живой зоне-поддомене не заменять на CNAME в `cellestial.ru.` в одном `up`:
  пока зона существует, NS Selectel отвечают из неё и не видят CNAME.

## Эксплуатация

- Перед `up`: `cd pulumi && . bootstrap/env.sh && unset OS_PROJECT_NAME`; backend прибит в
  `pulumi/Pulumi.yaml` (`pulumi whoami -v`).
- `INFRA_PROJECT_ID` не нужен: DNS-ресурсы в `infra-shared` идут через явный провайдер `dns` с
  `projectId` из `infra:dnsProjectId` (провайдер берёт из него проект для `import`).
- Не прерывать `pulumi` (Ctrl+C) во время записи стейта: `prod.json` в бакете остаётся пустым
  (`failed to load checkpoint: unexpected end of JSON input`). Лечение — до любой следующей записи
  скопировать `prod/.pulumi/stacks/infra/prod.json.bak` поверх `prod.json`.
