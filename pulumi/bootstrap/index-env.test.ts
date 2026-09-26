import { expect, test } from "bun:test";

// Отдельный процесс без OS_*: программа стека должна сразу назвать env.sh,
// а не упасть позже на 401 от Keystone или на конфигурации провайдера.
test("без env.sh программа стека падает с подсказкой source env.sh", () => {
  const script = `
    const pulumi = require("@pulumi/pulumi");
    pulumi.runtime.setAllConfig({ "infra-bootstrap:s3Pool": "ru-7", "infra-bootstrap:bucketName": "b", "infra-bootstrap:dnsZone": "z." });
    pulumi.runtime.setMocks({ newResource: (a) => ({ id: a.name + "-id", state: a.inputs }), call: (a) => a.inputs },
      "infra-bootstrap", "main", true);
    require("./index");
  `;
  const env: Record<string, string> = { PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? "" };
  const run = Bun.spawnSync(["bun", "-e", script], { cwd: import.meta.dir, env });
  expect(run.exitCode).not.toBe(0);
  expect(run.stderr.toString()).toContain("source pulumi/bootstrap/env.sh");
});
