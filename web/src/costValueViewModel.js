const STATUS = Object.freeze({
  complete: { label: "已计价", tone: "ok" },
  incomplete: { label: "数据不完整", tone: "warn" },
  not_configured: { label: "价格未配置", tone: "neutral" },
  verified: { label: "已验证 ROI", tone: "ok" },
  not_recorded: { label: "未记录", tone: "neutral" },
  not_monetized: { label: "未货币化", tone: "warn" },
  observed: { label: "已观察", tone: "info" },
  estimated: { label: "情景估算", tone: "neutral" },
});

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function currency(value) {
  const code = String(value || "").trim().toUpperCase();
  return /^[A-Z]{3}$/.test(code) ? code : "";
}

function money(value, unit) {
  const amount = finite(value);
  const code = currency(unit);
  return amount === null || !code ? "未记录" : `${code} ${amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function state(value, fallback) {
  const key = String(value?.status || value?.state || fallback).toLowerCase();
  return { status: key, ...(STATUS[key] || STATUS[fallback]) };
}

function scenarioView(value) {
  const result = value?.result || {};
  const view = state(value, "estimated");
  const ratio = finite(result.roi_ratio);
  return {
    id: /^[a-z][a-z0-9_]{3,80}$/i.test(String(value?.scenario_id || "")) ? value.scenario_id : "",
    title: String(value?.title || "未命名情景").slice(0, 160),
    revision: Number.isInteger(value?.revision) && value.revision > 0 ? value.revision : null,
    badge: view.status === "estimated" ? "情景估算" : view.label,
    tone: view.status === "estimated" ? "neutral" : view.tone,
    valueText: money(result.estimated_business_value, result.currency),
    roiText: ratio === null ? "未记录" : `${(ratio * 100).toFixed(1)}%`,
  };
}

export function costValueViewModel(payload = {}) {
  const cost = state(payload.cost_evidence, "not_configured");
  const outcomes = state(payload.outcome_evidence, "not_recorded");
  const realized = state(payload.realized_roi, "not_recorded");
  const foundryState = String(payload?.foundry_integration?.state || "not_connected").toLowerCase();
  const foundry = {
    state: foundryState,
    official: payload?.foundry_integration?.official_source === true,
    label: foundryState === "verified" ? "官方来源已验证" : foundryState === "available" ? "集成可用，尚未验证官方 ROI" : "未接入官方 ROI 数据源",
    tone: foundryState === "verified" ? "ok" : foundryState === "available" ? "warn" : "neutral",
  };
  const roiRatio = finite(payload?.realized_roi?.roi_ratio);
  return {
    cost: { ...cost, totalText: money(payload?.cost_evidence?.total, payload?.cost_evidence?.currency) },
    outcomes: {
      ...outcomes,
      count: Array.isArray(payload?.outcome_evidence?.outcome_event_ids) ? payload.outcome_evidence.outcome_event_ids.length : 0,
    },
    realized: {
      ...realized,
      valueText: realized.status === "verified" ? money(payload?.realized_roi?.value, payload?.realized_roi?.currency) : "未记录",
      roiText: realized.status === "verified" && roiRatio !== null ? `${(roiRatio * 100).toFixed(1)}%` : "未记录",
    },
    scenarios: Array.isArray(payload?.scenarios) ? payload.scenarios.slice(0, 12).map(scenarioView).filter((item) => item.id) : [],
    foundry,
  };
}
