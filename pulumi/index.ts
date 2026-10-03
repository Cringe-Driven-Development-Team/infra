import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import * as openstack from "@pulumi/openstack";
import * as selectel from "@pulumi/selectel";
import * as aws from "@pulumi/aws";
import { credentialsFromEnv, curl, initProjectS3, waitForS3Key } from "./bootstrap/selectel-s3";
import { BucketAccess, BucketDomain, CdnDomain, CdnResource, checkCdnName } from "./selectel-storage";

const cfg = new pulumi.Config();
const selectelCfg = new pulumi.Config("selectel");

// Учётные данные Selectel — личный сервисный пользователь аккаунта из ~/.config/selectel.env
// (source pulumi/bootstrap/env.sh → OS_USERNAME/OS_PASSWORD/OS_DOMAIN_NAME). Провайдер selectel
// читает их из окружения сам; в конфиге стека (он коммитится) логина и пароля нет.
const selectelCreds = credentialsFromEnv(process.env);
// SELECTEL_PROJECT в selectel.env даёт OS_PROJECT_NAME (для openstack CLI). Провайдер OpenStack
// читает его как tenant_name рядом с явным tenantId ниже, и gophercloud такую пару отвергает —
// с непонятной ошибкой посреди up. Проверяем заранее.
if (process.env.OS_PROJECT_NAME) {
  throw new Error(
    `Задан OS_PROJECT_NAME=${process.env.OS_PROJECT_NAME} (SELECTEL_PROJECT в selectel.env): провайдер OpenStack ` +
      "возьмёт его вместе с tenantId проекта и не авторизуется. Для pulumi: unset OS_PROJECT_NAME " +
      "или source pulumi/bootstrap/env.sh без SELECTEL_PROJECT.",
  );
}
if (selectelCfg.get("username") !== undefined || selectelCfg.get("password") !== undefined) {
  // Конфиг сильнее окружения: провайдер работал бы от пользователя из конфига, а инициализация
  // S3 — от пользователя из selectel.env. Такая смесь не нужна — убрать из конфига.
  throw new Error(
    "В конфиге стека остались selectel:username/selectel:password — учётные данные теперь из " +
    "selectel.env. Выполните: pulumi config rm selectel:username && pulumi config rm selectel:password",
  );
}

// Номер аккаунта: infra:domainName → selectel:domainName → OS_DOMAIN_NAME из selectel.env
const domainName = cfg.get("domainName") ?? selectelCfg.get("domainName") ?? selectelCreds.domain;
const pool = cfg.require("pool");                  // пул VPS, например ru-9
const zone = cfg.require("zone");                  // например ru-9a
const volumeType = cfg.require("volumeType");      // например fast.ru-9a
const imageName = cfg.require("imageName");
const sshPublicKey = cfg.require("sshPublicKey");

// Имя уровня аккаунта (проект, keypair). Аккаунт общий на курс,
// поэтому переопределяется через infra:name. Логические имена были "study" — aliases
// сохраняют связь со стейтом, чтобы переименование не пересоздавало ресурсы.
const name = cfg.get("name") ?? "pulumi-release";

// Объектное хранилище: пул (например ru-1) задаёт endpoint s3.<pool>.storage.selcloud.ru
// и region подписи. Имя бакета глобально уникально в рамках аккаунта.
const s3Pool = cfg.require("s3Pool");
const s3BucketName = cfg.require("s3Bucket");
const s3EndpointUrl = `https://s3.${s3Pool}.storage.selcloud.ru`;
// Приватный бакет ноутбуков пользователей (.ipynb) — в том же пуле; доступ только у Go API.
const notebooksBucketName = cfg.require("notebooksBucket");
// Публичный бакет аватарок пользователей — в том же пуле; пишет Go API, читают все.
const avatarsBucketName = cfg.require("avatarsBucket");
// Тип бакета Selectel: public (по умолчанию) — чтение объектов без авторизации, источник для CDN.
const s3Public = cfg.getBoolean("s3Public") ?? true;
// CDN-ресурс с бакетом источником; технический домен — <id>.selcdn.net.
const cdnEnabled = cfg.getBoolean("cdn") ?? false;
// Свои домены CDN-ресурса и бакета релизов (например cdn.cellestial.ru, s3.cellestial.ru): CNAME в
// зоне infra:dnsZone, привязка и сертификат Let's Encrypt — CdnDomain и BucketDomain.
const cdnCustomDomainName = cfg.get("cdnDomain");
const s3CustomDomainName = cfg.get("s3Domain");
if (cdnCustomDomainName && !cdnEnabled) {
  throw new Error("infra:cdnDomain задан без infra:cdn: домен привязывается к CDN-ресурсу");
}
if (s3CustomDomainName && !s3Public) {
  throw new Error("infra:s3Domain задан при infra:s3Public=false: свой домен бывает только у публичного бакета");
}

