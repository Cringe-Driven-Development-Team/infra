# Webhook alerts: локальная интеграция infra#70 / .github#20

Основа — Task 9 согласованного плана `.github#20`; исполнение передано infra
исполнителю в subagent-driven-development. Этот документ фиксирует локальный
контракт, дополнения после исследования infra и оставшиеся внешние проверки.

Образ Task 8: `.github` commit `08e7968fa0d39dd5f25fe8495c58d78006a26653`,
manifest digest `sha256:30718ed06926bff66627502361185c2ca6ebca78b20b044ce4d8bba92f12e858`.
Compose разворачивает digest, SHA-тег остаётся для трассировки. Worker выключен.
HTTPS healthcheck и изоляция auxiliary/gateway сохранены.

Перед изменениями VPS preflight проверяет обязательные webhook env и наличие
GHCR credentials. Private pull использует временный controller credential,
logout выполняется в always. CI использует short-lived GITHUB_TOKEN с
packages:read; package Actions access для infra проверяет оператор. В Vault
registry token не сохраняется. Существующий root-owned volume подготавливается
одноразовым контейнером от root; владельца bun определяет сам закреплённый image.

Snapshot CLI вызывается ExecStartPre до backup. В архиве только файл
`hooks_hooks_data/_data/snapshots/queue.sqlite`, Caddy volumes и `/opt/hooks`.
Живая WAL-база и sidecars не копируются. Ошибка snapshot блокирует backup/prune.
verify обнаруживает отсутствие snapshot; целостность и статусы проверяются
при изолированном restore по RUNBOOK, без worker и сети.

Порядок локального исполнения:

- [x] RED: проверки реального rendered unit и фактических файлов в локальном
  Restic, Compose image/entrypoint/env, preflight и logout при ошибках.
- [x] Минимальная реализация roles/hooks, roles/restic_backup и workflow env.
- [x] RED/GREEN: root-owned volume → запись пользователя bun на реальном Docker.
- [x] Проверка отсутствующего snapshot в verify, документация restore.
- [x] Полный suite, syntax/check, lint и self-review; результат в отчёте Task 9.

Внешние gates принадлежат контроллеру: package Actions read для infra и private
pull с VPS; Vault webhook env; применение роли; snapshot → Restic → isolated
restore с pending/sent и dedupe; PR/merge. Исполнитель не меняет VPS/Vault,
не принимает новый SSH host key, не публикует ветку. Живая приёмка не завершена.
