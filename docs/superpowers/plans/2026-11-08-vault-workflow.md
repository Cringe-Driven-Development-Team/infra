# Vault Workflow Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Способ исполнения выбирает пользователь.

**Goal:** Закрепить существующий порядок работы с Vault в корневых инструкциях infra.

**Architecture:** Краткие разделы в README.md для людей и AGENTS.md для агентов. Подробная инструкция и команды остаются в ansible/README.md.

**Tech Stack:** Markdown, Git.

**Spec:** [Согласованная спецификация](../specs/2026-11-08-vault-workflow-design.md).

## Global Constraints

- Изменить только корневые README.md и AGENTS.md; подробная инструкция остаётся в ansible/README.md.
- Выдачу доступа, пароль, шифротекст, playbook'и и CI не менять. Не требовать пароль у всей команды.
- Реальные секреты не читать, playbook'и не запускать; для правки документации тесты приложения не нужны.

## Review Focus

- Область действия: пароль нужен для правки секретов и локальных инфраструктурных запусков, не всем участникам команды.
- Нестандартный путь: заданный ANSIBLE_VAULT_PASSWORD_FILE имеет приоритет перед ~/.config/cdd-vault-pass.
- Нет пароля: указать личное получение у Дениса или менторов, не просить секрет в чате.
- Правка Vault: только edit и коммит шифротекста; пароль и значения секретов не попадают в ответы и логи.
- Ссылки: оба раздела ведут на существующий заголовок ansible/README.md, команды не дублируются.

### Task 1: Дополнить корневые инструкции

**Files:** Modify: `README.md`, `AGENTS.md`. Read: `ansible/README.md:63`.

**Interfaces:** Используется существующая инструкция; новых программных интерфейсов нет. Ссылка из обоих файлов: `ansible/README.md#секреты-ansible-vault`.

- [ ] **Step 1:** Прочитать актуальные README.md, AGENTS.md и раздел секретов ansible/README.md; сохранить сторонние изменения. Сверить со спецификацией.
- [ ] **Step 2:** В README.md добавить короткий раздел «Ansible Vault»: кому нужен пароль, личное получение у Дениса или менторов, файл ~/.config/cdd-vault-pass вне репозитория с правами 600, ANSIBLE_VAULT_PASSWORD_FILE, edit и коммит шифротекста; добавить ссылку на подробную инструкцию.
- [ ] **Step 3:** В AGENTS.md добавить раздел «Ansible Vault»: проверить доступность файла по заданному ANSIBLE_VAULT_PASSWORD_FILE, а при незаданной переменной — по стандартному пути и использовать его через эту переменную. При отсутствии пароля сообщить пользователю порядок получения без запроса секрета в чат. Только edit, без decrypt → правка → encrypt; не выводить секреты и пароль, коммитить шифротекст. Добавить ссылку.
- [ ] **Step 4:** Просмотреть `git diff -- README.md AGENTS.md`, вручную проверить все пять пунктов Review Focus и соответствие спецификации. Выполнить `rg -n '^## Секреты \(ansible-vault\)$' ansible/README.md` (один существующий заголовок) и `git diff --check` (код 0, без вывода). Отдельные тесты для текста не добавлять.
- [ ] **Step 5:** Закоммитить только два изменённых файла и отметки выполнения этого плана: `git add README.md AGENTS.md docs/superpowers/plans/2026-11-08-vault-workflow.md`, затем `git diff --cached --check` и `git commit -m 'docs: закрепить порядок работы с Ansible Vault'`. Пройти журнал и ревью выбранного скилла исполнения; публикацию и слияние этот план не включает.
