import assert from "node:assert/strict";
import test from "node:test";

import {
  FINOPS_TABS,
  finopsBootstrapViewData,
  finopsMetricCards,
  finopsRequestViewModel,
  finopsTrendViewModel,
  finopsBudgetView,
  finopsDoughnutSegments,
  finopsRoiEconomicsView,
  finopsOpportunityRows,
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
    "tokens",
    "success",
    "p95",
    "cache",
    "coverage",
  ]);
  assert.equal(cards.find((item) => item.id === "cost").value, "不可用");
  assert.equal(cards.find((item) => item.id === "budget").value, "未配置");
  assert.equal(cards.find((item) => item.id === "p95").value, "未记录");
  assert.equal(cards.find((item) => item.id === "cache").value, "未记录");
  assert.equal(cards.find((item) => item.id === "coverage").tone, "warning");
  assert.equal(cards.find((item) => item.id === "cost").metric.kind, "cost");
  assert.equal(cards.find((item) => item.id === "success").metric.kind, "quality");
  assert.equal(cards.find((item) => item.id === "cache").metric.kind, "cache");
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


test("budget and allocation view models preserve estimated and unavailable states", () => {
  const budget = finopsBudgetView({
    items: [{
      name: "月度预算",
      amount: 100,
      progress: {
        spent_amount: 40,
        usage_pct: 40,
        forecast_amount: 80,
        forecast_status: "estimated",
        confidence: "partial",
        threshold_state: "normal",
      },
    }],
  });
  const doughnut = finopsDoughnutSegments([
    { key: "gpt-5", cost: 8 },
    { key: "gpt-4.1", cost: 2 },
  ], "cost");

  assert.equal(budget.name, "月度预算");
  assert.equal(budget.forecastLabel, "$80");
  assert.equal(budget.status, "estimated");
  assert.deepEqual(doughnut.map((item) => item.sharePct), [80, 20]);
  assert.deepEqual(finopsDoughnutSegments([{ key: "unknown", cost: null }], "cost"), []);
});


test("ROI economics view keeps verified ROI separate from estimated scenarios", () => {
  const view = finopsRoiEconomicsView({
    funnel: [
      { id: "investment", label: "投入", value: 12, unit: "USD", status: "estimated" },
      { id: "outcome", label: "业务结果", value: 1, unit: "项已验证结果", status: "verified" },
    ],
    unit_economics: {
      cost_per_analysis: { label: "每次分析成本", value: 3, currency: "USD", status: "estimated" },
    },
    verified_roi: { status: "verified", value: 1.5, currency: "USD" },
    scenarios: [{ scenario_id: "roi_scenario_a", status: "estimated", title: "扩容情景" }],
    evidence_gaps: [],
  });

  assert.equal(view.verifiedRoiLabel, "150%");
  assert.equal(view.scenarios[0].status, "estimated");
  assert.equal(finopsRoiEconomicsView({}).verifiedRoiLabel, "证据不足");
});


test("opportunity rows keep observing and estimated savings states explicit", () => {
  const rows = finopsOpportunityRows({
    items: [
      {
        opportunity_id: "opp-a",
        title: "缓存效率优化",
        impact: "medium",
        confidence: "low",
        effort: "medium",
        queue_state: "observing",
        estimated_savings: null,
        evidence_state: "partial",
      },
    ],
  });

  assert.equal(rows[0].stateLabel, "观察中");
  assert.equal(rows[0].savingsLabel, "暂不可估算");
  assert.equal(rows[0].actionLabel, "建议 · 需人工审批");
});
