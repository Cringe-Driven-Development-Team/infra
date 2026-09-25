# RUNBOOK: поднять стенд с нуля

Порядок: панель Selectel (руками) → Pulumi → Ansible → проверки.
Все команды — с управляющей машины (macOS), из корня репозитория.

## 0. Инструменты (один раз)

```bash
brew install pulumi/tap/pulumi bun awscli
pipx install ansible-core || brew install ansible   # + python3
cd ansible && pip install -r requirements.txt       # openstacksdk
ansible-galaxy collection install -r requirements.yml
cd ..
```

Ключ стенда (если нет):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/selectel_release -N ""
```

## 1. Руками в панели Selectel

1. **Сервисный пользователь аккаунта** (Управление доступом → Сервисные пользователи):
   роли `member` + `iam.admin` **на аккаунт**. Записать: логин (hex-строка) и пароль.
2. **Зона DNS** `cellestial.ru.` существует (создаётся автоматически при покупке домена;
   если её нет — включить DNS-хостинг для домена). Записать **project id** проекта, где лежит зона
   (панель → проект → адрес: `.../projects/<id>/...`) → это `dnsProjectId`.
3. **Object Storage для состояния Pulumi** (отдельный пользователь, не из п.1):
   - сервисный пользователь с доступом к объекчному хранилищу → выдать access/secret key;
   - создать бакет, например `pulumi-state-cellestial` (приватный).
   Записать: ключи, имя бакета, пул (у нас `ru-7`).
4. Если в зоне `cellestial.ru.` уже есть **рукописная A-запись** `cellestial.ru.` — удалить:
   запись будет под управлением Pulumi, иначе конфликт.

## 2. Pulumi: backend и стек

```bash
export AWS_ACCESS_KEY_ID='<ключ из п.1.3>'
export AWS_SECRET_ACCESS_KEY='<секретный ключ>'
# Схема https:// обязательна: без неё Pulumi падает с "was not a valid URI"
export AWS_ENDPOINT_URL='https://s3.ru-7.storage.selcloud.ru'   # https://s3.<пул>.storage.selcloud.ru

cd pulumi
pulumi login 's3://pulumi-state-cellestial'
pulumi install                       # генерирует sdks/selectel, ставит deps через bun
pulumi stack init dev                # спросит passphrase для секретов — СОХРАНИТЬ
```

## 3. Pulumi: конфиг (руками, значения свои)

```bash
pulumi config set selectel:domainName '<номер аккаунта>'
pulumi config set selectel:username   '<логин пользователя из п.1.1>'
pulumi config set --secret selectel:password '<пароль>'
pulumi config set selectel:authUrl    https://cloud.api.selcloud.ru/identity/v3/
pulumi config set selectel:authRegion ru-9

pulumi config set infra:name              pulumi-cellestial   # уникально в общем аккаунте
pulumi config set infra:serviceUserName   cellestialSystemUser
pulumi config set infra:pool              ru-9
pulumi config set infra:zone              ru-9a
pulumi config set infra:volumeType        fast.ru-9a
pulumi config set infra:gatewayFlavorName SL1.2-4096
pulumi config set infra:backendFlavorName SL2.2-8192
pulumi config set infra:imageName        'Ubuntu 24.04 LTS 64-bit'
pulumi config set infra:sshPublicKey     "$(cat ~/.ssh/selectel_release.pub)"

pulumi config set infra:domain       cellestial.ru
pulumi config set infra:dnsZone      cellestial.ru.
pulumi config set infra:dnsProjectId '<project id из п.1.2>'

pulumi config set infra:s3Pool   ru-7
pulumi config set infra:s3Bucket '<имя бакета релизов, глобально уникальное>'
pulumi config set infra:s3PublicRead true   # публичное чтение объектов (политика бакета), нужно для DoD
```

## 4. Pulumi: создать инфраструктуру

```bash
pulumi preview        # должно быть: 2 server, 2 volume, сеть, бакет, rrset, ...
pulumi up             # подтвердить, ~5-10 минут
pulumi stack output   # projectId, publicIp, privateIp, s3Endpoint, s3Bucket, s3AccessKey, s3SecretKey, domain
```

## 5. Ansible: креды и запуск

```bash
cd ansible
cp clouds.yaml.example clouds.yaml
# в clouds.yaml руками: username/password/user_domain_name из п.1.1,
# project_id = `pulumi stack output projectId` (из каталога pulumi/), region_name ru-9

