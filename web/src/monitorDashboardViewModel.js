import { CUSTOMER_INFRA_LABELS } from "./finopsViewModel.js";

function asNumber(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function asInt(value) {
  const number = asNumber(value);
  return number === null ? null : Math.trunc(number);
}

function formatInteger(value) {
  const number = asInt(value);
  return number === null ? "未记录" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

function formatCurrency(amount, currency = "USD") {
  const number = asNumber(amount);
  if (number === null) return "未记录";
  const code = String(currency || "USD").trim() || "USD";
  return `${code} ${new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(number)}`;
}

function formatPercent(value, digits = 0) {
  const number = asNumber(value);
  if (number === null) return "未记录";
  return `${new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(number)}%`;
}

function shareLabel(count, total) {
  const countValue = asNumber(count);
  const totalValue = asNumber(total);
  if (countValue === null || totalValue === null || totalValue <= 0) return "未记录";
  return formatPercent((countValue / totalValue) * 100, 0);
}

function allowedState(value, allowed, fallback) {
  const text = String(value || "").trim().toLowerCase();
  return allowed.has(text) ? text : fallback;
}

function tokenMeta(summary = {}) {
  const input = asInt(summary.input);
  const output = asInt(summary.output);
  const unknownRuns = asInt(summary.unknown_runs) || 0;
  if (input === null || output === null) return "输入/输出未完整记录";
  if (unknownRuns > 0) return `${unknownRuns} 次运行缺少用量明细`;
  return `${formatInteger(input)} 输入 / ${formatInteger(output)} 输出`;
}

function qualityBadge(summary = {}) {
  const gate = summary.context_optimization || {};
  const status = allowedState(gate.status, new Set(["evaluated", "stale", "malformed", "unavailable"]), "unavailable");
  if (status === "evaluated" && gate.eligible === true) return "候选路由可用";
  if (status === "stale") return "离线评估过期";
  if (status === "malformed") return "评估摘要异常";
  if (status === "unavailable") return "尚无离线评估";
  return "待验证";
}

function roiBadge(summary = {}) {
  const status = allowedState(summary.status, new Set(["verified", "pending_verification", "unavailable"]), "unavailable");
  if (status === "verified") return "已验证";
  if (status === "pending_verification") return "待验证";
  return "不可用";
}

function cacheView(summary = {}) {
  const eligible = Math.max(asInt(summary.eligible) || 0, 0);
  const hits = Math.min(Math.max(asInt(summary.hits) || 0, 0), eligible);
  const misses = Math.min(Math.max(asInt(summary.misses) || 0, 0), Math.max(eligible - hits, 0));
  const unavailable = Math.max(asInt(summary.unavailable) || 0, 0);
  const recordedRate = asNumber(summary.hit_rate_pct);
  const hitRate = eligible > 0
    ? Math.min(Math.max(recordedRate === null ? (hits / eligible) * 100 : recordedRate, 0), 100)
    : null;
  const avoidedTokens = Math.max(asInt(summary.avoided_tokens) || 0, 0);
  const avoidedCost = summary.avoided_cost && typeof summary.avoided_cost === "object" ? summary.avoided_cost : {};
  const avoidedCostStatus = allowedState(avoidedCost.status, new Set(["estimated", "partial", "unavailable"]), "unavailable");
  const reuseDetail = avoidedCostStatus === "estimated"
    ? `；估算避免 ${formatCurrency(avoidedCost.amount, avoidedCost.currency)}`
    : avoidedCostStatus === "partial"
      ? "；部分复用尚未计价"
      : "；避免成本待计价";

  if (!eligible) {
    return {
      value: "未记录",
      badge: unavailable ? "缓存不可用" : "待记录",
      tone: unavailable ? "warn" : "neutral",
      meta: unavailable ? `${formatInteger(unavailable)} 次 Redis 不可用` : "尚无可缓存的分析调用",
    };
  }

  return {
    value: formatPercent(hitRate, 0),
    badge: `${formatInteger(hits)} 命中`,
    tone: hits > 0 ? "ok" : "neutral",
    meta: `${formatInteger(hits)} 命中 / ${formatInteger(eligible)} 可缓存；避免 ${formatInteger(avoidedTokens)} Tokens${reuseDetail}`,
    hits,
    misses,
    unavailable,
  };
}

function requestCacheLabel(cache = {}) {
  const state = allowedState(cache.state, new Set(["hit", "miss", "unavailable", "bypassed"]), "bypassed");
  if (state === "hit") return "Redis 命中";
  if (state === "miss") return "Redis 未命中";
  if (state === "unavailable") return "Redis 不可用";
  return "不适用";
}

function requestStatusLabel(status) {
  const value = allowedState(status, new Set(["completed", "succeeded", "failed", "cancelled", "unknown"]), "unknown");
  return {
    completed: "成功",
    succeeded: "成功",
    failed: "失败",
    cancelled: "已取消",
    unknown: "未记录",
  }[value];
}

function requestStatusState(status) {
  const value = allowedState(status, new Set(["completed", "succeeded", "failed", "cancelled", "unknown"]), "unknown");
  return value === "completed" ? "succeeded" : value;
}

function requestTokenTotal(value) {
  const scalar = asInt(value);
  if (scalar !== null && scalar >= 0) return scalar;
  const usage = value && typeof value === "object" ? value : {};
  const total = asInt(usage.total ?? usage.total_tokens);
  if (total !== null && total >= 0) return total;
  const input = asInt(usage.input ?? usage.input_tokens ?? usage.prompt_tokens);
  const output = asInt(usage.output ?? usage.output_tokens ?? usage.completion_tokens);
  if (input !== null && input >= 0 && output !== null && output >= 0) return input + output;
  return null;
}

function requestTimeLabel(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未记录";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function requestRows(rows = []) {
  return rows.map((row) => {
    const cache = row?.cache && typeof row.cache === "object" ? row.cache : {};
    const trace = row?.trace && typeof row.trace === "object" ? row.trace : {};
    const durationMs = asInt(row?.duration_ms);
    return {
      runId: typeof row?.run_id === "string" ? row.run_id : "",
      occurredAt: typeof row?.occurred_at === "string" ? row.occurred_at : "",
      occurredLabel: requestTimeLabel(row?.occurred_at),
      memberLabel: typeof row?.member_label === "string" && row.member_label.trim() ? row.member_label.trim() : "未归因",
      workspaceLabel: typeof row?.workspace_label === "string" && row.workspace_label.trim() ? row.workspace_label.trim() : "当前工作区",
      route: typeof row?.route === "string" && row.route.trim() ? row.route.trim() : "未记录",
      deployment: typeof row?.deployment === "string" && row.deployment.trim() ? row.deployment.trim() : "未记录",
      status: requestStatusState(row?.status),
      statusLabel: requestStatusLabel(row?.status),
      tokensLabel: formatInteger(requestTokenTotal(row?.tokens)),
      durationLabel: durationMs === null || durationMs < 0 ? "未记录" : `${formatInteger(durationMs)} ms`,
      cacheLabel: requestCacheLabel(cache),
      cacheState: allowedState(cache.state, new Set(["hit", "miss", "unavailable", "bypassed"]), "bypassed"),
      traceLabel: typeof trace.trace_id === "string" && /^[a-f0-9]{32}$/i.test(trace.trace_id.trim()) ? trace.trace_id.trim().toLowerCase() : "未记录",
      traceAgent: typeof trace.agent_id === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(trace.agent_id.trim()) ? trace.agent_id.trim() : "未记录",
    };
  });
}

function scopeLabel(scope = {}) {
  const kind = String(scope.kind || "").trim().toLowerCase();
  if (typeof scope.label === "string" && scope.label.trim()) return scope.label.trim();
  if (kind === "portfolio") return "已拥有的工作区组合";
  return "当前工作区";
}

function selectionLabel(counts = {}) {
  const source = counts && typeof counts === "object" ? counts : {};
  const labels = [
    ["manual", "手动"],
    ["workspace_policy", "策略"],
    ["fallback", "备选"],
    ["policy", "默认"],
  ];
  const parts = labels
    .map(([key, label]) => [label, asInt(source[key]) || 0])
    .filter(([, count]) => count > 0)
    .map(([label, count]) => `${label} ${count} 次`);
  return parts.join(" · ") || "未记录选择来源";
}

function estimatedCostLabel(cost = {}) {
  const status = allowedState(cost.status, new Set(["estimated", "partial", "unavailable"]), "unavailable");
  if (status === "estimated") return `估算 ${formatCurrency(cost.amount, cost.currency)}`;
  if (status === "partial") return `${formatInteger(cost.unpriced_calls)} 次未计价`;
  if (status === "unavailable") return "未配置估算参考";
  return "";
}

const GATEWAY_COPY = Object.freeze({
  verified: { label: "入口指标已验证", tone: "ok" },
  partial: { label: "部分入口指标已验证", tone: "warn" },
  pending: { label: "等待入口指标", tone: "warn" },
  unavailable: { label: "入口指标暂不可用", tone: "error" },
  not_configured: { label: "未配置入口指标", tone: "neutral" },
});

const GATEWAY_SOURCE_COPY = Object.freeze({
  apim_custom_metric: `${CUSTOMER_INFRA_LABELS.gateway}指标`,
  apim_metric_pending: "入口指标采集中",
  apim_metric_query_unavailable: "入口指标查询",
  apim_metric_not_configured: "未配置入口指标",
});

function gatewayEvidenceView(value = {}) {
  const state = allowedState(value.state, new Set(["verified", "partial", "pending", "unavailable", "not_configured"]), "not_configured");
  const copy = GATEWAY_COPY[state];
  const provenance = String(value.provenance || "").trim();
  const lastObservedAt = typeof value.last_observed_at === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value.last_observed_at)
    ? value.last_observed_at
    : null;
  const verifiedWorkspaceCount = asInt(value.verified_workspace_count) || 0;
  const workspaceCount = asInt(value.workspace_count) || 0;
  return {
    state,
    label: copy.label,
    tone: copy.tone,
    callsLabel: formatInteger(value.governed_calls),
    tokensLabel: formatInteger(value.total_tokens),
    sourceLabel: GATEWAY_SOURCE_COPY[provenance] || "未记录网关来源",
    lastObservedAt,
    workspaceCount,
    scopeLabel: workspaceCount > 1 ? `${verifiedWorkspaceCount} / ${workspaceCount} 个工作区已验证` : "当前工作区",
  };
}

export function monitorDashboardViewModel(payload = {}) {
  const summary = payload.summary || {};
  const calls = summary.calls || {};
  const tokens = summary.tokens || {};
  const cost = summary.cost || {};
  const quality = summary.quality || {};
  const roi = summary.roi || {};
  const models = Array.isArray(payload.models) ? payload.models : [];
  const routes = Array.isArray(payload.routes) ? payload.routes : [];
  const dailySeries = Array.isArray(payload?.series?.daily) ? payload.series.daily : [];
  const members = Array.isArray(payload.members) ? payload.members : [];
  const observedCalls = asInt(calls.observed) || 0;
  const modelCallTotal = models.reduce((sum, row) => sum + (asInt(row.calls) || 0), 0);
  const routeCallTotal = routes.reduce((sum, row) => sum + (asInt(row.calls) || 0), 0);
  const costStatus = allowedState(cost.status, new Set(["available", "estimated", "partial", "unavailable"]), "unavailable");
  const roiStatus = allowedState(roi.status, new Set(["verified", "pending_verification", "unavailable"]), "unavailable");
  const gateway = gatewayEvidenceView(payload?.gateway || {});
  const opportunityStatus = allowedState(
    payload?.opportunity?.status,
    new Set(["available", "unavailable", "pending_verification", "partial", "stale", "error"]),
    "unavailable",
  );

  return {
    cards: {
      governed: {
        value: gateway.callsLabel,
        badge: gateway.label,
        tone: gateway.tone,
        meta: `${gateway.sourceLabel}；${gateway.tokensLabel} 网关 Tokens`,
      },
      calls: {
        value: formatInteger(calls.observed),
        badge: `${formatInteger(calls.succeeded)} 成功`,
        meta: `${formatInteger(calls.failed)} 失败 / ${formatInteger(calls.unknown)} 未知`,
      },
      tokens: {
        value: formatInteger(tokens.total),
        badge: `${formatInteger(tokens.known_runs)} 次已记录`,
        meta: tokenMeta(tokens),
      },
      cost: {
        value: ["available", "estimated"].includes(costStatus) ? formatCurrency(cost.amount, cost.currency) : "未记录",
        badge: costStatus === "estimated" ? "估算" : costStatus === "available" ? "已计价" : costStatus === "partial" ? "部分未计价" : "不可用",
        meta: costStatus === "estimated"
          ? "Owner 维护价格卡；非云平台账单"
          : costStatus === "partial"
            ? `${formatInteger(cost.unpriced_calls)} 次调用未计价`
            : cost.price_catalog_version || String(cost.currency || "USD"),
      },
      cache: cacheView(summary.cache || {}),
      quality: {
        value: quality.audited_runs === null ? "未记录" : formatInteger(quality.audited_runs),
        badge: qualityBadge(quality),
        meta: `${formatInteger(quality.rework_runs)} 次复修`,
      },
      roi: {
        value: roiStatus === "verified" ? formatCurrency(roi.verified_value, roi.currency || "CNY") : "未记录",
        badge: roiBadge(roi),
        meta: roiStatus === "verified" ? `${formatPercent(roi.roi_pct, 0)} ROI` : "等待验证结果",
      },
    },
    dailySeries,
    modelRows: models.map((row) => ({
      deployment: String(row.deployment || "unknown"),
      route: String(row.route || "unknown"),
      calls: asInt(row.calls) || 0,
      totalTokens: asInt(row.total_tokens) || 0,
      shareLabel: shareLabel(row.calls, modelCallTotal || observedCalls),
      selectionLabel: selectionLabel(row.selection_counts),
      estimatedCostLabel: estimatedCostLabel(row.estimated_cost),
      secondaryLabel: [selectionLabel(row.selection_counts), estimatedCostLabel(row.estimated_cost)].filter(Boolean).join(" · "),
    })),
    routeRows: routes.map((row) => ({
      route: String(row.route || "unknown"),
      calls: asInt(row.calls) || 0,
      totalTokens: asInt(row.total_tokens) || 0,
      shareLabel: shareLabel(row.calls, routeCallTotal || observedCalls),
      selectionLabel: selectionLabel(row.selection_counts),
      estimatedCostLabel: estimatedCostLabel(row.estimated_cost),
      secondaryLabel: [selectionLabel(row.selection_counts), estimatedCostLabel(row.estimated_cost)].filter(Boolean).join(" · "),
    })),
    memberRows: members.map((row) => ({
      label: String(row.member_label || row.subject_label || "成员"),
      calls: asInt(row.runs) || 0,
      totalTokens: asInt(row.total_tokens) || 0,
      totalTokensLabel: formatInteger(row.total_tokens),
      costLabel: formatCurrency(row?.cost?.total, row?.cost?.currency || "USD"),
    })),
    requestRows: requestRows(Array.isArray(payload.requests) ? payload.requests : []),
    opportunity: {
      status: opportunityStatus,
      kind: payload?.opportunity?.kind || null,
      message: String(payload?.opportunity?.message || "暂无可展示机会"),
    },
    coverage: {
      governedTextLabel: formatInteger(payload?.coverage?.governed_text_calls),
      imageCallLabel: formatInteger(payload?.coverage?.out_of_scope_image_calls),
    },
    gateway,
    scope: payload.scope || {},
    scopeLabel: scopeLabel(payload.scope || {}),
  };
}
