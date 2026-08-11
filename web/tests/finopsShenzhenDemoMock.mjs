import { bootstrapPayload, installFinOpsDemoCompletenessApi } from "./finopsMockApi.mjs";

const NOW = "2026-08-11T12:00:00Z";
export const SHENZHEN_REFS = Object.freeze({
  request: "req_shenzhen_00000001",
  run: "synthetic-shenzhen-site-selection-0001",
  correlation: "corr_shenzhen_00000001",
  attempt: "attempt_shenzhen_00000001",
  result: "result-shenzhen-0000",
});

const shenzhenBootstrap = {
  ...bootstrapPayload,
  window: { from: "2026-07-12T12:00:00Z", to: NOW, timezone: "UTC" },
  coverage: { observed_requests: 2480, apim_governed_requests: 2349, apim_coverage_pct: 94.72 },
  overview: {
    ...bootstrapPayload.overview,
    metrics: {
      ...bootstrapPayload.overview.metrics,
      requests: 2480,
      estimated_cost: { amount: 206.4, priced_requests: 2320, unpriced_requests: 160, status: "partial" },
      budget: { amount: 5, used_amount: 206.4, usage_pct: 4128, status: "partial", source: "daily_cost_budget" },
    },
    trust: {
      ...bootstrapPayload.overview.trust,
      pricing: { priced_requests: 2320, unpriced_requests: 160, coverage_pct: 93.55, state: "partial" },
      tokens: { known_requests: 2480, unknown_requests: 0, coverage_pct: 100, state: "complete" },
      apim: { ...bootstrapPayload.overview.trust.apim, app_observed_requests: 2480, apim_governed_requests: 2349, coverage_pct: 94.72, state: "synthetic_demo" },
    },
  },
  anomalies: {
    count: 7,
    items: [
      { policy_type: "daily_cost_budget", title: "深圳选址日成本预算", severity: "warning", status: "open", observed_value: 206.4, threshold_value: 5, sample_count: 2480, observed_at: NOW, evidence_refs: [SHENZHEN_REFS.request], evidence_state: "synthetic_demo", provenance: "synthetic_demo" },
      { policy_type: "unpriced_requests", title: "深圳选址未计价请求", severity: "warning", status: "open", observed_value: 160, threshold_value: 0, sample_count: 2480, observed_at: NOW, evidence_refs: [SHENZHEN_REFS.request], evidence_state: "synthetic_demo", provenance: "synthetic_demo" },
      { policy_type: "cache_hit_rate", title: "深圳选址缓存效率", severity: "warning", status: "open", observed_value: 11.1, threshold_value: 20, sample_count: 2480, observed_at: NOW, evidence_refs: [SHENZHEN_REFS.request], evidence_state: "synthetic_demo", provenance: "synthetic_demo" },
    ],
  },
  departments: {
    items: [
      { key: "深圳选址", requests: 2480, tokens: 12648000, estimated_cost: 206.4, error_rate_pct: 0.4, success_rate_pct: 99.6, p95_latency_ms: 900, cache_hit_rate_pct: 11.1, data_status: "partial" },
    ],
  },
};

const shenzhenBreakdowns = {
  ...shenzhenBootstrap,
  items: [{ key: "demo-corpus", requests: 2480, tokens: 12648000, estimated_cost: 206.4, error_rate_pct: 0.4, success_rate_pct: 99.6, p95_latency_ms: 900, cache_hit_rate_pct: 11.1, data_status: "partial" }],
  count: 1,
};
const shenzhenAgents = {
  ...shenzhenBootstrap,
  agents: [
    { key: "深圳选址分析 Agent", requests: 1460, tokens: 7540000, estimated_cost: 121.1, error_rate_pct: 0.3, success_rate_pct: 99.7, p95_latency_ms: 880, cache_hit_rate_pct: 11.2, data_status: "estimated" },
    { key: "选址证据审阅 Agent", requests: 1020, tokens: 5108000, estimated_cost: 85.3, error_rate_pct: 0.5, success_rate_pct: 99.5, p95_latency_ms: 930, cache_hit_rate_pct: 11.0, data_status: "estimated" },
  ],
  models: [
    { key: "gpt-5.6-terra", requests: 2320, tokens: 12000000, estimated_cost: 206.4, error_rate_pct: 0.4, success_rate_pct: 99.6, p95_latency_ms: 900, cache_hit_rate_pct: 11.1, token_composition: { input: 2400000, cached_input: 120000, uncached_input: 2280000, output: 9600000, reasoning: 480000, known_requests: 2320, data_status: "available" }, data_status: "estimated" },
    { key: "deepseek-v4-flash（未计价）", requests: 160, tokens: 648000, estimated_cost: null, error_rate_pct: 0.6, success_rate_pct: 99.4, p95_latency_ms: 940, cache_hit_rate_pct: 11.0, token_composition: { input: 128000, cached_input: 0, uncached_input: 128000, output: 520000, reasoning: null, known_requests: 160, data_status: "partial" }, data_status: "unpriced" },
  ],
};

