# Ansible: настройка двух VPS из Pulumi

Инфраструктура: **VPS 1 (gateway)** — публичный IP, Caddy на домене с Let's Encrypt, jump-хост;
**VPS 2 (backend)** — без публичного IP, доступна только из приватной сети через VPS 1 (ProxyCommand).

## Установка

```bash
cd ansible
pip install -r requirements.txt          # openstacksdk для dynamic inventory
ansible-galaxy collection install -r requirements.yml   # openstack.cloud, community.general
```

## Подготовка

1. `cp clouds.yaml.example clouds.yaml` — сервисный пользователь **аккаунта** Selectel
   (тот же, что `selectel:username` в Pulumi) и `project_id` из `pulumi stack output projectId`.
2. Ключ стенда `~/.ssh/selectel_release` (+ `.pub`) — он же `infra:sshPublicKey` в Pulumi.
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
| users | все | пользователь `deploy`, authorized_keys, sudo NOPASSWD (в bootstrap) |
| common | все | базовые пакеты |
| ssh_hardening | все | drop-in: без root-логина и паролей, ключи (порт 22 открыт по DoD) |
| firewall | все | ufw: deny incoming; gateway — 22/80/443, backend — 22 из `private_network_cidr` |
| docker | все | Docker Engine + Compose plugin, `deploy` в группе docker |
| caddy | gateway | Caddyfile с доменом `app_domain`, сертификат Let's Encrypt автоматически |

## Проверки verify.yml

- `https://<app_domain>/` отвечает 200, сертификат от Let's Encrypt (с управляющей машины);
- с VPS 1 доступен порт 22 приватного IP VPS 2;
- приватный IP VPS 2 недоступен снаружи.

CDN для бакета S3 настраивается вручную вне этого стека (публичное чтение объектов включает Pulumi при `infra:s3PublicRead=true`).
