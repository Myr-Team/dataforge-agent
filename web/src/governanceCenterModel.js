const DOMAIN_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;

export function normalizeDomainDraft(value) {
  const values = Array.isArray(value) ? value : String(value || "").split(/[\s,;]+/);
  const seen = new Set();
  const domains = [];
  for (const item of values) {
    const domain = String(item || "").trim().toLowerCase().replace(/\.+$/, "");
    if (!DOMAIN_PATTERN.test(domain) || seen.has(domain)) continue;
    seen.add(domain);
    domains.push(domain);
  }
  return domains.slice(0, 20);
}

export function domainsToDraft(value) {
  return normalizeDomainDraft(value).join(", ");
}

export function resolveLineageScope(capabilities) {
  return capabilities?.sections?.lineage?.scope === "workspace" ? "workspace" : "self";
}
