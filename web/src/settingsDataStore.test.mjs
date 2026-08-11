import assert from "node:assert/strict";
import test from "node:test";

import {
  clearSettingsData,
  clearSettingsScope,
  invalidateSettingsResource,
  loadSettingsResource,
  peekSettingsResource,
  settingsDataKey,
} from "./settingsDataStore.js";


test.afterEach(() => {
  clearSettingsData();
});


test("settings keys isolate opaque authorization scopes and stable queries", () => {
  const first = settingsDataKey("opaque-a", "workspace-settings", { workspaceId: "ws-a", schemaRevision: "settings-v1" });
  const reordered = settingsDataKey("opaque-a", "workspace-settings", { schemaRevision: "settings-v1", workspaceId: "ws-a" });
  const otherScope = settingsDataKey("opaque-b", "workspace-settings", { workspaceId: "ws-a", schemaRevision: "settings-v1" });

  assert.equal(first, reordered);
  assert.notEqual(first, otherScope);
  assert.doesNotMatch(first, /@|token|secret/i);
});


test("fresh settings snapshot returns without another request", async () => {
  let calls = 0;
  const key = settingsDataKey("scope-a", "routing", { workspaceId: "ws-a" });
  await loadSettingsResource(key, async () => ({ revision: 1 }), { now: 1_000 });
  const value = await loadSettingsResource(key, async () => {
    calls += 1;
    return { revision: 2 };
  }, { now: 30_999 });

  assert.equal(calls, 0);
  assert.equal(value.revision, 1);
  assert.equal(peekSettingsResource(key, 30_999).status, "fresh");
});


test("stale settings snapshot remains usable while one shared revalidation runs", async () => {
  const key = settingsDataKey("scope-a", "budget", { workspaceId: "ws-a" });
  await loadSettingsResource(key, async () => ({ revision: 1 }), { now: 0 });
  let calls = 0;
  let resolveRefresh;
  const loader = () => {
    calls += 1;
    return new Promise((resolve) => { resolveRefresh = resolve; });
  };
  const first = loadSettingsResource(key, loader, { now: 30_001 });
  const second = loadSettingsResource(key, loader, { now: 30_001 });

  assert.equal(first, second);
  assert.equal(calls, 1);
  assert.deepEqual(peekSettingsResource(key, 30_001).value, { revision: 1 });
  assert.equal(peekSettingsResource(key, 30_001).status, "stale_usable");
  resolveRefresh({ revision: 2 });
  assert.deepEqual(await first, { revision: 2 });
});


test("failed refresh retains the stale snapshot and records a public error", async () => {
  const key = settingsDataKey("scope-a", "identity", { workspaceId: "ws-a" });
  await loadSettingsResource(key, async () => ({ revision: 1 }), { now: 0 });

  await assert.rejects(
    loadSettingsResource(key, async () => { throw Object.assign(new Error("upstream"), { status: 500 }); }, { now: 30_001 }),
    /upstream/,
  );

  const entry = peekSettingsResource(key, 30_001);
  assert.equal(entry.status, "stale_usable");
  assert.deepEqual(entry.value, { revision: 1 });
  assert.deepEqual(entry.lastError, { code: "refresh_failed", status: 500, message: "Settings refresh failed", occurredAt: 30_001 });
});


test("clearing a scope aborts its loader and ignores a late response", async () => {
  const scope = "scope-a";
  const key = settingsDataKey(scope, "members", { workspaceId: "ws-a" });
  let signal;
  let resolveLate;
  const pending = loadSettingsResource(key, ({ signal: currentSignal }) => {
    signal = currentSignal;
    return new Promise((resolve) => { resolveLate = resolve; });
  });

  clearSettingsScope(scope);
  assert.equal(signal.aborted, true);
  resolveLate({ revision: 1 });
  await pending;
  assert.equal(peekSettingsResource(key).status, "missing");
});


test("resource invalidation removes only the matching current-scope family", async () => {
  const scope = "scope-a";
  const budget = settingsDataKey(scope, "budget", { workspaceId: "ws-a" });
  const routing = settingsDataKey(scope, "routing", { workspaceId: "ws-a" });
  await Promise.all([
    loadSettingsResource(budget, async () => ({ revision: 1 }), { now: 0 }),
    loadSettingsResource(routing, async () => ({ revision: 1 }), { now: 0 }),
  ]);

  assert.equal(invalidateSettingsResource(budget), 1);
  assert.equal(peekSettingsResource(budget).status, "missing");
  assert.equal(peekSettingsResource(routing, 0).status, "fresh");
});


test("readiness uses its 15 second fresh and 60 second stale policy", async () => {
  const key = settingsDataKey("scope-a", "readiness", { workspaceId: "ws-a" });
  await loadSettingsResource(key, async () => ({ generated_at: "now" }), { now: 0, freshMs: 15_000, staleUsableMs: 60_000 });

  assert.equal(peekSettingsResource(key, 15_000, { freshMs: 15_000, staleUsableMs: 60_000 }).status, "fresh");
  assert.equal(peekSettingsResource(key, 15_001, { freshMs: 15_000, staleUsableMs: 60_000 }).status, "stale_usable");
  assert.equal(peekSettingsResource(key, 60_001, { freshMs: 15_000, staleUsableMs: 60_000 }).status, "expired");
});
