const GATEWAY_COPY = Object.freeze({
  not_configured: { label: "网关未启用", tone: "neutral" },
  configured_unverified: { label: "网关已配置，待验证实际调用", tone: "warn" },
  verified: { label: "网关调用已验证", tone: "ok" },
  misconfigured: { label: "网关配置不完整", tone: "error" },
});

function nonNegativeNumber(value, fallback = null) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return fallback;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : fallback;
}

function displayNumber(value) {
  const number = nonNegativeNumber(value);
  return number === null ? "未记录" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

export function monitoringSnapshotViewModel(snapshot = {}) {
  const usage = snapshot?.usage || {};
  const gateway = snapshot?.gateway || {};
  const reliability = snapshot?.reliability || {};
  const models = snapshot?.models || {};
  const routes = Array.isArray(models?.routes) ? models.routes : [];
  const defaultRoute = String(models?.default_route || "").trim();
  const selectedRoute = routes.find((route) => String(route?.id || "").trim() === defaultRoute) || routes[0] || {};
  const state = String(gateway?.state || "not_configured").trim().toLowerCase();
  const copy = GATEWAY_COPY[state] || GATEWAY_COPY.not_configured;
  return {
    evidenceSource: String(snapshot?.evidence_source || "run_store"),
    tokenLabel: displayNumber(usage?.total_tokens),
    tokenState: String(usage?.status || "unknown"),
    inputLabel: displayNumber(usage?.input_tokens),
    outputLabel: displayNumber(usage?.output_tokens),
    knownRuns: nonNegativeNumber(usage?.known_runs, 0),
    unknownRuns: nonNegativeNumber(usage?.unknown_runs, 0),
    gateway: {
      state,
      label: copy.label,
      tone: copy.tone,
      governedCalls: nonNegativeNumber(gateway?.governed_calls),
    },
    reliability: {
      completedRuns: nonNegativeNumber(reliability?.completed_runs, 0),
      failedRuns: nonNegativeNumber(reliability?.failed_runs, 0),
      auditEvents: nonNegativeNumber(reliability?.audit_events, 0),
    },
    models: {
      state: String(models?.state || "unknown"),
      defaultRoute: defaultRoute || null,
      routeCount: routes.length,
      label: String(selectedRoute?.deployment || "未记录"),
    },
  };
}
