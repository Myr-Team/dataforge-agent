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

function scopeLabel(scope = {}) {
  const kind = String(scope.kind || "").trim().toLowerCase();
  if (typeof scope.label === "string" && scope.label.trim()) return scope.label.trim();
  if (kind === "portfolio") return "已拥有的工作区组合";
  return "当前工作区";
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
  const costStatus = allowedState(cost.status, new Set(["available", "unavailable"]), "unavailable");
  const roiStatus = allowedState(roi.status, new Set(["verified", "pending_verification", "unavailable"]), "unavailable");
  const opportunityStatus = allowedState(
    payload?.opportunity?.status,
    new Set(["available", "unavailable", "pending_verification", "partial", "stale", "error"]),
    "unavailable",
  );

  return {
    cards: {
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
        value: costStatus === "available" ? formatCurrency(cost.amount, cost.currency) : "未记录",
        badge: costStatus === "available" ? "已计价" : "不可用",
        meta: cost.price_catalog_version || String(cost.currency || "USD"),
      },
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
    })),
    routeRows: routes.map((row) => ({
      route: String(row.route || "unknown"),
      calls: asInt(row.calls) || 0,
      totalTokens: asInt(row.total_tokens) || 0,
      shareLabel: shareLabel(row.calls, routeCallTotal || observedCalls),
    })),
    memberRows: members.map((row) => ({
      label: String(row.member_label || row.subject_label || "成员"),
      calls: asInt(row.runs) || 0,
      totalTokens: asInt(row.total_tokens) || 0,
      costLabel: formatCurrency(row?.cost?.total, row?.cost?.currency || "USD"),
    })),
    opportunity: {
      status: opportunityStatus,
      kind: payload?.opportunity?.kind || null,
      message: String(payload?.opportunity?.message || "暂无可展示机会"),
    },
    coverage: {
      governedTextLabel: formatInteger(payload?.coverage?.governed_text_calls),
      imageCallLabel: formatInteger(payload?.coverage?.out_of_scope_image_calls),
    },
    scope: payload.scope || {},
    scopeLabel: scopeLabel(payload.scope || {}),
  };
}
