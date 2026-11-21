---
type: testing
title: Проверки и границы свидетельств
description: Локальные тесты деклараций, CI preview/apply и проверки стенда Ansible verify.
tags: [infra, testing]
sources:
  - id: openwiki-source-8014719a1e041417a7bc1faa
    resource: repo://.github/workflows/infra.yml
  - id: openwiki-source-8ece604787a8810976b11f33
    resource: repo://ansible/tests/test_deploy.py
  - id: openwiki-source-4ec8511f4cf587e7262215df
    resource: repo://ansible/tests/test_hooks.py
  - id: openwiki-source-ee93631d668f7c764486e435
    resource: repo://ansible/verify.yml
  - id: openwiki-source-37d442f8646ada18d0a5ee57
    resource: repo://pulumi/bootstrap/selectel-s3.test.ts
generated: { by: "codex", at: "2026-11-21T16:58:46.168Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-21T16:58:46.168Z
---

# Проверки и границы свидетельств

Внешние проверки стенда и результаты выкатки смотрите в запуске CI для нужного SHA.

## Локальный цикл

Workflow задаёт `bun test` для основной Pulumi-программы и bootstrap; для основной программы дополнительно запускается TypeScript typecheck. Для Ansible предусмотрены unittest с `COMPOSE_CLI='docker compose'`, syntax-check основных плейбуков, а для runtime — lint и отдельный syntax-check с fixture. Зависимости перед этим устанавливаются явно. Эти команды проверяют код и шаблоны, а не статус облачных ресурсов.

[Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L56-L86)

## Какие отказы покрываются

Тесты S3 используют управляемые ответы и часы для проверки ожидания ключа, таймаутов и ограничения сетевых ошибок; секрет проверяется на отсутствие в argv и тексте ошибки. Тесты деплоя отвергают некорректные теги API и BFF без digest/полного SHA. Это полезные точки входа при изменении preflight и retry.

[Источник: pulumi/bootstrap/selectel-s3.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/bootstrap/selectel-s3.test.ts#L76-L120) · [Источник: ansible/tests/test_deploy.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/tests/test_deploy.py#L145-L181)

Тесты hooks проверяют тему и API-токен для уведомлений о релизе. При включённой отправке preflight требует секрет вебхука GitHub, токен и идентификатор чата Telegram. Тесты проверяют, что ошибочный preflight не раскрывает тестовые секреты, а итоговое окружение сервиса Compose содержит ровно ожидаемый набор переменных.

[Источник: ansible/tests/test_hooks.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/tests/test_hooks.py#L83-L98) · [Источник: ansible/tests/test_hooks.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/tests/test_hooks.py#L170-L189) · [Источник: ansible/tests/test_hooks.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/tests/test_hooks.py#L281-L306)

## Preview и apply

`infra.yml` запускается по изменениям Pulumi, Ansible и самого workflow. Preview для PR из этого репозитория зависит от checks и использует environment `infra-preview`: там предусмотрены Pulumi preview и Ansible check/diff. Apply разрешён на main для push или ручного запуска, зависит от checks и использует production. Его очередь не отменяет уже запущенную операцию; порядок — Pulumi up, site, auxiliary, verify.

[Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L7-L19) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L89-L99) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L142-L156) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L207-L222) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L254-L282)

### Передача известных SSH-ключей

Шаг подготовки секретов получает `SSH_KNOWN_HOSTS`. В workflow включена строгая проверка ключей Ansible. Вызовы `site.yml` для preview и apply и `verify.yml` для apply используют `INFRA_SSH_ARGS` и получают путь к `known_hosts` через `jump_known_hosts`.

[Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L25-L30) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L120-L131) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L149-L155) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L247-L248) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L262-L282)

## Что проверяется на стенде

`verify.yml` содержит внешние проверки BFF, TLS и портов; на gateway/backend — контейнер, Redis, firewall и эффективный sshd. Отдельные блоки предназначены для auxiliary и split backend/S2S. Их присутствие в Git не означает успешного исполнения: свидетельством служит результат конкретного запуска на нужном стенде.

[Источник: ansible/verify.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/verify.yml#L57-L97) · [Источник: ansible/verify.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/verify.yml#L119-L219) · [Источник: ansible/verify.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/verify.yml#L246-L318) · [Источник: ansible/verify.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/verify.yml#L324-L355)

## Backup и восстановление

Локальный hooks-тест с restic проверяет содержимое архива: snapshot включён, живая база и WAL/SHM исключены. Затем он восстанавливает fixture и проверяет целостность SQLite и записи. Это тест механизма на fixture, не подтверждение восстановимости текущей облачной копии.

[Источник: ansible/tests/test_hooks.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/tests/test_hooks.py#L326-L382)

## Выбор следующей проверки

При изменении релиза начните с [деплоя](../operations/deployment.md), сетевой изоляции — с [runtime](../operations/runtime.md), хранения очереди — с [auxiliary](../operations/auxiliary.md). Проверка свежести wiki относительно SHA описана в [quickstart](../quickstart.md) и не заменяет ни один стендовый сценарий.



[Карта wiki](../quickstart.md)
