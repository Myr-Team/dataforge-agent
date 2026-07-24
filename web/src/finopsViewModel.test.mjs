import assert from "node:assert/strict";
import test from "node:test";

import {
  FINOPS_TABS,
  finopsBootstrapViewData,
  finopsMetricCards,
  finopsRequestViewModel,
  finopsTrendViewModel,
  formatRelativeUpdateTime,
} from "./finopsViewModel.js";


test("finops metric cards preserve unavailable and partial evidence", () => {
  const cards = finopsMetricCards({
    data_status: "partial",
    metrics: {
      requests: 12,
      tokens: { total: null, known_requests: 0, unknown_requests: 12 },
      estimated_cost: { amount: null, status: "unavailable", priced_requests: 0, unpriced_requests: 12 },
      budget: { amount: null, used_amount: null, usage_pct: null, status: "unavailable" },
      latency: { p50_ms: 900, p95_ms: null, known_requests: 1 },
      error_rate_pct: 8.33,
      success_rate_pct: 91.67,
      cache_hit_rate_pct: null,
      apim_coverage_pct: 91.67,
    },
  });

  assert.deepEqual(cards.map((item) => item.id), [
    "cost",
    "budget",
    "requests",
    "success",
    "p95",
    "coverage",
  ]);
  assert.equal(cards.find((item) => item.id === "cost").value, "不可用");
  assert.equal(cards.find((item) => item.id === "budget").value, "未配置");
  assert.equal(cards.find((item) => item.id === "p95").value, "未记录");
  assert.equal(cards.find((item) => item.id === "coverage").tone, "warning");
});


test("FinOps portal exposes four operations pages and natural update copy", () => {
  assert.deepEqual(FINOPS_TABS.map((item) => item.id), ["overview", "cost", "roi", "risk"]);
  assert.deepEqual(FINOPS_TABS.map((item) => item.label), ["运营总览", "成本与预算", "效能与 ROI", "风险与优化"]);
  assert.equal(
    formatRelativeUpdateTime("2026-07-24T02:00:00Z", Date.parse("2026-07-24T02:01:20Z")),
    "1 分钟前更新",
  );
  assert.equal(formatRelativeUpdateTime("", Date.now()), "数据更新中");
});


test("bootstrap payload maps directly to the operations overview", () => {
  const data = finopsBootstrapViewData({
    overview: { metrics: { requests: 8 } },
    trend: { bucket: "day", items: [{ bucket: "2026-07-24T00:00:00Z" }] },
    departments: { items: [{ key: "Commerce", requests: 8 }] },
    anomalies: { items: [{ policy_type: "apim_coverage", title: "网关治理覆盖不足" }] },
    insights: { finops: null, roi: null },
    filters: { departments: ["Commerce"], agents: ["df-coordinator"], models: ["gpt-5-mini"] },
  });

  assert.equal(data.overview.metrics.requests, 8);
  assert.equal(data.trends.bucket, "day");
  assert.equal(data.department.items[0].key, "Commerce");
  assert.equal(data.anomalies.items[0].title, "网关治理覆盖不足");
  assert.deepEqual(data.filterOptions.filters.models, ["gpt-5-mini"]);
});


test("finops trend view model keeps token categories separate", () => {
  const rows = finopsTrendViewModel({
    items: [
      {
        bucket: "2026-07-24T00:00:00Z",
        requests: 4,
        tokens: { input: 80, output: 20, cached_input: 15, reasoning: null, total: 100 },
        estimated_cost: 0.0042,
        data_status: "partial",
      },
    ],
  });

  assert.deepEqual(rows[0].series, {
    input: 80,
    output: 20,
    cached: 15,
    reasoning: null,
  });
  assert.equal(rows[0].status, "partial");
});


test("request detail view model prefers friendly evidence and keeps technical refs collapsed", () => {
  const request = finopsRequestViewModel({
    display: {
      name: "Commerce · 分析运行 · 7月24日 10:42",
      operation: "分析运行",
      occurred_at: "2026-07-24T02:42:00Z",
    },
    status: "succeeded",
    metrics: {
      tokens: { input: 10, output: 2, total: 12 },
      cache: { state: "hit" },
      estimated_cost: { amount: 0.0012, status: "estimated", currency: "USD" },
      latency_ms: 1234,
      gateway_coverage: "apim_governed",
    },
    business_request: { text: "分析本月销售异常", status: "recorded" },
    business_response: { text: "已定位主要变化来自华东区域。", status: "recorded" },
    technical_refs: {
      request_ref: "req_safe",
      run_id: "run-safe",
      apim_correlation_id: "4f8b0f37b5824af5a2ac7ed9129ee70b",
    },
    links: { foundry_trace: "https://ai.azure.com/trace/safe" },
  });

  assert.equal(request.title, "Commerce · 分析运行 · 7月24日 10:42");
  assert.equal(request.businessRequest.text, "分析本月销售异常");
  assert.equal(request.businessResponse.text, "已定位主要变化来自华东区域。");
  assert.equal(request.cost, "$0.0012");
  assert.equal(request.cache, "命中");
  assert.equal(request.technical.expanded, false);
  assert.equal(request.technical.items[0].value, "req_safe");
  assert.equal(request.links.foundryTrace, "https://ai.azure.com/trace/safe");
  assert.equal(Object.hasOwn(request, "providerResponseId"), false);
});


test("request detail view model marks missing business evidence without fake trace actions", () => {
  const request = finopsRequestViewModel({
    display: { name: "工作区 · 操作记录 · 7月24日 10:42" },
    business_request: { text: null, status: "unavailable" },
    business_response: { text: null, status: "unavailable" },
    links: {},
  });

  assert.equal(request.businessRequest.text, "未记录");
  assert.equal(request.businessResponse.text, "未记录");
  assert.equal(request.links.foundryTrace, "");
});
