import assert from "node:assert/strict";
import test from "node:test";

import {
  clearFinOpsData,
  finopsDataKey,
  invalidateFinOpsData,
  loadFinOpsData,
  readFinOpsData,
} from "./finopsDataStore.js";


test.afterEach(() => {
  clearFinOpsData();
});


test("finopsDataKey is stable across safe filter and workspace-role ordering", () => {
  const first = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: { "ws-b": "viewer", "ws-a": "owner" },
    workspaceId: "ws-a",
    domain: "roi",
    from: "2026-07-01T00:00:00Z",
    to: "2026-07-31T00:00:00Z",
    filters: { model: "gpt-safe", departmentId: "finance" },
    schemaRevision: "decision-v2",
  });
  const reordered = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: { "ws-a": "owner", "ws-b": "viewer" },
    workspaceId: "ws-a",
    domain: "roi",
    from: "2026-07-01T00:00:00Z",
    to: "2026-07-31T00:00:00Z",
    filters: { departmentId: "finance", model: "gpt-safe" },
    schemaRevision: "decision-v2",
  });

  assert.equal(first, reordered);
  assert.notEqual(first, finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: { "ws-a": "admin", "ws-b": "viewer" },
    workspaceId: "ws-a",
    domain: "roi",
    from: "2026-07-01T00:00:00Z",
    to: "2026-07-31T00:00:00Z",
    filters: { departmentId: "finance", model: "gpt-safe" },
    schemaRevision: "decision-v2",
  }));
});


test("finopsDataKey excludes raw identity and secret-bearing input", () => {
  const key = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: [{ workspaceId: "ws-a", role: "owner" }],
    workspaceId: "ws-a",
    domain: "risk",
    filters: {
      model: "gpt-safe",
      actorEmail: "owner@example.test",
      email: "standalone@example.test",
      user: "raw-user-marker",
      token: "token-secret-marker",
      headers: { Authorization: "Bearer secret-marker" },
      prompt: "prompt-secret-marker",
      apiKey: "key-secret-marker",
      key: "generic-key-secret-marker",
      providerResponse: "provider-secret-marker",
    },
    actorIdentity: "owner@example.test",
    accessToken: "top-level-secret-marker",
  });

  assert.match(key, /tenant-safe/);
  assert.match(key, /gpt-safe/);
  for (const secret of [
    "owner@example.test",
    "standalone@example.test",
    "raw-user-marker",
    "token-secret-marker",
    "secret-marker",
    "prompt-secret-marker",
    "key-secret-marker",
    "generic-key-secret-marker",
    "provider-secret-marker",
    "top-level-secret-marker",
  ]) {
    assert.doesNotMatch(key, new RegExp(secret.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
});


test("finopsDataKey accepts only allowlisted workspace-role permission entries", () => {
  const long129 = `w${"s".repeat(128)}`;
  const long160 = `w${"s".repeat(159)}`;
  const validBusinessWorkspaces = [
    { workspaceId: "identity-governance", role: "owner" },
    { workspaceId: "provider-demo", role: "viewer" },
    { workspaceId: "prompt-library", role: "owner" },
    { workspaceId: "user-research", role: "viewer" },
    { workspaceId: "actor-analysis", role: "owner" },
    { workspaceId: "token-secret-marker", role: "viewer" },
    { workspaceId: "email-workspace", role: "owner" },
    { workspaceId: "provider-response-workspace", role: "viewer" },
    { workspaceId: "region:finance", role: "owner" },
    { workspaceId: long129, role: "viewer" },
    { workspaceId: long160, role: "owner" },
  ];
  const safe = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: [
      "finops.cost.read",
      "finops.summary.read",
      { workspaceId: " ws-b ", role: "VIEWER" },
      { workspace_id: "ws-a", role: "owner" },
      ...validBusinessWorkspaces,
    ],
    workspaceId: "ws-a",
    domain: "risk",
  });
  const hostile = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: [
      { workspace_id: "ws-a", role: "owner" },
      { workspaceId: "ws-email", role: "owner@example.test" },
      { workspaceId: "ws-token", role: "ToKeN-secret-marker" },
      { workspaceId: "ws-key", role: "api-Key-secret-marker" },
      { workspaceId: "ws-prompt", role: "prompt-secret-marker" },
      { workspaceId: "ws-provider", role: "provider-response-marker" },
      { workspaceId: "ws-object", role: { value: "object-secret-marker" } },
      { workspaceId: "owner@example.test", role: "owner" },
      { workspaceId: "ws/invalid", role: "owner" },
      { workspaceId: "ws?query", role: "viewer" },
      { workspaceId: "ws#fragment", role: "owner" },
      { workspaceId: "ws\\path", role: "viewer" },
      { workspaceId: "ws\ncontrol", role: "owner" },
      { workspaceId: `w${"s".repeat(160)}`, role: "owner" },
      { workspaceId: { value: "ws-object" }, role: "viewer" },
      { workspaceId: 42, role: "owner" },
      { arbitrary: { email: "nested@example.test" } },
      "raw-string-secret-marker",
      "finops.summary.read",
      "finops.cost.read",
      "finops.unknown.read",
      { workspaceId: "ws-b", role: "viewer" },
      ...validBusinessWorkspaces,
    ].reverse(),
    workspaceId: "ws-a",
    domain: "risk",
  });

  assert.equal(hostile, safe);
  assert.match(safe, /ws-a:owner/);
  assert.match(safe, /ws-b:viewer/);
  for (const workspaceId of [
    "identity-governance",
    "provider-demo",
    "prompt-library",
    "user-research",
    "actor-analysis",
    "token-secret-marker",
    "email-workspace",
    "provider-response-workspace",
    "region:finance",
    long129,
    long160,
  ]) {
    assert.match(safe, new RegExp(workspaceId));
  }
  for (const secret of [
    "owner@example.test",
    "key-secret-marker",
    "prompt-secret-marker",
    "object-secret-marker",
    "nested@example.test",
    "raw-string-secret-marker",
    "finops.unknown.read",
  ]) {
    assert.doesNotMatch(hostile.toLowerCase(), new RegExp(secret));
  }

  const ownerKey = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: [{ workspaceId: "identity-governance", role: "owner" }],
    workspaceId: "identity-governance",
    domain: "overview",
  });
  const viewerKey = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: [{ workspaceId: "identity-governance", role: "viewer" }],
    workspaceId: "identity-governance",
    domain: "overview",
  });
  assert.notEqual(ownerKey, viewerKey);
});


