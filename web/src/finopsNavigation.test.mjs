import assert from "node:assert/strict";
import test from "node:test";

import {
  FINOPS_REFRESH_MS,
  createFinOpsRequestGuard,
  createFinOpsRefreshTracker,
  finopsAuthorizationBoundary,
  finopsAuthorizationScopeKey,
  finopsIntentHandlers,
  finopsPreloadScope,
  finopsTabDataKey,
  finopsTabIntentHandlers,
  invalidateFinOpsMutation,
  loadFinOpsTab,
  prefetchFinOpsTab,
  reconcileFinOpsAuthorizationScope,
  scheduleFinOpsPreload,
  scheduleFinOpsTabPreload,
  settleFinOpsLoadFailure,
  shouldRefreshFinOpsTab,
} from "./finopsNavigation.js";
import {
  clearFinOpsData,
  finopsDataKey,
  loadFinOpsData,
  readFinOpsData,
} from "./finopsDataStore.js";


test.afterEach(() => {
  clearFinOpsData();
});


test("finops navigation intent handlers preload on hover focus and touch", () => {
  let calls = 0;
  const handlers = finopsIntentHandlers(
    { id: "finops" },
    () => {
      calls += 1;
    },
  );

  handlers.onMouseEnter();
  handlers.onFocus();
  handlers.onTouchStart();

  assert.equal(calls, 3);
  assert.deepEqual(finopsIntentHandlers({ id: "runs" }, () => {}), {});
});


test("finops preload scope is unavailable until summary permission resolves", () => {
  const base = {
    authState: "authenticated",
    workspaceId: "ws-a",
    user: { email: "owner@contoso.com" },
  };

  assert.equal(finopsPreloadScope({ ...base, capabilities: null }), null);
  assert.equal(finopsPreloadScope({
    ...base,
    capabilities: {
      sections: {
        finops: {
          visible: false,
          permissions: { "finops.summary.read": false },
        },
      },
    },
  }), null);
});


test("finops preload scope includes identity workspace permissions and default window", () => {
  const scope = finopsPreloadScope({
    authState: "authenticated",
    workspaceId: "ws-a",
    user: { email: "OWNER@CONTOSO.COM", tenantScope: "tenant-a" },
    workspaceAccess: {
      workspace_id: "ws-a",
      authenticated: true,
      allowed: true,
      role: "owner",
    },
    capabilities: {
      workspace_id: "ws-a",
      sections: {
        finops: {
          visible: true,
          permissions: {
            "finops.summary.read": true,
            "finops.cost.read": true,
            "finops.trace.read": false,
          },
        },
      },
    },
  });

  assert.equal(scope.workspaceId, "ws-a");
  assert.equal(scope.tenantScope, "tenant-a");
  assert.equal(Object.hasOwn(scope, "identityKey"), false);
  assert.deepEqual(scope.permissions, ["finops.cost.read", "finops.summary.read"]);
  assert.deepEqual(scope.authorizedWorkspaceScope, [{ workspaceId: "ws-a", role: "owner" }]);
  assert.deepEqual(scope.filters, { window: "30d" });
  assert.match(scope.key, /ws-a/);
  assert.doesNotMatch(scope.key, /owner@contoso\.com/i);
});


test("finops preload scope waits for matching trusted workspace authorization", () => {
  const base = {
    authState: "authenticated",
    workspaceId: "ws-a",
    user: { tenantScope: "tenant-a" },
    capabilities: {
      workspace_id: "ws-a",
      sections: {
        finops: {
          visible: true,
          permissions: { "finops.summary.read": true },
        },
      },
    },
  };

  assert.equal(finopsPreloadScope(base), null);
  assert.equal(finopsPreloadScope({
    ...base,
    workspaceAccess: {
      workspace_id: "ws-b",
      authenticated: true,
      allowed: true,
      role: "owner",
    },
  }), null);
  assert.equal(finopsPreloadScope({
    ...base,
    workspaceAccess: {
      workspace_id: "ws-a",
      authenticated: true,
      allowed: false,
      role: null,
    },
  }), null);
});


