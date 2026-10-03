import { describe, expect, test } from "bun:test";
import type { Http, HttpResult } from "./bootstrap/selectel-s3";
import {
  bindBucketDomain, bindCdnDomain, getBucketDomains, unbindBucketDomain, unbindCdnDomain,
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

describe("unbindCdnDomain", () => {
  test("убирает только свой домен, технический остаётся", async () => {
    const { http, calls } = fakeHttp((method) =>
      method === "GET"
        ? jsonRes({ id: "r1", cdn_domain: "r1.selcdn.net", names: ["r1.selcdn.net", "cdn.example.ru"] })
        : jsonRes({ status: "accept" }));
    await unbindCdnDomain(http, "tok", "r1", "cdn.example.ru");
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(JSON.parse(patch.args[patch.args.indexOf("--data-raw") + 1])).toEqual({ names: ["r1.selcdn.net"] });
  });

  test("ресурса или домена уже нет — запросов на изменение нет", async () => {
    const { http, calls } = fakeHttp(() => ({ status: 404, body: "" }));
    await unbindCdnDomain(http, "tok", "r1", "cdn.example.ru");
    expect(calls.map((c) => c.method)).toEqual(["GET"]);
  });
});

describe("домен бакета", () => {
  test("getBucketDomains: 404 — бакета нет, пустое тело — доменов нет", async () => {
    expect(await getBucketDomains(fakeHttp(() => ({ status: 404, body: "" })).http, "tok", "ru-7", "b")).toBeUndefined();
    expect(await getBucketDomains(fakeHttp(() => ({ status: 204, body: "" })).http, "tok", "ru-7", "b")).toEqual([]);
    expect(await getBucketDomains(fakeHttp(() => jsonRes({ domains: ["s3.example.ru"] })).http, "tok", "ru-7", "b"))
      .toEqual(["s3.example.ru"]);
  });

  test("домен уже привязан — PUT не шлётся", async () => {
    const { http, calls } = fakeHttp(() => jsonRes({ domains: ["s3.example.ru"] }));
    await bindBucketDomain(http, "tok", "ru-7", "b", "s3.example.ru", noWait);
    expect(calls.map((c) => c.method)).toEqual(["GET"]);
  });

  test("повторяет, пока Selectel не увидит CNAME", async () => {
    let puts = 0;
    const { http, calls } = fakeHttp((method) => {
      if (method === "GET") return jsonRes({ domains: [] });
      return ++puts < 3 ? jsonRes({ error: "domain_cname_invalid" }, 422) : { status: 204, body: "" };
    });
    await bindBucketDomain(http, "tok", "ru-7", "b", "s3.example.ru", noWait);
    expect(puts).toBe(3);
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toBe("https://api.ru-7.storage.selcloud.ru/v2/containers/b/domains");
    expect(JSON.parse(put.args[put.args.indexOf("--data-raw") + 1])).toEqual({ domain_name: "s3.example.ru" });
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

  test("отвязка не привязанного домена — без DELETE", async () => {
    const { http, calls } = fakeHttp(() => jsonRes({ domains: [] }));
    await unbindBucketDomain(http, "tok", "ru-7", "b", "s3.example.ru");
    expect(calls.map((c) => c.method)).toEqual(["GET"]);
  });

  test("отвязка сверяется через GET: домен остался — второй запрос, потом ошибка с подсказкой про панель", async () => {
    const { http, calls } = fakeHttp((method) =>
      method === "GET" ? jsonRes({ domains: ["s3.example.ru"] }) : { status: 404, body: "" });
    await expect(unbindBucketDomain(http, "tok", "ru-7", "b", "s3.example.ru")).rejects.toThrow(/остался привязан.*панели/s);
    const deletes = calls.filter((c) => c.method === "DELETE");
    expect(deletes.map((c) => c.url)).toEqual([
      "https://api.ru-7.storage.selcloud.ru/v2/containers/b/domains/s3.example.ru",
      "https://api.ru-7.storage.selcloud.ru/v2/containers/b/domains",
    ]);
  });

  test("отвязка удалась первым запросом — второго нет", async () => {
    let bound = true;
    const { http, calls } = fakeHttp((method) => {
      if (method === "GET") return jsonRes({ domains: bound ? ["s3.example.ru"] : [] });
      bound = false;
      return { status: 204, body: "" };
    });
    await unbindBucketDomain(http, "tok", "ru-7", "b", "s3.example.ru");
    expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1);
  });
});
