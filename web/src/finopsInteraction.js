import { CUSTOMER_INFRA_LABELS } from "./finopsViewModel.js";

const FILTER_FIELDS = {
  departmentId: { api: "department_id", label: "部门" },
  workspaceId: { api: "workspace_id", label: "工作区" },
  actorRef: { api: "actor_ref", label: "人员" },
  agentId: { api: "agent_id", label: "Agent" },
  model: { api: "model", label: "模型" },
};

const DIMENSION_FIELDS = {
  department: "departmentId",
  workspace: "workspaceId",
  actor: "actorRef",
  agent: "agentId",
  model: "model",
};

const CACHE_STATES = new Set(["hit", "miss", "bypassed", "unavailable"]);
const ASSISTANT_DATA_STATES = new Set(["complete", "partial", "unavailable", "insufficient_data"]);
const ASSISTANT_EVIDENCE_STATES = new Set(["observed", "estimated", "partial", "unavailable"]);
const ASSISTANT_POLICY_TYPES = new Set([
  "error_rate",
  "p95_latency",
  "daily_cost_budget",
  "token_spike",
  "apim_coverage",
  "unpriced_requests",
  "cache_hit_rate",
]);
const SAFE_REQUEST_REF = /^req_[A-Za-z0-9_-]{4,123}$/;


function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}


function bounded(value, length = 160) {
  return String(value ?? "").trim().slice(0, length);
}


function assistantDataStatus(value) {
  const status = bounded(value, 32).toLowerCase();
  if (ASSISTANT_DATA_STATES.has(status)) return status;
  if (["available", "ready", "verified", "observed"].includes(status)) return "complete";
  if (status === "estimated") return "partial";
  return "unavailable";
}


function assistantEvidenceState(value) {
  const state = bounded(value, 32).toLowerCase();
  return ASSISTANT_EVIDENCE_STATES.has(state) ? state : "unavailable";
}


export function assistantFailureMessage(error) {
  const message = error instanceof Error ? error.message : String(error || "");
  if (/timeout|timed out|abort/i.test(message)) {
    return "分析响应超时，请稍后重试。";
  }
  if (/failed to fetch|network|connection|disconnected/i.test(message)) {
    return "暂时无法连接分析服务，请检查网络后重试。";
  }
  return "当前分析未完成，请重试。若问题持续，可清除当前指标后重新提问。";
}


function pushNumber(rows, label, value, format = "number") {
  if (!finite(value)) return;
  rows.push({ label, value, format });
}


export function metricTooltip(metric = {}) {
  const rows = [];
  if (metric.kind === "tokens") {
    pushNumber(rows, "输入 Token", metric.tokens?.input);
    pushNumber(rows, "输出 Token", metric.tokens?.output);
    pushNumber(rows, "缓存输入", metric.tokens?.cachedInput);
    pushNumber(rows, "推理 Token", metric.tokens?.reasoning);
    pushNumber(rows, "Token 总量", metric.tokens?.total);
  } else if (metric.kind === "cache") {
    pushNumber(rows, "缓存命中", metric.cache?.hit);
    pushNumber(rows, "缓存未命中", metric.cache?.miss);
    pushNumber(rows, "绕过缓存", metric.cache?.bypassed);
    pushNumber(rows, "状态不可用", metric.cache?.unavailable);
    pushNumber(rows, "可缓存样本", metric.cache?.eligible);
    pushNumber(rows, "避免 Token", metric.cache?.avoidedTokens);
    pushNumber(rows, "估算节省", metric.cache?.estimatedSavings, "currency");
    if (
      !finite(metric.cache?.avoidedTokens)
      && !finite(metric.cache?.estimatedSavings)
      && metric.cache?.reason === "avoided_tokens_not_recorded"
    ) {
      rows.push({
        label: "节省依据",
        value: "缺少可追溯的避免 Token 与价目证据",
        format: "text",
      });
    }
  } else if (metric.kind === "cost") {
    pushNumber(rows, "估算成本", metric.amount, "currency");
    pushNumber(rows, "已计价请求", metric.pricedRequests);
    pushNumber(rows, "未计价请求", metric.unpricedRequests);
    if (bounded(metric.priceRevision)) {
      rows.push({ label: "价目表版本", value: bounded(metric.priceRevision), format: "text" });
    }
  } else if (metric.kind === "budget") {
    pushNumber(rows, "预算额度", metric.amount, "currency");
    pushNumber(rows, "已使用", metric.usedAmount, "currency");
    pushNumber(rows, "使用比例", metric.usagePct, "percent");
  } else if (metric.kind === "coverage") {
    pushNumber(rows, CUSTOMER_INFRA_LABELS.gatewayCoverage, metric.coveragePct, "percent");
    pushNumber(rows, "调用样本", metric.requests);
  } else if (metric.kind === "quality") {
    pushNumber(rows, "调用样本", metric.requests);
    pushNumber(rows, "成功率", metric.successRatePct, "percent");
    pushNumber(rows, "P50", metric.p50Ms, "duration");
    pushNumber(rows, "P95", metric.p95Ms, "duration");
    pushNumber(rows, "4xx", metric.error4xx);
    pushNumber(rows, "5xx", metric.error5xx);
  } else {
    pushNumber(rows, "调用", metric.requests);
    pushNumber(rows, "成功率", metric.successRatePct, "percent");
    pushNumber(rows, "Token", metric.tokens?.total);
    pushNumber(rows, "估算成本", metric.amount, "currency");
    pushNumber(rows, "P95", metric.p95Ms, "duration");
  }
  return {
    title: bounded(metric.label, 120) || "指标详情",
    rows,
    dataStatus: bounded(metric.dataStatus, 32) || "unavailable",
    evidenceState: bounded(metric.evidenceState, 32) || "unavailable",
  };
}


