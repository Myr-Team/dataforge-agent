export const MODEL_EXECUTION_KINDS = [
  { id: "direct_reply", label: "直接回复", description: "简短且有明确上下文的回答", capability: "chat" },
  { id: "follow_up", label: "会话跟进", description: "基于当前工作区和历史结论继续讨论", capability: "chat" },
  { id: "full_analysis", label: "完整分析", description: "多 Agent 分析与产物规划", capability: "analysis" },
  { id: "audit_repair", label: "审计复修", description: "证据不足时的复修与重新判定", capability: "analysis" },
];

function text(value) {
  return String(value || "").trim();
}

function assignment(raw = {}) {
  return {
    primaryRouteId: text(raw.primary_route_id),
    fallbackRouteId: text(raw.fallback_route_id),
  };
}

export function modelRoutingViewModel(payload = {}) {
  const rawRoutes = Array.isArray(payload.routes) ? payload.routes : [];
  const routes = rawRoutes
    .filter((route) => route && typeof route === "object" && text(route.id))
    .map((route) => ({
      id: text(route.id),
      label: text(route.label) || text(route.id),
      capabilities: Array.isArray(route.capabilities) ? route.capabilities.map(text).filter(Boolean) : [],
    }));
  const rawAssignments = payload?.policy?.assignments || {};
  const assignments = Object.fromEntries(MODEL_EXECUTION_KINDS.map((kind) => [kind.id, assignment(rawAssignments[kind.id])]));
  const rawPriceCard = payload.price_card || {};
  const configuredRoutes = Array.isArray(rawPriceCard.configured_route_ids)
    ? rawPriceCard.configured_route_ids.map(text).filter(Boolean)
    : [];
  const configured = text(rawPriceCard.state) === "configured";

  return {
    defaultRouteId: text(payload.default_route),
    routes,
    assignments,
    policyRevision: Number.isInteger(payload?.policy?.revision) ? payload.policy.revision : 0,
    priceCard: {
      state: configured ? "configured" : "not_configured",
      statusLabel: configured ? "已配置估算参考" : "尚未配置估算参考",
      revision: Number.isInteger(rawPriceCard.revision) ? rawPriceCard.revision : 0,
      currency: text(rawPriceCard.currency) || "USD",
      configuredRouteIds: configuredRoutes,
      configuredRouteCount: configuredRoutes.length,
    },
  };
}
