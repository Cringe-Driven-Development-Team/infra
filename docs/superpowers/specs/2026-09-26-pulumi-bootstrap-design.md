# Bootstrap-стек для бакета стейта Pulumi

Дата: 2026-09-26. Задача: [#5](https://github.com/Cringe-Driven-Development-Team/infra/issues/5).
Статус: утверждено.

## Цель

Бакет со стейтом Pulumi описан кодом и живёт в отдельном проекте Selectel `infra-state`, а не
создан руками в общем проекте DevOps_2026H2 рядом с чужими ресурсами. Бакет стейта нельзя создать
в том же стеке, чей стейт в нём лежит, поэтому он выносится в отдельный bootstrap-стек.

## Решения

- Bootstrap самодостаточен: логика инициализации S3 и ожидания ключа — свой модуль
  `selectel-s3.ts`, не зависит от неслитого PR #4. Коллега подстраивает ветку `task-infra-1` под
  этот стек и может переиспользовать модуль.
- Стейт самого bootstrap после первого `up` переносится в созданный им бакет (префикс
  `bootstrap/`). Локальный стейт — только на первый запуск.
- Основной стек хранит стейт в том же бакете под префиксом `main/`.
- Доступ людей к стейту — личные S3-ключи: каждый своим сервисным пользователем аккаунта выпускает
  ключ на проект `infra-state` (`bun state-key.ts`, пишет `AWS_*` в `~/.config/selectel.env`).
  Общих ключей нет: иначе ключи для чтения стейта хранились бы только в самом стейте
  (решение принято 2026-09-26 после того, как единственная внешняя копия ключей была утеряна).
  Ключи пользователя `infra-state-s3` из выходов стека — для автоматизации (CI).
- Учётные данные Selectel — сервисный пользователь аккаунта `pulumi-yaroslav` (`member` на
  аккаунт + `iam.admin`), из `~/.config/selectel.env`, не из конфига стека.

## Структура

```
pulumi/bootstrap/
├── Pulumi.yaml            проект infra-bootstrap; nodejs + bun; пакет selectel 8.3.1 (terraform-provider)
├── Pulumi.main.yaml       конфиг стека main — коммитится (секретов нет)
├── package.json, bun.lock, tsconfig.json
├── env.sh                 загрузка ~/.config/selectel.env и выставление OS_*
├── index.ts               ресурсы и выходы
├── selectel-s3.ts         initProjectS3, waitForS3Key
├── *.test.ts              модульные тесты (bun test)
└── README.md              первый запуск, перенос стейта, логин основного стека, переезд pulumi-cellestial
```

Стек один — `main`. Сгенерированный SDK (`sdks/`), `node_modules/`, локальный стейт и файл экспорта
стейта — в `.gitignore`.

## Ресурсы

1. `selectel.VpcProjectV2("infra-state")` — проект `infra-state` создаётся стеком (созданный
   ранее в панели проект удалён 2026-09-26 до первого `up`); `protect: true`.
2. `random.RandomPassword` + `selectel.IamServiceuserV1("state")` — пользователь `infra-state-s3`,
   одна роль: `member`, scope `project`, `projectId` = проект `infra-state`. В проекте нет ничего,
   кроме бакета стейта.
3. `selectel.IamS3CredentialsV1("state")` — S3-ключ пользователя на проект `infra-state`.
4. Инициализация S3 в проекте и ожидание, пока S3 примет новый ключ (`selectel-s3.ts`) — только
   при реальном `up`, не при `preview`.
5. `aws.Provider` с endpoint `https://s3.<s3Pool>.storage.selcloud.ru`, path-style, пропуском
   AWS-проверок (`skipCredentialsValidation`, `skipRegionValidation`, `skipRequestingAccountId`,
   `skipMetadataApiCheck`) + `aws.s3.Bucket("state")` — `protect: true`, `forceDestroy` не задан
   (false) + `aws.s3.BucketVersioningV2` со статусом `Enabled`.

Выходы: `stateBucket`, `stateEndpoint`, `stateRegion`, `stateAccessKey`, `stateSecretKey`
(secret), `backendUrl` (строка для `pulumi login` с префиксом `main/`).

Конфиг (`infra-bootstrap:*`): `s3Pool` (`ru-7`), `bucketName`,
`s3KeyReadyTimeoutSeconds` (по умолчанию 600).

## Учётные данные и секреты

- `~/.config/selectel.env` (права 600): `SELECTEL_USERNAME`, `SELECTEL_PASSWORD`,
  `SELECTEL_DOMAIN_NAME`, `PULUMI_CONFIG_PASSPHRASE`.
- `env.sh` выставляет `OS_USERNAME`, `OS_PASSWORD`, `OS_DOMAIN_NAME`,
  `OS_AUTH_URL=https://cloud.api.selcloud.ru/identity/v3/`, `OS_REGION_NAME=<s3Pool>` и
  экспортирует passphrase. Запуск: `source pulumi/bootstrap/env.sh`.
- Passphrase хранится в менеджере паролей команды.
- Для стейтов в бакете — `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` из выходов bootstrap.

## Первый запуск и перенос стейта

1. `pulumi login file://~/.pulumi-bootstrap-local`, `pulumi stack init main`, `pulumi preview` —
   показать план пользователю (ожидается только создание ресурсов).
2. После подтверждения пользователя — `pulumi up`.
3. Перенос стейта bootstrap в бакет: `pulumi stack export > main.json`;
   `pulumi login "s3://<bucket>/bootstrap?region=<pool>&endpoint=s3.<pool>.storage.selcloud.ru&s3ForcePathStyle=true"`
   с ключами из выходов; `pulumi stack init main`; `pulumi stack import < main.json`;
   `pulumi preview` — без изменений; удалить `main.json` и локальный стейт.

## Основной стек

- Логин: `s3://<bucket>/main?region=<pool>&endpoint=…&s3ForcePathStyle=true` с теми же ключами.
- Переезд `pulumi-cellestial` (делает коллега, у него доступ к `devops-pulumi-state`):
  `pulumi stack export` из старого бакета → `login` в новый префикс → `stack import` →
  `pulumi preview` без изменений. После этого `devops-pulumi-state` можно удалить.
  Последовательность команд — в README bootstrap.

## Ошибки

- `initProjectS3`: успех — любой 2xx (повторный вызов на уже инициализированном проекте отвечает 200 — проверено 2026-09-26 на pulumi-cellestial); любой другой ответ — ошибка с кодом и первыми 300 символами тела.
- `waitForS3Key`: опрос ListBuckets новым ключом каждые 15 с до `s3KeyReadyTimeoutSeconds`;
  сетевые ошибки — неудачная попытка, не успех; по таймауту — ошибка с последним ответом.
- Секреты не передаются в аргументах командной строки (curl получает их через stdin/`-K -`).

## Проверка

1. `bun test` без сети:
   - `selectel-s3.ts` с подменяемой HTTP-функцией: 200, 201, 204, 400, 403, 500, сетевая
     ошибка; ожидание ключа — успех после N попыток и таймаут;
   - `index.ts` с `pulumi.runtime.setMocks`: `protect` у проекта и бакета; одна роль `member`
     scope `project` на `projectId`; версионирование `Enabled`; `forceDestroy` не `true`;
     `stateSecretKey` — secret.
2. На Selectel (после подтверждения пользователя): `preview` → `up` → повторный `preview` без
   изменений → перенос стейта → `preview` без изменений → `aws s3 cp` тестового файла ключами стека
   и удаление → доступ этими ключами к бакетам DevOps_2026H2 запрещён → `get-bucket-versioning` =
   `Enabled`.
3. Команды README выполнены на первом запуске.
