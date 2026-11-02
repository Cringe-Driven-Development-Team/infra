---
type: guide
title: Карта инфраструктуры
description: Куда идти за Pulumi, Ansible, релизами, runtime и проверками; как оценить свежесть wiki.
tags: [infra, guide]
verified:
  - by: openwiki/0.7.2
    at: 2026-11-02T22:05:37.662Z
sources:
  - id: openwiki-source-e119253b3c3737247dc63f2a
    resource: repo://.openwikiignore
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-1f9936c402ec4050b7ba2299
    resource: repo://SUMMARY.md
generated: { by: "codex", at: "2026-11-02T22:05:37.662Z" }
---

# Карта инфраструктуры

Описание декларативного кода на SHA `ac02542b4d522e67ebedb666af09f2a4d0e02cfd`; фактическое состояние Selectel и выкатку проверяют отдельно.

## Как читать эту wiki

Это производный указатель по декларативному коду infra. Он не утверждает, что ресурсы созданы, сервис доступен или релиз выкачен. Корпус исключает конфиги стеков, inventory, реальные env, ключи и state; значения сетевых констант здесь заменены их назначением или именем переменной.

[Источник: openwiki/INSTRUCTIONS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/openwiki/INSTRUCTIONS.md#L3-L18)

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

[Источник: openwiki/INSTRUCTIONS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/openwiki/INSTRUCTIONS.md#L26-L29)

## Поиск между репозиториями

AGENTS направляет вопросы infra в эту wiki, общую архитектуру и проверенный прод — в [docs](https://github.com/Cringe-Driven-Development-Team/docs/tree/main/openwiki), планы — в модули docs. Для связанного workspace передавайте в `openwiki_read` ID wiki из поиска. Без MCP читайте Markdown; если wiki отсутствует, переходите к `SUMMARY.md`, Pulumi и Ansible. Данные соседнего репозитория здесь не скопированы.

[Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/AGENTS.md#L16-L27) · [Источник: AGENTS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/AGENTS.md#L37-L43)

## Обновление

После bootstrap ручное обновление запускается из соседнего docs командой `bun run wiki:update-local --repo ../infra`. Генерация выполняется локально отдельным wiki PR; CI проверяет его без модели и production environment. Источники и `INSTRUCTIONS.md` не входят в такой PR; Claims, индексы и checkpoint изменяет lifecycle OpenWiki. Это позволяет проверять документацию отдельно от изменения ресурсов и деплоя.

[Источник: SUMMARY.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/SUMMARY.md#L136-L138) · [Правила: openwiki/INSTRUCTIONS.md](https://github.com/Cringe-Driven-Development-Team/infra/blob/ac02542b4d522e67ebedb666af09f2a4d0e02cfd/openwiki/INSTRUCTIONS.md#L30-L32)
