# Деплой сервиса вебхуков через Coolify

Для автора сервиса уведомлений GitHub/Apidog → Telegram
([.github#20](https://github.com/Cringe-Driven-Development-Team/.github/issues/20)). Площадка —
отдельная VPS с self-hosted Coolify; как она устроена и обслуживается — `RUNBOOK.md`, «VPS с Coolify».

- Панель: `https://coolify.cellestial.ru` — учётку выдаёт администратор (приглашение в Team, роль Member).
- Адрес сервиса: `https://hooks.cellestial.ru` — его указывают в настройках вебхуков GitHub и Apidog.

## Первый деплой

1. Projects → проект сервиса → Add Resource:
   - из репозитория — Public/Private Repository (GitHub App или deploy key Coolify), сборка Nixpacks или
     `Dockerfile` из репозитория;
   - из образа — Docker Image с конкретным тегом (не `latest`).
2. General → Domains: `https://hooks.cellestial.ru`. Порт приложения — в Ports Exposes (наружу он не
   публикуется: снаружи открыты только 80/443 через proxy Coolify).
3. Health Check: путь `/health`, ответ `200`. Пока проверка не проходит, Coolify не переключает трафик.
4. Environment Variables — секреты, с отметкой secret (в логи сборки они не выводятся):
   GitHub webhook secret, секрет приёма Apidog, токен Telegram-бота, chat ID и topic ID.
   В репозиторий и в CI их не класть.
5. Storages → Add → Volume Mount: каталог очереди и дедупликации (например `/app/data`). Данные тома
   переживают redeploy и пересоздание контейнера и попадают в ночной бэкап. SQLite — в режиме WAL.
6. Deploy. Проверка: `curl -s https://hooks.cellestial.ru/health`.

## Обновление и откат

- Новый коммит в ветке — Deploy (или автодеплой по webhook Coolify из настроек ресурса).
- Откат — Deployments → предыдущий удачный деплой → Redeploy; для образа — прежний тег.
- Логи — Logs ресурса; что пришло от GitHub — Recent Deliveries в настройках вебхука (там же Redeliver).
