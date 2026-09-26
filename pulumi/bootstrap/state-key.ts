// Личный S3-ключ на проект infra-shared: bun state-key.ts [--force]
// Выпускает ключ текущему сервисному пользователю (OS_USERNAME из env.sh) и дописывает
// AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY в ~/.config/selectel.env (или SELECTEL_ENV).
import { chmodSync, existsSync, readFileSync, writeFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { accountToken, createS3Key, credentialsFromEnv, curl, findProjectId, findServiceUserId, waitForS3Key } from "./selectel-s3";

const STATE_PROJECT = "infra-shared";
const POOL = "ru-7";

// Заменяет или добавляет строки KEY=value, остальные строки файла сохраняет.
export function upsertEnv(content: string, values: Record<string, string>): string {
  const keep = content.split(/\r?\n/).filter((line) => line !== "" && !Object.hasOwn(values, line.split("=")[0]));
  const added = Object.entries(values).map(([k, v]) => `${k}=${v}`);
  return [...keep, ...added].join("\n") + "\n";
}

async function main(): Promise<void> {
  const file = process.env.SELECTEL_ENV || join(homedir(), ".config", "selectel.env");
  const current = existsSync(file) ? readFileSync(file, "utf8") : "";
  if (/^AWS_ACCESS_KEY_ID=./m.test(current) && !process.argv.includes("--force")) {
    throw new Error(`В ${file} уже есть AWS_ACCESS_KEY_ID. Новый ключ — с --force (старый удалите в IAM)`);
  }
  const creds = credentialsFromEnv(process.env);
  const token = await accountToken(curl, creds);
  const projectId = await findProjectId(curl, token, STATE_PROJECT);
  const userId = await findServiceUserId(curl, token, creds.username);
  const key = await createS3Key(curl, token, userId, projectId, `pulumi-state-${creds.username}`);
  writeFileSync(file, upsertEnv(current, { AWS_ACCESS_KEY_ID: key.accessKey, AWS_SECRET_ACCESS_KEY: key.secretKey }), { mode: 0o600 });
  chmodSync(file, 0o600);
  const attempts = await waitForS3Key(curl, {
    endpoint: `https://s3.${POOL}.storage.selcloud.ru`, pool: POOL, accessKey: key.accessKey, secretKey: key.secretKey, timeoutSeconds: 600,
  });
  console.log(`S3-ключ ${key.accessKey.slice(0, 4)}… для ${creds.username} на ${STATE_PROJECT} записан в ${file}, S3 принял его (попытка ${attempts}). Выполните source env.sh`);
}

if (import.meta.main) {
  main().catch((e: Error) => {
    console.error(`state-key: ${e.message}`);
    process.exit(1);
  });
}
