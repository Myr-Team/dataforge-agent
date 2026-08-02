import {
  clearFinOpsData,
  finopsDataKey,
  invalidateFinOpsData,
  loadFinOpsData,
  readFinOpsData,
} from "./finopsDataStore.js";


export function finopsScopeKey(scope = {}) {
  return finopsDataKey({
    tenantScope: scope.tenantScope || scope.tenantKey,
    permissionSummary: scope.permissionSummary || scope.workspaceRoles || scope.permissions,
    workspaceId: scope.workspaceId,
    domain: "overview",
    window: scope.window,
    filters: scope.filters,
    schemaRevision: scope.schemaRevision || "finops-bootstrap-v1",
  });
}


export function readFinOpsBootstrap(key, now = Date.now()) {
  const current = readFinOpsData(key, now);
  return {
    status: current.status === "stale_usable" ? "stale" : current.status,
    value: current.value,
  };
}


export function prefetchFinOpsBootstrap(
  key,
  loader,
  { now = Date.now(), force = false } = {},
) {
  return loadFinOpsData(key, loader, { domain: "overview", force, now });
}


export function clearFinOpsBootstrap(key = null) {
  if (key !== null && key !== undefined) {
    clearFinOpsData(key);
    return;
  }
  invalidateFinOpsData((entry) => entry.domain === "overview");
}
