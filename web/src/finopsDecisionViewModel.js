const EVIDENCE_STATE_LABELS = Object.freeze({
  observed: "已观测",
  estimated: "情景测算",
  verified: "已验证",
  partial: "部分证据",
  unavailable: "暂不可用",
  not_recorded: "未记录",
  complete: "已确认",
  available: "可用",
});

const DECISION_STATES = new Set([
  "verified",
  "scenario_positive_unverified",
  "evidence_incomplete",
  "prioritized",
  "no_current_risk",
]);

const POLICY_LABELS = Object.freeze({
  daily_cost_budget: "成本预算",
  token_spike: "Token 异常增长",
  unpriced_requests: "计价覆盖",
  p95_latency: "响应时延",
  error_rate: "调用失败率",
  cache_hit_rate: "缓存效率",
  apim_coverage: "统一入口治理覆盖",
  other: "运营风险",
});

const DOMAIN_LABELS = Object.freeze({
  cost: "成本",
  experience: "体验",
  efficiency: "效率",
  governance: "治理",
});

const CUSTOMER_FACING_FINOPS_TERMS = Object.freeze({
  gateway_coverage: "入口治理覆盖",
  app_observed: "应用侧已观测",
  unmanaged: "未纳入统一入口",
  unknown: "来源待确认",
  cache_state: "缓存状态",
  tokens_total: "Token 总量",
  provider_5xx: "模型服务异常",
});

const CUSTOMER_FACING_FINOPS_TERM_PATTERN = /(^|[^A-Za-z0-9_.:/-])(gateway_coverage|app_observed|unmanaged|unknown|cache_state|tokens_total|provider_5xx)(?![A-Za-z0-9_.:/-])/g;

const EVIDENCE_SIGNAL_LABELS = Object.freeze({
  latency_ms: "响应时延",
  request_status: "调用状态",
  pricing_status: "计价状态",
  cache_state: "缓存状态",
  gateway_coverage: "入口治理覆盖",
  tokens_total: "Token 总量",
  estimated_cost: "估算成本",
});

const EVIDENCE_VALUE_LABELS = Object.freeze({
  succeeded: "调用成功",
  failed: "调用失败",
  hit: "缓存命中",
  miss: "缓存未命中",
  bypassed: "未使用缓存",
  unavailable: "状态暂不可用",
  priced: "已计价",
  unpriced: "未计价",
  estimated: "估算值",
  apim_governed: "已纳入统一入口",
  app_observed: "应用侧已观测",
  unmanaged: "未纳入统一入口",
  unknown: "来源待确认",
});

const EVIDENCE_STRING_VALUES_BY_SIGNAL = Object.freeze({
  request_status: Object.freeze(["succeeded", "failed"]),
  cache_state: Object.freeze(["hit", "miss", "bypassed", "unavailable"]),
  pricing_status: Object.freeze(["priced", "unpriced", "estimated", "unavailable"]),
  gateway_coverage: Object.freeze(["apim_governed", "app_observed", "unmanaged", "unknown", "unavailable"]),
});

const EVIDENCE_UNITS_BY_SIGNAL = Object.freeze({
  latency_ms: Object.freeze(["ms", "milliseconds"]),
  request_status: Object.freeze(["status"]),
  pricing_status: Object.freeze(["status"]),
  cache_state: Object.freeze(["state"]),
  gateway_coverage: Object.freeze(["state"]),
  tokens_total: Object.freeze(["token", "tokens", "Token"]),
  estimated_cost: Object.freeze(["USD"]),
});

const EVIDENCE_UNIT_LABELS = Object.freeze({
  USD: "USD",
  ratio: "ROI",
  percent: "%",
  percentage_point: "个百分点",
  milliseconds: "毫秒",
  ms: "毫秒",
  requests: "次请求",
  token: "Token",
  tokens: "Token",
  Token: "Token",
  status: "",
  state: "",
});

const LEVEL_LABELS = Object.freeze({
  low: "低",
  medium: "中",
  high: "高",
  unavailable: "待确认",
});

const UNIT_LABELS = Object.freeze({
  USD: "USD",
  ratio: "ROI",
  percent: "%",
  percentage_point: "个百分点",
  milliseconds: "毫秒",
  ms: "毫秒",
  requests: "次请求",
  tokens: "Token",
  Token: "Token",
  "次调用": "次调用",
  "个产物": "个产物",
  "项结果": "项结果",
  "项已验证结果": "项已验证结果",
  "小时": "小时",
  "小时/月": "小时/月",
  "月": "月",
  "USD per successful request": "USD / 成功调用",
});

const ROI_METRIC_COPY = Object.freeze({
  monthly_total_cost: {
    label: "AI 运营总投入",
    explanation: "可包含实施摊销、固定运营成本与当前模型成本；不等同于请求级模型使用成本。",
  },
});

const REMEDIATION_STATUS_LABELS = Object.freeze({
  draft: "草案",
  reviewed: "已复核",
  pending_approval: "待审批",
  promoted: "已转为治理动作草案",
  closed: "已关闭",
});

const ACTION_KIND_LABELS = Object.freeze({
  cache_policy: "缓存策略",
  model_route: "模型路由",
  price_mapping: "计价映射",
  budget_notification: "预算提醒",
  investigation: "运营调查",
});

