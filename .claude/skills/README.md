# Скиллы Claude Code

Скиллы Pulumi скопированы из [pulumi/agent-skills](https://github.com/pulumi/agent-skills)
(Apache-2.0, текст лицензии — `LICENSE-pulumi-agent-skills`), коммит
`9b794aec9c4169f137285c2763c06064d247dd47`, каталог `pulumi/skills/`. Плагин `pulumi` целиком
не ставим: в нём 15 скиллов, большая часть (миграции с Terraform/CDK/ARM/CloudFormation,
Pulumi ESC, Neo) нам не нужна.

| Скилл | Зачем |
|---|---|
| `pulumi-best-practices` | правила написания Pulumi-программ |
| `pulumi-component` | ComponentResource |
| `pulumi-debug-failed-operation` | разбор упавших `pulumi up`/`preview` |
| `provider-upgrade` | обновление версий провайдеров |

Скопированы только `SKILL.md` и `references/`; `agents/openai.yaml` (Codex) и `use_cases.yaml`
(тесты апстрима) не нужны. Упоминания скиллов, которых здесь нет (`pulumi-esc`, `pulumi-overview`,
`pulumi-automation-api`, `package-usage`), — отсылки «см. также», на работу не влияют.

Обновление: склонировать апстрим, скопировать те же файлы, обновить коммит выше.

Ansible-скилл `ansible-good-practices` подключён плагином (`.claude/settings.json`,
маркетплейс `leogallego/claude-ansible-skills`): в плагине он единственный.
