---
type: operations
title: Подготовка хостов и выкатка API/BFF
description: Точки входа Ansible, выбор сервисов, проверка BFF и ограниченное восстановление при сбое.
tags: [infra, operations]
sources:
  - id: openwiki-source-d76f6aaea685257d0d950e8d
    resource: repo://ansible/bootstrap.yml
  - id: openwiki-source-4bcb7c19507dcccd68312234
    resource: repo://ansible/deploy-backend.yml
  - id: openwiki-source-88188b8b9b375bdca5a94efd
    resource: repo://ansible/deploy-bff.yml
  - id: openwiki-source-7de3d5e270229cbfe817f19d
    resource: repo://ansible/roles/app/tasks/main.yml
  - id: openwiki-source-3a9c98c7a31c269263ced081
    resource: repo://ansible/roles/app/tasks/probe-bff.yml
  - id: openwiki-source-9cc0c4087f8c472014c827b6
    resource: repo://ansible/roles/app/tasks/pull-bff.yml
  - id: openwiki-source-26b47f647eb84aa994c0ba56
    resource: repo://ansible/roles/app/tasks/tags.yml
  - id: openwiki-source-3c00005662a2894d1caf8947
    resource: repo://ansible/site.yml
generated: { by: "codex", at: "2026-11-21T16:58:46.168Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-21T16:58:46.168Z
---

# Подготовка хостов и выкатка API/BFF

Описание декларативного кода. Фактическое состояние инфраструктуры и выкатку проверяют отдельно.

## Первый контакт и базовая настройка

`bootstrap.yml` использует root для установки Python и роли users на новых хостах. `site.yml` затем применяет базовую настройку gateway и backend с повышением привилегий; Caddy входит только в gateway. Bootstrap нужен отдельно от обычной выкатки контейнеров.

[Источник: ansible/bootstrap.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/bootstrap.yml#L5-L26) · [Источник: ansible/site.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/site.yml#L5-L26)

## Два входа для релизов

`deploy-backend.yml` требует по одному хосту в группах gateway и backend и выкатывает API на backend. `deploy-bff.yml` работает на gateway. Роль app использует записанный digest для компонента, которому не передан новый образ. Перед выкаткой BFF проверяется доступность Go API с gateway.

[Источник: ansible/deploy-backend.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/deploy-backend.yml#L5-L37) · [Источник: ansible/deploy-bff.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/deploy-bff.yml#L5-L31) · [Источник: ansible/roles/app/tasks/tags.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/app/tasks/tags.yml#L4-L54)

## BFF проверяется до изменения файлов

BFF задаётся digest и полным SHA релиза. Pull выполняется с пустым временным Docker config, после чего label `org.opencontainers.image.revision` сравнивается с запросом. Ошибка скачивания либо несовпадение останавливают выполнение до применения роли app.

[Источник: ansible/deploy-bff.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/deploy-bff.yml#L22-L33) · [Источник: ansible/roles/app/tasks/pull-bff.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/roles/app/tasks/pull-bff.yml#L5-L55)

## Health и отказ

App запускает Compose с `wait: true`, `pull: never` и удалением orphan-контейнеров. Rescue восстанавливает сохранённые env/Compose-файлы, либо убирает файлы неудачной первой установки, после чего явно падает. Контейнеры автоматически не откатываются: восстановление файлов не является завершённым rollback, нужен повторный деплой рабочего образа.

[Источник: ansible/roles/app/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/app/tasks/main.yml#L143-L188)

## Публичный путь после запуска

После healthcheck роль применяет Caddy route. `deploy-bff.yml` затем сверяет работающий контейнер и запускает внешний probe: гостевой HTML с release/bootstrap и защитными заголовками, отказ при входе с неверными данными, отказ защищённого RPC без сессии и недоступность внутренних API/health. Эти задачи становятся свидетельством стенда только после реального успешного запуска.

[Источник: ansible/roles/app/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/app/tasks/main.yml#L194-L211) · [Источник: ansible/deploy-bff.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/deploy-bff.yml#L34-L43) · [Источник: ansible/roles/app/tasks/probe-bff.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/roles/app/tasks/probe-bff.yml#L5-L103)

## Где менять и проверять

Ресурсы хостов принадлежат [Pulumi](../architecture/cloud.md), настройка пользовательских сессий — [runtime](runtime.md), CI и локальные тесты — [проверкам](../testing/verification.md). Исходники BFF принадлежат [frontend](https://github.com/Cringe-Driven-Development-Team/frontend), Go API — [backend](https://github.com/Cringe-Driven-Development-Team/backend); здесь описан их механизм выкатки.



[Карта wiki](../quickstart.md)
