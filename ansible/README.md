# Ansible: настройка единственной VPS из Pulumi

Инфраструктура: **одна VPS** — публичный IP, Caddy в Docker Compose на домене с Let's Encrypt.
Схемы уже переведены на одну VPS с Docker Compose (Caddy, Go API, Postgres);
прежний вариант с двумя VPS заморожен в docs (раздел bff/infra).

## Установка

Нужен **ansible-core ≥ 2.18** (`community.general` 13.x: `requires_ansible: '>=2.18.0'`); ansible-core
из apt Ubuntu 24.04 — 2.16, не подойдёт.

```bash
cd ansible
pipx install 'ansible-core>=2.18'
pipx inject ansible-core -r requirements.txt            # openstacksdk в venv Ansible (pipx ≥ 1.4)
ansible-galaxy collection install -r requirements.yml   # openstack.cloud, community.general, ansible.posix
```

Ansible не из pipx — `openstacksdk` ставится тем же python, что у Ansible (`ansible --version`,
строка `python version`), иначе inventory не найдёт модуль.

## Подготовка

1. Доступ к OpenStack для inventory и `verify.yml` — одно из двух:
   - `. ./env.sh` (из `ansible/`): учётка из `~/.config/selectel.env`, проект и пул — из прод-стека
     Pulumi, `clouds.yaml` не нужен;
   - `cp clouds.yaml.example clouds.yaml` — сервисный пользователь **аккаунта** Selectel
     (тот же, что в `selectel.env`) и `project_id` из `pulumi stack output projectId`.

   Оба сразу нельзя: openstacksdk откажется от двух облаков `selectel`.
2. Ключ стенда `~/.ssh/selectel_release` (+ `.pub`) — он же `infra:sshPublicKey` в Pulumi
   (логин через keypair при создании сервера).
   Публичные ключи всех, кто работает со стендом, лежат в репозитории:
   `files/authorized_keys/*.pub` (один ключ — один файл с узнаваемым именем). Роль `users`
   кладёт их все в `deploy` c `exclusive: true`, поэтому новый доступ = PR с `.pub`-файлом
   + `ansible-playbook site.yml`; ушедший участник = удалить его `.pub` из репо + прогнать
   `site.yml`. Сервер пересоздавать не нужно.
3. Inventory динамический (`inventory/openstack.yml`): группа `gateway` собирается по
   `metadata.role`, `ansible_host` — floating IP (публичный адрес).
4. Пароль vault — в `~/.config/cdd-vault-pass` (права `600`), путь — в `ANSIBLE_VAULT_PASSWORD_FILE`
   (`. ./env.sh` экспортирует сам; с `clouds.yaml` — выставить руками). Нужен для **любого**
   playbook'а — см. «Секреты».

## Запуск

```bash
ansible-playbook bootstrap.yml   # один раз: python3 + пользователь deploy (root, порт 22)
ansible-playbook site.yml        # базовая настройка VPS + Caddy
ansible-playbook verify.yml      # проверки из DoD (см. ниже)
```

Повторный `site.yml` должен давать `changed=0`.

`bootstrap.yml` ходит под `root`, а после `site.yml` root-логин закрыт — на уже настроенный хост
его повторно не запустить. Он нужен только после пересоздания сервера **с новым диском**
(`pulumi destroy` или замена boot-volume) и запускается без `--limit`.

Пересоздание сервера Pulumi'ем (смена `infra:sshPublicKeys`, `deleteBeforeReplace`) диск сохраняет:
`deploy` и hardening на нём уже есть, bootstrap не нужен (и не пройдёт) — хватает `site.yml`.

Ключи команды на существующем хосте раскатывает `site.yml` (роль `users`), bootstrap для этого не нужен.

## Секреты (ansible-vault)

Секреты выкатки лежат в репо зашифрованными: `inventory/group_vars/all/vault.yml`
(`ansible-vault`, AES256). Репо публичный, шифротекст виден всем — стойкость держится на пароле.

| В `vault.yml` (зашифровано) | Открытое имя в `group_vars/all/vars.yml` |
|---|---|
| `vault_postgres_password` | `postgres_password` |
| `vault_jwt_secret` | `jwt_secret` |
| `vault_csrf_secret` | `csrf_secret` |
| `vault_totp_encryption_key` | `totp_encryption_key` |
| `vault_avatars_s3_access_key` | `avatars_s3_access_key` |
| `vault_avatars_s3_secret_key` | `avatars_s3_secret_key` |
| `vault_session_key` | `session_key` |
| `vault_bff_api_key` | `bff_api_key` |

