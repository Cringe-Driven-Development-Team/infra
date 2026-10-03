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
- Свои домены — **CNAME внутри зоны `cellestial.ru.`**, без зон-поддоменов, и привязка dynamic-ресурсами
  (`pulumi/selectel-storage.ts`); сертификатов Pulumi не выпускает:
  - `infra:cdnDomain` (`cdn.cellestial.ru`) → CNAME на `cdnDefaultDomain`, `CdnDomain`: домен в `names`
    CDN-ресурса; выход `cdnCustomDomain`.
  - `infra:s3Domain` (`s3.cellestial.ru`) → CNAME на `access.<infra:s3Pool>.storage.selcloud.ru`,
    `BucketDomain`: домен бакета релизов `infra:s3Bucket`; выход `s3CustomDomain`.

### Руками в панели Selectel

- Первый сервисный пользователь аккаунта и его роли (`member`, `iam.admin` на аккаунт).
- Личный доступ к стейту на человека — `pulumi/bootstrap/README.md`.
- Сертификаты своих доменов: `cdn.cellestial.ru` — панель → CDN → ресурс → сертификаты;
  `s3.cellestial.ru` — панель → S3 → SSL-сертификаты. В код не добавлять: заказ через CDN API
  (`POST /cdn/v3/letsencrypt/<id>`) дважды завершался `failed` без причины. Сами домены привязывает
  Pulumi — в панели их не трогать.
- Пока у `s3.cellestial.ru` нет сертификата, по HTTPS он отвечает сертификатом
  `*.ru-7.storage.selcloud.ru`, а HTTP перенаправляет на HTTPS: Caddy и бэк ходят на технические домены
  `<uuid>.selstorage.ru` (`s3PublicDomain`, `avatarsPublicDomain`), на свой домен их не переключать.

### Чего не делать в коде (уже падало)

- NS-делегирование поддомена в `cellestial.ru.`: Selectel ставит его сам при создании зоны-поддомена,
  своя NS-запись — `this_rrset_is_already_exists`.
- Зону-поддомен под `cdn.cellestial.ru`/`s3.cellestial.ru` и ALIAS: на вершине зоны CNAME невозможен, ALIAS не принимают
  ни привязка домена бакета (`domain_cname_invalid`), ни CDN. Только CNAME-запись в `cellestial.ru.`.
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
