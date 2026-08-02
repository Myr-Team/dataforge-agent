import {
  formatFinOpsCost,
  formatFinOpsNumber,
  formatFinOpsPercent,
} from "./finopsViewModel.js";


const EVIDENCE_STATES = new Set([
  "available",
  "complete",
  "observed",
  "measured",
  "verified",
  "estimated",
  "partial",
  "insufficient_data",
  "not_recorded",
  "not_configured",
  "unavailable",
  "unpriced",
  "ready",
]);


function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}


function boundedText(value, fallback = "", maximum = 120) {
  return String(value || fallback).trim().slice(0, maximum);
}


function evidenceState(value) {
  const normalized = String(value || "unavailable").toLowerCase();
  return EVIDENCE_STATES.has(normalized) ? normalized : "unavailable";
}


function isCompleteEvidence(value) {
  return ["available", "complete", "observed", "measured", "verified"].includes(value);
}


function valueAssessment(roi = {}) {
  const evidence = evidenceState(roi.evidence_state);
  const status = evidenceState(roi.status);
  const state = evidence !== "unavailable" ? evidence : status;
  if (["verified", "complete"].includes(state)) {
    return { value: "已验证", tone: "positive", status: "verified" };
  }
  if (["estimated", "partial", "ready"].includes(state)) {
    return { value: "需验证", tone: "warning", status: state };
  }
  if (["insufficient_data", "unavailable", "not_recorded", "not_configured"].includes(state)) {
    return { value: "证据不足", tone: "warning", status: state };
  }
  return { value: "待评估", tone: "neutral", status: "unavailable" };
}


export function executiveCostSummary(overview = {}) {
  const cost = overview?.metrics?.estimated_cost || {};
  const pricing = overview?.trust?.pricing || {};
  const coverage = finiteNumber(pricing.coverage_pct);
  const unpriced = finiteNumber(cost.unpriced_requests ?? pricing.unpriced_requests);
  const coverageLabel = coverage === null
    ? "计价覆盖待补齐"
    : `计价覆盖 ${formatFinOpsPercent(coverage)}`;
  return {
    value: formatFinOpsCost(finiteNumber(cost.amount), cost.status),
    meta: `${coverageLabel}${unpriced ? ` · ${formatFinOpsNumber(unpriced, "0")} 次未计价` : ""}`,
    status: evidenceState(cost.status),
  };
}


function isUnassignedLabel(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return ["unassigned", "未归属"].includes(normalized);
}


function departmentCostComposition(department = {}) {
  const rows = (Array.isArray(department?.items) ? department.items : [])
    .map((item) => ({
      label: boundedText(item?.key, "未记录", 80),
      value: finiteNumber(item?.estimated_cost),
      evidenceState: evidenceState(item?.data_status),
    }))
    .filter((item) => item.value !== null && item.value > 0)
    .sort((left, right) => right.value - left.value);
  const total = rows.reduce((sum, item) => sum + item.value, 0);
  if (!rows.length || total <= 0) {
    return {
      total: 0,
      currency: "USD",
      status: "unavailable",
      segments: [],
    };
  }

  const unassigned = rows.find((item) => isUnassignedLabel(item.label));
  const named = rows.filter((item) => item !== unassigned);
  const visible = unassigned
    ? [...named.slice(0, 2), unassigned]
    : rows.slice(0, 3);
  const visibleSet = new Set(visible);
  const remainder = rows.filter((item) => !visibleSet.has(item));
  if (remainder.length) {
    visible.push({
      label: "Other",
      value: remainder.reduce((sum, item) => sum + item.value, 0),
      evidenceState: remainder.every((item) => isCompleteEvidence(item.evidenceState))
        ? "available"
        : "partial",
    });
  }

  let offsetPct = 0;
  const segments = visible.map((item, index) => {
    const sharePct = Number(((item.value / total) * 100).toFixed(1));
    const segment = {
      id: `department-${index}`,
      ...item,
      sharePct,
      offsetPct,
      colorIndex: index % 6,
    };
    offsetPct += sharePct;
    return segment;
  });
  return {
    total,
    currency: "USD",
    status: rows.every((item) => isCompleteEvidence(item.evidenceState))
      ? "available"
      : "partial",
    segments,
  };
}


function anomalyAttention(anomalies = {}) {
  const items = Array.isArray(anomalies?.items) ? anomalies.items : [];
  return items.slice(0, 1).map((item, index) => ({
    id: `anomaly-${boundedText(item?.anomaly_id || item?.id, String(index), 64)}`,
    title: boundedText(item?.title, "运营异常待处理"),
    detail: "来自当前筛选范围的已记录异常",
    tone: item?.severity === "critical" ? "critical" : "warning",
    status: evidenceState(item?.evidence_state),
    reason: boundedText(item?.title, "运营异常"),
    evidenceRefs: Array.isArray(item?.evidence_refs)
      ? item.evidence_refs.map((value) => boundedText(value, "", 96)).filter(Boolean).slice(0, 10)
      : [],
  }));
}


