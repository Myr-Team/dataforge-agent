const FRESH_MS = 300_000;
const STALE_USABLE_MS = 1_800_000;
const entries = new Map();
const WORKSPACE_ROLES = new Set(["owner", "admin", "editor", "viewer"]);
const FINOPS_CAPABILITIES = new Set([
  "finops.summary.read",
  "finops.cost.read",
  "finops.roi.read",
  "finops.request_detail.read",
  "finops.trace.read",
  "finops.action.draft",
]);
const WORKSPACE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/;

const SAFE_ACTOR_REF_KEYS = new Set(["actorref", "actor_ref"]);
const SENSITIVE_KEY = /(?:actor|identity|email|user|principal|subject|\bupn\b|token|headers?|authorization|cookie|prompt|secret|credential|key$|api[_-]?key|provider[_-]?response|response[_-]?id)/i;


function stableValue(value, key = "") {
  if (value === null || value === undefined || value === "") return undefined;
  const normalizedKey = String(key).toLowerCase();
  if (SENSITIVE_KEY.test(normalizedKey) && !SAFE_ACTOR_REF_KEYS.has(normalizedKey)) {
    return undefined;
  }
  if (SAFE_ACTOR_REF_KEYS.has(normalizedKey) && String(value).includes("@")) {
    return undefined;
  }
  if (Array.isArray(value)) {
    const items = value
      .map((item) => stableValue(item))
      .filter((item) => item !== undefined);
    if (items.every((item) => ["string", "number", "boolean"].includes(typeof item))) {
      return items.sort((left, right) => String(left).localeCompare(String(right)));
    }
    return items;
  }
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([childKey, item]) => [childKey, stableValue(item, childKey)])
        .filter(([, item]) => item !== undefined),
    );
  }
  return value;
}


function permissionEntries(permissionSummary) {
  if (!permissionSummary) return { capabilities: [], workspaceRoles: [] };
  const capabilityCandidates = [];
  let roleCandidates = [];
  if (Array.isArray(permissionSummary)) {
    for (const item of permissionSummary) {
      if (typeof item === "string") {
        capabilityCandidates.push(item);
      } else if (item && typeof item === "object" && !Array.isArray(item)) {
        roleCandidates.push([
          item.workspaceId ?? item.workspace_id ?? "",
          item.role ?? "",
        ]);
      }
    }
  } else if (typeof permissionSummary === "string") {
    capabilityCandidates.push(permissionSummary);
  } else if (typeof permissionSummary === "object") {
    if ("workspaceId" in permissionSummary || "workspace_id" in permissionSummary) {
      roleCandidates = [[
        permissionSummary.workspaceId ?? permissionSummary.workspace_id ?? "",
        permissionSummary.role ?? "",
      ]];
    } else {
      roleCandidates = Object.entries(permissionSummary);
    }
  }
  const capabilities = capabilityCandidates
    .map((value) => String(value).trim().toLowerCase())
    .filter((value) => FINOPS_CAPABILITIES.has(value));
  const workspaceRoles = roleCandidates
    .map(([workspaceValue, roleValue]) => {
      if (typeof workspaceValue !== "string" || typeof roleValue !== "string") return null;
      const workspaceId = workspaceValue.trim();
      const role = roleValue.trim().toLowerCase();
      if (!WORKSPACE_ID_PATTERN.test(workspaceId)) return null;
      if (!WORKSPACE_ROLES.has(role)) return null;
      return `${workspaceId}:${role}`;
    })
    .filter(Boolean);
  return {
    capabilities: [...new Set(capabilities)].sort(),
    workspaceRoles: [...new Set(workspaceRoles)].sort(),
  };
}


function publicEntry(entry, status, value) {
  return {
    status,
    value,
    domain: entry.domain,
    storedAt: entry.storedAt,
    inFlight: Boolean(entry.inFlight),
    lastError: entry.lastError,
  };
}


