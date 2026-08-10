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
  configured_unverified: { label: "已配置，证据未验证", tone: "warn" },
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
  removed: { label: "已移除", tone: "neutral" },
});

function stateOf(value, fallback = "unknown") {
  const raw = typeof value === "string" ? value : value?.state ?? value?.status;
  if (raw && typeof raw === "object") return stateOf(raw, fallback);
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

export function traceNeedsRefresh(status) {
  return stateOf(status) === "partial";
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
    errorStatus: state === "unavailable" && Number.isInteger(Number(status?.error_status)) ? Number(status.error_status) : null,
    issueCode: traceIssueCode(status),
  };
}

export function traceIssueCode(status) {
  if (stateOf(status) !== "unavailable") return "";
  const code = Number(status?.error_status);
  if (code === 401 || code === 403) return "runtime_access_denied";
  if (code === 429) return "query_throttled";
  return "query_unavailable";
}

export function traceTelemetryMetricsViewModel(status = {}) {
  const state = stateOf(status);
  const copy = STATUS_COPY[state] || { label: "未记录", tone: "neutral" };
  return {
    state,
    label: copy.label,
    tone: copy.tone,
    available: state === "connected",
    issueCode: traceIssueCode(status),
    recordCount: finiteNumber(status?.record_count),
    requestCount: finiteNumber(status?.request_count),
    dependencyCount: finiteNumber(status?.dependency_count),
    traceEventCount: finiteNumber(status?.trace_event_count),
    errorCount: finiteNumber(status?.error_count),
    firstObservedAt: status?.first_observed_at || null,
    lastObservedAt: status?.last_observed_at || null,
  };
}

export function runTraceReferenceViewModel(reference, deliveryStatus = {}) {
  const traceId = String(reference?.trace_id || "").trim().toLowerCase();
  const agentId = String(reference?.agent_id || "").trim();
  if (!/^[0-9a-f]{32}$/.test(traceId) || !/^[A-Za-z0-9_.:-]{1,128}$/.test(agentId)) {
    return { available: false, traceId: "", agentId: "", delivery: traceViewModel({}), transactionUrl: "" };
  }
  const delivery = traceViewModel(deliveryStatus);
  return {
    available: true,
    traceId,
    agentId,
    delivery,
    transactionUrl: delivery.transactionUrl,
  };
}

export function roiViewModel({ local = {}, provider = null } = {}) {
  const foundry = provider || local?.foundry_roi || {};
  const providerSnapshot = foundry?.provider_snapshot || foundry?.snapshot || {};
  const foundryConnectionState = stateOf(foundry?.status ?? foundry);
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
  return String(member?.subject_label || member?.actor_id || "").trim().toLowerCase();
}

function safeMemberLabel(member) {
  return validBoundedPseudonym(member?.subject_label, "member") || "成员（已脱敏）";
}

function verifiedEnterpriseDisplay(member) {
  if (member?.identity_visibility !== "verified_enterprise") return null;
  const name = String(member?.display?.name || "").trim().replace(/[\r\n\t]/g, " ");
  const email = String(member?.display?.email || "").trim().toLowerCase();
  if (!name || name.length > 120 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return null;
  return { name, email };
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
  return validBoundedPseudonym(value, expectedPrefix) || `${expectedPrefix}_已脱敏`;
}

function validBoundedPseudonym(value, expectedPrefix) {
  const raw = String(value || "").trim().toLowerCase();
  const match = raw.match(/^([a-z][a-z0-9_]*_)([0-9a-f]{40,64})$/);
  if (!match || !match[1].startsWith(`${expectedPrefix}_`)) return "";
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
  const actions = permissions?.actions || {};
  const serverReasons = permissions?.reasons || {};
  const reasons = {};
  for (const action of ["audit.read", "chargeback.read", "invitation.read", "member.manage"]) {
    if (actions[action] !== true) reasons[action] = String(serverReasons[action] || `服务端未提供 ${action} 权限`);
  }
  return {
    canReadAudit: actions["audit.read"] === true,
    canManageMembers: actions["member.manage"] === true,
    canReadChargeback: actions["chargeback.read"] === true,
    canReadInvitations: actions["invitation.read"] === true,
    reasons,
  };
}

export function invitationLifecycleViewModel(payload = {}) {
  return (Array.isArray(payload?.invitations) ? payload.invitations : []).map((item) => {
    const state = stateOf(item);
    const copy = INVITATION_COPY[state] || { label: "未记录", tone: "neutral" };
    return {
      invitationRef: validBoundedPseudonym(item?.invitation_ref, "invite") || "invite_已脱敏",
      subjectLabel: validBoundedPseudonym(item?.subject_label, "member") || "成员（已脱敏）",
      role: ["owner", "admin", "editor", "viewer"].includes(item?.role) ? item.role : "viewer",
      state,
      stateLabel: copy.label,
      tone: copy.tone,
      updatedAt: item?.updated_at || null,
    };
  });
}

export function directorySelectionViewModel(payload = {}) {
  return (Array.isArray(payload?.users) ? payload.users : []).flatMap((item) => {
    const selectionRef = String(item?.selection_ref || "").trim().toLowerCase();
    const subjectLabel = validBoundedPseudonym(item?.subject_label, "member");
    if (!/^selection_[0-9a-f]{40}$/.test(selectionRef) || !subjectLabel) return [];
    return [{ selectionRef, subjectLabel }];
  });
}

export function memberDirectoryViewModel(members = []) {
  return (Array.isArray(members) ? members : []).map((member, index) => {
    const rawRef = String(member?.subject_label || "").trim().toLowerCase();
    const actionRef = /^member_[0-9a-f]{40}$/.test(rawRef) ? rawRef : "";
    const role = ["owner", "admin", "editor", "viewer"].includes(member?.role) ? member.role : "viewer";
    const status = ["active", "pending"].includes(member?.status) ? member.status : "pending";
    const display = verifiedEnterpriseDisplay(member);
    const subjectLabel = validBoundedPseudonym(actionRef, "member") || "成员（已脱敏）";
    return {
      actionRef,
      subjectLabel,
      label: display?.name || `待关联 Entra 成员 ${index + 1}`,
      detail: display?.email || "尚未完成企业身份关联",
      identityVisibility: display ? "verified_enterprise" : "pseudonymous",
      role,
      owner: role === "owner",
      status,
      source: safeIdentifier(member?.source, "workspace_member"),
      usage: member?.usage && typeof member.usage === "object" ? member.usage : {},
      lastSeenAt: member?.last_seen_at || "",
      invitedAt: member?.invited_at || "",
      updatedAt: member?.updated_at || "",
    };
  });
}
