import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CUSTOMER_INFRA_LABELS,
  FINOPS_REFRESH_MS,
  FINOPS_TABS,
  finopsBootstrapViewData,
  finopsMetricCards,
  finopsBreakdownRows,
  finopsRequestViewModel,
  finopsTrendViewModel,
  finopsBudgetView,
  finopsDoughnutSegments,
  finopsRoiEconomicsView,
  finopsOpportunityRows,
  finopsPolicyLabel,
  finopsBarPercent,
  niceFinOpsAxis,
  formatFinOpsAxisCost,
  formatRelativeUpdateTime,
  gatewayUnmatchedEvidence,
} from "./finopsViewModel.js";


test("model breakdown keeps cached, uncached and output token populations separate", () => {
  const [row] = finopsBreakdownRows({ items: [{
    key: "deepseek-v4-flash",
    requests: 2,
    tokens: 195,
    estimated_cost: 0.42,
    token_composition: {
      input: 160,
      cached_input: 70,
      uncached_input: 90,
      output: 35,
      reasoning: null,
      known_requests: 2,
      data_status: "available",
    },
  }] });

  assert.deepEqual(row.tokenComposition, {
    input: 160,
    cachedInput: 70,
    uncachedInput: 90,
    output: 35,
    reasoning: null,
    knownRequests: 2,
    status: "available",
  });
});


test("operations customer labels hide infrastructure product names", () => {
  const source = readFileSync(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8");
  for (const forbidden of ["APIM", "Foundry Trace", "Azure Monitor"]) {
    assert.equal(source.includes(forbidden), false);
  }
  assert.deepEqual(CUSTOMER_INFRA_LABELS, {
    reconciliation: "请求对账",
    gatewayCoverage: "统一入口覆盖率",
    gateway: "统一入口",
    gatewayCorrelation: "入口关联",
    trace: "运行追踪",
    monitor: "云端监控",
  });
});


test("operations refresh interval is ten minutes", () => {
  assert.equal(FINOPS_REFRESH_MS, 600_000);
});


test("operations rules use business labels instead of infrastructure keys", () => {
  assert.equal(finopsPolicyLabel("apim_coverage"), "统一入口覆盖");
  assert.equal(finopsPolicyLabel("cache_hit_rate"), "缓存命中率");
  assert.equal(finopsPolicyLabel("unknown"), "运营规则");
});


test("gateway unmatched evidence is scope-labelled and never invented", () => {
  assert.equal(gatewayUnmatchedEvidence({ apim: {} }), null);
  assert.equal(gatewayUnmatchedEvidence({ apim: { gateway_unmatched: null } }), null);

  const evidence = gatewayUnmatchedEvidence({
    apim: {
      apim_governed_requests: 7,
      gateway_unmatched: {
        scope: "unattributed",
        linked_requests: 7,
        unmatched_gateway_errors: { total: 5, client_error_4xx: 3, server_error_5xx: 2 },
        updated_at: "2026-07-24T03:00:00Z",
        data_source: "apim_gateway_logs",
        note: "范围说明",
      },
    },
  });
  assert.equal(evidence.scope, "unattributed");
  assert.equal(evidence.linkedRequests, 7);
  assert.equal(evidence.unmatchedTotal, 5);
  assert.equal(evidence.clientErrors, 3);
  assert.equal(evidence.serverErrors, 2);
  assert.equal(evidence.updatedAt, "2026-07-24T03:00:00Z");
});


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
    "requests",
    "tokens",
    "success",
    "p95",
    "cache",
    "cache_avoided_tokens",
    "cache_savings",
  ]);
  assert.equal(cards.find((item) => item.id === "cost").value, "未计价");
  assert.equal(cards.find((item) => item.id === "p95").value, "未记录");
  assert.equal(cards.find((item) => item.id === "cache").value, "未记录");
  assert.equal(cards.find((item) => item.id === "cost").metric.kind, "cost");
  assert.equal(cards.find((item) => item.id === "success").metric.kind, "quality");
  assert.equal(cards.find((item) => item.id === "cache").metric.kind, "cache");
  assert.equal(cards.find((item) => item.id === "cache_avoided_tokens").value, "未记录");
  assert.equal(cards.find((item) => item.id === "cache_savings").value, "未记录");
});


test("cache savings stay unavailable when traceable tokens and pricing revision are absent", () => {
  const cards = finopsMetricCards({
    data_status: "synthetic_demo",
    metrics: {
      requests: 2480,
      tokens: { total: 100, known_requests: 2480, unknown_requests: 0 },
      estimated_cost: { amount: 206.4, status: "partial", priced_requests: 2320, unpriced_requests: 160 },
      budget: { amount: 300, used_amount: 206.4, usage_pct: 68.8, status: "available" },
      latency: { p50_ms: 900, p95_ms: 1800, known_requests: 2480 },
      cache_hit_rate_pct: 60,
      cache: {
        eligible_requests: 2000,
        hit: 1200,
        miss: 800,
        bypassed: 480,
        unavailable: 0,
        avoided_tokens: null,
        estimated_savings: null,
        data_status: "unavailable",
        reason: "avoided_tokens_not_recorded",
      },
    },
  });

  const avoided = cards.find((item) => item.id === "cache_avoided_tokens");
  const savings = cards.find((item) => item.id === "cache_savings");
  assert.equal(avoided.value, "未记录");
  assert.equal(avoided.meta, "缺少可追溯的避免 Token 证据");
  assert.equal(avoided.metric.cache.reason, "avoided_tokens_not_recorded");
  assert.equal(savings.value, "未记录");
  assert.equal(savings.meta, "缺少 Token 与价目版本，无法估算");
  assert.equal(savings.metric.cache.reason, "avoided_tokens_not_recorded");
});


