---
type: architecture
title: Данные, S3 и домены
description: Защита бакетов, зависимости S3, dynamic-ресурсы доступа и привязки доменов.
tags: [infra, architecture]
sources:
  - id: openwiki-source-8cfe9eaff28795d31978b96f
    resource: repo://pulumi/domains.ts
  - id: openwiki-source-a814ab612b38039ee1006659
    resource: repo://pulumi/foundation.ts
  - id: openwiki-source-794328fb29325d13f5db178c
    resource: repo://pulumi/selectel-storage.test.ts
  - id: openwiki-source-50c0e28d3cce2a6cd87573c7
    resource: repo://pulumi/selectel-storage.ts
  - id: openwiki-source-a3aaca36c4232db6852b0258
    resource: repo://pulumi/storage.ts
generated: { by: "codex", at: "2026-11-21T16:58:46.168Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-21T16:58:46.168Z
---

# Данные, S3 и домены

Описание декларативного кода. Фактическое состояние Selectel и выкатку проверяют отдельно.

## Готовность S3

Основная программа откладывает создание S3 provider до инициализации хранилища проекта и принятия ключа шлюзом. В `isDryRun()` эта последовательность пропускается. Это граница побочных эффектов внутри программы; preview не следует считать проверкой работоспособности S3.

[Источник: pulumi/foundation.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/foundation.ts#L104-L138)

## Разные классы данных

Бакет аватарок публичный и защищён через `protect`. Бакеты релизов и опциональной статики используют `forceDestroy`, поэтому удаление ресурса допускает удаление объектов.

[Источник: pulumi/storage.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/storage.ts#L40-L53) · [Источник: pulumi/storage.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/storage.ts#L86-L99) · [Источник: pulumi/storage.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/storage.ts#L130-L143)

## Права приложения и управление

Политика аватарок оставляет пользователю стека `s3:*`, а сервисному пользователю аватарок даёт чтение, запись и удаление объектов. Анонимное чтение по ключу обеспечивает публичный тип бакета.

[Источник: pulumi/storage.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/storage.ts#L86-L128)

## Владение доступом

`BucketAccess` устанавливает тип бакета через API хранилища, ждёт публичный домен для public, а при удалении возвращает существующий бакет в private. Его `diff` учитывает сериализованный код provider, чтобы изменение реализации не осталось незаметным для обновления.

[Источник: pulumi/selectel-storage.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/selectel-storage.ts#L252-L284)

## Домены CDN

Для CDN релизов и статики программа создаёт CNAME, затем привязывает домен CDN с зависимостью от этой записи. Привязка проверяет `names` повторным GET после PATCH и завершает операцию ошибкой при исчерпании попыток.

[Источник: pulumi/domains.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/domains.ts#L57-L73) · [Источник: pulumi/selectel-storage.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/selectel-storage.ts#L170-L185)

## Проверки и соседи

Поведение привязки, повторов и отказов проверяется в `pulumi/selectel-storage.test.ts`. За backup вспомогательного сервиса отвечает [auxiliary](../operations/auxiliary.md); границы локальных тестов и стенда описаны в [проверках](../testing/verification.md).

[Источник: pulumi/selectel-storage.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/selectel-storage.test.ts#L24-L55) · [Источник: pulumi/selectel-storage.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/selectel-storage.test.ts#L75-L111)

[Карта wiki](../quickstart.md)
