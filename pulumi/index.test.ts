import { beforeAll, describe, expect, mock, test } from "bun:test";
import * as pulumi from "@pulumi/pulumi";
import * as storage from "./selectel-storage";

// Dynamic-ресурсы хранилища bun сериализовать не умеет («Function serialization is not supported»):
// в этом тесте они — обычные ресурсы с теми же выходами. Функции модуля — настоящие: подмена модуля
// видна и другим файлам тестов в том же процессе bun (selectel-storage.test.ts).
mock.module("./selectel-storage", () => {
  class Fake extends pulumi.CustomResource {
    declare readonly publicDomain: pulumi.Output<string>;
    declare readonly cdnDomain: pulumi.Output<string>;
    constructor(kind: string, name: string, args: object, opts?: pulumi.CustomResourceOptions) {
      super(`test:storage:${kind}`, name, { ...args, publicDomain: undefined, cdnDomain: undefined }, opts);
    }
  }
  const of = (kind: string) => class extends Fake {
    constructor(name: string, args: object, opts?: pulumi.CustomResourceOptions) { super(kind, name, args, opts); }
  };
  return {
    ...storage,
    BucketAccess: of("BucketAccess"),
    BucketDomain: of("BucketDomain"),
    CdnDomain: of("CdnDomain"),
    CdnResource: of("CdnResource"),
  };
});

// Программа стека на моках: проверяется раздел runtime-VPS (infra#89). Сеть не трогается —
// режим preview, побочные эффекты под isDryRun пропускаются.
const created = new Map<string, { type: string; inputs: Record<string, any> }>();

Object.assign(process.env, { OS_USERNAME: "u", OS_PASSWORD: "p", OS_DOMAIN_NAME: "123456" });
delete process.env.OS_PROJECT_NAME;
pulumi.runtime.setAllConfig({
  "selectel:domainName": "123456",
  "selectel:authUrl": "https://cloud.api.selcloud.ru/identity/v3/",
  "selectel:authRegion": "ru-9",
  "infra:name": "pulumi-test",
  "infra:pool": "ru-9",
  "infra:zone": "ru-9a",
  "infra:volumeType": "fast.ru-9a",
  "infra:gatewayFlavorName": "SL1.2-8192",
  "infra:imageName": "Ubuntu 24.04 LTS 64-bit",
  "infra:sshPublicKey": "ssh-ed25519 AAAA test",
  "infra:s3Pool": "ru-7",
  "infra:s3Bucket": "releases",
  "infra:notebooksBucket": "notebooks",
  "infra:avatarsBucket": "avatars",
  "infra:backend": "true",
  "infra:backendFlavorName": "SL1.2-4096",
  "infra:auxiliary": "true",
  "infra:auxiliaryFlavorName": "SL1.2-4096",
  "infra:auxiliaryBackupBucket": "backups",
  "infra:runtime": "true",
  "infra:runtimeFlavorName": "SL1.1-2048",
  "infra:runtimeVolumeType": "basicssd.ru-9a",
});

const fixedIps: Record<string, string> = { gateway: "192.168.199.225", backend: "192.168.199.10", auxiliary: "192.168.199.30" };
const floating: Record<string, string> = { gateway: "203.0.113.1", auxiliary: "203.0.113.2" };

pulumi.runtime.setMocks(
  {
    newResource(args) {
      created.set(`${args.type}::${args.name}`, { type: args.type, inputs: args.inputs });
      const state: Record<string, unknown> = { ...args.inputs };
      if (args.type === "openstack:networking/port:Port") state.allFixedIps = [fixedIps[args.name]];
      if (args.type === "openstack:networking/floatingIp:FloatingIp") state.address = floating[args.name];
      if (args.type === "openstack:networking/subnet:Subnet") state.cidr = args.inputs.cidr;
      if (args.type === "random:index/randomPassword:RandomPassword") state.result = `${args.name}-password`;
      if (args.type === "random:index/randomBytes:RandomBytes") state.base64 = "A".repeat(43) + "=";
      if (args.type === "tls:index/privateKey:PrivateKey") {
        state.privateKeyPem = `${args.name}-pem`;
        state.privateKeyPemPkcs8 = `${args.name}-pkcs8`;
        state.privateKeyOpenssh = `${args.name}-openssh`;
        state.publicKeyOpenssh = `ssh-ed25519 ${args.name}\n`;
      }
      if (args.type === "tls:index/selfSignedCert:SelfSignedCert") state.certPem = "ca-cert";
      if (args.type === "tls:index/certRequest:CertRequest") state.certRequestPem = "client-csr";
      if (args.type === "tls:index/locallySignedCert:LocallySignedCert") state.certPem = "client-cert";
      if (args.type.startsWith("aws:s3/bucket:Bucket")) state.arn = `arn:${args.inputs.bucket}`;
      if (args.type === "selectel:index/iamS3CredentialsV1:IamS3CredentialsV1") {
        state.accessKey = "AK";
        state.secretKey = "SK";
      }
      if (args.type.includes("BucketAccess")) state.publicDomain = `${args.name}.selstorage.ru`;
      if (args.type.includes("CdnResource")) state.cdnDomain = `${args.name}.selcdn.net`;
      return { id: `${args.name}-id`, state };
    },
    call(args) {
      if (args.token === "openstack:images/getImage:getImage") return { id: `image:${args.inputs.name}` };
      if (args.token === "openstack:compute/getFlavor:getFlavor") return { id: `flavor:${args.inputs.name}` };
      return { id: `${args.token}-id`, ...args.inputs };
    },
  },
  "infra",
  "prod",
  true,
);