const CHANGE_FIELD_LABELS = Object.freeze({
  ttl_seconds: "缓存有效期",
  enabled: "启用状态",
  deployment: "模型部署",
  price_mapping: "计价映射",
  notification_threshold: "提醒阈值",
  investigation_scope: "调查范围",
});

const VERIFICATION_METRIC_LABELS = Object.freeze({
  cache_hit_rate_pct: "缓存命中率",
  unit_cost: "单位成本",
  result_consistency_pct: "结果一致性",
  success_rate_pct: "成功率",
  p95_latency_ms: "P95 时延",
  pricing_coverage_pct: "计价覆盖率",
});

const OPERATOR_LABELS = Object.freeze({
  gte: "不低于",
  lte: "不高于",
  no_worse_than_pct: "劣化不超过",
});

const SAFE_REFERENCE = /^(?:req_|run-|run_|outcome-|outcome_|event_)[A-Za-z0-9_-]{0,120}$/;
const SAFE_IDENTIFIER = /^[A-Za-z0-9_-]{1,128}$/;
const SAFE_REVISION = /^[A-Za-z0-9._:-]{1,128}$/;
const APPLICABLE_ANOMALY_ACTIONS = new Set(["acknowledge", "suppress"]);


function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}


function records(value) {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}


function boundedText(value, maximum = 160) {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, maximum);
}


function customerFacingFinOpsProse(value, maximum) {
  const text = boundedText(value, maximum);
  return text.replace(
    CUSTOMER_FACING_FINOPS_TERM_PATTERN,
    (match, prefix, term) => `${prefix}${CUSTOMER_FACING_FINOPS_TERMS[term]}`,
  );
}


function customerFacingFinOpsLabel(value, maximum) {
  const text = boundedText(value, maximum);
  return CUSTOMER_FACING_FINOPS_TERMS[text] || text;
}


function boundedTexts(value, limit = 8, maximum = 200) {
  if (typeof value === "string") {
    const item = boundedText(value, maximum);
    return item ? [item] : [];
  }
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => typeof item === "string")
    .map((item) => boundedText(item, maximum))
    .filter(Boolean)
    .slice(0, limit);
}


function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}


function nonNegativeNumber(value) {
  const number = finiteNumber(value);
  return number !== null && number >= 0 ? number : null;
}


function safeIdentifier(value) {
  const identifier = boundedText(value, 128);
  return SAFE_IDENTIFIER.test(identifier) ? identifier : "";
}


function safeRevision(value) {
  const revision = boundedText(value, 128);
  return SAFE_REVISION.test(revision) ? revision : "";
}


function safeReference(value) {
  const reference = boundedText(value, 128);
  return SAFE_REFERENCE.test(reference) ? reference : "";
}


function evidenceState(value) {
  const raw = boundedText(value, 32).toLowerCase();
  if (!raw) {
    return { key: "unavailable", label: EVIDENCE_STATE_LABELS.unavailable, known: true };
  }
  if (Object.hasOwn(EVIDENCE_STATE_LABELS, raw)) {
    return { key: raw, label: EVIDENCE_STATE_LABELS[raw], known: true };
  }
  return { key: "unavailable", label: "状态待确认", known: false };
}


function safeDecision(raw) {
  const source = isRecord(raw) ? raw : {};
  const evidence = evidenceState(source.evidence_state ?? source.status);
  const state = boundedText(source.state, 48);
  return {
    state: DECISION_STATES.has(state) ? state : "unavailable",
    title: boundedText(source.title, 160),
    summary: boundedText(source.summary, 320),
    status: evidence.key,
    badge: evidence.label,
    description: boundedText(source.summary, 320) || "当前没有足够证据形成判断。",
  };
}


function safeUnit(value) {
  const key = boundedText(value, 32);
  return Object.hasOwn(UNIT_LABELS, key) ? key : "";
}


function formatNumber(value, maximumFractionDigits = 2) {
  return new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits,
    minimumFractionDigits: 0,
  }).format(value);
}


function unavailableValueLabel(status) {
  return status === "not_recorded" ? "未记录" : "暂不可用";
}


function formatValue(value, unit, status) {
  if (value === null) return unavailableValueLabel(status);
  if (unit === "USD") {
    const maximumFractionDigits = value !== 0 && Math.abs(value) < 0.01
      ? 5
      : value !== 0 && Math.abs(value) < 1
        ? 4
        : 2;
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits,
    }).format(value);
  }
  if (unit === "ratio") {
    return new Intl.NumberFormat("zh-CN", {
      style: "percent",
      maximumFractionDigits: 1,
    }).format(value);
  }
  if (unit === "percent") return `${formatNumber(value, 1)}%`;
  if (unit === "ms" || unit === "milliseconds") return `${formatNumber(value, 1)} ms`;
  const suffix = UNIT_LABELS[unit] || "";
  return suffix ? `${formatNumber(value)} ${suffix}` : formatNumber(value);
}


