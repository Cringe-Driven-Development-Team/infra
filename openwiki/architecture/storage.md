---
type: architecture
title: Данные, S3 и домены
description: Защита бакетов, зависимости S3, dynamic-ресурсы доступа и привязки доменов.
tags: [infra, architecture]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-31T14:36:53.458Z
sources:
  - id: openwiki-source-4a6d25d7743b479ecae59713
    resource: repo://pulumi/index.ts
  - id: openwiki-source-794328fb29325d13f5db178c
    resource: repo://pulumi/selectel-storage.test.ts
  - id: openwiki-source-50c0e28d3cce2a6cd87573c7
    resource: repo://pulumi/selectel-storage.ts
generated: { by: "codex", at: "2026-10-31T14:36:53.458Z" }
---

# Данные, S3 и домены

Описание декларативного кода на SHA `1e7bc4e5946c69065c04393efb015eb7c9b5101f`; фактическое состояние Selectel и выкатку проверяют отдельно.

## Готовность S3

Основная программа откладывает создание S3 provider до инициализации хранилища проекта и принятия ключа шлюзом. В `isDryRun()` эта последовательность пропускается. Это граница побочных эффектов внутри программы; preview не следует считать проверкой работоспособности S3.

[Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L371-L405)

## Разные классы данных

Ноутбуки объявлены приватными, аватарки — публичными через `BucketAccess`; оба бакета защищены `protect` и не включают `forceDestroy`. Бакет релизов и опциональная статика, напротив, включают `forceDestroy`: удаление их ресурса допускает удаление объектов. Это различие важно при ревью изменений хранения.

[Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L408-L421) · [Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L454-L467) · [Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L504-L516) · [Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L548-L561)

## Права приложения и управление

Политики ноутбуков и аватарок явно оставляют пользователю стека `s3:*`. Пользователь backend получает операции над объектами обоих бакетов, а листинг — для ноутбуков. Публичность аватарок задаётся типом бакета, без правила `PublicRead` в его политике. Ни реальные ключи, ни имена учётных записей не являются содержимым wiki.

[Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L473-L502) · [Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L524-L546)

## Владение доступом

`BucketAccess` устанавливает тип бакета через API хранилища, ждёт публичный домен для public, а при удалении возвращает существующий бакет в private. Его `diff` учитывает сериализованный код provider, чтобы изменение реализации не осталось незаметным для обновления.

[Источник: pulumi/selectel-storage.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/selectel-storage.ts#L252-L284)

## Домены и ручная граница

Для CDN статики программа создаёт CNAME и затем `CdnDomain` с зависимостью от записи. Привязка CDN проверяет `names` повторным GET после PATCH и завершается ошибкой при исчерпании попыток. Сертификаты пользовательских доменов оставлены ручному выпуску в панели; успешная декларация DNS сама по себе не доказывает HTTPS на стенде.

[Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L65-L67) · [Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L626-L649) · [Источник: pulumi/selectel-storage.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/selectel-storage.ts#L170-L185)

## Проверки и соседи

Поведение привязки, повторов и отказов проверяется в `pulumi/selectel-storage.test.ts`. За backup вспомогательного сервиса отвечает [auxiliary](../operations/auxiliary.md); границы локальных тестов и стенда описаны в [проверках](../testing/verification.md).

[Источник: pulumi/selectel-storage.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/selectel-storage.test.ts#L24-L55) · [Источник: pulumi/selectel-storage.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/selectel-storage.test.ts#L75-L111)

[Карта wiki](../quickstart.md)
