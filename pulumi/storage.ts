import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import * as selectel from "@pulumi/selectel";
import * as aws from "@pulumi/aws";
import { InfraConfig, serviceUserPasswordArgs } from "./config";
import { Foundation } from "./foundation";
import { BucketAccess, CdnResource } from "./selectel-storage";

export interface Storage {
  releasesBucket: aws.s3.Bucket;
  releasesAccess: BucketAccess;
  avatarsCredentials: selectel.IamS3CredentialsV1;
  avatarsBucket: aws.s3.Bucket;
  avatarsAccess: BucketAccess;
  staticBucket?: aws.s3.Bucket;
  staticAccess?: BucketAccess;
  cdn?: CdnResource;
  staticCdn?: CdnResource;
}

export function createStorage(c: InfraConfig, f: Foundation): Storage {
  const { project, serviceUser, s3 } = f;

  // Пользователь Go API для аватарок: s3.bucket.user сам по себе ничего не даёт — доступ только к
  // объектам бакета аватарок по его политике (ниже, "avatars"). Пароль нужен API IAM, им никто не входит.
  const avatarsUser = new selectel.IamServiceuserV1("avatars", {
    name: "cellestialAvatarsUser",
    password: new random.RandomPassword("avatars-serviceuser", serviceUserPasswordArgs).result,
    roles: [
      { roleName: "s3.bucket.user", scope: "project", projectId: project.id },
    ],
  });

  const avatarsCredentials = new selectel.IamS3CredentialsV1("avatars-s3", {
    name: `${c.name}-avatars`,
    userId: avatarsUser.id,
    projectId: project.id,
  });

  // forceDestroy: true — pulumi destroy удаляет бакет вместе с объектами
  const releasesBucket = new aws.s3.Bucket("product-releases", {
    bucket: c.s3.releasesBucket,
    forceDestroy: true,
  }, { provider: s3 });

  // Тип бакета — не S3 API (ACL Selectel не поддерживает), а API хранилища пула: selectel-storage.ts.
  // Публичный бакет получает домен <uuid>.selstorage.ru — источник для CDN.
  const releasesAccess = new BucketAccess("product-releases", {
    projectId: project.id,
    pool: c.s3.pool,
    bucket: releasesBucket.bucket,
    type: c.s3.public ? "public" : "private",
  }, { dependsOn: [releasesBucket] });

  // Публичное чтение через S3 API по политике бакета; включается infra:s3PublicRead=true.
  // Для публичного бакета (infra:s3Public) не нужна.
  // В Selectel политика бакета работает по принципу «всё, что не разрешено, запрещено» — роли
  // проекта перестают действовать. Политика только с публичным GetObject отрезала бы сервисного
  // пользователя стека от собственного бакета (403 уже на GetBucketPolicy сразу после создания),
  // поэтому вторым правилом ему явно выдан полный доступ. Principal пользователя — его id в IAM.
  if (c.s3.publicRead) {
    new aws.s3.BucketPolicy("product-public-read", {
      bucket: releasesBucket.id,
      policy: pulumi.all([releasesBucket.arn, serviceUser.id]).apply(([arn, userId]) => JSON.stringify({
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

  // Бакет аватарок пользователей. Данные пользователей: без forceDestroy и с protect — pulumi destroy
  // и случайная замена ресурса не сносят бакет с объектами (снять: pulumi state unprotect).
  const avatarsBucket = new aws.s3.Bucket("avatars", {
    bucket: c.s3.avatarsBucket,
  }, { provider: s3, protect: true });

  // Тип public: аватарки отдаются без авторизации с домена <uuid>.selstorage.ru (выход
  // avatarsPublicDomain). Источником CDN бакет не служит.
  const avatarsAccess = new BucketAccess("avatars", {
    projectId: project.id,
    pool: c.s3.pool,
    bucket: avatarsBucket.bucket,
    type: "public",
  }, { dependsOn: [avatarsBucket] });

  // Пишет аватарки пользователь avatars (ключ avatarsAccessKey); листинг ему не нужен. Политика отключает роли проекта, поэтому пользователю стека явно оставлен полный доступ.
  // Анонимное чтение по ключу — https://<avatarsPublicDomain>/<ключ>: его даёт тип бакета public, а не
  // политика. Политика Selectel действует только на авторизованные запросы (Principal "*" — «все
  // авторизованные»), поэтому правила PublicRead здесь нет: через S3 API (endpoint пула) анонимный
  // запрос получает 403 при любой политике.
  new aws.s3.BucketPolicy("avatars", {
    bucket: avatarsBucket.id,
    policy: pulumi.all([avatarsBucket.arn, serviceUser.id, avatarsUser.id])
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

  // Бакет статики продукта. Содержимое выкладывает CI репозитория static и может выложить заново,
  // поэтому forceDestroy, как у бакета релизов. Политики нет: пишет пользователь стека по роли member.
  const staticBucket = c.s3.staticBucket
    ? new aws.s3.Bucket("static", { bucket: c.s3.staticBucket, forceDestroy: true }, { provider: s3 })
    : undefined;
  // Тип public: публичный домен <uuid>.selstorage.ru (выход staticPublicDomain) — источник CDN статики.
  const staticAccess = staticBucket
    ? new BucketAccess("static", {
        projectId: project.id,
        pool: c.s3.pool,
        bucket: staticBucket.bucket,
        type: "public",
      }, { dependsOn: [staticBucket] })
    : undefined;

  // CDN-ресурс с публичным бакетом источником; чанки клиент грузит с cdnDefaultDomain или со своего
  // домена infra:cdnDomain (DNS-запись и привязка — domains.ts).
  const cdn = c.s3.cdnName
    ? new CdnResource("cdn", {
        projectId: project.id,
        name: c.s3.cdnName,
        originHost: releasesAccess.publicDomain,
      })
    : undefined;

  // Второй CDN-ресурс — с бакетом статики источником; свой домен infra:staticDomain привязывается так
  // же, как infra:cdnDomain.
  const staticCdn = staticAccess && c.s3.staticCdnName
    ? new CdnResource("static-cdn", {
        projectId: project.id,
        name: c.s3.staticCdnName,
        originHost: staticAccess.publicDomain,
      })
    : undefined;

  return {
    releasesBucket,
    releasesAccess,
    avatarsCredentials,
    avatarsBucket,
    avatarsAccess,
    staticBucket,
    staticAccess,
    cdn,
    staticCdn,
  };
}
