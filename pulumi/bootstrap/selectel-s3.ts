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

// Keystone-токен сервисного пользователя: на проект (инициализация S3) или на аккаунт (IAM, VPC API).
async function keystoneToken(http: Http, c: Credentials, scope: object, what: string): Promise<string> {
  const body = JSON.stringify({
    auth: {
      identity: {
        methods: ["password"],
        password: { user: { name: c.username, domain: { name: c.domain }, password: c.password } },
      },
      scope,
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
    throw new Error(`Не удалось получить токен ${what}: HTTP ${res.status || "без ответа"}`);
  }
  return token;
}

const projectToken = (http: Http, c: Credentials, projectId: string) =>
  keystoneToken(http, c, { project: { id: projectId } }, `проекта ${projectId}`);

export const accountToken = (http: Http, c: Credentials) =>
  keystoneToken(http, c, { domain: { name: c.domain } }, `аккаунта ${c.domain}`);

// GET к API Selectel с токеном в stdin; тело — JSON.
async function apiGet(http: Http, token: string, url: string): Promise<any> {
  const res = await http(["-H", "Accept: application/json", "-K", "-", url], `header = "X-Auth-Token: ${token}"\n`);
  if (res.status !== 200) {
    throw new Error(`${url} ответил ${res.status || "без ответа"}: ${res.body.slice(0, 300)}`);
  }
  return JSON.parse(res.body);
}

export async function findProjectId(http: Http, token: string, name: string): Promise<string> {
  const { projects = [] } = await apiGet(http, token, "https://api.selectel.ru/vpc/resell/v2/projects");
  const found = projects.find((p: { name: string }) => p.name === name);
  if (!found) {
    throw new Error(`Проект ${name} не найден; есть: ${projects.map((p: { name: string }) => p.name).join(", ") || "—"}`);
  }
  return found.id;
}

export async function findServiceUserId(http: Http, token: string, name: string): Promise<string> {
  const { users = [] } = await apiGet(http, token, "https://api.selectel.ru/iam/v1/service_users");
  const found = users.find((u: { name: string }) => u.name === name);
  if (!found) {
    throw new Error(`Сервисный пользователь ${name} не найден`);
  }
  return found.id;
}

// Новый S3-ключ пользователя на проект. Секрет Selectel показывает только в ответе на создание.
export async function createS3Key(
  http: Http, token: string, userId: string, projectId: string, name: string,
): Promise<{ accessKey: string; secretKey: string }> {
  const url = `https://api.selectel.ru/iam/v1/service_users/${userId}/credentials`;
  const res = await http(
    ["-X", "POST", "-H", "Content-Type: application/json", "--data-raw", JSON.stringify({ name, project_id: projectId }), "-K", "-", url],
    `header = "X-Auth-Token: ${token}"\n`,
  );
  if (res.status < 200 || res.status >= 300) {
    throw new Error(`Выпуск S3-ключа ответил ${res.status || "без ответа"}: ${res.body.slice(0, 300)}`);
  }
  const { access_key: accessKey, secret_key: secretKey } = JSON.parse(res.body);
  if (!accessKey || !secretKey) {
    throw new Error("Выпуск S3-ключа: в ответе нет access_key/secret_key");
  }
  return { accessKey, secretKey };
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