ansible-inventory -i inventory --graph        # должны появиться 2 хоста: gateway и backend
ansible-playbook bootstrap.yml                # root:22 → python3 + пользователь deploy
ansible-playbook site.yml                     # базовая настройка + Caddy (получит сертификат LE)
ansible-playbook site.yml                     # повтор: expected changed=0
ansible-playbook verify.yml                   # проверки DoD
```

## 6. Проверки руками (DoD)

```bash
curl -I https://cellestial.ru/                                  # 200
echo | openssl s_client -connect cellestial.ru:443 -servername cellestial.ru 2>/dev/null | grep issuer

cd ../pulumi
# Ключи продукта читаем в локальные переменные и отдаём только команде aws:
# AWS_* в окружении — это ключи backend'а стейта (п.2), перетирать их нельзя,
# иначе следующие pulumi stack output не прочитают стейт.
S3_ENDPOINT=$(pulumi stack output s3Endpoint)
S3_BUCKET=$(pulumi stack output s3Bucket)
S3_AK=$(pulumi stack output s3AccessKey)
S3_SK=$(pulumi stack output s3SecretKey --show-secrets)    # без --show-secrets будет "[secret]"

echo hello > /tmp/hello.txt
# awscli не доверяет цепочке сертификатов Selectel из коробки — нужен их корневой сертификат,
# см. https://docs.selectel.ru/en/s3/tools/aws-cli (ca_bundle / AWS_CA_BUNDLE)
AWS_ACCESS_KEY_ID="$S3_AK" AWS_SECRET_ACCESS_KEY="$S3_SK" \
  aws --endpoint-url "$S3_ENDPOINT" --region "$(pulumi config get infra:s3Pool)" \
  s3 cp /tmp/hello.txt "s3://$S3_BUCKET/"

# публичный URL объекта = <s3Endpoint>/<s3Bucket>/<key>; без авторизации отвечает 200,
# только если включено infra:s3PublicRead (п.3)
curl -I "$S3_ENDPOINT/$S3_BUCKET/hello.txt"   # 200

# VPS 2 доступна только через VPS 1. Ключ и IdentitiesOnly передаём и хопу:
# опции командной строки на хоп через -J не действуют.
KEY=~/.ssh/selectel_release
ssh -i $KEY -o IdentitiesOnly=yes \
  -o ProxyCommand="ssh -W %h:%p -i $KEY -o IdentitiesOnly=yes deploy@$(pulumi stack output publicIp)" \
  deploy@$(pulumi stack output privateIp) 'hostname'
```

CDN (static.site.ru в схеме) — настраивается вручную в панели Selectel и связывается с бакетом;
в этом стеке не автоматизировано.

## 7. Снос всего

```bash
cd pulumi && pulumi destroy    # бакет удалится с объектами (forceDestroy: true)
```

## Частые грабли

| Симптом | Причина |
|---|---|
| `pulumi whoami` падает с `no EC2 IMDS role found` | Не заданы `AWS_*` env или ключи от state-бакета — п.2 |
| `Custom endpoint ... was not a valid URI` | В `AWS_ENDPOINT_URL` нет схемы `https://` — п.2 |
| `409 already_exists` | Имя занято в общем аккаунте → сменить `infra:name` / `infra:serviceUserName` / `infra:s3Bucket` |
| `Your query returned no results` на зоне | `infra:dnsZone`/`infra:dnsProjectId` не совпадают с реальностью |
| `ExternalGatewayForFloatingIPNotFound` | Уже обработан (`dependsOn`), повторить `pulumi up` |
| VPS 2 не пингуется из Ansible | До `bootstrap.yml` на шлюзе нет `deploy`, хоп под `root` идёт только в `bootstrap.yml`. Если bootstrap прервался на VPS 2: `ansible-playbook bootstrap.yml --limit backend` |
| `Host key verification failed` на хопе до VPS 1 | Стек пересоздан с тем же floating IP, а host key новый: `ssh-keygen -R <publicIp>` |
| `Too many authentication failures` | ssh перебрал ключи агента раньше ключа стенда (`MaxAuthTries 4`). В `ansible.cfg` уже `IdentitiesOnly=yes`; при ручном ssh добавлять `-o IdentitiesOnly=yes` |
| Caddy не получает сертификат | A-запись ещё не указала на `publicIp` — `dig cellestial.ru`, подождать TTL 300s |
