import assert from "node:assert/strict";
import test from "node:test";

import {
  REMEDIATION_CONFLICT_MESSAGE,
  orchestrateRemediationMutation,
} from "./finops/remediationOrchestration.js";


function harness(overrides = {}) {
  const calls = { create: [], review: [], promote: [], reload: [], refresh: 0 };
  return {
    calls,
    options: {
      clients: {
        async create(payload) { calls.create.push(payload); return { draft: { draft_id: "draft-created" } }; },
        async review(id, payload) { calls.review.push([id, payload]); return { draft: { draft_id: id, revision: 3 } }; },
        async promote(id, payload) { calls.promote.push([id, payload]); return { draft: { draft_id: id, revision: 4 } }; },
        ...overrides.clients,
      },
      async reloadLatest(message) { calls.reload.push(message); return { draft: { draft_id: "draft-latest" } }; },
      refreshRisk() { calls.refresh += 1; },
      ...overrides,
    },
  };
}


test("remediation create sends only workspace opportunity and server base version", async () => {
  const { calls, options } = harness();
  const result = await orchestrateRemediationMutation({
    ...options,
    kind: "create",
    workspaceId: "ws-a",
    opportunity: { id: "opp-cache", baseVersion: "cache-policy-v7", secret: "drop" },
  });

  assert.deepEqual(calls.create, [{
    workspaceId: "ws-a",
    sourceOpportunityId: "opp-cache",
    baseVersion: "cache-policy-v7",
  }]);
  assert.equal(result.status, "succeeded");
  assert.equal(calls.refresh, 1);
  assert.deepEqual(calls.reload, []);
});


test("remediation review and promote use the current revision and bounded reason", async () => {
  const reviewHarness = harness();
  await orchestrateRemediationMutation({
    ...reviewHarness.options,
    kind: "review",
    draft: { id: "draft-a", revision: 7 },
    reason: `  ${"复核".repeat(200)}  `,
  });
  assert.deepEqual(reviewHarness.calls.review, [["draft-a", {
    baseRevision: 7,
    reason: "复核".repeat(150),
  }]]);
  assert.equal(reviewHarness.calls.refresh, 1);

  const promoteHarness = harness();
  await orchestrateRemediationMutation({
    ...promoteHarness.options,
    kind: "promote",
    draft: { id: "draft-a", revision: 8, executionCapability: "typed_action_available" },
    reason: "  形成审批草案  ",
  });
  assert.deepEqual(promoteHarness.calls.promote, [["draft-a", {
    baseRevision: 8,
    reason: "形成审批草案",
  }]]);
  assert.equal(promoteHarness.calls.refresh, 1);
});


test("advisory remediation never invokes the promotion client", async () => {
  const { calls, options } = harness();
  const result = await orchestrateRemediationMutation({
    ...options,
    kind: "promote",
    draft: { id: "draft-a", revision: 8, executionCapability: "advisory_only" },
  });

  assert.equal(result.status, "failed");
  assert.deepEqual(calls.promote, []);
  assert.equal(calls.refresh, 0);
});


test("remediation conflict reloads latest and never refreshes risk", async () => {
  const conflict = Object.assign(new Error("private conflict detail"), { status: 409 });
  const { calls, options } = harness({
    clients: { async review() { throw conflict; } },
  });
  const result = await orchestrateRemediationMutation({
    ...options,
    kind: "review",
    draft: { id: "draft-a", revision: 2 },
  });

  assert.deepEqual(result, {
    status: "conflict",
    error: REMEDIATION_CONFLICT_MESSAGE,
    latest: { draft: { draft_id: "draft-latest" } },
  });
  assert.deepEqual(calls.reload, [REMEDIATION_CONFLICT_MESSAGE]);
  assert.equal(calls.refresh, 0);
});


test("remediation failure returns a bounded public error and never refreshes", async () => {
  const { calls, options } = harness({
    clients: { async promote() { throw new Error("private backend stack and secret"); } },
  });
  const result = await orchestrateRemediationMutation({
    ...options,
    kind: "promote",
    draft: { id: "draft-a", revision: 2, executionCapability: "typed_action_available" },
  });

  assert.equal(result.status, "failed");
  assert.equal(result.error, "审批动作草案创建失败");
  assert.equal(JSON.stringify(result).includes("private backend"), false);
  assert.equal(calls.refresh, 0);
  assert.deepEqual(calls.reload, []);
});
