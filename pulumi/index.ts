import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import * as openstack from "@pulumi/openstack";
import * as selectel from "@pulumi/selectel";
import * as aws from "@pulumi/aws";
import { execFile } from "child_process";

const cfg = new pulumi.Config();
const selectelCfg = new pulumi.Config("selectel");

// Номер аккаунта Selectel: берём из selectel:domainName, infra:domainName — только для переопределения
const domainName = cfg.get("domainName") ?? selectelCfg.require("domainName");
const pool = cfg.require("pool");                  // пул VPS, например ru-9
const zone = cfg.require("zone");                  // например ru-9a
const volumeType = cfg.require("volumeType");      // например fast.ru-9a
const imageName = cfg.require("imageName");
const sshPublicKey = cfg.require("sshPublicKey");

// Имя уровня аккаунта (проект, keypair). Аккаунт общий на курс,
// поэтому переопределяется через infra:name. Логические имена "study" не менять — это replace.
const name = cfg.get("name") ?? "pulumi-study";

// Объектное хранилище: пул (например ru-1) задаёт endpoint s3.<pool>.storage.selcloud.ru
// и region подписи. Имя бакета глобально уникально в рамках аккаунта.
const s3Pool = cfg.require("s3Pool");
const s3BucketName = cfg.require("s3Bucket");
const s3EndpointUrl = `https://s3.${s3Pool}.storage.selcloud.ru`;

const project = new selectel.VpcProjectV2("study", { name });

const password = new random.RandomPassword("serviceuser", {
  length: 24,
  upper: true,
  lower: true,
  numeric: true,
  minUpper: 1,
  minLower: 1,
  minNumeric: 1,
  minSpecial: 1,
  overrideSpecial: "!#$%&*+-.:;<=>?@^_{|}~",
});

// Имя сервисного пользователя проекта отдельно от infra:name (проект/keypair),
// оно видно в панели IAM и используется как логин OpenStack.
const serviceUser = new selectel.IamServiceuserV1("study", {
  name: cfg.get("serviceUserName") ?? "cellestialSystemUser",
  password: password.result,
  // member на проект: OpenStack + полный доступ к S3 проекта (создание бакетов,
  // политики, объекты). s3.user/s3.bucket.user без bucket policy ничего не могут.
  roles: [
    { roleName: "member", scope: "project", projectId: project.id },
  ],
});

// S3-ключ сервисного пользователя, выданный на проект продукта.
// В Selectel ключ привязан к паре «пользователь + проект», а не к бакету:
// все бакеты проекта доступны этим ключом в рамках ролей пользователя.
const s3Credentials = new selectel.IamS3CredentialsV1("product-s3", {
  name: `${name}-releases`,
  userId: serviceUser.id,
  projectId: project.id,
});

const keypair = new selectel.VpcKeypairV2("study", {
  name,
  publicKey: sshPublicKey,
  userId: serviceUser.id,
});

const os = new openstack.Provider("selectel-project", {
  authUrl: "https://cloud.api.selcloud.ru/identity/v3",
  domainName,
  tenantId: project.id,
  userName: serviceUser.name,
  password: password.result,
  region: pool,
});

const withOs = { provider: os };

const external = openstack.networking.getNetworkOutput({ external: true }, withOs);

const network = new openstack.networking.Network("private", {
  name: "private-network",
  adminStateUp: true,
}, withOs);

const subnet = new openstack.networking.Subnet("private", {
  name: "private-subnet",
  networkId: network.id,
  cidr: "192.168.199.0/24",
}, withOs);

const router = new openstack.networking.Router("router", {
  name: "router",
  externalNetworkId: external.id,
}, withOs);

const routerInterface = new openstack.networking.RouterInterface("router", {
  routerId: router.id,
  subnetId: subnet.id,
}, withOs);

const image = openstack.images.getImageOutput({
  name: imageName,
  mostRecent: true,
  visibility: "public",
}, withOs);

// Флейворы: id в панели не показывается, а имена ("SL1.2-4096") есть не во всех пулах —
// getFlavor по несуществующему имени падает с "Your query returned no results".
// Порядок: infra:<role>FlavorId → infra:<role>FlavorName → поиск по vcpus/ram(/disk).
// Список доступных имён: ./scripts/list-flavors.sh
function flavorId(role: string): pulumi.Input<string> {
  const id = cfg.get(`${role}FlavorId`);
  if (id) {
    return id;
  }
  const flavorName = cfg.get(`${role}FlavorName`);
  if (flavorName) {
    return openstack.compute.getFlavorOutput({ name: flavorName }, withOs).id;
  }
  const disk = cfg.getNumber(`${role}Disk`);
  return openstack.compute.getFlavorOutput({
    vcpus: cfg.requireNumber(`${role}Vcpus`),
    ram: cfg.requireNumber(`${role}Ram`),
    ...(disk ? { disk } : {}),
  }, withOs).id;
}

