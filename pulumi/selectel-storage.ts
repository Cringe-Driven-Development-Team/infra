import * as pulumi from "@pulumi/pulumi";
import { credentialsFromEnv, curl, projectToken, type Http, type HttpResult } from "./bootstrap/selectel-s3";

// Тип бакета, CDN-ресурс, свои домены и их сертификаты — API Selectel, которых нет ни в провайдере selectel, ни в S3 API (ACL и
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
// Свои домены: CDN-ресурса (CDN API v3) и бакета (API хранилища пула), сертификаты Let's Encrypt.
// Оба домена — CNAME в зоне DNS Selectel; запись создаёт index.ts, здесь — привязка и сертификат.
// ---------------------------------------------------------------------------

// Selectel сам проверяет CNAME и до этого домен не принимает: свежая запись — короткий повтор.
// Это не ожидание распространения DNS и не ожидание сертификата: не успело — ошибка, повторный up.
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

// Статус заказа Let's Encrypt у CDN-ресурса: accepted → processed/failed; undefined — заказа нет
// (без своего домена API отвечает 200 с телом {"status":450,"message":"Invalid Request"}).
export async function cdnCertificateStatus(http: Http, token: string, id: string): Promise<string | undefined> {
  const res = await request(http, token, "GET", `${cdnApi}/letsencrypt/${id}`);
  if (!ok(res)) fail(`Сертификат CDN-ресурса ${id}`, res);
  return json(`Сертификат CDN-ресурса ${id}`, res).data?.task_status;
}

// Заказ сертификата: отправили и вышли, готовности не ждём. Уже заказанный или выпущенный не трогаем.
export async function orderCdnCertificate(http: Http, token: string, id: string): Promise<string> {
  const current = await cdnCertificateStatus(http, token, id);
  if (current === "accepted" || current === "processed") return current;
  const what = `Заказ Let's Encrypt для CDN-ресурса ${id}`;
  const res = await request(http, token, "POST", `${cdnApi}/letsencrypt/${id}`);
  if (!ok(res)) fail(what, res);
  const body = res.body.trim() === "" ? {} : json(what, res);
  if (typeof body.status === "number" && body.status >= 400) {
    throw new Error(`${what}: ${body.status} ${body.message ?? res.body.slice(0, 300)}`);
  }
  return (await cdnCertificateStatus(http, token, id)) ?? body.data?.task_status ?? "accepted";
}

const domainNames = (body: any): string[] =>
  (body?.domains ?? []).map((d: any) => (typeof d === "string" ? d : d.domain_name ?? d.name));

