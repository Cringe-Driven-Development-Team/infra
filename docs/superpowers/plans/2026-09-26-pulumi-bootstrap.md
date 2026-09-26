# Bootstrap-стек для бакета стейта Pulumi — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pulumi-проект `pulumi/bootstrap/` (стек `main`) создаёт в проекте Selectel `infra-state` сервисного пользователя со S3-ключом и версионируемый бакет стейта; стейт самого стека после первого `up` переезжает в этот бакет.

**Architecture:** `index.ts` описывает ресурсы (импорт проекта, пользователь, ключ, бакет, версионирование) и выходы. Сетевые шаги, которые Pulumi не умеет (инициализация S3 в проекте, ожидание готовности ключа), — в `selectel-s3.ts` с подменяемой HTTP-функцией, выполняются только при реальном `up`. `env.sh` переводит `~/.config/selectel.env` в переменные `OS_*` провайдера Selectel.

**Tech Stack:** Pulumi (TypeScript, runtime nodejs, пакетный менеджер bun), провайдер `selectel` 8.3.1 через terraform-bridge, `@pulumi/aws` 7.x (S3 Selectel), `@pulumi/random`, `bun test`, bash/POSIX sh, curl.

**Spec:** `docs/superpowers/specs/2026-09-26-pulumi-bootstrap-design.md`

## Global Constraints

- Ветка `task-infra-5-bootstrap` репо `/mnt/f/Github/2026_H2/infra`; весь код — в `pulumi/bootstrap/`.
- Инструменты вне PATH агента: каждую команду начинать с `export PATH="$HOME/.pulumi/bin:$HOME/.bun/bin:$HOME/.local/node/bin:$HOME/.local/bin:$PATH"`.
- Проект Pulumi `infra-bootstrap`, стек `main`; ключи конфига `infra-bootstrap:projectId`, `infra-bootstrap:s3Pool`, `infra-bootstrap:bucketName`, `infra-bootstrap:s3KeyReadyTimeoutSeconds` (по умолчанию 600).
- Значения: `projectId` = `800b74820d5440a3a00b6b961eccabf7`; `s3Pool` = `ru-7`; `bucketName` = `cdd-infra-state`.
- Провайдер `selectel` 8.3.1 (`source: terraform-provider`, параметры `selectel/selectel`, `8.3.1`); `@pulumi/aws` `^7.48.0`, `@pulumi/pulumi` `^3.263.0`, `@pulumi/random` `^4.21.2`.
- Логические имена ресурсов: `infra-state` (проект), `state-user-password`, `state-user`, `state-s3`, `selectel-s3` (aws-провайдер), `state-bucket`, `state-versioning`. Не менять после первого `up`.
- Пользователь стейта `infra-state-s3`: ровно одна роль `member`, scope `project`, на проект `infra-state`.
- Проект и бакет — `protect: true`; `forceDestroy` у бакета не задаётся.
- Секреты (пароли, токены, secretKey) никогда не попадают в аргументы командной строки, логи и тексты ошибок — только stdin (`curl -K -`).
- Инициализация S3: успех — любой 2xx (проверено: повторный вызов на инициализированном проекте отвечает 200); иначе — ошибка с кодом и первыми 300 символами тела.
- `pulumi up`, перенос стейта и любые действия, создающие/меняющие ресурсы Selectel, — только после явного «да» пользователя на показанный `preview`.
- Коммиты заканчиваются строкой `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; не коммитить `.github/workflows/automation.yml` и `w~` (чужие изменения рабочей копии).

## Review Focus

- Пароль Selectel со спецсимволами (`;`, `$`, `&`, `"`, пробел) в `~/.config/selectel.env` — `env.sh` должен передать его провайдеру без искажений. Тест: `test_special_chars_preserved` (Task 1).
- `source env.sh` без файла `selectel.env` — понятная ошибка, и интерактивная оболочка пользователя не закрывается. Тест: `test_missing_file_keeps_shell` (Task 1).
- `pulumi up` без загруженного `env.sh` — ошибка, называющая `env.sh`, а не невнятный 401 от Keystone. Тест: `credentialsFromEnv` «перечисляет недостающие переменные» (Task 2).
- `s3KeyReadyTimeoutSeconds` меньше интервала опроса (или 0) — хотя бы одна попытка, затем ошибка с последним ответом. Тест: «таймаут 0 — одна попытка» (Task 2).
- Повторный `up`/`preview` после импорта проекта и после переноса стейта — «без изменений», без попытки заново импортировать или пересоздать ресурсы. Проверка: шаги Task 5.

---

### Task 1: Каркас проекта и env.sh

**Files:**
- Modify: `docs/superpowers/specs/2026-09-26-pulumi-bootstrap-design.md`
- Create: `pulumi/bootstrap/Pulumi.yaml`
- Create: `pulumi/bootstrap/package.json`
- Create: `pulumi/bootstrap/tsconfig.json`
- Create: `pulumi/bootstrap/.gitignore`
- Create: `pulumi/bootstrap/env.sh`
- Test: `pulumi/bootstrap/env_test.sh`

**Interfaces:**
- Produces: `source pulumi/bootstrap/env.sh` выставляет `OS_USERNAME`, `OS_PASSWORD`, `OS_DOMAIN_NAME`, `OS_AUTH_URL=https://cloud.api.selcloud.ru/identity/v3/`, `OS_REGION_NAME=ru-7`, `PULUMI_CONFIG_PASSPHRASE`; путь к файлу переопределяется `SELECTEL_ENV`. Сгенерированный SDK `@pulumi/selectel` в `pulumi/bootstrap/sdks/selectel`.

- [ ] **Step 1: Обновить спеку по проверенному факту**

В спеке:
- строку `Статус: на утверждении.` заменить на `Статус: утверждено.`
- в разделе «Ошибки» пункт `initProjectS3` заменить на:
  `- `initProjectS3`: успех — любой 2xx (повторный вызов на уже инициализированном проекте отвечает 200 — проверено 2026-09-26 на pulumi-cellestial); любой другой ответ — ошибка с кодом и первыми 300 символами тела.`
