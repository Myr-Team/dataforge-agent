const DELIVERY_LABELS = {
  pending: "等待发送",
  sending: "发送中",
  sent: "已发送",
  failed: "发送失败",
  suppressed: "已合并",
};

const SAFE_TEST_EMAIL_STATES = {
  not_configured: "邮件服务尚未配置",
  permission_required: "托管身份缺少邮件发送权限",
  timeout: "邮件服务响应超时",
  service_unavailable: "邮件服务暂时不可用",
};

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function money(value) {
  const amount = finite(value);
  return amount === null
    ? null
    : new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
}

function percentage(value) {
  const amount = finite(value);
  return amount === null ? null : Math.max(0, Math.min(100, amount));
}

function friendlyName(value) {
  const name = text(value);
  if (!name || /^actor[-_:]/i.test(name) || /^member[-_:][a-f0-9]{8,}$/i.test(name)) {
    return "未命名成员";
  }
  return name === "Former member" ? "历史成员" : name;
}

function maskedEmail(value) {
  const email = text(value);
  const [local, domain] = email.split("@");
  if (!local || !domain) return "尚未配置";
  return `${local.slice(0, 1)}***@${domain}`;
}

function normalizedThresholds(value) {
  const items = Array.isArray(value)
    ? value.filter((item) => Number.isInteger(item) && item >= 1 && item <= 100)
    : [];
  return [...new Set(items)].sort((left, right) => left - right);
}

function currentUtcPeriodKey() {
  const now = new Date();
  return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
}

function rowAlert(alerts, budgetId, periodKey) {
  return alerts
    .filter((item) => item && text(item.budget_id) === budgetId && text(item.period_key) === periodKey)
    .sort((left, right) => Number(right.threshold_pct || 0) - Number(left.threshold_pct || 0))[0] || null;
}

function rowModel(item, alerts, periodKey) {
  const member = item?.member && typeof item.member === "object" ? item.member : {};
  const progress = item?.progress && typeof item.progress === "object" ? item.progress : {};
  const budgetId = text(item?.budget_id);
  const budgetAmount = finite(item?.amount_usd);
  const spend = finite(progress.estimated_spend_usd);
  const usage = budgetAmount !== null && budgetAmount > 0 && spend !== null
    ? spend / budgetAmount * 100
    : null;
  const progressWidth = percentage(usage);
  const matchedAlert = rowAlert(alerts, budgetId, periodKey);
  const identityState = text(member.identity_state) || "inactive";
  const enabled = item?.enabled === true;
  const coverage = finite(progress.pricing_coverage_pct);
  const thresholdsPct = normalizedThresholds(item?.thresholds_pct);
  const firstThreshold = thresholdsPct[0] ?? null;
  const severity = usage !== null && usage >= 100
    ? "critical"
    : usage !== null && firstThreshold !== null && usage >= firstThreshold
      ? "warning"
      : "normal";
  const statusLabel = usage === null
    ? "进度不可用"
    : `${Math.round(usage)}%${matchedAlert?.delivery_state === "sent" ? " · 已提醒" : severity === "normal" ? "" : " · 接近预算"}`;

  return {
    budgetId,
    memberRef: text(member.member_ref) || text(item?.member_ref),
    revision: Number.isInteger(item?.revision) ? item.revision : 0,
    memberLabel: friendlyName(member.display_name),
    memberInitial: friendlyName(member.display_name).slice(0, 1),
    identityState,
    identityLabel: identityState === "active" ? "预算主体已启用" : "预算主体已停用",
    workspaceLabel: Array.isArray(member.workspace_ids) && member.workspace_ids.length
      ? `${member.workspace_ids.length} 个工作区`
      : "无当前工作区",
    departmentLabel: Array.isArray(member.department_labels) && member.department_labels.length
      ? member.department_labels.map(text).filter(Boolean).join("、")
      : "未归属部门",
    spendValue: spend,
    spendLabel: money(spend) || "未计价",
    budgetAmount,
    budgetLabel: money(budgetAmount) || "未设置",
    usagePct: usage,
    progressWidth,
    severity,
    statusLabel,
    coveragePct: coverage,
    coverageLabel: coverage === null ? "估算覆盖不可用" : `${Math.round(coverage)}% 已计价`,
    primaryModel: text(progress.primary_model) || "未记录",
    thresholdsPct,
    enabled,
    canEdit: identityState === "active",
    canDisable: enabled && identityState === "active",
    lifecycleLabel: enabled ? "预算已启用" : "预算已停用",
    currentAlertState: text(matchedAlert?.delivery_state),
    dataStatus: ["complete", "partial", "unavailable"].includes(text(item?.data_status))
      ? text(item.data_status)
      : "unavailable",
  };
}

