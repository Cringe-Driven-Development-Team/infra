# VPS с Coolify для сервиса вебхуков — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** отдельная VPS с self-hosted Coolify (панель `coolify.cellestial.ru`, сервис `hooks.cellestial.ru`), бэкапы restic в S3, CI — без изменений прод-VPS.

**Architecture:** Pulumi (стек `prod`) добавляет ресурсы `coolify*`, security group, A-записи и бакет бэкапов с пользователем; Ansible — плейбук `coolify.yml` с общими ролями и ролями `coolify`, `restic_backup`; прод-плейбуки сужаются до группы `gateway`; CI гоняет `coolify.yml`.

**Tech Stack:** Pulumi TypeScript (`@pulumi/openstack`, `@pulumi/selectel`, `@pulumi/aws`), Ansible 2.19, Coolify 4.4.6, restic.

**Spec:** `docs/superpowers/specs/2026-10-20-coolify-vps-design.md`

## Global Constraints

- Флейвор `SL1.2-4096`, диск 40 ГБ `infra:volumeType`, `metadata: { role: "coolify", env: "release" }`.
- Домены: `coolify.cellestial.ru`, `hooks.cellestial.ru`; бакет `cellestial-coolify-backups-0`.
- Coolify `4.4.6`, `AUTOUPDATE=false`; root по SSH — только ключ и только из `10.0.0.0/8`, `172.16.0.0/12`.
- Наружу на новой VPS — только 22, 80, 443 (security group); `:8000` наружу никогда.
- Логические имена существующих ресурсов не меняются; новые — с префиксом `coolify`.
- Секреты — только Vault/секреты GitHub, не в выводах.

## Review Focus

- `preview` не меняет ни одного существующего ресурса — проверяется `pulumi preview` в задаче 1.
- `site.yml` не попадает на хост `coolify` — `ansible-inventory --graph` и `--list-hosts` в задаче 2.
- Повторный прогон `coolify.yml` при той же версии не перезапускает установщик — `changed=0` в задаче 3.
- Порт `8000` новой VPS снаружи закрыт — внешняя проба в задаче 3.
- Бэкап без ключей Vault не ломает плейбук молча — роль падает с понятной ошибкой (задача 4).

---

### Task 1: Pulumi — VPS, security group, DNS, бакет бэкапов

**Files:** Modify `pulumi/index.ts`, `pulumi/Pulumi.prod.yaml`, `pulumi/Pulumi.prod.yaml.example`, `pulumi/README.md`.

**Produces:** выходы `coolifyPublicIp`, `coolifyDomain`, `hooksDomain`, `coolifyBackupBucket`, `coolifyBackupAccessKey`, `coolifyBackupSecretKey` (секрет). Всё включается конфигом `infra:coolify: true`.

- [ ] Конфиг: `infra:coolify`, `infra:coolifyFlavorName`, `infra:coolifyVolumeSize` (40), `infra:coolifyDomain`, `infra:hooksDomain`, `infra:coolifyBackupBucket`, `infra:coolifyBackupUserName`.
- [ ] Ресурсы: `SecGroup coolify` + правила 22/80/443 tcp и icmp ingress; `Port coolify` (сеть/подсеть прода, `securityGroupIds`); `Volume coolify`; `Instance coolify` (`userData`, `keypair`, `deleteBeforeReplace`, `ignoreChanges: ["imageId"]`, `dependsOn: routerInterface`); `FloatingIp coolify` + `FloatingIpAssociate`; `DomainsRrsetV2 coolify`, `hooks` (A на floating IP); `IamServiceuserV1 coolify-backup` (`s3.bucket.user`), `IamS3CredentialsV1 coolify-backup-s3`, `Bucket coolify-backups` (`protect`), `BucketAccess` private, `BucketPolicy` (`s3:*` стеку, объекты и листинг — пользователю бэкапа).
- [ ] `bunx tsc --noEmit` и `bun test` — проходят.
- [ ] `pulumi preview --stack prod --diff`: только `create` новых ресурсов, существующие `unchanged`.
- [ ] Коммит.

