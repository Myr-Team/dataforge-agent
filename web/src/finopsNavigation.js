import { finopsScopeKey } from "./finopsPreload.js";
import {
  finopsDataKey,
  invalidateFinOpsData,
  loadFinOpsData,
  readFinOpsData,
} from "./finopsDataStore.js";


export const FINOPS_REFRESH_MS = 600_000;
const FINOPS_TABS = new Set(["overview", "cost", "roi", "risk"]);
const WORKSPACE_ROLES = new Set(["owner", "admin", "editor", "viewer"]);
const MUTATION_DOMAINS = Object.freeze({
  roi_scenario: ["roi"],
  risk_draft: ["risk"],
  risk_anomaly: ["risk"],
  saved_cost_view: ["cost"],
  price_setting: ["cost", "roi", "risk", "overview"],
  model_setting: ["cost", "roi", "risk", "overview"],
  cache_setting: ["cost", "roi", "risk", "overview"],
});


function normalizedWorkspaceAccess(workspaceId, workspaceAccess) {
  const accessWorkspaceId = String(workspaceAccess?.workspace_id || "");
  const role = String(workspaceAccess?.role || "").trim().toLowerCase();
  if (
    workspaceAccess?.authenticated !== true
    || workspaceAccess?.allowed !== true
    || accessWorkspaceId !== String(workspaceId || "")
    || !WORKSPACE_ROLES.has(role)
  ) {
    return null;
  }
  return { workspaceId: accessWorkspaceId, role };
}


function permissionSummary(scope = {}) {
  return [
    ...(Array.isArray(scope.permissions) ? scope.permissions : []),
    ...(Array.isArray(scope.authorizedWorkspaceScope) ? scope.authorizedWorkspaceScope : []),
  ];
}


function normalizedTab(tab) {
  const value = String(tab || "").trim().toLowerCase();
  if (!FINOPS_TABS.has(value)) throw new Error("Unknown FinOps tab");
  return value;
}


function cacheKeyWorkspace(key) {
  try {
    const value = JSON.parse(key);
    return String(value?.workspaceId || "");
  } catch {
    return "";
  }
}


export function finopsIntentHandlers(item, onIntent) {
  if (!["finops", "finops-risk"].includes(item?.id) || typeof onIntent !== "function") return {};
  return {
    onMouseEnter: onIntent,
    onFocus: onIntent,
    onTouchStart: onIntent,
  };
}


export function finopsTabIntentHandlers(tab, onIntent) {
  if (!FINOPS_TABS.has(String(tab || "")) || typeof onIntent !== "function") return {};
  const intent = () => onIntent(tab);
  return {
    onPointerEnter: intent,
    onFocus: intent,
    onTouchStart: intent,
  };
}


export function finopsPreloadScope({
  authState,
  workspaceId,
  user,
  capabilities,
  workspaceAccess,
} = {}) {
  const finops = capabilities?.sections?.finops;
  const permissionMap = finops?.permissions;
  const tenantScope = String(user?.tenantScope || user?.tenant_scope || "").trim();
  const authorizedWorkspace = normalizedWorkspaceAccess(workspaceId, workspaceAccess);
  if (
    finops?.visible !== true
    || permissionMap?.["finops.summary.read"] !== true
    || !workspaceId
    || !tenantScope
    || capabilities?.workspace_id !== workspaceId
    || !authorizedWorkspace
  ) {
    return null;
  }
  const permissions = Object.entries(permissionMap)
    .filter(([, allowed]) => allowed === true)
    .map(([permission]) => permission)
    .sort();
  const scope = {
    tenantScope,
    workspaceId: String(workspaceId),
    permissions,
    authorizedWorkspaceScope: [authorizedWorkspace],
    filters: { window: "30d" },
  };
  return {
    ...scope,
    key: finopsScopeKey({
      ...scope,
      permissionSummary: permissionSummary(scope),
    }),
  };
}


