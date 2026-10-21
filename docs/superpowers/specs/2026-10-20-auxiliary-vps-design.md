# Вспомогательная VPS для сервиса вебхуков — дизайн

Задача: [infra#54](https://github.com/Cringe-Driven-Development-Team/infra/issues/54) (ТЗ обновлено
2026-10-20: без Coolify). Сервис уведомлений GitHub/Apidog → Telegram —
[.github#20](https://github.com/Cringe-Driven-Development-Team/.github/issues/20).

## Зачем

Сервису нужен публичный HTTPS-адрес `hooks.cellestial.ru` и постоянное хранилище очереди на машине,
отдельной от прода. На машине — только этот небольшой сервис; ресурсов «на вырост» не берём.

## Анти-цели

- `pulumi up` меняет прод-VPS, её диск, IP, сеть или DNS прода.
- Порт сервиса или Docker виден снаружи; прод-роли (`caddy`, `app`) попадают на новую VPS.
- Секреты сервиса или бэкапа — в Git, выводах CI или `pulumi`.

## Решения

- **Pulumi** (`infra:auxiliary`): тот же стек `prod`, проект и приватная сеть; своя VPS `pulumi-server-auxiliary`
  (`SL1.2-4096`, диск 40 ГБ), порт, floating IP и security group `auxiliary` — входящие 22/80/443 tcp и ICMP
  (Docker публикует порты мимо ufw). A-запись `hooks.cellestial.ru`. Приватный бакет бэкапов
  `cellestial-auxiliary-backups-0` (`protect`) со своим пользователем `s3.bucket.user`.
- **Ansible** `auxiliary.yml` на группу `auxiliary` (`metadata.role`): `users`, `common`, `ssh_hardening`,
  `firewall`, `docker`, `hooks`, `restic_backup`. `site.yml` и `verify.yml` — только `gateway`.
- **Роль `hooks`**: Compose `/opt/hooks` — Caddy (80/443, Let's Encrypt для `hooks_domain`) и контейнер
  сервиса во внутренней сети без `ports`; том `hooks_data`; `.env` из `vault_hooks_env` (0600, `no_log`).
  Пока сервиса нет — тестовый `busybox` httpd с `/health` и томом `/data`. Деплой сервиса — `hooks_image`
  (тег) в `group_vars/auxiliary` и прогон плейбука/CI; откат — прежний тег.
- **Бэкап**: restic по ночам (systemd-таймер) — тома Docker и `/opt/hooks` в бакет бэкапов, 7 дневных и
  4 недельных снимка; пароль и S3-ключ — в Vault. Восстановление — по RUNBOOK.
- **CI**: `auxiliary.yml` в синтаксисе, `--check --diff` в preview, прогон в apply; хост в
  `inventory/ci.yml` — `hooks.cellestial.ru`, его host key — в `SSH_KNOWN_HOSTS`.

## Приёмка (из задачи)

- `pulumi preview`: только новые ресурсы `auxiliary*`, `hooks`, бакет и пользователь бэкапа.
- `https://hooks.cellestial.ru/health` — 200 с валидным сертификатом; порт контейнера снаружи закрыт.
- Тестовые данные в томе переживают пересоздание контейнера; бэкап и восстановление проверены.
- Снаружи открыты только 22/80/443; прод отвечает, `verify.yml` зелёный.
