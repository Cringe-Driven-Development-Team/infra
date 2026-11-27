import * as openstack from "@pulumi/openstack";
import { flavorId, InfraConfig } from "./config";
import { Foundation } from "./foundation";

export interface Gateway {
  port: openstack.networking.Port;
  server: openstack.compute.Instance;
  floatingIp: openstack.networking.FloatingIp;
}

export function createGateway(c: InfraConfig, f: Foundation): Gateway {
  const withOs = { provider: f.os };

  // Явная группа нужна, чтобы доступ к метрикам определялся адресом VPS4.
  const securityGroup = c.monitoring ? new openstack.networking.SecGroup("gateway", {
    name: "gateway",
  }, withOs) : undefined;
  if (securityGroup && c.monitoring) {
    for (const port of [22, 80, 443, 9100]) {
      new openstack.networking.SecGroupRule(`gateway-tcp${port}`, {
        securityGroupId: securityGroup.id,
        direction: "ingress",
        ethertype: "IPv4",
        protocol: "tcp",
        portRangeMin: port,
        portRangeMax: port,
        remoteIpPrefix: port === 9100 ? `${c.monitoring.privateIp}/32` : "0.0.0.0/0",
      }, withOs);
    }
  }

  const port = new openstack.networking.Port("gateway", {
    name: "gateway-port",
    networkId: f.network.id,
    fixedIps: [{ subnetId: f.subnet.id }],
    securityGroupIds: securityGroup ? [securityGroup.id] : undefined,
  }, withOs);

  // Boot-диск единственной VPS — под будущий Postgres (docker volume), 20 ГБ;
  // enableOnlineResize: увеличение без пересоздания.
  const volume = new openstack.blockstorage.Volume("gateway", {
    name: "boot-volume-gateway",
    size: c.gateway.volumeSize,
    imageId: f.imageId,
    volumeType: c.volumeType,
    availabilityZone: c.zone,
    enableOnlineResize: true,
  }, { ...withOs, ignoreChanges: ["imageId"] });

  // Единственная VPS: публичный IP, Caddy; позже — Go API и Postgres в Docker Compose.
  // Приватная сеть/подсеть/роутер оставлены: без них к инстансу нельзя привязать floating IP.
  const server = new openstack.compute.Instance("gateway", {
    name: "pulumi-server-gateway",
    flavorId: flavorId(c.gateway.flavor, f.os),
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
    // По metadata.role dynamic inventory Ansible собирает группу gateway
    metadata: { role: "gateway", env: "release" },
    userData: c.userData,
    vendorOptions: { ignoreResizeConfirmation: true },
  // userData ForceNew: сервер пересоздаётся. deleteBeforeReplace — иначе up падает
  // на занятом старым сервером порту/boot-диске (имена и ресурсы переиспользуются).
  }, { ...withOs, deleteBeforeReplace: true, ignoreChanges: ["imageId"], dependsOn: [f.routerInterface] });

  const floatingIp = new openstack.networking.FloatingIp("gateway", {
    pool: c.externalNetworkName,
  }, withOs);

  // Без dependsOn привязка стартует раньше подключения подсети к роутеру:
  // Neutron отвечает ExternalGatewayForFloatingIPNotFound
  new openstack.networking.FloatingIpAssociate("gateway", {
    portId: port.id,
    floatingIp: floatingIp.address,
  }, { ...withOs, dependsOn: [f.routerInterface] });

  return { port, server, floatingIp };
}
