import { describe, expect, test } from "bun:test";
import type { Http, HttpResult } from "./bootstrap/selectel-s3";
import { bindCdnDomain, unbindCdnDomain } from "./selectel-storage";

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
