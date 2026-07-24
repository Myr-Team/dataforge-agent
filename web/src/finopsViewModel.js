const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });

export const FINOPS_TABS = [
  { id: "overview", label: "运营总览" },
  { id: "cost", label: "成本与预算" },
  { id: "roi", label: "效能与 ROI" },
  { id: "risk", label: "风险与优化" },
];

function hasNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatFinOpsNumber(value, fallback = "未记录") {
  return hasNumber(value) ? numberFormat.format(value) : fallback;
}

export function formatFinOpsCost(value, status = "") {
  if (!hasNumber(value)) return status === "unavailable" ? "不可用" : "未记录";
  const digits = value >= 1 ? 2 : value >= 0.01 ? 4 : 6;
  return `$${value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "")}`;
}

export function formatFinOpsDuration(value) {
  if (!hasNumber(value)) return "未记录";
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2).replace(/0+$/, "").replace(/\.$/, "")} s`;
  return `${Math.round(value)} ms`;
}

export function formatFinOpsPercent(value, fallback = "未记录") {
  return hasNumber(value) ? `${value.toFixed(1).replace(/\.0$/, "")}%` : fallback;
}

export function formatRelativeUpdateTime(value, now = Date.now()) {
  if (!value) return "数据更新中";
  const generatedAt = Date.parse(value);
  if (!Number.isFinite(generatedAt)) return "数据更新中";
  const ageMs = Math.max(0, now - generatedAt);
  if (ageMs < 60_000) return "刚刚更新";
  return `${Math.max(1, Math.floor(ageMs / 60_000))} 分钟前更新`;
}

export function finopsBootstrapViewData(payload = {}) {
  return {
    overview: payload.overview || {},
    trends: payload.trend || {},
    department: payload.departments || {},
    anomalies: payload.anomalies || { items: [], count: 0 },
    insights: payload.insights || { finops: null, roi: null },
    filterOptions: {
      filters: payload.filters || {
        departments: [],
        workspaces: [],
        actors: [],
        agents: [],
        models: [],
      },
    },
  };
}

export function finopsMetricCards(payload = {}) {
  const metrics = payload?.metrics || {};
  const cost = metrics.estimated_cost || {};
  const budget = metrics.budget || {};
  const latency = metrics.latency || {};
  const coverage = metrics.apim_coverage_pct;
  return [
    {
      id: "cost",
      label: "估算成本",
      value: formatFinOpsCost(cost.amount, cost.status),
      meta: cost.status === "partial" ? `${cost.unpriced_requests || 0} 次未计价` : "USD · 非账单",
      tone: cost.status === "partial" || cost.status === "unavailable" ? "warning" : "neutral",
    },
    {
      id: "budget",
      label: "预算使用",
      value: budget.status === "unavailable"
        ? "未配置"
        : formatFinOpsPercent(budget.usage_pct),
      meta: hasNumber(budget.amount)
        ? `${formatFinOpsCost(budget.used_amount, budget.status)} / ${formatFinOpsCost(budget.amount, "estimated")}`
        : "等待预算策略",
      tone: hasNumber(budget.usage_pct) && budget.usage_pct >= 100
        ? "critical"
        : hasNumber(budget.usage_pct) && budget.usage_pct >= 80
          ? "warning"
          : "neutral",
    },
    {
      id: "requests",
      label: "调用次数",
      value: formatFinOpsNumber(metrics.requests, "0"),
      meta: "授权范围内已观测调用",
      tone: "neutral",
    },
    {
      id: "success",
      label: "成功率",
      value: formatFinOpsPercent(metrics.success_rate_pct),
      meta: "成功调用 / 已观测调用",
      tone: hasNumber(metrics.success_rate_pct) && metrics.success_rate_pct < 95 ? "warning" : "neutral",
    },
    {
      id: "p95",
      label: "P95 延迟",
      value: formatFinOpsDuration(latency.p95_ms),
      meta: hasNumber(latency.p95_ms) && latency.p95_ms > 2000 ? "超过默认阈值" : "请求延迟",
      tone: hasNumber(latency.p95_ms) && latency.p95_ms > 2000 ? "warning" : "neutral",
    },
    {
      id: "coverage",
      label: "APIM 覆盖率",
      value: formatFinOpsPercent(coverage),
      meta: "APIM governed / observed",
      tone: hasNumber(coverage) && coverage < 95 ? "warning" : "neutral",
    },
  ];
}

export function finopsTrendViewModel(payload = {}) {
  return (Array.isArray(payload?.items) ? payload.items : []).map((item) => ({
    bucket: item.bucket,
    label: String(item.bucket || "").replace("T00:00:00Z", "").replace("T", " ").replace(":00:00Z", ":00"),
    requests: hasNumber(item.requests) ? item.requests : 0,
    cost: item.estimated_cost,
    total: item?.tokens?.total,
    series: {
      input: item?.tokens?.input ?? null,
      output: item?.tokens?.output ?? null,
      cached: item?.tokens?.cached_input ?? null,
      reasoning: item?.tokens?.reasoning ?? null,
    },
    status: item.data_status || "unavailable",
  }));
}

export function finopsRequestViewModel(item = {}) {
  const cache = {
    hit: "命中",
    miss: "未命中",
    bypassed: "绕过",
    unavailable: "未记录",
  }[item?.cache?.state] || "未记录";
  return {
    requestRef: item.request_ref || "",
    occurredAt: item.occurred_at || "",
    workspaceId: item.workspace_id || "未记录",
    departmentId: item.department_id || "未归属",
    actorRef: item.actor_ref || "未记录",
    runId: item.run_id || "未记录",
    agentId: item.agent_id || "未记录",
    model: item.deployment || item.model || "未记录",
    route: item.route || "未记录",
    status: item.status || "unknown",
    correlation: item.apim_correlation_id || item.correlation_ref || "未记录",
    tokens: item?.tokens?.total ?? null,
    tokenDetail: item.tokens || {},
    cache,
    cost: formatFinOpsCost(item?.estimated_cost?.amount, item?.estimated_cost?.status),
    costStatus: item?.estimated_cost?.status || "unavailable",
    priceRevision: item?.estimated_cost?.price_card_revision || "未记录",
    latency: formatFinOpsDuration(item.latency_ms),
    errorCategory: item.error_category || "无",
    gatewayCoverage: item.gateway_coverage || "unknown",
    evidenceState: item.evidence_state || "unavailable",
  };
}

export function finopsBreakdownRows(payload = {}) {
  return (Array.isArray(payload?.items) ? payload.items : []).map((item) => ({
    key: item.key || "未记录",
    requests: item.requests || 0,
    tokens: item.tokens,
    cost: item.estimated_cost,
    errorRate: item.error_rate_pct,
    p95: item.p95_latency_ms,
    status: item.data_status || "unavailable",
  }));
}
