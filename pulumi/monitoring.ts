import * as openstack from "@pulumi/openstack";
import * as pulumi from "@pulumi/pulumi";
import { Auxiliary } from "./auxiliary";
import { InfraConfig, flavorId } from "./config";
import { Foundation } from "./foundation";

// createMonitoring создаёт VPS4 с отдельным портом и сохраняемым загрузочным томом.
export function createMonitoring(
  c: InfraConfig, monitoring: NonNullable<InfraConfig["monitoring"]>, f: Foundation, auxiliary: Auxiliary,
): openstack.networking.FloatingIp {
  const withOs = { provider: f.os };
  const group = new openstack.networking.SecGroup("monitoring", { name: "monitoring" }, withOs);
  const allowTcp = (name: string, groupId: pulumi.Input<string>, port: number, source: pulumi.Input<string>) =>
    new openstack.networking.SecGroupRule(name, {
      securityGroupId: groupId,
      direction: "ingress",
      ethertype: "IPv4",
      protocol: "tcp",
      portRangeMin: port,
      portRangeMax: port,
      remoteIpPrefix: source,
    }, withOs);

  const ingress = [
    allowTcp("monitoring-ssh", group.id, 22, auxiliary.port.allFixedIps.apply((ips) => `${ips[0]}/32`)),
    allowTcp("monitoring-http", group.id, 80, "0.0.0.0/0"),
    allowTcp("monitoring-https", group.id, 443, "0.0.0.0/0"),
  ];
  allowTcp("auxiliary-metrics", auxiliary.securityGroup.id, 9100, `${monitoring.privateIp}/32`);

  const port = new openstack.networking.Port("monitoring", {
    name: "monitoring-port",
    networkId: f.network.id,
    fixedIps: [{ subnetId: f.subnet.id, ipAddress: monitoring.privateIp }],
    securityGroupIds: [group.id],
  }, { ...withOs, dependsOn: ingress });
  const volume = new openstack.blockstorage.Volume("monitoring", {
    name: "boot-volume-monitoring",
    size: monitoring.volumeSize,
    imageId: f.imageId,
    volumeType: c.volumeType,
    availabilityZone: c.zone,
    enableOnlineResize: true,
  }, { ...withOs, ignoreChanges: ["imageId"], protect: true });

  new openstack.compute.Instance("monitoring", {
    name: "pulumi-server-monitoring",
    flavorId: flavorId(monitoring.flavor, f.os),
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
    metadata: { role: "monitoring", env: "release" },
    userData: c.userData,
  }, { ...withOs, deleteBeforeReplace: true, dependsOn: [f.routerInterface] });

  const floatingIp = new openstack.networking.FloatingIp("monitoring", { pool: c.externalNetworkName }, withOs);
  new openstack.networking.FloatingIpAssociate("monitoring", {
    portId: port.id,
    floatingIp: floatingIp.address,
  }, { ...withOs, dependsOn: [f.routerInterface] });
  return floatingIp;
}
