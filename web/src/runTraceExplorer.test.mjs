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

test("trace explorer separates result cache from provider token cache evidence", () => {
  const [row] = traceExplorerRows([{
    index: 0,
    event: "model_response",
    agent: "df-feasibility-analyst",
    status: "completed",
    detail: {
      provider_type: "deepseek",
      model_id: "deepseek-v4-flash",
      gateway_coverage: "apim_governed",
      result_cache: {
        state: "miss",
        provider: "redis",
        eligible: true,
        reason: "eligible",
        policy_revision: 4,
      },
      provider_cache: {
        state: "partial_hit",
        hit_tokens: 800,
        miss_tokens: 200,
        hit_rate_pct: 80,
        evidence_state: "observed",
      },
    },
  }]);

  assert.deepEqual(row.cacheEvidence, {
    result: { state: "miss", label: "未命中", detail: "符合缓存条件 · 策略 v4" },
    provider: { state: "partial_hit", label: "部分命中", detail: "命中 800 · 未命中 200 · 80%" },
    gateway: { state: "apim_governed", label: "统一入口已治理" },
  });
  assert.equal(row.modelLabel, "deepseek-v4-flash · DeepSeek");
});

test("trace explorer keeps missing cache evidence explicit", () => {
  const [row] = traceExplorerRows([{ event: "model_response", detail: {} }]);

  assert.equal(row.cacheEvidence.result.label, "未记录");
  assert.equal(row.cacheEvidence.provider.label, "未记录");
  assert.equal(row.cacheEvidence.gateway.label, "来源未记录");
});