const roiDecision = {
  ...bootstrapPayload,
  currency: "USD",
  decision: { state: "scenario_positive_unverified", title: "深圳选址情景测算", summary: "合成演示数据，不构成生产已验证 ROI。", evidence_state: "estimated" },
  metrics: [
    { id: "monthly_benefit", label: "月度收益", value: 6240, unit: "USD", status: "estimated" },
    { id: "monthly_total_cost", label: "月度总成本", value: 1586.4, unit: "USD", status: "estimated" },
    { id: "monthly_net_benefit", label: "月度净收益", value: 4653.6, unit: "USD", status: "estimated" },
    { id: "roi_ratio", label: "ROI 比率", value: 2.933, unit: "ratio", status: "estimated" },
  ],
  value_bridge: { formula_revision: "dataforge-roi-v1", scenario_id: "roi_scenario_shenzhen", payback_months: 0.34, items: [] },
  evidence_maturity: { score_pct: 70, formula_revision: "roi-evidence-maturity-v1", stages: [] },
  verified_roi: { status: "unavailable", value: null, currency: "USD" },
  scenarios: [{
    scenario_id: "roi_scenario_shenzhen", status: "estimated", result: { monthly_benefit: 6240, monthly_total_cost: 1586.4, monthly_net_benefit: 4653.6, roi_ratio: 2.933, formula_revision: "dataforge-roi-v1" },
    demo_evidence: {
      provenance: "synthetic_demo", production_quality_claim: false, label: "演示验证结果 · 合成数据",
      measured: { paired_evaluations: 18, historical_hours: 17.8, assisted_hours: 8.1 },
      process: { analysis_tasks: 96, reports: 78, evidence_reviews: 18, reviewed_savings_hours: 174.6 },
      actors: { outcome_actor_ref: "synthetic_site_selection_outcome_reviewer", reviewer_actor_ref: "synthetic_site_selection_finance_reviewer" },
      window: { from: "2026-07-12T12:00:00Z", to: NOW, currency: "USD" },
      source_refs: { run_id: SHENZHEN_REFS.run, request_ref: SHENZHEN_REFS.request, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt },
    },
  }],
};

const riskOpportunity = {
  opportunity_id: "opp-shenzhen-unpriced", anomaly_id: "anom-shenzhen-unpriced", anomaly_status: "open", applicable_actions: ["acknowledge"], policy_type: "unpriced_requests", risk_domain: "cost", title: "深圳选址计价覆盖", recommendation: "补齐未计价适配器的官方价格映射。", impact: "medium", confidence: "high", effort: "medium", sample_count: 160, evidence_refs: [SHENZHEN_REFS.request], expected_impact: { status: "estimated", value: 0.00325, currency: "USD" }, base_version: "shenzhen-demo-v1",
};
const riskDecision = {
  ...shenzhenBootstrap,
  decision: { state: "prioritized", title: "深圳选址合成风险优先级", summary: "仅使用合成演示证据，不触发生产动作。", evidence_state: "synthetic_demo" },
  risk_domains: [{ id: "cost", count: 1 }],
  risk_matrix: [{ ...riskOpportunity, x_confidence: 3, y_impact: 2, x_confidence_state: "synthetic_demo", y_impact_state: "synthetic_demo", bubble_size: 160 }],
  priorities: [riskOpportunity],
  optimization_portfolio: [{ ...riskOpportunity, x_effort: 2, y_value_impact: 2, x_effort_state: "synthetic_demo", y_value_impact_state: "synthetic_demo", bubble_size: 160 }],
  portfolio_metadata: { x_axis: "effort", y_axis: "value_impact", size: "sample_count", color: "risk_domain" },
  selected_evidence_summaries: [{ request_ref: SHENZHEN_REFS.request, request_name: "深圳选址 · 合成请求", operation: "深圳选址评估", signal: { metric: "pricing_status", value: "unpriced", unit: "status" }, cache_state: "miss", status: "succeeded", technical_refs: { request_ref: SHENZHEN_REFS.request, run_id: SHENZHEN_REFS.run, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt }, visible_answer_summary: "存在 160 条合成未计价请求。" }],
  insight: { title: "深圳选址 AI 解读", summary: "合成证据指向未计价适配器与缓存链路。", status: "synthetic_demo", evidence_refs: [SHENZHEN_REFS.request] },
  drafts: [], governance_capability: { read_enabled: true, draft_enabled: true, actions_enabled: false, typed_executors: [] },
};

