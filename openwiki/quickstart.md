---
type: guide
title: Карта инфраструктуры
description: Куда идти за Pulumi, Ansible, релизами, runtime и проверками; как оценить свежесть wiki.
tags: [infra, guide]
sources:
  - id: openwiki-source-e119253b3c3737247dc63f2a
    resource: repo://.openwikiignore
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-1f9936c402ec4050b7ba2299
    resource: repo://SUMMARY.md
generated: { by: "codex", at: "2026-11-21T16:58:46.168Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-21T16:58:46.168Z
---

# Карта инфраструктуры

## Как читать эту wiki

Wiki описывает декларативный код infra. Состояние облака, доступность сервиса и текущую версию релиза подтверждают отдельно на стенде. Корпус исключает конфиги стеков, inventory, реальные env, ключи и state. Сетевые константы описаны по их назначению.

[Источник: .openwikiignore](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.openwikiignore#L18-L59) · [Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/AGENTS.md#L25-L38)

## Маршруты по задачам

| Задача | Страница | Первые исходники |
| --- | --- | --- |
| Ресурсы, сеть, gateway/backend | [Облако](architecture/cloud.md) | `pulumi/index.ts`, `pulumi/bootstrap/index.ts` |
| S3, права, защита данных, CDN | [Хранилища](architecture/storage.md) | `pulumi/storage.ts`, `pulumi/domains.ts` |
| Подготовка хоста и релиз API/BFF | [Деплой](operations/deployment.md) | `ansible/site.yml`, `ansible/deploy-backend.yml`, `ansible/deploy-bff.yml` |
| Подготовка runtime VPS и сеть kernel | [Runtime](operations/runtime.md) | `ansible/runtime/prepare.yml` |
| Вебхуки и backup очереди | [Auxiliary](operations/auxiliary.md) | `ansible/auxiliary.yml` |
| Локальные тесты, CI, стенд | [Проверки](testing/verification.md) | `.github/workflows/infra.yml`, `ansible/verify.yml` |

## Свежесть относительно исходников

При обычном чтении актуальность опубликованной wiki обеспечивает процесс обновления и публикации. К исходнику переходят для правки, при пробеле или противоречии. Свежесть относительно исходников проверяют при обслуживании wiki, генерации, в CI или при явной диагностике.

```sh
bun scripts/openwiki-check.ts --against <полный-SHA-infra-main>
```

Checkpoint должен иметь `status: complete`, а его SHA должен быть предком целевого SHA.

[Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/AGENTS.md#L27-L34) · [Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/AGENTS.md#L57-L61) · [Источник: openwiki/INSTRUCTIONS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/openwiki/INSTRUCTIONS.md#L26-L40)

## Поиск между репозиториями

Начните с `openwiki_search`, затем вызовите `openwiki_read` с ID wiki из результата и ссылкой на нужный раздел. Если ответ достаточен и согласован, на этом можно остановиться. Без MCP читайте эту карту и нужную Markdown-страницу, а при отсутствии wiki переходите к `SUMMARY.md`, Pulumi и Ansible. Общая архитектура и сведения о проверенном проде находятся в [wiki docs](https://github.com/Cringe-Driven-Development-Team/docs/tree/main/openwiki).

[Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/AGENTS.md#L25-L44) · [Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/AGENTS.md#L57-L61)

## Обновление

После bootstrap ручное обновление запускается из соседнего docs командой `bun run wiki:update-local --repo ../infra`. Генерация выполняется локально отдельным wiki PR; CI проверяет его без модели и production environment. Источники и `INSTRUCTIONS.md` не входят в такой PR; Claims, индексы и checkpoint изменяет lifecycle OpenWiki. Это позволяет проверять документацию отдельно от изменения ресурсов и деплоя.

[Источник: SUMMARY.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/SUMMARY.md#L133-L135) · [Источник: openwiki/INSTRUCTIONS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/openwiki/INSTRUCTIONS.md#L38-L43)
