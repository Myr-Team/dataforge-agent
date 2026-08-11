import assert from "node:assert/strict";
import test from "node:test";

import {
  prefetchSettingsHome,
  reconcileSettingsAuthorizationScope,
  scheduleSettingsPreload,
  settingsAuthorizationBoundary,
  settingsIntentHandlers,
  settingsPreloadScope,
  settingsResourceKey,
  settingsResourceKeys,
} from "./settingsNavigation.js";
import { clearSettingsData, loadSettingsResource, peekSettingsResource } from "./settingsDataStore.js";


test.afterEach(() => clearSettingsData());


function authority(overrides = {}) {
  return {
    authState: "authenticated",
    workspaceId: "ws-a",
    user: {
      tenantRef: "tenant_opaque_reference_a",
      actorRef: "actor_opaque_reference_a",
      sessionRef: "session_opaque_reference_a",
      email: "owner@contoso.test",
    },
    workspaceAccess: { workspace_id: "ws-a", authenticated: true, allowed: true, role: "owner" },
    capabilities: {
      workspace_id: "ws-a",
      sections: {
        finops: { permissions: { "finops.summary.read": true } },
        governance: { permissions: { "member.read": true, "member.manage": true } },
      },
    },
    ...overrides,
  };
}


test("settings scope requires server-issued tenant actor and session refs", () => {
  assert.equal(settingsPreloadScope(authority({ workspaceAccess: null })), null);
  assert.equal(settingsPreloadScope(authority({ user: { tenantRef: "tenant_opaque_reference_a", actorRef: "actor_opaque_reference_a" } })), null);
  const scope = settingsPreloadScope(authority());
  assert.equal(scope.workspaceId, "ws-a");
  assert.match(scope.key, /ws-a/);
  assert.match(scope.key, /tenant_opaque_reference_a/);
  assert.match(scope.key, /session_opaque_reference_a/);
  assert.doesNotMatch(scope.key, /owner@contoso\.test/);

  const changedPermission = settingsAuthorizationBoundary(authority({
    capabilities: { workspace_id: "ws-a", sections: { governance: { permissions: { "member.read": true, "member.manage": false } } } },
  }));
  assert.notEqual(settingsAuthorizationBoundary(authority()), changedPermission);
  assert.notEqual(settingsAuthorizationBoundary(authority()), settingsAuthorizationBoundary(authority({
    user: { tenantRef: "tenant_opaque_reference_a", actorRef: "actor_opaque_reference_a", sessionRef: "session_opaque_reference_b" },
  })));
});


test("settings authorization reconciliation clears the prior scope on workspace actor permission and logout boundaries", () => {
  const cleared = [];
  let current = reconcileSettingsAuthorizationScope("", "scope-a", (scope) => cleared.push(scope));
  current = reconcileSettingsAuthorizationScope(current, "scope-b", (scope) => cleared.push(scope));
  current = reconcileSettingsAuthorizationScope(current, "", (scope) => cleared.push(scope));

  assert.equal(current, "");
  assert.deepEqual(cleared, ["scope-a", "scope-b"]);
});


test("Settings intent handlers cover hover focus and touch", () => {
  const calls = [];
  const handlers = settingsIntentHandlers({ id: "settings" }, () => calls.push("preload"));
  handlers.onMouseEnter();
  handlers.onFocus();
  handlers.onTouchStart();
  assert.deepEqual(calls, ["preload", "preload", "preload"]);
  assert.deepEqual(settingsIntentHandlers({ id: "workspaces" }, () => {}), {});
});


test("Settings preload loads bounded home resources once and never member contracts", async () => {
  const scope = settingsPreloadScope(authority());
  const keys = settingsResourceKeys(scope);
  const calls = [];
  const loaders = Object.fromEntries(Object.keys(keys).map((resource) => [resource, async () => {
    calls.push(resource);
    return { resource };
  }]));

  await Promise.all([
    prefetchSettingsHome(scope, loaders, { now: 1_000 }),
    prefetchSettingsHome(scope, loaders, { now: 1_000 }),
  ]);

  assert.deepEqual(calls.sort(), ["alerts", "budget", "notification", "routing", "workspaceSettings"]);
  assert.equal(Object.hasOwn(keys, "members"), false);
  assert.equal(peekSettingsResource(keys.workspaceSettings, 1_000).status, "fresh");
});


test("home and child settings consumers resolve the same resource key in one scope", () => {
  const scope = settingsPreloadScope(authority());
  assert.equal(settingsResourceKeys(scope).budget, settingsResourceKey(scope, "budget"));
  assert.equal(settingsResourceKeys(scope).routing, settingsResourceKey(scope, "routing"));
  assert.notEqual(settingsResourceKey(scope, "budget"), settingsResourceKey({ ...scope, workspaceId: "ws-b" }, "budget"));
});


test("settings preload is idle-scheduled and cancellable", () => {
  let callback;
  let cancelled = null;
  const host = {
    requestIdleCallback(next) { callback = next; return 12; },
    cancelIdleCallback(handle) { cancelled = handle; },
  };
  let calls = 0;
  const cancel = scheduleSettingsPreload(() => { calls += 1; }, host);
  callback();
  cancel();
  assert.equal(calls, 1);
  assert.equal(cancelled, 12);
});


test("cached system settings is the only initial system-status source", async () => {
  const scope = settingsPreloadScope(authority());
  const settingsKey = settingsResourceKeys(scope).workspaceSettings;
  await loadSettingsResource(settingsKey, async () => ({ system_status: { release: { version: "v1" } } }), { now: 0 });
  assert.equal(peekSettingsResource(settingsKey, 0).value.system_status.release.version, "v1");
});


test("App wires Settings navigation intent and passes the current scoped cache boundary", async () => {
  const { readFile } = await import("node:fs/promises");
  const app = await readFile(new URL("./App.jsx", import.meta.url), "utf8");
  const components = await readFile(new URL("./components.jsx", import.meta.url), "utf8");

  assert.match(app, /settingsAuthorizationBoundary/);
  assert.match(app, /tenantRef: String\(session\?\.tenant_ref/);
  assert.match(app, /actorRef: String\(session\?\.actor_ref/);
  assert.match(app, /sessionRef: String\(session\?\.session_ref/);
  assert.match(app, /onSettingsIntent=\{preloadSettings\}/);
  assert.match(app, /settingsPreloadScope=\{settingsScope\}/);
  assert.match(components, /settingsScope=\{settingsPreloadScope\}/);
  assert.match(components, /loadWorkspaceSettings\(workspaceId, \{ signal \}\)/);
  assert.match(components, /data\?\.system_status/);
  assert.doesNotMatch(components, /useEffect\(\(\) => \{ loadSystemStatus\(\)\.then\(setSys\)/);
});
