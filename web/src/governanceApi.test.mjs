import assert from "node:assert/strict";
import test from "node:test";

import {
  loadWorkspaceChargeback,
  loadWorkspaceGovernanceAuditEvents,
  loadWorkspaceInvitationHistory,
  loadWorkspaceRoi,
  loadWorkspaceTraceStatus,
} from "./api.js";

function withFetch(testBody) {
  return async () => {
    const original = globalThis.fetch;
    const calls = [];
    globalThis.fetch = async (url) => {
      calls.push(String(url));
      return { ok: true, json: async () => ({ ok: true }) };
    };
    try {
      await testBody(calls);
    } finally {
      globalThis.fetch = original;
    }
  };
}

test("governance endpoints encode workspace and exact bounded query parameters", withFetch(async (calls) => {
  await loadWorkspaceTraceStatus("ws/a", { runId: "run/1", correlationId: "corr-1" });
  await loadWorkspaceRoi("ws/a", { from: "2026-07-01T00:00:00Z", to: "2026-08-01T00:00:00Z" });
  await loadWorkspaceChargeback("ws/a", { from: "2026-07-01T00:00:00Z", to: "2026-08-01T00:00:00Z" });
  await loadWorkspaceGovernanceAuditEvents("ws/a", { limit: 25, cursor: "cursor/value" });
  await loadWorkspaceInvitationHistory("ws/a");

  assert.equal(calls[0], "/api/workspaces/ws%2Fa/governance/trace-status?run_id=run%2F1&correlation_id=corr-1");
  assert.equal(calls[1], "/api/workspaces/ws%2Fa/governance/roi?from=2026-07-01T00%3A00%3A00Z&to=2026-08-01T00%3A00%3A00Z");
  assert.equal(calls[2], "/api/workspaces/ws%2Fa/governance/chargeback?from=2026-07-01T00%3A00%3A00Z&to=2026-08-01T00%3A00%3A00Z");
  assert.equal(calls[3], "/api/workspaces/ws%2Fa/governance/audit-events?limit=25&cursor=cursor%2Fvalue");
  assert.equal(calls[4], "/api/workspaces/ws%2Fa/governance/invitations");
}));

test("audit pagination clamps the page size to the backend contract", withFetch(async (calls) => {
  await loadWorkspaceGovernanceAuditEvents("ws", { limit: 1000 });
  assert.equal(calls[0], "/api/workspaces/ws/governance/audit-events?limit=100");
}));