export function metricContext(metric = {}, scope = {}) {
  const filters = {};
  Object.entries(FILTER_FIELDS).forEach(([field, descriptor]) => {
    const value = bounded(scope?.filters?.[field]);
    if (value) filters[descriptor.api] = value;
  });
  const window = {};
  const from = bounded(scope?.window?.from, 40);
  const to = bounded(scope?.window?.to, 40);
  if (from) window.from = from;
  if (to) window.to = to;
  const result = {
    metric_id: bounded(metric.id, 96),
    label: bounded(metric.label, 120),
    value: finite(metric.value) ? metric.value : bounded(metric.value, 120) || null,
    unit: bounded(metric.unit, 24),
    dimension: bounded(metric.dimension, 48) || null,
    dimension_value: bounded(metric.dimensionValue, 160) || null,
    window,
    filters,
    data_status: assistantDataStatus(metric.dataStatus),
    evidence_state: assistantEvidenceState(metric.evidenceState),
  };
  const cacheState = bounded(metric.cacheState, 24);
  if (CACHE_STATES.has(cacheState)) result.cache_state = cacheState;
  const policyType = bounded(metric.policyType ?? metric.policy_type, 64);
  if (ASSISTANT_POLICY_TYPES.has(policyType)) result.policy_type = policyType;
  const evidenceRefs = Array.isArray(metric.evidenceRefs ?? metric.evidence_refs)
    ? [...new Set((metric.evidenceRefs ?? metric.evidence_refs)
      .map((value) => bounded(value, 128))
      .filter((value) => SAFE_REQUEST_REF.test(value)))]
      .slice(0, 3)
    : [];
  if (evidenceRefs.length) result.evidence_refs = evidenceRefs;
  return result;
}


export function contextualAssistantQuestion(context = {}) {
  const label = bounded(context.label, 120) || "当前运营指标";
  const unit = bounded(context.unit, 24);
  const rawValue = context.value;
  const value = finite(rawValue)
    ? `${rawValue}${unit}`
    : bounded(rawValue, 120)
      ? `${bounded(rawValue, 120)}${unit}`
      : "未记录";
  const dimension = bounded(context.dimension, 48);
  const dimensionValue = bounded(context.dimension_value, 160);
  const dimensionLabel = {
    model: "模型",
    agent: "Agent",
    department: "部门",
    workspace: "工作区",
    actor: "人员",
  }[dimension] || dimension;
  const suffix = dimensionLabel && dimensionValue
    ? `，${dimensionLabel}为 ${dimensionValue}`
    : "";
  return `请分析“${label}”（当前值 ${value}${suffix}）：说明结论、证据依据、影响、建议和判断边界。`.slice(0, 600);
}


export function applyDimensionFilter(filters = {}, selection = {}) {
  const field = DIMENSION_FIELDS[selection.dimension];
  if (!field) return { ...filters };
  return {
    ...filters,
    [field]: bounded(selection.value),
  };
}


export function filterChips(filters = {}) {
  return Object.entries(FILTER_FIELDS)
    .map(([key, descriptor]) => ({
      key,
      label: descriptor.label,
      value: bounded(filters[key]),
    }))
    .filter((item) => item.value);
}


export function previousEqualWindow(window = {}) {
  const from = Date.parse(window.from);
  const to = Date.parse(window.to);
  if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to) return null;
  const duration = to - from;
  return {
    from: new Date(from - duration).toISOString(),
    to: new Date(from).toISOString(),
  };
}
