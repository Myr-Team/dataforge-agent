import { FINOPS_REFRESH_MS } from "./finopsNavigation.js";

const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });

export { FINOPS_REFRESH_MS };

export const CUSTOMER_INFRA_LABELS = Object.freeze({
  reconciliation: "请求对账",
  gatewayCoverage: "统一入口覆盖率",
  gateway: "统一入口",
  gatewayCorrelation: "入口关联",
  trace: "运行追踪",
  monitor: "云端监控",
});

export const FINOPS_TABS = [
  { id: "overview", label: "运营总览" },
  { id: "cost", label: "成本分析" },
  { id: "roi", label: "效能与 ROI" },
  { id: "risk", label: "风险与优化" },
];

const FINOPS_POLICY_LABELS = Object.freeze({
  error_rate: "调用失败率",
  p95_latency: "P95 响应时延",
  daily_cost_budget: "日预算使用",
  token_spike: "Token 异常增长",
  apim_coverage: "统一入口覆盖",
  unpriced_requests: "未计价调用",
  cache_hit_rate: "缓存命中率",
});

export function finopsPolicyLabel(value) {
  const key = String(value || "").trim();
  return FINOPS_POLICY_LABELS[key] || "运营规则";
}

export function finopsGatewayCoverageLabel(value) {
  return {
    apim_governed: "统一入口",
    app_observed: "应用观测",
    unmanaged: "未经过统一入口",
    unknown: "未记录",
  }[String(value || "").trim()] || "未记录";
}

function hasNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function niceFinOpsAxis(values = [], tickCount = 4) {
  const count = Math.max(2, Math.floor(Number(tickCount) || 4));
  const observedMaximum = (Array.isArray(values) ? values : [])
    .filter((value) => hasNumber(value) && value >= 0)
    .reduce((maximum, value) => Math.max(maximum, value), 0);
  if (observedMaximum <= 0) {
    return {
      max: 1,
      ticks: Array.from({ length: count }, (_item, index) => (
        (count - 1 - index) / (count - 1)
      )),
    };
  }
  const roughStep = observedMaximum / (count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalized = roughStep / magnitude;
  const niceFactor = [1, 2, 2.5, 5, 10].find((candidate) => candidate >= normalized) || 10;
  const step = niceFactor * magnitude;
  const max = step * (count - 1);
  return {
    max,
    ticks: Array.from({ length: count }, (_item, index) => (
      step * (count - 1 - index)
    )),
  };
}

export function finopsBarPercent(value, axisMaximum) {
  const numericValue = Number(value);
  const numericMaximum = Number(axisMaximum);
  if (!Number.isFinite(numericValue) || numericValue <= 0) return 0;
  if (!Number.isFinite(numericMaximum) || numericMaximum <= 0) return 0;
  return Math.min(100, (numericValue / numericMaximum) * 100);
}

export function formatFinOpsNumber(value, fallback = "未记录") {
  return hasNumber(value) ? numberFormat.format(value) : fallback;
}

export function formatFinOpsCost(value, status = "") {
  if (!hasNumber(value)) return ["unavailable", "unpriced"].includes(status) ? "未计价" : "未记录";
  const digits = value >= 1 ? 2 : value >= 0.01 ? 4 : 6;
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  })}`;
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

export function gatewayUnmatchedEvidence(trust = {}) {
  const apim = (trust && typeof trust === "object" && trust.apim) || {};
  const gateway = apim.gateway_unmatched;
  if (!gateway || typeof gateway !== "object") return null;
  const errors = gateway.unmatched_gateway_errors || {};
  const linked = Number(gateway.linked_requests);
  return {
    scope: gateway.scope || "unattributed",
    linkedRequests: Number.isFinite(linked)
      ? linked
      : Number(apim.apim_governed_requests || 0),
    unmatchedTotal: Number(errors.total || 0),
    clientErrors: Number(errors.client_error_4xx || 0),
    serverErrors: Number(errors.server_error_5xx || 0),
    updatedAt: gateway.updated_at || null,
    dataSource: gateway.data_source || "",
    note: gateway.note || "",
  };
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
  const latency = metrics.latency || {};
  const tokens = metrics.tokens || {};
  const cache = metrics.cache || {};
  const dataStatus = payload?.data_status || "unavailable";
  return [
    {
      id: "cost",
      label: "估算成本",
      value: formatFinOpsCost(cost.amount, cost.status),
      meta: cost.status === "partial" ? `${cost.unpriced_requests || 0} 次未计价` : "USD · 非账单",
      tone: cost.status === "partial" || cost.status === "unavailable" ? "warning" : "neutral",
      metric: {
        id: "estimated_cost",
        label: "估算成本",
        value: cost.amount ?? null,
        unit: "USD",
        kind: "cost",
        amount: cost.amount ?? null,
        pricedRequests: cost.priced_requests ?? null,
        unpricedRequests: cost.unpriced_requests ?? null,
        priceRevision: cost.price_card_revision || "",
        dataStatus,
        evidenceState: cost.status || "unavailable",
      },
    },
    {
      id: "requests",
      label: "调用次数",
      value: formatFinOpsNumber(metrics.requests, "0"),
      meta: "授权范围内已观测调用",
      tone: "neutral",
      metric: {
        id: "requests",
        label: "调用次数",
        value: metrics.requests ?? null,
        unit: "次",
        kind: "quality",
        requests: metrics.requests ?? null,
        successRatePct: metrics.success_rate_pct ?? null,
        p50Ms: latency.p50_ms ?? null,
        p95Ms: latency.p95_ms ?? null,
        dataStatus,
        evidenceState: dataStatus === "complete" ? "observed" : dataStatus,
      },
    },
    {
      id: "tokens",
      label: "Token",
      value: formatFinOpsNumber(tokens.total),
      meta: tokens.unknown_requests
        ? `${tokens.unknown_requests} 次调用未记录`
        : "输入、输出、缓存与推理",
      tone: tokens.unknown_requests ? "warning" : "neutral",
      metric: {
        id: "tokens",
        label: "Token",
        value: tokens.total ?? null,
        unit: "Token",
        kind: "tokens",
        tokens: {
          input: tokens.input ?? null,
          output: tokens.output ?? null,
          cachedInput: tokens.cached_input ?? null,
          reasoning: tokens.reasoning ?? null,
          total: tokens.total ?? null,
        },
        dataStatus,
        evidenceState: tokens.known_requests ? (tokens.unknown_requests ? "partial" : "observed") : "unavailable",
      },
    },
    {
      id: "success",
      label: "成功率",
      value: formatFinOpsPercent(metrics.success_rate_pct),
      meta: hasNumber(metrics.error_rate_pct)
        ? `错误率 ${formatFinOpsPercent(metrics.error_rate_pct)}`
        : "成功调用 / 已观测调用",
      tone: hasNumber(metrics.success_rate_pct) && metrics.success_rate_pct < 95 ? "warning" : "neutral",
      metric: {
        id: "success_rate",
        label: "成功率",
        value: metrics.success_rate_pct ?? null,
        unit: "%",
        kind: "quality",
        requests: metrics.requests ?? null,
        successRatePct: metrics.success_rate_pct ?? null,
        p50Ms: latency.p50_ms ?? null,
        p95Ms: latency.p95_ms ?? null,
        dataStatus,
        evidenceState: dataStatus === "complete" ? "observed" : dataStatus,
      },
    },
    {
      id: "p95",
      label: "P95 延迟",
      value: formatFinOpsDuration(latency.p95_ms),
      meta: hasNumber(latency.p95_ms) && latency.p95_ms > 2000
        ? "超过默认阈值"
        : `P50 ${formatFinOpsDuration(latency.p50_ms)}`,
      tone: hasNumber(latency.p95_ms) && latency.p95_ms > 2000 ? "warning" : "neutral",
      metric: {
        id: "p95_latency",
        label: "P95 延迟",
        value: latency.p95_ms ?? null,
        unit: "ms",
        kind: "quality",
        requests: latency.known_requests ?? null,
        successRatePct: metrics.success_rate_pct ?? null,
        p50Ms: latency.p50_ms ?? null,
        p95Ms: latency.p95_ms ?? null,
        dataStatus,
        evidenceState: latency.known_requests ? (latency.known_requests < metrics.requests ? "partial" : "observed") : "unavailable",
      },
    },
    {
      id: "cache",
      label: "缓存命中率",
      value: formatFinOpsPercent(metrics.cache_hit_rate_pct),
      meta: hasNumber(cache.eligible_requests)
        ? `${formatFinOpsNumber(cache.eligible_requests, "0")} 次可缓存调用`
        : "缓存状态未记录",
      tone: hasNumber(metrics.cache_hit_rate_pct) && metrics.cache_hit_rate_pct < 60 ? "warning" : "neutral",
      metric: {
        id: "cache_hit_rate",
        label: "缓存命中率",
        value: metrics.cache_hit_rate_pct ?? null,
        unit: "%",
        kind: "cache",
        cache: {
          hit: cache.hit ?? null,
          miss: cache.miss ?? null,
          bypassed: cache.bypassed ?? null,
          unavailable: cache.unavailable ?? null,
          eligible: cache.eligible_requests ?? null,
          avoidedTokens: cache.avoided_tokens ?? null,
          estimatedSavings: cache.estimated_savings ?? null,
          status: cache.data_status || "unavailable",
        },
        dataStatus,
        evidenceState: cache.eligible_requests ? "observed" : "unavailable",
      },
    },
    {
      id: "cache_avoided_tokens",
      label: "缓存避免 Token",
      value: formatFinOpsNumber(cache.avoided_tokens),
      meta: cache.data_status === "partial" ? "部分命中缺少完整计量" : "来自已记录的结果复用",
      tone: cache.data_status === "partial" ? "warning" : "neutral",
      metric: {
        id: "cache_avoided_tokens",
        label: "缓存避免 Token",
        value: cache.avoided_tokens ?? null,
        unit: "Token",
        kind: "cache",
        cache: {
          hit: cache.hit ?? null,
          miss: cache.miss ?? null,
          bypassed: cache.bypassed ?? null,
          unavailable: cache.unavailable ?? null,
          eligible: cache.eligible_requests ?? null,
          avoidedTokens: cache.avoided_tokens ?? null,
          estimatedSavings: cache.estimated_savings ?? null,
          status: cache.data_status || "unavailable",
        },
        dataStatus,
        evidenceState: cache.avoided_tokens == null ? "unavailable" : (cache.data_status || "observed"),
      },
    },
    {
      id: "cache_savings",
      label: "缓存估算节省",
      value: formatFinOpsCost(cache.estimated_savings),
      meta: cache.data_status === "partial" ? "仅统计可可靠计价的命中" : "按当前价目映射估算",
      tone: cache.data_status === "partial" ? "warning" : "neutral",
      metric: {
        id: "cache_estimated_savings",
        label: "缓存估算节省",
        value: cache.estimated_savings ?? null,
        unit: "USD",
        kind: "cache",
        cache: {
          hit: cache.hit ?? null,
          miss: cache.miss ?? null,
          bypassed: cache.bypassed ?? null,
          unavailable: cache.unavailable ?? null,
          eligible: cache.eligible_requests ?? null,
          avoidedTokens: cache.avoided_tokens ?? null,
          estimatedSavings: cache.estimated_savings ?? null,
          status: cache.data_status || "unavailable",
        },
        dataStatus,
        evidenceState: cache.estimated_savings == null ? "unavailable" : (cache.data_status || "estimated"),
      },
    },
  ];
}

export function finopsTrendViewModel(payload = {}) {
  return (Array.isArray(payload?.items) ? payload.items : []).map((item) => ({
    bucket: item.bucket,
    label: String(item.bucket || "").replace("T00:00:00Z", "").replace("T", " ").replace(":00:00Z", ":00"),
    requests: hasNumber(item.requests) ? item.requests : 0,
    cost: item.estimated_cost,
    p95: item.p95_latency_ms,
    total: item?.tokens?.total,
    series: {
      input: item?.tokens?.input ?? null,
      output: item?.tokens?.output ?? null,
      cached: item?.tokens?.cached_input ?? null,
      reasoning: item?.tokens?.reasoning ?? null,
    },
    cache: {
      eligible: item?.cache?.eligible_requests ?? null,
      hit: item?.cache?.hit ?? null,
      miss: item?.cache?.miss ?? null,
      bypassed: item?.cache?.bypassed ?? null,
      unavailable: item?.cache?.unavailable ?? null,
      avoidedTokens: item?.cache?.avoided_tokens ?? null,
      estimatedSavings: item?.cache?.estimated_savings ?? null,
      status: item?.cache?.data_status || "unavailable",
    },
    status: item.data_status || "unavailable",
  }));
}

export function finopsBudgetView(payload = {}) {
  const budget = Array.isArray(payload?.items) ? payload.items[0] : null;
  if (!budget) {
    return {
      name: "未配置预算",
      amountLabel: "未记录",
      spentLabel: "未记录",
      forecastLabel: "暂不可用",
      usagePct: null,
      thresholdState: "unavailable",
      confidence: "unavailable",
      status: "unavailable",
    };
  }
  const progress = budget.progress || {};
  return {
    name: budget.name || "预算",
    amountLabel: formatFinOpsCost(budget.amount, "estimated"),
    spentLabel: formatFinOpsCost(progress.spent_amount, progress.spent_amount == null ? "unavailable" : "estimated"),
    forecastLabel: formatFinOpsCost(progress.forecast_amount, progress.forecast_status),
    usagePct: hasNumber(progress.usage_pct) ? progress.usage_pct : null,
    thresholdState: progress.threshold_state || "unavailable",
    confidence: progress.confidence || "unavailable",
    status: progress.forecast_status || "unavailable",
  };
}

export function finopsDoughnutSegments(rows = [], valueKey = "cost") {
  const values = rows
    .map((row) => ({
      key: String(row.key || "未记录"),
      value: hasNumber(row[valueKey]) && row[valueKey] > 0 ? row[valueKey] : null,
    }))
    .filter((row) => row.value != null);
  const total = values.reduce((sum, row) => sum + row.value, 0);
  if (!total) return [];
  return values
    .sort((a, b) => b.value - a.value)
    .map((row, index) => ({
      ...row,
      colorIndex: index % 6,
      sharePct: Number(((row.value / total) * 100).toFixed(1)),
    }));
}

export function finopsRoiEconomicsView(payload = {}) {
  const verified = payload.verified_roi || {};
  const unitEconomics = Object.values(payload.unit_economics || {}).map((item) => ({
    label: item.label || "单位成本",
    valueLabel: item.value == null ? "暂不可用" : formatFinOpsCost(item.value, item.status),
    status: item.status || "unavailable",
  }));
  return {
    funnel: Array.isArray(payload.funnel) ? payload.funnel : [],
    unitEconomics,
    verifiedRoiLabel: verified.status === "verified" && hasNumber(verified.value)
      ? formatFinOpsPercent(verified.value * 100)
      : "证据不足",
    verifiedRoiStatus: verified.status || "not_recorded",
    scenarios: (Array.isArray(payload.scenarios) ? payload.scenarios : [])
      .filter((item) => item.status === "estimated")
      .map((item) => {
        const result = item.result || {};
        return {
          ...item,
          monthlyBenefitLabel: formatFinOpsCost(result.monthly_benefit),
          monthlyCostLabel: formatFinOpsCost(result.monthly_total_cost),
          monthlyNetBenefitLabel: formatFinOpsCost(result.monthly_net_benefit),
          roiLabel: hasNumber(result.roi_ratio)
            ? formatFinOpsPercent(result.roi_ratio * 100)
            : "暂不可用",
          paybackLabel: hasNumber(result.payback_months)
            ? `${formatFinOpsNumber(result.payback_months)} 个月`
            : "暂不可用",
          formulaRevision: result.formula_revision || item.formula_revision || "",
        };
      }),
    evidenceGaps: Array.isArray(payload.evidence_gaps) ? payload.evidence_gaps : [],
  };
}

export function finopsOpportunityRows(payload = {}) {
  return (Array.isArray(payload?.items) ? payload.items : []).map((item) => ({
    ...item,
    evidenceRefs: (Array.isArray(item.evidence_refs) ? item.evidence_refs : [])
      .filter((value) => typeof value === "string" && value.trim())
      .slice(0, 5),
    stateLabel: item.queue_state === "ready" ? "可评估" : "观察中",
    savingsLabel: hasNumber(item.estimated_savings)
      ? formatFinOpsCost(item.estimated_savings, "estimated")
      : "暂不可估算",
    impactLabel: { high: "高", medium: "中", low: "低" }[item.impact] || "未记录",
    confidenceLabel: { high: "高", medium: "中", low: "低" }[item.confidence] || "未记录",
    effortLabel: { high: "高", medium: "中", low: "低" }[item.effort] || "未记录",
    actionLabel: "建议 · 需人工审批",
  }));
}

export function evidenceRequestRef({
  evidenceRefs = [],
  fallbackItems = [],
} = {}) {
  const values = [
    ...(Array.isArray(evidenceRefs) ? evidenceRefs : []),
    ...(Array.isArray(fallbackItems)
      ? fallbackItems.map((item) => item?.request_ref)
      : []),
  ];
  return values
    .map((value) => String(value || "").trim())
    .find((value) => /^[A-Za-z][A-Za-z0-9_-]{7,127}$/.test(value)) || "";
}

export function finopsRequestViewModel(item = {}) {
  const metrics = item?.metrics || {};
  const technicalRefs = item?.technical_refs || {};
  const cache = {
    hit: "命中",
    miss: "未命中",
    bypassed: "绕过",
    unavailable: "未记录",
  }[metrics?.cache?.state] || "未记录";
  const technicalLabels = {
    request_ref: "请求关联",
    run_id: "MAF 运行",
    apim_correlation_id: CUSTOMER_INFRA_LABELS.gatewayCorrelation,
    price_card_revision: "价目表版本",
    trace_id: CUSTOMER_INFRA_LABELS.trace,
    agent_id: "Agent",
  };
  const technicalItems = Object.entries(technicalRefs)
    .filter(([, value]) => value !== undefined && value !== null && String(value).trim())
    .map(([key, value]) => ({
      key,
      label: technicalLabels[key] || key,
      value: String(value),
    }));
  return {
    title: item?.display?.name || "请求证据",
    operation: item?.display?.operation || "操作记录",
    occurredAt: item?.display?.occurred_at || "",
    status: item.status || "unknown",
    tokens: metrics?.tokens?.total ?? null,
    tokenDetail: metrics.tokens || {},
    cache,
    cost: formatFinOpsCost(
      metrics?.estimated_cost?.amount,
      metrics?.estimated_cost?.status,
    ),
    costStatus: metrics?.estimated_cost?.status || "unavailable",
    latency: formatFinOpsDuration(metrics.latency_ms),
    errorCategory: metrics.error_category || "无",
    gatewayCoverage: finopsGatewayCoverageLabel(metrics.gateway_coverage),
    evidenceState: metrics.evidence_state || "unavailable",
    businessRequest: {
      text: item?.business_request?.text || "未记录",
      status: item?.business_request?.status || "unavailable",
    },
    businessResponse: {
      text: item?.business_response?.text || "未记录",
      status: item?.business_response?.status || "unavailable",
    },
    timeline: Array.isArray(item.timeline) ? item.timeline : [],
    technical: {
      expanded: false,
      items: technicalItems,
    },
    links: {
      foundryTrace: item?.links?.foundry_trace || "",
      azureMonitor: item?.links?.azure_monitor || "",
    },
    sectionOrder: [
      "summary",
      "metrics",
      "business_request",
      "business_response",
      "timeline",
      "technical",
    ],
  };
}


export function finopsInsightViewModel(item = null) {
  if (!item) {
    return {
      state: "empty",
      stateLabel: "尚未分析",
      title: "尚无分析结论",
      summary: "有可复核证据后，可按需运行分析。",
      findings: [],
      gaps: [],
      draftSuggestions: [],
      evidenceState: "unavailable",
      confidence: null,
      generatedAt: "",
    };
  }
  const state = String(item.status || "failed");
  const stateLabel = {
    ready: "分析完成",
    insufficient_data: "证据不足",
    stale: "分析结果已过期",
    failed: "分析暂不可用",
  }[state] || "分析暂不可用";
  const findings = ["ready", "stale"].includes(state)
    ? (Array.isArray(item.findings) ? item.findings : []).map((finding) => ({
      kind: finding.kind || "evidence_gap",
      statement: finding.statement || "未记录",
      evidenceRefs: Array.isArray(finding.evidence_refs) ? finding.evidence_refs : [],
      evidenceCount: Number(finding.evidence_count || finding.evidence_refs?.length || 0),
    }))
    : [];
  const gaps = Array.isArray(item.evidence_gaps)
    ? item.evidence_gaps.filter(Boolean)
    : [];
  return {
    state,
    stateLabel,
    title: item.title || stateLabel,
    summary: state === "insufficient_data"
      ? "证据不足，暂不生成推测性结论。"
      : state === "failed"
        ? "分析暂不可用"
        : item.summary || "未记录",
    findings,
    gaps,
    draftSuggestions: state === "ready"
      ? (Array.isArray(item.draft_suggestions) ? item.draft_suggestions : []).map((suggestion) => ({
        actionType: suggestion.action_type || "",
        reason: suggestion.reason || "",
        payload: suggestion.payload && typeof suggestion.payload === "object"
          ? { ...suggestion.payload }
          : {},
      }))
      : [],
    evidenceState: item.evidence_state || "unavailable",
    confidence: item.confidence ?? null,
    generatedAt: item.generated_at || "",
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
    cacheHitRate: item.cache_hit_rate_pct,
    cache: {
      eligible: item?.cache?.eligible_requests ?? null,
      hit: item?.cache?.hit ?? null,
      miss: item?.cache?.miss ?? null,
      bypassed: item?.cache?.bypassed ?? null,
      avoidedTokens: item?.cache?.avoided_tokens ?? null,
      estimatedSavings: item?.cache?.estimated_savings ?? null,
      status: item?.cache?.data_status || "unavailable",
    },
    status: item.data_status || "unavailable",
  }));
}