function safeMetric(raw) {
  if (!isRecord(raw)) return null;
  const id = safeIdentifier(raw.id);
  if (!id) return null;
  const evidence = evidenceState(raw.status);
  const value = finiteNumber(raw.value);
  const unit = safeUnit(raw.unit);
  const copy = ROI_METRIC_COPY[id];
  return {
    id,
    label: copy?.label || boundedText(raw.label, 80) || "运营指标",
    value,
    unit,
    unitLabel: UNIT_LABELS[unit] || "",
    status: evidence.key,
    badge: evidence.label,
    valueLabel: formatValue(value, unit, evidence.key),
    explanation: copy?.explanation || boundedText(raw.explanation, 240),
  };
}


function safeMetrics(value) {
  return records(value).map(safeMetric).filter(Boolean).slice(0, 12);
}


function visualPercent(value, minimum = 1, maximum = 3) {
  const bounded = Math.min(maximum, Math.max(minimum, value));
  return ((bounded - minimum) / (maximum - minimum)) * 100;
}


function proportionalItems(items) {
  const groups = new Map();
  for (const item of items) {
    const scaleGroup = item.unit || `independent-${item.id}`;
    const current = groups.get(scaleGroup) || 0;
    groups.set(scaleGroup, item.value === null ? current : Math.max(current, Math.abs(item.value)));
  }
  return items.map((item) => {
    const scaleGroup = item.unit || `independent-${item.id}`;
    const maximum = groups.get(scaleGroup) || 0;
    const sign = item.value === null ? null : Math.sign(item.value);
    const direction = sign === null ? "unavailable" : sign < 0 ? "negative" : sign > 0 ? "positive" : "zero";
    return {
      ...item,
      scaleGroup,
      sign,
      direction,
      directionLabel: {
        negative: "负值",
        positive: "正值",
        zero: "零值",
        unavailable: "方向不可用",
      }[direction],
      formulaValueLabel: item.value === null
        ? item.valueLabel
        : formatValue(direction === "negative" ? Math.abs(item.value) : item.value, item.unit, item.status),
      barPct: item.value === null || maximum === 0
        ? 0
        : Math.min(100, (Math.abs(item.value) / maximum) * 100),
    };
  });
}


function safeBridge(raw, metrics) {
  const source = isRecord(raw) ? raw : {};
  const explicit = records(source.items).map(safeMetric).filter(Boolean);
  const metricItems = explicit.length ? explicit : metrics
    .filter((item) => [
      "monthly_benefit",
      "monthly_total_cost",
      "monthly_net_benefit",
      "roi_ratio",
    ].includes(item.id))
    .map((item) => {
      if (item.id !== "monthly_total_cost" || item.value === null) return item;
      const value = -Math.abs(item.value);
      return { ...item, value, valueLabel: formatValue(value, item.unit, item.status) };
    });
  const items = proportionalItems(metricItems.slice(0, 8));
  const paybackMonths = finiteNumber(source.payback_months);
  return {
    items,
    formulaRevision: safeRevision(source.formula_revision),
    scenarioId: safeIdentifier(source.scenario_id),
    paybackMonths,
    paybackLabel: paybackMonths === null ? "暂不可用" : `${formatNumber(paybackMonths, 1)} 月`,
    description: items.length
      ? `价值构成包含 ${items.length} 项服务端值；仅相同单位共享比例尺，负值在零轴左侧展示。`
      : "当前没有可展示的价值构成。",
  };
}


function safeEvidenceRefs(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(safeReference).filter(Boolean))].slice(0, 20);
}


function safeMaturity(raw) {
  const source = isRecord(raw) ? raw : {};
  const scorePct = finiteNumber(source.score_pct);
  const stages = records(source.stages).flatMap((stage) => {
    const id = safeIdentifier(stage.id);
    if (!id) return [];
    const status = evidenceState(stage.status);
    const value = finiteNumber(stage.value);
    const unit = safeUnit(stage.unit);
    const evidenceCount = nonNegativeNumber(stage.evidence_count);
    return [{
      id,
      label: boundedText(stage.label, 80) || "证据阶段",
      value,
      unit,
      unitLabel: UNIT_LABELS[unit] || "",
      valueLabel: formatValue(value, unit, status.key),
      status: status.key,
      badge: status.label,
      evidenceCount,
      evidenceGap: boundedText(stage.evidence_gap, 240),
      evidenceRefs: safeEvidenceRefs(stage.evidence_refs),
      complete: stage.complete === true,
      description: [
        `${boundedText(stage.label, 80) || "证据阶段"}：${formatValue(value, unit, status.key)}`,
        status.label,
        evidenceCount === null ? "证据数量未记录" : `${formatNumber(evidenceCount, 0)} 条证据`,
        boundedText(stage.evidence_gap, 240),
      ].filter(Boolean).join("；"),
    }];
  }).slice(0, 8);
  return {
    scorePct,
    visualScorePct: scorePct === null ? null : Math.min(100, Math.max(0, scorePct)),
    scoreLabel: scorePct === null ? "暂不可用" : `${formatNumber(scorePct, 1)}%`,
    formulaRevision: safeRevision(source.formula_revision),
    stages,
    description: stages.length
      ? `证据成熟度包含 ${stages.length} 个服务端阶段。`
      : "当前没有可展示的证据阶段。",
  };
}


function capabilityList(source, keys) {
  for (const key of keys) {
    if (Object.hasOwn(source, key)) return boundedTexts(source[key]);
  }
  return [];
}


