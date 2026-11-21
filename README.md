# infra

Стенд в Selectel: одна VPS + объектное хранилище S3 + DNS.

- `pulumi/` — инфраструктура как код: проект, сервисный пользователь (+S3-ключи), сети,
  VPS (публичный IP), A-запись домена и S3-бакеты: публичные под фронт и под статику (источники CDN),
  публичный под аватарки (бэкенд, без CDN), приватный под ноутбуки (бэкенд, без CDN).
  См. `pulumi/README.md`, «Бакеты».
- `ansible/` — настройка сервера: пользователь, SSH-hardening, firewall, Docker + Compose,
  Caddy с Let's Encrypt. См. `ansible/README.md`.

На единственной VPS крутится Docker Compose: Caddy, Go API, Postgres
(схемы: docs/deployment.html, docs/infra.html; вариант с двумя VPS заморожен в docs/bff/infra.html).

Порядок: `pulumi up` → `ansible-playbook bootstrap.yml` → `site.yml` → `verify.yml`.
Пошаговая инструкция со всеми командами и значениями «что вводить руками» — [RUNBOOK.md](RUNBOOK.md).

## Ansible Vault

Для правки секретов и локального запуска инфраструктурных playbook'ов нужен пароль
Vault. Получи его лично у Дениса или менторов и сохрани вне репозитория в
`~/.config/cdd-vault-pass` с правами `600`; путь к файлу задай через
`ANSIBLE_VAULT_PASSWORD_FILE`. Правь секреты через `ansible-vault edit` — при
сохранении файл шифруется обратно. Коммить только зашифрованный `vault.yml`.
Подробный порядок и команды — [Секреты (ansible-vault)](ansible/README.md#секреты-ansible-vault).

Версии приложений VPS3 выбираются из `/opt/hooks/releases.json` (desired/confirmed),
а не из inventory; обычный infra apply сохраняет выбранные digest обоих сервисов.
Первая установка и восстановление состояния описаны в [RUNBOOK](RUNBOOK.md#состояние-релизов-vps3-первая-установка-и-восстановление).

VPS3 application releases use [auxiliary-deploy.yml](.github/workflows/auxiliary-deploy.yml).
A successful publisher dispatches one immutable digest; infrastructure apply keeps the
selection in `/opt/hooks/releases.json`. All production writers share `infra-production`.
See [RUNBOOK.md](RUNBOOK.md) for initialization and recovery.
