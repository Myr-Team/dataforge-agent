import assert from "node:assert/strict";
import test from "node:test";

import {
  memberBudgetHomeSummaryViewModel,
  memberBudgetViewModel,
  safeTestEmailResult,
} from "./memberBudgetViewModel.js";

const payload = {
  periodKey: "2026-07",
  budgets: {
    items: [{
      budget_id: "budget-safe",
      period_key: "2026-07",
      revision: 3,
      member_ref: "member-safe",
      member: {
        member_ref: "member-safe",
        display_name: "Finance Admin",
        identity_state: "active",
        workspace_ids: ["ws-safe"],
        department_labels: ["财务部"],
      },
      amount_usd: 200,
      thresholds_pct: [80, 95, 100],
      enabled: true,
      progress: {
        estimated_spend_usd: 190,
        pricing_coverage_pct: 90,
        primary_model: "gpt-5.6-terra",
        priced_requests: 18,
        total_requests: 20,
      },
      data_status: "partial",
    }],
    data_status: "partial",
  },
  members: {
    items: [{
      member_ref: "member-safe",
      display_name: "Finance Admin",
      role: "admin",
      identity_state: "active",
      workspace_ids: ["ws-safe"],
      department_labels: ["财务部"],
    }],
  },
  notification: {
    item: {
      recipient_actor_ref: "member-safe",
      recipient_email: "demo-admin@example.test",
      sender_display_name: "DataForge",
      subject_template: "{{member_name}} 预算提醒",
      body_template: "{{estimated_spend}} / {{budget_amount}}",
      enabled: true,
      test_email_succeeded_at: "2026-07-29T00:30:00Z",
      revision: 2,
    },
  },
  alerts: {
    items: [{
      alert_id: "alert-safe",
      tenant_ref: "tenant-raw-must-not-survive",
      actor_ref: "actor-raw-must-not-survive",
      budget_id: "budget-safe",
      period_key: "2026-07",
      threshold_pct: 95,
      budget_amount_usd: 200,
      estimated_spend_usd: 190,
      pricing_coverage_pct: 90,
      delivery_state: "sent",
      triggered_at: "2026-07-29T01:00:00Z",
    }],
  },
};

test("member budget view preserves partial coverage and actual 95 percent progress", () => {
  const view = memberBudgetViewModel(payload);

  assert.equal(view.rows[0].spendLabel, "$190.00");
  assert.equal(view.rows[0].coverageLabel, "90% 已计价");
  assert.equal(view.rows[0].statusLabel, "95% · 已提醒");
  assert.equal(view.rows[0].progressWidth, 95);
  assert.equal(view.rows[0].severity, "warning");
  assert.equal(view.rows[0].primaryModel, "gpt-5.6-terra");
  assert.equal(view.summary.estimatedSpendLabel, "$190.00");
  assert.equal(view.summary.nearBudgetCount, 1);
  assert.equal(view.notification.recipientEmail, "demo-admin@example.test");
  assert.equal(view.notification.recipientLabel, "d***@example.test");
  assert.equal(view.notification.testEmailReady, true);
});

test("missing spend stays unavailable instead of becoming zero", () => {
  const view = memberBudgetViewModel({
    budgets: {
      items: [{
        budget_id: "budget-unavailable",
        revision: 1,
        member_ref: "member-unavailable",
        member: { member_ref: "member-unavailable", display_name: "IT Operator" },
        amount_usd: 200,
        thresholds_pct: [80, 95, 100],
        enabled: true,
        progress: {
          estimated_spend_usd: null,
          pricing_coverage_pct: null,
          primary_model: null,
        },
        data_status: "unavailable",
      }],
    },
  });

  assert.equal(view.rows[0].spendLabel, "未计价");
  assert.equal(view.rows[0].coverageLabel, "估算覆盖不可用");
  assert.equal(view.rows[0].progressWidth, null);
  assert.equal(view.summary.estimatedSpendLabel, "不可用");
});