const gatewayFlavorId = flavorId("gateway");
const backendFlavorId = flavorId("backend");

const gatewayPort = new openstack.networking.Port("gateway", {
  name: "gateway-port",
  networkId: network.id,
  fixedIps: [{ subnetId: subnet.id }],
}, withOs);

const backendPort = new openstack.networking.Port("backend", {
  name: "backend-port",
  networkId: network.id,
  fixedIps: [{ subnetId: subnet.id }],
}, withOs);

const gatewayVolume = new openstack.blockstorage.Volume("gateway", {
  name: "boot-volume-gateway",
  size: 10,
  imageId: image.id,
  volumeType,
  availabilityZone: zone,
  enableOnlineResize: true,
}, { ...withOs, ignoreChanges: ["imageId"] });

const backendVolume = new openstack.blockstorage.Volume("backend", {
  name: "boot-volume-backend",
  size: cfg.getNumber("backendVolumeSize") ?? 10,
  imageId: image.id,
  volumeType,
  availabilityZone: zone,
  enableOnlineResize: true,
}, { ...withOs, ignoreChanges: ["imageId"] });

// VPS 1: шлюз — публичный IP, Caddy, jump-хост для Ansible к VPS 2
const serverGateway = new openstack.compute.Instance("gateway", {
  name: "pulumi-server-gateway",
  flavorId: gatewayFlavorId,
  keyPair: keypair.name,
  availabilityZone: zone,
  networks: [{ port: gatewayPort.id }],
  blockDevices: [{
    sourceType: "volume",
    destinationType: "volume",
    uuid: gatewayVolume.id,
    bootIndex: 0,
    deleteOnTermination: false,
  }],
  // По metadata.role dynamic inventory Ansible собирает группы gateway/backend
  metadata: { role: "gateway", env: "study" },
  vendorOptions: { ignoreResizeConfirmation: true },
}, { ...withOs, ignoreChanges: ["imageId"], dependsOn: [routerInterface] });

// VPS 2: только в приватной сети, без floating IP
const serverBackend = new openstack.compute.Instance("backend", {
  name: "pulumi-server-backend",
  flavorId: backendFlavorId,
  keyPair: keypair.name,
  availabilityZone: zone,
  networks: [{ port: backendPort.id }],
  blockDevices: [{
    sourceType: "volume",
    destinationType: "volume",
    uuid: backendVolume.id,
    bootIndex: 0,
    deleteOnTermination: false,
  }],
  metadata: { role: "backend", env: "study" },
  vendorOptions: { ignoreResizeConfirmation: true },
}, { ...withOs, ignoreChanges: ["imageId"], dependsOn: [routerInterface] });

const floatingIp = new openstack.networking.FloatingIp("gateway", {
  pool: "external-network",
}, withOs);

// Без dependsOn привязка стартует раньше подключения подсети к роутеру:
// Neutron отвечает ExternalGatewayForFloatingIPNotFound
new openstack.networking.FloatingIpAssociate("gateway", {
  portId: gatewayPort.id,
  floatingIp: floatingIp.address,
}, { ...withOs, dependsOn: [routerInterface] });

// ---------------------------------------------------------------------------
// S3 в проекте продукта. Отдельного ресурса «включить S3 в проекте» нет:
// хранилище проекта в пуле появляется с первым бакетом, созданным через
// эндпоинт этого пула ключом, выданным на этот проект.
// Провайдер Selectel бакеты не создаёт — только S3 API (@pulumi/aws), как в
// документации Selectel по Terraform.
// ---------------------------------------------------------------------------

// Ключ, выданный через IAM, S3-шлюз признаёт не сразу: до этого CreateBucket
// отвечает 403 InvalidAccessKeyId. Вместо фиксированной паузы опрашиваем S3
// этим же ключом (ListBuckets, подпись SigV4 делает curl — он ходит через
// системное доверие macOS, как и сам Pulumi) и ждём, пока ключ примут.
// Секрет передаётся curl'у через stdin, не через аргументы.
function probeS3(accessKey: string, secretKey: string): Promise<{ code: number; body: string }> {
  return new Promise((resolve) => {
    const child = execFile("curl", [
      "-sS", "-o", "-", "-w", "\n%{http_code}",
      "--max-time", "20",
      "--aws-sigv4", `aws:amz:${s3Pool}:s3`,
      "-K", "-",
      `${s3EndpointUrl}/`,
    ], (err, stdout, stderr) => {
      const lines = String(stdout ?? "").trimEnd().split("\n");
      const code = Number(lines.pop());
      if (err || !Number.isFinite(code) || code === 0) {
        // curl не запустился, сеть, TLS — ответа от S3 нет
        resolve({ code: -1, body: String(stderr || err?.message || "нет ответа").trim() });
        return;
      }
      resolve({ code, body: lines.join("\n") });
    });
    child.stdin?.end(`user = "${accessKey}:${secretKey}"\n`);
  });
}

