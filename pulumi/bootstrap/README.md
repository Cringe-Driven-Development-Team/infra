# Bootstrap: бакет стейта Pulumi

Отдельный стек (`infra-bootstrap`, стек `main`) создаёт в проекте Selectel `infra-state` бакет, в
котором хранятся стейты всех Pulumi-стеков инфраструктуры, и сервисного пользователя с доступом
только к этому проекту. Зачем отдельно — бакет стейта нельзя создать в стеке, чей стейт в нём
лежит (задача #5, спека `docs/superpowers/specs/2026-09-26-pulumi-bootstrap-design.md`).

| Префикс в бакете | Стейт |
|---|---|
| `bootstrap/` | этот стек |
| `main/` | основной стек (`pulumi/`) |

## Что нужно

- Pulumi CLI, bun, Node.js, curl.
- Сервисный пользователь **аккаунта** Selectel с ролями `member` (аккаунт) и `iam.admin` — свой у
  каждого (IAM → Сервисные пользователи).
- Файл `~/.config/selectel.env` с правами 600 — одной строкой в отдельном терминале, пароль и
  passphrase вводятся скрыто:

  ```sh
  install -m600 /dev/null ~/.config/selectel.env && read -rsp 'Selectel password: ' p && echo && read -rsp 'Pulumi passphrase: ' pp && echo && printf 'SELECTEL_USERNAME=<ваш-пользователь>\nSELECTEL_PASSWORD=%s\nSELECTEL_DOMAIN_NAME=631994\nPULUMI_CONFIG_PASSPHRASE=%s\n' "$p" "$pp" > ~/.config/selectel.env && unset p pp
  ```

  Passphrase стека `main` — в менеджере паролей команды.

## Каждый запуск

```sh
cd pulumi/bootstrap
source env.sh                        # OS_* для провайдера Selectel, PULUMI_CONFIG_PASSPHRASE
pulumi install                       # один раз на клоне: SDK selectel в sdks/, зависимости через bun
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…   # ключи стейта (см. «Ключи стейта»)
pulumi login "s3://cdd-infra-state/bootstrap?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true"
pulumi stack select main
pulumi preview
```

Тесты: `bun run test`.

## Ключи стейта

Выходы стека `stateAccessKey` и `stateSecretKey` — ключи S3 для стейтов:

```sh
pulumi stack output stateAccessKey
pulumi stack output stateSecretKey --show-secrets
```

Получить их можно только имея доступ к стейту, поэтому первый раз их передаёт тот, у кого они уже
есть (менеджер паролей команды).

## Основной стек

```sh
cd pulumi
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
pulumi login "$(pulumi -C bootstrap stack output backendUrl)"   # s3://cdd-infra-state/main?…
```

### Переезд стека pulumi-cellestial из devops-pulumi-state

Делает тот, у кого есть доступ к старому бакету:

```sh
cd pulumi
# старый бакет — прежние ключи и login
pulumi stack select dev
pulumi stack export --show-secrets --file /tmp/cellestial-dev.json   # с секретами в открытом виде — не коммитить
# новый бакет
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…     # ключи стейта из bootstrap
pulumi login "s3://cdd-infra-state/main?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true"
pulumi stack init dev --secrets-provider passphrase     # та же passphrase, что у старого стека
pulumi stack import --file /tmp/cellestial-dev.json
pulumi preview                                          # ожидается: без изменений
rm /tmp/cellestial-dev.json
```

После этого `devops-pulumi-state` можно удалить.

## Первый запуск (уже выполнен, для справки)

1. `pulumi login file://~/.pulumi-bootstrap-local`, `pulumi stack init main`,
   `pulumi config set infra-bootstrap:projectId 800b74820d5440a3a00b6b961eccabf7`,
   `pulumi config set infra-bootstrap:s3Pool ru-7`, `pulumi config set infra-bootstrap:bucketName cdd-infra-state`.
2. `pulumi preview` → `pulumi up` (импорт проекта `infra-state`, пользователь, ключ, бакет,
   версионирование).
3. Перенос стейта в бакет: `pulumi stack export --show-secrets --file bootstrap-state-export.json` (файл в `.gitignore`), ключи стейта в
   `AWS_*`, `pulumi login "s3://cdd-infra-state/bootstrap?…"`, `pulumi stack init main`,
   `pulumi stack import --file bootstrap-state-export.json`, `pulumi preview` (без изменений),
   удалить файл экспорта и `~/.pulumi-bootstrap-local`.
