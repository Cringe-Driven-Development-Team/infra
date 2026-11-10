import * as pulumi from "@pulumi/pulumi";
import { createAuxiliary } from "./auxiliary";
import { createBackend } from "./backend";
import { loadConfig } from "./config";
import { createDomains } from "./domains";
import { createFoundation } from "./foundation";
import { createGateway } from "./gateway";
import { createRuntime } from "./runtime";
import { createStorage } from "./storage";

// Сборка стека: foundation → gateway/backend/storage/auxiliary → runtime и domains.
const c = loadConfig();
const f = createFoundation(c);
const gateway = createGateway(c, f);
const backend = createBackend(c, f, gateway.port);
const storage = createStorage(c, f);
const auxiliary = createAuxiliary(c, f);
const runtime = createRuntime(c, f, backend, [gateway.floatingIp.address, auxiliary?.floatingIp.address]);
createDomains(c, f, gateway, storage, auxiliary);

export const projectId = f.project.id;
export const publicIp = gateway.floatingIp.address;
// Приватные адреса: gateway — источник правил security group VPS2, backend — адрес Go API для BFF
// (app_backend_address в inventory) и цель ProxyCommand Ansible
export const gatewayPrivateIp = gateway.port.allFixedIps.apply((ips) => ips[0]);
export const backendPrivateIp = backend?.port.allFixedIps.apply((ips) => ips[0]) ?? null;
export const gatewayName = gateway.server.name;
export const sshUser = c.sshUser;
export const domain = c.dns.appDomain ?? null;
export const s3Endpoint = c.s3.endpoint;
export const s3Bucket = storage.releasesBucket.bucket;
export const s3PublicDomain = storage.releasesAccess.publicDomain;
// Свой домен CDN-ресурса (у аватарок — avatarsCustomDomain); HTTPS на них — после выпуска
// сертификатов в панели
export const cdnCustomDomain = c.dns.cdnDomain ?? null;
export const cdnResourceId = storage.cdn?.id ?? null;
export const cdnDefaultDomain = storage.cdn?.cdnDomain ?? null;
export const serviceUserName = f.serviceUser.name;
export const serviceUserPassword = pulumi.secret(f.serviceUserPassword.result);
export const s3AccessKey = f.s3Credentials.accessKey;
export const s3SecretKey = pulumi.secret(f.s3Credentials.secretKey);
// Ключ Go API к бакету ноутбуков; endpoint и регион — s3Endpoint и infra:s3Pool
export const notebooksBucket = storage.notebooksBucket.bucket;
export const notebooksAccessKey = storage.notebooksCredentials.accessKey;
export const notebooksSecretKey = pulumi.secret(storage.notebooksCredentials.secretKey);
// Бакет аватарок: пишет тот же ключ Go API, публичный URL — https://<avatarsPublicDomain>/<ключ>
export const avatarsBucket = storage.avatarsBucket.bucket;
export const avatarsPublicDomain = storage.avatarsAccess.publicDomain;
export const avatarsCustomDomain = c.dns.avatarsDomain ?? null;
// Бакет статики: пишет ключ стека (s3AccessKey), публичный URL — https://<staticCustomDomain>/<ключ>
export const staticBucket = storage.staticBucket?.bucket ?? null;
export const staticPublicDomain = storage.staticAccess?.publicDomain ?? null;
export const staticCustomDomain = c.dns.staticDomain ?? null;
export const staticCdnResourceId = storage.staticCdn?.id ?? null;
export const staticCdnDefaultDomain = storage.staticCdn?.cdnDomain ?? null;
// Вспомогательная VPS (infra:auxiliary): адрес, домен вебхуков, бакет и ключ бэкапов restic
export const auxiliaryPublicIp = auxiliary?.floatingIp.address ?? null;
export const hooksDomain = c.dns.hooksDomain ?? null;
export const auxiliaryBackupBucket = auxiliary?.backupBucket.bucket ?? null;
export const auxiliaryBackupPoolName = c.auxiliary?.backupPool ?? null;
export const auxiliaryBackupAccessKey = auxiliary?.backupCredentials.accessKey ?? null;
export const auxiliaryBackupSecretKey = auxiliary ? pulumi.secret(auxiliary.backupCredentials.secretKey) : null;
// Runtime-VPS (infra:runtime): вход Go API в проект и материалы mTLS/SSH; в vault и vars.yml —
// RUNBOOK.md, «Runtime-VPS». Секретные — только с --show-secrets.
const runtimeOutput = (key: keyof NonNullable<typeof runtime>) => (runtime ? pulumi.output(runtime[key]) : null);
export const runtimeOsUsername = runtimeOutput("runtimeOsUsername");
export const runtimeOsPassword = runtimeOutput("runtimeOsPassword");
export const runtimeProjectId = runtimeOutput("runtimeProjectId");
export const runtimeAccountId = runtimeOutput("runtimeAccountId");
export const runtimeRegion = runtimeOutput("runtimeRegion");
export const runtimeAvailabilityZone = runtimeOutput("runtimeAvailabilityZone");
export const runtimeNetworkId = runtimeOutput("runtimeNetworkId");
export const runtimeSubnetId = runtimeOutput("runtimeSubnetId");
export const runtimeSecurityGroupId = runtimeOutput("runtimeSecurityGroupId");
export const runtimeKeypairName = runtimeOutput("runtimeKeypairName");
export const runtimeSshPrivateKey = runtimeOutput("runtimeSshPrivateKey");
export const runtimeCaCert = runtimeOutput("runtimeCaCert");
export const runtimeCaKey = runtimeOutput("runtimeCaKey");
export const runtimeClientCert = runtimeOutput("runtimeClientCert");
export const runtimeClientKey = runtimeOutput("runtimeClientKey");
export const runtimeImageId = runtimeOutput("runtimeImageId");
export const runtimeFlavorId = runtimeOutput("runtimeFlavorId");
export const runtimeVolumeType = runtimeOutput("runtimeVolumeType");
export const runtimeBlockedPublicIps = runtimeOutput("runtimeBlockedPublicIps");
export const runtimeSecretsKey = runtimeOutput("runtimeSecretsKey");
