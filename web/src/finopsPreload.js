const FRESH_MS = 60_000;
const USABLE_STALE_MS = 300_000;
const entries = new Map();


function sortedObject(value = {}) {
  return Object.fromEntries(
    Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => [key, item]),
  );
}


export function finopsScopeKey(scope = {}) {
  return JSON.stringify({
    tenantKey: String(scope.tenantKey || ""),
    workspaceId: String(scope.workspaceId || ""),
    identityKey: String(scope.identityKey || ""),
    permissions: [...(scope.permissions || [])].map(String).sort(),
    filters: sortedObject(scope.filters || {}),
  });
}


export function readFinOpsBootstrap(key, now = Date.now()) {
  const entry = entries.get(key);
  if (!entry?.value) return { status: "missing", value: null };
  const ageMs = Math.max(0, now - entry.storedAt);
  if (ageMs <= FRESH_MS) return { status: "fresh", value: entry.value };
  if (ageMs <= USABLE_STALE_MS) return { status: "stale", value: entry.value };
  return { status: "expired", value: null };
}


export function prefetchFinOpsBootstrap(
  key,
  loader,
  { now = Date.now(), force = false } = {},
) {
  if (!key) return Promise.reject(new Error("FinOps preload scope is required"));
  if (typeof loader !== "function") {
    return Promise.reject(new Error("FinOps bootstrap loader is required"));
  }
  const current = entries.get(key) || {
    value: null,
    storedAt: 0,
    inFlight: null,
    controller: null,
  };
  if (current.inFlight) return current.inFlight;
  if (!force && readFinOpsBootstrap(key, now).status === "fresh") {
    return Promise.resolve(current.value);
  }

  const controller = new AbortController();
  current.controller = controller;
  entries.set(key, current);
  let loaded;
  try {
    loaded = loader({ signal: controller.signal });
  } catch (error) {
    current.controller = null;
    throw error;
  }
  const inFlight = Promise.resolve(loaded)
    .then((value) => {
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new Error("FinOps bootstrap response must be an object");
      }
      current.value = value;
      current.storedAt = now;
      return value;
    })
    .finally(() => {
      if (current.inFlight === inFlight) {
        current.inFlight = null;
        current.controller = null;
      }
    });
  current.inFlight = inFlight;
  return inFlight;
}


export function clearFinOpsBootstrap(key = null) {
  if (key !== null && key !== undefined) {
    const entry = entries.get(key);
    entry?.controller?.abort();
    entries.delete(key);
    return;
  }
  entries.forEach((entry) => entry.controller?.abort());
  entries.clear();
}