test("FinOps portal exposes four operations pages and natural update copy", () => {
  assert.deepEqual(FINOPS_TABS.map((item) => item.id), ["overview", "cost", "roi", "risk"]);
  assert.deepEqual(FINOPS_TABS.map((item) => item.label), ["运营总览", "成本分析", "效能与 ROI", "风险与优化"]);
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
    uncached: 65,
    unclassifiedInput: null,
    output: 20,
    cached: 15,
    reasoning: null,
  });
  assert.equal(rows[0].status, "partial");
});


test("token trend does not double-count cached input as an extra token pool", () => {
  const [classified, unclassified] = finopsTrendViewModel({ items: [
    { bucket: "2026-08-09T00:00:00Z", tokens: { input: 100, cached_input: 70, output: 20, total: 120 } },
    { bucket: "2026-08-10T00:00:00Z", tokens: { input: 80, cached_input: null, output: 10, total: 90 } },
  ] });

  assert.equal(classified.series.uncached + classified.series.cached + classified.series.output, classified.total);
  assert.equal(classified.series.unclassifiedInput, null);
  assert.equal(unclassified.series.uncached, null);
  assert.equal(unclassified.series.unclassifiedInput, 80);
});


test("finops trend view model preserves cache economics", () => {
  const rows = finopsTrendViewModel({
    items: [{
      bucket: "2026-07-24T00:00:00Z",
      cache: {
        eligible_requests: 5,
        hit: 3,
        miss: 2,
        bypassed: 1,
        unavailable: 0,
        avoided_tokens: 840,
        estimated_savings: 0.0097,
        data_status: "available",
      },
    }],
  });

  assert.deepEqual(rows[0].cache, {
    eligible: 5,
    hit: 3,
    miss: 2,
    bypassed: 1,
    unavailable: 0,
    avoidedTokens: 840,
    estimatedSavings: 0.0097,
    status: "available",
  });
});


test("finops chart axis is adaptive and bar proportions remain truthful", () => {
  const axis = niceFinOpsAxis([0.0021, 0.0097], 4);

  assert.equal(axis.ticks.at(-1), 0);
  assert.ok(axis.max >= 0.0097);
  assert.ok(axis.max < 0.02);
  assert.equal(finopsBarPercent(0, axis.max), 0);
  assert.ok(finopsBarPercent(0.0021, axis.max) < finopsBarPercent(0.0097, axis.max));
  assert.ok(finopsBarPercent(0.0021, axis.max) < 50);
});


test("cost axis keeps zero concise and all non-zero ticks monetary", () => {
  assert.deepEqual(
    [0, 0.5, 1, 1.5, 2].map(formatFinOpsAxisCost),
    ["$0", "$0.50", "$1.00", "$1.50", "$2.00"],
  );
  assert.equal(formatFinOpsAxisCost(0.005), "$0.005");
});


test("finops chart uses a close readable ceiling and preserves bucket progress", () => {
  const axis = niceFinOpsAxis([15, 23], 5);
  const rows = finopsTrendViewModel({
    items: [
      { bucket: "2026-08-09T00:00:00Z", estimated_cost: 15, bucket_status: "complete" },
      { bucket: "2026-08-10T00:00:00Z", estimated_cost: 0.2, bucket_status: "in_progress" },
    ],
  });

  assert.equal(axis.max, 25);
  assert.deepEqual(axis.ticks, [25, 20, 15, 10, 5, 0]);
  assert.equal(rows[0].bucketStatus, "complete");
  assert.equal(rows[1].bucketStatus, "in_progress");
  assert.equal(rows[1].dateLabel, "08-10");
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
  assert.equal(request.gatewayCoverage, "统一入口");
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
  assert.equal(budget.forecastLabel, "$80.00");
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
    scenarios: [{
      scenario_id: "roi_scenario_a",
      status: "estimated",
      title: "扩容情景",
      revision: 2,
      result: {
        monthly_benefit: 3000,
        monthly_total_cost: 800,
        monthly_net_benefit: 2200,
        roi_ratio: 2.75,
        payback_months: 2.222222,
        formula_revision: "dataforge-roi-v1",
      },
    }],
    evidence_gaps: [],
  });

  assert.equal(view.verifiedRoiLabel, "150%");
  assert.equal(view.scenarios[0].status, "estimated");
  assert.equal(view.scenarios[0].monthlyBenefitLabel, "$3,000.00");
  assert.equal(view.scenarios[0].roiLabel, "275%");
  assert.equal(view.scenarios[0].paybackLabel, "2.2 个月");
  assert.equal(view.scenarios[0].formulaRevision, "dataforge-roi-v1");
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
