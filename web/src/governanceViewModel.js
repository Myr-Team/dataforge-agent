const STATUS_COPY = Object.freeze({
  connected: { label: "已确认遥测送达", tone: "ok" },
  partial: { label: "已配置，尚未确认遥测到达", tone: "warn" },
  not_configured: { label: "未配置 Azure 遥测", tone: "neutral" },
  unavailable: { label: "遥测验证暂不可用", tone: "error" },
});

const ROI_COPY = Object.freeze({
  estimated: { label: "估算", tone: "neutral" },
  measured: { label: "已测量", tone: "info" },
  verified: { label: "已验证", tone: "ok" },
  connected: { label: "已连接，证据状态未记录", tone: "info" },
  discovery_verified: { label: "仅发现已验证", tone: "warn" },
  not_configured: { label: "未配置", tone: "neutral" },
  unavailable: { label: "暂不可用", tone: "error" },
  unknown: { label: "未记录", tone: "neutral" },
});

const INVITATION_COPY = Object.freeze({
  pending: { label: "待接受", tone: "warn" },
  accepted: { label: "已接受", tone: "ok" },
  active: { label: "已接受", tone: "ok" },
  failed: { label: "发送失败", tone: "error" },
  expired: { label: "已过期", tone: "neutral" },
  revoked: { label: "已撤销", tone: "neutral" },
});

function stateOf(value, fallback = "unknown") {
  const raw = typeof value === "string" ? value : value?.state ?? value?.status;
  const state = String(raw || "").trim().toLowerCase();
  return state || fallback;
}