const renamedFromStudy = { aliases: [{ name: "study" }] };

const project = new selectel.VpcProjectV2("release", { name }, renamedFromStudy);

const serviceUserPasswordArgs: random.RandomPasswordArgs = {
  length: 24,
  upper: true,
  lower: true,
  numeric: true,
  minUpper: 1,
  minLower: 1,
  minNumeric: 1,
  minSpecial: 1,
  overrideSpecial: "!#$%&*+-.:;<=>?@^_{|}~",
};
const password = new random.RandomPassword("serviceuser", serviceUserPasswordArgs);

// Имя сервисного пользователя проекта отдельно от infra:name (проект/keypair),
// оно видно в панели IAM и используется как логин OpenStack.
const serviceUser = new selectel.IamServiceuserV1("release", {
  name: cfg.get("serviceUserName") ?? "cellestialSystemUser",
  password: password.result,
  // member на проект: OpenStack + полный доступ к S3 проекта (создание бакетов,
  // политики, объекты). s3.user/s3.bucket.user без bucket policy ничего не могут.
  roles: [
    { roleName: "member", scope: "project", projectId: project.id },
  ],
}, renamedFromStudy);

// S3-ключ сервисного пользователя, выданный на проект продукта.
// В Selectel ключ привязан к паре «пользователь + проект», а не к бакету:
// все бакеты проекта доступны этим ключом в рамках ролей пользователя.
const s3Credentials = new selectel.IamS3CredentialsV1("product-s3", {
  name: `${name}-releases`,
  userId: serviceUser.id,
  projectId: project.id,
});

// Сервисный пользователь Go API — только бакет ноутбуков. Ключ пользователя release бэку не годится:
// у того member на весь проект (OpenStack и все бакеты). s3.bucket.user сам по себе не даёт ничего —
// доступ появляется только там, где пользователь назван в политике бакета (ниже, "notebooks").
// Пароль нужен API IAM, им никто не входит: бэк ходит в S3 по ключу.
const notebooksUser = new selectel.IamServiceuserV1("notebooks", {
  name: cfg.get("notebooksUserName") ?? "cellestialNotebooksUser",
  password: new random.RandomPassword("notebooks-serviceuser", serviceUserPasswordArgs).result,
  roles: [
    { roleName: "s3.bucket.user", scope: "project", projectId: project.id },
  ],
});

const notebooksCredentials = new selectel.IamS3CredentialsV1("notebooks-s3", {
  name: `${name}-notebooks`,
  userId: notebooksUser.id,
  projectId: project.id,
});

const keypair = new selectel.VpcKeypairV2("release", {
  name,
  publicKey: sshPublicKey,
  userId: serviceUser.id,
// Смена infra:sshPublicKey — замена keypair с тем же name: create-before-delete упрётся в 409
}, { ...renamedFromStudy, deleteBeforeReplace: true });

