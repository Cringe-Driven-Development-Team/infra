import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import * as openstack from "@pulumi/openstack";
import * as selectel from "@pulumi/selectel";
import * as aws from "@pulumi/aws";
import { curl, initProjectS3 } from "./bootstrap/selectel-s3";
import { flavorId, InfraConfig, serviceUserPasswordArgs } from "./config";
import { Foundation } from "./foundation";
import { BucketAccess } from "./selectel-storage";

export interface Auxiliary {
  floatingIp: openstack.networking.FloatingIp;
  backupBucket: aws.s3.Bucket;
  backupCredentials: selectel.IamS3CredentialsV1;
}

// ---------------------------------------------------------------------------
// Вспомогательная VPS для сервиса вебхуков (infra#54). Включается infra:auxiliary.
// Та же приватная сеть и роутер, что у gateway, но свои порт, диск, floating IP и security group:
// прод-ресурсы не меняются. Docker публикует порты в обход ufw, поэтому наружу порты закрывает
// security group на порту VPS — только 22/80/443; порт сервиса снаружи не виден.
// A-запись домена вебхуков — domains.ts.
// ---------------------------------------------------------------------------
export function createAuxiliary(c: InfraConfig, f: Foundation): Auxiliary | undefined {
  const aux = c.auxiliary;
  if (!aux) {
    return undefined;
  }
  const withOs = { provider: f.os };
  const secGroup = new openstack.networking.SecGroup("auxiliary", {
    name: "auxiliary",
    description: "Auxiliary VPS: only SSH, HTTP, HTTPS and ICMP from outside",
  }, withOs);
  const ingress: [string, number | undefined][] = [["tcp", 22], ["tcp", 80], ["tcp", 443], ["icmp", undefined]];
  for (const [protocol, port] of ingress) {
    new openstack.networking.SecGroupRule(`auxiliary-${protocol}${port ?? ""}`, {
      securityGroupId: secGroup.id,
      direction: "ingress",
      ethertype: "IPv4",
      protocol,
      ...(port ? { portRangeMin: port, portRangeMax: port } : {}),
      remoteIpPrefix: "0.0.0.0/0",
    }, withOs);
  }

  const port = new openstack.networking.Port("auxiliary", {
    name: "auxiliary-port",
    networkId: f.network.id,
    fixedIps: [{ subnetId: f.subnet.id }],
    securityGroupIds: [secGroup.id],
  }, withOs);

  // 40 ГБ: образы Docker и тома сервиса; enableOnlineResize — увеличение без пересоздания.
  const volume = new openstack.blockstorage.Volume("auxiliary", {
    name: "boot-volume-auxiliary",
    size: aux.volumeSize,
    imageId: f.imageId,
    volumeType: c.volumeType,
    availabilityZone: c.zone,
    enableOnlineResize: true,
  }, { ...withOs, ignoreChanges: ["imageId"] });

  new openstack.compute.Instance("auxiliary", {
    name: "pulumi-server-auxiliary",
    flavorId: flavorId(aux.flavor, f.os),
    keyPair: f.keypair.name,
    availabilityZone: c.zone,
    networks: [{ port: port.id }],
    blockDevices: [{
      sourceType: "volume",
      destinationType: "volume",
      uuid: volume.id,
      bootIndex: 0,
      deleteOnTermination: false,
    }],
    // По metadata.role dynamic inventory Ansible собирает группу auxiliary
    metadata: { role: "auxiliary", env: "release" },
    userData: c.userData,
    vendorOptions: { ignoreResizeConfirmation: true },
  }, { ...withOs, deleteBeforeReplace: true, ignoreChanges: ["imageId"], dependsOn: [f.routerInterface] });

  const floatingIp = new openstack.networking.FloatingIp("auxiliary", {
    pool: c.externalNetworkName,
  }, withOs);
  new openstack.networking.FloatingIpAssociate("auxiliary", {
    portId: port.id,
    floatingIp: floatingIp.address,
  }, { ...withOs, dependsOn: [f.routerInterface] });

  // Бэкапы restic с VPS: приватный бакет и свой пользователь, как у бакета ноутбуков. Ключ этого
  // пользователя лежит на VPS — доступ у него только к этому бакету (по политике).
  const backupUser = new selectel.IamServiceuserV1("auxiliary-backup", {
    name: aux.backupUserName,
    password: new random.RandomPassword("auxiliary-backup-serviceuser", serviceUserPasswordArgs).result,
    roles: [
      { roleName: "s3.bucket.user", scope: "project", projectId: f.project.id },
    ],
  });
  const backupCredentials = new selectel.IamS3CredentialsV1("auxiliary-backup-s3", {
    name: `${c.name}-auxiliary-backup`,
    userId: backupUser.id,
    projectId: f.project.id,
  });
  // Пул бакета бэкапов — infra:auxiliaryBackupPool (по умолчанию infra:s3Pool): в ru-7 Selectel
  // временно не создаёт бакеты (AccessDenied «Bucket creation is temporarily denied in this region»).
  // S3-ключ выдан на проект, а не на пул — провайдер другого пула работает тем же ключом стека.
  const backupS3 = aux.backupPool === c.s3.pool ? f.s3 : new aws.Provider("selectel-s3-auxiliary-backups", {
    region: aux.backupPool,
    accessKey: pulumi.all([f.s3AccessKeyReady, f.project.id]).apply(async ([accessKey, projectId]) => {
      if (!pulumi.runtime.isDryRun()) {
        await initProjectS3(curl, { ...c.selectelCreds, domain: c.domainName }, projectId, aux.backupPool);
      }
      return accessKey;
    }),
    secretKey: f.s3Credentials.secretKey,
    endpoints: [{ s3: `https://s3.${aux.backupPool}.storage.selcloud.ru` }],
    s3UsePathStyle: true,
    skipCredentialsValidation: true,
    skipRegionValidation: true,
    skipRequestingAccountId: true,
    skipMetadataApiCheck: true,
  });
  // Резервные копии: без forceDestroy и с protect, как данные пользователей.
  const backupBucket = new aws.s3.Bucket("auxiliary-backups", {
    bucket: aux.backupBucket,
  }, { provider: backupS3, protect: true });
  new BucketAccess("auxiliary-backups", {
    projectId: f.project.id,
    pool: aux.backupPool,
    bucket: backupBucket.bucket,
    type: "private",
  }, { dependsOn: [backupBucket] });
  // Первым правилом s3:* пользователю стека — иначе политика отрежет его самого (см. "notebooks").
  new aws.s3.BucketPolicy("auxiliary-backups", {
    bucket: backupBucket.id,
    policy: pulumi.all([backupBucket.arn, f.serviceUser.id, backupUser.id])
      .apply(([arn, stackUserId, backupUserId]) => JSON.stringify({
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
            Sid: "ResticObjects",
            Effect: "Allow",
            Principal: { AWS: [backupUserId] },
            Action: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
            Resource: `${arn}/*`,
          },
          {
            Sid: "ResticList",
            Effect: "Allow",
            Principal: { AWS: [backupUserId] },
            Action: ["s3:ListBucket", "s3:GetBucketLocation"],
            Resource: arn,
          },
        ],
      })),
  }, { provider: backupS3 });

  return { floatingIp, backupBucket, backupCredentials };
}
