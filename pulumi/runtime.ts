import * as pulumi from "@pulumi/pulumi";
import * as random from "@pulumi/random";
import * as openstack from "@pulumi/openstack";
import * as selectel from "@pulumi/selectel";
import * as tls from "@pulumi/tls";
import { flavorId, InfraConfig, serviceUserPasswordArgs } from "./config";
import { Backend } from "./backend";
import { Foundation } from "./foundation";

export interface RuntimeOutputs {
  runtimeOsUsername: pulumi.Input<string>;
  runtimeOsPassword: pulumi.Input<string>;
  runtimeProjectId: pulumi.Input<string>;
  runtimeAccountId: pulumi.Input<string>;
  runtimeRegion: pulumi.Input<string>;
  runtimeAvailabilityZone: pulumi.Input<string>;
  runtimeNetworkId: pulumi.Input<string>;
  runtimeSubnetId: pulumi.Input<string>;
  runtimeSecurityGroupId: pulumi.Input<string>;
  runtimeKeypairName: pulumi.Input<string>;
  runtimeSshPrivateKey: pulumi.Input<string>;
  runtimeCaCert: pulumi.Input<string>;
  runtimeCaKey: pulumi.Input<string>;
  runtimeClientCert: pulumi.Input<string>;
  runtimeClientKey: pulumi.Input<string>;
  runtimeImageId: pulumi.Input<string>;
  runtimeFlavorId: pulumi.Input<string>;
  runtimeVolumeType: pulumi.Input<string>;
  runtimeBlockedPublicIps: pulumi.Input<string>;
  runtimeSecretsKey: pulumi.Input<string>;
}

