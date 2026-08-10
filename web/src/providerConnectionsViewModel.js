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

const SAFE_ERROR_LABELS = {
  provider_secret_missing: "需要重新录入 Key。保存后将重新检测连接。",
  provider_secret_get_failed: "暂时无法读取安全凭据，请稍后重试。",
  provider_secret_write_denied: "安全凭据无法保存，请联系管理员检查权限。",
  authentication_failed: "凭据验证未通过，请更新后重试。",
  access_denied: "当前凭据没有所需访问权限。",
  configuration_conflict: "配置不兼容，请复核后重试。",
  insufficient_balance: "原厂账户余额不足，请充值后重新检测。",
  invalid_request: "配置请求无效，请复核后重试。",
  invalid_parameters: "配置参数无效，请复核后重试。",
  provider_unavailable: "服务暂时不可用，请稍后重试。",
  provider_timeout: "原厂服务响应超时，请稍后重试。",
  rate_limited: "服务暂时繁忙，请稍后重试。",
  throttled: "服务暂时繁忙，请稍后重试。",
  timeout: "连接超时，请稍后重试。",
};

const STAGE_LABELS = {
  secret_read: "读取安全凭据",
  endpoint_resolution: "解析官方服务地址",
  tls_connect: "建立加密连接",
  provider_auth: "验证原厂凭据与余额",
  minimal_inference: "验证最小模型调用",
  model_discovery: "同步可用模型",
  completed: "全部检测完成",
};

const GENERIC_SAFE_ERROR = "连接状态异常，请检查配置后重试。";

const ROUTE_REASON_LABELS = {
  governance_required: "连接与计价已就绪，可纳入 Agent 模型路由。",
  provider_secret_unavailable: "需重新录入安全凭据后才能纳入模型路由。",
  connection_verification_required: "请先完成连接检测，再纳入模型路由。",
  provider_connection_unavailable: "连接异常，当前不可纳入模型路由。",
  supported_model_required: "尚未发现可用于 Agent 的受支持模型。",
  official_pricing_required: "仍有模型缺少官方计价，暂不可纳入路由。",
  provider_type_not_routable: "当前提供商仅用于连接验证，暂不参与 Agent 路由。",
};

function lifecycleStep(id, label, state, detail) {
  return { id, label, state, detail };
}

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
      const secretStatus = text(item.secret_status) || "unavailable";
      const connectionStage = text(item.connection_stage);
      const stageDurations = item.stage_durations_ms && typeof item.stage_durations_ms === "object"
        ? Object.fromEntries(Object.entries(item.stage_durations_ms)
          .filter(([key, value]) => STAGE_LABELS[key] && Number.isInteger(value) && value >= 0))
        : {};
      const totalDurationMs = Object.values(stageDurations)
        .reduce((total, value) => total + value, 0);
      const models = Array.isArray(item.available_models)
        ? item.available_models.map(providerModel).filter((model) => model.id)
        : [];
      const hasEligibility = item.route_eligibility && typeof item.route_eligibility === "object";
      const eligibility = hasEligibility
        ? item.route_eligibility
        : {};
      const legacySelectable = text(item.provider_type) !== "aws_bedrock"
        && connectionState === "connected"
        && governanceState === "governed"
        && models.some((model) => model.supportState === "supported");
      const routeSelectable = hasEligibility ? eligibility.selectable === true : legacySelectable;
      const canGovern = eligibility.can_govern === true;
      const routeReason = text(eligibility.reason);
      const pricedModels = models.filter((model) => model.supportState === "supported" && model.priceKey).length;
      const connectionReady = connectionState === "connected";
      const discoveryReady = models.some((model) => model.supportState === "supported") && pricedModels > 0;
      const lifecycle = [
        lifecycleStep("credential", "安全凭据", secretStatus === "stored" ? "complete" : "current", secretStatus === "stored" ? "已保存" : "待录入"),
        lifecycleStep("connection", "连接验证", connectionReady ? "complete" : secretStatus === "stored" ? "current" : "pending", connectionReady ? "已通过" : "待检测"),
        lifecycleStep("pricing", "模型与计价", discoveryReady ? "complete" : connectionReady ? "current" : "pending", discoveryReady ? `${pricedModels} 个已计价模型` : "待同步"),
        lifecycleStep("governance", "路由治理", routeSelectable ? "complete" : canGovern ? "current" : "pending", routeSelectable ? "已纳管" : canGovern ? "可纳管" : "待就绪"),
      ];
      return {
        providerId: text(item.provider_id),
        providerType: text(item.provider_type),
        providerLabel: text(item.provider_type) === "deepseek"
          ? "DeepSeek 原厂"
          : text(item.provider_type) === "aws_bedrock"
            ? "AWS Bedrock"
            : text(item.provider_type),
        region: text(item.region),
        isBedrock: text(item.provider_type) === "aws_bedrock",
        name: text(item.display_name) || "未命名提供商",
        baseUrl: text(item.base_url),
        connectionState,
        connectionLabel: CONNECTION_LABELS[connectionState] || "状态未知",
        governanceState,
        governanceLabel: GOVERNANCE_LABELS[governanceState] || "状态未知",
        secretStatus,
        secretStored: secretStatus === "stored",
        credentialLabel: secretStatus === "stored"
          ? "已安全保存"
          : secretStatus === "missing"
            ? "需要重新录入 Key"
            : "凭据状态暂不可用",
        credentialTone: secretStatus === "stored" ? "success" : "warning",
        connectionStage,
        stageLabel: STAGE_LABELS[connectionStage] || "尚未开始检测",
        stageDurations,
        totalDurationLabel: totalDurationMs > 0 ? `${totalDurationMs} ms` : "—",
        canTest: secretStatus === "stored" && connectionState !== "disabled",
        primaryAction: secretStatus === "stored" ? "test" : "rotate_secret",
        revision: Number.isInteger(item.revision) ? item.revision : 0,
        lastTestedAt: text(item.last_tested_at),
        lastSuccessAt: text(item.last_success_at),
        safeErrorLabel: !(text(item.provider_type) === "deepseek" && connectionState === "connected") && text(item.safe_error_category)
          ? SAFE_ERROR_LABELS[text(item.safe_error_category)] || GENERIC_SAFE_ERROR
          : "",
        models,
        routeSelectable,
        canGovern,
        eligibleModelCount: Number.isInteger(eligibility.eligible_model_count)
          ? eligibility.eligible_model_count
          : pricedModels,
        routeReason,
        routeReasonLabel: ROUTE_REASON_LABELS[routeReason]
          || (routeSelectable ? "已进入 Agent 模型路由，可在模型分配中选择。" : "尚未满足模型路由条件。"),
        governanceAction: canGovern ? "govern" : routeSelectable ? "suspend" : "none",
        lifecycle,
        canAssign: text(item.provider_type) !== "aws_bedrock" && routeSelectable,
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