const shenzhenRun = {
  run_id: SHENZHEN_REFS.run,
  title: "深圳选址 · 合成运行",
  summary: "深圳选址的合成证据链路，供演示验证使用。",
  status: "done",
  verdict: "feasible",
  completed_at: NOW,
  time: NOW,
  step_count: 1,
};
const shenzhenTraceDetail = {
  model_id: "gpt-5.6-terra",
  provider_type: "azure_foundry",
  provenance: "synthetic_demo",
  request_ref: SHENZHEN_REFS.request,
  run_id: SHENZHEN_REFS.run,
  correlation_ref: SHENZHEN_REFS.correlation,
  attempt_ref: SHENZHEN_REFS.attempt,
  result_id: SHENZHEN_REFS.result,
  route_evidence: "synthetic",
  gateway_coverage: "app_observed",
  provider_cache: { state: "miss", hit_tokens: 0, miss_tokens: 100, evidence_state: "synthetic" },
  result_cache: { state: "miss", eligible: true, source_result_version: null },
  cost_estimate: { amount: 0.00325, currency: "USD", official_price_key: "azure-openai:gpt-5.6-terra:global-standard:global", price_card_revision: "2026-07-27" },
};
const shenzhenTraceHitDetail = {
  ...shenzhenTraceDetail,
  request_ref: "req_shenzhen_00000002",
  result_cache: { state: "hit", eligible: true, source_result_version: SHENZHEN_REFS.result },
  provider_cache: { state: "hit", hit_tokens: 80, miss_tokens: 20, evidence_state: "synthetic" },
  cost_estimate: { ...shenzhenTraceDetail.cost_estimate, amount: 0 },
};
const shenzhenTrace = [{
  index: 0,
  event: "model_response",
  agent: "深圳选址分析 Agent",
  role: "选址证据汇总",
  status: "completed",
  time: NOW,
  duration_ms: 900,
  source: "synthetic_demo",
  detail: shenzhenTraceDetail,
  data: shenzhenTraceDetail,
}, {
  index: 1,
  event: "model_response",
  agent: "深圳选址分析 Agent",
  role: "结果缓存复用",
  status: "completed",
  time: NOW,
  duration_ms: 120,
  source: "synthetic_demo",
  detail: shenzhenTraceHitDetail,
  data: shenzhenTraceHitDetail,
}];

function json(route, body) {
  return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
}

