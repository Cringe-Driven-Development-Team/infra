import * as pulumi from "@pulumi/pulumi";
import * as openstack from "@pulumi/openstack";
import * as random from "@pulumi/random";
import { Credentials, credentialsFromEnv } from "./bootstrap/selectel-s3";
import { checkCdnName } from "./selectel-storage";

// Пароли сервисных пользователей IAM: им никто не входит, но API требует сложность
export const serviceUserPasswordArgs: random.RandomPasswordArgs = {
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

// Флейворы: id в панели не показывается, а имена ("SL1.2-4096") есть не во всех пулах —
// getFlavor по несуществующему имени падает с "Your query returned no results".
// Порядок: infra:<role>FlavorId → infra:<role>FlavorName → поиск по vcpus/ram(/disk).
// Список доступных имён: ./scripts/list-flavors.sh
export type FlavorRule = { id: string } | { name: string } | { vcpus: number; ram: number; disk?: number };

export interface InfraConfig {
  selectelCreds: Credentials;
  domainName: string;
  authUrl: string;
  authRegion: string;
  name: string;
  serviceUserName: string;
  pool: string;
  zone: string;
  volumeType: string;
  imageName: string;
  sshPublicKey: string;
  sshUser: string;
  userData: string | undefined;
  externalNetworkName: string;
  gateway: { flavor: FlavorRule; volumeSize: number };
  backend?: { flavor: FlavorRule; volumeSize: number };
  auxiliary?: {
    flavor: FlavorRule;
    volumeSize: number;
    backupBucket: string;
    backupPool: string;
    backupUserName: string;
  };
  runtime?: { flavor: FlavorRule; imageId: string; userName: string; volumeType: string };
  s3: {
    pool: string;
    endpoint: string;
    keyReadyTimeoutSeconds: number;
    releasesBucket: string;
    public: boolean;
    publicRead: boolean;
    avatarsBucket: string;
    staticBucket?: string;
    // Задан ⇔ infra:cdn; CDN-ресурс статики — при infra:staticBucket
    cdnName?: string;
    staticCdnName?: string;
  };
  dns: {
    zone?: string;
    projectId?: string;
    appDomain?: string;
    // Только при infra:auxiliary: A-запись на её floating IP
    hooksDomain?: string;
    cdnDomain?: string;
    avatarsDomain?: string;
    staticDomain?: string;
    docsDomain?: string;
    githubPagesChallenge?: string;
  };
}

function readFlavor(cfg: pulumi.Config, role: string): FlavorRule {
  const id = cfg.get(`${role}FlavorId`);
  if (id) {
    return { id };
  }
  const name = cfg.get(`${role}FlavorName`);
  if (name) {
    return { name };
  }
  const disk = cfg.getNumber(`${role}Disk`);
  return {
    vcpus: cfg.requireNumber(`${role}Vcpus`),
    ram: cfg.requireNumber(`${role}Ram`),
    ...(disk ? { disk } : {}),
  };
}

export function flavorId(rule: FlavorRule, provider: openstack.Provider): pulumi.Input<string> {
  if ("id" in rule) {
    return rule.id;
  }
  return openstack.compute.getFlavorOutput(rule, { provider }).id;
}

export function imageId(c: InfraConfig, provider: openstack.Provider): pulumi.Output<string> {
  return openstack.images.getImageOutput({
    name: c.imageName,
    mostRecent: true,
    visibility: "public",
  }, { provider }).id;
}

// Публичные ключи команды (infra:sshPublicKeys) кладутся root через cloud-init
// при первой загрузке, чтобы каждый заходил своим ключом ещё до первого прогона
// Ansible. Дальше доступом рулит ansible/files/authorized_keys/*.pub.
// Смена списка пересоздаёт сервер — для штатного добавления человека её не используют.
function cloudInit(teamSshKeys: string[]): string | undefined {
  for (const k of teamSshKeys) {
    if (/['"\n\r\\]/.test(k)) {
      throw new Error(`infra:sshPublicKeys: ключ содержит недопустимые символы: ${k.slice(0, 40)}...`);
    }
  }
  return teamSshKeys.length > 0
    ? Buffer.from([
        "#cloud-config",
        "runcmd:",
        "  - |",
        "    mkdir -p /root/.ssh && chmod 700 /root/.ssh",
        ...teamSshKeys.map((k) => `    printf '%s\\n' '${k}' >> /root/.ssh/authorized_keys`),
        "    chmod 600 /root/.ssh/authorized_keys",
      ].join("\n") + "\n").toString("base64")
    : undefined;
}

export function loadConfig(): InfraConfig {
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

  // Имя уровня аккаунта (проект, keypair). Аккаунт общий на курс,
  // поэтому переопределяется через infra:name. Логические имена были "study" — aliases
  // сохраняют связь со стейтом, чтобы переименование не пересоздавало ресурсы.
  const name = cfg.get("name") ?? "pulumi-release";

  // Объектное хранилище: пул (например ru-1) задаёт endpoint s3.<pool>.storage.selcloud.ru
  // и region подписи. Имя бакета глобально уникально в рамках аккаунта.
  const s3Pool = cfg.require("s3Pool");
  const s3Bucket = cfg.require("s3Bucket");
  // Публичный бакет аватарок пользователей — в том же пуле; пишет Go API, читают все.
  const avatarsBucket = cfg.require("avatarsBucket");
  // Публичный бакет статики продукта (картинки, шрифты) — в том же пуле; пишет CI репозитория static
  // ключом пользователя стека, отдаётся через свой CDN-ресурс. Не задан — бакета и его CDN нет.
  const staticBucket = cfg.get("staticBucket");
  // CDN-ресурс с бакетом источником; технический домен — <id>.selcdn.net.
  const cdnEnabled = cfg.getBoolean("cdn") ?? false;
  // Свои домены CDN-ресурса и бакета аватарок (например cdn.cellestial.ru, avatars.cellestial.ru) и их
  // привязка (CdnDomain, BucketDomain): у CDN — CNAME в зоне infra:dnsZone, у аватарок — своя зона DNS.
  // Сертификаты доменов выпускают в панели. У бакета релизов своего домена нет.
  const cdnDomain = cfg.get("cdnDomain");
  const avatarsDomain = cfg.get("avatarsDomain");
  // Свой домен CDN-ресурса статики (static.cellestial.ru): CNAME в зоне infra:dnsZone, как у infra:cdnDomain.
  const staticDomain = cfg.get("staticDomain");
  // Приватная VPS2 (infra#59) — Go API, Postgres и Redis без публичного IP; доступ только с gateway.
  const backendEnabled = cfg.getBoolean("backend") ?? false;
  // Вспомогательная VPS (infra#54) — сервис вебхуков; его домен — A-запись в infra:dnsZone.
  const auxiliaryEnabled = cfg.getBoolean("auxiliary") ?? false;
  const hooksDomain = cfg.get("hooksDomain");
  if (staticDomain && !staticBucket) {
    throw new Error("infra:staticDomain задан без infra:staticBucket: домен привязывается к CDN-ресурсу бакета статики");
  }
  if (cdnDomain && !cdnEnabled) {
    throw new Error("infra:cdnDomain задан без infra:cdn: домен привязывается к CDN-ресурсу");
  }
  // Свой домен сайта документации (docs.cellestial.ru): сайт собирает и отдаёт GitHub Pages репозитория
  // docs, в зоне infra:dnsZone — только CNAME на Pages. Сертификат выпускает GitHub.
  const docsDomain = cfg.get("docsDomain");
  // Значение TXT-записи, которой GitHub подтверждает домен infra:dnsZone для организации (организация →
  // Settings → Pages → Add a domain). Не секрет: значение публично в DNS.
  const githubPagesChallenge = cfg.get("githubPagesChallenge");
  const appDomain = cfg.get("domain");

  // Runtime-VPS пользовательских сессий (infra#89, backend#57). Включается infra:runtime (нужен infra:backend).
  const runtimeEnabled = cfg.getBoolean("runtime") ?? false;
  if (runtimeEnabled && !backendEnabled) {
    throw new Error("infra:runtime задан без infra:backend: runtime-VPS пускают SSH и TLS только с VPS2");
  }
  const volumeType = cfg.require("volumeType");      // например fast.ru-9a

  return {
    selectelCreds,
    // Номер аккаунта: infra:domainName → selectel:domainName → OS_DOMAIN_NAME из selectel.env
    domainName: cfg.get("domainName") ?? selectelCfg.get("domainName") ?? selectelCreds.domain,
    authUrl: selectelCfg.require("authUrl"),
    authRegion: selectelCfg.require("authRegion"),
    name,
    // Имя сервисного пользователя проекта отдельно от infra:name (проект/keypair),
    // оно видно в панели IAM и используется как логин OpenStack.
    serviceUserName: cfg.get("serviceUserName") ?? "cellestialSystemUser",
    pool: cfg.require("pool"),                       // пул VPS, например ru-9
    zone: cfg.require("zone"),                       // например ru-9a
    volumeType,
    imageName: cfg.require("imageName"),
    sshPublicKey: cfg.require("sshPublicKey"),
    sshUser: cfg.get("sshUser") ?? "deploy",
    userData: cloudInit(cfg.getObject<string[]>("sshPublicKeys") ?? []),
    // Внешняя сеть — по имени (в Selectel external-network). pool у FloatingIp — ForceNew: имя
    // берём из конфига, а не из invoke, иначе смена ответа getNetwork пересоздала бы floating IP
    // (новый publicIp и A-запись), а при нескольких внешних сетях invoke без имени упал бы.
    externalNetworkName: cfg.get("externalNetwork") ?? "external-network",
    gateway: { flavor: readFlavor(cfg, "gateway"), volumeSize: cfg.getNumber("gatewayVolumeSize") ?? 20 },
    backend: backendEnabled
      ? { flavor: readFlavor(cfg, "backend"), volumeSize: cfg.getNumber("backendVolumeSize") ?? 40 }
      : undefined,
    auxiliary: auxiliaryEnabled
      ? {
          flavor: readFlavor(cfg, "auxiliary"),
          volumeSize: cfg.getNumber("auxiliaryVolumeSize") ?? 40,
          backupBucket: cfg.require("auxiliaryBackupBucket"),
          backupPool: cfg.get("auxiliaryBackupPool") ?? s3Pool,
          backupUserName: cfg.get("auxiliaryBackupUserName") ?? "cellestialAuxiliaryBackupUser",
        }
      : undefined,
    runtime: runtimeEnabled
      ? {
          flavor: readFlavor(cfg, "runtime"),
          // Собственный образ runtime-VPS (runtime-image/, infra#134) по фиксированному id; тот же —
          // runtime_image_id в vars.yml (infra#130). Менять оба в одном PR.
          imageId: cfg.require("runtimeImageId"),
          userName: cfg.get("runtimeUserName") ?? "cellestialRuntimeProvisioner",
          volumeType: cfg.get("runtimeVolumeType") ?? volumeType,
        }
      : undefined,
    s3: {
      pool: s3Pool,
      endpoint: `https://s3.${s3Pool}.storage.selcloud.ru`,
      keyReadyTimeoutSeconds: cfg.getNumber("s3KeyReadyTimeoutSeconds") ?? 900,
      releasesBucket: s3Bucket,
      // Тип бакета Selectel: public (по умолчанию) — чтение объектов без авторизации, источник для CDN.
      public: cfg.getBoolean("s3Public") ?? true,
      publicRead: cfg.getBoolean("s3PublicRead") ?? false,
      avatarsBucket,
      staticBucket,
      cdnName: cdnEnabled ? checkCdnName(cfg.get("cdnName") ?? `${name}-cdn`) : undefined,
      staticCdnName: staticBucket ? checkCdnName(cfg.get("staticCdnName") ?? `${name}-static-cdn`) : undefined,
    },
    dns: {
      // Зона нужна, только если есть записи в infra:dnsZone (cellestial.ru.)
      zone: appDomain || hooksDomain || cdnDomain || staticDomain || docsDomain || githubPagesChallenge
        ? cfg.require("dnsZone")
        : undefined,
      // Зона домена (infra:dnsZone) лежит в проекте infra:dnsProjectId, по умолчанию — в проекте стека.
      projectId: cfg.get("dnsProjectId"),
      appDomain,
      hooksDomain: auxiliaryEnabled ? hooksDomain : undefined,
      cdnDomain,
      avatarsDomain,
      staticDomain,
      docsDomain,
      githubPagesChallenge,
    },
  };
}
