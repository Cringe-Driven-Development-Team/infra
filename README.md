# infra

Стенд в Selectel: две VPS + объектное хранилище S3 + DNS.

- `pulumi/` — инфраструктура как код: проект, сервисный пользователь (+S3-ключи), сети,
  VPS 1 (gateway, публичный IP), VPS 2 (backend, только приватная сеть), S3-бакет с публичным
  чтением, A-запись домена. См. `pulumi/README.md`.
- `ansible/` — настройка серверов: пользователь, SSH-hardening, firewall, Docker + Compose,
  Caddy с Let's Encrypt на VPS 1. См. `ansible/README.md`.

Порядок: `pulumi up` → `ansible-playbook bootstrap.yml` → `site.yml` → `verify.yml`.