type Stack = typeof import("./index");
let stack: Stack;
const value = <T>(o: pulumi.Output<T> | null) =>
  new Promise<T>((resolve) => o!.apply((v) => { resolve(v); return v; }));
const find = (type: string, name: string) => created.get(`${type}::${name}`)!;

beforeAll(async () => {
  stack = await import("./index");
  await Promise.all([value(stack.runtimeSecurityGroupId), value(stack.runtimeBlockedPublicIps), value(stack.runtimeKeypairName)]);
});

describe("runtime-VPS", () => {
  test("свой сервисный пользователь с ролью member на проект", () => {
    const user = find("selectel:index/iamServiceuserV1:IamServiceuserV1", "runtime");
    expect(user.inputs.name).toBe("cellestialRuntimeProvisioner");
    expect(user.inputs.roles).toEqual([{ roleName: "member", scope: "project", projectId: "release-id" }]);
  });

  test("security group: SSH и 8443 только с VPS2, ICMP из подсети, другого ingress нет", () => {
    const rules = [...created.entries()]
      .filter(([key]) => key.startsWith("openstack:networking/secGroupRule:SecGroupRule::runtime-"))
      .map(([, r]) => [r.inputs.protocol, r.inputs.portRangeMin ?? null, r.inputs.remoteIpPrefix, r.inputs.direction])
      .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
    expect(rules).toEqual([
      ["icmp", null, "192.168.199.0/24", "ingress"],
      ["tcp", 22, "192.168.199.10/32", "ingress"],
      ["tcp", 8443, "192.168.199.10/32", "ingress"],
    ]);
    expect(find("openstack:networking/secGroup:SecGroup", "runtime").inputs.name).toBe("runtime");
  });

  test("keypair runtime-provisioner из ed25519-ключа принадлежит пользователю runtime", () => {
    expect(find("tls:index/privateKey:PrivateKey", "runtime-ssh").inputs.algorithm).toBe("ED25519");
    const keypair = find("selectel:index/vpcKeypairV2:VpcKeypairV2", "runtime");
    expect(keypair.inputs).toMatchObject({ name: "runtime-provisioner", publicKey: "ssh-ed25519 runtime-ssh", userId: "runtime-id" });
  });

  test("CA и клиентский сертификат backend с usage client_auth", () => {
    const ca = find("tls:index/selfSignedCert:SelfSignedCert", "runtime-ca");
    expect(ca.inputs.isCaCertificate).toBe(true);
    expect(ca.inputs.allowedUses).toContain("cert_signing");
    const csr = find("tls:index/certRequest:CertRequest", "runtime-client");
    expect(csr.inputs.subject.commonName).toBe("cellestial-backend");
    const cert = find("tls:index/locallySignedCert:LocallySignedCert", "runtime-client");
    expect(cert.inputs.allowedUses).toContain("client_auth");
    expect(cert.inputs.allowedUses).not.toContain("server_auth");
    expect(cert.inputs.caCertPem).toBe("ca-cert");
  });

  test("ключ RUNTIME_SECRETS_KEY — 32 случайных байта", () => {
    expect(find("random:index/randomBytes:RandomBytes", "runtime-secrets-key").inputs.length).toBe(32);
  });

  test("открытые выходы", async () => {
    expect(await value(stack.runtimeOsUsername)).toBe("cellestialRuntimeProvisioner");
    expect(await value(stack.runtimeAccountId)).toBe("123456");
    expect(await value(stack.runtimeRegion)).toBe("ru-9");
    expect(await value(stack.runtimeAvailabilityZone)).toBe("ru-9a");
    expect(await value(stack.runtimeNetworkId)).toBe("private-id");
    expect(await value(stack.runtimeSubnetId)).toBe("private-id");
    expect(await value(stack.runtimeSecurityGroupId)).toBe("runtime-id");
    expect(await value(stack.runtimeKeypairName)).toBe("runtime-provisioner");
    expect(await value(stack.runtimeImageId)).toBe("image:Debian 13 (Trixie) 64-bit");
    expect(await value(stack.runtimeFlavorId)).toBe("flavor:SL1.1-2048");
    expect(await value(stack.runtimeVolumeType)).toBe("basicssd.ru-9a");
    expect(await value(stack.runtimeCaCert)).toBe("ca-cert");
    expect(await value(stack.runtimeClientCert)).toBe("client-cert");
    expect(await value(stack.runtimeBlockedPublicIps)).toBe("203.0.113.1,203.0.113.2");
  });

  test("секретные выходы помечены секретом", async () => {
    for (const name of [
      "runtimeOsPassword", "runtimeSshPrivateKey", "runtimeCaKey", "runtimeClientKey", "runtimeSecretsKey",
    ] as const) {
      expect(await stack[name]!.isSecret).toBe(true);
    }
    expect(await value(stack.runtimeCaKey)).toBe("runtime-ca-pkcs8");
    expect(await value(stack.runtimeClientKey)).toBe("runtime-client-pkcs8");
    expect(await value(stack.runtimeSshPrivateKey)).toBe("runtime-ssh-openssh");
  });
});