// Публичные ключи команды (infra:sshPublicKeys) кладутся root через cloud-init
// при первой загрузке, чтобы каждый заходил своим ключом ещё до первого прогона
// Ansible. Дальше доступом рулит ansible/files/authorized_keys/*.pub.
// Смена списка пересоздаёт сервер — для штатного добавления человека её не используют.
const teamSshKeys = cfg.getObject<string[]>("sshPublicKeys") ?? [];
for (const k of teamSshKeys) {
  if (/['"\n\r\\]/.test(k)) {
    throw new Error(`infra:sshPublicKeys: ключ содержит недопустимые символы: ${k.slice(0, 40)}...`);
  }
}
const userData = teamSshKeys.length > 0
  ? Buffer.from([
      "#cloud-config",
      "runcmd:",
      "  - |",
      "    mkdir -p /root/.ssh && chmod 700 /root/.ssh",
      ...teamSshKeys.map((k) => `    printf '%s\\n' '${k}' >> /root/.ssh/authorized_keys`),
      "    chmod 600 /root/.ssh/authorized_keys",
    ].join("\n") + "\n").toString("base64")
  : undefined;

const os = new openstack.Provider("selectel-project", {
  authUrl: "https://cloud.api.selcloud.ru/identity/v3",
  domainName,
  tenantId: project.id,
  // Через id: имя и пароль известны уже на preview, до создания пользователя, — invoke'и
  // (образ, внешняя сеть) шли бы от несуществующего пользователя и падали с 401.
  userName: pulumi.all([serviceUser.id, serviceUser.name]).apply(([, userName]) => userName),
  password: password.result,
  region: pool,
});

const withOs = { provider: os };

// Внешняя сеть — по имени (в Selectel external-network). pool у FloatingIp — ForceNew: имя
// берём из конфига, а не из invoke, иначе смена ответа getNetwork пересоздала бы floating IP
// (новый publicIp и A-запись), а при нескольких внешних сетях invoke без имени упал бы.
const externalNetworkName = cfg.get("externalNetwork") ?? "external-network";
const external = openstack.networking.getNetworkOutput({ name: externalNetworkName, external: true }, withOs);

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

const gatewayPort = new openstack.networking.Port("gateway", {
  name: "gateway-port",
  networkId: network.id,
  fixedIps: [{ subnetId: subnet.id }],
}, withOs);

// Boot-диск единственной VPS — под будущий Postgres (docker volume), 20 ГБ;
// enableOnlineResize: увеличение без пересоздания.
const gatewayVolume = new openstack.blockstorage.Volume("gateway", {
  name: "boot-volume-gateway",
  size: cfg.getNumber("gatewayVolumeSize") ?? 20,
  imageId: image.id,
  volumeType,
  availabilityZone: zone,
  enableOnlineResize: true,
}, { ...withOs, ignoreChanges: ["imageId"] });

// Единственная VPS: публичный IP, Caddy; позже — Go API и Postgres в Docker Compose.
// Приватная сеть/подсеть/роутер оставлены: без них к инстансу нельзя привязать floating IP.
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
  // По metadata.role dynamic inventory Ansible собирает группу gateway
  metadata: { role: "gateway", env: "release" },
  userData,
  vendorOptions: { ignoreResizeConfirmation: true },
// userData ForceNew: сервер пересоздаётся. deleteBeforeReplace — иначе up падает
// на занятом старым сервером порту/boot-диске (имена и ресурсы переиспользуются).
}, { ...withOs, deleteBeforeReplace: true, ignoreChanges: ["imageId"], dependsOn: [routerInterface] });