test("authorization scope changes only for tenant permission or authorized workspace changes", () => {
  const first = finopsPreloadScope({
    authState: "authenticated",
    workspaceId: "ws-a",
    user: { email: "owner@contoso.com", tenantScope: "tenant-a" },
    workspaceAccess: { workspace_id: "ws-a", authenticated: true, allowed: true, role: "owner" },
    capabilities: {
      workspace_id: "ws-a",
      sections: { finops: { visible: true, permissions: { "finops.summary.read": true } } },
    },
  });
  const renamed = {
    ...first,
    identityKey: "different-user@contoso.com",
  };
  const tenantChanged = { ...first, tenantScope: "tenant-b" };
  const roleChanged = {
    ...first,
    authorizedWorkspaceScope: [{ workspaceId: "ws-a", role: "admin" }],
  };

  assert.equal(finopsAuthorizationScopeKey(first), finopsAuthorizationScopeKey(renamed));
  assert.notEqual(finopsAuthorizationScopeKey(first), finopsAuthorizationScopeKey(tenantChanged));
  assert.notEqual(finopsAuthorizationScopeKey(first), finopsAuthorizationScopeKey(roleChanged));
  assert.doesNotMatch(finopsAuthorizationScopeKey(first), /owner@contoso\.com/i);
});


test("authorization reconciliation clears once on a stable scope change but not initial unknown or unmount", () => {
  let clears = 0;
  const clear = () => { clears += 1; };
  let current = reconcileFinOpsAuthorizationScope("", "", clear);
  assert.equal(current, "");
  current = reconcileFinOpsAuthorizationScope(current, "scope-a", clear);
  assert.equal(current, "scope-a");
  assert.equal(clears, 0);
  current = reconcileFinOpsAuthorizationScope(current, "", clear);
  assert.equal(current, "scope-a");
  assert.equal(clears, 0);
  current = reconcileFinOpsAuthorizationScope(current, "scope-a", clear);
  assert.equal(clears, 0);
  current = reconcileFinOpsAuthorizationScope(current, "scope-b", clear);
  assert.equal(current, "scope-b");
  assert.equal(clears, 1);
});


test("authorization boundary distinguishes unresolved loading from a stable permission downgrade", () => {
  const base = {
    authState: "authenticated",
    workspaceId: "ws-a",
    user: { email: "owner@contoso.com", tenantScope: "tenant-a" },
    workspaceAccess: { workspace_id: "ws-a", authenticated: true, allowed: true, role: "owner" },
  };
  assert.equal(finopsAuthorizationBoundary(base), "");

  const allowed = finopsAuthorizationBoundary({
    ...base,
    capabilities: {
      workspace_id: "ws-a",
      sections: { finops: { permissions: { "finops.summary.read": true } } },
    },
  });
  const downgraded = finopsAuthorizationBoundary({
    ...base,
    capabilities: {
      workspace_id: "ws-a",
      sections: { finops: { permissions: { "finops.summary.read": false } } },
    },
  });
  const denied = finopsAuthorizationBoundary({
    ...base,
    workspaceAccess: { workspace_id: "ws-a", authenticated: true, allowed: false, role: null },
  });
  const renamedAuthState = finopsAuthorizationBoundary({
    ...base,
    authState: "signed-in",
    capabilities: {
      workspace_id: "ws-a",
      sections: { finops: { permissions: { "finops.summary.read": true } } },
    },
  });

  assert.ok(allowed);
  assert.ok(downgraded);
  assert.ok(denied);
  assert.notEqual(allowed, downgraded);
  assert.notEqual(allowed, denied);
  assert.equal(allowed, renamedAuthState);
  for (const value of [allowed, downgraded, denied]) {
    assert.doesNotMatch(value, /owner@contoso\.com/i);
  }
});


test("automatic refresh is exactly ten minutes and only due for the visible current tab", () => {
  assert.equal(FINOPS_REFRESH_MS, 600_000);
  assert.equal(shouldRefreshFinOpsTab({
    hidden: true,
    now: 700_000,
    lastSuccessfulAt: 1_000,
  }), false);
  assert.equal(shouldRefreshFinOpsTab({
    hidden: false,
    now: 600_999,
    lastSuccessfulAt: 1_000,
  }), false);
  assert.equal(shouldRefreshFinOpsTab({
    hidden: false,
    now: 601_000,
    lastSuccessfulAt: 1_000,
  }), true);
});