- в разделе «Проверка» заменить `201, 204, 400 «уже», 400 другое, 500, сетевая ошибка` на `200, 201, 204, 400, 403, 500, сетевая ошибка`.

- [ ] **Step 2: Написать тест `pulumi/bootstrap/env_test.sh`**

```bash
#!/usr/bin/env bash
# Тесты env.sh: разбор ~/.config/selectel.env и переменные OS_* для провайдера Selectel.
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
FAILS=0
fail() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }

cat > "$TMP/ok.env" <<'EOF'
SELECTEL_USERNAME=pulumi-test
SELECTEL_PASSWORD=p w;$HOME&|"x'
SELECTEL_DOMAIN_NAME=631994
PULUMI_CONFIG_PASSPHRASE=pass phrase
EOF

# $1 — оболочка, $2 — env-файл; печатает значения через разделитель \x1f
show() {
  SELECTEL_ENV=$2 "$1" -c '. "$0" && printf "%s\037%s\037%s\037%s\037%s\037%s" \
    "$OS_USERNAME" "$OS_PASSWORD" "$OS_DOMAIN_NAME" "$OS_AUTH_URL" "$OS_REGION_NAME" "$PULUMI_CONFIG_PASSPHRASE"' \
    "$HERE/env.sh"
}

expected=$(printf '%s\037%s\037%s\037%s\037%s\037%s' pulumi-test 'p w;$HOME&|"x'"'" 631994 \
  https://cloud.api.selcloud.ru/identity/v3/ ru-7 'pass phrase')

test_special_chars_preserved() {
  [ "$(show bash "$TMP/ok.env" 2>/dev/null)" = "$expected" ] || fail "bash: значения искажены"
}

test_posix_sh() {
  [ "$(show sh "$TMP/ok.env" 2>/dev/null)" = "$expected" ] || fail "sh: значения искажены"
}

test_missing_file_keeps_shell() {
  local out
  out=$(SELECTEL_ENV=$TMP/nope.env bash -c '. "$0"; echo "rc=$?"; echo alive' "$HERE/env.sh" 2>&1)
  grep -q 'nope.env' <<<"$out" || fail "в ошибке нет пути к файлу: $out"
  grep -q 'rc=1' <<<"$out" || fail "env.sh должен вернуть 1: $out"
  grep -q 'alive' <<<"$out" || fail "env.sh завершил вызывающую оболочку: $out"
}

test_missing_passphrase_warns() {
  grep -v PULUMI_CONFIG_PASSPHRASE "$TMP/ok.env" > "$TMP/nopass.env"
  local out
  out=$(SELECTEL_ENV=$TMP/nopass.env bash -c '. "$0"; echo "user=$OS_USERNAME"' "$HERE/env.sh" 2>&1)
  grep -q 'PULUMI_CONFIG_PASSPHRASE' <<<"$out" || fail "нет предупреждения о passphrase: $out"
  grep -q 'user=pulumi-test' <<<"$out" || fail "OS_USERNAME не выставлен: $out"
}

test_ignores_comments_and_unknown_keys() {
  { echo '# comment'; echo ''; echo 'OTHER=1'; cat "$TMP/ok.env"; } > "$TMP/extra.env"
  [ "$(show bash "$TMP/extra.env" 2>/dev/null)" = "$expected" ] || fail "комментарии/чужие ключи мешают"
  [ -z "$(SELECTEL_ENV=$TMP/extra.env bash -c '. "$0"; printf %s "${OTHER:-}"' "$HERE/env.sh")" ] ||
    fail "чужой ключ OTHER экспортирован"
}

for t in $(declare -F | awk '$3 ~ /^test_/ {print $3}'); do
  echo "$t"
  "$t"
done
echo
if [ "$FAILS" -gt 0 ]; then echo "провалов: $FAILS"; exit 1; fi
echo "все тесты прошли"
```

- [ ] **Step 3: Запустить — убедиться, что падает**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && chmod +x env_test.sh && bash env_test.sh`
Expected: FAIL во всех тестах (`env.sh` не существует), итог `провалов: N`, exit 1.

- [ ] **Step 4: Написать `pulumi/bootstrap/env.sh`**

```sh
# Учётные данные Selectel для bootstrap-стека. Использование: source pulumi/bootstrap/env.sh
# Файл ~/.config/selectel.env (права 600, путь переопределяется SELECTEL_ENV) содержит строки
# KEY=value: SELECTEL_USERNAME, SELECTEL_PASSWORD, SELECTEL_DOMAIN_NAME, PULUMI_CONFIG_PASSPHRASE.
# Файл разбирается построчно, а не через source: значения со спецсимволами передаются как есть.
# POSIX sh — работает в bash, zsh и dash.
_sel_file=${SELECTEL_ENV:-$HOME/.config/selectel.env}
if [ ! -r "$_sel_file" ]; then
  echo "env.sh: нет файла $_sel_file (см. pulumi/bootstrap/README.md)" >&2
  unset _sel_file
  return 1
fi
while IFS= read -r _sel_line || [ -n "$_sel_line" ]; do
  case "$_sel_line" in
    SELECTEL_USERNAME=* | SELECTEL_PASSWORD=* | SELECTEL_DOMAIN_NAME=* | PULUMI_CONFIG_PASSPHRASE=*)
      export "${_sel_line%%=*}=${_sel_line#*=}" ;;
  esac
done < "$_sel_file"
export OS_USERNAME="${SELECTEL_USERNAME:-}"
export OS_PASSWORD="${SELECTEL_PASSWORD:-}"
export OS_DOMAIN_NAME="${SELECTEL_DOMAIN_NAME:-}"
export OS_AUTH_URL=https://cloud.api.selcloud.ru/identity/v3/
export OS_REGION_NAME=ru-7
if [ -z "${PULUMI_CONFIG_PASSPHRASE:-}" ]; then
  echo "env.sh: в $_sel_file нет PULUMI_CONFIG_PASSPHRASE — pulumi спросит passphrase" >&2
