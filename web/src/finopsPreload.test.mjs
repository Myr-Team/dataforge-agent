import assert from "node:assert/strict";
import test from "node:test";

import {
  clearFinOpsBootstrap,
  finopsScopeKey,
  prefetchFinOpsBootstrap,
  readFinOpsBootstrap,
} from "./finopsPreload.js";


function scopeKey(workspaceId = "ws-a") {
  return finopsScopeKey({
    tenantKey: "easy-auth",
    workspaceId,
    identityKey: "owner@contoso.com",
    permissions: ["finops.summary.read", "finops.cost.read"],
    filters: { window: "30d" },
  });
}


test.afterEach(() => {
  clearFinOpsBootstrap();
});


test("finopsScopeKey is stable across permission ordering and separates workspaces", () => {
  const first = scopeKey("ws-a");
  const reordered = finopsScopeKey({
    tenantKey: "easy-auth",
    workspaceId: "ws-a",
    identityKey: "owner@contoso.com",
    permissions: ["finops.cost.read", "finops.summary.read"],
    filters: { window: "30d" },
  });

  assert.equal(first, reordered);
  assert.notEqual(first, scopeKey("ws-b"));
});


test("same scope shares one in-flight bootstrap request", async () => {
  let calls = 0;
  let resolveLoader;
  const loader = () => {
    calls += 1;
    return new Promise((resolve) => {
      resolveLoader = resolve;
    });
  };
  const key = scopeKey();

  const first = prefetchFinOpsBootstrap(key, loader);
  const second = prefetchFinOpsBootstrap(key, loader);
  resolveLoader({ freshness: { generated_at: "2026-07-24T02:00:00Z" } });

  assert.equal(await first, await second);
  assert.equal(calls, 1);
});


test("bootstrap compatibility uses the five and thirty minute store lifecycle", async () => {
  const key = scopeKey();
  await prefetchFinOpsBootstrap(
    key,
    async () => ({ overview: { metrics: { requests: 7 } } }),
    { now: 1_000 },
  );

  assert.equal(readFinOpsBootstrap(key, 301_000).status, "fresh");
  assert.equal(readFinOpsBootstrap(key, 301_001).status, "stale");
  assert.equal(readFinOpsBootstrap(key, 1_801_001).status, "expired");
  assert.equal(readFinOpsBootstrap(key, 1_801_001).value, null);
});


test("clearing a scope aborts its in-flight request", async () => {
  const key = scopeKey();
  let observedSignal;
  const pending = prefetchFinOpsBootstrap(key, ({ signal }) => {
    observedSignal = signal;
    return new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
    });
  });

  clearFinOpsBootstrap(key);

  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(observedSignal.aborted, true);
  assert.deepEqual(readFinOpsBootstrap(key), { status: "missing", value: null });
});