test("refresh tracker keeps successful timestamps separate by tab and scope", () => {
  const tracker = createFinOpsRefreshTracker();
  tracker.markSuccessful("scope-a", "overview", 1_000);
  tracker.markSuccessful("scope-a", "roi", 2_000);
  tracker.markSuccessful("scope-b", "overview", 3_000);

  assert.equal(tracker.lastSuccessfulAt("scope-a", "overview"), 1_000);
  assert.equal(tracker.lastSuccessfulAt("scope-a", "roi"), 2_000);
  assert.equal(tracker.lastSuccessfulAt("scope-b", "overview"), 3_000);
  assert.equal(tracker.isDue("scope-a", "overview", { now: 601_000, hidden: false }), true);
  assert.equal(tracker.isDue("scope-a", "roi", { now: 601_000, hidden: false }), false);
});


test("refresh tracker consumes a force request once per scope tab and resource", () => {
  const tracker = createFinOpsRefreshTracker();
  const refresh = { version: 3, force: true, scopeKey: "scope-a" };

  assert.equal(tracker.consumeForce("scope-a", "cost", "main", refresh), true);
  assert.equal(tracker.consumeForce("scope-a", "cost", "main", refresh), false);
  assert.equal(tracker.consumeForce("scope-a", "cost", "comparison", refresh), true);
  assert.equal(tracker.consumeForce("scope-b", "cost", "main", refresh), false);
  assert.equal(tracker.consumeForce("scope-a", "cost", "main", {
    version: 4,
    force: false,
    scopeKey: "scope-a",
  }), false);
});


test("refresh tracker reset clears success and consumed-force state at an authorization boundary", () => {
  const tracker = createFinOpsRefreshTracker();
  const refresh = { version: 1, force: true, scopeKey: "auth-a:query-a" };
  tracker.markSuccessful("auth-a:query-a", "roi", 1_000);
  assert.equal(tracker.consumeForce("auth-a:query-a", "roi", "main", refresh), true);

  tracker.reset();

  assert.equal(tracker.lastSuccessfulAt("auth-a:query-a", "roi"), 0);
  assert.equal(tracker.consumeForce("auth-a:query-a", "roi", "main", refresh), true);
});


test("request guard rejects late completion after key change or deactivation", () => {
  const guard = createFinOpsRequestGuard();
  const first = guard.begin("cost:scope-a");
  assert.equal(guard.isActive(first), true);
  const second = guard.begin("cost:scope-b");
  assert.equal(guard.isActive(first), false);
  assert.equal(guard.isActive(second), true);
  guard.deactivate(second);
  assert.equal(guard.isActive(second), false);
});


test("request guard blocks late success and error from a loader that ignores abort", async () => {
  const guard = createFinOpsRequestGuard();
  let visible = { key: "initial" };
  let resolveOld;
  const oldRequest = guard.begin("comparison:scope-a");
  const ignoredAbortSuccess = new Promise((resolve) => { resolveOld = resolve; })
    .then((value) => {
      if (guard.isActive(oldRequest)) visible = value;
    });

  const controller = new AbortController();
  controller.abort();
  const currentRequest = guard.begin("comparison:scope-b");
  visible = { key: "scope-b" };
  resolveOld({ key: "scope-a-late" });
  await ignoredAbortSuccess;
  assert.deepEqual(visible, { key: "scope-b" });

  let rejectLate;
  const ignoredAbortError = new Promise((_resolve, reject) => { rejectLate = reject; })
    .catch(() => {
      if (guard.isActive(currentRequest)) visible = { key: "late-error" };
    });
  guard.deactivate(currentRequest);
  rejectLate(new Error("late failure"));
  await ignoredAbortError;
  assert.deepEqual(visible, { key: "scope-b" });
});


test("load failure settlement preserves data and ends active AbortError loading", () => {
  const cached = { revision: 7 };
  const networkError = new Error("network unavailable");
  const failed = settleFinOpsLoadFailure({
    loading: true,
    updating: true,
    error: "",
    data: cached,
  }, networkError);
  assert.equal(failed.loading, false);
  assert.equal(failed.updating, false);
  assert.equal(failed.data, cached);
  assert.equal(failed.error, "network unavailable");

  const abortError = new Error("aborted");
  abortError.name = "AbortError";
  const aborted = settleFinOpsLoadFailure({ loading: true, updating: false, error: "", data: null }, abortError);
  assert.equal(aborted.loading, false);
  assert.match(aborted.error, /重试/);
});


