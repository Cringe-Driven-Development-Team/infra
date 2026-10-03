import * as pulumi from "@pulumi/pulumi";
import { credentialsFromEnv, curl, projectToken, type Http, type HttpResult } from "./bootstrap/selectel-s3";

// Тип бакета, CDN-ресурс и свои домены — API Selectel, которых нет ни в провайдере selectel, ни в S3 API (ACL и
// Public Access Block Selectel не поддерживает). Здесь — функции API (тестируются с
// подменой http, selectel-storage.test.ts) и dynamic-ресурсы Pulumi поверх них.
// Все запросы — с project-токеном сервисного пользователя из selectel.env: токен идёт через stdin
// curl (-K -), в список процессов и в стейт не попадает.

const request = (http: Http, token: string, method: string, url: string, body?: unknown) =>
  http(
    [
      "-X", method, "-H", "Accept: application/json",
      ...(body === undefined ? [] : ["-H", "Content-Type: application/json", "--data-raw", JSON.stringify(body)]),
      "-K", "-", url,
    ],
    `header = "X-Auth-Token: ${token}"\n`,
  );

const ok = (res: HttpResult) => res.status >= 200 && res.status < 300;

function fail(what: string, res: HttpResult): never {
  throw new Error(`${what}: HTTP ${res.status || "без ответа"}: ${res.body.slice(0, 300)}`);
}

function json(what: string, res: HttpResult): any {
  try {
    return JSON.parse(res.body);
  } catch {
    return fail(`${what}: ответ не JSON`, res);
  }
}

// ---------------------------------------------------------------------------
// Бакет: тип (public/private) — API управления хранилищем пула.
// ---------------------------------------------------------------------------

export type BucketType = "public" | "private";

const containerUrl = (pool: string, bucket: string) =>
  `https://api.${pool}.storage.selcloud.ru/v2/containers/${encodeURIComponent(bucket)}`;

export async function setBucketType(http: Http, token: string, pool: string, bucket: string, type: BucketType) {
  const res = await request(http, token, "PUT", `${containerUrl(pool, bucket)}/options`, { general: { type } });
  if (!ok(res)) fail(`Тип бакета ${bucket} → ${type}`, res);
}

// undefined — бакета нет
export async function getBucketType(http: Http, token: string, pool: string, bucket: string): Promise<string | undefined> {
  const res = await request(http, token, "GET", `${containerUrl(pool, bucket)}/options`);
  if (res.status === 404) return undefined;
  if (!ok(res)) fail(`Настройки бакета ${bucket}`, res);
  return json(`Настройки бакета ${bucket}`, res).general?.type;
}

// Публичный домен <uuid>.selstorage.ru выдаётся бакету при переводе в public; у приватного — нет:
// на pubdomains Selectel отвечает ему 204 с пустым телом (read при pulumi refresh падал на разборе JSON).
export async function getPublicDomain(http: Http, token: string, pool: string, bucket: string): Promise<string | undefined> {
  const res = await request(http, token, "GET", `${containerUrl(pool, bucket)}/pubdomains`);
  if (res.status === 404 || res.status === 204) return undefined;
  if (!ok(res)) fail(`Публичный домен бакета ${bucket}`, res);
  if (res.body.trim() === "") return undefined;
  const list: { container: string; uuid: string }[] = json(`Публичный домен бакета ${bucket}`, res);
  const found = list.find((d) => d.container === bucket);
  return found ? `${found.uuid}.selstorage.ru` : undefined;
}

export interface Waiting {
  attempts?: number;
  intervalMs?: number;
  sleep?: (ms: number) => Promise<void>;
}
const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function waitPublicDomain(
  http: Http, token: string, pool: string, bucket: string, w: Waiting = {},
): Promise<string> {
  const attempts = w.attempts ?? 10;
  for (let i = 1; ; i++) {
    const domain = await getPublicDomain(http, token, pool, bucket);
    if (domain) return domain;
    if (i >= attempts) {
      throw new Error(`У бакета ${bucket} нет публичного домена после перевода в public (${attempts} попыток)`);
    }
    await (w.sleep ?? defaultSleep)(w.intervalMs ?? 3000);
  }
}

// ---------------------------------------------------------------------------
// CDN: HTTP-ресурс с бакетом-источником — CDN API v3.
// ---------------------------------------------------------------------------

