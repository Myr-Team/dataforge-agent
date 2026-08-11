const SECRET_KEY = /^(authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|password|secret|connection[-_]?string|sas|sig)$/i;

function text(value) {
  return String(value ?? "").trim();
}

export function safeTraceValue(value, depth = 0) {
  if (depth > 6) return "[truncated]";
  if (Array.isArray(value)) return value.slice(0, 100).map((item) => safeTraceValue(item, depth + 1));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).slice(0, 100).map(([key, item]) => [
      key,
      SECRET_KEY.test(key) ? "[redacted]" : safeTraceValue(item, depth + 1),
    ]));
  }
  return value;
}

export function prettyTraceJson(value) {
  return JSON.stringify(safeTraceValue(value), null, 2);
}

const RESULT_CACHE_LABELS = {
  hit: "命中",
  miss: "未命中",
  bypassed: "已绕过",
  unavailable: "未记录",
};

const PROVIDER_CACHE_LABELS = {
  hit: "命中",
  partial_hit: "部分命中",
  miss: "未命中",
  unavailable: "未记录",
};

const GATEWAY_LABELS = {
  apim_governed: "统一入口已治理",
  app_observed: "应用侧已观测",
  unmanaged: "未纳入统一入口",
  unknown: "来源未记录",
};

function nonnegativeInteger(value) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : null;
}

function cacheEvidence(detail = {}) {
  const resultCache = detail.result_cache && typeof detail.result_cache === "object"
    ? detail.result_cache
    : detail.cache && typeof detail.cache === "object"
      ? detail.cache
      : {};
  const resultState = text(resultCache.state) || "unavailable";
  const resultBits = [];
  if (resultCache.eligible === true) resultBits.push("符合缓存条件");
  else if (resultCache.eligible === false) resultBits.push("本次不适用");
  const policyRevision = nonnegativeInteger(resultCache.policy_revision);
  if (policyRevision !== null) resultBits.push(`策略 v${policyRevision}`);

  const providerCache = detail.provider_cache && typeof detail.provider_cache === "object"
    ? detail.provider_cache
    : {};
  const providerState = text(providerCache.state) || "unavailable";
  const providerBits = [];
  const hitTokens = nonnegativeInteger(providerCache.hit_tokens);
  const missTokens = nonnegativeInteger(providerCache.miss_tokens);
  const hitRate = Number(providerCache.hit_rate_pct);
  if (hitTokens !== null) providerBits.push(`命中 ${hitTokens.toLocaleString("zh-CN")}`);
  if (missTokens !== null) providerBits.push(`未命中 ${missTokens.toLocaleString("zh-CN")}`);
  if (Number.isFinite(hitRate)) providerBits.push(`${hitRate.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`);

  const gatewayState = text(detail.gateway_coverage) || "unknown";
  return {
    result: {
      state: resultState,
      label: RESULT_CACHE_LABELS[resultState] || "未记录",
      detail: resultBits.join(" · ") || "没有可用的结果缓存证据",
    },
    provider: {
      state: providerState,
      label: PROVIDER_CACHE_LABELS[providerState] || "未记录",
      detail: providerBits.join(" · ") || "模型服务未返回缓存 Token 明细",
    },
    gateway: {
      state: gatewayState,
      label: GATEWAY_LABELS[gatewayState] || GATEWAY_LABELS.unknown,
    },
  };
}

function traceModelLabel(detail = {}) {
  const model = text(detail.model_id) || text(detail.deployment) || text(detail.model);
  const provider = text(detail.provider_type);
  const providerLabel = provider === "deepseek" ? "DeepSeek" : provider === "azure_foundry" ? "Azure Foundry" : "";
  return [model, providerLabel].filter(Boolean).join(" · ") || "模型未记录";
}

export function traceExplorerRows(trace = []) {
  return (Array.isArray(trace) ? trace : [])
    .filter((item) => item && typeof item === "object")
    .map((item, index) => {
      const detail = item.detail && typeof item.detail === "object" ? item.detail : {};
      const agentReference = detail.agent_reference && typeof detail.agent_reference === "object"
        ? detail.agent_reference
        : null;
      const event = text(item.event) || "event";
      const agent = text(item.agent) || text(agentReference?.name) || "系统";
      const external = Boolean(agentReference)
        || /external|hosted|mcp/i.test(`${event} ${text(detail.execution_kind)} ${text(detail.provider)}`);
      return {
        id: `${Number.isInteger(item.index) ? item.index : index}:${event}:${agent}`,
        index: Number.isInteger(item.index) ? item.index + 1 : index + 1,
        event,
        agent,
        role: text(item.role),
        status: text(item.status) || "unknown",
        time: text(item.time),
        durationMs: Number.isFinite(Number(item.duration_ms)) ? Number(item.duration_ms) : null,
        source: text(item.source) || "run_store.steps",
        external,
        modelLabel: traceModelLabel(detail),
        cacheEvidence: cacheEvidence(detail),
        payload: safeTraceValue(item),
      };
    });
}