function safeCapability(raw) {
  const source = isRecord(raw) ? raw : {};
  return {
    platformConfirmed: capabilityList(source, ["platform_confirmed", "platformConfirmed", "平台自动确认"]),
    businessVerification: capabilityList(source, ["business_verification", "businessVerification", "业务侧补充验证"]),
    governanceBoundary: capabilityList(source, ["governance_boundary", "governanceBoundary", "治理边界"]),
  };
}


function safeScenario(raw) {
  if (!isRecord(raw)) return null;
  const id = safeIdentifier(raw.scenario_id);
  if (!id) return null;
  const status = evidenceState(raw.status);
  const result = isRecord(raw.result) ? raw.result : {};
  const projected = {};
  for (const [key, unit] of [
    ["monthly_benefit", "USD"],
    ["monthly_total_cost", "USD"],
    ["monthly_net_benefit", "USD"],
    ["roi_ratio", "ratio"],
    ["payback_months", "月"],
  ]) {
    const value = finiteNumber(result[key]);
    projected[key] = value;
    projected[`${key}_label`] = formatValue(value, unit, status.key);
  }
  return {
    id,
    status: status.key,
    badge: status.key === "estimated" ? "情景测算" : status.label,
    formulaRevision: safeRevision(result.formula_revision),
    result: projected,
  };
}


function safeVerifiedRoi(raw) {
  const source = isRecord(raw) ? raw : {};
  const status = evidenceState(source.status);
  const value = status.key === "verified" ? finiteNumber(source.value) : null;
  return {
    value,
    label: value === null ? "证据不足" : formatValue(value, "ratio", "verified"),
    status: value === null ? "unavailable" : "verified",
    badge: value === null ? "结果待验证" : EVIDENCE_STATE_LABELS.verified,
  };
}


function safeTrend(value) {
  return records(value).flatMap((item, index) => {
    const number = finiteNumber(item.value);
    const status = evidenceState(item.status);
    const unit = safeUnit(item.unit || item.currency);
    const period = boundedText(item.period, 64);
    const label = boundedText(item.label, 80) || period;
    if (!label) return [];
    return [{
      id: `${safeIdentifier(item.id) || "trend"}-${index}`,
      label,
      period,
      value: number,
      unit,
      unitLabel: UNIT_LABELS[unit] || "",
      valueLabel: formatValue(number, unit, status.key),
      status: status.key,
      badge: status.label,
    }];
  }).slice(0, 60);
}


export function roiDecisionView(payload) {
  const source = isRecord(payload) ? payload : {};
  const metrics = safeMetrics(source.metrics);
  const verified = safeVerifiedRoi(source.verified_roi);
  return {
    decision: safeDecision(source.decision),
    metrics,
    valueBridge: safeBridge(source.value_bridge, metrics),
    evidenceMaturity: safeMaturity(source.evidence_maturity),
    unitEconomicsTrend: safeTrend(source.unit_economics_trend),
    verifiedRoiValue: verified.value,
    verifiedRoiLabel: verified.label,
    verifiedRoiStatus: verified.status,
    verifiedRoiBadge: verified.badge,
    capability: safeCapability(source.capability_explanation),
    scenarios: records(source.scenarios).map(safeScenario).filter(Boolean).slice(0, 20),
    evidenceGaps: boundedTexts(source.evidence_gaps, 12, 240),
  };
}


function safePolicy(value) {
  const policy = boundedText(value, 48);
  return Object.hasOwn(POLICY_LABELS, policy) ? policy : "other";
}


function safeDomain(value) {
  const domain = boundedText(value, 32);
  return Object.hasOwn(DOMAIN_LABELS, domain) ? domain : "governance";
}


function level(value) {
  const key = boundedText(value, 24).toLowerCase();
  return Object.hasOwn(LEVEL_LABELS, key) ? key : "unavailable";
}


function pointRadius(value, minimum, maximum) {
  if (minimum === maximum) return value > 0 ? 15 : 8;
  const normalized = (Math.sqrt(value) - Math.sqrt(minimum))
    / (Math.sqrt(maximum) - Math.sqrt(minimum));
  return 8 + (Math.min(1, Math.max(0, normalized)) * 16);
}


function matrixPoints(value) {
  const raw = records(value).flatMap((item) => {
    const id = safeIdentifier(item.id ?? item.opportunity_id);
    const xConfidence = finiteNumber(item.x_confidence);
    const yImpact = finiteNumber(item.y_impact);
    if (!id || xConfidence === null || yImpact === null) return [];
    const policy = safePolicy(item.policy_type);
    const domain = safeDomain(item.risk_domain);
    const bubbleSize = nonNegativeNumber(item.bubble_size) ?? 0;
    const xState = evidenceState(item.x_confidence_state);
    const yState = evidenceState(item.y_impact_state);
    return [{
      id,
      label: boundedText(item.title, 100) || POLICY_LABELS[policy],
      policy,
      policyLabel: POLICY_LABELS[policy],
      domain,
      domainLabel: DOMAIN_LABELS[domain],
      xConfidence,
      yImpact,
      xStatus: xState.key,
      yStatus: yState.key,
      bubbleSize,
      evidenceRefs: safeEvidenceRefs(item.evidence_refs),
      visualX: visualPercent(xConfidence),
      visualY: visualPercent(yImpact),
    }];
  }).slice(0, 40);
  const sizes = raw.map((item) => item.bubbleSize);
  const minimum = sizes.length ? Math.min(...sizes) : 0;
  const maximum = sizes.length ? Math.max(...sizes) : 0;
  return raw.map((item) => ({
    ...item,
    radius: pointRadius(item.bubbleSize, minimum, maximum),
    accessibleLabel: `${item.label}；证据置信度 ${formatNumber(item.xConfidence)}；运营严重度 ${formatNumber(item.yImpact)}；评估样本量 ${formatNumber(item.bubbleSize, 0)} 次请求`,
  }));
}


