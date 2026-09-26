# Bootstrap: бакет стейта Pulumi

Отдельный стек (`infra-bootstrap`, стек `main`) создаёт проект Selectel `infra-state` и в нём бакет
`cdd-infra-state`, где хранятся стейты всех Pulumi-стеков инфраструктуры. Зачем отдельно — бакет
стейта нельзя создать в стеке, чей стейт в нём лежит (задача #5, спека
`docs/superpowers/specs/2026-09-26-pulumi-bootstrap-design.md`).

| Префикс в бакете | Стейт |
|---|---|
| `bootstrap/` | этот стек |
| `main/` | основной стек (`pulumi/`) |

Доступ к стейту у каждого свой: личный S3-ключ на проект `infra-state`, выпущенный своим
сервисным пользователем. Общих ключей, которые надо передавать из рук в руки, нет.

## Один раз на человека

1. Pulumi CLI, bun, Node.js, curl.
2. Свой сервисный пользователь **аккаунта** Selectel с ролями `member` (аккаунт) и `iam.admin`
   (IAM → Сервисные пользователи; пароль показывается один раз — сразу в менеджер паролей).
3. Файл `~/.config/selectel.env` (права 600): пароль и passphrase вводятся скрыто, passphrase
   стеков — в менеджере паролей команды. Скрипт запускается через `bash` и в macOS (zsh):

   ```sh
   bash pulumi/bootstrap/init-env.sh <ваш-сервисный-пользователь>
   ```

4. Личный S3-ключ стейта — скрипт выпускает его вашему сервисному пользователю на проект
   `infra-state` и дописывает `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` в `selectel.env`:

   ```sh
   cd pulumi/bootstrap && pulumi install && source env.sh && bun state-key.ts
   ```

   Ключ в `selectel.env` уже есть — скрипт откажется; новый — `bun state-key.ts --force` (старый
   удалите в IAM → ваш пользователь → Доступ → S3-ключи). Ушёл человек — удаляют его сервисного
   пользователя, вместе с ним пропадает и доступ к стейту.

## Каждый запуск

```sh
cd pulumi/bootstrap
source env.sh     # OS_* для Selectel, passphrase, личный ключ стейта; личный ~/.aws отключён
pulumi login "s3://cdd-infra-state/bootstrap?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true"
pulumi stack select main
pulumi preview
```

`pulumi login` глобален: перед работой с основным стеком войдите в его префикс (ниже).
Тесты: `bun run test`.

Выходы `stateAccessKey`/`stateSecretKey` — ключи пользователя `infra-state-s3` для автоматизации
(CI); людям они не нужны.

## Основной стек

```sh
source pulumi/bootstrap/env.sh
cd pulumi
pulumi login "s3://cdd-infra-state/main?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true"
```

### Переезд стека pulumi-cellestial из devops-pulumi-state

Делает тот, у кого есть доступ к старому бакету:

```sh
cd pulumi
# старый бакет — прежние ключи и login
pulumi stack select dev
pulumi stack export --show-secrets --file /tmp/cellestial-dev.json   # секреты в открытом виде — не коммитить
# новый бакет — личный ключ стейта
source bootstrap/env.sh
pulumi login "s3://cdd-infra-state/main?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true"
pulumi stack init dev --secrets-provider passphrase     # та же passphrase, что у старого стека
pulumi stack import --file /tmp/cellestial-dev.json
pulumi preview                                          # ожидается: без изменений
rm /tmp/cellestial-dev.json
```

После этого `devops-pulumi-state` можно удалить.

## Первый запуск (выполнен 2026-09-26, для справки)

1. `mkdir -p ~/.pulumi-bootstrap-local && pulumi login file://~/.pulumi-bootstrap-local`,
   `pulumi stack init main --secrets-provider passphrase`,
   `pulumi config set infra-bootstrap:s3Pool ru-7`, `pulumi config set infra-bootstrap:bucketName cdd-infra-state`.
2. `pulumi preview` → `pulumi up` (проект `infra-state`, пользователь, ключ, бакет, версионирование).
3. `bun state-key.ts` — личный ключ стейта (проект `infra-state` уже существует).
4. Перенос стейта в бакет: `pulumi stack export --show-secrets --file bootstrap-state-export.json`
   (файл в `.gitignore`), `source env.sh`, `pulumi login "s3://cdd-infra-state/bootstrap?…"`,
   `pulumi stack init main --secrets-provider passphrase`,
   `pulumi stack import --file bootstrap-state-export.json`, `pulumi preview` (без изменений),
   удалить файл экспорта и `~/.pulumi-bootstrap-local`.
