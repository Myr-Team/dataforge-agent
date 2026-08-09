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
  { id: "df-finops-analyst", label: "FinOps 分析 Agent", description: "分析成本、用量、缓存与运营风险" },
  { id: "df-roi-analyst", label: "ROI 分析 Agent", description: "分析价值测算、证据成熟度与回报边界" },
];

function text(value) {
  return String(value || "").trim();
}

const ROUTE_UNAVAILABLE_LABELS = {
  governance_required: "需先纳入模型路由",
  official_pricing_required: "需先关联官方价格",
  provider_secret_unavailable: "需重新录入安全凭据",
  connection_verification_required: "需先完成连接检测",
  provider_connection_unavailable: "连接异常，暂不可选",
  supported_model_required: "暂无受支持模型",
  provider_type_not_routable: "当前仅用于连接验证",
};

function money(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  return `$${amount.toLocaleString("en-US", { maximumFractionDigits: 6 })}`;
}

export function officialPricePresentation(raw = {}) {
  const provider = text(raw.provider);
  const displayName = text(raw.display_name) || text(raw.official_model) || "官方价格";
  const input = money(raw.input_per_million);
  const output = money(raw.output_per_million);
  const cached = raw.cached_input_per_million === null || raw.cached_input_per_million === undefined
    ? ""
    : money(raw.cached_input_per_million);
  const rates = [];
  if (cached) rates.push({ label: "缓存命中", value: cached, unit: "/ 百万 Token" });
  rates.push({ label: cached ? "缓存未命中" : "输入", value: input, unit: "/ 百万 Token" });
  rates.push({ label: "输出", value: output, unit: "/ 百万 Token" });
  const rateLabel = provider === "deepseek" && cached
    ? `缓存命中 ${cached} / 未命中 ${input} / 输出 ${output}`
    : `${cached ? `缓存 ${cached} / ` : ""}输入 ${input} / 输出 ${output}`;
  return {
    label: `${displayName} · ${rateLabel}`,
    rates,
    currency: text(raw.currency) || "USD",
    revision: text(raw.revision),
    sourceUrl: text(raw.source_url),
  };
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
    .map((route) => {
      const providerType = text(route.provider_type) || "azure_foundry";
      const unavailableReason = text(route.unavailable_reason);
      return {
        id: text(route.id),
        deployment: text(route.deployment),
        modelId: text(route.model_id) || text(route.deployment),
        providerId: text(route.provider_id) || "azure-foundry",
        providerType,
        providerLabel: text(route.provider_label)
          || (providerType === "deepseek" ? "DeepSeek 原厂" : "Azure Foundry"),
        label: text(route.label) || text(route.id),
        capabilities: Array.isArray(route.capabilities) ? route.capabilities.map(text).filter(Boolean) : [],
        officialPriceKey: text(route.official_price_key),
        pricingState: text(route.pricing_state),
        healthState: text(route.health_state),
        governanceState: text(route.governance_state),
        selectable: route.selectable !== false,
        unavailableReason,
        unavailableLabel: ROUTE_UNAVAILABLE_LABELS[unavailableReason] || "暂不可选",
      };
    });
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
