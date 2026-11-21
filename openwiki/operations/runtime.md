---
type: operations
title: Provisioning runtime-сессий
description: Граница Pulumi и backend, повторное назначение VPS, сеть kernel и доставка агента.
tags: [infra, operations]
sources:
  - id: openwiki-source-8014719a1e041417a7bc1faa
    resource: repo://.github/workflows/infra.yml
  - id: openwiki-source-0b3d39b3ce76beafe9b965af
    resource: repo://ansible/runtime/prepare.yml
  - id: openwiki-source-5ddab4963a554a77ce7dfc6e
    resource: repo://ansible/runtime/roles/runtime_agent/tasks/main.yml
  - id: openwiki-source-f0db81fc48e9da563a28cb0c
    resource: repo://ansible/runtime/roles/runtime_host/tasks/main.yml
  - id: openwiki-source-09b6a79b98dd78b2f6c41588
    resource: repo://ansible/runtime/roles/runtime_host/templates/firewall.nft.j2
  - id: openwiki-source-02cfa1db9bb098ddcab33853
    resource: repo://ansible/runtime/roles/runtime_host/templates/nginx-agent.conf.j2
  - id: openwiki-source-add79eeb0b8d2c389d888bdd
    resource: repo://pulumi/config.ts
  - id: openwiki-source-0c381c0fc9e669d92423b26f
    resource: repo://pulumi/index.test.ts
  - id: openwiki-source-260da8d4eb82f42b732bd6d4
    resource: repo://pulumi/runtime.ts
generated: { by: "codex", at: "2026-11-21T16:58:46.168Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-21T16:58:46.168Z
---

# Provisioning runtime-сессий

Описание декларативного кода. Фактическое состояние инфраструктуры и выкатку проверяют отдельно.

## Что принадлежит Pulumi

Ветка `infra:runtime` требует включённый backend. Она объявляет отдельного сервисного пользователя проекта, security group, SSH keypair, CA, клиентский сертификат и ключ шифрования. Ingress SSH и TLS агента ограничен backend. Согласно границе в программе, сами VPS сессий создаёт и удаляет Go API; эти динамические экземпляры не объявлены ресурсами данной ветки.

[Источник: pulumi/runtime.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/runtime.ts#L64-L146)

## Контракт операции

`ansible/runtime/prepare.yml` проверяет параметры хоста и пути артефактов, а для TLS использует `no_log`. Затем ждёт SSH и cloud-init. Коды 0 и 2 допускаются. Роли runtime_host и runtime_agent подготавливают VPS без сессии. Агент ждёт отдельного назначения от Go API через `POST /assign`.

[Источник: ansible/runtime/prepare.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/runtime/prepare.yml#L2-L52)

## Сеть kernel и TLS

Runtime_host загружает и проверяет собственную таблицу nftables до запуска Docker, затем сверяет форму bridge-сети. Шаблон firewall разрешает DNS только к `kernel_dns`, фильтрует запрещённые назначения до разрешения HTTP/HTTPS и запрещает прочий новый исходящий трафик kernel. Nginx слушает `private_ip`, требует клиентский сертификат CA и проверяет CN backend, затем проксирует к локальному агенту.

[Источник: ansible/runtime/roles/runtime_host/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/runtime/roles/runtime_host/tasks/main.yml#L33-L125) · [Источник: ansible/runtime/roles/runtime_host/templates/firewall.nft.j2](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/runtime/roles/runtime_host/templates/firewall.nft.j2#L33-L50) · [Источник: ansible/runtime/roles/runtime_host/templates/nginx-agent.conf.j2](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/runtime/roles/runtime_host/templates/nginx-agent.conf.j2#L8-L39)

## Проверенный артефакт агента

Runtime_agent проверяет SHA-256 скопированного wheel. При изменении артефактов зависимости устанавливаются из отдельного requirements, а wheel — с `--no-deps`. Env агента записывается с правами 0600. Systemd drop-ins связывают старт агента с firewall и монтированием рабочего каталога.

[Источник: ansible/runtime/roles/runtime_agent/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/runtime/roles/runtime_agent/tasks/main.yml#L51-L107) · [Источник: ansible/runtime/roles/runtime_agent/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/ansible/runtime/roles/runtime_agent/tasks/main.yml#L124-L147)

## Проверка изменений

Mock-тест `pulumi/index.test.ts` проверяет runtime ingress и секретные выходы. В CI для runtime предусмотрены ansible-lint и syntax-check с fixture-переменными. Это проверки декларации; они не доказывают сетевую изоляцию реальной VPS. Связанный путь доставки runtime playbook — [выкатка API](deployment.md).

[Источник: pulumi/index.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/index.test.ts#L177-L188) · [Источник: pulumi/index.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/pulumi/index.test.ts#L229-L238) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/430053a157dcd6d9660f8da1285b37482384b256/.github/workflows/infra.yml#L80-L86)

[Карта wiki](../quickstart.md)