function eligibleMemberModel(item) {
  return {
    memberRef: text(item?.member_ref),
    memberLabel: friendlyName(item?.display_name),
    roleLabel: text(item?.role) === "owner" ? "所有者" : text(item?.role) === "admin" ? "管理员" : "成员",
    identityState: text(item?.identity_state) || "inactive",
    departmentLabel: Array.isArray(item?.department_labels) && item.department_labels.length
      ? item.department_labels.map(text).filter(Boolean).join("、")
      : "未归属部门",
  };
}

export function memberBudgetViewModel({
  budgets = {},
  budgetsState = "",
  members = {},
  notification = null,
  notificationState = "",
  alerts = {},
  alertsState = "available",
  periodKey = currentUtcPeriodKey(),
} = {}) {
  const rawAlerts = Array.isArray(alerts?.items) ? alerts.items : [];
  const rows = (Array.isArray(budgets?.items) ? budgets.items : [])
    .filter((item) => item && typeof item === "object" && text(item.budget_id))
    .map((item) => rowModel(item, rawAlerts, periodKey));
  const eligibleMembers = (Array.isArray(members?.items) ? members.items : [])
    .filter((item) => item && typeof item === "object" && text(item.member_ref))
    .map(eligibleMemberModel);
  const membersByRef = new Map([
    ...eligibleMembers.map((item) => [item.memberRef, item.memberLabel]),
    ...rows.map((item) => [item.memberRef, item.memberLabel]),
  ]);
  const alertsView = rawAlerts
    .filter((item) => item && typeof item === "object" && text(item.budget_id))
    .map((item) => ({
      memberLabel: rows.find((row) => row.budgetId === text(item.budget_id))?.memberLabel
        || membersByRef.get(text(item.actor_ref))
        || "历史成员",
      thresholdLabel: Number.isFinite(Number(item.threshold_pct)) ? `${Number(item.threshold_pct)}%` : "阈值未记录",
      spendLabel: item.estimated_spend_usd === null || item.estimated_spend_usd === undefined
        ? "未计价"
        : money(Number(item.estimated_spend_usd)) || "未计价",
      coverageLabel: finite(item.pricing_coverage_pct) === null
        ? "估算覆盖不可用"
        : `${Math.round(item.pricing_coverage_pct)}% 已计价`,
      deliveryState: text(item.delivery_state),
      deliveryLabel: DELIVERY_LABELS[text(item.delivery_state)] || "状态不可用",
      triggeredAt: text(item.triggered_at),
    }));
  const notificationItem = notification?.item && typeof notification.item === "object"
    ? notification.item
    : null;
  const recipientEmail = text(notificationItem?.recipient_email);
  const knownSpend = rows.map((row) => row.spendValue).filter((value) => value !== null);
  const estimatedSpend = knownSpend.length ? knownSpend.reduce((sum, value) => sum + value, 0) : null;
  const sentAlerts = alertsView.filter((item) => item.deliveryState === "sent").length;
  const resolvedBudgetState = ["complete", "partial", "unavailable"].includes(text(budgetsState))
    ? text(budgetsState)
    : ["complete", "partial", "unavailable"].includes(text(budgets?.data_status))
      ? text(budgets.data_status)
      : rows.length
        ? "partial"
        : "complete";
  const budgetsUnavailable = resolvedBudgetState === "unavailable";
  const existingMemberRefs = new Set(rows.map((row) => row.memberRef));
  const createMembers = eligibleMembers.filter((member) => member.identityState === "active" && !existingMemberRefs.has(member.memberRef));

  return {
    rows,
    eligibleMembers,
    createMembers,
    alerts: alertsView,
    alertsState,
    summary: {
      estimatedSpend,
      estimatedSpendLabel: budgetsUnavailable ? "不可用" : money(estimatedSpend) || "不可用",
      configuredCount: budgetsUnavailable ? null : rows.length,
      nearBudgetCount: budgetsUnavailable
        ? null
        : rows.filter((row) => row.enabled && row.usagePct !== null && row.thresholdsPct.length > 0 && row.usagePct >= row.thresholdsPct[0]).length,
      sentAlertCount: resolvedBudgetState === "unavailable" || alertsState === "unavailable" ? null : sentAlerts,
      dataStatus: resolvedBudgetState,
    },
    notification: {
      state: notificationItem
        ? "configured"
        : notificationState === "disabled"
          ? "disabled"
          : notificationState === "permission_required"
            ? "permission_required"
            : notificationState === "unavailable"
              ? "unavailable"
              : "not_configured",
      configured: Boolean(notificationItem),
      recipientEmail,
      recipientLabel: recipientEmail ? maskedEmail(recipientEmail) : "尚未配置",
      senderDisplayName: text(notificationItem?.sender_display_name) || "DataForge",
      subjectTemplate: text(notificationItem?.subject_template) || "{{member_name}} 预算提醒",
      bodyTemplate: text(notificationItem?.body_template) || "本月估算成本 {{estimated_spend}}，预算 {{budget_amount}}，已达到 {{threshold_percent}}%。估算覆盖率：{{pricing_coverage}}。",
      enabled: notificationItem?.enabled === true,
      testEmailSucceededAt: text(notificationItem?.test_email_succeeded_at),
      testEmailReady: Boolean(text(notificationItem?.test_email_succeeded_at)),
      testDeliveryState: text(notificationItem?.last_test_delivery_state) || "not_tested",
      testAcceptedAt: text(notificationItem?.last_test_accepted_at),
      testDeliveryCheckedAt: text(notificationItem?.last_test_delivery_checked_at),
      revision: Number.isInteger(notificationItem?.revision) ? notificationItem.revision : 0,
    },
  };
}