export function finopsAuthorizationScopeKey(scope = {}) {
  const permissions = Array.isArray(scope.permissions)
    ? [...new Set(scope.permissions.map((value) => String(value)))].sort()
    : [];
  const workspaces = Array.isArray(scope.authorizedWorkspaceScope)
    ? scope.authorizedWorkspaceScope
      .map((item) => ({
        workspaceId: String(item?.workspaceId || item?.workspace_id || ""),
        role: String(item?.role || "").toLowerCase(),
      }))
      .filter((item) => item.workspaceId && WORKSPACE_ROLES.has(item.role))
      .sort((left, right) => `${left.workspaceId}:${left.role}`.localeCompare(`${right.workspaceId}:${right.role}`))
    : [];
  return JSON.stringify({
    tenantScope: String(scope.tenantScope || ""),
    permissions,
    workspaces,
  });
}


export function finopsAuthorizationBoundary({
  workspaceId,
  user,
  capabilities,
  workspaceAccess,
} = {}) {
  const tenantScope = String(user?.tenantScope || user?.tenant_scope || "").trim();
  const expectedWorkspace = String(workspaceId || "");
  if (
    !tenantScope
    || !expectedWorkspace
    || String(workspaceAccess?.workspace_id || "") !== expectedWorkspace
    || workspaceAccess?.authenticated !== true
  ) {
    return "";
  }
  if (workspaceAccess?.allowed !== true) {
    return JSON.stringify({
      tenantScope,
      permissions: [],
      workspaces: [{ workspaceId: expectedWorkspace, role: "denied" }],
    });
  }
  if (capabilities?.workspace_id !== expectedWorkspace) return "";
  const role = String(workspaceAccess?.role || "").trim().toLowerCase();
  if (!WORKSPACE_ROLES.has(role)) return "";
  const permissionMap = capabilities?.sections?.finops?.permissions;
  if (!permissionMap || typeof permissionMap !== "object") return "";
  const permissions = Object.entries(permissionMap)
    .filter(([, allowed]) => allowed === true)
    .map(([permission]) => permission)
    .sort();
  return JSON.stringify({
    tenantScope,
    permissions,
    workspaces: [{ workspaceId: expectedWorkspace, role }],
  });
}


export function reconcileFinOpsAuthorizationScope(previousKey, nextKey, clear) {
  const previous = String(previousKey || "");
  const next = String(nextKey || "");
  if (!next) return previous;
  if (previous && previous !== next) clear?.();
  return next;
}


export function finopsTabDataKey(tab, {
  scope = {},
  query = {},
  defaultScope = false,
} = {}) {
  const domain = normalizedTab(tab);
  if (domain === "overview" && defaultScope && scope.key) return scope.key;
  return finopsDataKey({
    tenantScope: scope.tenantScope,
    permissionSummary: permissionSummary(scope),
    workspaceId: query.workspaceId || scope.workspaceId,
    domain,
    from: query.from,
    to: query.to,
    filters: {
      departmentId: query.departmentId,
      agentId: query.agentId,
      model: query.model,
    },
    schemaRevision: domain === "overview"
      ? "finops-bootstrap-v1"
      : domain === "cost"
        ? "finops-cost-v1"
        : "finops-decision-v1",
  });
}


export function loadFinOpsTab({
  tab,
  key,
  loader,
  force = false,
  now = Date.now(),
} = {}) {
  const domain = normalizedTab(tab);
  const cache = readFinOpsData(key, now);
  const requested = Boolean(force) || cache.status !== "fresh";
  const ownsRequest = requested && !cache.inFlight;
  const promise = requested
    ? loadFinOpsData(
      key,
      ({ signal }) => loader({ signal, refresh: Boolean(force) }),
      { domain, force: Boolean(force), now },
    )
    : Promise.resolve(cache.value);
  return { cache, requested, ownsRequest, promise };
}


export function prefetchFinOpsTab(tab, {
  keys = {},
  loaders = {},
  force = false,
  now = Date.now(),
} = {}) {
  const domain = normalizedTab(tab);
  if (!keys[domain] || typeof loaders[domain] !== "function") {
    return Promise.reject(new Error("FinOps tab prefetch scope is unavailable"));
  }
  return loadFinOpsTab({
    tab: domain,
    key: keys[domain],
    loader: loaders[domain],
    force,
    now,
  }).promise;
}


