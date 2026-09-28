# Образ бекенда: как публиковать для стенда

Документ — контракт между репозиторием бекенда и `infra`. Бекенд собирает и публикует Docker-образ,
инфраструктура его только запускает: код бекенда в `infra` не попадает, туда попадает **тег образа**.

> Статус: стенд (Pulumi + Ansible) готов, роль запуска приложения (`ansible/roles/app`,
> `deploy.yml`) ещё не написана — она пишется под этот контракт. Отклонения от него обсуждаем до
> первого деплоя, а не после.

## Где будет работать образ

```
интернет ──443──▶ VPS 1 gateway (Caddy, TLS Let's Encrypt)
                        │ http, приватная сеть 192.168.199.0/24
                        ▼
                  VPS 2 backend (Docker) ──▶ S3 Selectel, внешние API (исходящий трафик есть)
```

- Образ запускается на **VPS 2**: Ubuntu 24.04, **x86_64**, Docker Engine + Compose plugin.
- Публичного адреса у VPS 2 нет. Снаружи к бекенду ходит только Caddy с gateway по HTTP.
- TLS, домен, сертификаты — забота Caddy. Бекенд слушает **обычный HTTP**.
- Исходящий интернет у VPS 2 есть (через роутер проекта): registry, S3 и внешние API доступны.

## Требования к образу

### Сборка

1. **Платформа `linux/amd64`.** На Mac с Apple Silicon `docker build` по умолчанию собирает
   `arm64` — на VPS такой образ не запустится (`exec format error`). Собирать с
   `--platform linux/amd64` или в CI (там amd64 по умолчанию).
2. **Multi-stage Dockerfile**: сборка — в builder-стадии, в финальный образ идут только
   артефакт и рантайм. Без компиляторов, исходников, `node_modules` для dev и т.п.
3. **Никаких секретов в образе.** Ни `.env`, ни ключей, ни токенов — ни в `COPY`, ни в `ARG`/`ENV`
   (они остаются в слоях и видны через `docker history`). Обязателен `.dockerignore` минимум с
   `.env*`, `.git`, локальными артефактами.
4. **Не root.** В финальной стадии `USER <непривилегированный>` (например `USER 10001`).
5. OCI-метки — `docker/metadata-action` в CI проставит их сам; главное —
   `org.opencontainers.image.source` = URL репозитория бекенда (GHCR привяжет пакет к репо) и
   `org.opencontainers.image.revision` = git sha.

### Рантайм

| Что | Требование |
|---|---|
| Порт | HTTP на `0.0.0.0:8080` внутри контейнера. Порт можно переопределить `PORT`, по умолчанию 8080 |
| Health | `GET /health` → `200`, тело `ok`, ответ < 1 с, **без** проверки БД и внешних сервисов (это liveness: «процесс жив и принимает HTTP»). Ею пользуются Caddy, compose-healthcheck и `verify.yml` |
| Конфиг | Только переменные окружения. Список переменных — в `.env.example` репозитория бекенда (имена + описание, без значений) |
| Логи | В stdout/stderr, по строке на событие (лучше JSON). В файлы внутри контейнера не писать |
| Состояние | Контейнер stateless: пересоздаётся при каждом деплое. Файлы пользователей — в S3, данные — в БД |
| Остановка | На `SIGTERM` дорабатывает текущие запросы и выходит за ≤ 10 с (у compose по умолчанию 10 с до `SIGKILL`). Процесс приложения — PID 1 или под `tini`/`--init`, иначе сигнал не дойдёт |
| За прокси | Запросы приходят от Caddy: реальный IP клиента — в `X-Forwarded-For`, схема — `X-Forwarded-Proto: https`. Доверять этим заголовкам только от приватной сети `192.168.199.0/24` |
| Миграции | Отдельной командой того же образа (например `<image> migrate`), не при старте приложения: деплой запускает её один раз перед переключением версии. Миграции только совместимые назад — старая версия должна пережить новую схему, пока идёт переключение |

### S3

Бакет и ключ выдаёт Pulumi (проект продукта, пул `ru-7`). Бекенд получает их через окружение:

| Переменная | Значение |
|---|---|
| `S3_ENDPOINT` | `https://s3.ru-7.storage.selcloud.ru` |
| `S3_REGION` | `ru-7` (регион подписи = пул) |
| `S3_BUCKET` | `pulumi stack output s3Bucket` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `pulumi stack output s3AccessKey` / `pulumi stack output s3SecretKey --show-secrets` |

Клиент S3 — любой AWS SDK с **path-style** адресацией (`forcePathStyle: true` / `use_path_style`),
virtual-host стиль Selectel для этого бакета не нужен. Значения в образ не вшиваются — их подставит
деплой.

## Registry и теги

