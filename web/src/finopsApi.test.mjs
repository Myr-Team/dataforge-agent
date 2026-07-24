import assert from "node:assert/strict";
import test from "node:test";

import { buildFinOpsQuery, loadFinOpsBootstrap } from "./api.js";


test("buildFinOpsQuery emits only supported non-empty filters", () => {
  const query = buildFinOpsQuery({
    from: "2026-07-01T00:00:00Z",
    to: "2026-07-24T00:00:00Z",
    workspaceId: "ws-a",
    departmentId: "",
    agentId: "df-coordinator",
    actorRef: null,
    model: "gpt-5-mini",
    ignored: "must-not-pass",
  });
  const params = new URLSearchParams(query);

  assert.equal(params.get("workspace_id"), "ws-a");
  assert.equal(params.get("agent_id"), "df-coordinator");
  assert.equal(params.get("model"), "gpt-5-mini");
  assert.equal(params.has("department_id"), false);
  assert.equal(params.has("ignored"), false);
});


test("loadFinOpsBootstrap calls the bounded bootstrap endpoint", async () => {
  const originalFetch = globalThis.fetch;
  let requestedUrl = "";
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return {
      ok: true,
      json: async () => ({ overview: { metrics: { requests: 1 } } }),
    };
  };

  try {
    await loadFinOpsBootstrap({ workspaceId: "ws-a", from: "2026-07-01T00:00:00Z" });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.match(requestedUrl, /^\/api\/finops\/bootstrap\?/);
  assert.match(requestedUrl, /workspace_id=ws-a/);
});
