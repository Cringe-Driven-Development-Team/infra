import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import * as openstack from "@pulumi/openstack";
import * as selectel from "@pulumi/selectel";
import * as aws from "@pulumi/aws";
import { curl, initProjectS3, waitForS3Key } from "./bootstrap/selectel-s3";
import { imageId, InfraConfig, serviceUserPasswordArgs } from "./config";

export interface Foundation {
  project: selectel.VpcProjectV2;
  serviceUser: selectel.IamServiceuserV1;
  serviceUserPassword: random.RandomPassword;
  s3Credentials: selectel.IamS3CredentialsV1;
  // accessKey ключа стека, отданный после инициализации S3 и готовности ключа (на up)
  s3AccessKeyReady: pulumi.Output<string>;
  s3: aws.Provider;
  os: openstack.Provider;
  keypair: selectel.VpcKeypairV2;
  network: openstack.networking.Network;
  subnet: openstack.networking.Subnet;
  routerInterface: openstack.networking.RouterInterface;
  imageId: pulumi.Output<string>;
}

export function createFoundation(c: InfraConfig): Foundation {
  const renamedFromStudy = { aliases: [{ name: "study" }] };

  const project = new selectel.VpcProjectV2("release", { name: c.name }, renamedFromStudy);

  const password = new random.RandomPassword("serviceuser", serviceUserPasswordArgs);

  const serviceUser = new selectel.IamServiceuserV1("release", {
    name: c.serviceUserName,
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
    name: `${c.name}-releases`,
    userId: serviceUser.id,
    projectId: project.id,
  });

  const keypair = new selectel.VpcKeypairV2("release", {
    name: c.name,
    publicKey: c.sshPublicKey,
    userId: serviceUser.id,
  // Смена infra:sshPublicKey — замена keypair с тем же name: create-before-delete упрётся в 409
  }, { ...renamedFromStudy, deleteBeforeReplace: true });

  const os = new openstack.Provider("selectel-project", {
    authUrl: "https://cloud.api.selcloud.ru/identity/v3",
    domainName: c.domainName,
    tenantId: project.id,
    // Через id: имя и пароль известны уже на preview, до создания пользователя, — invoke'и
    // (образ, внешняя сеть) шли бы от несуществующего пользователя и падали с 401.
    userName: pulumi.all([serviceUser.id, serviceUser.name]).apply(([, userName]) => userName),
    password: password.result,
    region: c.pool,
  });

  const withOs = { provider: os };

  const external = openstack.networking.getNetworkOutput({ name: c.externalNetworkName, external: true }, withOs);

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

  // ---------------------------------------------------------------------------
  // S3 в проекте продукта. Отдельного ресурса «включить S3 в проекте» нет:
  // хранилище проекта в пуле появляется с первым бакетом, созданным через
  // эндпоинт этого пула ключом, выданным на этот проект.
  // Провайдер Selectel бакеты не создаёт — только S3 API (@pulumi/aws), как в
  // документации Selectel по Terraform.
  // ---------------------------------------------------------------------------

  // curl-хелперы S3 (через системное доверие macOS, секреты только через stdin) —
  // общие с bootstrap-стеком, там же их тесты: selectel-s3.test.ts.
  const s3AccessKeyReady = pulumi.all([
    s3Credentials.accessKey, s3Credentials.secretKey, project.id,
  ]).apply(async ([accessKey, secretKey, projectId]) => {
    if (pulumi.runtime.isDryRun()) {
      return accessKey;
    }
    // Инициализация S3 — от того же пользователя, что и провайдер selectel (selectel.env)
    const creds = { ...c.selectelCreds, domain: c.domainName };
    const initStatus = await initProjectS3(curl, creds, projectId, c.s3.pool);
    pulumi.log.info(`S3 в проекте проинициализирован (HTTP ${initStatus})`);
    // Выданный через IAM ключ S3-шлюз признаёт не сразу: до этого CreateBucket
    // отвечает 403 InvalidAccessKeyId. Ждём, пока ключ начнёт работать.
    const attempts = await waitForS3Key(curl, {
      endpoint: c.s3.endpoint,
      pool: c.s3.pool,
      accessKey,
      secretKey,
      timeoutSeconds: c.s3.keyReadyTimeoutSeconds,
    });
    pulumi.log.info(`S3-ключ принят шлюзом (попытка ${attempts})`);
    return accessKey;
  });

  const s3 = new aws.Provider("selectel-s3-product", {
    region: c.s3.pool,                       // регион подписи = пул
    accessKey: s3AccessKeyReady,
    secretKey: s3Credentials.secretKey,
    endpoints: [{ s3: c.s3.endpoint }],      // https://s3.<пул>.storage.selcloud.ru
    s3UsePathStyle: true,
    // Selectel — не AWS: те же skip_*, что в документации Selectel для Terraform
    skipCredentialsValidation: true,
    skipRegionValidation: true,
    skipRequestingAccountId: true,
    skipMetadataApiCheck: true,
  });

  return {
    project,
    serviceUser,
    serviceUserPassword: password,
    s3Credentials,
    s3AccessKeyReady,
    s3,
    os,
    keypair,
    network,
    subnet,
    routerInterface,
    imageId: imageId(c, os),
  };
}