function navigationScope() {
  return finopsPreloadScope({
    authState: "authenticated",
    workspaceId: "ws-a",
    user: { tenantScope: "tenant-a" },
    workspaceAccess: { workspace_id: "ws-a", authenticated: true, allowed: true, role: "owner" },
    capabilities: {
      workspace_id: "ws-a",
      sections: {
        finops: {
          visible: true,
          permissions: {
            "finops.summary.read": true,
            "finops.cost.read": true,
            "finops.roi.read": true,
          },
        },
      },
    },
  });
}


function currentQuery(workspaceId = "ws-a") {
  return {
    workspaceId,
    from: "2026-07-01T00:00:00Z",
    to: "2026-07-31T23:59:59Z",
    departmentId: "",
    agentId: "",
    model: "",
  };
}


test("tab keys isolate page resources while keeping ROI and risk complete objects", () => {
  const scope = navigationScope();
  const roi = finopsTabDataKey("roi", { scope, query: currentQuery() });
  const risk = finopsTabDataKey("risk", { scope, query: currentQuery() });
  const cost = finopsTabDataKey("cost", { scope, query: currentQuery() });

  assert.notEqual(roi, risk);
  assert.notEqual(roi, cost);
  assert.match(roi, /"domain":"roi"/);
  assert.match(risk, /"domain":"risk"/);
  assert.doesNotMatch(roi, /owner@contoso\.com/i);
});


test("tab lifecycle renders fresh without a request and stale while one revalidation runs", async () => {
  const scope = navigationScope();
  const key = finopsTabDataKey("roi", { scope, query: currentQuery() });
  await loadFinOpsData(key, async () => ({ revision: 1 }), { domain: "roi", now: 1_000 });
  let calls = 0;
  const fresh = loadFinOpsTab({
    tab: "roi",
    key,
    now: 200_000,
    loader: async () => {
      calls += 1;
      return { revision: 2 };
    },
  });
  assert.equal(fresh.cache.status, "fresh");
  assert.equal(fresh.cache.value.revision, 1);
  assert.equal(fresh.requested, false);
  assert.equal((await fresh.promise).revision, 1);
  assert.equal(calls, 0);

  let resolve;
  const stale = loadFinOpsTab({
    tab: "roi",
    key,
    now: 400_000,
    loader: () => {
      calls += 1;
      return new Promise((done) => { resolve = done; });
    },
  });
  const duplicate = loadFinOpsTab({
    tab: "roi",
    key,
    now: 400_000,
    loader: () => {
      calls += 100;
      return Promise.resolve({ revision: 99 });
    },
  });
  assert.equal(stale.cache.status, "stale_usable");
  assert.equal(stale.cache.value.revision, 1);
  assert.equal(stale.requested, true);
  assert.equal(stale.ownsRequest, true);
  assert.equal(duplicate.ownsRequest, false);
  assert.equal(stale.promise, duplicate.promise);
  assert.equal(calls, 1);
  resolve({ revision: 2 });
  assert.equal((await stale.promise).revision, 2);
});


test("missing lifecycle requests once and force is the only path that asks the server to refresh", async () => {
  const scope = navigationScope();
  const key = finopsTabDataKey("risk", { scope, query: currentQuery() });
  const refreshHints = [];
  const missing = loadFinOpsTab({
    tab: "risk",
    key,
    now: 1_000,
    loader: async ({ refresh }) => {
      refreshHints.push(refresh);
      return { revision: 1 };
    },
  });
  assert.equal(missing.cache.status, "missing");
  assert.equal(missing.requested, true);
  await missing.promise;

  const forced = loadFinOpsTab({
    tab: "risk",
    key,
    force: true,
    now: 2_000,
    loader: async ({ refresh }) => {
      refreshHints.push(refresh);
      return { revision: 2 };
    },
  });
  await forced.promise;
  assert.deepEqual(refreshHints, [false, true]);
});


