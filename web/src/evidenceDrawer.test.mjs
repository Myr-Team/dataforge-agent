import assert from "node:assert/strict";
import test from "node:test";

import {
  evidenceRequestRef,
  finopsRequestViewModel,
} from "./finopsViewModel.js";


test("evidence selection prefers the clicked finding reference", () => {
  assert.equal(
    evidenceRequestRef({
      evidenceRefs: ["req_slow"],
      fallbackItems: [{ request_ref: "req_latest" }],
    }),
    "req_slow",
  );
  assert.equal(
    evidenceRequestRef({
      evidenceRefs: [],
      fallbackItems: [{ request_ref: "req_latest" }],
    }),
    "req_latest",
  );
});


test("evidence drawer model uses business-first section order", () => {
  const detail = finopsRequestViewModel({
    display: {
      name: "Commerce · 分析运行 · 7月24日 10:42",
      operation: "分析运行",
      occurred_at: "2026-07-24T02:42:00Z",
    },
    metrics: {
      latency_ms: 1300,
      tokens: { total: 12 },
      cache: { state: "miss" },
      estimated_cost: { amount: 0.001, status: "estimated" },
    },
    business_request: { text: "分析本月销售异常", status: "recorded" },
    business_response: { text: "已定位主要变化来自华东区域。", status: "recorded" },
    timeline: [
      { stage: "gateway", label: "APIM 网关", status: "observed" },
      { stage: "response", label: "完成返回", status: "succeeded" },
    ],
    technical_refs: { request_ref: "req_safe" },
  });

  assert.deepEqual(detail.sectionOrder, [
    "summary",
    "metrics",
    "business_request",
    "business_response",
    "timeline",
    "technical",
  ]);
  assert.equal(detail.title.includes("req_safe"), false);
  assert.equal(detail.timeline[0].label, "APIM 网关");
});