export function shouldRefreshFinOpsTab({
  hidden = false,
  lastSuccessfulAt = 0,
  now = Date.now(),
} = {}) {
  if (hidden) return false;
  return Number(now) - Number(lastSuccessfulAt || 0) >= FINOPS_REFRESH_MS;
}


export function createFinOpsRequestGuard() {
  let generation = 0;
  let activeKey = "";
  const isActive = (request) => Boolean(
    request
    && request.generation === generation
    && request.key === activeKey
    && activeKey,
  );
  return {
    begin(key) {
      generation += 1;
      activeKey = String(key || "");
      return Object.freeze({ generation, key: activeKey });
    },
    isActive,
    deactivate(request = null) {
      if (request && !isActive(request)) return false;
      generation += 1;
      activeKey = "";
      return true;
    },
  };
}


export function settleFinOpsLoadFailure(
  state,
  error,
  { fallbackMessage = "页面数据读取失败" } = {},
) {
  const data = state?.data;
  const hasUsableData = Boolean(
    data
    && typeof data === "object"
    && (!Array.isArray(data) || data.length)
    && (Array.isArray(data) || Object.keys(data).length),
  );
  const message = error?.name === "AbortError"
    ? (hasUsableData ? "" : "数据更新已中止，请重试。")
    : (error instanceof Error ? error.message : fallbackMessage);
  return {
    ...state,
    loading: false,
    updating: false,
    error: message,
    data,
  };
}


export function createFinOpsRefreshTracker() {
  const successful = new Map();
  const consumedForces = new Map();
  const key = (scopeKey, tab) => `${String(scopeKey || "")}\u0000${normalizedTab(tab)}`;
  return {
    markSuccessful(scopeKey, tab, at = Date.now()) {
      successful.set(key(scopeKey, tab), Number(at));
    },
    lastSuccessfulAt(scopeKey, tab) {
      return successful.get(key(scopeKey, tab)) || 0;
    },
    isDue(scopeKey, tab, { now = Date.now(), hidden = false } = {}) {
      return shouldRefreshFinOpsTab({
        hidden,
        now,
        lastSuccessfulAt: successful.get(key(scopeKey, tab)) || 0,
      });
    },
    consumeForce(scopeKey, tab, resource, refresh = {}) {
      const refreshScope = String(refresh.scopeKey || "");
      const version = Number(refresh.version || 0);
      if (
        refresh.force !== true
        || !refreshScope
        || refreshScope !== String(scopeKey || "")
        || version < 1
      ) {
        return false;
      }
      const resourceKey = `${key(scopeKey, tab)}\u0000${String(resource || "main")}`;
      if ((consumedForces.get(resourceKey) || 0) >= version) return false;
      consumedForces.set(resourceKey, version);
      return true;
    },
    reset() {
      successful.clear();
      consumedForces.clear();
    },
  };
}


export function invalidateFinOpsMutation(kind, {
  workspaceId,
  invalidate = invalidateFinOpsData,
} = {}) {
  const domains = MUTATION_DOMAINS[String(kind || "")] || [];
  if (!workspaceId || !domains.length) return 0;
  const allowed = new Set(domains);
  return invalidate((entry, key) => (
    allowed.has(String(entry?.domain || "").split(":", 1)[0])
    && cacheKeyWorkspace(key) === String(workspaceId)
  ));
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


export function scheduleFinOpsTabPreload(tab, {
  keys = {},
  loaders = {},
  host = globalThis,
  onError = () => {},
} = {}) {
  const domain = normalizedTab(tab);
  let started = false;
  const cancelSchedule = scheduleFinOpsPreload(() => {
    started = true;
    prefetchFinOpsTab(domain, { keys, loaders }).catch((error) => {
      if (error?.name !== "AbortError") onError(error);
    });
  }, host);
  return () => {
    if (!started) cancelSchedule();
  };
}
