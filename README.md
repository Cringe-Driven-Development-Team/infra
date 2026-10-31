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

Проверки OpenWiki: `bun test scripts/openwiki-check.test.ts`. Тест границ корпуса
использует настоящий парсер OpenWiki 0.7.2: нужен `npm install -g openwiki@0.7.2`
либо `OPENWIKI_PACKAGE_ROOT=/путь/к/node_modules/openwiki` для уже установленного
пакета (например, из соседнего `docs`). Модель и облачные учётные данные не нужны.
