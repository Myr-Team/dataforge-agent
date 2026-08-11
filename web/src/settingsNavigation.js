import { loadSettingsResource, settingsDataKey } from "./settingsDataStore.js";

const HOME_RESOURCES = Object.freeze(["budget", "notification", "alerts", "workspaceSettings", "routing"]);
const WORKSPACE_ROLES = new Set(["owner", "admin", "editor", "viewer"]);


function permissionFingerprint(capabilities) {
  const sections = capabilities?.sections;
  if (!sections || typeof sections !== "object") return [];
  return Object.entries(sections).flatMap(([section, config]) => Object.entries(config?.permissions || {})
    .filter(([, allowed]) => allowed === true)
    .map(([permission]) => `${section}:${permission}`))
    .sort();
}


export function settingsAuthorizationOwnerBoundary({ authState, workspaceId, user } = {}) {
  const expectedWorkspace = String(workspaceId || "").trim();
  const tenantRef = String(user?.tenantRef || user?.tenant_ref || "").trim();
  const actorRef = String(user?.actorRef || user?.actor_ref || "").trim();
  const sessionRef = String(user?.sessionRef || user?.session_ref || "").trim();
  if (
    !["authenticated", "local"].includes(String(authState || ""))
    || !expectedWorkspace
    || !tenantRef
    || !actorRef
    || !sessionRef
  ) return "";
  return JSON.stringify({ tenantRef, actorRef, sessionRef, workspaceId: expectedWorkspace });
}


export function settingsAuthorizationBoundary({ authState, workspaceId, user, capabilities, workspaceAccess, permissionsResolved = false } = {}) {
  const expectedWorkspace = String(workspaceId || "").trim();
  // These references are issued by /api/auth/session. Do not derive scope from
  // email, object IDs, or any browser-supplied identity field.
  const tenantRef = String(user?.tenantRef || user?.tenant_ref || "").trim();
  const actorRef = String(user?.actorRef || user?.actor_ref || "").trim();
  const sessionRef = String(user?.sessionRef || user?.session_ref || "").trim();
  const role = String(workspaceAccess?.role || "").trim().toLowerCase();
  if (
    !expectedWorkspace
    || !tenantRef
    || !actorRef
    || !sessionRef
    || permissionsResolved !== true
    || (workspaceAccess?.authenticated !== true && authState !== "local")
    || workspaceAccess?.allowed !== true
    || String(workspaceAccess?.workspace_id || "") !== expectedWorkspace
    || !WORKSPACE_ROLES.has(role)
    || String(capabilities?.workspace_id || "") !== expectedWorkspace
    || !capabilities?.sections
    || typeof capabilities.sections !== "object"
  ) return "";
  return JSON.stringify({
    tenantRef,
    actorRef,
    sessionRef,
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


export function reconcileSettingsAuthorizationScope(previousState, nextState, clearScope) {
  const previous = previousState && typeof previousState === "object"
    ? { ownerKey: String(previousState.ownerKey || ""), scopeKey: String(previousState.scopeKey || "") }
    : { ownerKey: "", scopeKey: String(previousState || "") };
  const next = nextState && typeof nextState === "object"
    ? {
        ownerKey: String(nextState.ownerKey || ""),
        scopeKey: String(nextState.scopeKey || ""),
        permissionsResolved: nextState.permissionsResolved === true,
      }
    : { ownerKey: "", scopeKey: String(nextState || ""), permissionsResolved: true };
  const ownerChanged = previous.ownerKey !== next.ownerKey;
  if (!next.ownerKey || (previous.ownerKey && ownerChanged)) {
    if (previous.scopeKey) clearScope?.(previous.scopeKey);
    return { ownerKey: next.ownerKey, scopeKey: "" };
  }
  if (!next.permissionsResolved) {
    return { ownerKey: next.ownerKey, scopeKey: ownerChanged ? "" : previous.scopeKey };
  }
  if (previous.scopeKey && previous.scopeKey !== next.scopeKey) clearScope?.(previous.scopeKey);
  return { ownerKey: next.ownerKey, scopeKey: next.scopeKey };
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
