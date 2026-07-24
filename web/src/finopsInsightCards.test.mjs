import assert from "node:assert/strict";
import test from "node:test";

import { finopsInsightViewModel } from "./finopsViewModel.js";


test("ready insight shows findings evidence state and separate analysis time", () => {
  const view = finopsInsightViewModel({
    status: "ready",
    title: "成本变化",
    summary: "主分析流程是当前主要成本驱动。",
    findings: [
      {
        kind: "cost_driver",
        statement: "当前结论具备请求证据。",
        evidence_refs: ["req_safe"],
      },
    ],
    evidence_state: "estimated",
    confidence: 0.8,
    generated_at: "2026-07-24T02:00:00Z",
    draft_suggestions: [],
  });

  assert.equal(view.state, "ready");
  assert.equal(view.title, "成本变化");
  assert.equal(view.findings[0].statement, "当前结论具备请求证据。");
  assert.equal(view.evidenceState, "estimated");
  assert.equal(view.generatedAt, "2026-07-24T02:00:00Z");
});


test("insufficient stale and failed insights remain truthful", () => {
  const insufficient = finopsInsightViewModel({
    status: "insufficient_data",
    evidence_gaps: ["已验证结果事件不足"],
  });
  const stale = finopsInsightViewModel({
    status: "stale",
    title: "上一版成本结论",
    summary: "保留已通过校验的上一版内容。",
    findings: [
      {
        kind: "cost_driver",
        statement: "上一版结论。",
        evidence_refs: ["req_safe"],
      },
    ],
  });
  const failed = finopsInsightViewModel({
    status: "failed",
    title: "分析暂不可用",
    summary: "结构化分析未通过校验。",
  });

  assert.deepEqual(insufficient.gaps, ["已验证结果事件不足"]);
  assert.equal(insufficient.summary, "证据不足，暂不生成推测性结论。");
  assert.equal(stale.stateLabel, "分析结果已过期");
  assert.equal(stale.summary, "保留已通过校验的上一版内容。");
  assert.equal(failed.summary, "分析暂不可用");
  assert.deepEqual(failed.findings, []);
});


test("agent draft suggestions stay typed and never expose approval transitions", () => {
  const view = finopsInsightViewModel({
    status: "ready",
    title: "缓存建议",
    summary: "可创建草案后由人工审批。",
    findings: [
      {
        kind: "optimization",
        statement: "评估缓存策略。",
        evidence_refs: ["req_safe"],
      },
    ],
    draft_suggestions: [
      {
        action_type: "cache_policy",
        reason: "重复分析请求适合评估缓存。",
        payload: {
          workspace_id: "ws-a",
          enabled: true,
          ttl_seconds: 300,
          base_version: "v1",
        },
      },
    ],
  });

  assert.equal(view.draftSuggestions[0].actionType, "cache_policy");
  assert.equal(Object.hasOwn(view.draftSuggestions[0], "approve"), false);
  assert.equal(Object.hasOwn(view.draftSuggestions[0], "execute"), false);
});
