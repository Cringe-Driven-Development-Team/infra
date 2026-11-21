---
type: operations
title: Вебхуки и резервные копии
description: Изоляция auxiliary, проверка конфигурации hooks и резервирование очереди через snapshot.
tags: [infra, operations]
sources:
  - id: openwiki-source-9f9915f890b2de275d85922a
    resource: repo://ansible/auxiliary.yml
  - id: openwiki-source-3d8956538a9600ded637cca8
    resource: repo://ansible/roles/hooks/tasks/deploy.yml
  - id: openwiki-source-a7c5b73745c56676960ac831
    resource: repo://ansible/roles/hooks/tasks/main.yml
  - id: openwiki-source-ed9d0f5d6cfb125eb17fe4c7
    resource: repo://ansible/roles/hooks/tasks/prepare-data.yml
  - id: openwiki-source-bfe0c7d639da9f03984f5403
    resource: repo://ansible/roles/hooks/tasks/validate-candidate.yml
  - id: openwiki-source-0d838e9255be9db6396b747b
    resource: repo://ansible/roles/hooks/tasks/validate.yml
  - id: openwiki-source-363c7d448c2e37aeb20d0600
    resource: repo://ansible/roles/hooks/tasks/verify-service.yml
  - id: openwiki-source-2143ad1cf9f8ecf21dbb6613
    resource: repo://ansible/roles/restic_backup/defaults/main.yml
  - id: openwiki-source-7c5b9b5db4ec449784cb6198
    resource: repo://ansible/roles/restic_backup/templates/restic-backup.service.j2
  - id: openwiki-source-25b272f502e9dde6483cc924
    resource: repo://ansible/tasks/verify-hooks-snapshot.yml
generated: { by: "codex", at: "2026-11-21T16:58:46.168Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-21T16:58:46.168Z
---

# Вебхуки и резервные копии

Описание декларативного кода. Фактическое состояние инфраструктуры и выкатку проверяют отдельно.

## Отдельная цель настройки

`auxiliary.yml` нацелен на группу auxiliary и проверяет параметры hooks до изменения пользователей, firewall и конфигурации. Затем применяются базовые роли, hooks и restic. Это отдельный путь от gateway/backend в `site.yml`.

[Источник: ansible/auxiliary.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/auxiliary.yml#L4-L30)

## Контейнер и входные данные

Hooks требует образ по registry digest, согласованные пути данных, пустой `hooks_command` и секрет подписи webhook GitHub. При включённой отправке проверяются токен Telegram и ID чата. Env записывается с ограниченными правами и `no_log`. Для конфигурации proxy также отключён diff.

[Источник: ansible/roles/hooks/tasks/validate.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/validate.yml#L2-L18) · [Источник: ansible/roles/hooks/tasks/validate.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/validate.yml#L53-L74) · [Источник: ansible/roles/hooks/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/main.yml#L28-L60)

### Уведомления о выпусках и выкатках

Заданные темы выпусков фронтенда и backend должны содержать положительный целый ID в диапазоне JavaScript safe integer. Непустая тема выкаток инфраструктуры проходит ту же проверку. В каждом из этих случаев требуется `GITHUB_API_TOKEN`. Проверки выполняются независимо от общего флага отправки.

[Источник: ansible/roles/hooks/tasks/validate.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/validate.yml#L20-L51)

## Порядок запуска

Роль проверяет конфигурацию Compose и анонимно скачивает закреплённые образы до записи нового Compose. Неудачный pull прерывает выкатку. Роль приводит владельца тома очереди к пользователю bun, ждёт запуска проекта и проверяет публичный HTTPS health. Точечная выкладка меняет только образ выбранного сервиса и подтверждает его после проверки.

[Источник: ansible/roles/hooks/tasks/validate-candidate.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/validate-candidate.yml#L8-L55) · [Источник: ansible/roles/hooks/tasks/prepare-data.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/prepare-data.yml#L2-L22) · [Источник: ansible/roles/hooks/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/main.yml#L62-L107) · [Источник: ansible/roles/hooks/tasks/deploy.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/deploy.yml#L106-L144) · [Источник: ansible/roles/hooks/tasks/verify-service.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/hooks/tasks/verify-service.yml#L49-L63)

## Сначала snapshot, затем restic

Unit restic сначала вызывает CLI snapshot сервиса; ошибка этой команды прерывает запуск backup. Явный список архивирования включает snapshot очереди, данные/конфигурацию Caddy и каталог Compose, но не живую SQLite WAL-базу. После backup применяется политика хранения daily/weekly с prune.

[Источник: ansible/roles/restic_backup/templates/restic-backup.service.j2](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/roles/restic_backup/templates/restic-backup.service.j2#L13-L16) · [Источник: ansible/roles/restic_backup/defaults/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/roles/restic_backup/defaults/main.yml#L6-L15)

## Что подтверждает verify

`verify-hooks-snapshot.yml` проверяет существование, обычный тип файла и ненулевой размер snapshot. Это не тест восстановления: для уверенности в backup отдельно нужны проверка restic и изолированное восстановление. [Проверки](../testing/verification.md) объясняют остальные границы.

[Источник: ansible/tasks/verify-hooks-snapshot.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/tasks/verify-hooks-snapshot.yml#L2-L15)

[Карта wiki](../quickstart.md)
