# Ansible: настройка двух VPS из Pulumi

Инфраструктура: **VPS 1 (gateway)** — публичный IP, Caddy на домене с Let's Encrypt, jump-хост;
**VPS 2 (backend)** — без публичного IP, доступна только из приватной сети через VPS 1 (ProxyCommand).

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
   (логин через keypair при создании серверов).
   Публичные ключи всех, кто работает со стендом, лежат в репозитории:
   `files/authorized_keys/*.pub` (один ключ — один файл с узнаваемым именем). Роль `users`
   кладёт их все в `deploy` c `exclusive: true`, поэтому новый доступ = PR с `.pub`-файлом
   + `ansible-playbook site.yml`; ушедший участник = удалить его `.pub` из репо + прогнать
   `site.yml`. Серверы пересоздавать не нужно.
3. Inventory динамический (`inventory/openstack.yml`): группы `gateway` и `backend` собираются
   по `metadata.role`, `ansible_host` — floating IP у VPS 1 и приватный IP у VPS 2.

## Запуск

```bash
ansible-playbook bootstrap.yml   # один раз: python3 + пользователь deploy (root, порт 22)
ansible-playbook site.yml        # базовая настройка обеих VPS + Caddy на gateway
ansible-playbook verify.yml      # проверки из DoD (см. ниже)
```

Повторный `site.yml` должен давать `changed=0`.

`bootstrap.yml` ходит под `root`, а после `site.yml` root-логин закрыт — на уже настроенные хосты
его повторно не запустить. Он нужен только хосту с **новым диском** (после `pulumi destroy` или замены
boot-volume), и только с `--limit`:

- gateway: `ansible-playbook bootstrap.yml --limit gateway`;
- backend при уже настроенном gateway: `ansible-playbook bootstrap.yml --limit backend -e jump_user=deploy`
  — на VPS 2 заходим под `root`, но хоп через gateway уже только под `deploy`.

Пересоздание сервера Pulumi'ем (смена `infra:sshPublicKeys`, `deleteBeforeReplace`) диск сохраняет:
`deploy` и hardening на нём уже есть, bootstrap не нужен (и не пройдёт) — хватает `site.yml`.

Ключи команды на существующие хосты раскатывает `site.yml` (роль `users`), bootstrap для этого не нужен.

## Доступ к VPS 2

`group_vars/backend/vars.yml` подставляет `ProxyCommand` через VPS 1 из inventory
(`hostvars[groups['gateway'][0]]`). Именно `ProxyCommand`, а не `ProxyJump`: опции командной
строки на хоп через `-J` не действуют, а хопу нужен ключ стенда и `IdentitiesOnly=yes`.
Во время bootstrap прыгаем под `root` (`jump_user=root` в плейбуке), после `ssh_hardening`
root-логин запрещён и прыгаем под `deploy`.

## Что настраивается

| Роль | Хосты | Содержимое |
|---|---|---|
| users | все | пользователь `deploy`, authorized_keys из `files/authorized_keys/*.pub` (exclusive, с проверкой ключа запускающего), sudo NOPASSWD — в bootstrap и в каждом site.yml |
| common | все | базовые пакеты |
| ssh_hardening | все | `00-hardening.conf` (validate через `sshd -t`): без root-логина и паролей, ключи (порт 22 открыт по DoD); ubuntu 24.04 — socket activation, рестарт `ssh.socket` + `ssh.service` |
| firewall | все | ufw: deny incoming; gateway — 22/80/443, backend — 22 из `private_network_cidr` |
| docker | все | Docker Engine + Compose plugin, `deploy` в группе docker (про порты — ниже) |
| caddy | gateway | Caddyfile с доменом `app_domain`, сертификат Let's Encrypt автоматически |

## Порты контейнеров и ufw

Docker публикует порты контейнеров (`-p 8080:80`) своими iptables-правилами в цепочке
`nat`/`DOCKER`, которые обрабатываются **до** ufw: опубликованный порт откроется наружу,
даже при `deny incoming`. Правила на стенде:

- на gateway публиковать только на `127.0.0.1` (`-p 127.0.0.1:8080:80`): floating IP — это 1:1 DNAT
  на приватный адрес шлюза, поэтому `-p 192.168.199.x:…` на gateway открыт в интернет мимо ufw;
- на приватном IP (`-p 192.168.199.x:8080:80`) — только на VPS 2: floating IP у него нет;
- публичные сервисы вести через Caddy на gateway, а не через проброс портов;
- если нужен фильтр — править цепочку `DOCKER-USER` (ufw её не трогает). Правило вставлять
  в начало (`-I`): добавленное через `-A` окажется после `RETURN` и не сработает. Интерфейс —
  внешний интерфейс сервера (`ip route show default`), например:
  `iptables -I DOCKER-USER -i eth0 '!' -s 192.168.199.0/24 -p tcp -m conntrack --ctorigdstport 8080 -j DROP`.
  Такое правило не переживает перезагрузку — после reboot его нужно вернуть (или завести в Ansible).

## Проверки verify.yml

- `https://<app_domain>/` отвечает 200, содержимое `ok`, сертификат от Let's Encrypt;
- VPS 2 без публичного адреса — по данным OpenStack: ни один floating IP проекта не привязан к его
  портам, порты не во внешней сети (приватный `ansible_host` сам по себе ничего не доказывает);
- ufw на VPS 2: active, `deny (incoming)`, разрешающих правил ровно `firewall_rules` из
  `group_vars/backend` (сейчас одно — 22/tcp из `private_network_cidr`);
- с VPS 1: порты из `firewall_rules` VPS 2 открыты, остальные — всё, что VPS 2 слушает не на loopback,
  плюс 80/443/2375/2376/8080 — закрыты (ловит и порты Docker в обход ufw). Порт приложения для Caddy
  добавляется в `firewall_rules` — проверки его учтут;
- `sshd -T` на обеих VPS: `permitrootlogin no`, `passwordauthentication no`,
  `kbdinteractiveauthentication no`.

`verify.yml` запускать целиком: с `--limit gateway` проба портов не знает, что слушает VPS 2, и
проверяет только типовые порты.

CDN для бакета S3 настраивается вручную вне этого стека (публичное чтение объектов включает Pulumi при `infra:s3PublicRead=true`).
