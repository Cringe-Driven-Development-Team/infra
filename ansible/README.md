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

## Что настраивается

| Роль | Хосты | Содержимое |
|---|---|---|
| users | все | пользователь `deploy`, authorized_keys из `files/authorized_keys/*.pub` (exclusive, с проверкой ключа запускающего), sudo NOPASSWD — в bootstrap и в каждом site.yml |
| common | все | базовые пакеты |
| ssh_hardening | все | `00-hardening.conf` (validate через `sshd -t`): без root-логина и паролей, форвардинг запрещён (jump-хост не нужен); ubuntu 24.04 — socket activation, рестарт `ssh.socket` + `ssh.service` |
| firewall | все | ufw: deny incoming; наружу только 22/80/443 (DoD) |
| docker | все | Docker Engine + Compose plugin, `deploy` в группе docker (про порты — ниже) |
| caddy | все | Проект Compose `/opt/cellestial` (`compose.yml`, сеть `app`), Caddy в контейнере (`caddy:2.11-alpine`, 80/443), Caddyfile с доменом `app_domain`, сертификаты Let's Encrypt — в volume `caddy_data`. Caddy, ранее поставленный из apt, удаляется |

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

## Проверки verify.yml

- ровно один сервер в inventory (проект) — второй VPS больше нет;
- `https://<app_domain>/` отвечает 200, содержимое `ok`, сертификат от Let's Encrypt;
- на публичном IP открыты 22/80/443 и закрыты 5432, 8080, 2375, 2376 (ловит порты Docker
  в обход ufw; Go API при переезде в Compose добавит свой порт в этот список закрытых);
- ufw: active, `deny (incoming)`, разрешающих правил ровно `firewall_rules` из
  `group_vars/gateway` (22/80/443 tcp);
- `sshd -T`: `permitrootlogin no`, `passwordauthentication no`,
  `kbdinteractiveauthentication no`, `allowtcpforwarding no`;
- вне allowlist (22/80/443) на интерфейсах, отличных от loopback, ничего не слушает.

CDN для бакета S3 настраивается вручную вне этого стека (публичное чтение объектов включает Pulumi при `infra:s3PublicRead=true`).
