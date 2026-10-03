import { describe, expect, test } from "bun:test";
import type { Http, HttpResult } from "./bootstrap/selectel-s3";
import {
  bindBucketDomain, bindCdnDomain, cdnCertificateStatus, ensureBucketDomain, getBucketDomains,
  orderCdnCertificate, uploadBucketCertificate,
} from "./selectel-storage";

type Route = (method: string, url: string, stdin: string) => HttpResult | undefined;

// Подмена curl: метод — после -X, адрес — последний аргумент; ответы отдаёт route.
function fakeHttp(route: Route) {
  const calls: { method: string; url: string; args: string[]; stdin: string }[] = [];
  const http: Http = async (args, stdin) => {
    const method = args[args.indexOf("-X") + 1];
    const url = args[args.length - 1];
    calls.push({ method, url, args, stdin });
    return route(method, url, stdin) ?? { status: 599, body: `нет ответа для ${method} ${url}` };
  };
  return { http, calls };
}

const jsonRes = (body: unknown, status = 200): HttpResult => ({ status, body: JSON.stringify(body) });
const noWait = { attempts: 3, intervalMs: 0, sleep: async () => {} };

describe("bindCdnDomain", () => {
  const resource = (names: string[]) => jsonRes({ id: "r1", cdn_domain: "r1.selcdn.net", names });

  test("PATCH names с техническим и своим доменом, сверка через GET", async () => {
    let names = ["r1.selcdn.net"];
    const { http, calls } = fakeHttp((method) => {
      if (method === "GET") return resource(names);
      names = ["r1.selcdn.net", "cdn.example.ru"];
      return jsonRes({ status: "accept" });
    });
    await bindCdnDomain(http, "tok", "r1", "cdn.example.ru", noWait);
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(JSON.parse(patch.args[patch.args.indexOf("--data-raw") + 1])).toEqual({
      names: ["r1.selcdn.net", "cdn.example.ru"],
    });
  });

  test("домен уже привязан — PATCH не шлётся", async () => {
    const { http, calls } = fakeHttp(() => resource(["r1.selcdn.net", "cdn.example.ru"]));
    await bindCdnDomain(http, "tok", "r1", "cdn.example.ru", noWait);
    expect(calls.map((c) => c.method)).toEqual(["GET"]);
  });

  test("accept, но домен отброшен — ошибка после всех попыток", async () => {
    const { http, calls } = fakeHttp((method) =>
      method === "GET" ? resource(["r1.selcdn.net"]) : jsonRes({ status: "accept" }));
    await expect(bindCdnDomain(http, "tok", "r1", "cdn.example.ru", noWait)).rejects.toThrow(
      /не принял домен cdn\.example\.ru \(3 попыток\).*r1\.selcdn\.net.*pulumi up/s,
    );
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(3);
  });
});

describe("сертификат CDN", () => {
  test("450 в теле при HTTP 200 — заказа нет", async () => {
    const { http } = fakeHttp(() => jsonRes({ status: 450, message: "Invalid Request" }));
    expect(await cdnCertificateStatus(http, "tok", "r1")).toBeUndefined();
  });

  test("выпущенный или заказанный сертификат заново не заказывается", async () => {
    for (const status of ["accepted", "processed"]) {
      const { http, calls } = fakeHttp(() => jsonRes({ data: { task_status: status } }));
      expect(await orderCdnCertificate(http, "tok", "r1")).toBe(status);
      expect(calls.map((c) => c.method)).toEqual(["GET"]);
    }
  });

  test("после failed заказывает заново", async () => {
    let ordered = false;
    const { http } = fakeHttp((method) => {
      if (method === "POST") {
        ordered = true;
        return jsonRes({});
      }
      return jsonRes({ data: { task_status: ordered ? "accepted" : "failed" } });
    });
    expect(await orderCdnCertificate(http, "tok", "r1")).toBe("accepted");
    expect(ordered).toBe(true);
  });

  test("450 на заказе — ошибка", async () => {
    const { http } = fakeHttp(() => jsonRes({ status: 450, message: "Invalid Request" }));
    await expect(orderCdnCertificate(http, "tok", "r1")).rejects.toThrow(/450 Invalid Request/);
  });
});

describe("домен бакета", () => {
  test("getBucketDomains: 404 — бакета нет, пустое тело — доменов нет", async () => {
    expect(await getBucketDomains(fakeHttp(() => ({ status: 404, body: "" })).http, "tok", "ru-7", "b")).toBeUndefined();
    expect(await getBucketDomains(fakeHttp(() => ({ status: 204, body: "" })).http, "tok", "ru-7", "b")).toEqual([]);
    expect(await getBucketDomains(fakeHttp(() => jsonRes({ domains: [] })).http, "tok", "ru-7", "b")).toEqual([]);
  });

  test("повторяет, пока Selectel не увидит CNAME", async () => {
    let puts = 0;
    const { http } = fakeHttp((method) => {
      if (method === "GET") return jsonRes({ domains: [] });
      return ++puts < 3 ? jsonRes({ error: "domain_cname_invalid" }, 422) : { status: 204, body: "" };
    });
    await bindBucketDomain(http, "tok", "ru-7", "b", "s3.example.ru", noWait);
    expect(puts).toBe(3);
  });

  test("другая ошибка — сразу, без повторов", async () => {
    let puts = 0;
    const { http } = fakeHttp((method) => {
      if (method === "GET") return jsonRes({ domains: [] });
      puts++;
      return jsonRes({ error: "forbidden" }, 403);
    });
    await expect(bindBucketDomain(http, "tok", "ru-7", "b", "s3.example.ru", noWait)).rejects.toThrow(/403.*forbidden/);
    expect(puts).toBe(1);
  });
});