**Registry — GitHub Container Registry** организации:
`ghcr.io/cringe-driven-development-team/<имя-репо-бекенда>` (только строчные буквы).
Публикует CI бекенда встроенным `GITHUB_TOKEN`, отдельных секретов не нужно.

| Тег | Когда | Для чего |
|---|---|---|
| `sha-<7 символов sha>` | каждый push в `main` | **деплоится именно он**: неизменяемый, однозначно указывает на коммит |
| `vX.Y.Z` | git-тег `vX.Y.Z` | релизы, человекочитаемая история |
| `main` | каждый push в `main` | для глаз и локальных запусков; **на стенд не деплоится** |

`latest` не используем: плавающий тег не даёт понять, что запущено, и не даёт откатиться.
Однажды опубликованный `sha-…`/`vX.Y.Z` не перезаписывается.

## CI: GitHub Actions

`.github/workflows/image.yml` в репозитории бекенда:

```yaml
name: image
on:
  push:
    branches: [main]
    tags: ["v*.*.*"]
  pull_request:

permissions:
  contents: read
  packages: write          # push в ghcr.io встроенным GITHUB_TOKEN

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        if: github.event_name != 'pull_request'
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}      # metadata-action приводит к нижнему регистру
          tags: |
            type=sha,prefix=sha-
            type=semver,pattern=v{{version}}
            type=ref,event=branch

      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64
          push: ${{ github.event_name != 'pull_request' }}   # в PR только проверяем, что собирается
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

В PR образ собирается, но не публикуется. Тесты бекенда — отдельным job до сборки, по вкусу
команды.

### Доступ стенда к образу

- **Пакет публичный** (Package settings → Change visibility → Public) — VPS тянет образ без
  логина. Годится, если в образе нет ничего закрытого (секретов в нём и так быть не должно).
- **Пакет приватный** — нужен токен только на чтение: classic PAT с одним scope
  `read:packages` от сервисного/ботового аккаунта (fine-grained PAT с GHCR не работают). Токен
  передаётся инфраструктуре, в `infra` он хранится зашифрованным (Ansible Vault), в git открыто
  не лежит. Не забудьте дать этому аккаунту доступ к пакету (Package settings → Manage access).

Выбор — за командой бекенда; скажите инфре, какой вариант.

## Проверка перед публикацией (локально)

```sh
docker buildx build --platform linux/amd64 -t backend:local --load .
docker run --rm -p 8080:8080 --env-file .env.local backend:local
curl -fsS localhost:8080/health          # → ok
docker history --no-trunc backend:local | grep -iE 'secret|password|token|key' # должно быть пусто
docker image inspect backend:local --format '{{.Config.User}}'                  # не пусто и не root
```

И остановка: `docker stop` должен завершить контейнер заметно быстрее 10 с (иначе SIGTERM не
обрабатывается).

## Что передать инфраструктуре

Один раз (PR в `infra` или issue):

1. Имя образа в GHCR и какой вариант доступа (публичный / приватный + токен).
2. `.env.example` — все переменные окружения с описанием: какие обязательные, какие секретные.
3. Нужна ли БД/кэш/очередь и какие (от этого зависит, что поднимает Pulumi/Ansible).
4. Команда миграций, если есть.
5. Примерные ресурсы: память под нагрузкой, нужен ли диск (обычно нет — всё в S3/БД).

На каждый релиз — только тег: `sha-abc1234` (или `vX.Y.Z`).

## Как версия попадает на стенд

Когда будет роль `app` (под этот контракт):

- В `ansible/inventory/group_vars/backend/vars.yml` — `app_image` и `app_image_tag`.
- Деплой: `ansible-playbook deploy.yml -e app_image_tag=sha-abc1234` (или PR, меняющий
  `app_image_tag`). Плейбук: `docker compose pull` → миграции → `docker compose up -d` → ждёт
  `/health`. Порт публикуется только на приватном IP VPS 2 (`192.168.199.x:8080`), Caddy на gateway
  проксирует на него.
- Откат — тот же деплой с предыдущим тегом. Поэтому теги неизменяемые, а миграции совместимы назад.
- Автодеплой из CI бекенда (job после публикации запускает `deploy.yml` с ключом CI через gateway)
  — следующий шаг, после того как ручной деплой обкатан.

## Чеклист бекенда

- [ ] `linux/amd64`, multi-stage, не root, `.dockerignore`
- [ ] HTTP на 8080 (`PORT`), `GET /health` → 200 `ok` без зависимостей
- [ ] Весь конфиг из env, `.env.example` в репо, секретов в образе нет
- [ ] Логи в stdout, SIGTERM → выход ≤ 10 с
- [ ] S3 через env, path-style
- [ ] Миграции отдельной командой, совместимые назад
- [ ] CI публикует `ghcr.io/cringe-driven-development-team/<repo>:sha-<sha>` на push в `main`
- [ ] Инфре переданы: имя образа, доступ, `.env.example`, зависимости, команда миграций