const floatingIp = new openstack.networking.FloatingIp("gateway", {
  pool: externalNetworkName,
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

// curl-хелперы S3 (через системное доверие macOS, секреты только через stdin) —
// общие с bootstrap-стеком, там же их тесты: selectel-s3.test.ts.
const s3KeyReadyTimeoutSeconds = cfg.getNumber("s3KeyReadyTimeoutSeconds") ?? 900;
const s3AccessKeyReady = pulumi.all([
  s3Credentials.accessKey, s3Credentials.secretKey, project.id,
]).apply(async ([accessKey, secretKey, projectId]) => {
    if (pulumi.runtime.isDryRun()) {
      return accessKey;
    }
    // Инициализация S3 — от того же пользователя, что и провайдер selectel (selectel.env)
    const creds = { ...selectelCreds, domain: domainName };
    const initStatus = await initProjectS3(curl, creds, projectId, s3Pool);
    pulumi.log.info(`S3 в проекте проинициализирован (HTTP ${initStatus})`);
    // Выданный через IAM ключ S3-шлюз признаёт не сразу: до этого CreateBucket
    // отвечает 403 InvalidAccessKeyId. Ждём, пока ключ начнёт работать.
    const attempts = await waitForS3Key(curl, {
      endpoint: s3EndpointUrl,
      pool: s3Pool,
      accessKey,
      secretKey,
      timeoutSeconds: s3KeyReadyTimeoutSeconds,
    });
    pulumi.log.info(`S3-ключ принят шлюзом (попытка ${attempts})`);
    return accessKey;
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

// Тип бакета — не S3 API (ACL Selectel не поддерживает), а API хранилища пула: selectel-storage.ts.
// Публичный бакет получает домен <uuid>.selstorage.ru — источник для CDN.
const bucketAccess = new BucketAccess("product-releases", {
  projectId: project.id,
  pool: s3Pool,
  bucket: bucket.bucket,
  type: s3Public ? "public" : "private",
}, { dependsOn: [bucket] });

// Публичное чтение через S3 API по политике бакета; включается infra:s3PublicRead=true.
// Для публичного бакета (infra:s3Public) не нужна.
// В Selectel политика бакета работает по принципу «всё, что не разрешено, запрещено» — роли
// проекта перестают действовать. Политика только с публичным GetObject отрезала бы сервисного
// пользователя стека от собственного бакета (403 уже на GetBucketPolicy сразу после создания),
// поэтому вторым правилом ему явно выдан полный доступ. Principal пользователя — его id в IAM.
if (cfg.getBoolean("s3PublicRead") ?? false) {
  new aws.s3.BucketPolicy("product-public-read", {
    bucket: bucket.id,
    policy: pulumi.all([bucket.arn, serviceUser.id]).apply(([arn, userId]) => JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Sid: "StackServiceUserFullAccess",
          Effect: "Allow",
          Principal: { AWS: [userId] },
          Action: "s3:*",
          Resource: [arn, `${arn}/*`],
        },
        {
          Sid: "PublicRead",
          Effect: "Allow",
          Principal: { AWS: ["*"] },
          Action: "s3:GetObject",
          Resource: `${arn}/*`,
        },
      ],
    })),
  }, { provider: s3 });
}

// Бакет ноутбуков пользователей. Данные пользователей: без forceDestroy и с protect — pulumi destroy
// и случайная замена ресурса не сносят бакет с объектами (снять: pulumi state unprotect).
const notebooksBucketResource = new aws.s3.Bucket("notebooks", {
  bucket: notebooksBucketName,
}, { provider: s3, protect: true });

// Тип private задаётся явно: публичного домена <uuid>.selstorage.ru у бакета нет, источником CDN
// он не служит.
new BucketAccess("notebooks", {
  projectId: project.id,
  pool: s3Pool,
  bucket: notebooksBucketResource.bucket,
  type: "private",
}, { dependsOn: [notebooksBucketResource] });

// Пользователю бэка — объекты и листинг только этого бакета; в product-releases его нет ни в какой
// политике, там он получает AccessDenied. Как и у product-public-read, с появлением политики роли
// проекта перестают действовать, поэтому первым правилом полный доступ оставлен пользователю стека
// (release) — иначе 403 уже на GetBucketPolicy, и Pulumi не сможет ни изменить, ни снять политику.
new aws.s3.BucketPolicy("notebooks", {
  bucket: notebooksBucketResource.id,
  policy: pulumi.all([notebooksBucketResource.arn, serviceUser.id, notebooksUser.id])
    .apply(([arn, stackUserId, backendUserId]) => JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Sid: "StackServiceUserFullAccess",
          Effect: "Allow",
          Principal: { AWS: [stackUserId] },
          Action: "s3:*",
          Resource: [arn, `${arn}/*`],
        },
        {
          Sid: "BackendObjects",
          Effect: "Allow",
          Principal: { AWS: [backendUserId] },
          Action: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
          Resource: `${arn}/*`,
        },
        {
          Sid: "BackendList",
          Effect: "Allow",
          Principal: { AWS: [backendUserId] },
          Action: "s3:ListBucket",
          Resource: arn,
        },
      ],
    })),
}, { provider: s3 });

