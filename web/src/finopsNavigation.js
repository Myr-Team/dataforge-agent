import { finopsScopeKey } from "./finopsPreload.js";


export function finopsIntentHandlers(item, onIntent) {
  if (item?.id !== "finops" || typeof onIntent !== "function") return {};
  return {
    onMouseEnter: onIntent,
    onFocus: onIntent,
    onTouchStart: onIntent,
  };
}


export function finopsPreloadScope({
  authState,
  workspaceId,
  user,
  capabilities,
} = {}) {
  const finops = capabilities?.sections?.finops;
  const permissionMap = finops?.permissions;
  if (
    finops?.visible !== true
    || permissionMap?.["finops.summary.read"] !== true
    || !workspaceId
  ) {
    return null;
  }
  const permissions = Object.entries(permissionMap)
    .filter(([, allowed]) => allowed === true)
    .map(([permission]) => permission)
    .sort();
  const scope = {
    tenantKey: String(authState || "unknown"),
    workspaceId: String(workspaceId),
    identityKey: String(user?.email || "").trim().toLowerCase(),
    permissions,
    filters: { window: "30d" },
  };
  return {
    ...scope,
    key: finopsScopeKey(scope),
  };
}


export function scheduleFinOpsPreload(callback, host = globalThis) {
  if (typeof host?.requestIdleCallback === "function") {
    const handle = host.requestIdleCallback(callback, { timeout: 1_000 });
    return () => host.cancelIdleCallback?.(handle);
  }
  const schedule = host?.setTimeout?.bind(host) || setTimeout;
  const cancel = host?.clearTimeout?.bind(host) || clearTimeout;
  const handle = schedule(callback, 250);
  return () => cancel(handle);
}
