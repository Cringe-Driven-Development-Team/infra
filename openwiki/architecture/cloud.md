---
type: architecture
title: Ресурсы облака и границы стеков
description: Два стека Pulumi, сеть gateway и приватного backend, зависимости и границы владения.
tags: [infra, architecture]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-31T14:36:53.458Z
sources:
  - id: openwiki-source-3c00005662a2894d1caf8947
    resource: repo://ansible/site.yml
  - id: openwiki-source-178fce7909991c9067421c1e
    resource: repo://pulumi/bootstrap/index.ts
  - id: openwiki-source-4a6d25d7743b479ecae59713
    resource: repo://pulumi/index.ts
generated: { by: "codex", at: "2026-10-31T14:36:53.458Z" }
---

# Ресурсы облака и границы стеков

Описание декларативного кода на SHA `1e7bc4e5946c69065c04393efb015eb7c9b5101f`; фактическое состояние Selectel и выкатку проверяют отдельно.

## Долгоживущий bootstrap

`pulumi/bootstrap/index.ts` отделяет общий проект и DNS-зону от проекта продукта. Проект, зона и бакет стейта имеют `protect: true`; для бакета включено версионирование. Это декларативные меры защиты, а не свидетельство наличия резервной копии.

[Источник: pulumi/bootstrap/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/bootstrap/index.ts#L16-L23) · [Источник: pulumi/bootstrap/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/bootstrap/index.ts#L73-L80)

## Авторизация перед ресурсами

Основная программа получает credentials из окружения и заранее отвергает `OS_PROJECT_NAME`, а также `selectel:username/password` в конфиге. OpenStack provider получает имя нового сервисного пользователя через зависимость от его ID: вызовы поиска ресурсов должны дождаться создания пользователя.

[Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L13-L34) · [Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L175-L184)

## Gateway и сеть

Основной стек объявляет приватную сеть, подсеть и роутер. Gateway получает отдельный порт, boot-диск и floating IP; привязка floating IP зависит от интерфейса роутера. Диск не удаляется при завершении экземпляра, а замена экземпляра использует `deleteBeforeReplace`. CIDR и адреса здесь намеренно не воспроизводятся.

[Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L244-L293)

## Приватный backend

Ветка `infra:backend` объявляет второй сервер в той же сети без отдельного floating IP. Ingress security group разрешает SSH и API от gateway, ICMP — из подсети. `metadata.role` отличает gateway и backend; Ansible `site.yml` сначала применяет базовые роли к gateway, затем к backend, исключая Caddy для второго хоста.

[Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L301-L358) · [Источник: ansible/site.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/site.yml#L5-L26)

## Дальнейшие маршруты

Политики данных и DNS — [S3 и домены](storage.md). Релизы контейнеров — [выкатка](../operations/deployment.md). Материалы доступа runtime и настройка сессии — [runtime](../operations/runtime.md). Наличие этих веток в программе не означает, что соответствующие флаги включены в каком-либо стеке.



[Карта wiki](../quickstart.md)
