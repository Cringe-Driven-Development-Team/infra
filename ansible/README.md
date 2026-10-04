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
| `vault_notebooks_s3_access_key` | `notebooks_s3_access_key` |
| `vault_notebooks_s3_secret_key` | `notebooks_s3_secret_key` |

`notebooks_s3_*` — S3-ключ Go API к приватному бакету ноутбуков, значения — выходы Pulumi
`notebooksAccessKey` и `notebooksSecretKey` (`pulumi stack output <имя> --show-secrets`, оба секретные;
`pulumi/README.md`, «Бакет ноутбуков»). Ключ перевыпущен (пересоздан стек или пользователь) — обновить
оба значения через `ansible-vault edit`. В `.env` Go API они попадут через роль `app`
([backend#2](https://github.com/Cringe-Driven-Development-Team/backend/issues/2)).

Несекретные параметры S3 для бэка лежат открыто в `group_vars/all/vars.yml`: `s3_endpoint`,
`s3_region`, `s3_force_path_style`, `notebooks_bucket`, `avatars_bucket`, `avatars_public_domain` —
значения из `pulumi stack output`; `avatars_public_domain` — свой домен бакета аватарок (выход
`avatarsCustomDomain`), сертификат к нему выпускается в панели.

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
закоммитить. Значения секретов — `openssl rand -base64 32` (кроме `notebooks_s3_*` — они из Pulumi).

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
   `notebooks_s3_*`;
3. S3-ключ Go API (`notebooks_s3_*`) случайной строкой не заменить: бэк получит `InvalidAccessKeyId`,
   а утёкший ключ останется рабочим (чтение, запись и удаление ноутбуков и аватарок). Ключ
   перевыпускается в Pulumi:

   ```bash
   cd ../pulumi
   pulumi stack --show-urns | grep notebooks-s3     # URN ресурса IamS3CredentialsV1 "notebooks-s3"
   pulumi up --replace '<URN notebooks-s3>'
   pulumi stack output notebooksAccessKey --show-secrets
   pulumi stack output notebooksSecretKey --show-secrets
   ```

   новые значения — в `vault.yml` через `ansible-vault edit`;
4. выкатить: пароль Postgres меняется и в самой БД, смена `jwt_secret` разлогинивает пользователей;
5. убедиться, что старый S3-ключ больше не действует: запрос с ним к бакету ноутбуков должен
   вернуть `InvalidAccessKeyId`;
6. обновить `ANSIBLE_VAULT_PASSWORD` в CI.

## Что настраивается

| Роль | Хосты | Содержимое |
|---|---|---|
| users | все | пользователь `deploy`, authorized_keys из `files/authorized_keys/*.pub` (exclusive, с проверкой ключа запускающего), sudo NOPASSWD — в bootstrap и в каждом site.yml |
| common | все | базовые пакеты |
| ssh_hardening | все | `00-hardening.conf` (validate через `sshd -t`): без root-логина и паролей, форвардинг запрещён (jump-хост не нужен); ubuntu 24.04 — socket activation, рестарт `ssh.socket` + `ssh.service` |
| firewall | все | ufw: deny incoming; наружу только 22/80/443 (DoD) |
| docker | все | Docker Engine + Compose plugin, `deploy` в группе docker (про порты — ниже) |
| caddy | все | Проект Compose `/opt/cellestial` (`compose.yml`, сеть `app`), Caddy в контейнере (`caddy:2.11-alpine`, 80/443), Caddyfile с доменом `app_domain`: `/api/v1/*` — Go API (пока `503`), остальные пути — `index.html` клиента из бакета релизов с CDN (`frontend_cdn_domain`); сертификаты Let's Encrypt — в volume `caddy_data`. Caddy, ранее поставленный из apt, удаляется |

## Проект Compose

Всё приложение — один проект Compose в `/opt/cellestial` на VPS. Сейчас в нём только Caddy
(роль `caddy`); Go API и Postgres добавит задача деплоя
([backend#2](https://github.com/Cringe-Driven-Development-Team/backend/issues/2)) в тот же `compose.yml`,
в сеть `app` и **без** `ports:` — Caddy достаёт их по имени сервиса (`reverse_proxy api:8080`).
Смена Caddyfile применяется `caddy reload` внутри контейнера, без рестарта.

## Порты контейнеров и ufw (одна VPS)

Docker публикует порты контейнеров (`-p 8080:80`) своими iptables-правилами в цепочке
`nat`/`DOCKER`, которые обрабатываются **до** ufw: опубликованный порт откроется наружу,
даже при `deny incoming`. Правила на стенде:

- **Postgres и Go API наружу не публикуются**: только внутренняя сеть Compose
  (`expose`, без `-p`); при прямом доступе с хоста — bind на `127.0.0.1`
  (`-p 127.0.0.1:8080:8000`), не `0.0.0.0`;
- наружу смотрит **только Caddy** (80/443) и ssh (22); floating IP — 1:1 DNAT на адрес
  сервера, поэтому любой `-p 0.0.0.0:X` открыт в интернет мимо ufw;
- если нужен фильтр-страховка — править цепочку `DOCKER-USER` (ufw её не трогает). Правило
  вставлять в начало (`-I`): добавленное через `-A` окажется после `RETURN` и не сработает.
  Интерфейс — внешний (`ip route show default`), например — все новые соединения снаружи к
  контейнерам, кроме 80/443:
  `iptables -I DOCKER-USER -i eth0 -p tcp -m conntrack --ctstate NEW -m multiport ! --dports 80,443 -j DROP`
  (в `DOCKER-USER` порт уже после DNAT — порт контейнера; у Caddy он те же 80/443).
  Такое правило не переживает перезагрузку — после reboot его нужно вернуть (или завести в Ansible).

## Клиент

Клиент выкатывает CI фронта, Ansible в выкатке не участвует: сборка лежит в бакете релизов
(`releases/{sha}/`), корневой `index.html` бакета — копия `index.html` текущего релиза.

- Caddy на всех путях, кроме `/api/v1/*`, отдаёт этот `index.html`: `rewrite` на `/index.html` и
  `reverse_proxy` на домен CDN `frontend_cdn_domain` (`cdn.cellestial.ru`, `pulumi stack output
  cdnCustomDomain`) с `Host` этого домена. `Cookie` и `Authorization` в CDN не уходят, в ответе —
  `Cache-Control: no-cache`. Домен при пересоздании стека и бакета не меняется; его сертификат
  выпускается в панели — без него Caddy отвечает `502`.
- В CDN уходят только `GET` и `HEAD` и без query string запроса; остальные методы получают `405`.
- `/api/v1/*` отвечает `503` с телом `API is not deployed`, пока Go API нет в Compose.
- Чанки браузер грузит с того же домена CDN (`<script type="module" crossorigin>`, запрос в режиме CORS). CORS
  настраивать не нужно: CDN-ресурс Selectel на запрос с `Origin` сам отвечает
  `Access-Control-Allow-Origin: *`. Проверка:

  ```bash
  curl -sI -H 'Origin: https://cellestial.ru' "https://$(cd ../pulumi && pulumi stack output cdnCustomDomain)/index.html" \
    | grep -i access-control-allow-origin
  ```

## Проверки verify.yml

- ровно один сервер в inventory (проект) — второй VPS больше нет;
- `https://<app_domain>/` отдаёт `index.html` клиента из бакета релизов с `Cache-Control: no-cache`,
  в нём `<meta name="release">` — релиз `stable` из `current.json` бакета
  (`https://<frontend_cdn_domain>/current.json`). `404` от хранилища допустим, только пока
  `current.json` нет — клиент ещё не выкатан. CDN кэширует ответы: сразу после выкатки клиента
  проверка может отстать — повторить позже или сбросить кэш CDN-ресурса;
- вложенный маршрут SPA (`/notebooks/…`) отдаёт тот же ответ, `POST` на него — `405`;
- путь `/api/v1/*` отвечает `503` с телом `API is not deployed`;
- сертификат от Let's Encrypt;
- на публичном IP открыты 22/80/443 и закрыты 5432, 8080, 2375, 2376 (ловит порты Docker
  в обход ufw; Go API при переезде в Compose добавит свой порт в этот список закрытых);
- ufw: active, `deny (incoming)`, разрешающих правил ровно `firewall_rules` из
  `group_vars/gateway` (22/80/443 tcp);
- `sshd -T`: `permitrootlogin no`, `passwordauthentication no`,
  `kbdinteractiveauthentication no`, `allowtcpforwarding no`;
- вне allowlist (22/80/443) на интерфейсах, отличных от loopback, ничего не слушает.

CDN-ресурс и свои домены CDN и бакета создаёт Pulumi, сертификаты доменов — в панели (`pulumi/README.md`, «Свои домены»).