export async function installFinOpsShenzhenDemoApi(page, calls = []) {
  await installFinOpsDemoCompletenessApi(page, calls);
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    calls.push({ path, method: request.method(), body: request.postData() || "" });
    if (path === "/api/workspaces/demo-corpus/dashboard") return json(route, {
      workspace_id: "demo-corpus",
      workspace: { workspace_id: "demo-corpus", name: "深圳选址演示" },
      workspaces: [{ workspace_id: "demo-corpus", name: "深圳选址演示" }],
      runs: [shenzhenRun],
      conversations: [],
      health: { ok: true },
    });
    if (path === `/api/runs/${SHENZHEN_REFS.run}/summary`) return json(route, {
      run_id: SHENZHEN_REFS.run,
      status: "done",
      verdict: "feasible",
      confidence: "synthetic_demo",
      started_at: NOW,
      finished_at: NOW,
      duration_ms: 900,
      agent_count: 2,
      tokens: { total: 5100, prompt: 100, completion: 5000 },
      tool_calls: { total: 0, ok: 0, fail: 0 },
      audit: { status: "synthetic_demo" },
      evidence: { source: "synthetic_demo", tokens: "run.models[].usage" },
    });
    if (path === "/api/finops/bootstrap") return json(route, shenzhenBootstrap);
    if (path === "/api/finops/breakdowns") return json(route, shenzhenBreakdowns);
    if (path === "/api/finops/agents") return json(route, shenzhenAgents);
    if (path === "/api/finops/roi/decision") return json(route, roiDecision);
    if (path === "/api/finops/risk/decision") return json(route, riskDecision);
    if (path === "/api/finops/risk/scans") return json(route, { items: [], count: 0, workspace_id: "demo-corpus" });
    if (path === "/api/finops/requests") return json(route, { ...shenzhenBootstrap, count: 2480, next_cursor: null, items: [{ request_ref: SHENZHEN_REFS.request, occurred_at: NOW, workspace_id: "demo-corpus", status: "succeeded", model: "gpt-5.6-terra", estimated_cost: { amount: 0.00325, currency: "USD", status: "estimated" } }] });
    if (path === `/api/finops/requests/${SHENZHEN_REFS.request}`) return json(route, {
      ...shenzhenBootstrap, display: { name: "深圳选址 · 合成请求", operation: "深圳选址评估", occurred_at: NOW }, status: "succeeded",
      metrics: { latency_ms: 900, tokens: { input: 100, output: 5000, total: 5100 }, cache: { state: "miss", eligible: true }, result_cache: { state: "miss", eligible: true, source_result_version: null }, provider_cache: { state: "miss", hit_tokens: 0, miss_tokens: 100, evidence_state: "synthetic" }, estimated_cost: { amount: 0.00325, currency: "USD", status: "estimated", official_price_key: "azure-openai:gpt-5.6-terra:global-standard:global", price_card_revision: "2026-07-27" }, gateway_coverage: "app_observed", evidence_state: "synthetic_demo", provenance: "synthetic_demo" },
      technical_refs: { request_ref: SHENZHEN_REFS.request, run_id: SHENZHEN_REFS.run, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt },
      timeline: [{ stage: "execution", label: "深圳选址合成模型尝试", status: "synthetic_demo" }],
    });
    if (path === "/api/finops/requests/req_shenzhen_00000002") return json(route, {
      ...shenzhenBootstrap, status: "succeeded", metrics: { result_cache: { state: "hit", eligible: true, source_result_version: SHENZHEN_REFS.result }, provider_cache: { state: "hit", hit_tokens: 80, miss_tokens: 20, evidence_state: "synthetic" }, estimated_cost: { status: "estimated", amount: 0, currency: "USD", official_price_key: "azure-openai:gpt-5.6-terra:global-standard:global", price_card_revision: "2026-07-27" }, provenance: "synthetic_demo" }, technical_refs: { request_ref: "req_shenzhen_00000002", run_id: SHENZHEN_REFS.run, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt, source_result_version: SHENZHEN_REFS.result },
    });
    if (path === "/api/runs/" + SHENZHEN_REFS.run + "/trace") return json(route, shenzhenTrace);
    if (path === "/api/finops/pricing/catalog") return json(route, { ...shenzhenBootstrap, count: 2, items: [{ provider: "azure_foundry", official_model: "gpt-5.6-terra", currency: "USD", input_per_million: 2.5, output_per_million: 15, revision: "2026-07-27" }, { provider: "deepseek", official_model: "deepseek-v4-flash", currency: "USD", input_per_million: 0.14, output_per_million: 0.28, revision: "2026-07-27" }] });
    if (path === "/api/finops/pricing/mappings") return json(route, { ...shenzhenBootstrap, count: 1, items: [{ deployment: "gpt-5.6-terra", official_price_key: "azure-openai:gpt-5.6-terra:global-standard:global", mapping_revision: 1 }] });
    if (path === "/api/finops/assistant/query") return json(route, { status: "ready", conversation_ref: "conversation-shenzhen", answer: "深圳选址 AI 解读仅引用合成证据。", sections: { conclusion: "深圳选址的未计价请求需要补齐价格映射。", basis: "2,480 条合成请求，月度 AI 成本 $206.40。", impact: "结果缓存展示 miss→hit，供应商缓存保持 synthetic 标识。", recommendation: "先查看同一请求、运行、关联和尝试引用。", caveat: "演示验证结果 · 合成数据，不构成生产观测。" }, evidence_state: "synthetic_demo", evidence_refs: [SHENZHEN_REFS.request], evidence_labels: ["深圳选址请求证据"], knowledge_citations: [], context: { request_ref: SHENZHEN_REFS.request, run_id: SHENZHEN_REFS.run, correlation_ref: SHENZHEN_REFS.correlation, attempt_ref: SHENZHEN_REFS.attempt, provenance: "synthetic_demo" } });
    return route.fallback();
  });
}