const s3KeyReadyTimeoutSeconds = cfg.getNumber("s3KeyReadyTimeoutSeconds") ?? 900;
const s3AccessKeyReady = pulumi.all([s3Credentials.accessKey, s3Credentials.secretKey])
  .apply(async ([accessKey, secretKey]) => {
    if (pulumi.runtime.isDryRun()) {
      return accessKey;
    }
    const deadline = Date.now() + s3KeyReadyTimeoutSeconds * 1000;
    let last = { code: 0, body: "" };
    for (let attempt = 1; Date.now() < deadline; attempt++) {
      last = await probeS3(accessKey, secretKey);
      if (last.code === 200) {
        pulumi.log.info(`S3-ключ принят шлюзом (попытка ${attempt})`);
        return accessKey;
      }
      if (last.code === -1) {
        // curl не запустился или сеть/TLS — не блокируем, пусть провайдер покажет ошибку сам
        pulumi.log.warn(`Проверка S3-ключа пропущена: ${last.body}`);
        return accessKey;
      }
      if (!last.body.includes("InvalidAccessKeyId")) {
        pulumi.log.warn(`S3 ответил ${last.code}, а не InvalidAccessKeyId — продолжаю: ${last.body.slice(0, 300)}`);
        return accessKey;
      }
      pulumi.log.info(`S3-ключ ещё не принят (попытка ${attempt}, HTTP ${last.code}), жду 15 с`);
      await new Promise((resolve) => setTimeout(resolve, 15000));
    }
    throw new Error(
      `S3-шлюз ${s3EndpointUrl} так и не принял ключ за ${s3KeyReadyTimeoutSeconds} с ` +
      `(последний ответ HTTP ${last.code}: ${last.body.slice(0, 300)}). ` +
      `Ключ выдан в IAM, но в S3 не появился — это на стороне Selectel.`,
    );
  });

const s3 = new aws.Provider("selectel-s3-product", {
  region: s3Pool,                          // регион подписи = пул
  accessKey: s3AccessKeyReady,
  secretKey: s3Credentials.secretKey,
  endpoints: [{ s3: s3EndpointUrl }],      // https://s3.<пул>.storage.selcloud.ru
  s3UsePathStyle: true,
  // Selectel — не AWS: те же skip_*, что в документации Selectel для Terraform
  skipCredentialsValidation: true,
  skipRegionValidation: true,
  skipRequestingAccountId: true,
  skipMetadataApiCheck: true,
});

// forceDestroy: true — pulumi destroy удаляет бакет вместе с объектами
const bucket = new aws.s3.Bucket("product-releases", {
  bucket: s3BucketName,
  forceDestroy: true,
}, { provider: s3 });

// Публичное чтение объектов (под будущий CDN). Роль member на проект разрешает
// управлять политиками; включается infra:s3PublicRead=true.
if (cfg.getBoolean("s3PublicRead") ?? false) {
  new aws.s3.BucketPolicy("product-public-read", {
    bucket: bucket.id,
    policy: bucket.arn.apply((arn) => JSON.stringify({
      Version: "2012-10-17",
      Statement: [{
        Effect: "Allow",
        Principal: "*",
        Action: "s3:GetObject",
        Resource: `${arn}/*`,
      }],
    })),
  }, { provider: s3 });
}

// A-запись домена → publicIp VPS 1 в зоне Selectel DNS (зона может лежать в другом проекте)
const appDomain = cfg.get("domain");
if (appDomain) {
  const dnsZone = cfg.require("dnsZone");            // с точкой на конце: cellestial.ru.
  const dnsProjectId = cfg.require("dnsProjectId");  // проект, где лежит зона
  const fqdn = appDomain.endsWith(".") ? appDomain : `${appDomain}.`;

  const zoneRef = selectel.getDomainsZoneV2Output({ name: dnsZone, projectId: dnsProjectId });

  new selectel.DomainsRrsetV2("app", {
    zoneId: zoneRef.id,
    projectId: dnsProjectId,
    name: fqdn,
    type: "A",
    ttl: 300,
    records: [{ content: floatingIp.address }],
  });
}

export const projectId = project.id;
export const publicIp = floatingIp.address;
export const privateIp = backendPort.allFixedIps.apply((ips) => ips[0]);
export const gatewayName = serverGateway.name;
export const backendName = serverBackend.name;
export const sshUser = cfg.get("sshUser") ?? "root";
export const domain = appDomain ?? null;
export const s3Endpoint = s3EndpointUrl;
export const s3Bucket = bucket.bucket;
export const serviceUserName = serviceUser.name;
export const serviceUserPassword = pulumi.secret(password.result);
export const s3AccessKey = s3Credentials.accessKey;
export const s3SecretKey = pulumi.secret(s3Credentials.secretKey);
