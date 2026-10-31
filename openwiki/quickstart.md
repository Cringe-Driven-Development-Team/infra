---
type: guide
title: Карта инфраструктуры
description: Куда идти за Pulumi, Ansible, релизами, runtime и проверками; как оценить свежесть wiki.
tags: [infra, guide]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-31T14:36:53.458Z
sources:
  - id: openwiki-source-e119253b3c3737247dc63f2a
    resource: repo://.openwikiignore
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-1f9936c402ec4050b7ba2299
    resource: repo://SUMMARY.md
generated: { by: "codex", at: "2026-10-31T14:36:53.458Z" }
---

# Карта инфраструктуры

Описание декларативного кода на SHA `1e7bc4e5946c69065c04393efb015eb7c9b5101f`; фактическое состояние Selectel и выкатку проверяют отдельно.

## Как читать эту wiki

Это производный указатель по декларативному коду infra. Он не утверждает, что ресурсы созданы, сервис доступен или релиз выкачен. Корпус исключает конфиги стеков, inventory, реальные env, ключи и state; значения сетевых констант здесь заменены их назначением или именем переменной.

[Источник: openwiki/INSTRUCTIONS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/openwiki/INSTRUCTIONS.md#L3-L18)

## Маршруты по задачам

| Задача | Страница | Первые исходники |
| --- | --- | --- |
| Ресурсы, сеть, gateway/backend | [Облако](architecture/cloud.md) | `pulumi/index.ts`, `pulumi/bootstrap/index.ts` |
| S3, права, защита данных, CDN | [Хранилища](architecture/storage.md) | `pulumi/index.ts`, `pulumi/selectel-storage.ts` |
| Подготовка хоста и релиз API/BFF | [Деплой](operations/deployment.md) | `ansible/site.yml`, `ansible/deploy-backend.yml`, `ansible/deploy-bff.yml` |
| Назначение сессии и сеть kernel | [Runtime](operations/runtime.md) | `ansible/runtime/site.yml` |
| Вебхуки и backup очереди | [Auxiliary](operations/auxiliary.md) | `ansible/auxiliary.yml` |
| Локальные тесты, CI, стенд | [Проверки](testing/verification.md) | `.github/workflows/infra.yml`, `ansible/verify.yml` |



## Свежесть относительно исходников

Из корня актуального checkout выполните:

```sh
bun scripts/openwiki-check.ts --against <полный-SHA-infra-main>
```

Checkpoint должен иметь `status: complete`; его SHA должен быть предком целевого SHA. Ориентируйтесь на результат checker, а не на дату обновления. При `stale` wiki остаётся указателем к файлу: утверждение сверяют с актуальным кодом. Даже `fresh` не заменяет свидетельство проверки стенда.

[Источник: openwiki/INSTRUCTIONS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/openwiki/INSTRUCTIONS.md#L26-L29)

## Поиск между репозиториями

AGENTS направляет вопросы infra в эту wiki, общую архитектуру и проверенный прод — в [docs](https://github.com/Cringe-Driven-Development-Team/docs/tree/main/openwiki), планы — в модули docs. Для связанного workspace передавайте в `openwiki_read` ID wiki из поиска. Без MCP читайте Markdown; если wiki отсутствует, переходите к `SUMMARY.md`, Pulumi и Ansible. Данные соседнего репозитория здесь не скопированы.

[Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/AGENTS.md#L14-L31) · [Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/AGENTS.md#L41-L48)

## Обновление

После bootstrap ручное обновление запускается из соседнего docs командой `bun run wiki:update-local --repo ../infra`. Генерация выполняется локально отдельным wiki PR; CI проверяет его без модели и production environment. Источники и `INSTRUCTIONS.md` не входят в такой PR; Claims, индексы и checkpoint изменяет lifecycle OpenWiki. Это позволяет проверять документацию отдельно от изменения ресурсов и деплоя.

[Источник: openwiki/INSTRUCTIONS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/openwiki/INSTRUCTIONS.md#L30-L32)