function expectedImpact(raw) {
  const source = isRecord(raw) ? raw : {};
  const status = evidenceState(source.status);
  const value = finiteNumber(source.value ?? source.amount);
  const currency = boundedText(source.currency ?? source.unit, 16) === "USD" ? "USD" : "";
  const usable = value !== null && !["unavailable", "not_recorded"].includes(status.key);
  return {
    value: usable ? value : null,
    currency,
    status: usable ? status.key : "unavailable",
    label: usable ? formatValue(value, currency, status.key) : "待验证",
  };
}


function safeOpportunity(raw) {
  if (!isRecord(raw)) return null;
  const id = safeIdentifier(raw.id ?? raw.opportunity_id);
  if (!id) return null;
  const policy = safePolicy(raw.policy_type);
  const domain = safeDomain(raw.risk_domain);
  const confidence = level(raw.confidence);
  const impact = level(raw.impact);
  const effort = level(raw.effort);
  const expected = expectedImpact(raw.expected_impact ?? {
    value: null,
    status: "unavailable",
  });
  const anomalyStatus = boundedText(raw.anomaly_status, 32);
  return {
    id,
    label: boundedText(raw.title, 100) || POLICY_LABELS[policy],
    summary: customerFacingFinOpsProse(raw.recommendation ?? raw.summary, 260),
    policy,
    policyLabel: POLICY_LABELS[policy],
    domain,
    domainLabel: DOMAIN_LABELS[domain],
    confidence,
    confidenceLabel: LEVEL_LABELS[confidence],
    impact,
    impactLevelLabel: LEVEL_LABELS[impact],
    effort,
    effortLabel: LEVEL_LABELS[effort],
    sampleCount: nonNegativeNumber(raw.sample_count),
    evidenceRefs: safeEvidenceRefs(raw.evidence_refs),
    expectedImpact: expected,
    impactLabel: expected.label,
    baseVersion: safeRevision(raw.base_version),
    anomalyId: safeIdentifier(raw.anomaly_id),
    anomalyStatus: ["open", "acknowledged", "suppressed", "resolved"].includes(anomalyStatus)
      ? anomalyStatus
      : "",
    applicableActions: Array.isArray(raw.applicable_actions)
      ? [...new Set(raw.applicable_actions.filter((item) => APPLICABLE_ANOMALY_ACTIONS.has(item)))].slice(0, 2)
      : [],
  };
}


function safePriorities(value) {
  return records(value).map(safeOpportunity).filter(Boolean).slice(0, 30);
}


function portfolioView(value, metadata) {
  const items = safePriorities(value);
  const sourceById = new Map(
    records(value).map((item) => [safeIdentifier(item.id ?? item.opportunity_id), item]),
  );
  const rawPoints = items.flatMap((item) => {
    const raw = sourceById.get(item.id) || {};
    const x = finiteNumber(raw.x_effort ?? raw.x);
    const y = finiteNumber(raw.y_value_impact ?? raw.y);
    const bubbleSize = nonNegativeNumber(raw.bubble_size ?? raw.affected_scope);
    if (x === null || y === null || bubbleSize === null) return [];
    return [{
      ...item,
      x,
      y,
      bubbleSize,
      visualX: visualPercent(x),
      visualY: visualPercent(y),
    }];
  });
  const sizes = rawPoints.map((item) => item.bubbleSize);
  const minimum = sizes.length ? Math.min(...sizes) : 0;
  const maximum = sizes.length ? Math.max(...sizes) : 0;
  return {
    items,
    points: rawPoints.map((item) => ({
      ...item,
      radius: pointRadius(item.bubbleSize, minimum, maximum),
      accessibleLabel: `${item.label}；${metadata.xAxis || "横轴"} ${formatNumber(item.x)}；${metadata.yAxis || "纵轴"} ${formatNumber(item.y)}；${metadata.size || "点大小"} ${formatNumber(item.bubbleSize, 0)}`,
    })),
  };
}


function safePortfolioMetadata(raw) {
  const source = isRecord(raw) ? raw : {};
  const values = {
    xAxis: source.x_axis === "effort" ? "实施难度" : "",
    yAxis: source.y_axis === "value_impact" ? "价值影响" : "",
    size: ["sample_count", "affected_scope"].includes(source.size) ? "评估样本量" : "",
    color: source.color === "risk_domain" ? "风险域" : "",
  };
  const available = Object.values(values).filter(Boolean).length;
  return {
    ...values,
    status: available === 4 ? "available" : available ? "partial" : "unavailable",
  };
}


