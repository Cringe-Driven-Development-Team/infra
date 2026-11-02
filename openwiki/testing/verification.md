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
generated: { by: "codex", at: "2026-11-02T22:05:37.662Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-02T22:05:37.662Z
---

# Проверки и границы свидетельств

Описание декларативного кода на SHA `ac02542b4d522e67ebedb666af09f2a4d0e02cfd`; фактическое состояние Selectel и выкатку проверяют отдельно.

## Локальный цикл

Workflow задаёт `bun test` для основной Pulumi-программы и bootstrap; для основной программы дополнительно запускается TypeScript typecheck. Для Ansible предусмотрены unittest с `COMPOSE_CLI='docker compose'`, syntax-check основных плейбуков, а для runtime — lint и отдельный syntax-check с fixture. Зависимости перед этим устанавливаются явно. Эти команды проверяют код и шаблоны, а не статус облачных ресурсов.

[Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L49-L75)

## Какие отказы покрываются

Тесты S3 используют управляемые ответы и часы для проверки ожидания ключа, таймаутов и ограничения сетевых ошибок; секрет проверяется на отсутствие в argv и тексте ошибки. Тесты деплоя отвергают некорректные теги API и BFF без digest/полного SHA. Это полезные точки входа при изменении preflight и retry.

[Источник: pulumi/bootstrap/selectel-s3.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/pulumi/bootstrap/selectel-s3.test.ts#L76-L120) · [Источник: ansible/tests/test_deploy.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/tests/test_deploy.py#L116-L158)

Тесты hooks проверяют принятие корректной конфигурации уведомлений о выпусках и отказ при нулевой или слишком большой теме, пустом либо отсутствующем `GITHUB_API_TOKEN`; тестовый токен не должен появляться в выводе отказа. Для отправки Apidog проверяется отказ при отсутствии `APIDOG_MR_REVIEWERS` и других обязательных параметров. Это описывает сценарии тестов, а не результат их запуска.

[Источник: ansible/tests/test_hooks.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/tests/test_hooks.py#L73-L88) · [Источник: ansible/tests/test_hooks.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/tests/test_hooks.py#L109-L146)

## Preview и apply

`infra.yml` запускается по изменениям Pulumi, Ansible и самого workflow. Preview для PR из этого репозитория зависит от checks и использует environment `infra-preview`: там предусмотрены Pulumi preview и Ansible check/diff. Apply разрешён на main для push или ручного запуска, зависит от checks и использует production. Его очередь не отменяет уже запущенную операцию; порядок — Pulumi up, site, auxiliary, verify.

[Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L6-L15) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L77-L88) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L138-L160) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L187-L198) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L217-L241)

### Передача известных SSH-ключей

Workflow сохраняет `SSH_KNOWN_HOSTS` в файл `known_hosts` во временном каталоге runner и задаёт `INFRA_SSH_ARGS` с этим файлом и строгой проверкой ключей хоста. В вызовы `site.yml` для preview и apply, а также `verify.yml` при apply дополнительно передаётся `jump_known_hosts`, указывающий на тот же файл. Здесь подтверждается передача параметра; фактическая схема SSH-подключения из исключённого inventory не рассматривается.

[Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L119-L133) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L145-L152) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/.github/workflows/infra.yml#L224-L241)

## Что проверяется на стенде

`verify.yml` содержит внешние проверки BFF, TLS и портов; на gateway/backend — контейнер, Redis, firewall и эффективный sshd. Отдельные блоки предназначены для auxiliary и split backend/S2S. Их присутствие в Git не означает успешного исполнения: свидетельством служит результат конкретного запуска на нужном стенде.

[Источник: ansible/verify.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/verify.yml#L57-L100) · [Источник: ansible/verify.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/verify.yml#L121-L219) · [Источник: ansible/verify.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/verify.yml#L246-L340)

## Backup и восстановление

Локальный hooks-тест с restic проверяет содержимое архива: snapshot включён, живая база и WAL/SHM исключены. Затем он восстанавливает fixture и проверяет целостность SQLite и записи. Это тест механизма на fixture, не подтверждение восстановимости текущей облачной копии.

[Источник: ansible/tests/test_hooks.py](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/ansible/tests/test_hooks.py#L266-L324)

## Выбор следующей проверки

При изменении релиза начните с [деплоя](../operations/deployment.md), сетевой изоляции — с [runtime](../operations/runtime.md), хранения очереди — с [auxiliary](../operations/auxiliary.md). Проверка свежести wiki относительно SHA описана в [quickstart](../quickstart.md) и не заменяет ни один стендовый сценарий.



[Карта wiki](../quickstart.md)