test("force arriving during ordinary tab load queues one real refresh request", async () => {
  const scope = navigationScope();
  const key = finopsTabDataKey("risk", { scope, query: currentQuery() });
  const refreshHints = [];
  let resolveOrdinary;
  let resolveForced;
  const ordinary = loadFinOpsTab({
    tab: "risk",
    key,
    now: 1_000,
    loader: ({ refresh }) => {
      refreshHints.push(refresh);
      return new Promise((resolve) => { resolveOrdinary = resolve; });
    },
  });
  const forced = loadFinOpsTab({
    tab: "risk",
    key,
    force: true,
    now: 2_000,
    loader: ({ refresh }) => {
      refreshHints.push(refresh);
      return new Promise((resolve) => { resolveForced = resolve; });
    },
  });
  const duplicateForced = loadFinOpsTab({
    tab: "risk",
    key,
    force: true,
    now: 2_000,
    loader: () => { throw new Error("duplicate force should share pending work"); },
  });

  assert.equal(ordinary.ownsRequest, true);
  assert.equal(forced.ownsRequest, false);
  assert.equal(forced.promise, duplicateForced.promise);
  assert.deepEqual(refreshHints, [false]);
  resolveOrdinary({ revision: 1 });
  await ordinary.promise;
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(refreshHints, [false, true]);
  resolveForced({ revision: 2 });
  assert.equal((await forced.promise).revision, 2);
});


test("tab intent prefetches only the selected resource and shares in-flight work", async () => {
  const scope = navigationScope();
  const keys = Object.fromEntries(["overview", "cost", "roi", "risk"].map((tab) => [
    tab,
    finopsTabDataKey(tab, { scope, query: currentQuery(), defaultScope: tab === "overview" }),
  ]));
  const calls = [];
  let resolve;
  const loaders = Object.fromEntries(Object.keys(keys).map((tab) => [tab, () => {
    calls.push(tab);
    return tab === "roi"
      ? new Promise((done) => { resolve = done; })
      : Promise.resolve({ tab });
  }]));

  const first = prefetchFinOpsTab("roi", { keys, loaders, now: 1_000 });
  const second = prefetchFinOpsTab("roi", { keys, loaders, now: 1_000 });
  assert.equal(first, second);
  assert.deepEqual(calls, ["roi"]);
  resolve({ tab: "roi" });
  assert.deepEqual(await first, { tab: "roi" });
});


test("tab intent handlers use pointer focus and touch with the selected tab", () => {
  const calls = [];
  const handlers = finopsTabIntentHandlers("risk", (tab) => calls.push(tab));
  handlers.onPointerEnter();
  handlers.onFocus();
  handlers.onTouchStart();
  assert.deepEqual(calls, ["risk", "risk", "risk"]);
});


test("successful mutations invalidate exact domains in only the active workspace", async () => {
  const scope = navigationScope();
  const keys = {};
  for (const workspaceId of ["ws-a", "ws-b"]) {
    const scoped = workspaceId === "ws-a"
      ? scope
      : { ...scope, workspaceId, authorizedWorkspaceScope: [{ workspaceId, role: "owner" }] };
    for (const domain of ["overview", "cost", "roi", "risk"]) {
      const key = finopsTabDataKey(domain, {
        scope: scoped,
        query: currentQuery(workspaceId),
        defaultScope: false,
      });
      keys[`${workspaceId}:${domain}`] = key;
      await loadFinOpsData(key, async () => ({ workspaceId, domain }), { domain, now: 1_000 });
    }
    const comparisonKey = finopsDataKey({
      tenantScope: scoped.tenantScope,
      permissionSummary: [...scoped.permissions, ...scoped.authorizedWorkspaceScope],
      workspaceId,
      domain: "cost:comparison",
      window: { from: "2026-06-01", to: "2026-06-30" },
    });
    keys[`${workspaceId}:cost:comparison`] = comparisonKey;
    await loadFinOpsData(comparisonKey, async () => ({ workspaceId, domain: "cost:comparison" }), {
      domain: "cost:comparison",
      now: 1_000,
    });
  }

  assert.equal(invalidateFinOpsMutation("roi_scenario", { workspaceId: "ws-a" }), 1);
  assert.equal(readFinOpsData(keys["ws-a:roi"], 1_000).status, "missing");
  assert.equal(readFinOpsData(keys["ws-a:risk"], 1_000).status, "fresh");
  assert.equal(readFinOpsData(keys["ws-b:roi"], 1_000).status, "fresh");

  assert.equal(invalidateFinOpsMutation("model_setting", { workspaceId: "ws-a" }), 4);
  for (const domain of ["overview", "cost", "risk"]) {
    assert.equal(readFinOpsData(keys[`ws-a:${domain}`], 1_000).status, "missing");
    assert.equal(readFinOpsData(keys[`ws-b:${domain}`], 1_000).status, "fresh");
  }
  assert.equal(readFinOpsData(keys["ws-a:cost:comparison"], 1_000).status, "missing");
  assert.equal(readFinOpsData(keys["ws-b:cost:comparison"], 1_000).status, "fresh");
});