// Бакет аватарок пользователей. Как и ноутбуки — данные пользователей: protect и без forceDestroy.
const avatarsBucketResource = new aws.s3.Bucket("avatars", {
  bucket: avatarsBucketName,
}, { provider: s3, protect: true });

// Тип public: аватарки отдаются без авторизации с домена <uuid>.selstorage.ru (выход
// avatarsPublicDomain). Источником CDN бакет не служит.
const avatarsAccess = new BucketAccess("avatars", {
  projectId: project.id,
  pool: s3Pool,
  bucket: avatarsBucketResource.bucket,
  type: "public",
}, { dependsOn: [avatarsBucketResource] });

// Пишет аватарки тот же пользователь Go API, что и ноутбуки (ключ notebooksAccessKey); листинг ему
// не нужен. Политика отключает роли проекта, поэтому пользователю стека явно оставлен полный доступ.
// Анонимное чтение по ключу — https://<avatarsPublicDomain>/<ключ>: его даёт тип бакета public, а не
// политика. Политика Selectel действует только на авторизованные запросы (Principal "*" — «все
// авторизованные»), поэтому правила PublicRead здесь нет: через S3 API (endpoint пула) анонимный
// запрос получает 403 при любой политике.
new aws.s3.BucketPolicy("avatars", {
  bucket: avatarsBucketResource.id,
  policy: pulumi.all([avatarsBucketResource.arn, serviceUser.id, notebooksUser.id])
    .apply(([arn, stackUserId, backendUserId]) => JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Sid: "StackServiceUserFullAccess",
          Effect: "Allow",
          Principal: { AWS: [stackUserId] },
          Action: "s3:*",
          Resource: [arn, `${arn}/*`],
        },
        {
          Sid: "BackendObjects",
          Effect: "Allow",
          Principal: { AWS: [backendUserId] },
          Action: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
          Resource: `${arn}/*`,
        },
      ],
    })),
}, { provider: s3 });

// DNS: зона домена (infra:dnsZone) лежит в проекте infra:dnsProjectId, по умолчанию — в проекте стека.
const appDomain = cfg.get("domain");
const withDot = (d: string) => (d.endsWith(".") ? d : `${d}.`);
const dnsProjectId: pulumi.Input<string> = cfg.get("dnsProjectId") ?? project.id;
// Свой провайдер selectel для DNS: его projectId — проект, где провайдер ищет импортируемые записи и
// зоны (у провайдера по умолчанию он берётся из INFRA_PROJECT_ID, иначе import падает с
// «INFRA_PROJECT_ID must be set»). Явный провайдер не читает selectel:* из конфига стека — передаём;
// логин и пароль он, как и провайдер по умолчанию, берёт из OS_* (selectel.env).
const dnsProvider = new selectel.Provider("dns", {
  projectId: dnsProjectId,
  domainName,
  authUrl: selectelCfg.get("authUrl"),
  authRegion: selectelCfg.get("authRegion"),
});
const withDns = { provider: dnsProvider };
const parentZone = appDomain || cdnCustomDomainName || s3CustomDomainName
  ? selectel.getDomainsZoneV2Output({ name: cfg.require("dnsZone"), projectId: dnsProjectId }, withDns)  // cellestial.ru.
  : undefined;

// A-запись домена → publicIp VPS
if (appDomain && parentZone) {
  new selectel.DomainsRrsetV2("app", {
    zoneId: parentZone.id,
    projectId: dnsProjectId,
    name: withDot(appDomain),
    type: "A",
    ttl: 300,
    records: [{ content: floatingIp.address }],
  // Смена projectId/zoneId — замена записи с тем же именем: сначала удалить, иначе 409
  }, { ...withDns, deleteBeforeReplace: true });
}