fi
unset _sel_file _sel_line
```

- [ ] **Step 5: Запустить — убедиться, что проходит**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && bash env_test.sh`
Expected: пять имён `test_*` без `FAIL`, `все тесты прошли`, exit 0.

- [ ] **Step 6: Файлы проекта Pulumi**

`pulumi/bootstrap/Pulumi.yaml`:
```yaml
name: infra-bootstrap
runtime:
  name: nodejs
  options:
    typescript: true
    packagemanager: bun
description: Бакет стейта Pulumi в проекте Selectel infra-state (#5)
packages:
  selectel:
    source: terraform-provider
    parameters:
      - selectel/selectel
      - 8.3.1
```

`pulumi/bootstrap/package.json`:
```json
{
  "name": "infra-bootstrap",
  "main": "index.ts",
  "scripts": {
    "test": "bun test && bash env_test.sh"
  },
  "devDependencies": {
    "@types/bun": "^1.2.0",
    "@types/node": "^22",
    "typescript": "^5"
  },
  "dependencies": {
    "@pulumi/aws": "^7.48.0",
    "@pulumi/pulumi": "^3.263.0",
    "@pulumi/random": "^4.21.2",
    "@pulumi/selectel": "file:sdks/selectel"
  },
  "trustedDependencies": ["@pulumi/selectel"]
}
```

`pulumi/bootstrap/tsconfig.json` (тесты вне `include`: их запускает bun, а Pulumi компилирует только программу):
```json
{
  "compilerOptions": {
    "strict": true,
    "target": "ES2020",
    "module": "commonjs",
    "moduleResolution": "node",
    "sourceMap": false,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "skipLibCheck": true,
    "types": ["node"]
  },
  "include": ["index.ts", "selectel-s3.ts"]
}
```

`pulumi/bootstrap/.gitignore`:
```
node_modules/
sdks/
package-lock.json
bootstrap-state-export.json
```

- [ ] **Step 7: Сгенерировать SDK и поставить зависимости**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && pulumi install`
Expected: создан `sdks/selectel/`, `node_modules/@pulumi/{pulumi,aws,random,selectel}`, `bun.lock`; нет `package-lock.json`. Проверка: `ls sdks/selectel/iamServiceuserV1.ts sdks/selectel/iamS3CredentialsV1.ts sdks/selectel/vpcProjectV2.ts`.

- [ ] **Step 8: Commit**

```bash
cd /mnt/f/Github/2026_H2/infra
git add docs/superpowers/specs/2026-09-26-pulumi-bootstrap-design.md pulumi/bootstrap/{Pulumi.yaml,package.json,bun.lock,tsconfig.json,.gitignore,env.sh,env_test.sh}
git commit -m "feat(bootstrap): каркас Pulumi-проекта и env.sh (#5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: selectel-s3.ts — инициализация S3 и ожидание ключа

**Files:**
- Create: `pulumi/bootstrap/selectel-s3.ts`
- Test: `pulumi/bootstrap/selectel-s3.test.ts`

**Interfaces:**
- Produces (для Task 3):
  - `interface HttpResult { status: number; body: string }` — `status: 0` = ответа нет (сеть/TLS/curl), тогда `body` — текст ошибки.
  - `type Http = (args: string[], stdin: string) => Promise<HttpResult>`; `const curl: Http` — реальная реализация.
  - `interface Credentials { authUrl: string; username: string; password: string; domain: string }`
  - `function credentialsFromEnv(env: Record<string, string | undefined>): Credentials` — бросает ошибку со списком недостающих `OS_USERNAME`/`OS_PASSWORD`/`OS_DOMAIN_NAME` и подсказкой `source pulumi/bootstrap/env.sh`; `authUrl` из `OS_AUTH_URL` или `https://cloud.api.selcloud.ru/identity/v3/`.
  - `async function initProjectS3(http: Http, creds: Credentials, projectId: string, pool: string): Promise<number>` — возвращает HTTP-код (2xx) или бросает.
  - `interface WaitOptions { endpoint: string; pool: string; accessKey: string; secretKey: string; timeoutSeconds: number; intervalMs?: number; sleep?: (ms: number) => Promise<void>; now?: () => number }`
  - `async function waitForS3Key(http: Http, o: WaitOptions): Promise<number>` — число попыток до 200 или бросает.

- [ ] **Step 1: Написать тесты `pulumi/bootstrap/selectel-s3.test.ts`**

