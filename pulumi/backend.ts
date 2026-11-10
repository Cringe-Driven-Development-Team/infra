import * as pulumi from "@pulumi/pulumi";
import * as openstack from "@pulumi/openstack";
import { flavorId, InfraConfig } from "./config";
import { Foundation } from "./foundation";

// ---------------------------------------------------------------------------
// Приватная VPS2 (infra#59): Go API, Postgres и Redis. Включается infra:backend. Та же приватная сеть
// и роутер (исходящий доступ — SNAT роутера), но без floating IP: из интернета её не видно. Security
// group пускает SSH и API (8080) только с приватного адреса gateway, ICMP — из подсети. На gateway
// ничего не меняется: её floating IP — 1:1 DNAT, поэтому API на ней не публикуется.
// ---------------------------------------------------------------------------
export interface Backend {
  port: openstack.networking.Port;
}

export function createBackend(c: InfraConfig, f: Foundation, gatewayPort: openstack.networking.Port): Backend | undefined {
  const backend = c.backend;
  if (!backend) {
    return undefined;
  }
  const withOs = { provider: f.os };
  const gatewayAddress = gatewayPort.allFixedIps.apply((ips) => `${ips[0]}/32`);
  const secGroup = new openstack.networking.SecGroup("backend", {
    name: "backend",
    description: "Private backend VPS: SSH and Go API only from the gateway VPS",
  }, withOs);
  const ingress: [string, string, number | undefined, pulumi.Input<string>][] = [
    ["ssh", "tcp", 22, gatewayAddress],
    ["api", "tcp", 8080, gatewayAddress],
    ["icmp", "icmp", undefined, f.subnet.cidr],
  ];
  for (const [ruleName, protocol, port, remoteIpPrefix] of ingress) {
    new openstack.networking.SecGroupRule(`backend-${ruleName}`, {
      securityGroupId: secGroup.id,
      direction: "ingress",
      ethertype: "IPv4",
      protocol,
      ...(port ? { portRangeMin: port, portRangeMax: port } : {}),
      remoteIpPrefix,
    }, withOs);
  }

  const port = new openstack.networking.Port("backend", {
    name: "backend-port",
    networkId: f.network.id,
    fixedIps: [{ subnetId: f.subnet.id }],
    securityGroupIds: [secGroup.id],
  }, withOs);

  // Postgres и Redis — в Docker volume на boot-диске; enableOnlineResize — увеличение без пересоздания.
  const volume = new openstack.blockstorage.Volume("backend", {
    name: "boot-volume-backend",
    size: backend.volumeSize,
    imageId: f.imageId,
    volumeType: c.volumeType,
    availabilityZone: c.zone,
    enableOnlineResize: true,
  }, { ...withOs, ignoreChanges: ["imageId"] });

  new openstack.compute.Instance("backend", {
    name: "pulumi-server-backend",
    flavorId: flavorId(backend.flavor, f.os),
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
    // По metadata.role dynamic inventory Ansible собирает группу backend
    metadata: { role: "backend", env: "release" },
    userData: c.userData,
    vendorOptions: { ignoreResizeConfirmation: true },
  }, { ...withOs, deleteBeforeReplace: true, ignoreChanges: ["imageId"], dependsOn: [f.routerInterface] });

  return { port };
}