export async function getBucketDomains(http: Http, token: string, pool: string, bucket: string): Promise<string[] | undefined> {
  const res = await request(http, token, "GET", `${containerUrl(pool, bucket)}/domains`);
  if (res.status === 404) return undefined;
  if (!ok(res)) fail(`Домены бакета ${bucket}`, res);
  return res.body.trim() === "" ? [] : domainNames(json(`Домены бакета ${bucket}`, res));
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

// Let's Encrypt Selectel (api.selectel.ru/certs/le): проверка DNS-01, TXT-запись в зоне DNS-хостинга
// Selectel ставит сам. Выпущенный сертификат лежит в менеджере сертификатов проекта (knox_cert_id).
const leApi = "https://api.selectel.ru/certs/le";
const certManagerApi = "https://cloud.api.selcloud.ru/certificate-manager/v1";

export interface LeCertificate {
  id: string;
  name: string;
  status: string;          // creating, active, renewing, invalid, error — API отдаёт строчными
  version?: number;
  knox_cert_id?: string;
  expire_at?: string;
  error_description?: string;
}

export async function findLeCertificate(http: Http, token: string, name: string): Promise<LeCertificate | undefined> {
  const res = await request(http, token, "GET", `${leApi}/`);
  if (!ok(res)) fail("Сертификаты Let's Encrypt", res);
  return (json("Сертификаты Let's Encrypt", res).items ?? []).find((c: any) => c.name === name && !c.deleted_at);
}

export async function issueLeCertificate(http: Http, token: string, name: string, domain: string): Promise<LeCertificate> {
  const what = `Выпуск Let's Encrypt для ${domain}`;
  const res = await request(http, token, "POST", `${leApi}/issue?dnsv2=true`, { name, domains: [domain] });
  if (!ok(res)) fail(what, res);
  return json(what, res);
}

export async function deleteLeCertificate(http: Http, token: string, id: string) {
  const res = await request(http, token, "DELETE", `${leApi}/${id}`);
  if (!ok(res) && res.status !== 404) fail(`Удаление сертификата Let's Encrypt ${id}`, res);
}

// PEM из менеджера сертификатов: ca_chain — цепочка, private_key — ключ.
async function certificatePem(http: Http, token: string, certId: string, part: "ca_chain" | "private_key"): Promise<string> {
  const what = `Сертификат ${certId} (${part})`;
  const res = await request(http, token, "GET", `${certManagerApi}/cert/${certId}/${part}`);
  // Тело — ключ: в текст ошибки оно не попадает
  if (!ok(res)) throw new Error(`${what}: HTTP ${res.status || "без ответа"}`);
  const pem = res.body.trim();
  if (!pem.startsWith("-----BEGIN")) throw new Error(`${what}: в ответе не PEM`);
  return `${pem}\n`;
}

const sslUrl = (pool: string) => `https://api.${pool}.storage.selcloud.ru/v2/ssl`;

// Сертификат своего домена в S3 — отдельный список сертификатов хранилища проекта; Selectel продлевает
// Let's Encrypt сам, но в хранилище новую версию кладём мы. Ключ идёт через stdin curl.
export async function uploadBucketCertificate(
  http: Http, token: string, pool: string, name: string, certificate: string, privateKey: string,
) {
  // И токен, и тело с ключом — в конфиге curl из stdin (-K -): в списке процессов их нет.
  const body = JSON.stringify({ name, certificate, private_key: privateKey }).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const res = await http(
    ["-X", "POST", "-H", "Accept: application/json", "-H", "Content-Type: application/json", "-K", "-", sslUrl(pool)],
    `header = "X-Auth-Token: ${token}"\ndata-binary = "${body}"\n`,
  );
  if (!ok(res)) fail(`Сертификат ${name} в хранилище ${pool}`, res);
}

export async function deleteBucketCertificate(http: Http, token: string, pool: string, name: string) {
  const res = await request(http, token, "DELETE", `${sslUrl(pool)}/${encodeURIComponent(name)}`);
  if (!ok(res) && res.status !== 404) fail(`Удаление сертификата ${name} из хранилища ${pool}`, res);
}

export interface BucketDomainSpec {
  pool: string;
  bucket: string;
  domain: string;
  certName: string;
}

export interface BucketDomainState {
  leCertificateId: string;
  certificateStatus: string;
  issuedVersion: string;      // версия сертификата у Let's Encrypt Selectel
  uploadedVersion: string;    // версия, загруженная в хранилище; "" — ещё не загружен
  s3CertificateName: string;
  expireAt: string;
}

// Домен бакета и его сертификат за один проход, без ожидания выпуска: сертификат ещё не ACTIVE —
// в состоянии остаётся пустой uploadedVersion, и следующий up загружает его в хранилище.
// storageToken — проект бакета, certToken — проект, где выпускается сертификат.
export async function ensureBucketDomain(
  http: Http, storageToken: string, certToken: string, s: BucketDomainSpec,
  prev: Partial<BucketDomainState> = {}, w: Waiting = {},
): Promise<BucketDomainState> {
  await bindBucketDomain(http, storageToken, s.pool, s.bucket, s.domain, w);
  // Статус сравниваем без регистра: документация пишет ACTIVE, API отвечает active
  const status = (c: LeCertificate) => c.status.toUpperCase();
  let le = await findLeCertificate(http, certToken, s.certName);
  if (le && status(le) === "ERROR") {
    await deleteLeCertificate(http, certToken, le.id);
    le = undefined;
  }
  le ??= await issueLeCertificate(http, certToken, s.certName, s.domain);
  const issuedVersion = String(le.version ?? "");
  let uploadedVersion = prev.uploadedVersion ?? "";
  let s3CertificateName = prev.s3CertificateName ?? "";
  if (status(le) === "ACTIVE" && le.knox_cert_id && uploadedVersion !== issuedVersion) {
    const name = `${s.certName}-v${issuedVersion}`;
    await uploadBucketCertificate(
      http, storageToken, s.pool, name,
      await certificatePem(http, certToken, le.knox_cert_id, "ca_chain"),
      await certificatePem(http, certToken, le.knox_cert_id, "private_key"),
    );
    if (s3CertificateName && s3CertificateName !== name) {
      await deleteBucketCertificate(http, storageToken, s.pool, s3CertificateName);
    }
    uploadedVersion = issuedVersion;
    s3CertificateName = name;
  }
  return {
    leCertificateId: le.id,
    certificateStatus: status(le),
    issuedVersion,
    uploadedVersion,
    s3CertificateName,
    expireAt: le.expire_at ?? "",
  };
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

// HTTP CDN-ресурс Selectel. Свой домен и сертификат — CdnDomain; cdnDomain (<id>.selcdn.net) — цель его CNAME.
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
    // Пока сертификат не выпущен, каждый up перечитывает статус заказа (и заказывает заново после failed)
    const changes = replaces.length > 0 || codeChanged || olds.certificateStatus !== "processed";
    return { changes, replaces, deleteBeforeReplace: true };
  },
  async create(inputs: CdnDomainInputs) {
    const token = await tokenFor(inputs.projectId);
    await bindCdnDomain(curl, token, inputs.resourceId, inputs.domain);
    const certificateStatus = await orderCdnCertificate(curl, token, inputs.resourceId);
    return { id: `${inputs.resourceId}/${inputs.domain}`, outs: { ...inputs, certificateStatus } };
  },
  async update(_id, _olds, news: CdnDomainInputs) {
    return { outs: (await cdnDomainProvider.create(news)).outs };
  },
  async read(id, props: any) {
    const token = await tokenFor(props.projectId);
    const r = await getCdnResource(curl, token, props.resourceId);
    if (r === undefined || !(r.names ?? []).includes(props.domain)) return { id: "", props: {} };
    const certificateStatus = (await cdnCertificateStatus(curl, token, props.resourceId)) ?? "";
    return { id, props: { ...props, certificateStatus } };
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

// Свой домен CDN-ресурса и сертификат Let's Encrypt к нему. Домен — уже CNAME на cdnDomain ресурса
// (запись в зоне создаётся раньше, dependsOn). certificateStatus: accepted → processed.
export class CdnDomain extends pulumi.dynamic.Resource {
  declare public readonly certificateStatus: pulumi.Output<string>;
  constructor(name: string, args: CdnDomainArgs, opts?: pulumi.CustomResourceOptions) {
    super(cdnDomainProvider, name, { ...args, certificateStatus: undefined }, opts, "selectel-storage", "CdnDomain");
  }
}

interface BucketDomainInputs extends BucketDomainSpec {
  projectId: string;
  certProjectId: string;
}

const bucketDomainState = (props: any): BucketDomainState => ({
  leCertificateId: props.leCertificateId ?? "",
  certificateStatus: props.certificateStatus ?? "",
  issuedVersion: props.issuedVersion ?? "",
  uploadedVersion: props.uploadedVersion ?? "",
  s3CertificateName: props.s3CertificateName ?? "",
  expireAt: props.expireAt ?? "",
});

const bucketDomainProvider: pulumi.dynamic.ResourceProvider<BucketDomainInputs> = {
  async diff(_id, olds: any, news: BucketDomainInputs) {
    const replaces = changed(olds, news, ["projectId", "certProjectId", "pool", "bucket", "domain", "certName"]);
    const codeChanged = olds.__provider !== (news as any).__provider;
    // Сертификат ещё не в хранилище или Selectel продлил его (issuedVersion обновляет refresh)
    const stale = !olds.uploadedVersion || olds.uploadedVersion !== olds.issuedVersion;
    return { changes: replaces.length > 0 || codeChanged || stale, replaces, deleteBeforeReplace: true };
  },
  async create(inputs: BucketDomainInputs) {
    const state = await ensureBucketDomain(
      curl, await tokenFor(inputs.projectId), await tokenFor(inputs.certProjectId), inputs,
    );
    return { id: `${inputs.projectId}/${inputs.bucket}/${inputs.domain}`, outs: { ...inputs, ...state } };
  },
  async update(_id, olds: any, news: BucketDomainInputs) {
    const state = await ensureBucketDomain(
      curl, await tokenFor(news.projectId), await tokenFor(news.certProjectId), news, bucketDomainState(olds),
    );
    return { outs: { ...news, ...state } };
  },
  async read(id, props: any) {
    const domains = await getBucketDomains(curl, await tokenFor(props.projectId), props.pool, props.bucket);
    if (!domains?.includes(props.domain)) return { id: "", props: {} };
    const le = await findLeCertificate(curl, await tokenFor(props.certProjectId), props.certName);
    return {
      id,
      props: {
        ...props,
        leCertificateId: le?.id ?? "",
        certificateStatus: le?.status.toUpperCase() ?? "",
        issuedVersion: String(le?.version ?? ""),
        expireAt: le?.expire_at ?? "",
      },
    };
  },
  async delete(_id, props: any) {
    const token = await tokenFor(props.projectId);
    await unbindBucketDomain(curl, token, props.pool, props.bucket, props.domain);
    if (props.s3CertificateName) await deleteBucketCertificate(curl, token, props.pool, props.s3CertificateName);
    if (props.leCertificateId) await deleteLeCertificate(curl, await tokenFor(props.certProjectId), props.leCertificateId);
  },
};

export interface BucketDomainArgs {
  projectId: pulumi.Input<string>;
  // Проект, в котором выпускается Let's Encrypt
  certProjectId: pulumi.Input<string>;
  pool: pulumi.Input<string>;
  bucket: pulumi.Input<string>;
  domain: pulumi.Input<string>;
  certName: pulumi.Input<string>;
}

// Свой домен публичного бакета и сертификат Let's Encrypt к нему. Домен — уже CNAME на
// access.<пул>.storage.selcloud.ru. Сертификат выпускается не мгновенно: пока certificateStatus не
// ACTIVE, uploadedVersion пуст, и следующий up загружает сертификат в хранилище. В стейте — только
// id, версии и статус; ключ читается и сразу уходит в хранилище.
export class BucketDomain extends pulumi.dynamic.Resource {
  declare public readonly certificateStatus: pulumi.Output<string>;
  declare public readonly uploadedVersion: pulumi.Output<string>;
  declare public readonly expireAt: pulumi.Output<string>;
  constructor(name: string, args: BucketDomainArgs, opts?: pulumi.CustomResourceOptions) {
    super(bucketDomainProvider, name, {
      ...args, leCertificateId: undefined, certificateStatus: undefined, issuedVersion: undefined,
      uploadedVersion: undefined, s3CertificateName: undefined, expireAt: undefined,
    }, opts, "selectel-storage", "BucketDomain");
  }
}