```ts
import { describe, expect, test } from "bun:test";
import {
  credentialsFromEnv, initProjectS3, waitForS3Key,
  type Credentials, type Http, type HttpResult,
} from "./selectel-s3";

const creds: Credentials = {
  authUrl: "https://auth.example/identity/v3/", username: "u", password: "s3cr3t-pw", domain: "631994",
};
const tokenOk: HttpResult = { status: 201, body: "HTTP/1.1 201 Created\r\nX-Subject-Token: tok-123\r\n\r\n{}" };

function fakeHttp(init: HttpResult, token: HttpResult = tokenOk) {
  const calls: { args: string[]; stdin: string }[] = [];
  const http: Http = async (args, stdin) => {
    calls.push({ args, stdin });
    return args[args.length - 1].endsWith("/auth/tokens") ? token : init;
  };
  return { http, calls };
}

describe("credentialsFromEnv", () => {
  test("берёт OS_* и адрес Keystone по умолчанию", () => {
    expect(credentialsFromEnv({ OS_USERNAME: "u", OS_PASSWORD: "p", OS_DOMAIN_NAME: "1" })).toEqual({
      authUrl: "https://cloud.api.selcloud.ru/identity/v3/", username: "u", password: "p", domain: "1",
    });
  });
  test("перечисляет недостающие переменные и называет env.sh", () => {
    expect(() => credentialsFromEnv({ OS_USERNAME: "u" })).toThrow(/OS_PASSWORD.*OS_DOMAIN_NAME.*env\.sh/s);
  });
});

describe("initProjectS3", () => {
  for (const status of [200, 201, 204]) {
    test(`успех на ${status}`, async () => {
      const { http } = fakeHttp({ status, body: "" });
      expect(await initProjectS3(http, creds, "p1", "ru-7")).toBe(status);
    });
  }
  for (const status of [400, 403, 500]) {
    test(`ошибка на ${status} с кодом и телом`, async () => {
      const { http } = fakeHttp({ status, body: "bad things" });
      await expect(initProjectS3(http, creds, "p1", "ru-7")).rejects.toThrow(new RegExp(`${status}.*bad things`));
    });
  }
  test("ошибка, если сеть не ответила", async () => {
    const { http } = fakeHttp({ status: 0, body: "Could not resolve host" });
    await expect(initProjectS3(http, creds, "p1", "ru-7")).rejects.toThrow(/без ответа.*Could not resolve host/);
  });
  test("ошибка, если Keystone не выдал токен", async () => {
    const { http } = fakeHttp({ status: 200, body: "" }, { status: 401, body: "unauthorized" });
    await expect(initProjectS3(http, creds, "p1", "ru-7")).rejects.toThrow(/токен.*401/);
  });
  test("пароль и токен — только в stdin; адрес init по пулу", async () => {
    const { http, calls } = fakeHttp({ status: 200, body: "" });
    await initProjectS3(http, creds, "p1", "ru-7");
    expect(calls).toHaveLength(2);
    expect(calls[0].args.join(" ")).not.toContain("s3cr3t-pw");
    expect(calls[0].stdin).toContain("s3cr3t-pw");
    expect(calls[0].stdin).toContain('"id":"p1"');
    expect(calls[1].args.join(" ")).not.toContain("tok-123");
    expect(calls[1].stdin).toContain("X-Auth-Token: tok-123");
    expect(calls[1].args[calls[1].args.length - 1]).toBe("https://api.ru-7.storage.selcloud.ru/v2/hello/init");
  });
});

describe("waitForS3Key", () => {
  function clock() {
    let t = 0;
    return { now: () => t, sleep: async (ms: number) => { t += ms; } };
  }
  const base = {
    endpoint: "https://s3.ru-7.storage.selcloud.ru", pool: "ru-7",
    accessKey: "AK", secretKey: "SK-secret", timeoutSeconds: 60, intervalMs: 15000,
  };

  test("успех после отказов и сетевой ошибки", async () => {
    const replies: HttpResult[] = [
      { status: 403, body: "InvalidAccessKeyId" }, { status: 0, body: "timeout" }, { status: 200, body: "<ok/>" },
    ];
    let i = 0;
    const http: Http = async () => replies[i++];
    expect(await waitForS3Key(http, { ...base, ...clock() })).toBe(3);
  });
  test("таймаут: ошибка с последним ответом, попытки ограничены", async () => {
    let calls = 0;
    const http: Http = async () => { calls++; return { status: 403, body: "InvalidAccessKeyId" }; };
    await expect(waitForS3Key(http, { ...base, ...clock() })).rejects.toThrow(/60 с.*403.*InvalidAccessKeyId/s);
    expect(calls).toBe(5);
  });
  test("таймаут 0 — одна попытка", async () => {
    let calls = 0;
    const http: Http = async () => { calls++; return { status: 403, body: "no" }; };
    await expect(waitForS3Key(http, { ...base, timeoutSeconds: 0, ...clock() })).rejects.toThrow();
    expect(calls).toBe(1);
  });
  test("секрет — только в stdin, не в аргументах и не в ошибке", async () => {
    const seen: { args: string[]; stdin: string }[] = [];
    const http: Http = async (args, stdin) => { seen.push({ args, stdin }); return { status: 403, body: "no" }; };
    const err = await waitForS3Key(http, { ...base, timeoutSeconds: 0, ...clock() }).catch((e: Error) => e);
    expect(String(err)).not.toContain("SK-secret");
    expect(seen[0].args.join(" ")).not.toContain("SK-secret");
    expect(seen[0].stdin).toContain("AK:SK-secret");
    expect(seen[0].args).toContain("aws:amz:ru-7:s3");
    expect(seen[0].args[seen[0].args.length - 1]).toBe("https://s3.ru-7.storage.selcloud.ru/");
  });
});
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && bun test selectel-s3.test.ts`
Expected: FAIL — `Cannot find module './selectel-s3'`.

- [ ] **Step 3: Написать `pulumi/bootstrap/selectel-s3.ts`**

