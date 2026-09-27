# Ansible: настройка двух VPS из Pulumi

Инфраструктура: **VPS 1 (gateway)** — публичный IP, Caddy на домене с Let's Encrypt, jump-хост;
**VPS 2 (backend)** — без публичного IP, доступна только из приватной сети через VPS 1 (ProxyCommand).

## Установка

```bash
cd ansible
pip install -r requirements.txt          # openstacksdk для dynamic inventory
ansible-galaxy collection install -r requirements.yml   # openstack.cloud, community.general, ansible.posix
```

## Подготовка

1. `cp clouds.yaml.example clouds.yaml` — сервисный пользователь **аккаунта** Selectel
   (тот же, что `selectel:username` в Pulumi) и `project_id` из `pulumi stack output projectId`.
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

- публиковать только локально: `-p 127.0.0.1:8080:80` или на приватном IP
  (`-p 192.168.199.x:8080:80`) — наружу порт не смотрит;
- публичные сервисы вести через Caddy на gateway, а не через проброс портов;
- если нужен фильтр — править цепочку `DOCKER-USER` (ufw её не трогает). Правило вставлять
  в начало (`-I`): добавленное через `-A` окажется после `RETURN` и не сработает. Интерфейс —
  внешний интерфейс сервера (`ip route show default`), например:
  `iptables -I DOCKER-USER -i eth0 '!' -s 192.168.199.0/24 -p tcp -m conntrack --ctorigdstport 8080 -j DROP`.

## Проверки verify.yml

- `https://<app_domain>/` отвечает 200, содержимое `ok`, сертификат от Let's Encrypt;
- у VPS 2 в inventory только приватный IP из `private_network_cidr` (floating IP не выдан —
  структурная проверка; тест «таймаут к приватному IP извне» ничего не доказывает);
- с VPS 1 доступен порт 22 приватного IP VPS 2;
- `sshd -T` на gateway: `permitrootlogin no`, `passwordauthentication no`,
  `kbdinteractiveauthentication no`.

CDN для бакета S3 настраивается вручную вне этого стека (публичное чтение объектов включает Pulumi при `infra:s3PublicRead=true`).
