import { beforeAll, describe, expect, test } from "bun:test";
import * as pulumi from "@pulumi/pulumi";

const created = new Map<string, { type: string; inputs: Record<string, any> }>();

Object.assign(process.env, { OS_USERNAME: "u", OS_PASSWORD: "p", OS_DOMAIN_NAME: "1" });
pulumi.runtime.setAllConfig({
  "infra-bootstrap:s3Pool": "ru-7",
  "infra-bootstrap:bucketName": "cdd-infra-state",
});
pulumi.runtime.setMocks(
  {
    newResource(args) {
      created.set(args.name, { type: args.type, inputs: args.inputs });
      const state: Record<string, unknown> = { ...args.inputs };
      if (args.name === "state-s3") {
        state.accessKey = "AK";
        state.secretKey = "SK";
      }
      if (args.name === "state-user-password") {
        state.result = "generated-password";
      }
      return { id: args.id || `${args.name}-id`, state };
    },
    call: (args) => args.inputs,
  },
  "infra-bootstrap",
  "main",
  true, // preview: сетевые шаги (init S3, ожидание ключа) пропускаются
);

type Stack = typeof import("./index");
let stack: Stack;
const value = <T>(o: pulumi.Output<T>) =>
  new Promise<T>((resolve) => o.apply((v) => { resolve(v); return v; }));

beforeAll(async () => {
  stack = await import("./index");
  await Promise.all([value(stack.backendUrl), value(stack.versioningStatus), value(stack.stateProjectId)]);
});

describe("bootstrap-стек", () => {
  test("проект infra-state создаётся стеком", () => {
    const project = created.get("infra-state")!;
    expect(project.type).toContain("VpcProjectV2");
    expect(project.inputs.name).toBe("infra-state");
  });

  test("пользователь стейта: одна роль member на проект infra-state", async () => {
    const user = created.get("state-user")!;
    expect(user.inputs.name).toBe("infra-state-s3");
    expect(user.inputs.roles).toEqual([
      { roleName: "member", scope: "project", projectId: await value(stack.stateProjectId) },
    ]);
  });

  test("S3-ключ выдан этому пользователю на проект infra-state", async () => {
    const s3 = created.get("state-s3")!;
    expect(s3.inputs.userId).toBe("state-user-id");
    expect(s3.inputs.projectId).toBe(await value(stack.stateProjectId));
  });

  test("бакет: имя из конфига, forceDestroy не включён", () => {
    const bucket = created.get("state-bucket")!;
    expect(bucket.inputs.bucket).toBe("cdd-infra-state");
    expect(bucket.inputs.forceDestroy).not.toBe(true);
  });

  test("версионирование бакета включено", async () => {
    expect(created.get("state-versioning")!.inputs.bucket).toBe("cdd-infra-state");
    expect(await value(stack.versioningStatus)).toBe("Enabled");
  });

  test("S3-провайдер смотрит в Selectel ru-7", () => {
    const provider = created.get("selectel-s3")!;
    expect(JSON.stringify(provider.inputs)).toContain("https://s3.ru-7.storage.selcloud.ru");
  });

  test("секретный ключ — secret", async () => {
    expect(await stack.stateSecretKey.isSecret).toBe(true);
  });

  test("backendUrl для основного стека — префикс main/", async () => {
    expect(await value(stack.backendUrl)).toBe(
      "s3://cdd-infra-state/main?region=ru-7&endpoint=s3.ru-7.storage.selcloud.ru&s3ForcePathStyle=true",
    );
  });
});
