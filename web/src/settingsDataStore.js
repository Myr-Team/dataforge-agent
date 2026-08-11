const DEFAULT_FRESH_MS = 30_000;
const DEFAULT_STALE_USABLE_MS = 300_000;
const entries = new Map();
const SENSITIVE_QUERY_KEY = /(?:actor|identity|email|user|principal|subject|token|headers?|authorization|cookie|secret|credential|api[_-]?key|password)/i;


function opaqueScope(scopeKey) {
  const source = String(scopeKey || "");
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `s${(hash >>> 0).toString(36)}`;
}


function stableQuery(value, key = "") {
  if (value === null || value === undefined || value === "") return undefined;
  if (SENSITIVE_QUERY_KEY.test(String(key))) return undefined;
  if (Array.isArray(value)) return value.map((item) => stableQuery(item)).filter((item) => item !== undefined);
  if (typeof value === "object") {
    return Object.fromEntries(Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([childKey, item]) => [childKey, stableQuery(item, childKey)])
      .filter(([, item]) => item !== undefined));
  }
  return value;
}


function policy(options = {}) {
  return {
    freshMs: Number.isFinite(Number(options.freshMs)) ? Number(options.freshMs) : DEFAULT_FRESH_MS,
    staleUsableMs: Number.isFinite(Number(options.staleUsableMs)) ? Number(options.staleUsableMs) : DEFAULT_STALE_USABLE_MS,
  };
}


function publicError(error, occurredAt) {
  const status = Number(error?.status);
  return {
    code: "refresh_failed",
    status: Number.isInteger(status) && status >= 400 && status <= 599 ? status : null,
    message: "Settings refresh failed",
    occurredAt,
  };
}


function publicEntry(entry, status, value) {
  return {
    status,
    value,
    storedAt: entry.storedAt,
    inFlight: Boolean(entry.inFlight),
    lastError: entry.lastError,
  };
}


export function settingsDataKey(scopeKey, resource, query = {}) {
  if (!scopeKey) throw new Error("Settings authorization scope is required");
  if (!resource) throw new Error("Settings resource is required");
  return JSON.stringify({
    scope: opaqueScope(scopeKey),
    resource: String(resource),
    query: stableQuery(query) || {},
  });
}


export function peekSettingsResource(key, now = Date.now(), options = {}) {
  const entry = entries.get(key);
  if (!entry) return { status: "missing", value: null, storedAt: 0, inFlight: false, lastError: null };
  if (entry.value === null || entry.value === undefined) return publicEntry(entry, "missing", null);
  const { freshMs, staleUsableMs } = policy(options);
  const age = Math.max(0, Number(now) - entry.storedAt);
  if (age <= freshMs) return publicEntry(entry, "fresh", entry.value);
  if (age <= staleUsableMs) return publicEntry(entry, "stale_usable", entry.value);
  return publicEntry(entry, "expired", null);
}


function startLoad(key, entry, loader, now) {
  const controller = new AbortController();
  entry.abortController = controller;
  entries.set(key, entry);
  let result;
  try {
    result = loader({ signal: controller.signal });
  } catch (error) {
    result = Promise.reject(error);
  }
  const inFlight = Promise.resolve(result)
    .then((value) => {
      if (entries.get(key) === entry && !controller.signal.aborted) {
        entry.value = value;
        entry.storedAt = Number(now);
        entry.lastError = null;
      }
      return value;
    })
    .catch((error) => {
      if (entries.get(key) === entry && !controller.signal.aborted) entry.lastError = publicError(error, Number(now));
      throw error;
    })
    .finally(() => {
      if (entries.get(key) === entry && entry.inFlight === inFlight) {
        entry.inFlight = null;
        entry.abortController = null;
      }
    });
  entry.inFlight = inFlight;
  return inFlight;
}


export function loadSettingsResource(key, loader, options = {}) {
  if (!key) return Promise.reject(new Error("Settings resource key is required"));
  if (typeof loader !== "function") return Promise.reject(new Error("Settings resource loader is required"));
  const now = options.now ?? Date.now();
  const current = entries.get(key) || {
    scope: JSON.parse(key)?.scope || "",
    value: null,
    storedAt: 0,
    inFlight: null,
    abortController: null,
    lastError: null,
  };
  if (current.inFlight) return current.inFlight;
  if (!options.force && peekSettingsResource(key, now, options).status === "fresh") return Promise.resolve(current.value);
  return startLoad(key, current, loader, now);
}


export function invalidateSettingsResource(key) {
  const entry = entries.get(key);
  if (!entry) return 0;
  entry.abortController?.abort();
  entries.delete(key);
  return 1;
}


export function clearSettingsScope(scopeKey) {
  const scope = opaqueScope(scopeKey);
  let removed = 0;
  for (const [key, entry] of entries) {
    if (entry.scope !== scope) continue;
    entry.abortController?.abort();
    entries.delete(key);
    removed += 1;
  }
  return removed;
}


export function clearSettingsData() {
  for (const key of [...entries.keys()]) invalidateSettingsResource(key);
}