test("legacy capability keys are allowlisted distinct deduplicated and order stable", () => {
  const summaryOnly = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: ["finops.summary.read"],
    workspaceId: "ws-a",
    domain: "overview",
  });
  const summaryAndCost = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: ["finops.summary.read", "finops.cost.read"],
    workspaceId: "ws-a",
    domain: "overview",
  });
  const mixedReordered = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: [
      { workspaceId: "ws-a", role: "owner" },
      "finops.cost.read",
      "finops.summary.read",
      "finops.cost.read",
      "finops.arbitrary.read",
      { workspaceId: "token-secret-marker", role: "viewer" },
    ],
    workspaceId: "ws-a",
    domain: "overview",
  });
  const mixedCanonical = finopsDataKey({
    tenantScope: "tenant-safe",
    permissionSummary: [
      "finops.summary.read",
      { workspace_id: "ws-a", role: "OWNER" },
      { workspaceId: "token-secret-marker", role: "viewer" },
      "finops.cost.read",
    ],
    workspaceId: "ws-a",
    domain: "overview",
  });

  assert.notEqual(summaryOnly, summaryAndCost);
  assert.equal(mixedReordered, mixedCanonical);
  assert.match(summaryAndCost, /finops\.summary\.read/);
  assert.match(summaryAndCost, /finops\.cost\.read/);
  assert.doesNotMatch(mixedReordered, /finops\.arbitrary\.read/);
  assert.match(mixedReordered, /token-secret-marker:viewer/);
});


test("fresh entries render without another request", async () => {
  let calls = 0;
  const loader = async () => {
    calls += 1;
    return { decision: { title: "ready" } };
  };

  await loadFinOpsData("tenant/ws/roi", loader, { domain: "roi", now: 1_000 });
  const value = await loadFinOpsData("tenant/ws/roi", loader, {
    domain: "roi",
    now: 300_999,
  });

  assert.equal(calls, 1);
  assert.equal(value.decision.title, "ready");
  assert.equal(readFinOpsData("tenant/ws/roi", 300_999).status, "fresh");
});


test("stale entries remain visible while one request revalidates", async () => {
  await loadFinOpsData(
    "tenant/ws/risk",
    async () => ({ revision: 1 }),
    { domain: "risk", now: 0 },
  );
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const first = loadFinOpsData(
    "tenant/ws/risk",
    () => pending,
    { domain: "risk", now: 360_001 },
  );
  const second = loadFinOpsData(
    "tenant/ws/risk",
    () => { throw new Error("duplicate request"); },
    { domain: "risk", force: true, now: 360_001 },
  );

  assert.equal(first, second);
  assert.equal(readFinOpsData("tenant/ws/risk", 360_001).status, "stale_usable");
  assert.equal(readFinOpsData("tenant/ws/risk", 360_001).value.revision, 1);
  resolve({ revision: 2 });
  assert.deepEqual(await Promise.all([first, second]), [{ revision: 2 }, { revision: 2 }]);
  assert.equal(readFinOpsData("tenant/ws/risk", 360_001).value.revision, 2);
});


