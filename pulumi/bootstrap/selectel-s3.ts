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