```ts
import { execFile } from "child_process";

// Ответ HTTP-запроса. status 0 — ответа нет (сеть, TLS, curl не запустился), body — текст ошибки.
export interface HttpResult {
  status: number;
  body: string;
}
export type Http = (args: string[], stdin: string) => Promise<HttpResult>;

// curl печатает тело и последней строкой HTTP-код. Секреты передаются через stdin (-K - или
// --data-binary @-), чтобы не светиться в списке процессов. curl ходит через системное доверие
// к сертификатам, как и сам Pulumi.
export const curl: Http = (args, stdin) =>
  new Promise((resolve) => {
    const child = execFile(
      "curl",
      ["-sS", "--max-time", "30", "-o", "-", "-w", "\n%{http_code}", ...args],
      { maxBuffer: 10 * 1024 * 1024 },
      (err, stdout, stderr) => {
        const lines = String(stdout ?? "").trimEnd().split("\n");
        const status = Number(lines.pop());
        if (!Number.isFinite(status) || status === 0) {
          resolve({ status: 0, body: String(stderr || err?.message || "нет ответа").trim() });
          return;
        }
        resolve({ status, body: lines.join("\n") });
      },
    );
    child.stdin?.end(stdin);
  });

export interface Credentials {
  authUrl: string;
  username: string;
  password: string;
  domain: string;
}

// Учётные данные сервисного пользователя аккаунта из переменных, которые выставляет env.sh.
export function credentialsFromEnv(env: Record<string, string | undefined>): Credentials {
  const missing = ["OS_USERNAME", "OS_PASSWORD", "OS_DOMAIN_NAME"].filter((k) => !env[k]);
  if (missing.length > 0) {
    throw new Error(`Нет ${missing.join(", ")}: выполните source pulumi/bootstrap/env.sh`);
  }
  return {
    authUrl: env.OS_AUTH_URL || "https://cloud.api.selcloud.ru/identity/v3/",
    username: env.OS_USERNAME!,
    password: env.OS_PASSWORD!,
    domain: env.OS_DOMAIN_NAME!,
  };
}

// Keystone-токен, скоупленный на проект: нужен для инициализации S3 в этом проекте.
async function projectToken(http: Http, c: Credentials, projectId: string): Promise<string> {
  const body = JSON.stringify({
    auth: {
      identity: {
        methods: ["password"],
        password: { user: { name: c.username, domain: { name: c.domain }, password: c.password } },
      },
      scope: { project: { id: projectId } },
    },
  });
  const res = await http(
    ["-D", "-", "-H", "Content-Type: application/json", "--data-binary", "@-", `${c.authUrl.replace(/\/+$/, "")}/auth/tokens`],
    body,
  );
  const token = res.body
    .split(/\r?\n/)
    .find((line) => line.toLowerCase().startsWith("x-subject-token:"))
    ?.slice("x-subject-token:".length)
    .trim();
  if (res.status !== 201 || !token) {
    throw new Error(`Не удалось получить токен проекта ${projectId}: HTTP ${res.status || "без ответа"}`);
  }
  return token;
}

// Инициализация S3 в проекте: пока её нет, S3 не знает проект и отвечает InvalidAccessKeyId на
// любой его ключ. Повторный вызов на инициализированном проекте отвечает 200 — операция идемпотентна.
export async function initProjectS3(http: Http, creds: Credentials, projectId: string, pool: string): Promise<number> {
  const token = await projectToken(http, creds, projectId);
  const url = `https://api.${pool}.storage.selcloud.ru/v2/hello/init`;
  const res = await http(["-X", "POST", "-H", "Accept: application/json", "-K", "-", url], `header = "X-Auth-Token: ${token}"\n`);
  if (res.status >= 200 && res.status < 300) {
    return res.status;
  }
  throw new Error(`Инициализация S3 (${url}) ответила ${res.status || "без ответа"}: ${res.body.slice(0, 300)}`);
}

