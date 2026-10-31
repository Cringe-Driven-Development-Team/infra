---
type: operations
title: Вебхуки и резервные копии
description: Изоляция auxiliary, проверка конфигурации hooks и резервирование очереди через snapshot.
tags: [infra, operations]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-31T14:36:53.458Z
sources:
  - id: openwiki-source-9f9915f890b2de275d85922a
    resource: repo://ansible/auxiliary.yml
  - id: openwiki-source-a7c5b73745c56676960ac831
    resource: repo://ansible/roles/hooks/tasks/main.yml
  - id: openwiki-source-ed9d0f5d6cfb125eb17fe4c7
    resource: repo://ansible/roles/hooks/tasks/prepare-data.yml
  - id: openwiki-source-21b3efa0248b003636803c30
    resource: repo://ansible/roles/hooks/tasks/pull-image.yml
  - id: openwiki-source-0d838e9255be9db6396b747b
    resource: repo://ansible/roles/hooks/tasks/validate.yml
  - id: openwiki-source-2143ad1cf9f8ecf21dbb6613
    resource: repo://ansible/roles/restic_backup/defaults/main.yml
  - id: openwiki-source-7c5b9b5db4ec449784cb6198
    resource: repo://ansible/roles/restic_backup/templates/restic-backup.service.j2
  - id: openwiki-source-25b272f502e9dde6483cc924
    resource: repo://ansible/tasks/verify-hooks-snapshot.yml
generated: { by: "codex", at: "2026-10-31T14:36:53.458Z" }
---

# Вебхуки и резервные копии

Описание декларативного кода на SHA `1e7bc4e5946c69065c04393efb015eb7c9b5101f`; фактическое состояние Selectel и выкатку проверяют отдельно.

## Отдельная цель настройки

`auxiliary.yml` нацелен на группу auxiliary и проверяет параметры hooks до изменения пользователей, firewall и конфигурации. Затем применяются базовые роли, hooks и restic. Это отдельный путь от gateway/backend в `site.yml`.

[Источник: ansible/auxiliary.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/auxiliary.yml#L4-L27)

## Контейнер и входные данные

Hooks требует образ по registry digest, согласованные пути данных и пустой `hooks_command`. До запуска проверяются обязательные переменные, а при включённой отправке — параметры Telegram/Apidog. Env записывается с закрытыми правами и `no_log`; секретная конфигурация proxy также исключена из diff.

[Источник: ansible/roles/hooks/tasks/validate.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/roles/hooks/tasks/validate.yml#L2-L37) · [Источник: ansible/roles/hooks/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/roles/hooks/tasks/main.yml#L18-L42)

## Порядок запуска

Роль скачивает публичный образ без login, при ошибке pull завершает выполнение. Затем подготавливает владельца тома для пользователя bun из образа, поднимает Compose с ожиданием healthcheck и проверяет публичный health с управляющей машины. Это описывает предусмотренную проверку, а не результат её запуска.

[Источник: ansible/roles/hooks/tasks/pull-image.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/roles/hooks/tasks/pull-image.yml#L2-L17) · [Источник: ansible/roles/hooks/tasks/prepare-data.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/roles/hooks/tasks/prepare-data.yml#L4-L22) · [Источник: ansible/roles/hooks/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/roles/hooks/tasks/main.yml#L67-L92)

## Сначала snapshot, затем restic

Unit restic сначала вызывает CLI snapshot сервиса; ошибка этой команды прерывает запуск backup. Явный список архивирования включает snapshot очереди, данные/конфигурацию Caddy и каталог Compose, но не живую SQLite WAL-базу. После backup применяется политика хранения daily/weekly с prune.

[Источник: ansible/roles/restic_backup/templates/restic-backup.service.j2](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/roles/restic_backup/templates/restic-backup.service.j2#L13-L16) · [Источник: ansible/roles/restic_backup/defaults/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/roles/restic_backup/defaults/main.yml#L6-L15)

## Что подтверждает verify

`verify-hooks-snapshot.yml` проверяет существование, обычный тип файла и ненулевой размер snapshot. Это не тест восстановления: для уверенности в backup отдельно нужны проверка restic и изолированное восстановление. [Проверки](../testing/verification.md) объясняют остальные границы.

[Источник: ansible/tasks/verify-hooks-snapshot.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/tasks/verify-hooks-snapshot.yml#L2-L15)

[Карта wiki](../quickstart.md)
