import { describe, expect, test } from "bun:test";
import {
  credentialsFromEnv, initProjectS3, waitForS3Key,
  type Credentials, type Http, type HttpResult,
} from "./selectel-s3";

const creds: Credentials = {
  authUrl: "https://auth.example/identity/v3/", username: "u", password: "s3cr3t-pw", domain: "631994",
};
const tokenOk: HttpResult = { status: 201, body: "HTTP/1.1 201 Created\r\nX-Subject-Token: tok-123\r\n\r\n{}" };

function fakeHttp(init: HttpResult, token: HttpResult = tokenOk) {
  const calls: { args: string[]; stdin: string }[] = [];
  const http: Http = async (args, stdin) => {
    calls.push({ args, stdin });
    return args[args.length - 1].endsWith("/auth/tokens") ? token : init;
  };
  return { http, calls };
}

describe("credentialsFromEnv", () => {
  test("берёт OS_* и адрес Keystone по умолчанию", () => {
    expect(credentialsFromEnv({ OS_USERNAME: "u", OS_PASSWORD: "p", OS_DOMAIN_NAME: "1" })).toEqual({
      authUrl: "https://cloud.api.selcloud.ru/identity/v3/", username: "u", password: "p", domain: "1",
    });
  });
  test("перечисляет недостающие переменные и называет env.sh", () => {
    expect(() => credentialsFromEnv({ OS_USERNAME: "u" })).toThrow(/OS_PASSWORD.*OS_DOMAIN_NAME.*env\.sh/s);
  });
});

describe("initProjectS3", () => {
  for (const status of [200, 201, 204]) {
    test(`успех на ${status}`, async () => {
      const { http } = fakeHttp({ status, body: "" });
      expect(await initProjectS3(http, creds, "p1", "ru-7")).toBe(status);
    });
  }
  for (const status of [400, 403, 500]) {
    test(`ошибка на ${status} с кодом и телом`, async () => {
      const { http } = fakeHttp({ status, body: "bad things" });
      await expect(initProjectS3(http, creds, "p1", "ru-7")).rejects.toThrow(new RegExp(`${status}.*bad things`));
    });
  }
  test("ошибка, если сеть не ответила", async () => {
    const { http } = fakeHttp({ status: 0, body: "Could not resolve host" });
    await expect(initProjectS3(http, creds, "p1", "ru-7")).rejects.toThrow(/без ответа.*Could not resolve host/);
  });
  test("ошибка, если Keystone не выдал токен", async () => {
    const { http } = fakeHttp({ status: 200, body: "" }, { status: 401, body: "unauthorized" });
    await expect(initProjectS3(http, creds, "p1", "ru-7")).rejects.toThrow(/токен.*401/);
  });
  test("пароль и токен — только в stdin; адрес init по пулу", async () => {
    const { http, calls } = fakeHttp({ status: 200, body: "" });
    await initProjectS3(http, creds, "p1", "ru-7");
    expect(calls).toHaveLength(2);
    expect(calls[0].args.join(" ")).not.toContain("s3cr3t-pw");
    expect(calls[0].stdin).toContain("s3cr3t-pw");
    expect(calls[0].stdin).toContain('"id":"p1"');
    expect(calls[1].args.join(" ")).not.toContain("tok-123");
    expect(calls[1].stdin).toContain("X-Auth-Token: tok-123");
    expect(calls[1].args[calls[1].args.length - 1]).toBe("https://api.ru-7.storage.selcloud.ru/v2/hello/init");
  });
});

describe("waitForS3Key", () => {
  function clock() {
    let t = 0;
    return { now: () => t, sleep: async (ms: number) => { t += ms; } };
  }
  const base = {
    endpoint: "https://s3.ru-7.storage.selcloud.ru", pool: "ru-7",
    accessKey: "AK", secretKey: "SK-secret", timeoutSeconds: 60, intervalMs: 15000,
  };

  test("успех после отказов и сетевой ошибки", async () => {
    const replies: HttpResult[] = [
      { status: 403, body: "InvalidAccessKeyId" }, { status: 0, body: "timeout" }, { status: 200, body: "<ok/>" },
    ];
    let i = 0;
    const http: Http = async () => replies[i++];
    expect(await waitForS3Key(http, { ...base, ...clock() })).toBe(3);
  });
  test("таймаут: ошибка с последним ответом, попытки ограничены", async () => {
    let calls = 0;
    const http: Http = async () => { calls++; return { status: 403, body: "InvalidAccessKeyId" }; };
    await expect(waitForS3Key(http, { ...base, ...clock() })).rejects.toThrow(/60 с.*403.*InvalidAccessKeyId/s);
    expect(calls).toBe(5);
  });
  test("таймаут 0 — одна попытка", async () => {
    let calls = 0;
    const http: Http = async () => { calls++; return { status: 403, body: "no" }; };
    await expect(waitForS3Key(http, { ...base, timeoutSeconds: 0, ...clock() })).rejects.toThrow();
    expect(calls).toBe(1);
  });
  test("секрет — только в stdin, не в аргументах и не в ошибке", async () => {
    const seen: { args: string[]; stdin: string }[] = [];
    const http: Http = async (args, stdin) => { seen.push({ args, stdin }); return { status: 403, body: "no" }; };
    const err = await waitForS3Key(http, { ...base, timeoutSeconds: 0, ...clock() }).catch((e: Error) => e);
    expect(String(err)).not.toContain("SK-secret");
    expect(seen[0].args.join(" ")).not.toContain("SK-secret");
    expect(seen[0].stdin).toContain("AK:SK-secret");
    expect(seen[0].args).toContain("aws:amz:ru-7:s3");
    expect(seen[0].args[seen[0].args.length - 1]).toBe("https://s3.ru-7.storage.selcloud.ru/");
  });
});