test("zero spend remains an observed zero and former member stays visible", () => {
  const view = memberBudgetViewModel({
    budgets: {
      items: [{
        budget_id: "budget-former",
        revision: 1,
        member_ref: "member-former",
        member: {
          member_ref: "member-former",
          display_name: "Former member",
          identity_state: "inactive",
        },
        amount_usd: 200.5,
        thresholds_pct: [80, 95, 100],
        enabled: false,
        progress: {
          estimated_spend_usd: 0,
          pricing_coverage_pct: 100,
          primary_model: null,
          priced_requests: 1,
          total_requests: 1,
        },
        data_status: "complete",
      }],
    },
  });

  assert.equal(view.rows[0].spendLabel, "$0.00");
  assert.equal(view.rows[0].budgetLabel, "$200.50");
  assert.equal(view.rows[0].identityLabel, "预算主体已停用");
  assert.equal(view.rows[0].lifecycleLabel, "预算已停用");
  assert.equal(view.rows[0].progressWidth, 0);
  assert.equal(view.rows[0].canEdit, false);
});

test("view model keeps only friendly alert labels and opaque member references", () => {
  const view = memberBudgetViewModel(payload);
  const serialized = JSON.stringify(view);

  assert.equal(view.alerts[0].memberLabel, "Finance Admin");
  assert.equal(serialized.includes("tenant-raw-must-not-survive"), false);
  assert.equal(serialized.includes("actor-raw-must-not-survive"), false);
  assert.equal(serialized.includes("alert-safe"), false);
  assert.equal(serialized.includes("finance.admin@"), false);
});

test("settings home summary is truthful for configured, unavailable and empty states", () => {
  const available = memberBudgetHomeSummaryViewModel(payload);
  assert.equal(available.nearBudgetLabel, "1 位接近预算");
  assert.equal(available.mailLabel, "邮件已配置");

  const unavailable = memberBudgetHomeSummaryViewModel({ status: "unavailable" });
  assert.equal(unavailable.stateLabel, "状态不可用");
  assert.equal(unavailable.nearBudgetLabel, "接近预算不可用");

  const empty = memberBudgetHomeSummaryViewModel({
    budgets: { items: [], data_status: "complete" },
    notification: null,
    notificationState: "not_configured",
  });
  assert.equal(empty.nearBudgetLabel, "暂无预算");
  assert.equal(empty.mailLabel, "邮件未配置");
});


test("settings home exposes workspace administrator permission without unavailable evidence", () => {
  assert.deepEqual(
    memberBudgetHomeSummaryViewModel({ status: "permission_required" }),
    {
      state: "permission_required",
      stateLabel: "需要权限",
      nearBudgetLabel: "需要工作区管理员权限",
      mailLabel: "预算与提醒已受限",
      actionLabel: "查看权限说明",
    },
  );
});


test("disabled email configuration stays distinct from an unconfigured recipient", () => {
  const view = memberBudgetViewModel({
    budgets: { items: [], data_status: "complete" },
    notification: null,
    notificationState: "disabled",
  });
  const home = memberBudgetHomeSummaryViewModel({
    budgets: { items: [], data_status: "complete" },
    notification: null,
    notificationState: "disabled",
  });

  assert.equal(view.notification.state, "disabled");
  assert.equal(view.notification.configured, false);
  assert.equal(home.mailLabel, "邮件配置未启用");
});


test("settings home uses the workspace administrator role for notification authorization", () => {
  const home = memberBudgetHomeSummaryViewModel({
    budgets: payload.budgets,
    budgetsState: "partial",
    notification: null,
    notificationState: "permission_required",
    alerts: payload.alerts,
    alertsState: "available",
  });

  assert.equal(home.state, "partial");
  assert.equal(home.nearBudgetLabel, "1 位接近预算");
  assert.equal(home.mailLabel, "需要工作区管理员权限");
});


test("test email response accepts only safe public categories", () => {
  assert.deepEqual(
    safeTestEmailResult({ state: "sent", sent_at: "2026-07-29T01:00:00Z", safe_error_category: null }),
    { state: "sent", label: "测试邮件已发送" },
  );
  assert.deepEqual(
    safeTestEmailResult({ state: "failed", safe_error_category: "permission_required", operation_id: "secret-op" }),
    { state: "permission_required", label: "托管身份缺少邮件发送权限" },
  );
  assert.deepEqual(
    safeTestEmailResult({ state: "failed", safe_error_category: "hostile-service-body" }),
    { state: "unavailable", label: "邮件服务暂时不可用" },
  );
});

test("missing alert evidence and unavailable feeds do not become observed zero", () => {
  const view = memberBudgetViewModel({
    budgets: payload.budgets,
    alerts: {
      items: [{
        budget_id: "budget-safe",
        threshold_pct: 95,
        estimated_spend_usd: null,
        pricing_coverage_pct: null,
        delivery_state: "failed",
      }],
    },
    alertsState: "unavailable",
  });

  assert.equal(view.alerts[0].spendLabel, "未计价");
  assert.equal(view.summary.sentAlertCount, null);
});