### Task 2: Ansible — inventory и прод-плейбуки на группу `gateway`

**Files:** Modify `ansible/inventory/openstack.yml`, `ansible/inventory/ci.yml`, `ansible/site.yml`, `ansible/verify.yml`, `ansible/bootstrap.yml` (комментарий про `--limit`); Create `ansible/inventory/group_vars/coolify.yml`.

- [ ] Группа `coolify` по `metadata.role`; в `ci.yml` — группа `coolify` с хостом `coolify.cellestial.ru`.
- [ ] `site.yml`: `hosts: gateway`; `verify.yml`: проверка «одна VPS» по `groups['gateway']`, второй play — `hosts: gateway`.
- [ ] `ansible-playbook --syntax-check`, `ansible-playbook -i inventory/ci.yml site.yml --list-hosts` — только `cellestial`.
- [ ] Коммит.

### Task 3: Ansible — роль `coolify`, плейбук `coolify.yml`, root из сетей Docker

**Files:** Create `ansible/coolify.yml`, `ansible/roles/coolify/{defaults,tasks}/main.yml`; Modify `ansible/roles/ssh_hardening/tasks/main.yml` (переменная `ssh_hardening_root_from: []`, блок `Match Address … PermitRootLogin prohibit-password`).

- [ ] `coolify_version: "4.4.6"`; установка — скачать `install.sh` в `/data/coolify/install-<версия>.sh`, запуск `bash … <версия>` с `AUTOUPDATE=false`, только если образ контейнера `coolify` не `…:<версия>` (`docker inspect`).
- [ ] `group_vars/coolify.yml`: `ssh_hardening_root_from: ["10.0.0.0/8", "172.16.0.0/12"]`, `firewall_rules` 22/80/443.
- [ ] На проде (после задачи 1 и подтверждения): `bootstrap.yml --limit coolify`, `coolify.yml`; повтор — `changed=0` у роли `coolify`; `nc -z <ip> 8000` снаружи — закрыт.
- [ ] Коммит.

### Task 4: Ansible — роль `restic_backup`

**Files:** Create `ansible/roles/restic_backup/{defaults,tasks,templates,handlers}/…`; Modify `ansible/coolify.yml`, `ansible/inventory/group_vars/coolify.yml`, `ansible/inventory/group_vars/all/vault.yml` (ключи — руками через `ansible-vault edit`).

- [ ] Пакет `restic`; `/etc/restic/env` (0600, `no_log`): `RESTIC_REPOSITORY=s3:https://s3.<пул>.storage.selcloud.ru/<бакет>`, `RESTIC_PASSWORD`, `AWS_*`; `restic init`, если репозитория нет.
- [ ] systemd `restic-backup.service` + `.timer` (ежедневно 03:30 MSK): `restic backup /data/coolify /var/lib/docker/volumes`, затем `forget --keep-daily 7 --keep-weekly 4 --prune`.
- [ ] Assert: `vault_coolify_restic_password`, `vault_coolify_backup_access_key`, `vault_coolify_backup_secret_key` заданы — иначе понятная ошибка.
- [ ] Проверка: `systemctl start restic-backup` → `restic snapshots` показывает снимок.
- [ ] Коммит.

### Task 5: CI

**Files:** Modify `.github/workflows/infra.yml`.

- [ ] Синтаксис `coolify.yml`; preview — `coolify.yml --check --diff`; apply — `coolify.yml` после `verify.yml`.
- [ ] actionlint, коммит.

### Task 6: Документация

**Files:** Modify `RUNBOOK.md`, `ansible/README.md`, `SUMMARY.md`; Create `docs/coolify.md` (инструкция автору сервиса).

- [ ] RUNBOOK: создание VPS, первая настройка через туннель, обновление Coolify, бэкап/восстановление, диагностика вебхуков, откат приложения.
- [ ] `docs/coolify.md`: деплой сервиса, секреты в Coolify, `/health`, volume, откат.
- [ ] Коммит, пуш, описание PR.