const cdnApi = "https://api.selectel.ru/cdn/v3";

export interface CdnSpec {
  name: string;          // ^[a-zA-Zа-яА-ЯёЁ0-9\-\_\ ]{1,50}$ — без точек
  originHost: string;    // публичный домен бакета <uuid>.selstorage.ru
}

export function checkCdnName(name: string): string {
  if (!/^[a-zA-Zа-яА-ЯёЁ0-9\-_ ]{1,50}$/.test(name)) {
    throw new Error(`Имя CDN-ресурса «${name}»: только буквы, цифры, «-», «_», пробел, до 50 символов`);
  }
  return name;
}

const cdnBody = (s: CdnSpec) => ({
  name: s.name,
  origin: { servers: { [s.originHost]: { port: 443 } }, https: true },
});

// Создание и изменение отвечают 200 и в теле status: accept или error.
function accepted(what: string, res: HttpResult): any {
  if (!ok(res)) fail(what, res);
  const body = json(what, res);
  if (body.status !== "accept") {
    throw new Error(`${what}: ${body.message ?? "error"}: ${body.description ?? res.body.slice(0, 300)}`);
  }
  return body;
}

export async function createCdnResource(http: Http, token: string, s: CdnSpec): Promise<{ id: string; cdnDomain: string }> {
  const body = accepted(`Создание CDN-ресурса ${s.name}`, await request(http, token, "POST", `${cdnApi}/resources`, cdnBody(s)));
  return { id: body.resource_id, cdnDomain: body.cdn_domain };
}

export async function updateCdnResource(http: Http, token: string, id: string, s: CdnSpec) {
  accepted(`Изменение CDN-ресурса ${id}`, await request(http, token, "PATCH", `${cdnApi}/resources/${id}`, cdnBody(s)));
}

// undefined — ресурса нет
export async function getCdnResource(http: Http, token: string, id: string): Promise<any | undefined> {
  const res = await request(http, token, "GET", `${cdnApi}/resources/${id}`);
  if (res.status === 404) return undefined;
  if (!ok(res)) fail(`CDN-ресурс ${id}`, res);
  return json(`CDN-ресурс ${id}`, res);
}

// DELETE в документации CDN API v3 нет. Пробуем его; не вышло — ресурс выключается и освобождает
// свои домены (иначе новый ресурс с теми же names не создать), удалить его можно в панели.
export async function removeCdnResource(http: Http, token: string, id: string): Promise<"deleted" | "deactivated"> {
  const res = await request(http, token, "DELETE", `${cdnApi}/resources/${id}`);
  if (ok(res) || (await getCdnResource(http, token, id)) === undefined) return "deleted";
  accepted(
    `Выключение CDN-ресурса ${id}`,
    await request(http, token, "PATCH", `${cdnApi}/resources/${id}`, { active: false, names: [] }),
  );
  return "deactivated";
}

// ---------------------------------------------------------------------------
// Свои домены CDN-ресурса и бакета — CNAME в зоне DNS Selectel; записи создаёт index.ts, здесь —
// привязка. Сертификаты своих доменов выпускают в панели, не здесь.
// ---------------------------------------------------------------------------

// Selectel сам проверяет CNAME и до этого домен не принимает: свежая запись — короткий повтор.
// Это не ожидание распространения DNS: не успело — ошибка, повторный up.
const bindWaiting = (w: Waiting) => ({
  attempts: w.attempts ?? 18,
  intervalMs: w.intervalMs ?? 10000,
  sleep: w.sleep ?? defaultSleep,
});

async function patchCdnNames(http: Http, token: string, id: string, names: string[]) {
  accepted(`Домены CDN-ресурса ${id}`, await request(http, token, "PATCH", `${cdnApi}/resources/${id}`, { names }));
}

