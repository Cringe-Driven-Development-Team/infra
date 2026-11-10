import { describe, expect, test } from "bun:test";
import * as pulumi from "@pulumi/pulumi";
import { loadConfig } from "./config";

// Комбинации переключателей infra:backend/auxiliary/runtime/cdn и проверки их совместимости
const base = {
  "selectel:authUrl": "https://cloud.api.selcloud.ru/identity/v3/",
  "selectel:authRegion": "ru-9",
  "infra:pool": "ru-9",
  "infra:zone": "ru-9a",
  "infra:volumeType": "fast.ru-9a",
  "infra:gatewayFlavorName": "SL1.2-8192",
  "infra:imageName": "Ubuntu 24.04 LTS 64-bit",
  "infra:sshPublicKey": "ssh-ed25519 AAAA test",
  "infra:s3Pool": "ru-7",
  "infra:s3Bucket": "releases",
  "infra:avatarsBucket": "avatars",
};
const load = (extra: Record<string, string> = {}) => {
  Object.assign(process.env, { OS_USERNAME: "u", OS_PASSWORD: "p", OS_DOMAIN_NAME: "123456" });
  delete process.env.OS_PROJECT_NAME;
  // Пространство имён infra: — имя проекта; ресурсов loadConfig не создаёт
  pulumi.runtime.setMocks({ newResource: (a) => ({ id: a.name, state: a.inputs }), call: (a) => a.inputs }, "infra", "prod", true);
  pulumi.runtime.setAllConfig({ ...base, ...extra });
  return loadConfig();
};

describe("loadConfig", () => {
  test("по умолчанию: только gateway и бакеты, без backend/auxiliary/runtime/CDN и зоны DNS", () => {
    const c = load();
    expect(c.backend).toBeUndefined();
    expect(c.auxiliary).toBeUndefined();
    expect(c.runtime).toBeUndefined();
    expect(c.s3.cdnName).toBeUndefined();
    expect(c.s3.staticCdnName).toBeUndefined();
    expect(c.s3.public).toBe(true);
    expect(c.s3.publicRead).toBe(false);
    expect(c.dns.zone).toBeUndefined();
    expect(c.domainName).toBe("123456");
    expect(c.gateway).toEqual({ flavor: { name: "SL1.2-8192" }, volumeSize: 20 });
  });

  test("все переключатели включены", () => {
    const c = load({
      "infra:backend": "true",
      "infra:backendFlavorId": "f-1",
      "infra:auxiliary": "true",
      "infra:auxiliaryVcpus": "2",
      "infra:auxiliaryRam": "4096",
      "infra:auxiliaryBackupBucket": "backups",
      "infra:runtime": "true",
      "infra:runtimeFlavorName": "SL1.1-2048",
      "infra:runtimeImageId": "img",
      "infra:cdn": "true",
      "infra:staticBucket": "static",
      "infra:hooksDomain": "hooks.example.ru",
      "infra:dnsZone": "example.ru.",
    });
    expect(c.backend).toEqual({ flavor: { id: "f-1" }, volumeSize: 40 });
    expect(c.auxiliary).toMatchObject({
      flavor: { vcpus: 2, ram: 4096 }, backupBucket: "backups", backupPool: "ru-7",
    });
    expect(c.dns.hooksDomain).toBe("hooks.example.ru");
    expect(c.runtime).toEqual({
      flavor: { name: "SL1.1-2048" }, imageId: "img", userName: "cellestialRuntimeProvisioner", volumeType: "fast.ru-9a",
    });
    expect(c.s3.cdnName).toBe("pulumi-release-cdn");
    expect(c.s3.staticCdnName).toBe("pulumi-release-static-cdn");
    expect(c.dns.zone).toBe("example.ru.");
  });

  test("hooksDomain без infra:auxiliary: записи нет, зона DNS всё равно нужна", () => {
    expect(() => load({ "infra:hooksDomain": "hooks.example.ru" })).toThrow("dnsZone");
    const c = load({ "infra:hooksDomain": "hooks.example.ru", "infra:dnsZone": "example.ru." });
    expect(c.dns.hooksDomain).toBeUndefined();
    expect(c.dns.zone).toBe("example.ru.");
  });

  test("отказы несовместимых настроек", () => {
    expect(() => load({ "infra:runtime": "true" })).toThrow("infra:runtime задан без infra:backend");
    expect(() => load({ "infra:cdnDomain": "cdn.example.ru" })).toThrow("infra:cdnDomain задан без infra:cdn");
    expect(() => load({ "infra:staticDomain": "static.example.ru" })).toThrow("infra:staticDomain задан без infra:staticBucket");
    expect(() => load({ "infra:domain": "example.ru" })).toThrow("dnsZone");
    expect(() => load({ "infra:auxiliary": "true", "infra:auxiliaryFlavorName": "x" })).toThrow("auxiliaryBackupBucket");
    expect(() => load({ "selectel:username": "x" })).toThrow("selectel:username/selectel:password");
  });
});