function safeRiskDomains(value) {
  return records(value).flatMap((item) => {
    const id = boundedText(item.id, 32);
    if (!Object.hasOwn(DOMAIN_LABELS, id)) return [];
    const count = nonNegativeNumber(item.count);
    return [{ id, label: DOMAIN_LABELS[id], count }];
  }).slice(0, 4);
}


function safeEvidenceSummaries(value) {
  return records(value).flatMap((item) => {
    const requestRef = safeReference(item.request_ref);
    if (!requestRef) return [];
    const signal = isRecord(item.signal) ? item.signal : {};
    const technical = isRecord(item.technical_refs) ? item.technical_refs : {};
    const metricKey = boundedText(signal.metric, 80);
    const metricLabel = EVIDENCE_SIGNAL_LABELS[metricKey] || "运营信号";
    const numericSignalValue = finiteNumber(signal.value);
    const rawSignalValue = boundedText(signal.value, 48).toLowerCase();
    const rawUnit = boundedText(signal.unit, 32);
    const allowedUnits = EVIDENCE_UNITS_BY_SIGNAL[metricKey] || [];
    const unitCompatible = allowedUnits.includes(rawUnit);
    const allowedStringValues = EVIDENCE_STRING_VALUES_BY_SIGNAL[metricKey] || [];
    const stringValueCompatible = unitCompatible && allowedStringValues.includes(rawSignalValue);
    const numericValueCompatible = unitCompatible
      && !Object.hasOwn(EVIDENCE_STRING_VALUES_BY_SIGNAL, metricKey)
      && numericSignalValue !== null;
    const signalValue = numericValueCompatible ? numericSignalValue : null;
    const stringValueLabel = stringValueCompatible
      ? EVIDENCE_VALUE_LABELS[rawSignalValue]
      : "";
    const unit = numericValueCompatible || stringValueCompatible ? rawUnit : "";
    let valueLabel = "暂不可用";
    if (signalValue !== null) {
      if (["ms", "milliseconds"].includes(unit)) valueLabel = `${formatNumber(signalValue, 1)} ${EVIDENCE_UNIT_LABELS[unit]}`;
      else if (unit === "USD" || unit === "ratio" || unit === "percent") valueLabel = formatValue(signalValue, unit, "observed");
      else {
        const suffix = EVIDENCE_UNIT_LABELS[unit] || "";
        valueLabel = suffix ? `${formatNumber(signalValue)} ${suffix}` : formatNumber(signalValue);
      }
    } else if (stringValueLabel) {
      valueLabel = stringValueLabel;
    }
    return [{
      requestRef,
      requestName: boundedText(item.request_name, 120),
      operation: boundedText(item.operation, 80),
      modelLabel: boundedText(item.model_label, 120),
      signal: {
        metric: metricLabel,
        value: signalValue,
        unit,
        valueLabel,
      },
      latencyMs: nonNegativeNumber(item.latency_ms),
      cacheState: ["hit", "miss", "bypassed", "unavailable"].includes(item.cache_state)
        ? item.cache_state
        : "unavailable",
      status: ["succeeded", "failed"].includes(item.status) ? item.status : "unavailable",
      errorCategory: customerFacingFinOpsLabel(item.error_category, 64),
      visibleAnswerSummary: boundedText(item.visible_answer_summary, 400),
      technical: {
        requestRef: safeReference(technical.request_ref),
        runId: safeIdentifier(technical.run_id),
        traceId: safeIdentifier(technical.trace_id),
        correlationId: safeIdentifier(technical.correlation_id),
      },
    }];
  }).slice(0, 30);
}


function safeDraftScope(raw) {
  const source = isRecord(raw) ? raw : {};
  return {
    workspaceId: safeIdentifier(source.workspace_id),
    agentId: safeIdentifier(source.agent_id),
    model: boundedText(source.model, 120),
    operation: boundedText(source.operation, 80),
  };
}


function safeDraftSummaries(value) {
  return records(value).map((item) => ({
    title: boundedText(item.title, 120),
    label: boundedText(item.label, 80),
    summary: boundedText(item.summary, 240),
    state: boundedText(item.state, 48),
    status: Object.hasOwn(REMEDIATION_STATUS_LABELS, item.status)
      ? item.status
      : "unavailable",
  })).slice(0, 20);
}


function safeGovernanceCapability(raw) {
  const source = isRecord(raw) ? raw : {};
  const allowedExecutors = new Set(["cache_policy", "budget_policy", "routing_policy", "pricing_policy"]);
  return {
    readEnabled: source.read_enabled === true,
    draftEnabled: source.draft_enabled === true,
    actionsEnabled: source.actions_enabled === true,
    typedExecutors: Array.isArray(source.typed_executors)
      ? [...new Set(source.typed_executors.filter((item) => allowedExecutors.has(item)))].slice(0, 4)
      : [],
  };
}