// Домен, который ещё не CNAME на cdn_domain, CDN API отбрасывает молча, с тем же accept, — поэтому
// после PATCH сверяем names через GET.
export async function bindCdnDomain(http: Http, token: string, id: string, domain: string, w: Waiting = {}) {
  const { attempts, intervalMs, sleep } = bindWaiting(w);
  for (let i = 1; ; i++) {
    const r = await getCdnResource(http, token, id);
    if (r === undefined) throw new Error(`CDN-ресурса ${id} нет`);
    if ((r.names ?? []).includes(domain)) return;
    await patchCdnNames(http, token, id, [r.cdn_domain, domain]);
    if (((await getCdnResource(http, token, id))?.names ?? []).includes(domain)) return;
    if (i >= attempts) {
      throw new Error(
        `CDN-ресурс ${id} не принял домен ${domain} (${attempts} попыток): Selectel ещё не видит CNAME ` +
          `${domain} → ${r.cdn_domain}. Повторите pulumi up.`,
      );
    }
    await sleep(intervalMs);
  }
}

export async function unbindCdnDomain(http: Http, token: string, id: string, domain: string) {
  const r = await getCdnResource(http, token, id);
  if (r === undefined || !(r.names ?? []).includes(domain)) return;
  await patchCdnNames(http, token, id, (r.names as string[]).filter((n) => n !== domain));
}

export async function getBucketDomains(http: Http, token: string, pool: string, bucket: string): Promise<string[] | undefined> {
  const res = await request(http, token, "GET", `${containerUrl(pool, bucket)}/domains`);
  if (res.status === 404) return undefined;
  if (!ok(res)) fail(`Домены бакета ${bucket}`, res);
  return res.body.trim() === "" ? [] : json(`Домены бакета ${bucket}`, res).domains ?? [];
}

// Свой домен бакета — только CNAME на access.<пул>.storage.selcloud.ru (ALIAS и вершину зоны
// Selectel отвергает); пока CNAME не виден — domain_lookup_failed / domain_cname_invalid.
export async function bindBucketDomain(
  http: Http, token: string, pool: string, bucket: string, domain: string, w: Waiting = {},
) {
  const { attempts, intervalMs, sleep } = bindWaiting(w);
  if ((await getBucketDomains(http, token, pool, bucket))?.includes(domain)) return;
  for (let i = 1; ; i++) {
    const res = await request(http, token, "PUT", `${containerUrl(pool, bucket)}/domains`, { domain_name: domain });
    if (ok(res)) return;
    if (!/domain_lookup_failed|domain_cname_invalid/.test(res.body) || i >= attempts) {
      fail(`Домен ${domain} бакета ${bucket} (попытка ${i})`, res);
    }
    await sleep(intervalMs);
  }
}

export async function unbindBucketDomain(http: Http, token: string, pool: string, bucket: string, domain: string) {
  if (!(await getBucketDomains(http, token, pool, bucket))?.includes(domain)) return;
  const res = await request(http, token, "DELETE", `${containerUrl(pool, bucket)}/domains/${encodeURIComponent(domain)}`);
  if (!ok(res) && res.status !== 404) fail(`Отвязка домена ${domain} от бакета ${bucket}`, res);
}

// ---------------------------------------------------------------------------
// Dynamic-ресурсы. Учётка — из окружения процесса провайдера (его запускает pulumi с тем же
// окружением, что после source pulumi/bootstrap/env.sh), в стейт попадают только id и домены.
// ---------------------------------------------------------------------------

const tokenFor = (projectId: string) => projectToken(curl, credentialsFromEnv(process.env), projectId);

const changed = <T extends object>(olds: T, news: T, keys: (keyof T)[]) =>
  keys.filter((k) => JSON.stringify(olds[k]) !== JSON.stringify(news[k])) as string[];

interface BucketAccessInputs {
  projectId: string;
  pool: string;
  bucket: string;
  type: BucketType;
}