// ---------------------------------------------------------------------------
// Runtime-VPS пользовательских сессий (infra#89, backend#57). Включается infra:runtime (нужен
// infra:backend). Сами VPS создаёт и удаляет Go API на VPS2 — здесь только то, что backend сам не
// создаст: свой сервисный пользователь (ключи release не трогаем: отзыв одних не ломает другое),
// security group, SSH-ключ provisioner и keypair, CA для mTLS backend ↔ агент, ключ шифрования
// токенов агентов. VPS — в той же приватной подсети без floating IP, наружу через SNAT роутера.
// ---------------------------------------------------------------------------
export function createRuntime(
  c: InfraConfig,
  f: Foundation,
  backend: Backend | undefined,
  // Публичные адреса своей инфраструктуры: floating IP gateway и auxiliary VPS (если есть)
  publicIps: (pulumi.Input<string> | undefined)[],
): RuntimeOutputs | undefined {
  const rt = c.runtime;
  // infra:runtime без infra:backend отвергает loadConfig
  if (!rt || !backend) {
    return undefined;
  }
  const withOs = { provider: f.os };
  // member на проект: Nova, Cinder, Neutron (порты, диски, серверы) — и ничего на уровне аккаунта
  const runtimePassword = new random.RandomPassword("runtime-serviceuser", serviceUserPasswordArgs);
  const runtimeUser = new selectel.IamServiceuserV1("runtime", {
    name: rt.userName,
    password: runtimePassword.result,
    roles: [
      { roleName: "member", scope: "project", projectId: f.project.id },
    ],
  });

  // Ingress — только с VPS2: SSH (Ansible из Go API) и 8443 (TLS-прокси API агента); ICMP — из подсети.
  // Egress — правила Neutron по умолчанию: трафик kernel фильтрует nftables на самой VPS
  // (ansible/runtime, контракт docs/runtime-agent-network.md в backend).
  const backendAddress = backend.port.allFixedIps.apply((ips) => `${ips[0]}/32`);
  const secGroup = new openstack.networking.SecGroup("runtime", {
    name: "runtime",
    description: "Runtime VPS of user sessions: SSH and agent TLS only from the backend VPS",
  }, withOs);
  const ingress: [string, string, number | undefined, pulumi.Input<string>][] = [
    ["ssh", "tcp", 22, backendAddress],
    ["agent-tls", "tcp", 8443, backendAddress],
    ["icmp", "icmp", undefined, f.subnet.cidr],
  ];
  for (const [ruleName, protocol, port, remoteIpPrefix] of ingress) {
    new openstack.networking.SecGroupRule(`runtime-${ruleName}`, {
      securityGroupId: secGroup.id,
      direction: "ingress",
      ethertype: "IPv4",
      protocol,
      ...(port ? { portRangeMin: port, portRangeMax: port } : {}),
      remoteIpPrefix,
    }, withOs);
  }

  // SSH-ключ provisioner: приватная часть — в Go API (vault), публичная — keypair пользователя
  // runtime. Keypair принадлежит пользователю: backend создаёт VPS от его имени и видит только его.
  const sshKey = new tls.PrivateKey("runtime-ssh", { algorithm: "ED25519" });
  const runtimeKeypair = new selectel.VpcKeypairV2("runtime", {
    name: "runtime-provisioner",
    publicKey: sshKey.publicKeyOpenssh.apply((k) => k.trim()),
    userId: runtimeUser.id,
  // Ротация ключа — замена keypair с тем же name: create-before-delete упёрся бы в 409
  }, { deleteBeforeReplace: true });

  // CA для mTLS: backend предъявляет агенту клиентский сертификат и сам выпускает серверный сертификат
  // каждой VPS этим CA при provisioning, поэтому ключ CA тоже уходит в backend.
  const caKey = new tls.PrivateKey("runtime-ca", { algorithm: "ECDSA", ecdsaCurve: "P256" });
  const caCert = new tls.SelfSignedCert("runtime-ca", {
    privateKeyPem: caKey.privateKeyPem,
    subject: { commonName: "cellestial-runtime-ca", organization: "Cellestial" },
    isCaCertificate: true,
    validityPeriodHours: 10 * 365 * 24,
    allowedUses: ["cert_signing", "crl_signing", "digital_signature"],
  });
  const clientKey = new tls.PrivateKey("runtime-client", { algorithm: "ECDSA", ecdsaCurve: "P256" });
  const clientCsr = new tls.CertRequest("runtime-client", {
    privateKeyPem: clientKey.privateKeyPem,
    subject: { commonName: "cellestial-backend", organization: "Cellestial" },
  });
  const clientCert = new tls.LocallySignedCert("runtime-client", {
    certRequestPem: clientCsr.certRequestPem,
    caPrivateKeyPem: caKey.privateKeyPem,
    caCertPem: caCert.certPem,
    validityPeriodHours: 2 * 365 * 24,
    allowedUses: ["client_auth", "digital_signature"],
  });

  // Ключ шифрования токенов агентов в БД (AES-256-GCM): base64 ровно 32 байт
  const secretsKey = new random.RandomBytes("runtime-secrets-key", { length: 32 });

  return {
    runtimeOsUsername: runtimeUser.name,
    runtimeOsPassword: pulumi.secret(runtimePassword.result),
    runtimeProjectId: f.project.id,
    runtimeAccountId: c.domainName,
    runtimeRegion: c.pool,
    runtimeAvailabilityZone: c.zone,
    runtimeNetworkId: f.network.id,
    runtimeSubnetId: f.subnet.id,
    runtimeSecurityGroupId: secGroup.id,
    runtimeKeypairName: runtimeKeypair.name,
    runtimeSshPrivateKey: pulumi.secret(sshKey.privateKeyOpenssh),
    runtimeCaCert: caCert.certPem,
    runtimeCaKey: pulumi.secret(caKey.privateKeyPemPkcs8),
    runtimeClientCert: clientCert.certPem,
    runtimeClientKey: pulumi.secret(clientKey.privateKeyPemPkcs8),
    runtimeImageId: rt.imageId,
    runtimeFlavorId: flavorId(rt.flavor, f.os),
    runtimeVolumeType: rt.volumeType,
    // Kernel на runtime-VPS до этих адресов не достучится (RUNTIME_BLOCKED_PUBLIC_IPS → nftables)
    runtimeBlockedPublicIps: pulumi
      .all(publicIps)
      .apply((ips) => ips.filter((ip) => ip).join(",")),
    runtimeSecretsKey: pulumi.secret(secretsKey.base64),
  };
}