export function riskDecisionView(payload) {
  const source = isRecord(payload) ? payload : {};
  const portfolioMetadata = safePortfolioMetadata(source.portfolio_metadata);
  const portfolio = portfolioView(source.optimization_portfolio, portfolioMetadata);
  return {
    decision: safeDecision(source.decision),
    riskDomains: safeRiskDomains(source.risk_domains),
    matrix: matrixPoints(source.risk_matrix),
    priorities: safePriorities(source.priorities),
    portfolio: {
      ...portfolio,
      metadata: portfolioMetadata,
    },
    evidence: safeEvidenceSummaries(source.selected_evidence_summaries),
    insight: isRecord(source.insight) ? (() => {
      const status = evidenceState(source.insight.status);
      return {
        title: boundedText(source.insight.title, 120),
        summary: boundedText(source.insight.summary, 320),
        status: status.key,
        badge: status.label,
      };
    })() : null,
    drafts: safeDraftSummaries(source.drafts),
    governance: safeGovernanceCapability(source.governance_capability),
  };
}


const RISK_SCAN_STATUS_LABELS = Object.freeze({
  triggered: "需关注",
  clear: "正常",
  insufficient_data: "样本不足",
  unavailable: "暂不可评估",
});


function scanValueLabel(value, unit) {
  if (value === null) return "未取得";
  if (unit === "%") return `${formatNumber(value, 2)}%`;
  if (unit === "ms") return `${formatNumber(value, 1)} ms`;
  if (unit === "x") return `${formatNumber(value, 2)} 倍`;
  return formatNumber(value, 2);
}


function scanEvidenceRefs(value, policy) {
  const matching = records(value).find((item) => (
    boundedText(item.subject_type, 24) === "risk"
    && safePolicy(item.policy_type ?? item.subject_id) === policy
  ));
  return safeEvidenceRefs(records(matching?.items).map((item) => item.request_ref));
}


export function riskScanView(payload) {
  const source = isRecord(payload) ? payload : {};
  const scanRef = boundedText(source.scan_ref, 40);
  const status = boundedText(source.status, 24);
  const evaluated = nonNegativeNumber(source.rules_evaluated) ?? 0;
  const triggered = nonNegativeNumber(source.rules_triggered) ?? 0;
  const clear = nonNegativeNumber(source.rules_clear) ?? 0;
  const insufficient = nonNegativeNumber(source.rules_insufficient) ?? 0;
  const unavailable = Math.max(0, evaluated - triggered - clear - insufficient);
  const evidenceSets = records(source.evidence_sets);
  const findings = records(source.findings).flatMap((item) => {
    const policy = safePolicy(item.policy_type);
    if (policy === "other") return [];
    const findingStatus = boundedText(item.status, 32);
    if (!Object.hasOwn(RISK_SCAN_STATUS_LABELS, findingStatus)) return [];
    const unit = ["%", "ms", "x"].includes(item.unit) ? item.unit : "";
    const directRefs = safeEvidenceRefs(item.evidence_refs).filter((reference) => reference.startsWith("req_"));
    const selectedRefs = scanEvidenceRefs(evidenceSets, policy).filter((reference) => reference.startsWith("req_"));
    return [{
      policy,
      label: POLICY_LABELS[policy],
      status: findingStatus,
      statusLabel: RISK_SCAN_STATUS_LABELS[findingStatus],
      severity: ["info", "warning", "critical"].includes(item.severity) ? item.severity : "info",
      observedValue: finiteNumber(item.observed_value),
      observedLabel: scanValueLabel(finiteNumber(item.observed_value), unit),
      thresholdValue: finiteNumber(item.threshold_value),
      thresholdLabel: scanValueLabel(finiteNumber(item.threshold_value), unit),
      unit,
      sampleCount: nonNegativeNumber(item.sample_count) ?? 0,
      minimumSamples: nonNegativeNumber(item.minimum_samples) ?? 0,
      reason: boundedText(item.reason, 300),
      recommendation: boundedText(item.recommendation, 300),
      ruleRevision: safeRevision(item.rule_revision),
      evidenceRefs: [...new Set([...directRefs, ...selectedRefs])].slice(0, 3),
    }];
  }).slice(0, 7);
  const governance = isRecord(source.governance) ? source.governance : {};
  return {
    isAvailable: Boolean(scanRef && status === "completed"),
    scanRef,
    status: status === "completed" ? status : "unavailable",
    summary: {
      evaluated,
      triggered,
      clear,
      insufficient,
      unavailable,
      sampleCount: nonNegativeNumber(source.request_sample_count) ?? 0,
      evidenceCoveragePct: nonNegativeNumber(source.evidence_coverage_pct),
    },
    findings,
    policyRevision: safeRevision(source.policy_revision),
    ledgerRevision: safeRevision(source.ledger_revision),
    startedAt: boundedText(source.started_at, 40),
    finishedAt: boundedText(source.finished_at, 40),
    readOnly: governance.mode === "read_only_scan" && governance.automatic_actions === false,
  };
}


export function riskScanHistoryView(payload) {
  return records(payload?.items).flatMap((item) => {
    const scanRef = boundedText(item.scan_ref, 48);
    const status = boundedText(item.status, 24);
    if (!scanRef || !["completed", "failed", "running"].includes(status)) return [];
    const triggered = nonNegativeNumber(item.rules_triggered) ?? 0;
    const samples = nonNegativeNumber(item.request_sample_count) ?? 0;
    const coverage = nonNegativeNumber(item.evidence_coverage_pct);
    const statusLabel = status === "completed"
      ? "已完成"
      : status === "running" ? "扫描中" : "未完成";
    const summary = status === "completed"
      ? `${triggered} 项需关注 · ${samples} 次请求 · 证据覆盖 ${coverage === null ? "待确认" : `${formatNumber(coverage, 2)}%`}`
      : status === "running" ? "正在读取当前授权范围的运行证据" : "扫描未完成，可重新执行";
    return [{
      scanRef,
      status,
      statusLabel,
      summary,
      startedAt: boundedText(item.started_at, 40),
      finishedAt: boundedText(item.finished_at, 40),
      triggered,
      samples,
      coverage,
    }];
  }).slice(0, 12);
}