export interface WaitOptions {
  endpoint: string;
  pool: string;
  accessKey: string;
  secretKey: string;
  timeoutSeconds: number;
  intervalMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

// Выданный через IAM ключ S3 принимает не сразу: до этого ListBuckets отвечает 403
// InvalidAccessKeyId. Опрашиваем, пока не будет 200; любой другой ответ — неудачная попытка.
export async function waitForS3Key(http: Http, o: WaitOptions): Promise<number> {
  const sleep = o.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const now = o.now ?? Date.now;
  const interval = o.intervalMs ?? 15000;
  const deadline = now() + o.timeoutSeconds * 1000;
  let last: HttpResult = { status: 0, body: "" };
  for (let attempt = 1; ; attempt++) {
    last = await http(
      ["--aws-sigv4", `aws:amz:${o.pool}:s3`, "-K", "-", `${o.endpoint}/`],
      `user = "${o.accessKey}:${o.secretKey}"\n`,
    );
    if (last.status === 200) {
      return attempt;
    }
    if (now() + interval > deadline) {
      break;
    }
    await sleep(interval);
  }
  throw new Error(
    `S3 ${o.endpoint} не принял ключ за ${o.timeoutSeconds} с ` +
      `(последний ответ ${last.status || "без ответа"}: ${last.body.slice(0, 300)})`,
  );
}
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && bun test selectel-s3.test.ts`
Expected: все тесты PASS (15), 0 fail.

- [ ] **Step 5: Проверить реальный `curl` без секретов**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && bun -e 'import {curl} from "./selectel-s3"; console.log(await curl(["https://s3.ru-7.storage.selcloud.ru/"], ""), await curl(["https://no-such-host.invalid/"], ""))'`
Expected: первый — `status: 403` (без подписи S3 отказывает) и XML в `body`; второй — `status: 0`, в `body` текст curl про resolve host.

- [ ] **Step 6: Commit**

```bash
cd /mnt/f/Github/2026_H2/infra
git add pulumi/bootstrap/selectel-s3.ts pulumi/bootstrap/selectel-s3.test.ts
git commit -m "feat(bootstrap): инициализация S3 в проекте и ожидание готовности ключа (#5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: index.ts — ресурсы и выходы

**Files:**
- Create: `pulumi/bootstrap/index.ts`
- Test: `pulumi/bootstrap/index.test.ts`

**Interfaces:**
- Consumes: `curl`, `credentialsFromEnv`, `initProjectS3`, `waitForS3Key` из `./selectel-s3` (Task 2); SDK `@pulumi/selectel` (Task 1).
- Produces: выходы стека `stateProjectId`, `stateBucket`, `stateEndpoint`, `stateRegion`, `stateAccessKey`, `stateSecretKey` (secret), `versioningStatus`, `backendUrl`.

- [ ] **Step 1: Написать тест `pulumi/bootstrap/index.test.ts`**

```ts
import { beforeAll, describe, expect, test } from "bun:test";
import * as pulumi from "@pulumi/pulumi";

const created = new Map<string, { type: string; inputs: Record<string, any> }>();

pulumi.runtime.setAllConfig({
  "infra-bootstrap:projectId": "p-123",
  "infra-bootstrap:s3Pool": "ru-7",
  "infra-bootstrap:bucketName": "cdd-infra-state",
});
pulumi.runtime.setMocks(
  {
    newResource(args) {
      created.set(args.name, { type: args.type, inputs: args.inputs });
      const state: Record<string, unknown> = { ...args.inputs };
      if (args.name === "state-s3") {
        state.accessKey = "AK";
        state.secretKey = "SK";
      }
      if (args.name === "state-user-password") {
        state.result = "generated-password";
      }
      return { id: args.id || `${args.name}-id`, state };
    },
    call: (args) => args.inputs,
  },
  "infra-bootstrap",
  "main",
  true, // preview: сетевые шаги (init S3, ожидание ключа) пропускаются
);

type Stack = typeof import("./index");
let stack: Stack;
const value = <T>(o: pulumi.Output<T>) =>
  new Promise<T>((resolve) => o.apply((v) => { resolve(v); return v; }));

beforeAll(async () => {
  stack = await import("./index");
  await Promise.all([value(stack.backendUrl), value(stack.versioningStatus), value(stack.stateProjectId)]);
});

describe("bootstrap-стек", () => {
  test("пользователь стейта: одна роль member на проект infra-state", async () => {
    const user = created.get("state-user")!;
    expect(user.inputs.name).toBe("infra-state-s3");
    expect(user.inputs.roles).toEqual([
      { roleName: "member", scope: "project", projectId: await value(stack.stateProjectId) },
    ]);
  });

  test("S3-ключ выдан этому пользователю на проект infra-state", async () => {
    const s3 = created.get("state-s3")!;
    expect(s3.inputs.userId).toBe("state-user-id");
    expect(s3.inputs.projectId).toBe(await value(stack.stateProjectId));
  });

  test("бакет: имя из конфига, forceDestroy не включён", () => {
    const bucket = created.get("state-bucket")!;
    expect(bucket.inputs.bucket).toBe("cdd-infra-state");
    expect(bucket.inputs.forceDestroy).not.toBe(true);
  });

  test("версионирование бакета включено", async () => {
    expect(created.get("state-versioning")!.inputs.bucket).toBe("cdd-infra-state");
    expect(await value(stack.versioningStatus)).toBe("Enabled");
  });

  test("S3-провайдер смотрит в Selectel ru-7", () => {
    const provider = created.get("selectel-s3")!;
    expect(JSON.stringify(provider.inputs)).toContain("https://s3.ru-7.storage.selcloud.ru");
  });

  test("секретный ключ — secret", async () => {
    expect(await stack.stateSecretKey.isSecret).toBe(true);
  });

  test("backendUrl для основного стека — префикс main/", async () => {
    expect(await value(stack.backendUrl)).toBe(
      "s3://cdd-infra-state/main?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true",
    );
  });
});
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && bun test index.test.ts`
Expected: FAIL — `Cannot find module './index'`.

- [ ] **Step 3: Написать `pulumi/bootstrap/index.ts`**

```ts
import * as aws from "@pulumi/aws";
import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import * as selectel from "@pulumi/selectel";
import { credentialsFromEnv, curl, initProjectS3, waitForS3Key } from "./selectel-s3";

const cfg = new pulumi.Config();
const projectId = cfg.require("projectId");
const pool = cfg.require("s3Pool");
const bucketName = cfg.require("bucketName");
const keyReadyTimeout = cfg.getNumber("s3KeyReadyTimeoutSeconds") ?? 600;
const endpoint = `https://s3.${pool}.storage.selcloud.ru`;

// Проект infra-state создан в панели; при первом up импортируется, дальше управляется стеком.
const project = new selectel.VpcProjectV2("infra-state", { name: "infra-state" }, { import: projectId, protect: true });

const password = new random.RandomPassword("state-user-password", {
  length: 24,
  minUpper: 1,
  minLower: 1,
  minNumeric: 1,
  minSpecial: 1,
  overrideSpecial: "!#$%&*+-.:;<=>?@^_{|}~",
});

// Доступ к стейту: роль member только на проект infra-state, где нет ничего, кроме бакета стейта.
const user = new selectel.IamServiceuserV1("state-user", {
  name: "infra-state-s3",
  password: password.result,
  roles: [{ roleName: "member", scope: "project", projectId: project.id }],
});

const credentials = new selectel.IamS3CredentialsV1("state-s3", {
  name: "pulumi-state",
  userId: user.id,
  projectId: project.id,
});

// Только при реальном up: инициализировать S3 в проекте и дождаться, пока S3 примет новый ключ.
const readyAccessKey = pulumi
  .all([credentials.accessKey, credentials.secretKey, project.id])
  .apply(async ([accessKey, secretKey, id]) => {
    if (pulumi.runtime.isDryRun()) {
      return accessKey;
    }
    await initProjectS3(curl, credentialsFromEnv(process.env), id, pool);
    const attempts = await waitForS3Key(curl, { endpoint, pool, accessKey, secretKey, timeoutSeconds: keyReadyTimeout });
    pulumi.log.info(`S3 принял ключ стейта (попытка ${attempts})`);
    return accessKey;
  });

// S3 Selectel — не AWS: path-style и пропуск проверок, как в документации Selectel для Terraform.
const s3 = new aws.Provider("selectel-s3", {
  region: pool,
  accessKey: readyAccessKey,
  secretKey: credentials.secretKey,
  endpoints: [{ s3: endpoint }],
  s3UsePathStyle: true,
  skipCredentialsValidation: true,
  skipRegionValidation: true,
  skipRequestingAccountId: true,
  skipMetadataApiCheck: true,
});

// Бакет стейта не удаляется случайным destroy: protect и forceDestroy по умолчанию (false).
const bucket = new aws.s3.Bucket("state-bucket", { bucket: bucketName }, { provider: s3, protect: true });

const versioning = new aws.s3.BucketVersioning(
  "state-versioning",
  { bucket: bucket.bucket, versioningConfiguration: { status: "Enabled" } },
  { provider: s3 },
);

export const stateProjectId = project.id;
export const stateBucket = bucket.bucket;
export const stateEndpoint = endpoint;
export const stateRegion = pool;
export const stateAccessKey = credentials.accessKey;
export const stateSecretKey = pulumi.secret(credentials.secretKey);
export const versioningStatus = versioning.versioningConfiguration.status;
// pulumi login для основного стека (префикс main/); стейт bootstrap — под префиксом bootstrap/
export const backendUrl = pulumi.interpolate`s3://${bucket.bucket}/main?region=${pool}&endpoint=s3.${pool}.storage.selcloud.ru&s3ForcePathStyle=true`;
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && bun test`
Expected: все тесты `index.test.ts` (7) и `selectel-s3.test.ts` (15) PASS, 0 fail.

- [ ] **Step 5: Типы**

Run: `cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && bunx tsc --noEmit -p .`
Expected: без ошибок.

- [ ] **Step 6: Commit**

```bash
cd /mnt/f/Github/2026_H2/infra
git add pulumi/bootstrap/index.ts pulumi/bootstrap/index.test.ts
git commit -m "feat(bootstrap): проект infra-state, пользователь и бакет стейта (#5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: README bootstrap

**Files:**
- Create: `pulumi/bootstrap/README.md`

- [ ] **Step 1: Написать README.md**

````markdown
# Bootstrap: бакет стейта Pulumi

Отдельный стек (`infra-bootstrap`, стек `main`) создаёт в проекте Selectel `infra-state` бакет, в
котором хранятся стейты всех Pulumi-стеков инфраструктуры, и сервисного пользователя с доступом
только к этому проекту. Зачем отдельно — бакет стейта нельзя создать в стеке, чей стейт в нём
лежит (задача #5, спека `docs/superpowers/specs/2026-09-26-pulumi-bootstrap-design.md`).

| Префикс в бакете | Стейт |
|---|---|
| `bootstrap/` | этот стек |
| `main/` | основной стек (`pulumi/`) |

## Что нужно

- Pulumi CLI, bun, Node.js, curl.
- Сервисный пользователь **аккаунта** Selectel с ролями `member` (аккаунт) и `iam.admin` — свой у
  каждого (IAM → Сервисные пользователи).
- Файл `~/.config/selectel.env` с правами 600 — одной строкой в отдельном терминале, пароль и
  passphrase вводятся скрыто:

  ```sh
  install -m600 /dev/null ~/.config/selectel.env && read -rsp 'Selectel password: ' p && echo && read -rsp 'Pulumi passphrase: ' pp && echo && printf 'SELECTEL_USERNAME=<ваш-пользователь>\nSELECTEL_PASSWORD=%s\nSELECTEL_DOMAIN_NAME=631994\nPULUMI_CONFIG_PASSPHRASE=%s\n' "$p" "$pp" > ~/.config/selectel.env && unset p pp
  ```

  Passphrase стека `main` — в менеджере паролей команды.

## Каждый запуск

```sh
cd pulumi/bootstrap
source env.sh                        # OS_* для провайдера Selectel, PULUMI_CONFIG_PASSPHRASE
pulumi install                       # один раз на клоне: SDK selectel в sdks/, зависимости через bun
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…   # ключи стейта (см. «Ключи стейта»)
pulumi login "s3://cdd-infra-state/bootstrap?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true"
pulumi stack select main
pulumi preview
```

Тесты: `bun run test`.

## Ключи стейта

Выходы стека `stateAccessKey` и `stateSecretKey` — ключи S3 для стейтов:

```sh
pulumi stack output stateAccessKey
pulumi stack output stateSecretKey --show-secrets
```

Получить их можно только имея доступ к стейту, поэтому первый раз их передаёт тот, у кого они уже
есть (менеджер паролей команды).

## Основной стек

```sh
cd pulumi
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
pulumi login "$(pulumi -C bootstrap stack output backendUrl)"   # s3://cdd-infra-state/main?…
```

### Переезд стека pulumi-cellestial из devops-pulumi-state

Делает тот, у кого есть доступ к старому бакету:

```sh
cd pulumi
# старый бакет — прежние ключи и login
pulumi stack select dev
pulumi stack export --show-secrets --file /tmp/cellestial-dev.json   # с секретами в открытом виде — не коммитить
# новый бакет
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…     # ключи стейта из bootstrap
pulumi login "s3://cdd-infra-state/main?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true"
pulumi stack init dev --secrets-provider passphrase     # та же passphrase, что у старого стека
pulumi stack import --file /tmp/cellestial-dev.json
pulumi preview                                          # ожидается: без изменений
rm /tmp/cellestial-dev.json
```

После этого `devops-pulumi-state` можно удалить.

## Первый запуск (уже выполнен, для справки)

1. `pulumi login file://~/.pulumi-bootstrap-local`, `pulumi stack init main`,
   `pulumi config set infra-bootstrap:projectId 800b74820d5440a3a00b6b961eccabf7`,
   `pulumi config set infra-bootstrap:s3Pool ru-7`, `pulumi config set infra-bootstrap:bucketName cdd-infra-state`.
2. `pulumi preview` → `pulumi up` (импорт проекта `infra-state`, пользователь, ключ, бакет,
   версионирование).
3. Перенос стейта в бакет: `pulumi stack export --show-secrets --file bootstrap-state-export.json` (файл в `.gitignore`), ключи стейта в
   `AWS_*`, `pulumi login "s3://cdd-infra-state/bootstrap?…"`, `pulumi stack init main`,
   `pulumi stack import --file bootstrap-state-export.json`, `pulumi preview` (без изменений),
   удалить файл экспорта и `~/.pulumi-bootstrap-local`.
````

- [ ] **Step 2: Commit**

```bash
cd /mnt/f/Github/2026_H2/infra
git add pulumi/bootstrap/README.md
git commit -m "docs(bootstrap): README — запуск, ключи стейта, переезд основного стека (#5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Первый запуск на Selectel и перенос стейта

**Files:**
- Create: `pulumi/bootstrap/Pulumi.main.yaml` (создаёт `pulumi stack init` + `config set`)
- Modify: `pulumi/bootstrap/README.md` (только если реальные команды разошлись с написанными)

**Interfaces:**
- Consumes: всё из Task 1–4.

Предусловие: в `~/.config/selectel.env` есть `PULUMI_CONFIG_PASSPHRASE`. Если нет — **остановиться** и попросить пользователя добавить её командой (в отдельном терминале, одной строкой):
`read -rsp 'Pulumi passphrase: ' pp && echo && printf 'PULUMI_CONFIG_PASSPHRASE=%s\n' "$pp" >> ~/.config/selectel.env && unset pp`
Проверка без вывода значения: `grep -c '^PULUMI_CONFIG_PASSPHRASE=.' ~/.config/selectel.env` → `1`.

- [ ] **Step 1: Локальный стейт, стек и конфиг**

```bash
cd /mnt/f/Github/2026_H2/infra/pulumi/bootstrap && source env.sh
pulumi login file://$HOME/.pulumi-bootstrap-local
pulumi stack init main
pulumi config set infra-bootstrap:projectId 800b74820d5440a3a00b6b961eccabf7
pulumi config set infra-bootstrap:s3Pool ru-7
pulumi config set infra-bootstrap:bucketName cdd-infra-state
cat Pulumi.main.yaml
```
Expected: `Pulumi.main.yaml` содержит `encryptionsalt` и три ключа `infra-bootstrap:*`, без секретов.

- [ ] **Step 2: preview — показать пользователю и ОСТАНОВИТЬСЯ**

Run: `pulumi preview --diff > /tmp/claude-1000/-mnt-f-Github-2026-H2-infra/ac9607f3-d6f2-4889-8563-2e9d2b29ec39/scratchpad/bootstrap-preview.txt 2>&1; tail -30 /tmp/claude-1000/-mnt-f-Github-2026-H2-infra/ac9607f3-d6f2-4889-8563-2e9d2b29ec39/scratchpad/bootstrap-preview.txt`
Expected: `= selectel:…VpcProjectV2 infra-state import`, `+` для `state-user-password`, `state-user`, `state-s3`, `selectel-s3`, `state-bucket`, `state-versioning`; нет `-` и `~`. Предупреждения импорта о расхождении входов проекта — показать пользователю; если импорт требует других входов (`name`), поправить `index.ts` под фактические значения и повторить preview.

Показать пользователю итог плана и **ждать явного «да»** перед Step 3.

- [ ] **Step 3: up**

Run: `pulumi up --yes --skip-preview 2>&1 | tail -40`
Expected: `Resources: 1 imported, 6 created` (плюс стек), в логе `S3 принял ключ стейта (попытка N)`. Ошибка — superpowers:systematic-debugging, стейт после частичного up не трогать руками.

- [ ] **Step 4: Повторный preview и проверки бакета**

```bash
pulumi preview 2>&1 | tail -5                     # Expected: без изменений (все unchanged)
export AWS_ACCESS_KEY_ID=$(pulumi stack output stateAccessKey) AWS_SECRET_ACCESS_KEY=$(pulumi stack output stateSecretKey --show-secrets)
E=https://s3.ru-7.storage.selcloud.ru
aws --endpoint-url $E --region ru-7 s3api get-bucket-versioning --bucket cdd-infra-state   # Expected: "Status": "Enabled"
echo probe > /tmp/claude-1000/-mnt-f-Github-2026-H2-infra/ac9607f3-d6f2-4889-8563-2e9d2b29ec39/scratchpad/probe.txt
aws --endpoint-url $E --region ru-7 s3 cp /tmp/claude-1000/-mnt-f-Github-2026-H2-infra/ac9607f3-d6f2-4889-8563-2e9d2b29ec39/scratchpad/probe.txt s3://cdd-infra-state/probe.txt && aws --endpoint-url $E --region ru-7 s3 rm s3://cdd-infra-state/probe.txt   # Expected: upload + delete
aws --endpoint-url $E --region ru-7 s3 ls s3://devops-pulumi-state/    # Expected: ошибка AccessDenied/NoSuchBucket — чужой проект недоступен
pulumi stack export | jq '[.deployment.resources[] | select(.protect == true) | .urn | split("::")[-1]]'   # Expected: ["infra-state","state-bucket"]
```
`aws` использовать с `AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt` (snap-awscli не доверяет цепочке Selectel).

- [ ] **Step 5: Перенос стейта bootstrap в бакет**

```bash
pulumi stack export --show-secrets --file bootstrap-state-export.json   # в .gitignore; секреты в открытом виде
pulumi login "s3://cdd-infra-state/bootstrap?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true"
pulumi stack init main
pulumi stack import --file bootstrap-state-export.json
pulumi preview 2>&1 | tail -5          # Expected: без изменений
aws --endpoint-url $E --region ru-7 s3 ls s3://cdd-infra-state/bootstrap/ --recursive | head   # Expected: .pulumi/stacks/infra-bootstrap/main.json
rm bootstrap-state-export.json && rm -rf $HOME/.pulumi-bootstrap-local
```
Если `pulumi login` не принимает параметры `endpoint`/`s3ForcePathStyle` — пробовать `AWS_ENDPOINT_URL=$E` с `s3://…?region=ru-7`, записать ruling и поправить `backendUrl` в `index.ts` (+ тест) и README.

- [ ] **Step 6: Commit**

```bash
cd /mnt/f/Github/2026_H2/infra
git add pulumi/bootstrap/Pulumi.main.yaml pulumi/bootstrap/README.md
git commit -m "feat(bootstrap): стек main поднят, стейт перенесён в cdd-infra-state/bootstrap (#5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
