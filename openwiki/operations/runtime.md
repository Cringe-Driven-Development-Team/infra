---
type: operations
title: Provisioning runtime-сессий
description: Граница Pulumi и backend, повторное назначение VPS, сеть kernel и доставка агента.
tags: [infra, operations]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-31T14:36:53.458Z
sources:
  - id: openwiki-source-8014719a1e041417a7bc1faa
    resource: repo://.github/workflows/infra.yml
  - id: openwiki-source-c4032314cbee8301326ef846
    resource: repo://ansible/runtime/roles/runtime_agent/tasks/assignment.yml
  - id: openwiki-source-5ddab4963a554a77ce7dfc6e
    resource: repo://ansible/runtime/roles/runtime_agent/tasks/main.yml
  - id: openwiki-source-f0db81fc48e9da563a28cb0c
    resource: repo://ansible/runtime/roles/runtime_host/tasks/main.yml
  - id: openwiki-source-09b6a79b98dd78b2f6c41588
    resource: repo://ansible/runtime/roles/runtime_host/templates/firewall.nft.j2
  - id: openwiki-source-02cfa1db9bb098ddcab33853
    resource: repo://ansible/runtime/roles/runtime_host/templates/nginx-agent.conf.j2
  - id: openwiki-source-c02efc11f5b3248133a61053
    resource: repo://ansible/runtime/site.yml
  - id: openwiki-source-0c381c0fc9e669d92423b26f
    resource: repo://pulumi/index.test.ts
  - id: openwiki-source-4a6d25d7743b479ecae59713
    resource: repo://pulumi/index.ts
generated: { by: "codex", at: "2026-10-31T14:36:53.458Z" }
---

# Provisioning runtime-сессий

Описание декларативного кода на SHA `1e7bc4e5946c69065c04393efb015eb7c9b5101f`; фактическое состояние Selectel и выкатку проверяют отдельно.

## Что принадлежит Pulumi

Ветка `infra:runtime` требует включённый backend. Она объявляет отдельного сервисного пользователя проекта, security group, SSH keypair, CA, клиентский сертификат и ключ шифрования. Ingress SSH и TLS агента ограничен backend. Согласно границе в программе, сами VPS сессий создаёт и удаляет Go API; эти динамические экземпляры не объявлены ресурсами данной ветки.

[Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L866-L921) · [Источник: pulumi/index.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.ts#L923-L947)

## Контракт операции

`ansible/runtime/site.yml` получает назначение от backend, проверяет UUID сессии, язык, формы сетевых параметров, пути артефактов и checksum wheel; секреты проверяются с `no_log`. Затем ожидаются SSH/Python и cloud-init; коды cloud-init 0 и 2 допускаются. После проверки прежнего назначения выполняются runtime_host и runtime_agent.

[Источник: ansible/runtime/site.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/runtime/site.yml#L13-L63)

## Повтор не перезапускает сессию

`assignment.yml` сравнивает checksum существующего agent env с новым шаблоном. Чужое назначение отвергается. Если агент не активен, но уже имеет файлы состояния, выполнение падает и требует новую VPS; активный агент после проверок завершает play для хоста без повторной настройки.

[Источник: ansible/runtime/roles/runtime_agent/tasks/assignment.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/runtime/roles/runtime_agent/tasks/assignment.yml#L7-L52)

## Сеть kernel и TLS

Runtime_host загружает и проверяет собственную таблицу nftables до запуска Docker, затем сверяет форму bridge-сети. Шаблон firewall разрешает DNS только к `kernel_dns`, фильтрует запрещённые назначения до разрешения HTTP/HTTPS и запрещает прочий новый исходящий трафик kernel. Nginx слушает `private_ip`, требует клиентский сертификат CA и проверяет CN backend, затем проксирует к локальному агенту. Конкретные адреса и CIDR здесь не приводятся.

[Источник: ansible/runtime/roles/runtime_host/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/runtime/roles/runtime_host/tasks/main.yml#L58-L102) · [Источник: ansible/runtime/roles/runtime_host/templates/firewall.nft.j2](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/runtime/roles/runtime_host/templates/firewall.nft.j2#L33-L50) · [Источник: ansible/runtime/roles/runtime_host/templates/nginx-agent.conf.j2](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/runtime/roles/runtime_host/templates/nginx-agent.conf.j2#L8-L34)

## Проверенный артефакт агента

Runtime_agent проверяет SHA-256 скопированного wheel до установки. Зависимости ставятся из отдельного requirements, wheel — с `--no-deps`. Назначение пишется с правами 0600 и `no_log`, сервис получает зависимость от firewall и запускается через `started`, а не принудительный restart.

[Источник: ansible/runtime/roles/runtime_agent/tasks/main.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/ansible/runtime/roles/runtime_agent/tasks/main.yml#L24-L104)

## Проверка изменений

Mock-тест `pulumi/index.test.ts` проверяет runtime ingress и секретные выходы. В CI для runtime предусмотрены ansible-lint и syntax-check с fixture-переменными. Это проверки декларации; они не доказывают сетевую изоляцию реальной VPS. Связанный путь доставки runtime playbook — [выкатка API](deployment.md).

[Источник: pulumi/index.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.test.ts#L119-L130) · [Источник: pulumi/index.test.ts](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/pulumi/index.test.ts#L171-L180) · [Источник: .github/workflows/infra.yml](https://github.com/Cringe-Driven-Development-Team/infra/blob/1e7bc4e5946c69065c04393efb015eb7c9b5101f/.github/workflows/infra.yml#L69-L75)

[Карта wiki](../quickstart.md)