// CNAME своего домена в зоне infra:dnsZone — обычная запись внутри зоны, не зона-поддомен: у зоны на
// вершине CNAME невозможен, а ALIAS не принимают ни CDN, ни привязка домена бакета.
const cnameRecord = (logicalName: string, domainName: string, target: pulumi.Input<string>) =>
  new selectel.DomainsRrsetV2(logicalName, {
    zoneId: parentZone!.id,
    projectId: dnsProjectId,
    name: withDot(domainName),
    type: "CNAME",
    ttl: 300,
    records: [{ content: pulumi.output(target).apply(withDot) }],
  }, { ...withDns, deleteBeforeReplace: true });

// CDN-ресурс с публичным бакетом источником; чанки клиент грузит с cdnDefaultDomain или со своего
// домена infra:cdnDomain.
let cdn: CdnResource | undefined;
let cdnDomain: CdnDomain | undefined;
if (cdnEnabled) {
  cdn = new CdnResource("cdn", {
    projectId: project.id,
    name: checkCdnName(cfg.get("cdnName") ?? `${name}-cdn`),
    originHost: bucketAccess.publicDomain,
  });
  if (cdnCustomDomainName) {
    // Домен, который ещё не CNAME на cdnDomain, CDN API не сохраняет: сначала запись, потом привязка
    const record = cnameRecord("cdn", cdnCustomDomainName, cdn.cdnDomain);
    cdnDomain = new CdnDomain("cdn", {
      projectId: project.id,
      resourceId: cdn.id,
      domain: cdnCustomDomainName,
    }, { dependsOn: [record] });
  }
}

// Свой домен бакета релизов: CNAME на access.<пул>.storage.selcloud.ru. Сертификат выпускается в
// проекте зоны DNS: Let's Encrypt Selectel ищет зону домена в проекте токена, в проекте стека он
// отвечает 400 domain not found. В хранилище проекта стека сертификат загружает BucketDomain.
let s3Domain: BucketDomain | undefined;
if (s3CustomDomainName) {
  const record = cnameRecord("s3", s3CustomDomainName, `access.${s3Pool}.storage.selcloud.ru`);
  s3Domain = new BucketDomain("product-releases", {
    projectId: project.id,
    certProjectId: cfg.get("s3CertProjectId") ?? dnsProjectId,
    pool: s3Pool,
    bucket: bucket.bucket,
    domain: s3CustomDomainName,
    certName: s3CustomDomainName.replace(/\./g, "-"),
  }, { dependsOn: [record, bucketAccess] });
}

export const projectId = project.id;
export const publicIp = floatingIp.address;
export const gatewayName = serverGateway.name;
export const sshUser = cfg.get("sshUser") ?? "deploy";
export const domain = appDomain ?? null;
export const s3Endpoint = s3EndpointUrl;
export const s3Bucket = bucket.bucket;
export const s3PublicDomain = bucketAccess.publicDomain;
// Свои домены и состояние их сертификатов: у бакета ACTIVE и непустая версия в хранилище, у CDN processed
export const s3CustomDomain = s3CustomDomainName ?? null;
export const s3CertificateStatus = s3Domain?.certificateStatus ?? null;
export const s3CertificateUploadedVersion = s3Domain?.uploadedVersion ?? null;
export const cdnCustomDomain = cdnCustomDomainName ?? null;
export const cdnCertificateStatus = cdnDomain?.certificateStatus ?? null;
export const cdnResourceId = cdn?.id ?? null;
export const cdnDefaultDomain = cdn?.cdnDomain ?? null;
export const serviceUserName = serviceUser.name;
export const serviceUserPassword = pulumi.secret(password.result);
export const s3AccessKey = s3Credentials.accessKey;
export const s3SecretKey = pulumi.secret(s3Credentials.secretKey);
// Ключ Go API к бакету ноутбуков; endpoint и регион — s3Endpoint и infra:s3Pool
export const notebooksBucket = notebooksBucketResource.bucket;
export const notebooksAccessKey = notebooksCredentials.accessKey;
export const notebooksSecretKey = pulumi.secret(notebooksCredentials.secretKey);
// Бакет аватарок: пишет тот же ключ Go API, публичный URL — https://<avatarsPublicDomain>/<ключ>
export const avatarsBucket = avatarsBucketResource.bucket;
export const avatarsPublicDomain = avatarsAccess.publicDomain;