test("failed revalidation preserves stale value and records only a bounded public error", async () => {
  await loadFinOpsData(
    "tenant/ws/risk",
    async () => ({ revision: 1 }),
    { domain: "risk", now: 1_000 },
  );

  await assert.rejects(
    loadFinOpsData(
      "tenant/ws/risk",
      async () => {
        const error = new Error("provider secret=do-not-expose");
        error.status = 503;
        throw error;
      },
      { domain: "risk", force: true, now: 400_000 },
    ),
    /do-not-expose/,
  );

  const cached = readFinOpsData("tenant/ws/risk", 400_000);
  assert.equal(cached.status, "stale_usable");
  assert.equal(cached.value.revision, 1);
  assert.deepEqual(cached.lastError, {
    code: "refresh_failed",
    status: 503,
    message: "FinOps data refresh failed",
    occurredAt: 400_000,
  });
  assert.doesNotMatch(JSON.stringify(cached.lastError), /do-not-expose/);
});


test("malformed payload never overwrites a usable value", async () => {
  await loadFinOpsData(
    "tenant/ws/roi",
    async () => ({ revision: 1 }),
    { domain: "roi", now: 1_000 },
  );

  await assert.rejects(
    loadFinOpsData(
      "tenant/ws/roi",
      async () => ["not", "an", "object"],
      { domain: "roi", force: true, now: 400_000 },
    ),
    /must be a non-array object/,
  );

  assert.equal(readFinOpsData("tenant/ws/roi", 400_000).value.revision, 1);
});


test("entries become stale after five minutes and expire after thirty minutes", async () => {
  await loadFinOpsData(
    "tenant/ws/roi",
    async () => ({ revision: 1 }),
    { domain: "roi", now: 1_000 },
  );

  assert.equal(readFinOpsData("tenant/ws/roi", 301_000).status, "fresh");
  assert.equal(readFinOpsData("tenant/ws/roi", 301_001).status, "stale_usable");
  assert.equal(readFinOpsData("tenant/ws/roi", 1_801_000).status, "stale_usable");
  assert.deepEqual(readFinOpsData("tenant/ws/roi", 1_801_001), {
    status: "expired",
    value: null,
    domain: "roi",
    storedAt: 1_000,
    inFlight: false,
    lastError: null,
  });
});


test("domain invalidation removes ROI without removing risk", async () => {
  const roiKey = "tenant/ws/roi";
  const riskKey = "tenant/ws/risk";
  await loadFinOpsData(roiKey, async () => ({ revision: 1 }), {
    domain: "roi",
    now: 1_000,
  });
  await loadFinOpsData(riskKey, async () => ({ revision: 1 }), {
    domain: "risk",
    now: 1_000,
  });

  assert.equal(invalidateFinOpsData((entry) => entry.domain === "roi"), 1);
  assert.equal(readFinOpsData(roiKey, 1_000).status, "missing");
  assert.equal(readFinOpsData(riskKey, 1_000).status, "fresh");
});


test("clear aborts in-flight loaders before deleting entries", async () => {
  let signal;
  const pending = loadFinOpsData(
    "tenant/ws/risk",
    ({ signal: observed }) => {
      signal = observed;
      return new Promise((_resolve, reject) => {
        observed.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    },
    { domain: "risk", now: 1_000 },
  );

  clearFinOpsData();

  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(signal.aborted, true);
  assert.deepEqual(readFinOpsData("tenant/ws/risk"), {
    status: "missing",
    value: null,
  });
});


for (const [label, remove] of [
  ["clear", () => clearFinOpsData("tenant/ws/race")],
  ["invalidation", () => invalidateFinOpsData((_entry, key) => key === "tenant/ws/race")],
]) {
  test(`late loader ignored abort cannot overwrite a replacement after ${label}`, async () => {
    const key = "tenant/ws/race";
    let resolveOld;
    let rejectOld;
    const old = loadFinOpsData(
      key,
      () => new Promise((resolve, reject) => {
        resolveOld = resolve;
        rejectOld = reject;
      }),
      { domain: "risk", now: 1_000 },
    );
    remove();

    let resolveNew;
    const replacement = loadFinOpsData(
      key,
      () => new Promise((resolve) => { resolveNew = resolve; }),
      { domain: "risk", now: 2_000 },
    );
    if (label === "clear") {
      resolveOld({ revision: 1 });
      assert.equal((await old).revision, 1);
    } else {
      const lateError = new Error("late-secret-marker");
      lateError.status = 503;
      rejectOld(lateError);
      await assert.rejects(old, /late-secret-marker/);
    }

    const duplicate = loadFinOpsData(
      key,
      () => { throw new Error("replacement in-flight was lost"); },
      { domain: "risk", force: true, now: 2_100 },
    );
    assert.equal(duplicate, replacement);
    resolveNew({ revision: 2 });
    assert.equal((await replacement).revision, 2);
    const current = readFinOpsData(key, 2_100);
    assert.equal(current.value.revision, 2);
    assert.equal(current.lastError, null);
  });
}


test("the browser store does not use persistent browser storage", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("./finopsDataStore.js", import.meta.url), "utf8");

  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie/i);
});
