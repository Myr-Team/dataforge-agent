const CONNECTION_LABELS = {
  testing: "检测中",
  connected: "已连接",
  degraded: "连接异常",
  invalid: "凭据无效",
  disabled: "已停用",
};

const GOVERNANCE_LABELS = {
  pending: "待验证",
  governed: "已纳管",
  degraded: "治理异常",
  unmanaged: "未纳管",
};

function text(value) {
  return String(value || "").trim();
}

function providerModel(raw = {}) {
  const supportState = text(raw.support_state) || "unsupported";
  return {
    id: text(raw.model_id),
    name: text(raw.display_name) || text(raw.model_id),
    capabilities: Array.isArray(raw.capabilities)
      ? raw.capabilities.map(text).filter(Boolean)
      : [],
    supportState,
    supportLabel: supportState === "supported" ? "可用" : supportState === "unpriced" ? "未计价" : "暂不支持",
    priceKey: text(raw.price_key),
    pricingLabel: text(raw.price_key) ? "已计价" : "未计价",
  };
}

export function providerConnectionsViewModel(payload = {}) {
  const rows = Array.isArray(payload.items) ? payload.items : [];
  const items = rows
    .filter((item) => item && typeof item === "object" && text(item.provider_id))
    .map((item) => {
      const connectionState = text(item.connection_state) || "invalid";
      const governanceState = text(item.governance_state) || "pending";
      const models = Array.isArray(item.available_models)
        ? item.available_models.map(providerModel).filter((model) => model.id)
        : [];
      return {
        providerId: text(item.provider_id),
        providerType: text(item.provider_type),
        providerLabel: text(item.provider_type) === "deepseek" ? "DeepSeek 原厂" : text(item.provider_type),
        name: text(item.display_name) || "未命名提供商",
        baseUrl: text(item.base_url),
        connectionState,
        connectionLabel: CONNECTION_LABELS[connectionState] || "状态未知",
        governanceState,
        governanceLabel: GOVERNANCE_LABELS[governanceState] || "状态未知",
        secretStored: text(item.secret_status) === "stored",
        revision: Number.isInteger(item.revision) ? item.revision : 0,
        lastTestedAt: text(item.last_tested_at),
        lastSuccessAt: text(item.last_success_at),
        safeErrorCategory: text(item.safe_error_category),
        models,
        canAssign: connectionState === "connected"
          && governanceState === "governed"
          && models.some((model) => model.supportState === "supported"),
      };
    });
  return {
    items,
    summary: {
      connected: items.filter((item) => item.connectionState === "connected").length,
      governed: items.filter((item) => item.governanceState === "governed").length,
      actionRequired: items.filter((item) => item.connectionState !== "connected" || item.governanceState !== "governed").length,
    },
  };
}