test("scheduleFinOpsPreload prefers idle callback and returns cancellation", () => {
  let scheduled;
  let cancelled = null;
  const host = {
    requestIdleCallback(callback) {
      scheduled = callback;
      return 17;
    },
    cancelIdleCallback(handle) {
      cancelled = handle;
    },
  };
  let calls = 0;

  const cancel = scheduleFinOpsPreload(() => {
    calls += 1;
  }, host);
  scheduled();
  cancel();

  assert.equal(calls, 1);
  assert.equal(cancelled, 17);
});


test("scheduleFinOpsPreload fallback is bounded and cancellable before it runs", () => {
  let scheduled;
  let delay;
  let cancelled = null;
  const host = {
    setTimeout(callback, timeout) {
      scheduled = callback;
      delay = timeout;
      return 22;
    },
    clearTimeout(handle) {
      cancelled = handle;
    },
  };
  let calls = 0;
  const cancel = scheduleFinOpsPreload(() => { calls += 1; }, host);
  cancel();

  assert.ok(delay > 0 && delay <= 1_000);
  assert.equal(cancelled, 22);
  assert.equal(calls, 0);
  assert.equal(typeof scheduled, "function");
});


test("idle ROI cleanup does not abort shared store work after prefetch starts", async () => {
  const scope = navigationScope();
  const keys = Object.fromEntries(["overview", "cost", "roi", "risk"].map((tab) => [
    tab,
    finopsTabDataKey(tab, { scope, query: currentQuery() }),
  ]));
  const calls = [];
  let scheduled;
  let cancelledIdle = false;
  let observedSignal;
  let resolveShared;
  const host = {
    requestIdleCallback(callback) {
      scheduled = callback;
      return 12;
    },
    cancelIdleCallback() { cancelledIdle = true; },
  };
  const loaders = Object.fromEntries(Object.keys(keys).map((tab) => [tab, ({ signal }) => {
    calls.push(tab);
    observedSignal = signal;
    return new Promise((resolve) => { resolveShared = resolve; });
  }]));

  const cancel = scheduleFinOpsTabPreload("roi", { keys, loaders, host });
  scheduled();
  assert.deepEqual(calls, ["roi"]);
  assert.equal(readFinOpsData(keys.roi).inFlight, true);
  cancel();
  assert.equal(cancelledIdle, false);
  assert.equal(observedSignal.aborted, false);
  resolveShared({ tab: "roi" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(readFinOpsData(keys.roi).status, "fresh");
});


test("App and Portal import the shared production cache lifecycle", async () => {
  const { readFile } = await import("node:fs/promises");
  const [app, portal] = await Promise.all([
    readFile(new URL("./App.jsx", import.meta.url), "utf8"),
    readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8"),
  ]);

  assert.match(app, /reconcileFinOpsAuthorizationScope/);
  assert.match(app, /tenantScope:/);
  assert.match(app, /authorizationFingerprint:\s*finopsAuthorizationKey/);
  assert.doesNotMatch(app, /clearFinOpsBootstrap\(finopsScope\.key\)/);
  assert.match(portal, /loadFinOpsTab/);
  assert.match(portal, /prefetchFinOpsTab/);
  assert.match(portal, /finopsTabIntentHandlers/);
  assert.match(portal, /useFinOpsRefreshLifecycle/);
  assert.match(portal, /useFinOpsTabResource/);
  assert.match(portal, /useFinOpsComparisonLifecycle/);
  assert.match(portal, /useFinOpsIdlePreload/);
  assert.doesNotMatch(portal, /prefetchFinOpsBootstrap\([\s\S]{0,240}force:\s*true/);
});