function safeScalar(value) {
  if (typeof value === "boolean") return value;
  const number = finiteNumber(value);
  if (number !== null) return number;
  if (typeof value === "string") return boundedText(value, 160);
  return null;
}


function safeProposedChanges(value) {
  return records(value).flatMap((item) => {
    const field = boundedText(item.field, 48);
    if (!Object.hasOwn(CHANGE_FIELD_LABELS, field)) return [];
    return [{
      field,
      label: CHANGE_FIELD_LABELS[field],
      currentValue: safeScalar(item.current_value),
      candidateValue: safeScalar(item.candidate_value),
      rationale: boundedText(item.rationale, 300),
    }];
  }).slice(0, 12);
}


function safeVerificationPlan(value) {
  return records(value).flatMap((item) => {
    const metric = boundedText(item.metric, 64);
    const operator = boundedText(item.operator, 32);
    if (!Object.hasOwn(VERIFICATION_METRIC_LABELS, metric)
      || !Object.hasOwn(OPERATOR_LABELS, operator)) return [];
    return [{
      metric,
      metricLabel: VERIFICATION_METRIC_LABELS[metric],
      operator,
      operatorLabel: OPERATOR_LABELS[operator],
      baselineValue: finiteNumber(item.baseline_value),
      baselineWindow: boundedText(item.baseline_window, 128),
      target: finiteNumber(item.target),
      candidateWindowMinutes: nonNegativeNumber(item.candidate_window_minutes),
      minimumSamples: nonNegativeNumber(item.minimum_samples),
    }];
  }).slice(0, 12);
}


function safeExpectedImpact(raw) {
  const source = isRecord(raw) ? raw : {};
  const status = evidenceState(source.status);
  const amount = finiteNumber(source.amount);
  const unitKey = boundedText(source.unit, 32);
  const unit = Object.hasOwn(UNIT_LABELS, unitKey) ? unitKey : "";
  const usable = amount !== null && !["unavailable", "not_recorded"].includes(status.key);
  return {
    amount: usable ? amount : null,
    unit,
    status: usable ? status.key : "unavailable",
    badge: usable ? status.label : EVIDENCE_STATE_LABELS.unavailable,
    label: usable ? formatValue(amount, unit, status.key) : "待验证",
    calculationBasis: boundedText(source.calculation_basis, 320),
  };
}


export function remediationDraftView(payload) {
  const envelope = isRecord(payload) ? payload : {};
  const source = isRecord(envelope.draft) ? envelope.draft : envelope;
  const id = safeIdentifier(source.draft_id);
  const status = boundedText(source.status, 32);
  const actionKind = boundedText(source.action_kind, 48);
  const executionCapability = boundedText(source.execution_capability, 48);
  return {
    isAvailable: Boolean(id),
    id,
    workspaceId: safeIdentifier(source.workspace_id),
    sourceOpportunityId: safeIdentifier(source.source_opportunity_id),
    sourceAnomalyId: safeIdentifier(source.source_anomaly_id),
    riskType: safePolicy(source.risk_type),
    title: boundedText(source.title, 140),
    summary: boundedText(source.summary, 360),
    scope: safeDraftScope(source.scope),
    evidenceRefs: safeEvidenceRefs(source.evidence_refs),
    proposedChanges: safeProposedChanges(source.proposed_changes),
    expectedImpact: safeExpectedImpact(source.expected_impact),
    prerequisites: boundedTexts(source.prerequisites, 12, 240),
    risksAndGuardrails: boundedTexts(source.risks_and_guardrails, 12, 240),
    verificationPlan: safeVerificationPlan(source.verification_plan),
    rollbackPlan: boundedTexts(source.rollback_plan, 12, 240),
    actionKind: Object.hasOwn(ACTION_KIND_LABELS, actionKind) ? actionKind : "investigation",
    actionKindLabel: ACTION_KIND_LABELS[actionKind] || ACTION_KIND_LABELS.investigation,
    executionCapability: ["advisory_only", "typed_action_available"].includes(executionCapability)
      ? executionCapability
      : "advisory_only",
    executionCapabilityLabel: executionCapability === "typed_action_available"
      ? "可转为审批动作草案"
      : "仅供建议",
    baseVersion: safeRevision(source.base_version),
    status: Object.hasOwn(REMEDIATION_STATUS_LABELS, status) ? status : "unavailable",
    statusLabel: REMEDIATION_STATUS_LABELS[status] || "状态待确认",
    revision: nonNegativeNumber(source.revision),
    translatedActionId: safeIdentifier(source.translated_action_id),
    createdAt: boundedText(source.created_at, 40),
    updatedAt: boundedText(source.updated_at, 40),
  };
}
