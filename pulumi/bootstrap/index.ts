import * as aws from "@pulumi/aws";
import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import * as selectel from "@pulumi/selectel";
import { credentialsFromEnv, curl, initProjectS3, waitForS3Key } from "./selectel-s3";

const cfg = new pulumi.Config();
const pool = cfg.require("s3Pool");
const bucketName = cfg.require("bucketName");
const keyReadyTimeout = cfg.getNumber("s3KeyReadyTimeoutSeconds") ?? 600;
const endpoint = `https://s3.${pool}.storage.selcloud.ru`;

// Отдельный проект только под стейт: рядом нет чужих ресурсов, его не снесут при уборке.
const project = new selectel.VpcProjectV2("infra-state", { name: "infra-state" }, { protect: true });

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
