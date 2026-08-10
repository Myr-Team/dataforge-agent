import assert from "node:assert/strict";
import test from "node:test";

import { prettyTraceJson, safeTraceValue, traceExplorerRows } from "./runTraceExplorer.js";

test("trace explorer preserves useful fields and recognizes an external agent", () => {
  const rows = traceExplorerRows([{
    index: 2,
    event: "model_response",
    status: "completed",
    detail: { agent_reference: { name: "customer-agent", type: "agent_reference" }, model: "deepseek-chat" },
  }]);

  assert.equal(rows[0].index, 3);
  assert.equal(rows[0].agent, "customer-agent");
  assert.equal(rows[0].external, true);
  assert.match(prettyTraceJson(rows[0].payload), /deepseek-chat/);
});

test("trace explorer applies a second client-side secret redaction boundary", () => {
  const safe = safeTraceValue({ api_key: "marker", nested: { Authorization: "Bearer secret", value: 3 } });

  assert.equal(safe.api_key, "[redacted]");
  assert.equal(safe.nested.Authorization, "[redacted]");
  assert.equal(JSON.stringify(safe).includes("secret-marker"), false);
});