function finiteNumber(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function formatNumber(value) {
  const number = finiteNumber(value);
  if (number === null) return "未记录";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

function formatMoneyValue(amount, currency) {
  const number = finiteNumber(amount);
  const code = /^[A-Z]{3}$/.test(String(currency || "")) ? String(currency) : "";
  if (number === null || !code) return "未记录";
  return `${code} ${number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function costText(cost) {
  const status = stateOf(cost);
  if (status === "complete") return formatMoneyValue(cost?.total, cost?.currency);
  const entries = Object.entries(cost?.by_currency || {}).filter(([currency, amount]) => /^[A-Z]{3}$/.test(currency) && finiteNumber(amount) !== null);
  if (status === "partial" && entries.length) {
    return `${entries.map(([currency, amount]) => formatMoneyValue(amount, currency)).join(" + ")}（部分已计价）`;
  }
  return "未记录";
}

function businessValueView(value) {
  return {
    text: formatMoneyValue(value?.total ?? value?.amount, value?.currency),
    status: stateOf(value, "not_monetized"),
  };
}

function roiEvidenceView(snapshot, statusOverride) {
  const status = stateOf(statusOverride || snapshot);
  const copy = ROI_COPY[status] || ROI_COPY.unknown;
  return {
    status,
    label: copy.label,
    tone: copy.tone,
    businessValue: businessValueView(snapshot?.business_value),
    costText: costText(snapshot?.cost),
    tokenText: formatNumber(snapshot?.usage?.total_tokens),
    outcomeCount: Array.isArray(snapshot?.outcome_event_ids) ? snapshot.outcome_event_ids.length : 0,
    unverifiedCount: Array.isArray(snapshot?.unverified_outcome_event_ids) ? snapshot.unverified_outcome_event_ids.length : 0,
    lineageComplete: snapshot?.lineage_complete === true,
    truncated: snapshot?.truncated === true,
  };
}

export function traceStatusLabel(status) {
  return (STATUS_COPY[stateOf(status)] || { label: "未记录" }).label;
}

export function traceViewModel(status) {
  const state = stateOf(status);
  const copy = STATUS_COPY[state] || { label: "未记录", tone: "neutral" };
  return {
    state,
    label: copy.label,
    tone: copy.tone,
    localEmitAt: status?.local_emit_at || null,
    exporterState: stateOf(status?.exporter_state),
    deliveredAt: status?.last_export_confirmed_at || null,
    transactionUrl: state === "connected" && /^https:\/\//.test(String(status?.transaction_url || "")) ? status.transaction_url : "",
    errorType: state === "unavailable" && /^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(String(status?.error_type || "")) ? status.error_type : "",
  };
}

export function roiViewModel({ local = {}, provider = null } = {}) {
  const foundry = provider || local?.foundry_roi || {};
  const providerSnapshot = foundry?.provider_snapshot || foundry?.snapshot || {};
  const foundryConnectionState = stateOf(foundry);
  const providerStatus = providerSnapshot?.status ? stateOf(providerSnapshot) : foundryConnectionState;
  return {
    localStatus: stateOf(local, "estimated"),
    providerStatus,
    foundryConnectionState,
    local: roiEvidenceView(local),
    provider: roiEvidenceView(providerSnapshot, providerStatus),
    difference: foundry?.difference || null,
    reconciliation: foundry?.reconciliation || null,
  };
}

function memberKey(member) {
  return String(member?.actor_id || member?.email || member?.name || "").trim().toLowerCase();
}

function safeMemberLabel(member) {
  const actorId = String(member?.actor_id || "");
  if (member?.name) return String(member.name);
  if (member?.email && member?.status !== "unknown_or_departed") return String(member.email);
  return boundedPseudonym(actorId, "actor") || "成员（未公开）";
}

export function chargebackViewModel(snapshot) {
  const groups = Array.isArray(snapshot?.groups) ? snapshot.groups : [];
  const rows = (Array.isArray(snapshot?.members) ? snapshot.members : []).map((row) => {
    const key = memberKey(row?.member);
    const matching = groups.filter((group) => memberKey(group?.member) === key);
    const knownTokens = matching.map((group) => finiteNumber(group?.total_tokens)).filter((value) => value !== null);
    const tokens = knownTokens.reduce((total, value) => total + value, 0);
    return {
      memberLabel: safeMemberLabel(row?.member || {}),
      memberStatus: row?.member?.status || "unknown_or_departed",
      tokenText: knownTokens.length ? formatNumber(tokens) : "未记录",
      costText: costText(row?.cost),
      evidenceStatus: stateOf(row?.cost),
      groupCount: finiteNumber(row?.groups) || 0,
    };
  });
  return {
    rows,
    totalCostText: costText(snapshot?.totals),
    evidenceStatus: stateOf(snapshot?.totals),
    truncated: snapshot?.truncated === true,
    duplicateEventCount: finiteNumber(snapshot?.duplicate_event_count) || 0,
  };
}

function boundedPseudonym(value, expectedPrefix) {
  const raw = String(value || "").trim().toLowerCase();
  const match = raw.match(/^([a-z][a-z0-9_]*_)([0-9a-f]{40,64})$/);
  if (!match || !match[1].startsWith(`${expectedPrefix}_`)) return `${expectedPrefix}_已脱敏`;
  return `${match[1]}${match[2].slice(0, 8)}…${match[2].slice(-4)}`;
}

function safeIdentifier(value, fallback) {
  const raw = String(value || "").trim().toLowerCase();
  return /^[a-z][a-z0-9_.-]{0,79}$/.test(raw) ? raw : fallback;
}

export function auditEventViewModel(event) {
  const correlationKeys = Object.keys(event?.correlation || {}).filter((key) => /^[a-z][a-z0-9_]{0,39}$/.test(key)).slice(0, 4);
  return {
    revision: Number.isInteger(event?.revision) && event.revision > 0 ? event.revision : null,
    actor: boundedPseudonym(event?.actor_hash, "actor"),
    action: safeIdentifier(event?.action, "未记录"),
    resourceType: safeIdentifier(event?.resource_type, "resource"),
    resource: boundedPseudonym(event?.resource_id, "res"),
    result: ["allowed", "denied", "failed"].includes(event?.result) ? event.result : "未记录",
    reasonCode: safeIdentifier(event?.reason_code, ""),
    correlationKeys,
    at: event?.at || null,
  };
}

export function appendAuditPage(current, next) {
  const seen = new Set();
  const events = [...(current?.events || []), ...(next?.events || [])].filter((event) => {
    const key = Number.isInteger(event?.revision) ? `revision:${event.revision}` : JSON.stringify(event);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return { ...current, ...next, events };
}

export function governancePermissions(payload) {
  const permissions = payload?.permissions || {};
  const role = ["owner", "admin"].includes(permissions.role) ? permissions.role : "";
  const canReadAudit = permissions.can_read === true && Boolean(role);
  const privileged = canReadAudit && (role === "owner" || role === "admin");
  return {
    role,
    canReadAudit,
    canManageMembers: privileged,
    canReadChargeback: privileged,
    reason: privileged ? "" : "需要工作区所有者或管理员权限",
  };
}

export function invitationLifecycleViewModel(members = [], recent = []) {
  const rows = [];
  const seen = new Set();
  const add = (item, fromMember = false) => {
    const state = fromMember && item?.status === "active" ? "accepted" : stateOf(item, fromMember ? "pending" : "unknown");
    const key = String(item?.email || item?.invitation_id || `${state}:${rows.length}`).toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    const copy = INVITATION_COPY[state] || { label: "未记录", tone: "neutral" };
    rows.push({
      email: String(item?.email || "").trim(),
      name: String(item?.name || item?.user || "").trim(),
      role: ["owner", "admin", "editor", "viewer"].includes(item?.role) ? item.role : "viewer",
      state,
      stateLabel: copy.label,
      tone: copy.tone,
      updatedAt: item?.updated_at || item?.invited_at || item?.at || null,
    });
  };
  members.filter((member) => member?.invitation_id || member?.status === "pending").forEach((member) => add(member, true));
  recent.forEach((item) => add(item, false));
  return rows;
}
