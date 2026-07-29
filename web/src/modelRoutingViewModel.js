export const MODEL_EXECUTION_KINDS = [
  { id: "direct_reply", label: "直接回复", description: "简短且有明确上下文的回答", capability: "chat" },
  { id: "follow_up", label: "会话跟进", description: "基于当前工作区和历史结论继续讨论", capability: "chat" },
  { id: "full_analysis", label: "完整分析", description: "多 Agent 分析与产物规划", capability: "analysis" },
  { id: "audit_repair", label: "审计复修", description: "证据不足时的复修与重新判定", capability: "analysis" },
];

export const MODEL_AGENT_ROLES = [
  { id: "df-coordinator", label: "协调 Agent", description: "识别意图、规划协作与汇总运行路径" },
  { id: "df-corpus-analyst", label: "数据分析 Agent", description: "读取工作区证据与数据上下文" },
  { id: "df-market-researcher", label: "市场研究 Agent", description: "补充市场、竞品与外部线索" },
  { id: "df-feasibility-analyst", label: "可行性 Agent", description: "形成产品机会与可行性判断" },
  { id: "df-auditor", label: "审计 Agent", description: "检查证据、结论强度与风险" },
  { id: "df-producer", label: "产物 Agent", description: "生成报告、图像和交付产物" },
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
      deployment: text(route.deployment),
      modelId: text(route.model_id) || text(route.deployment),
      providerId: text(route.provider_id) || "azure-foundry",
      providerType: text(route.provider_type) || "azure_foundry",
      providerLabel: text(route.provider_type) === "deepseek" ? "DeepSeek" : "Azure Foundry",
      label: text(route.label) || text(route.id),
      capabilities: Array.isArray(route.capabilities) ? route.capabilities.map(text).filter(Boolean) : [],
    }));
  const rawAssignments = payload?.policy?.assignments || {};
  const assignments = Object.fromEntries(MODEL_EXECUTION_KINDS.map((kind) => [kind.id, assignment(rawAssignments[kind.id])]));
  const rawAgentAssignments = payload?.policy?.agent_assignments || {};
  const agentAssignments = Object.fromEntries(
    MODEL_AGENT_ROLES.map((agent) => [agent.id, assignment(rawAgentAssignments[agent.id])]),
  );
  const rawPriceCard = payload.price_card || {};
  const configuredRoutes = Array.isArray(rawPriceCard.configured_route_ids)
    ? rawPriceCard.configured_route_ids.map(text).filter(Boolean)
    : [];
  const configured = text(rawPriceCard.state) === "configured";

  return {
    defaultRouteId: text(payload.default_route),
    routes,
    assignments,
    agentAssignments,
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
