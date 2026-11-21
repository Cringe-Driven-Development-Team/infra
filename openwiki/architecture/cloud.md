---
type: architecture
title: Ресурсы облака и границы стеков
description: Два стека Pulumi, сеть gateway и приватного backend, зависимости и границы владения.
tags: [infra, architecture]
verified:
  - by: openwiki/0.7.2
    at: 2026-11-21T16:58:46.168Z
sources:
  - id: openwiki-source-3c00005662a2894d1caf8947
    resource: repo://ansible/site.yml
  - id: openwiki-source-eda60f1d6520a70831fe5eed
    resource: repo://pulumi/backend.ts
  - id: openwiki-source-178fce7909991c9067421c1e
    resource: repo://pulumi/bootstrap/index.ts
  - id: openwiki-source-add79eeb0b8d2c389d888bdd
    resource: repo://pulumi/config.ts
  - id: openwiki-source-a814ab612b38039ee1006659
    resource: repo://pulumi/foundation.ts
  - id: openwiki-source-cc8dd97699845fed76dd3c76
    resource: repo://pulumi/gateway.ts
generated: { by: "codex", at: "2026-11-21T16:58:46.168Z" }
---

# Ресурсы облака и границы стеков

Описание декларативного кода. Фактическое состояние Selectel и выкатку проверяют отдельно.

## Долгоживущий bootstrap

`pulumi/bootstrap/index.ts` отделяет общий проект и DNS-зону от проекта продукта. Проект, зона и бакет стейта имеют `protect: true`; для бакета включено версионирование. Это декларативные меры защиты, а не свидетельство наличия резервной копии.

[Источник: pulumi/bootstrap/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/bootstrap/index.ts#L16-L23) · [Источник: pulumi/bootstrap/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/bootstrap/index.ts#L73-L80)

## Авторизация перед ресурсами

Основная программа получает credentials из окружения и заранее отвергает `OS_PROJECT_NAME`, а также `selectel:username/password` в конфиге. OpenStack provider получает имя нового сервисного пользователя через зависимость от его ID: вызовы поиска ресурсов должны дождаться создания пользователя.

[Источник: pulumi/config.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/config.ts#L137-L157) · [Источник: pulumi/foundation.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/foundation.ts#L58-L67)

## Gateway и сеть

Основной стек объявляет приватную сеть, подсеть и роутер. Gateway получает отдельный порт, boot-диск и floating IP; привязка floating IP зависит от интерфейса роутера. Диск не удаляется при завершении экземпляра, а замена экземпляра использует `deleteBeforeReplace`. CIDR и адреса здесь намеренно не воспроизводятся.

[Источник: pulumi/gateway.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/gateway.ts#L12-L63)

## Приватный backend

Ветка `infra:backend` объявляет второй сервер в той же сети без отдельного floating IP. Ingress security group разрешает SSH и API от gateway, ICMP — из подсети. `metadata.role` отличает gateway и backend; Ansible `site.yml` сначала применяет базовые роли к gateway, затем к backend, исключая Caddy для второго хоста.

[Источник: pulumi/backend.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/backend.ts#L15-L79) · [Источник: ansible/site.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/site.yml#L5-L26)

## Дальнейшие маршруты

Политики данных и DNS — [S3 и домены](storage.md). Релизы контейнеров — [выкатка](../operations/deployment.md). Материалы доступа runtime и настройка сессии — [runtime](../operations/runtime.md). Наличие этих веток в программе не означает, что соответствующие флаги включены в каком-либо стеке.



[Карта wiki](../quickstart.md)
