import { loadSettingsResource, settingsDataKey } from "./settingsDataStore.js";

const HOME_RESOURCES = Object.freeze(["budget", "notification", "alerts", "workspaceSettings", "routing"]);
const WORKSPACE_ROLES = new Set(["owner", "admin", "editor", "viewer"]);


function opaqueRef(value, prefix) {
  const source = String(value || "");
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${prefix}-${(hash >>> 0).toString(36)}`;
}


function permissionFingerprint(capabilities) {
  const sections = capabilities?.sections;
  if (!sections || typeof sections !== "object") return [];
  return Object.entries(sections).flatMap(([section, config]) => Object.entries(config?.permissions || {})
    .filter(([, allowed]) => allowed === true)
    .map(([permission]) => `${section}:${permission}`))
    .sort();
}


export function settingsAuthorizationBoundary({ authState, workspaceId, user, capabilities, workspaceAccess } = {}) {
  const expectedWorkspace = String(workspaceId || "").trim();
  const tenant = String(user?.tenantScope || user?.tenant_scope || "").trim();
  const actor = String(user?.actorRef || user?.actor_ref || user?.actor_id || user?.id || user?.email || "").trim();
  const role = String(workspaceAccess?.role || "").trim().toLowerCase();
  if (
    !expectedWorkspace
    || !tenant
    || !actor
    || (workspaceAccess?.authenticated !== true && authState !== "local")
    || workspaceAccess?.allowed !== true
    || String(workspaceAccess?.workspace_id || "") !== expectedWorkspace
    || !WORKSPACE_ROLES.has(role)
    || (capabilities?.workspace_id && String(capabilities.workspace_id) !== expectedWorkspace)
  ) return "";
  return JSON.stringify({
    tenant: opaqueRef(tenant, "tenant"),
    actor: opaqueRef(actor, "actor"),
    workspaceId: expectedWorkspace,
    role,
    permissions: permissionFingerprint(capabilities),
  });
}


export function settingsPreloadScope(args = {}) {
  const key = settingsAuthorizationBoundary(args);
  if (!key) return null;
  return { key, workspaceId: String(args.workspaceId) };
}


export function reconcileSettingsAuthorizationScope(previousKey, nextKey, clearScope) {
  const previous = String(previousKey || "");
  const next = String(nextKey || "");
  if (previous && previous !== next) clearScope?.(previous);
  return next;
}


export function settingsResourceKeys(scope) {
  if (!scope?.key || !scope?.workspaceId) return {};
  return Object.fromEntries(HOME_RESOURCES.map((resource) => [
    resource,
    settingsResourceKey(scope, resource),
  ]));
}


export function settingsResourceKey(scope, resource, query = {}) {
  if (!scope?.key || !scope?.workspaceId || !resource) return "";
  return settingsDataKey(scope.key, resource, {
    workspaceId: scope.workspaceId,
    schemaRevision: "settings-v1",
    ...query,
  });
}


export function prefetchSettingsHome(scope, loaders = {}, { now = Date.now() } = {}) {
  const keys = settingsResourceKeys(scope);
  if (Object.keys(keys).length !== HOME_RESOURCES.length) return Promise.reject(new Error("Settings preload scope is unavailable"));
  return Promise.all(HOME_RESOURCES.map((resource) => {
    const loader = loaders[resource];
    if (typeof loader !== "function") return Promise.reject(new Error(`Settings ${resource} loader is required`));
    return loadSettingsResource(keys[resource], ({ signal }) => loader({ signal }), { now });
  }));
}


export function settingsIntentHandlers(item, onIntent) {
  if (item?.id !== "settings" || typeof onIntent !== "function") return {};
  return { onMouseEnter: onIntent, onFocus: onIntent, onTouchStart: onIntent };
}


export function scheduleSettingsPreload(callback, host = globalThis) {
  if (typeof host?.requestIdleCallback === "function") {
    const handle = host.requestIdleCallback(callback, { timeout: 1_000 });
    return () => host.cancelIdleCallback?.(handle);
  }
  const schedule = host?.setTimeout?.bind(host) || setTimeout;
  const cancel = host?.clearTimeout?.bind(host) || clearTimeout;
  const handle = schedule(callback, 250);
  return () => cancel(handle);
}