`avatars_s3_*` — ключ Go API только к объектам бакета аватарок (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`),
значения — выходы Pulumi `avatarsAccessKey` и `avatarsSecretKey` (`pulumi stack output <имя> --show-secrets`,
оба секретные; `pulumi/README.md`, «Бакет аватарок»). Ключ перевыпущен (пересоздан стек или пользователь) — обновить
оба значения через `ansible-vault edit`. В `.env` Go API они попадут через роль `app`
([backend#2](https://github.com/Cringe-Driven-Development-Team/backend/issues/2)).

Несекретные параметры S3 для бэка лежат открыто в `group_vars/all/vars.yml`: `s3_endpoint`,
`s3_region`, `s3_force_path_style`, `avatars_bucket`, `avatars_public_domain` —
значения из `pulumi stack output`; `avatars_public_domain` — свой домен бакета аватарок (выход
`avatarsCustomDomain`), сертификат к нему выпускается в панели.

`app_cors_allowed_origins` там же — origin'ы, которым Go API подтверждает CORS (`CORS_ALLOWED_ORIGINS`
в `.env`): на проде только `https://cellestial.ru`, без `localhost`. Не секрет, в vault не кладётся.

`session_key` — ключ AES-256-GCM сессии BFF (`SESSION_KEY`): base64 ровно 32 байт
(`openssl rand -base64 32`). Он один для выпуска и отката: смена ключа сбрасывает все сессии.
`bff_api_key` — S2S-ключ запросов BFF к Go (`X-BFF-Key`, backend#41): BFF получает его как `BFF_KEY`,
Go — как `BFF_API_KEY`; 32+ символов `[A-Za-z0-9_-]` (`openssl rand -hex 32`). Без них или в неверном
формате выкатка останавливается до изменений VPS.

`vault_totp_encryption_key` — постоянный отдельный ключ шифрования TOTP, standard base64
ровно 32 байта. Если ключ уже использовался, перенести прежний без изменения. Только при первой
настройке сгенерировать `openssl rand -base64 32` один раз и записать через
`ansible-vault edit inventory/group_vars/all/vault.yml`. JWT, session и runtime-ключи для этого
не использовать. Сохранять ключ между выкладками и вместе с защищённой резервной копией БД:
замена ключа делает сохранённые TOTP-секреты нечитаемыми. Значение передаётся только API через
`.env` и явный `services.api.environment.TOTP_ENCRYPTION_KEY`; validation отклоняет отсутствующий
или некорректный ключ до записи файлов сервера, не раскрывая его в выводе.

Блокноты хранятся в PostgreSQL; S3-бакета блокнотов нет (архив удалён в infra#64). Однократный сброс БД и отчёт приёмки —
[RUNBOOK, infra#112](../RUNBOOK.md#восстановление-api-полный-сброс-бд-приложения-infra112).

Роли и шаблоны используют только открытые имена; `vault_*` напрямую не читаются. Новый секрет —
переменная `vault_<имя>` в `vault.yml` и строка `<имя>: "{{ vault_<имя> }}"` в `vars.yml`.

### Пароль

Пароль vault — у Дениса и менторов, передаётся лично, не в общий чат. Он лежит в файле вне репо, путь
к файлу — в `ANSIBLE_VAULT_PASSWORD_FILE`; в `ansible.cfg` путь не прописывается (у ноутбука и CI он
разный).

```bash
umask 077
printf '%s\n' '<пароль>' > ~/.config/cdd-vault-pass     # права 600
. ./env.sh                                               # экспортирует ANSIBLE_VAULT_PASSWORD_FILE
# без env.sh (clouds.yaml): export ANSIBLE_VAULT_PASSWORD_FILE=~/.config/cdd-vault-pass
```

`vault.yml` лежит в `group_vars/all`, поэтому пароль нужен для любого playbook'а: `site.yml`,
`verify.yml`, `bootstrap.yml`. Без него запуск падает (`Attempting to decrypt but no vault secrets
found`), а не идёт с пустыми значениями.

### Просмотр и правка

```bash
ansible-vault view inventory/group_vars/all/vault.yml
ansible-vault edit inventory/group_vars/all/vault.yml    # $EDITOR, при сохранении шифрует обратно
```

Только `edit`: `decrypt` → правка → `encrypt` оставляет на диске открытый текст, который легко
закоммитить. Значения секретов — `openssl rand -base64 32` (кроме `avatars_s3_*` — они из Pulumi).

### CI

- **Выкатка из CI** (job `deploy`,
  [backend#2](https://github.com/Cringe-Driven-Development-Team/backend/issues/2)): пароль приходит из
  секрета `ANSIBLE_VAULT_PASSWORD`, job пишет его во временный файл и выставляет
  `ANSIBLE_VAULT_PASSWORD_FILE` — playbook'и читают его так же, как с ноутбука:

  ```yaml
  - name: Пароль vault во временный файл
    env:
      ANSIBLE_VAULT_PASSWORD: ${{ secrets.ANSIBLE_VAULT_PASSWORD }}
    run: |
      umask 077
      printf '%s\n' "$ANSIBLE_VAULT_PASSWORD" > "$RUNNER_TEMP/vault-pass"
      echo "ANSIBLE_VAULT_PASSWORD_FILE=$RUNNER_TEMP/vault-pass" >> "$GITHUB_ENV"
  # ... шаги с ansible-playbook ...
  - name: Удалить файл пароля
    if: always()
    run: rm -f "$RUNNER_TEMP/vault-pass"
  ```

  Секрет передаётся через `env`, а не подстановкой `${{ }}` в текст скрипта. Сам секрет заводится в
  backend#2.

### Смена пароля

```bash
openssl rand -base64 48 > ~/.config/cdd-vault-pass.new && chmod 600 ~/.config/cdd-vault-pass.new
ansible-vault rekey --new-vault-password-file ~/.config/cdd-vault-pass.new \
  inventory/group_vars/all/vault.yml
mv ~/.config/cdd-vault-pass.new ~/.config/cdd-vault-pass
```

Затем: закоммитить перешифрованный `vault.yml`, обновить секрет `ANSIBLE_VAULT_PASSWORD` в CI,
передать новый пароль лично тем, у кого был старый.

### Утечка пароля

`rekey` **не помогает**: старый шифротекст остаётся в истории git публичного репо и расшифровывается
утёкшим паролем. Меняются сами секреты:

1. новый пароль vault — как в «Смена пароля»;
2. `ansible-vault edit` — новые значения секретов (`openssl rand -base64 32`), **кроме**
   `avatars_s3_*`;
3. S3-ключ Go API (`avatars_s3_*`) случайной строкой не заменить: бэк получит `InvalidAccessKeyId`,
   а утёкший ключ останется рабочим (чтение, запись и удаление аватарок). Ключ
   перевыпускается в Pulumi:

   ```bash
   cd ../pulumi
   pulumi stack --show-urns | grep avatars-s3     # URN ресурса IamS3CredentialsV1 "avatars-s3"
   pulumi up --replace '<URN avatars-s3>'
   pulumi stack output avatarsAccessKey --show-secrets
   pulumi stack output avatarsSecretKey --show-secrets
   ```

   новые значения — в `vault.yml` через `ansible-vault edit`;
4. выкатить: пароль Postgres меняется и в самой БД, смена `jwt_secret` разлогинивает пользователей;
5. убедиться, что старый S3-ключ больше не действует: запрос с ним к бакету аватарок должен
   вернуть `InvalidAccessKeyId`;
6. обновить `ANSIBLE_VAULT_PASSWORD` в CI.

## Что настраивается

| Роль | Хосты | Содержимое |
|---|---|---|
| users | все | пользователь `deploy`, authorized_keys из `files/authorized_keys/*.pub` (exclusive, с проверкой ключа запускающего), sudo NOPASSWD — в bootstrap и в каждом site.yml |
| common | все | базовые пакеты |
| ssh_hardening | все | `00-hardening.conf` (validate через `sshd -t`): без root-логина и паролей; gateway разрешает SSH jump только к приватной VPS2 (`PermitOpen`), на остальных хостах forwarding запрещён; Ubuntu 24.04 — socket activation, рестарт `ssh.socket` + `ssh.service` |
| firewall | все | ufw: deny incoming; публичный gateway — 22/80/443, приватная VPS2 — 22/8080 только с gateway |
| docker | все | Docker Engine + Compose plugin, `deploy` в группе docker (про порты — ниже) |
| caddy | gateway | Проект Compose `/opt/cellestial` (`compose.yml`, сеть `app`), Caddy в контейнере (`caddy:2.11-alpine`, 80/443), Caddyfile с доменом `app_domain`: все пути, кроме закрытого `/health`, — BFF (до его первой выкатки — `503 BFF is not deployed`), раздел «Клиент и BFF»; сертификаты Let's Encrypt — в volume `caddy_data`. Caddy, ранее поставленный из apt, удаляется |

| hooks | auxiliary | GitHubBot (long polling, `githubbot.env` 0600, отдельный том `hooks_githubbot_data`, без портов) и проект Compose `/opt/hooks`: Caddy (80/443, сертификат `hooks_domain`) и контейнер сервиса вебхуков во внутренней сети `hooks` — его порт не публикуется; том `hooks_data` (`hooks_data_path`), `.env` из `vault_hooks_env` (0600). Публичный image `webhook-alerts` по registry digest; анонимный pull; worker по умолчанию выключен |
| restic_backup | auxiliary | ночной CLI snapshot очереди перед `restic backup`; только snapshot, тома Caddy и `/opt/hooks` в бакет бэкапов, `forget` 7 дневных / 4 недельных; ключи — из Vault |

Плейбуки: `site.yml` и `verify.yml` обслуживают gateway и приватную VPS2;
`deploy-backend.yml` выкатывает Go API только на `backend`, `deploy-bff.yml` — BFF только
на `gateway`. `auxiliary.yml` обслуживает вспомогательную VPS (группа `auxiliary`,
RUNBOOK, «Вспомогательная VPS»).

## Проект Compose

На каждом хосте свой проект Compose в `/opt/cellestial` и своя сеть `app`:
на gateway — Caddy и BFF, на VPS2 — Go API, Postgres и Redis. Caddy обращается к BFF
по имени сервиса (`reverse_proxy bff:3000`), BFF — к приватному адресу VPS2
(`http://<private-IP-VPS2>:8080/api/v1`). Базы и BFF не публикуют порты; Go публикует
8080 только на приватном адресе VPS2 с доступом с gateway.
Смена Caddyfile применяется `caddy reload` внутри контейнера, без рестарта.

## Порты контейнеров и ufw

Docker публикует порты контейнеров (`-p 8080:80`) своими iptables-правилами в цепочке
`nat`/`DOCKER`, которые обрабатываются **до** ufw: опубликованный порт откроется наружу,
даже при `deny incoming`. Правила на стенде:

- **Postgres, Redis и BFF не публикуют порты**. Go API публикует
  `<private-IP-VPS2>:8080:8080`; security group и ufw разрешают доступ только с gateway.
  Loopback хоста VPS2 не заменяет этот приватный адрес. Проверка loopback внутри контейнера
  остаётся частью его healthcheck;
- наружу смотрит **только Caddy** (80/443) и ssh (22); floating IP — 1:1 DNAT на адрес
  сервера, поэтому любой `-p 0.0.0.0:X` открыт в интернет мимо ufw;
- если нужен фильтр-страховка — править цепочку `DOCKER-USER` (ufw её не трогает). Правило
  вставлять в начало (`-I`): добавленное через `-A` окажется после `RETURN` и не сработает.
  Интерфейс — внешний (`ip route show default`), например — все новые соединения снаружи к
  контейнерам, кроме 80/443:
  `iptables -I DOCKER-USER -i eth0 -p tcp -m conntrack --ctstate NEW -m multiport ! --dports 80,443 -j DROP`
  (в `DOCKER-USER` порт уже после DNAT — порт контейнера; у Caddy он те же 80/443).
  Такое правило не переживает перезагрузку — после reboot его нужно вернуть (или завести в Ansible).

## Клиент и BFF

Браузер ходит только в BFF (infra#58, [трек BFF](https://docs.cellestial.ru/modules/2/tracks/bff)):

| Путь | Куда |
|---|---|
| `/health` | `404` от Caddy: внутренний health BFF снаружи не виден |
| остальные | `reverse_proxy bff:3000` |

BFF сам разбирает остальное: `/api/trpc` и `/api/trpc/*` — tRPC; `/api/v1/*` и прочие `/api/*` —
`404`, в Go не проксируются и не становятся HTML; публичные файлы сборки — по списку;
`GET`/`HEAD` страниц с `Accept: text/html` — HTML клиента с пользователем в
`<script id="bootstrap">` и `Cache-Control: private, no-store`. Go API напрямую снаружи недоступен.

- HTML берётся из образа BFF своего коммита, не из бакета: S3-указатель `current.json` и копия
  корневого `index.html` версию на проде больше не меняют. Чанки JS и CSS браузер грузит с CDN
  каталога своего релиза `releases/{sha}/` (`frontend_cdn_domain`), через Caddy они не идут.
- До первой выкатки BFF (в `.env` на VPS нет `BFF_IMAGE_REF`) Caddy на всех путях, кроме `/health`,
  отвечает `503` с телом `BFF is not deployed`.
- Все ответы Caddy несут `X-Content-Type-Options: nosniff`, ответы BFF — ещё и
  `Content-Security-Policy`, и браузер её применяет: скрипты — только с `frontend_cdn_domain`, стили — с него
  и со своего домена, картинки — со своего домена, с него, `frontend_static_domain`,
  `avatars_public_domain` и из `data:` (иконки-маски), шрифты — с `frontend_static_domain`, запросы,
  манифест и service worker — со своего домена. Политика задаётся в `Caddyfile.j2`, режим — в
  `caddy_csp_report_only` (`inventory/group_vars/all/vars.yml`); в BFF заголовка нет. Новый источник
  ресурсов у клиента (чужой домен, воркер) — сначала правка политики, иначе браузер его заблокирует.
  При правке проверьте заголовок `curl -sI https://<app_domain>/login` и пройдите экраны в браузере:
  нарушения видны в консоли и на событии `securitypolicyviolation`.
- Чанки браузер грузит с CDN (`<script type="module" crossorigin>`, запрос в режиме CORS). CORS
  настраивать не нужно: CDN-ресурс Selectel на запрос с `Origin` сам отвечает
  `Access-Control-Allow-Origin: *`. Проверка:

  ```bash
  curl -sI -H 'Origin: https://cellestial.ru' "https://$(cd ../pulumi && pulumi stack output cdnDefaultDomain)/index.html" \
    | grep -i access-control-allow-origin
  ```

## Проверки verify.yml

- ровно один сервер в inventory (проект) — второй VPS больше нет;
- до первой выкатки BFF `https://<app_domain>/` отвечает `503 BFF is not deployed` с `nosniff`;
- после неё (в `.env` на VPS есть `BFF_IMAGE_REF`) — те же проверки, что в конце `deploy-bff.yml`:
  запущенный контейнер `bff` — образ `BFF_IMAGE_REF` из `.env` (тот же digest и image ID) и
  `healthy` (`roles/app/tasks/check-bff-image.yml`); снаружи (`roles/app/tasks/probe-bff.yml`) —
  гостевой HTML `/login` с ровно одним `<script id="bootstrap">` и `{"user":null}`,
  `<meta name="release">` с полным `BFF_RELEASE_SHA`, `Cache-Control: private, no-store`,
  `nosniff` и CSP; `users.me` без сессии — tRPC `UNAUTHORIZED` (401); `/api/v1/users/me`,
  `/api/unknown` и `/health` — `404` и не HTML;
- сертификат от Let's Encrypt;
- после выкатки бэка с Redis (есть `redis/redis.conf`) Redis отвечает `PONG` на `redis-cli -h redis ping` из сети `app` (одноразовый
  контейнер, пароль — через окружение);
- на публичном IP открыты 22/80/443 и закрыты 5432, 6379, 8080, 2375, 2376 (ловит порты Docker
  в обход ufw; Go API при переезде в Compose добавит свой порт в этот список закрытых);
- вспомогательная VPS (группа `auxiliary`, если она есть в inventory): `https://<hooks_domain>/health` — 200 с
  действительным сертификатом Let's Encrypt на этот домен; снаружи закрыты внутренний порт сервиса
  (`hooks_port`) и Docker API 2375/2376;
- ufw: active, `deny (incoming)`, разрешающих правил ровно `firewall_rules` из
  `group_vars/gateway` (22/80/443 tcp);
- `sshd -T`: `permitrootlogin no`, `passwordauthentication no`,
  `kbdinteractiveauthentication no`, `allowtcpforwarding no`;
- вне allowlist (22/80/443) на интерфейсах, отличных от loopback, ничего не слушает.

CDN-ресурс и свои домены CDN и бакета создаёт Pulumi, сертификаты доменов — в панели (`pulumi/README.md`, «Свои домены»).

## CI/CD инфраструктуры

Workflow `.github/workflows/infra.yml` запускается на изменения в `pulumi/`, `ansible/` и в нём самом:

| Событие | Что делает |
|---|---|
| PR | тесты Pulumi (`tsc`, `bun test`), синтаксис плейбуков и `tests/`; затем по проду `pulumi preview --diff` и `site.yml --check --diff` — вывод в сводке запуска (Summary). На проде ничего не меняется |
| мерж в `main`, ручной запуск из `main` | `pulumi up`, `site.yml`, `verify.yml` |

Выкатки идут по одной (`concurrency` без отмены). Запуск с `pulumi up` не отменять: прерванная
запись стейта оставляет пустой `prod.json` (`pulumi/README.md`, «Разбор ошибок»). Шаги, которые
делают руками в панели (привязка домена бакета, сертификаты), CI не делает: `up` на них падает, как и
локальный.

Ansible в CI ходит по статическому `inventory/ci.yml` своим ключом `ci-infra`, Pulumi — от своего
сервисного пользователя Selectel. Секреты — в двух environments репозитория с одинаковым набором:
`production` (только `main`, выкатка) и `infra-preview` (без ограничения веток, preview в PR):

| Секрет | Что в нём |
|---|---|
| `SELECTEL_ENV_FILE` | содержимое `selectel.env` CI: `SELECTEL_USERNAME`, `SELECTEL_PASSWORD`, `SELECTEL_DOMAIN_NAME`, `PULUMI_CONFIG_PASSPHRASE`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — его читает `pulumi/bootstrap/env.sh` |
| `INFRA_DEPLOY_SSH_KEY` | приватный ключ `ci-infra` без passphrase |
| `SSH_KNOWN_HOSTS` | host key `cellestial.ru`, сверенный по fingerprint из консоли сервера в панели |
| `ANSIBLE_VAULT_PASSWORD` | пароль `inventory/group_vars/all/vault.yml` |

Подключение, один раз:

1. Сервисный пользователь CI — в панели, как личный (`pulumi/bootstrap/README.md`, «Один раз на
   человека»): пользователь **аккаунта** с узнаваемым именем (например `cellestial-infra-ci`), роли
   `member` и `iam.admin`. Его `selectel.env` и ключ стейта — отдельным файлом, не поверх своего:

   ```sh
   SELECTEL_ENV=$HOME/.config/selectel-ci.env bash pulumi/bootstrap/init-env.sh cellestial-infra-ci
   cd pulumi/bootstrap && pulumi install && SELECTEL_ENV=$HOME/.config/selectel-ci.env sh -c '. ./env.sh && bun state-key.ts'
   ```

2. Ключ SSH: `ssh-keygen -t ed25519 -N '' -C ci-infra -f ~/.ssh/cdd-ci-infra`; публичная часть —
   `files/authorized_keys/ci-infra.pub` (PR), затем `ansible-playbook site.yml` своим ключом, чтобы
   ключ CI попал на сервер до первой выкатки из CI.
3. Секреты в оба environment (нужны права администратора репозитория):

   ```sh
   for env in production infra-preview; do
     gh secret set SELECTEL_ENV_FILE       --env "$env" < ~/.config/selectel-ci.env
     gh secret set INFRA_DEPLOY_SSH_KEY    --env "$env" < ~/.ssh/cdd-ci-infra
     ssh-keyscan -t ed25519 cellestial.ru | gh secret set SSH_KNOWN_HOSTS --env "$env"
     gh secret set ANSIBLE_VAULT_PASSWORD  --env "$env" < ~/.config/cdd-vault-pass
   done
   ```

   Fingerprint из `ssh-keyscan` сверить с консолью сервера (`ssh-keygen -lf
   /etc/ssh/ssh_host_ed25519_key.pub`). Environment `infra-preview` создаётся этой же командой.
4. Файлы `selectel-ci.env` и `~/.ssh/cdd-ci-infra` после загрузки удалить с машины. Отозвать доступ
   CI — удалить сервисного пользователя в панели и `ci-infra.pub` из репозитория.

## BFF: выкатка, проверка и откат

Публичный образ `ghcr.io/cringe-driven-development-team/bff` публикует CI фронта
([frontend#4](https://github.com/Cringe-Driven-Development-Team/frontend/issues/4)): в нём `bff.js` и
`index.html` клиента одного коммита, а метка `org.opencontainers.image.revision` — полный SHA этого
коммита. Выкатка и откат — один entrypoint `deploy-bff.yml` с неизменяемым образом по digest
(infra#74), без пересборки, без тегов и без GHCR credentials:

```bash
cd ansible
export ANSIBLE_VAULT_PASSWORD_FILE="$HOME/.config/cdd-vault-pass"
# реальные значения — из записи release-artifacts выпуска (docs/release.md во frontend)
BFF_IMAGE_REF='ghcr.io/cringe-driven-development-team/bff@sha256:...'   # 64 hex после sha256:
BFF_RELEASE_SHA='...'                                                    # полный SHA, 40 hex
ansible-playbook -i inventory/ci.yml deploy-bff.yml \
  -e "bff_image_ref=$BFF_IMAGE_REF" \
  -e "bff_release_sha=$BFF_RELEASE_SHA" \
  -e deploy_key_path="$HOME/.ssh/selectel_release" \
  --ssh-common-args='-o StrictHostKeyChecking=yes'
```

- До изменений VPS проверяются входы: `bff_image_ref` — только
  `ghcr.io/cringe-driven-development-team/bff@sha256:<64 hex>` (тег, другой package или registry
  отклоняются), `bff_release_sha` — полный SHA из 40 hex; и секреты BFF (`session_key`, `bff_api_key`).
- Затем образ скачивается на VPS **анонимно**: `docker --config <пустой временный каталог> pull` —
  без `docker login` и без credentials, даже если они есть на VPS. Метка
  `org.opencontainers.image.revision` скачанного образа должна совпасть с `bff_release_sha`.
  Приватный package, запрет анонимного скачивания, неизвестный digest или чужой релиз останавливают
  выкатку здесь: `.env`, `compose.yml`, Caddy и контейнеры остаются прежними. Package `bff` в GHCR
  должен быть публичным (GitHub → Packages → bff → Package settings → Change visibility → Public).
- В `.env` пишутся `BFF_IMAGE_REF` (ссылка по digest) и `BFF_RELEASE_SHA`; Compose запускает
  `image: ${BFF_IMAGE_REF}` с `pull_policy: never` и `--wait`. Полный digest-ref Go берётся из `.env` на VPS:
  выкатка BFF не трогает API, а `deploy-backend.yml` не меняет BFF (`bff_image_ref` он не принимает).
  Без выкаченного Go BFF не выкатывается (он ждёт `api` в состоянии healthy).
- После запуска проверяется фактически запущенный контейнер: его образ — тот же `BFF_IMAGE_REF` и
  image ID, состояние `healthy` (`roles/app/tasks/check-bff-image.yml`). Затем Caddy переключается на
  BFF и выполняются проверки снаружи (`roles/app/tasks/probe-bff.yml`, раздел «Проверки verify.yml»),
  `<meta name="release">` сверяется с `bff_release_sha`. Провал любой проверки — ошибка playbook, а
  не успех. Что остаётся на VPS, зависит от места сбоя:
  - до запуска (скачивание образа, сверка метки) — ничего не менялось;
  - при записи файлов или запуске Compose (роль `app`) — `.env` и `compose.yml` прежней выкатки
    восстанавливаются, контейнер — нет;
  - в проверках после запуска (запущенный образ, healthy, проба снаружи) — новая выкатка уже записана
    и запущена: `.env` и `compose.yml` — нового релиза.

  В двух последних случаях нужна выкатка рабочего digest (откат — той же командой).
- Повторная выкатка ранее опубликованного digest (в том числе откат) — та же команда с его
  `bff_image_ref` и `bff_release_sha`: возвращаются его HTML и ссылки на ассеты его SHA (их хранит
  retention фронта). Откат BFF не откатывает Go и БД.
- `SESSION_KEY` — из Vault и одинаков у выпуска и отката: действительные cookie сессии остаются
  читаемыми. `GO_API_URL` — `app_bff_go_api_url` (`roles/app/defaults/main.yml`,
  `http://<private-IP-VPS2>:8080/api/v1`); адрес берётся из inventory.
- `site.yml` образ BFF не меняет: `compose.yml` и `.env` после первой выкатки ведёт только роль `app`.

## Приватная VPS2: Go API, Postgres, Redis (infra#59)

Группа `backend` — сервер Pulumi с `metadata.role: backend`, без публичного IP; `ansible_host` — его
приватный адрес. Ansible ходит на неё через gateway: `ansible_ssh_extra_args` в `group_vars/backend`
задаёт `ProxyCommand` с ключом `deploy_key_path` (known_hosts gateway — `jump_known_hosts`), а sshd на
gateway пускает пересылку только на `VPS2:22` (`ssh_hardening_jump_targets`). `site.yml` настраивает VPS2 так же,
как gateway, без Caddy; ufw на ней пускает 22 и 8080 только с `app_gateway_address`.

Раскладка фиксирована: Caddy и BFF работают на gateway, Go API, Postgres и Redis — на
приватной VPS2. Порт API опубликован только на приватном адресе VPS2. В `.env` каждой
машины — только секреты её сервисов; `BFF_API_KEY` нужен обеим сторонам. `deploy-backend.yml`
выкатывает приложение только на группу `backend`; отсутствие или несколько VPS2 приводят
к ошибке предварительной проверки inventory. Gateway участвует в делегированных проверках.
`deploy-bff.yml` работает только на gateway и перед запуском требует доступный Go на VPS2.
Откат версии повторно выкатывает прежний digest на VPS2, без возврата сервисов на gateway.
Порядок выкатки и отката — [RUNBOOK.md](../RUNBOOK.md#go-и-postgres-на-vps2-infra59).

## Go API: первая выкатка и откат

На приватной VPS2 роль `app` управляет API, PostgreSQL 18 и Redis 8 в Compose-проекте
`/opt/cellestial`. Порт Go 8080 опубликован только на приватном адресе VPS2 для gateway;
Postgres и Redis доступны внутри сети `app`. На gateway находятся BFF и Caddy; роль `caddy`
управляет общим Caddyfile. Данные Postgres сохраняются в `cellestial_postgres_data`, Redis —
в `cellestial_redis_data` (AOF), сертификаты — в существующих `cellestial_caddy_data` и
`cellestial_caddy_config`. Не выполнять `down -v`.

Backend скачивается из публичного GHCR package `ghcr.io/cringe-driven-development-team/backend`.
Перевод package в Public и проверка реального анонимного pull выполняются вручную в рамках
backend#42 до применения infra#69. Новый playbook не использует GHCR credentials на runner или
сохранённую авторизацию Docker на VPS; публикация образов по-прежнему требует права записи.
CD бэка (`team/backend`) входит своим CI-ключом: public key —
`files/authorized_keys/ci-team-backend.pub` (на хосты его кладёт роль `users` через `site.yml`),
private key — только в secret `DEPLOY_SSH_KEY` environment `production` бэка, там же
`SSH_KNOWN_HOSTS` и пароль Vault в `ANSIBLE_VAULT_PASSWORD`. Личный ключ оператора в CI не передаётся.

Для `team/frontend` (CD и Rollback BFF, `deploy-bff.yml`) — своя пара CI: public key —
`files/authorized_keys/gateway/ci-team-frontend.pub` (только на gateway), private key — только в secret `DEPLOY_SSH_KEY`
environment `production` фронта, там же `SSH_KNOWN_HOSTS` gateway и `ANSIBLE_VAULT_PASSWORD`.
На gateway ключ попадает через `site.yml` при мерже PR с ним.

Для выкатки Selectel и Pulumi не нужны. Не подключайте `env.sh`: используйте
статический inventory `inventory/ci.yml` и явно экспортируйте путь к паролю:

```bash
cd ansible
python3 -m venv /tmp/cdd-deploy-venv
. /tmp/cdd-deploy-venv/bin/activate
pip install -r deploy-requirements.txt
ansible-galaxy collection install -r deploy-requirements.yml
export ANSIBLE_VAULT_PASSWORD_FILE="$HOME/.config/cdd-vault-pass"
# Вставьте точный digest опубликованного образа, выбранный backend workflow.
BACKEND_IMAGE_REF='ghcr.io/cringe-driven-development-team/backend@sha256:REPLACE_WITH_64_HEX'
ansible-playbook -i inventory/ci.yml deploy-backend.yml \
  -e "backend_image_ref=$BACKEND_IMAGE_REF" \
  -e deploy_key_path="$HOME/.ssh/selectel_release" \
  -e jump_known_hosts="$HOME/.ssh/known_hosts" \
  --ssh-common-args="-o UserKnownHostsFile=$HOME/.ssh/known_hosts -o StrictHostKeyChecking=yes"
```

Перед запуском замените `REPLACE_WITH_64_HEX` на 64 строчных hex-символа реального digest.
Выкатка требует один gateway и ровно одну приватную VPS2 в группе `backend`.
Go, PostgreSQL и Redis работают на VPS2, опубликованный порт 8080 привязан только к приватному
адресу; Caddy и BFF работают на gateway. SSH к VPS2 идёт существующим jump-путём через gateway,
проверка host key сохраняется. Адреса берутся из inventory.

`backend_image_ref` принимает только полный ref указанного backend package. Тег, другой package,
неправильный digest или неподходящая топология отклоняются до изменений. Старый
`backend_image_tag` не поддерживается. Переменные `GHCR_USERNAME`/`GHCR_TOKEN` не требуются.

До записи `.env` и `compose.yml` Ansible скачивает точный ref на VPS2 через временный пустой
Docker config. Login/logout не выполняются; сохранённые credentials VPS не используются, временный
каталог удаляется и при ошибке. Если образ недоступен, конфигурация и контейнеры остаются прежними.
После успешного pull `.env` с правами `0600` и `no_log` хранит `BACKEND_IMAGE_REF`. Compose
использует полный ref, `pull_policy: never`, запускается с `pull: never` и ожиданием healthcheck.
Секреты приложения из Vault продолжают записываться на соответствующие VPS с прежними ограничениями;
они не включаются в вывод проверок.

После запуска и применения handlers обязательны последовательные проверки:

1. `Backend image check`: ровно один работающий контейнер `api`, его локальный `.Image` совпадает
   с `.Id` локального образа, найденного по `backend_image_ref`; контейнер running и healthy.
   Registry digest и локальный image ID напрямую не сравниваются.
2. `Backend internal HTTP check`: запрос с gateway к приватному адресу VPS2 `:8080/health`,
   одновременно HTTP 200 и JSON `status: alive`. Runner не обращается к приватному HTTP напрямую.
   Redirects и proxy запрещены; timeout 5 секунд, максимум шесть запросов с интервалом 5 секунд.
3. `Backend external BFF check`: runner выполняет HTTPS POST `/api/trpc/auth.login`, отправляя
   `Origin`, `X-CSRF: 1`, JSON с новым случайным login из 32 hex-символов и паролем
   `deploy-probe-invalid-password`. Успех — одновременно HTTP 400, `BAD_REQUEST` и
   `invalid_credentials`. Login один на все повторы; максимум шесть запросов, timeout 5 секунд,
   интервал 5 секунд. TLS проверяется, redirects запрещены; существующий S2S-ключ добавляет BFF,
   для запроса runner ключ не получает.

Сохраняются проверки `/login` и гостевого `users.me`. Затем `/api/v1/users/me`, `/api/unknown`
и `/health` с runner должны вернуть 404 без HTML. Общие HTTP tasks используются также BFF playbook;
backend-выкатка читает текущий BFF release на gateway, не меняя его образ. Отсутствие BFF release
или ошибка любой обязательной проверки завершают playbook неуспехом. Уже пройденные задачи остаются
видны в логе; общий успех нельзя выводить из одного успешного раннего этапа.

`/health` подтверждает живой HTTP-процесс. Отрицательный login дополнительно подтверждает
HTTPS → BFF → Go → чтение PostgreSQL. Эти проверки не доказывают успешный вход, создание сессии,
работу Redis или runtime-VPS. Передача результатов в Actions Health Check/Summary — часть backend#42;
интеграция проверяется после готовности обеих задач. Отдельного JSON-файла отчёта нет.

Ручной повтор выкатки прежней версии использует ту же команду с прежним успешным digest этого
backend package. Цель выбирает workflow backend#42 из GitHub Deployments; отдельное SSH-чтение
Docker перед выбором цели не требуется. Ошибка после переключения не означает, что старый
контейнер восстановлен. Автоматического отката нет: изучите результаты и повторите выкатку
подтверждённого рабочего digest. Миграции и данные БД playbook не откатывает; `goose Down`
не запускается.

`site.yml` работает до первой выкатки и после неё сохраняет полный `compose.yml`/`.env`,
управляет сервисом Caddy и обновляет маршрут BFF. API он не скачивает и его образ не меняет.
GHCR credentials для `site.yml` не нужны.

S3 передаётся API через `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_ENDPOINT_URL_S3`, `S3_AVATARS_BUCKET`, `S3_AVATARS_PUBLIC_URL`.
Источники — `group_vars/all/vars.yml` и существующий Vault. API использует S3 для аватаров;
блокноты и результаты хранятся в PostgreSQL. `S3_NOTEBOOKS_BUCKET` в окружении API отсутствует.
Блокноты из архивного бакета не переносятся (backend#79): объекты и общий AWS-доступ сохраняются,
API бакет не использует.

Redis (infra#46) — refresh-сессии бэка. API получает `REDIS_HOST=redis`, `REDIS_PORT=6379`,
`REDIS_PASSWORD` (Vault: `vault_redis_password`, 16+ символов `[A-Za-z0-9_-]` — например
`openssl rand -hex 32`) и `REDIS_DB=0`. Пароль лежит в `/opt/cellestial/redis/redis.conf`
(`requirepass`, права `0600`, владелец — пользователь `redis` контейнера), а не в аргументах
`redis-server`; каталог `redis/` смонтирован целиком, после изменения конфига handler роли пересоздаёт
Redis и ждёт `healthy` (таймаут — ошибка выкатки). `appendonly yes` — сессии переживают перезапуск; `maxmemory 128mb` с
`noeviction` — при нехватке памяти Redis отвечает ошибкой, а не вытесняет сессии. Redis
появляется на VPS с выкаткой бэка (`deploy-backend.yml`): `site.yml` проект приложения не меняет.

Проверка после выкатки:

```bash
ansible-playbook -i inventory/ci.yml verify.yml
ssh deploy@cellestial.ru 'cd /opt/cellestial && sudo docker compose ps'
```

Статический inventory подтверждает один указанный хост, а не количество серверов
в Selectel: проверка количества серверов проекта требует динамического inventory.
Для CI достаточно `deploy-requirements.*`; `site.yml` и `verify.yml` требуют также
коллекций из `requirements.yml`. У CI свой `deploy_key_path` и доверенный
`UserKnownHostsFile`, проверка host key обязательна.

Локальные проверки без VPS и настоящих секретов:

```bash
RESTIC_CLI=restic COMPOSE_CLI='docker compose' python -m unittest discover -s tests -v
ansible-lint deploy-backend.yml roles/app roles/caddy verify.yml
```

По умолчанию `ansible.cfg` выбирает только `inventory/openstack.yml`: не указывайте
каталог `inventory` целиком, иначе Ansible объединит динамический и статический
хосты. Для CI всегда передавайте `-i inventory/ci.yml`.

Выкатка и ручной откат backend не передают GHCR credentials. Новое подключение workflow,
публичность package и реальный анонимный pull по digest проверяются в backend#42. Порядок:
подготовить оба PR → вручную открыть package и проверить pull → применить infra#69 →
применить backend#42 → проверить общую выкатку и Health Check/Summary. Локальная проверка
контракта не требует пробного отката production.

## Webhook alerts на auxiliary (infra#76)

`auxiliary.yml` не меняет прод-VPS. Перед его запуском нужны `vault_hooks_env`
с `GITHUB_WEBHOOK_SECRET`, `APIDOG_SIGNATURE_KEY`, `APIDOG_SIGNATURE_HEADER`,
доверенный SSH host key и публичный GHCR package `webhook-alerts`. Роль закрепляет
image по registry digest и оставляет `WEBHOOK_ALERTS_SEND_ENABLED=false`.
Preflight выполняется до изменения VPS; отсутствие обязательных webhook env
останавливает playbook. Роль явно выполняет анонимный pull перед подготовкой
тома и запуском контейнера; ошибка registry прекращает playbook. Auxiliary CI
не требует registry credentials и `packages: read`. До первой выкатки владелец
должен отдельно обеспечить публичную visibility package и проверить анонимный
pull закреплённого digest с VPS с чистым Docker config. Публичность `.github`
source repo сама по себе не означает публичность package. Этот PR не меняет
visibility; до подтверждения public pull live-приёмка остаётся незавершённой.

Старый том busybox автоматически получает владельца `bun:bun` перед запуском
нового образа. Env сериализуется с сохранением буквальных `$`, кавычек и слешей.
Путь `/data/queue.sqlite` фиксирован контрактом snapshot и проверяется preflight;
секреты env хранить строками и без переводов строк.

`restic-backup.service` сначала вызывает snapshot CLI в контейнере. При ошибке
Restic не запускается. Архивируется только согласованный snapshot, тома Caddy и
`/opt/hooks`; живые SQLite/WAL/SHM не входят в список. `verify.yml` обнаруживает
отсутствие непустого snapshot; на первом развёртывании сначала вручную запустить
`systemctl start restic-backup`. Полная проверка Restic/isolated restore, порядок
возврата файла и журнал приёмки — [RUNBOOK](../RUNBOOK.md#бэкап-и-изолированное-восстановление).

Проверки без VPS и секретов (Ansible dependencies из `deploy-requirements.txt`,
коллекции из `requirements.yml`; Docker Compose и Restic из локальной установки):

```bash
RESTIC_CLI=restic COMPOSE_CLI='docker compose' python -m unittest discover -s tests -v
ansible-playbook -i inventory/ci.yml --syntax-check site.yml verify.yml deploy-backend.yml auxiliary.yml
ansible-lint auxiliary.yml roles/hooks roles/restic_backup tasks/verify-hooks-snapshot.yml verify.yml
```

Restic-тест использует локальный repository, проверяет фактический состав архива
и восстановленную SQLite. Если Restic не установлен, этот тест пропускается;
CI устанавливает пакет явно. Для дополнительной проверки перехода root-owned
volume → bun на локальном Docker установить `WEBHOOK_ALERTS_TEST_IMAGE` в имя
локально собранного image сервиса (например `webhook-alerts:test`). Тест создаёт
и удаляет только собственный Compose project/volume; registry не используется.

### Telegram через VLESS Reality

`hooks` передаёт только Telegram API в `http://telegram-proxy:2080` через
`TELEGRAM_PROXY_URL`; GitHub enrichment остаётся прямым. Sidecar Xray Core 26.10.21
закреплён digest, не публикует портов и подключён к сети `telegram_proxy` только
вместе с `hooks`. Caddy использует прежнюю сеть `hooks`. Proxy принимает HTTP
на `0.0.0.0:2080` внутри Docker, разрешает ровно `api.telegram.org`
через единственный VLESS Reality outbound и отправляет остальные назначения в первый blackhole outbound.

Полный JSON-объект — `vault_hooks_proxy_config` в зашифрованном
`inventory/group_vars/all/vault.yml`; править через `ansible-vault edit`.
URI, UUID, адрес сервера, Reality key и токен бота не помещать в команды, Git и
логи. Preflight до изменений VPS проверяет inbound, TLS/Reality без `insecure`
и точный allowlist маршрута. Файл `/opt/hooks/proxy/config.json` имеет режим
0600 в каталоге 0700; оба принадлежат UID/GID 65532
(пользователь official Xray image); template использует `no_log` и `diff: false`.
Каталог смонтирован read-only, изменение JSON вызывает restart sidecar.

Проверка синтаксиса JSON, в том числе синтетического конфига без секретов:

```bash
sudo docker compose -f /opt/hooks/compose.yml run --rm --no-deps telegram-proxy run -c /etc/xray/config.json -test
sudo docker compose -f /opt/hooks/compose.yml ps
sudo stat -c '%a %u:%g' /opt/hooks/proxy/config.json
```

`docker compose config` использовать локально только с синтетическим `.env`:
команда раскрывает environment. Для реального конфига достаточно `config --quiet`.
Недостающий Vault останавливает preflight; ошибки image pull останавливают
выкатку до `up`. TCP timeout означает проверку доступности внешнего VLESS;
TLS/Reality handshake — сверку SNI, public key, short ID, flow и fingerprint
без отключения проверки сертификата. Отказ другого домена ожидаем.
Не включать sender до отдельной проверки `getMe`, тестового сообщения и очереди.
Откат — по [RUNBOOK](../RUNBOOK.md#telegram-proxy-на-auxiliary).

## GitHubBot на auxiliary (infra#111)

Роль задаёт в том же проекте `/opt/hooks` один service `githubbot`, без публичных
портов. Его Telegram-запросы идут через `telegram-proxy:2080`, GitHub — напрямую.
SQLite и heartbeat находятся в отдельном named volume `hooks_githubbot_data`;
перезапуск и recreate сохраняют том. Существующий snapshot Restic относится
к очереди webhook-alerts: резервная копия SQLite GitHubBot им не подтверждается.

Обязательные inputs перед preview/apply:

| Переменная | Источник и проверка |
| --- | --- |
| `hooks_githubbot_image` | `inventory/group_vars/auxiliary/vars.yml`: публичный GHCR **manifest digest** публикации `githubbot-image.yml`; не локальный image ID |
| `hooks_githubbot_image_source_tag` | Там же: `ghcr.io/cringe-driven-development-team/githubbot:sha-` + полный исходный SHA; SHA и совпадающий registry manifest digest тега/образа сверить с summary доверенного успешного запуска штатного `githubbot-image.yml` |
| `hooks_env.TELEGRAM_BOT_TOKEN` | Общий с GitHub Monitoring токен из `vault_hooks_env.TELEGRAM_BOT_TOKEN`; отдельный Telegram bot не нужен |
| `vault_githubbot_env.GITHUB_TOKEN` | Сервисный PAT с Issues read/write разрешённых repo и organization Projects read/write |
| `vault_githubbot_env.GITHUBBOT_CONFIG_JSON` | Однострочная JSON-строка с проверенными IDs, allowlists и username mappings |

OCI `org.opencontainers.image.source` должен указывать на
`https://github.com/Cringe-Driven-Development-Team/.github`. Унаследованный от Bun
`org.opencontainers.image.revision` не является SHA исходников GitHubBot.
Полная процедура анонимного pull и сверки digest — в [RUNBOOK](../RUNBOOK.md#githubbot-на-auxiliary).

Defaults добавляют `DATABASE_PATH=/data/githubbot.sqlite` и
`TELEGRAM_PROXY_URL=http://telegram-proxy:2080`; менять эти пути нельзя.
Telegram-токен берётся из effective `hooks_env` после объединения `vault_githubbot_env`;
устаревшее значение в `vault_githubbot_env` его не перекрывает. В `vault_githubbot_env`
хранятся только отдельный PAT `GITHUB_TOKEN` и `GITHUBBOT_CONFIG_JSON`.
Отсутствующий или пустой общий токен останавливает обязательный preflight до изменений VPS.
В inventory нет фиктивного digest или необязательного переключателя запуска.

JSON содержит `chatId` (отрицательная decimal string), `notificationBotIds`
(положительные decimal strings), `repositories:[{name,defaultPrefix,prefixes}]`,
`topics:[{threadId,repository}]` и `users:[{telegramId,username,githubLogin}]`.
`threadId` — положительное число, `telegramId` — положительная decimal string.
Разрешены `backend` (`Backend.`), `frontend` (`Frontend.` и `BFF.`), `figma`
(`Figma.`). Только Backend/Frontend/UX имеют фиксированный repo; Releases
и остальные топики предлагают кнопки. Карту пользователей хранить в Vault.

Роль рендерит отдельный `githubbot.env` с mode 0600, анонимно скачивает image,
запускает `bun run src/main.ts --check-config` без polling и готовит владельца
тома `bun:bun` перед общим Compose up. Секретные tasks скрывают лог и diff.
Не выводить `docker compose config` или полный `docker inspect` боевого
контейнера: они могут раскрыть env.

`verify.yml --limit auxiliary` требует ровно один контейнер `githubbot`, включая
остановленные, и настоящий Docker `Running=true`, `Health.Status=healthy`.
Healthcheck вызывает `bun run src/main.ts --health`: heartbeat не старше 120 с,
interval 30 с, timeout 5 с, retries 3, start period 40 с. Эта проверка не доказывает
отсутствие poller на другом хосте или доставку reply от другого бота.
Controller-level plays запускаются отдельно через `--limit localhost`.

Локальная проверка с реальным, заранее собранным image не использует сеть:

```bash
GITHUBBOT_TEST_IMAGE=githubbot:test COMPOSE_CLI='docker compose' python -m unittest discover -s tests -v
ansible-playbook -i inventory/ci.yml --syntax-check site.yml verify.yml deploy-backend.yml deploy-bff.yml auxiliary.yml
ansible-lint auxiliary.yml roles/hooks roles/restic_backup tasks/verify-hooks-snapshot.yml verify.yml
```

Fake Docker тест выполняет настоящие tasks verify для zero/two/stopped/unhealthy/
missing-health/one-healthy; real-image тест проверяет env, offline config checker,
права bun и SQLite/WAL после recreate. Процедура получения IDs, privacy,
обновления/рестарта и ручной сверки uncertain create — [RUNBOOK](../RUNBOOK.md#githubbot-на-auxiliary).
Результат выкладки фиксировать в [журнале](../docs/deployments/2026-11-07-githubbot.md);
зелёные локальные тесты не означают выполненную живую приёмку.

## Плейбук runtime-VPS (infra#89)

`runtime/` — отдельный проект Ansible (свой `ansible.cfg`, роли `runtime_host` и `runtime_agent`):
`prepare.yml` готовит runtime-VPS без сессии и запускает агента в режиме ожидания (infra#138); сессию
назначает Go API запросом `POST /assign` к агенту, без SSH и Ansible (пул VPS, backend#89). Запускает его Go API на VPS2 (backend#57) с `inventory.json` и
`vars.json` операции; роль `app` копирует каталог на VPS2 и монтирует в контейнер API в
`/app/deploy/runtime`. Сеть kernel — по контракту backend
`docs/runtime-agent-network.md`: таблица nftables `inet cellestial` в unit `cellestial-firewall.service`.
CI проверяет `ansible-lint runtime` и `--syntax-check` с заглушкой `tests/runtime/vars.json`. Настройка,
секреты и ротация — RUNBOOK, «Runtime-VPS сессий».