export function memberBudgetHomeSummaryViewModel(value = {}) {
  if (value?.status === "permission_required") {
    return {
      state: "permission_required",
      stateLabel: "需要权限",
      nearBudgetLabel: "需要工作区管理员权限",
      mailLabel: "预算与提醒已受限",
      actionLabel: "查看权限说明",
    };
  }
  const view = value?.status === "unavailable"
    ? memberBudgetViewModel({ budgetsState: "unavailable", notificationState: "unavailable", alertsState: "unavailable" })
    : memberBudgetViewModel(value);
  if (value?.status === "unavailable" || view.summary.dataStatus === "unavailable") {
    return {
      state: "unavailable",
      stateLabel: "状态不可用",
      nearBudgetLabel: "接近预算不可用",
      mailLabel: view.notification.configured
        ? "邮件已配置"
        : view.notification.state === "not_configured"
          ? "邮件未配置"
          : "邮件状态不可用",
    };
  }
  return {
    state: view.rows.length ? view.summary.dataStatus : "empty",
    stateLabel: view.rows.length ? (view.summary.dataStatus === "partial" ? "部分计价" : "已记录") : "尚未设置",
    nearBudgetLabel: view.rows.length ? `${view.summary.nearBudgetCount} 位接近预算` : "暂无预算",
    mailLabel: view.notification.configured
      ? "邮件已配置"
        : view.notification.state === "disabled"
          ? "邮件配置未启用"
          : view.notification.state === "permission_required"
            ? "需要组织 FinOps 管理员权限"
            : view.notification.state === "unavailable"
              ? "邮件状态不可用"
              : "邮件未配置",
    actionLabel: "配置",
  };
}

export function safeTestEmailResult(value = {}) {
  if (text(value?.state) === "accepted" && !text(value?.safe_error_category)) {
    return { state: "accepted", label: "邮件服务已接受，等待投递确认" };
  }
  const category = text(value?.safe_error_category);
  if (Object.hasOwn(SAFE_TEST_EMAIL_STATES, category)) {
    return { state: category, label: SAFE_TEST_EMAIL_STATES[category] };
  }
  return { state: "unavailable", label: "邮件服务暂时不可用" };
}