export function executiveOverviewView(data = {}) {
  const overview = data?.overview || {};
  const metrics = overview?.metrics || {};
  const cost = executiveCostSummary(overview);
  const value = valueAssessment(data?.insights?.roi || {});
  const requests = finiteNumber(metrics.requests);
  const successRate = finiteNumber(metrics.success_rate_pct);
  const cacheHitRate = finiteNumber(metrics.cache_hit_rate_pct);
  const cacheSavings = finiteNumber(metrics?.cache?.estimated_savings);
  const overviewStatus = evidenceState(overview.data_status);
  const pricing = overview?.trust?.pricing || {};

  const attention = anomalyAttention(data?.anomalies);
  const unpricedRequests = finiteNumber(pricing.unpriced_requests);
  if (unpricedRequests !== null && unpricedRequests > 0) {
    attention.push({
      id: "pricing-gap",
      title: "计价覆盖需要补齐",
      detail: `${formatFinOpsNumber(unpricedRequests, "0")} 次调用未计价`,
      tone: "warning",
      status: evidenceState(pricing.state),
      reason: "计价覆盖",
      evidenceRefs: [],
    });
  }
  if (value.status !== "verified") {
    attention.push({
      id: "roi-evidence",
      title: boundedText(data?.insights?.roi?.title, "业务价值仍需验证"),
      detail: "价值测算与已验证业务结果保持分离",
      tone: "neutral",
      status: value.status,
      reason: "价值证据",
      evidenceRefs: [],
    });
  }

  return {
    cards: [
      {
        id: "cost",
        label: "AI 使用成本",
        value: cost.value,
        meta: cost.meta,
        tone: ["partial", "unavailable", "unpriced"].includes(cost.status) ? "warning" : "neutral",
        metric: {
          id: "estimated_cost",
          label: "AI 使用成本",
          kind: "cost",
          amount: finiteNumber(metrics?.estimated_cost?.amount),
          value: finiteNumber(metrics?.estimated_cost?.amount),
          unit: "USD",
          pricedRequests: finiteNumber(metrics?.estimated_cost?.priced_requests),
          unpricedRequests: finiteNumber(metrics?.estimated_cost?.unpriced_requests),
          priceRevision: boundedText(metrics?.estimated_cost?.price_card_revision, "", 80),
          dataStatus: overviewStatus,
          evidenceState: cost.status,
        },
      },
      {
        id: "quality",
        label: "调用质量",
        value: formatFinOpsPercent(successRate),
        meta: `共 ${formatFinOpsNumber(requests, "未记录")} 次调用`,
        tone: successRate !== null && successRate < 95 ? "warning" : "neutral",
        metric: {
          id: "success_rate",
          label: "调用质量",
          kind: "quality",
          value: successRate,
          unit: "%",
          requests,
          successRatePct: successRate,
          p50Ms: finiteNumber(metrics?.latency?.p50_ms),
          p95Ms: finiteNumber(metrics?.latency?.p95_ms),
          dataStatus: overviewStatus,
          evidenceState: overviewStatus === "complete" ? "observed" : overviewStatus,
        },
      },
      {
        id: "cache_value",
        label: "缓存收益",
        value: formatFinOpsCost(cacheSavings, metrics?.cache?.data_status),
        meta: `命中率 ${formatFinOpsPercent(cacheHitRate)}`,
        tone: cacheHitRate !== null && cacheHitRate < 60 ? "warning" : "positive",
        metric: {
          id: "cache_savings",
          label: "缓存收益",
          kind: "cache",
          value: cacheSavings,
          unit: "USD",
          cache: {
            hit: finiteNumber(metrics?.cache?.hit),
            miss: finiteNumber(metrics?.cache?.miss),
            eligible: finiteNumber(metrics?.cache?.eligible_requests),
            avoidedTokens: finiteNumber(metrics?.cache?.avoided_tokens),
            estimatedSavings: cacheSavings,
          },
          dataStatus: evidenceState(metrics?.cache?.data_status),
          evidenceState: evidenceState(metrics?.cache?.data_status),
        },
      },
      {
        id: "value_assessment",
        label: "价值判断",
        value: value.value,
        meta: boundedText(data?.insights?.roi?.summary, "等待业务结果验证"),
        tone: value.tone,
        metric: {
          id: "roi_assessment",
          label: "价值判断",
          kind: "overview",
          value: null,
          unit: "",
          dataStatus: value.status,
          evidenceState: value.status,
        },
      },
    ],
    costComposition: departmentCostComposition(data?.department),
    attention: attention.slice(0, 3),
  };
}
