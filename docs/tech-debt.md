# Техдолг

Некритичные доработки, отложенные при ревью. Пункт закрыт — удалить его отсюда в том же PR,
где он исправлен. Ссылки на треды — для контекста.

## Ansible

- **ansible-lint `name[template]` в `ansible/verify.yml:101` и `:145`** — Jinja в середине имени задачи
  («UFW: разрешено {{ item.port }}/{{ item.proto }} только из {{ item.src }}», «С VPS 1 до VPS 2 открыт
  {{ item }}/tcp»). Шаблон — только в конце, например «UFW: разрешено только из src — {{ item.port }}/{{ item.proto }}
  {{ item.src }}» и «С VPS 1 до VPS 2 открыт порт — {{ item }}».
- **Правило `firewall_rules` без `src` ломает проверку ufw на VPS 2** (`ansible/verify.yml:98`, `:105`). Роль
  firewall такое правило допускает (`src | default(omit)`), а проверка делает `item.src | regex_escape` — undefined;
  ufw при `IPV6=yes` добавит строку `(v6)`, и не сойдётся число правил. Требовать `src` у правил VPS 2 (assert)
  или сравнивать только v4-строки с `Anywhere` вместо `src`.
- **`verify.yml --limit gateway` молча сужает пробу портов** до типовых (`ansible/verify.yml:141`): play VPS 2 не
  идёт, `backend_listen` нет. Вывести `ansible.builtin.debug`/`warn` в этом случае, чтобы было видно в выводе, а не
  только в README.
- **`ansible/env.sh:17`: `pulumi config get pool --stack prod`** читает локальный `pulumi/Pulumi.prod.yaml`, который до
  переезда стейта есть только у владельца стека. У остальных `env.sh` падает с подсказкой «pulumi login в main/?»,
  которая уводит не туда. Брать пул из `stack output` (добавить выход `pool` в `pulumi/index.ts`) — тогда нужен
  только login, как для `projectId`; или хотя бы уточнить сообщение.

## Pulumi / Selectel

- **Флейвор `SL1.2-8192` для backend в ru-9 не подтверждён** (`RUNBOOK.md:69`). Один раз:
  `source pulumi/bootstrap/env.sh && ./pulumi/scripts/list-flavors.sh` и сверить вывод. Смена флейвора у сервера —
  resize на месте, так что это проверка, а не риск.
- **Проверка `OS_PROJECT_NAME` в `pulumi/index.ts:18-24` строже, чем нужно:** при заданных и `ProjectID`, и
  `ProjectName` gophercloud берёт `ProjectID`. Ошибка понятная и безвредная — можно оставить или ослабить до
  предупреждения.
