# Техдолг

Некритичные доработки, отложенные при ревью. Пункт закрыт — удалить его отсюда в том же PR,
где он исправлен. Ссылки на треды — для контекста.

## Ansible

- **`sshd -T` проверяется только на gateway.** `ansible/verify.yml:147-163` стоит в play `hosts: gateway`,
  hardening на VPS 2 не проверяется. Вынести проверку в play `hosts: all`.
  ([тред](https://github.com/Cringe-Driven-Development-Team/infra/pull/4#discussion_r4111233585))
- **ansible-lint `command-instead-of-module`** в `ansible/roles/ssh_hardening/handlers/main.yml:6`
  (`systemctl is-active ssh.socket`). Вариант: `ansible.builtin.systemd_service` без `state` + `register`,
  условие по `status.ActiveState == 'active'`; или `# noqa: command-instead-of-module` с причиной.
  ([тред](https://github.com/Cringe-Driven-Development-Team/infra/pull/4#discussion_r4111233589))
- **ansible-lint `name[casing]`** в `ansible/verify.yml:88`: имя задачи «ufw: …» со строчной.
  ([тред](https://github.com/Cringe-Driven-Development-Team/infra/pull/4#discussion_r4111233596))
- **`verify.yml --limit gateway` падает:** проба портов с VPS 1 берёт `backend_listen` с VPS 2.
  Запускать `verify.yml` целиком или сделать пробу условной.
  ([тред](https://github.com/Cringe-Driven-Development-Team/infra/pull/4#discussion_r4111233596))
- **Проба портов «закрыто всё, кроме 22» станет allowlist'ом**, когда Caddy на gateway начнёт
  проксировать на приложение на VPS 2: его порт будет легитимно открыт из приватной сети.
  ([тред](https://github.com/Cringe-Driven-Development-Team/infra/pull/4#discussion_r4111233596))
- **Формулировка «новый или пересозданный хост»** в `ansible/README.md:39` и `RUNBOOK.md:107`.
  Пересоздание сервера Pulumi'ем (`deleteBeforeReplace`) сохраняет boot-диск: `deploy` и hardening на нём
  уже есть, `bootstrap.yml` не нужен (и не пройдёт), хватает `site.yml`. `bootstrap.yml --limit` — только для
  хоста с новым диском (после `destroy` или замены volume).
  ([тред](https://github.com/Cringe-Driven-Development-Team/infra/pull/4#discussion_r4111233599))
- **Inventory без `clouds.yaml` — через `OS_*` из `pulumi/bootstrap/env.sh`.** Сейчас к `clouds.yaml`
  привязаны `ansible/inventory/openstack.yml` и `module_defaults … cloud: selectel` в `ansible/verify.yml:8-10`.
  ([комментарий](https://github.com/Cringe-Driven-Development-Team/infra/pull/4#issuecomment-5860248852))

## Pulumi

- **`SELECTEL_PROJECT` в `selectel.env` и прод-стек.** `env.sh` тогда выставляет `OS_PROJECT_NAME`, а провайдер
  OpenStack читает его как `tenant_name` при явном `tenantId` (`pulumi/index.ts:110`) — gophercloud такую пару
  отвергает. Проверить `pulumi preview` с `SELECTEL_PROJECT` или предупредить в README.
  ([комментарий](https://github.com/Cringe-Driven-Development-Team/infra/pull/4#issuecomment-5860248852))
- **Ожидание S3-ключа при отсутствии `curl` или сети** — до таймаута (`waitForS3Key` в
  `pulumi/bootstrap/selectel-s3.ts`, общий для обоих стеков). Сетевую ошибку можно отличать от
  `InvalidAccessKeyId` и падать сразу.
