import { describe, expect, test } from "bun:test";
import type { Http, HttpResult } from "./bootstrap/selectel-s3";
import {
  checkCdnName, createCdnResource, getBucketType, getPublicDomain, removeCdnResource, setBucketType,
  updateCdnResource, waitPublicDomain,
} from "./selectel-storage";

type Call = { method: string; url: string; body?: any; stdin: string };

// Ответы по «METHOD url»; одинаковые запросы получают ответы по очереди, последний повторяется.
function fakeHttp(routes: Record<string, HttpResult | HttpResult[]>) {
  const calls: Call[] = [];
  const seen: Record<string, number> = {};
  const http: Http = async (args, stdin) => {
    const method = args[args.indexOf("-X") + 1];
    const url = args[args.length - 1];
    const raw = args.includes("--data-raw") ? args[args.indexOf("--data-raw") + 1] : undefined;
    calls.push({ method, url, body: raw && JSON.parse(raw), stdin });
    const key = `${method} ${url}`;
    const route = routes[key];
    if (!route) throw new Error(`неожиданный запрос ${key}`);
    const list = Array.isArray(route) ? route : [route];
    const i = Math.min(seen[key] ?? 0, list.length - 1);
    seen[key] = (seen[key] ?? 0) + 1;
    return list[i];
  };
  return { http, calls };
}

const res = (status: number, body: unknown = "") =>
  ({ status, body: typeof body === "string" ? body : JSON.stringify(body) });
const noSleep = async () => {};
const C = "https://api.ru-7.storage.selcloud.ru/v2/containers/b1";
const CDN = "https://api.selectel.ru/cdn/v3";

describe("тип бакета", () => {
  test("PUT options с general.type, токен только в stdin", async () => {
    const { http, calls } = fakeHttp({ [`PUT ${C}/options`]: res(200, {}) });
    await setBucketType(http, "tok", "ru-7", "b1", "public");
    expect(calls[0].body).toEqual({ general: { type: "public" } });
    expect(calls[0].stdin).toBe('header = "X-Auth-Token: tok"\n');
  });
  test("ошибка с кодом и телом", async () => {
    const { http } = fakeHttp({ [`PUT ${C}/options`]: res(403, "denied") });
    await expect(setBucketType(http, "t", "ru-7", "b1", "public")).rejects.toThrow(/403.*denied/);
  });
  test("чтение типа; 404 — бакета нет", async () => {
    const { http } = fakeHttp({ [`GET ${C}/options`]: [res(200, { general: { type: "public" } }), res(404)] });
    expect(await getBucketType(http, "t", "ru-7", "b1")).toBe("public");
    expect(await getBucketType(http, "t", "ru-7", "b1")).toBeUndefined();
  });
});

describe("публичный домен", () => {
  test("uuid своего бакета → <uuid>.selstorage.ru", async () => {
    const { http } = fakeHttp({
      [`GET ${C}/pubdomains`]: res(200, [{ container: "other", uuid: "x" }, { container: "b1", uuid: "u-1" }]),
    });
    expect(await getPublicDomain(http, "t", "ru-7", "b1")).toBe("u-1.selstorage.ru");
  });
  test("ждёт появления домена", async () => {
    const { http, calls } = fakeHttp({
      [`GET ${C}/pubdomains`]: [res(200, []), res(200, []), res(200, [{ container: "b1", uuid: "u-2" }])],
    });
    expect(await waitPublicDomain(http, "t", "ru-7", "b1", { sleep: noSleep })).toBe("u-2.selstorage.ru");
    expect(calls).toHaveLength(3);
  });
  test("не дождался — ошибка с числом попыток", async () => {
    const { http } = fakeHttp({ [`GET ${C}/pubdomains`]: res(200, []) });
    await expect(waitPublicDomain(http, "t", "ru-7", "b1", { attempts: 2, sleep: noSleep })).rejects.toThrow(/2 попыток/);
  });
});

describe("CDN-ресурс", () => {
  const spec = { name: "cdn-1", originHost: "u-1.selstorage.ru" };
  test("создание: origin — публичный домен бакета по https", async () => {
    const { http, calls } = fakeHttp({
      [`POST ${CDN}/resources`]: res(200, { status: "accept", resource_id: "r1", cdn_domain: "r1.selcdn.net" }),
    });
    expect(await createCdnResource(http, "t", spec)).toEqual({ id: "r1", cdnDomain: "r1.selcdn.net" });
    expect(calls[0].body).toEqual({
      name: "cdn-1",
      origin: { servers: { "u-1.selstorage.ru": { port: 443 } }, https: true },
    });
  });
  test("status error в теле 200 — ошибка с описанием", async () => {
    const { http } = fakeHttp({
      [`PATCH ${CDN}/resources/r1`]: res(200, { status: "error", message: "Json invalid", description: "bad names" }),
    });
    await expect(updateCdnResource(http, "t", "r1", spec)).rejects.toThrow(/Json invalid: bad names/);
  });
  test("удаление: DELETE прошёл", async () => {
    const { http } = fakeHttp({ [`DELETE ${CDN}/resources/r1`]: res(204) });
    expect(await removeCdnResource(http, "t", "r1")).toBe("deleted");
  });
  test("удаление без DELETE в API — выключить и освободить домены", async () => {
    const { http, calls } = fakeHttp({
      [`DELETE ${CDN}/resources/r1`]: res(405),
      [`GET ${CDN}/resources/r1`]: res(200, { id: "r1" }),
      [`PATCH ${CDN}/resources/r1`]: res(200, { status: "accept" }),
    });
    expect(await removeCdnResource(http, "t", "r1")).toBe("deactivated");
    expect(calls[2].body).toEqual({ active: false, names: [] });
  });
  test("имя с точкой отвергается до API", () => {
    expect(checkCdnName("cdn-ok_1")).toBe("cdn-ok_1");
    expect(() => checkCdnName("cdn.example.ru")).toThrow(/до 50 символов/);
  });
});
