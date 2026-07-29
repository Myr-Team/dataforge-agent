import assert from "node:assert/strict";
import test from "node:test";

import {
  memberBudgetHomeSummaryViewModel,
  memberBudgetViewModel,
  safeTestEmailResult,
} from "./memberBudgetViewModel.js";

const payload = {
  budgets: {
    items: [{
      budget_id: "budget-safe",
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
      sender_display_name: "DataForge",
      subject_template: "{{member_name}} 预算提醒",
      body_template: "{{estimated_spend}} / {{budget_amount}}",
      enabled: true,
      revision: 2,
    },
  },
  alerts: {
    items: [{
      alert_id: "alert-safe",
      tenant_ref: "tenant-raw-must-not-survive",
      actor_ref: "actor-raw-must-not-survive",
      budget_id: "budget-safe",
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
  assert.equal(view.rows[0].identityLabel, "身份已停用");
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
