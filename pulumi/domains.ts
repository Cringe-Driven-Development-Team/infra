import * as pulumi from "@pulumi/pulumi";
import * as selectel from "@pulumi/selectel";
import { Auxiliary } from "./auxiliary";
import { InfraConfig } from "./config";
import { Foundation } from "./foundation";
import { Gateway } from "./gateway";
import { BucketDomain, CdnDomain, CdnResource } from "./selectel-storage";
import { Storage } from "./storage";

const withDot = (d: string) => (d.endsWith(".") ? d : `${d}.`);

export function createDomains(
  c: InfraConfig, f: Foundation, gateway: Gateway, storage: Storage, auxiliary: Auxiliary | undefined,
): void {
  const dns = c.dns;
  const projectId = f.project.id;
  // DNS: зона домена (infra:dnsZone) лежит в проекте infra:dnsProjectId, по умолчанию — в проекте стека.
  const dnsProjectId: pulumi.Input<string> = dns.projectId ?? projectId;
  // Свой провайдер selectel для DNS: его projectId — проект, где провайдер ищет импортируемые записи и
  // зоны (у провайдера по умолчанию он берётся из INFRA_PROJECT_ID, иначе import падает с
  // «INFRA_PROJECT_ID must be set»). Явный провайдер не читает selectel:* из конфига стека — передаём;
  // Логин и пароль — из OS_* (selectel.env), как у провайдера по умолчанию, но явно: SDK, собранный
  // свежим pulumi install, делает их обязательными. Пароль в стейте — секретом.
  const dnsProvider = new selectel.Provider("dns", {
    projectId: dnsProjectId,
    domainName: c.domainName,
    authUrl: c.authUrl,
    authRegion: c.authRegion,
    username: c.selectelCreds.username,
    password: pulumi.secret(c.selectelCreds.password),
  });
  const withDns = { provider: dnsProvider };
  const parentZone = dns.zone
    ? selectel.getDomainsZoneV2Output({ name: dns.zone, projectId: dnsProjectId }, withDns)  // cellestial.ru.
    : undefined;

  // Запись в зоне infra:dnsZone. Смена projectId/zoneId — замена записи с тем же именем: сначала
  // удалить, иначе 409.
  const record = (name: string, args: { name: string; type: string; records: { content: pulumi.Input<string> }[] }) =>
    new selectel.DomainsRrsetV2(name, {
      zoneId: parentZone!.id,
      projectId: dnsProjectId,
      ttl: 300,
      ...args,
    }, { ...withDns, deleteBeforeReplace: true });

  // A-запись домена → publicIp VPS
  if (dns.appDomain && parentZone) {
    record("app", { name: withDot(dns.appDomain), type: "A", records: [{ content: gateway.floatingIp.address }] });
  }

  // Сервис вебхуков — A-запись в infra:dnsZone; сертификат выпускает Caddy на VPS.
  if (dns.hooksDomain && auxiliary) {
    record("hooks", { name: withDot(dns.hooksDomain), type: "A", records: [{ content: auxiliary.floatingIp.address }] });
  }

  // Свой домен CDN-ресурса (infra:cdnDomain) и CDN статики (infra:staticDomain). Запись внутри зоны, не
  // зона-поддомен: домен, который не CNAME на cdnDomain, CDN API не сохраняет, а на вершине зоны CNAME
  // невозможен.
  const cdnDomains: [string, string | undefined, CdnResource | undefined][] = [
    ["cdn", dns.cdnDomain, storage.cdn],
    ["static", dns.staticDomain, storage.staticCdn],
  ];
  for (const [name, domain, cdn] of cdnDomains) {
    if (domain && cdn) {
      const cname = record(name, { name: withDot(domain), type: "CNAME", records: [{ content: cdn.cdnDomain.apply(withDot) }] });
      new CdnDomain(name, {
        projectId,
        resourceId: cdn.id,
        domain,
      }, { dependsOn: [cname] });
    }
  }

  // Сайт документации на GitHub Pages: CNAME своего домена на адрес Pages организации.
  const githubOrg = "cringe-driven-development-team";
  if (dns.docsDomain) {
    record("docs", { name: withDot(dns.docsDomain), type: "CNAME", records: [{ content: `${githubOrg}.github.io.` }] });
  }

  // Подтверждение домена зоны для организации на GitHub: пока домен подтверждён, чужой репозиторий не
  // займёт его поддомен на Pages, даже если сайт docs отключат. Имя записи GitHub задаёт сам.
  if (dns.githubPagesChallenge) {
    record("github-pages-challenge", {
      name: `_github-pages-challenge-${githubOrg}.${withDot(dns.zone!)}`,
      type: "TXT",
      // Содержимое TXT Selectel принимает только в кавычках
      records: [{ content: `"${dns.githubPagesChallenge}"` }],
    });
  }

  // Свой домен бакета аватарок — отдельная зона DNS (не запись в infra:dnsZone). Зона — в проекте
  // родительской зоны: в проекте стека Selectel отвечает root_zone_already_belongs_to_another_user.
  // NS-делегирование из родительской зоны Selectel ставит сам, своей NS-записи не нужно.
  // На вершине зоны CNAME невозможен, поэтому ALIAS — на актуальный публичный домен бакета
  // <uuid>.selstorage.ru (avatarsPublicDomain): он меняется вместе с бакетом, запись идёт за ним.
  if (dns.avatarsDomain) {
    // Имя зоны уникально в аккаунте: при замене (смена проекта) сначала удалить старую.
    const avatarsZone = new selectel.DomainsZoneV2("avatars", {
      name: withDot(dns.avatarsDomain),
      projectId: dnsProjectId,
    }, { ...withDns, deleteBeforeReplace: true });
    const alias = new selectel.DomainsRrsetV2("avatars-alias", {
      zoneId: avatarsZone.id,
      projectId: dnsProjectId,
      name: withDot(dns.avatarsDomain),
      type: "ALIAS",
      ttl: 300,
      records: [{ content: storage.avatarsAccess.publicDomain.apply(withDot) }],
    }, { ...withDns, deleteBeforeReplace: true });
    // Привязка через API проверяет CNAME и ALIAS не принимает (domain_cname_invalid): уже привязанный
    // домен BucketDomain не трогает, а заново привязать домен-зону можно только в панели.
    new BucketDomain("avatars", {
      projectId,
      pool: c.s3.pool,
      bucket: storage.avatarsBucket.bucket,
      domain: dns.avatarsDomain,
    }, { dependsOn: [alias, storage.avatarsAccess] });
  }
}