function publicRefreshError(error, occurredAt) {
  const numericStatus = Number(error?.status);
  return {
    code: "refresh_failed",
    status: Number.isInteger(numericStatus) && numericStatus >= 400 && numericStatus <= 599
      ? numericStatus
      : null,
    message: "FinOps data refresh failed",
    occurredAt,
  };
}


function abortAndDelete(key, entry) {
  entry.abortController?.abort();
  entries.delete(key);
}


export function finopsDataKey({
  tenantScope,
  permissionSummary,
  workspaceId,
  domain,
  from = "",
  to = "",
  window = null,
  filters = {},
  schemaRevision = "finops-decision-v1",
} = {}) {
  const normalizedWindow = window && typeof window === "object" && !Array.isArray(window)
    ? stableValue(window)
    : { from: String(from || ""), to: String(to || "") };
  return JSON.stringify({
    tenantScope: String(tenantScope || ""),
    permissionSummary: permissionEntries(permissionSummary),
    workspaceId: String(workspaceId || ""),
    domain: String(domain || ""),
    window: normalizedWindow,
    filters: stableValue(filters) || {},
    schemaRevision: String(schemaRevision || "finops-decision-v1"),
  });
}


export function readFinOpsData(key, now = Date.now()) {
  const entry = entries.get(key);
  if (!entry?.value) return { status: "missing", value: null };
  const age = Math.max(0, Number(now) - entry.storedAt);
  if (age <= FRESH_MS) return publicEntry(entry, "fresh", entry.value);
  if (age <= STALE_USABLE_MS) {
    return publicEntry(entry, "stale_usable", entry.value);
  }
  return publicEntry(entry, "expired", null);
}


export function loadFinOpsData(
  key,
  loader,
  { domain = "", force = false, now = Date.now() } = {},
) {
  if (!key) return Promise.reject(new Error("FinOps data scope is required"));
  if (typeof loader !== "function") {
    return Promise.reject(new Error("FinOps data loader is required"));
  }

  const current = entries.get(key) || {
    domain: String(domain || ""),
    storedAt: 0,
    value: null,
    inFlight: null,
    abortController: null,
    lastError: null,
  };
  if (current.inFlight) return current.inFlight;
  if (!force && readFinOpsData(key, now).status === "fresh") {
    return Promise.resolve(current.value);
  }

  current.domain = String(domain || current.domain || "");
  const abortController = new AbortController();
  current.abortController = abortController;
  entries.set(key, current);

  let loaded;
  try {
    // Keep the existing preload contract: callers can observe the signal and
    // install abort handlers before this function returns.
    loaded = loader({ signal: abortController.signal });
  } catch (error) {
    loaded = Promise.reject(error);
  }
  const inFlight = Promise.resolve(loaded)
    .then((value) => {
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new TypeError("FinOps data response must be a non-array object");
      }
      if (entries.get(key) === current) {
        current.value = value;
        current.storedAt = Number(now);
        current.lastError = null;
      }
      return value;
    })
    .catch((error) => {
      if (entries.get(key) === current) {
        current.lastError = publicRefreshError(error, Number(now));
      }
      throw error;
    })
    .finally(() => {
      if (entries.get(key) === current && current.inFlight === inFlight) {
        current.inFlight = null;
        current.abortController = null;
      }
    });
  current.inFlight = inFlight;
  return inFlight;
}


export function invalidateFinOpsData(predicate) {
  if (typeof predicate !== "function") {
    throw new TypeError("FinOps invalidation predicate is required");
  }
  let removed = 0;
  for (const [key, entry] of entries) {
    if (predicate(publicEntry(entry, readFinOpsData(key).status, entry.value), key)) {
      abortAndDelete(key, entry);
      removed += 1;
    }
  }
  return removed;
}


export function clearFinOpsData(key = null) {
  if (key !== null && key !== undefined) {
    const entry = entries.get(key);
    if (entry) abortAndDelete(key, entry);
    return;
  }
  for (const [entryKey, entry] of entries) {
    abortAndDelete(entryKey, entry);
  }
}