const bucketAccessProvider: pulumi.dynamic.ResourceProvider<BucketAccessInputs> = {
  async diff(_id, olds: BucketAccessInputs, news: BucketAccessInputs) {
    const replaces = changed(olds, news, ["projectId", "pool", "bucket"]);
    // Код провайдера лежит в стейте (__provider), refresh и delete исполняют его оттуда. Без этого
    // сравнения правка провайдера в стейт не попадает: up видит «unchanged», refresh идёт старым кодом.
    const codeChanged = (olds as any).__provider !== (news as any).__provider;
    const changes = replaces.length > 0 || olds.type !== news.type || codeChanged;
    return { changes, replaces, deleteBeforeReplace: true };
  },
  async create(inputs: BucketAccessInputs) {
    const token = await tokenFor(inputs.projectId);
    await setBucketType(curl, token, inputs.pool, inputs.bucket, inputs.type);
    const publicDomain = inputs.type === "public"
      ? await waitPublicDomain(curl, token, inputs.pool, inputs.bucket)
      : "";
    return { id: `${inputs.projectId}/${inputs.bucket}`, outs: { ...inputs, publicDomain } };
  },
  async update(_id, _olds, news: BucketAccessInputs) {
    return { outs: (await bucketAccessProvider.create(news)).outs };
  },
  async read(id, props: any) {
    const token = await tokenFor(props.projectId);
    const type = await getBucketType(curl, token, props.pool, props.bucket);
    if (type === undefined) return { id: "", props: {} };
    const publicDomain = (await getPublicDomain(curl, token, props.pool, props.bucket)) ?? "";
    return { id, props: { ...props, type, publicDomain } };
  },
  // Удаление ресурса (флаг выключен или бакет удаляется) — бакет снова приватный.
  async delete(_id, props: any) {
    const token = await tokenFor(props.projectId);
    if ((await getBucketType(curl, token, props.pool, props.bucket)) !== undefined) {
      await setBucketType(curl, token, props.pool, props.bucket, "private");
    }
  },
};

export interface BucketAccessArgs {
  projectId: pulumi.Input<string>;
  pool: pulumi.Input<string>;
  bucket: pulumi.Input<string>;
  type: pulumi.Input<BucketType>;
}

// Тип бакета Selectel. Публичный — чтение объектов без авторизации через <uuid>.selstorage.ru;
// только он годится источником CDN и для привязки своего домена (BucketDomain).
export class BucketAccess extends pulumi.dynamic.Resource {
  declare public readonly publicDomain: pulumi.Output<string>;
  constructor(name: string, args: BucketAccessArgs, opts?: pulumi.CustomResourceOptions) {
    super(bucketAccessProvider, name, { ...args, publicDomain: undefined }, opts, "selectel-storage", "BucketAccess");
  }
}

interface CdnResourceInputs extends CdnSpec {
  projectId: string;
}

const cdnResourceProvider: pulumi.dynamic.ResourceProvider<CdnResourceInputs> = {
  async check(_olds, news: CdnResourceInputs) {
    checkCdnName(news.name);
    return { inputs: news };
  },
  async diff(_id, olds: CdnResourceInputs, news: CdnResourceInputs) {
    const replaces = changed(olds, news, ["projectId"]);
    const updates = changed(olds, news, ["name", "originHost"]);
    return { changes: replaces.length + updates.length > 0, replaces, deleteBeforeReplace: true };
  },
  async create(inputs: CdnResourceInputs) {
    const { id, cdnDomain } = await createCdnResource(curl, await tokenFor(inputs.projectId), inputs);
    return { id, outs: { ...inputs, cdnDomain } };
  },
  async update(id, olds: any, news: CdnResourceInputs) {
    await updateCdnResource(curl, await tokenFor(news.projectId), id, news);
    return { outs: { ...news, cdnDomain: olds.cdnDomain } };
  },
  async read(id, props: any) {
    const r = await getCdnResource(curl, await tokenFor(props.projectId), id);
    if (r === undefined) return { id: "", props: {} };
    return {
      id,
      props: {
        ...props,
        name: r.name,
        originHost: Object.keys(r.origin?.servers ?? {})[0] ?? "",
        cdnDomain: r.cdn_domain,
      },
    };
  },
  async delete(id, props: any) {
    await removeCdnResource(curl, await tokenFor(props.projectId), id);
  },
};

export interface CdnResourceArgs {
  projectId: pulumi.Input<string>;
  name: pulumi.Input<string>;
  originHost: pulumi.Input<string>;
}

// HTTP CDN-ресурс Selectel. Свой домен — CdnDomain, его сертификат — в панели; cdnDomain (<id>.selcdn.net) — цель его CNAME.
export class CdnResource extends pulumi.dynamic.Resource {
  declare public readonly cdnDomain: pulumi.Output<string>;
  constructor(name: string, args: CdnResourceArgs, opts?: pulumi.CustomResourceOptions) {
    super(cdnResourceProvider, name, { ...args, cdnDomain: undefined }, opts, "selectel-storage", "CdnResource");
  }
}