describe("uploadBucketCertificate", () => {
  test("токен и ключ — только в stdin curl", async () => {
    const { http, calls } = fakeHttp(() => jsonRes({}));
    await uploadBucketCertificate(http, "tok-1", "ru-7", "c-v1", "CERT\n", "-----BEGIN PRIVATE KEY-----\nabc\n");
    const [call] = calls;
    expect(call.args.join(" ")).not.toMatch(/tok-1|PRIVATE KEY/);
    expect(call.stdin).toContain('header = "X-Auth-Token: tok-1"');
    // curl раскрывает \\ и \" в значении конфига: после этого в теле остаётся исходный JSON
    const data = call.stdin.split("\n").find((l) => l.startsWith("data-binary = "))!;
    const raw = data.slice('data-binary = "'.length, -1).replace(/\\(["\\])/g, "$1");
    expect(JSON.parse(raw)).toEqual({
      name: "c-v1", certificate: "CERT\n", private_key: "-----BEGIN PRIVATE KEY-----\nabc\n",
    });
  });
});

describe("ensureBucketDomain", () => {
  const spec = { pool: "ru-7", bucket: "b", domain: "s3.example.ru", certName: "s3-example-ru" };
  const key = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----";

  function api(le: object | undefined) {
    return fakeHttp((method, url) => {
      if (url.endsWith("/domains")) return jsonRes({ domains: ["s3.example.ru"] });
      if (url.endsWith("/certs/le/")) return jsonRes({ items: le ? [le] : [] });
      if (url.includes("/certs/le/issue")) return jsonRes({ id: "le-new", name: "s3-example-ru", status: "CREATING" });
      if (url.endsWith("/ca_chain")) return { status: 200, body: "-----BEGIN CERTIFICATE-----\nc\n-----END CERTIFICATE-----" };
      if (url.endsWith("/private_key")) return { status: 200, body: key };
      if (url.endsWith("/v2/ssl") && method === "POST") return jsonRes({});
      if (method === "DELETE") return { status: 204, body: "" };
      return undefined;
    });
  }

  test("сертификата нет — заказ без ожидания, в хранилище ничего не уходит", async () => {
    const { http, calls } = api(undefined);
    const state = await ensureBucketDomain(http, "st", "ct", spec, {}, noWait);
    expect(state).toMatchObject({ leCertificateId: "le-new", certificateStatus: "CREATING", uploadedVersion: "" });
    expect(calls.some((c) => c.url.endsWith("/v2/ssl"))).toBe(false);
    const issue = calls.find((c) => c.url.includes("/issue"))!;
    expect(issue.url).toContain("dnsv2=true");
    expect(JSON.parse(issue.args[issue.args.indexOf("--data-raw") + 1])).toEqual({
      name: "s3-example-ru", domains: ["s3.example.ru"],
    });
  });

  test("ACTIVE — сертификат уходит в хранилище, ключа в состоянии нет", async () => {
    const { http, calls } = api({ id: "le1", name: "s3-example-ru", status: "ACTIVE", version: 1, knox_cert_id: "k1", expire_at: "2027-01-01" });
    const state = await ensureBucketDomain(http, "st", "ct", spec, {}, noWait);
    expect(state).toEqual({
      leCertificateId: "le1", certificateStatus: "ACTIVE", issuedVersion: "1", uploadedVersion: "1",
      s3CertificateName: "s3-example-ru-v1", expireAt: "2027-01-01",
    });
    expect(JSON.stringify(state)).not.toContain("PRIVATE KEY");
    expect(calls.filter((c) => c.url.endsWith("/v2/ssl") && c.method === "POST")).toHaveLength(1);
  });

  test("та же версия уже загружена — повторной загрузки нет", async () => {
    const { http, calls } = api({ id: "le1", name: "s3-example-ru", status: "ACTIVE", version: 1, knox_cert_id: "k1" });
    await ensureBucketDomain(http, "st", "ct", spec, { uploadedVersion: "1", s3CertificateName: "s3-example-ru-v1" }, noWait);
    expect(calls.some((c) => c.url.endsWith("/v2/ssl"))).toBe(false);
  });

  test("продлённая версия заменяет старую в хранилище", async () => {
    const { http, calls } = api({ id: "le1", name: "s3-example-ru", status: "ACTIVE", version: 2, knox_cert_id: "k1" });
    const state = await ensureBucketDomain(http, "st", "ct", spec, { uploadedVersion: "1", s3CertificateName: "s3-example-ru-v1" }, noWait);
    expect(state.s3CertificateName).toBe("s3-example-ru-v2");
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/v2/ssl/s3-example-ru-v1"))).toBe(true);
  });

  test("ERROR — сертификат удаляется и заказывается заново", async () => {
    const { http, calls } = api({ id: "le-bad", name: "s3-example-ru", status: "ERROR" });
    const state = await ensureBucketDomain(http, "st", "ct", spec, {}, noWait);
    expect(state.leCertificateId).toBe("le-new");
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/certs/le/le-bad"))).toBe(true);
  });

  test("в ответе менеджера сертификатов не PEM — ошибка без тела", async () => {
    const { http } = fakeHttp((_m, url) => {
      if (url.endsWith("/domains")) return jsonRes({ domains: ["s3.example.ru"] });
      if (url.endsWith("/certs/le/")) return jsonRes({ items: [{ id: "le1", name: "s3-example-ru", status: "ACTIVE", version: 1, knox_cert_id: "k1" }] });
      return jsonRes({ unexpected: "shape" });
    });
    await expect(ensureBucketDomain(http, "st", "ct", spec, {}, noWait)).rejects.toThrow(/ca_chain.*не PEM/);
  });
});