test("settings home distinguishes unavailable mail from not configured mail", () => {
  const unavailable = memberBudgetHomeSummaryViewModel({
    budgets: { items: [], data_status: "complete" },
    notificationState: "unavailable",
  });

  assert.equal(unavailable.mailLabel, "邮件状态不可用");
});

test("unavailable budget evidence makes every summary unavailable instead of zero", () => {
  const view = memberBudgetViewModel({
    budgetsState: "unavailable",
    alertsState: "unavailable",
    notificationState: "unavailable",
  });

  assert.equal(view.summary.estimatedSpendLabel, "不可用");
  assert.equal(view.summary.configuredCount, null);
  assert.equal(view.summary.nearBudgetCount, null);
  assert.equal(view.summary.sentAlertCount, null);
  assert.equal(view.summary.dataStatus, "unavailable");
});

test("configured member count includes persisted disabled budgets", () => {
  const view = memberBudgetViewModel({
    budgets: {
      data_status: "complete",
      items: [
        {
          budget_id: "budget-enabled",
          revision: 1,
          member: { member_ref: "member-enabled", display_name: "Enabled", identity_state: "active" },
          amount_usd: 100,
          thresholds_pct: [80, 95, 100],
          enabled: true,
          progress: { estimated_spend_usd: 10, pricing_coverage_pct: 100 },
        },
        {
          budget_id: "budget-disabled",
          revision: 2,
          member: { member_ref: "member-disabled", display_name: "Disabled", identity_state: "active" },
          amount_usd: 100,
          thresholds_pct: [80, 95, 100],
          enabled: false,
          progress: { estimated_spend_usd: 10, pricing_coverage_pct: 100 },
        },
      ],
    },
  });

  assert.equal(view.summary.configuredCount, 2);
  assert.equal(view.rows[1].canEdit, true);
  assert.equal(view.rows[1].canDisable, false);
  assert.equal(view.rows[1].lifecycleLabel, "预算已停用");
});

test("severity follows each budget thresholds and prior-month alerts do not mark current period", () => {
  const view = memberBudgetViewModel({
    periodKey: "2026-07",
    budgets: {
      items: [{
        budget_id: "budget-threshold",
        revision: 1,
        member: { member_ref: "member-threshold", display_name: "Threshold Member", identity_state: "active" },
        amount_usd: 100,
        thresholds_pct: [95, 100],
        enabled: true,
        progress: { estimated_spend_usd: 85, pricing_coverage_pct: 100 },
      }],
    },
    alerts: {
      items: [{
        budget_id: "budget-threshold",
        period_key: "2026-06",
        threshold_pct: 95,
        delivery_state: "sent",
      }],
    },
  });

  assert.equal(view.rows[0].severity, "normal");
  assert.equal(view.rows[0].statusLabel, "85%");
  assert.equal(view.rows[0].currentAlertState, "");
});

test("create choices exclude every member with an existing enabled or disabled budget", () => {
  const view = memberBudgetViewModel({
    budgets: {
      items: [
        {
          budget_id: "budget-a",
          revision: 1,
          member: { member_ref: "member-a", display_name: "A", identity_state: "active" },
          amount_usd: 100,
          enabled: true,
          thresholds_pct: [80],
          progress: {},
        },
        {
          budget_id: "budget-b",
          revision: 1,
          member: { member_ref: "member-b", display_name: "B", identity_state: "active" },
          amount_usd: 100,
          enabled: false,
          thresholds_pct: [80],
          progress: {},
        },
      ],
    },
    members: {
      items: [
        { member_ref: "member-a", display_name: "A", identity_state: "active" },
        { member_ref: "member-b", display_name: "B", identity_state: "active" },
        { member_ref: "member-c", display_name: "C", identity_state: "active" },
      ],
    },
  });

  assert.deepEqual(view.createMembers.map((item) => item.memberRef), ["member-c"]);
});

test("notification and alert availability remain independent", () => {
  const view = memberBudgetViewModel({
    budgets: { items: [], data_status: "complete" },
    notificationState: "unavailable",
    alertsState: "unavailable",
  });

  assert.equal(view.notification.state, "unavailable");
  assert.equal(view.alertsState, "unavailable");
  assert.equal(view.summary.sentAlertCount, null);
});