interface CdnDomainInputs {
  projectId: string;
  resourceId: string;
  domain: string;
}

const cdnDomainProvider: pulumi.dynamic.ResourceProvider<CdnDomainInputs> = {
  async diff(_id, olds: any, news: CdnDomainInputs) {
    const replaces = changed(olds, news, ["projectId", "resourceId", "domain"]);
    const codeChanged = olds.__provider !== (news as any).__provider;
    return { changes: replaces.length > 0 || codeChanged, replaces, deleteBeforeReplace: true };
  },
  async create(inputs: CdnDomainInputs) {
    await bindCdnDomain(curl, await tokenFor(inputs.projectId), inputs.resourceId, inputs.domain);
    return { id: `${inputs.resourceId}/${inputs.domain}`, outs: { ...inputs } };
  },
  async update(_id, _olds, news: CdnDomainInputs) {
    return { outs: (await cdnDomainProvider.create(news)).outs };
  },
  async read(id, props: any) {
    const r = await getCdnResource(curl, await tokenFor(props.projectId), props.resourceId);
    if (r === undefined || !(r.names ?? []).includes(props.domain)) return { id: "", props: {} };
    return { id, props };
  },
  async delete(_id, props: any) {
    await unbindCdnDomain(curl, await tokenFor(props.projectId), props.resourceId, props.domain);
  },
};

export interface CdnDomainArgs {
  projectId: pulumi.Input<string>;
  resourceId: pulumi.Input<string>;
  domain: pulumi.Input<string>;
}

// Свой домен CDN-ресурса: домен в names ресурса. Домен — уже CNAME на cdnDomain ресурса (запись в
// зоне создаётся раньше, dependsOn). Сертификат домена — в панели.
export class CdnDomain extends pulumi.dynamic.Resource {
  constructor(name: string, args: CdnDomainArgs, opts?: pulumi.CustomResourceOptions) {
    super(cdnDomainProvider, name, args, opts, "selectel-storage", "CdnDomain");
  }
}

interface BucketDomainInputs {
  projectId: string;
  pool: string;
  bucket: string;
  domain: string;
}

const bucketDomainProvider: pulumi.dynamic.ResourceProvider<BucketDomainInputs> = {
  async diff(_id, olds: any, news: BucketDomainInputs) {
    const replaces = changed(olds, news, ["projectId", "pool", "bucket", "domain"]);
    const codeChanged = olds.__provider !== (news as any).__provider;
    return { changes: replaces.length > 0 || codeChanged, replaces, deleteBeforeReplace: true };
  },
  async create(inputs: BucketDomainInputs) {
    await bindBucketDomain(curl, await tokenFor(inputs.projectId), inputs.pool, inputs.bucket, inputs.domain);
    return { id: `${inputs.projectId}/${inputs.bucket}/${inputs.domain}`, outs: { ...inputs } };
  },
  async update(_id, _olds, news: BucketDomainInputs) {
    return { outs: (await bucketDomainProvider.create(news)).outs };
  },
  async read(id, props: any) {
    const domains = await getBucketDomains(curl, await tokenFor(props.projectId), props.pool, props.bucket);
    return domains?.includes(props.domain) ? { id, props } : { id: "", props: {} };
  },
  async delete(_id, props: any) {
    await unbindBucketDomain(curl, await tokenFor(props.projectId), props.pool, props.bucket, props.domain);
  },
};

export interface BucketDomainArgs {
  projectId: pulumi.Input<string>;
  pool: pulumi.Input<string>;
  bucket: pulumi.Input<string>;
  domain: pulumi.Input<string>;
}

// Свой домен публичного бакета. Домен — уже CNAME на access.<пул>.storage.selcloud.ru (запись в зоне
// создаётся раньше, dependsOn). Сертификат домена — в панели: без него хранилище отвечает на домене
// своим сертификатом *.<пул>.storage.selcloud.ru, а HTTP перенаправляет на HTTPS.
export class BucketDomain extends pulumi.dynamic.Resource {
  constructor(name: string, args: BucketDomainArgs, opts?: pulumi.CustomResourceOptions) {
    super(bucketDomainProvider, name, args, opts, "selectel-storage", "BucketDomain");
  }
}
