# Bootstrap: бакет стейта Pulumi

Отдельный стек (`infra-bootstrap`, стек `main`) создаёт долгоживущий проект Selectel `infra-shared`:
бакет `cdd-infra-state` со стейтами всех Pulumi-стеков и DNS-зону домена `cellestial.ru.`. Прод
(`pulumi-cellestial`) — отдельный проект, его создаёт и удаляет основной стек. Зачем отдельно — бакет
стейта нельзя создать в стеке, чей стейт в нём лежит (задача #5, спека
`docs/superpowers/specs/2026-09-26-pulumi-bootstrap-design.md`).

| Префикс в бакете | Стейт |
|---|---|
| `bootstrap/` | этот стек |
| `main/` | основной стек (`pulumi/`) |

Доступ к стейту у каждого свой: личный S3-ключ на проект `infra-shared`, выпущенный своим
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
   `infra-shared` и дописывает `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` в `selectel.env`:

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

После `source env.sh` работает и `openstack` CLI (`uv tool install python-openstackclient`) — без
`clouds.yaml`, в проекте стейта по id (другой проект — `SELECTEL_PROJECT=<имя>` в `selectel.env`):
`openstack flavor list`, `openstack image list --public`.
С `SELECTEL_PROJECT` прод-стек не запускается: `OS_PROJECT_NAME` провайдер OpenStack берёт вместе с
`tenantId` проекта, и `pulumi/index.ts` падает заранее с подсказкой. Для `pulumi` — `source env.sh` без
`SELECTEL_PROJECT` (или `unset OS_PROJECT_NAME`).

id проекта стейта записан в `env.sh` (`OS_PROJECT_ID`): имя проекта может меняться, id — нет. Если
bootstrap-стек когда-нибудь пересоздаст проект, обновите id; проверка:
`[ "$(pulumi stack output stateProjectId)" = "$OS_PROJECT_ID" ] && echo ok`.

`pulumi login` глобален: перед работой с основным стеком войдите в его префикс (ниже).

Каждый `pulumi up` (не `preview`) инициализирует S3 в проекте и проверяет ключ `infra-state-s3`
запросом к S3 — это пара секунд и не меняет ресурсы. Если ключ стека отозвали вручную, `up`
будет ждать его до `infra-bootstrap:s3KeyReadyTimeoutSeconds` (600 с) и упадёт с последним ответом S3.
Тесты: `bun run test`.

Выходы `stateAccessKey`/`stateSecretKey` — ключи пользователя `infra-state-s3` для автоматизации
(CI); людям они не нужны.

## Основной стек

`env.sh` по умолчанию выставляет `OS_PROJECT_ID` проекта стейта: ресурсы основного стека должны задавать
проект явно (`projectId`, `tenantId` провайдера OpenStack), а не полагаться на окружение.

DNS-записи прода основной стек создаёт в зоне из этого стека:
`pulumi config set infra:dnsProjectId "$(pulumi -C bootstrap stack output dnsProjectId)"` (выполнять,
пока залогинен в префикс `bootstrap/`), `infra:dnsZone` — `cellestial.ru.`.


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
# логин/пароль Selectel теперь из selectel.env (source env.sh выше); с ними в конфиге
# программа остановится с подсказкой — убрать до первого preview
pulumi config rm selectel:username
pulumi config rm selectel:password
pulumi preview                                          # ожидается: ресурсы без изменений; допустим
                                                        # только ~ update провайдера selectel
                                                        # (логин/пароль ушли из его входов в env).
                                                        # FloatingIp gateway — строго без diff: замена
                                                        # = новый publicIp и A-запись, up не делать
# имя стека — prod. Ресурсы не пересоздаются, в стейте меняются только URN;
# Pulumi.dev.yaml переименовывается в Pulumi.prod.yaml сам
pulumi stack rename prod
pulumi preview                                          # снова без изменений
rm /tmp/cellestial-dev.json
# программа не читает секретов из конфига — secure-значения в нём остатки ранних версий
# (infra:s3AccessKey/infra:s3SecretKey). В публичном репо шифротекст + encryptionsalt дают
# офлайн-перебор passphrase, поэтому вывод должен быть пуст; иначе pulumi config rm <ключ>
grep -n 'secure:' Pulumi.prod.yaml
git add Pulumi.prod.yaml                                # конфиг прода — в репо (секретов нет)
```

Экспорт `dev` нельзя импортировать сразу в стек `prod`: URN в стейте содержат имя стека, и
Pulumi увидит все ресурсы как чужие. Переименование — только через `stack rename` после импорта.

`devops-pulumi-state` удалять только после того, как оба `preview` в новом бакете прошли без
изменений и личные ключи стейта у всех сохранены вне стейта.

## Первый запуск (выполнен 2026-09-26, для справки)

1. `mkdir -p ~/.pulumi-bootstrap-local && pulumi login file://~/.pulumi-bootstrap-local`,
   `pulumi stack init main --secrets-provider passphrase`,
   `pulumi config set infra-bootstrap:s3Pool ru-7`, `pulumi config set infra-bootstrap:bucketName cdd-infra-state`.
2. `pulumi preview` → `pulumi up` (проект `infra-shared`, пользователь, ключ, бакет, версионирование).
3. `bun state-key.ts` — личный ключ стейта (проект `infra-shared` уже существует).
4. Перенос стейта в бакет: `pulumi stack export --show-secrets --file bootstrap-state-export.json`
   (файл в `.gitignore`), `source env.sh`, `pulumi login "s3://cdd-infra-state/bootstrap?…"`,
   `pulumi stack init main --secrets-provider passphrase`,
   `pulumi stack import --file bootstrap-state-export.json`, `pulumi preview` (без изменений),
   удалить файл экспорта и `~/.pulumi-bootstrap-local`.
