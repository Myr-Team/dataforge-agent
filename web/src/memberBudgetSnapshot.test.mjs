import assert from "node:assert/strict";
import test from "node:test";

async function loadSnapshotUtilities() {
  const server = await import("vite").then(({ createServer }) => createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false, ws: false },
  }));
  const utilities = await server.ssrLoadModule("/src/MemberBudgetSettingsPage.jsx");
  return { ...utilities, close: () => server.close() };
}

const budget = {
  data_status: "partial",
  items: [{
    budget_id: "budget-safe", member_ref: "member-safe", amount_usd: 100, thresholds_pct: [80], enabled: true, revision: 1,
    member: { member_ref: "member-safe", display_name: "Finance Admin", identity_state: "active", workspace_ids: ["demo"], department_labels: ["Finance"] },
    progress: { estimated_spend_usd: 80, primary_model: "terra" },
  }],
};
const members = { data_status: "complete", items: [{ member_ref: "member-safe", display_name: "Finance Admin", identity_state: "active" }] };
const notification = { data_status: "complete", item: { recipient_email: "admin@example.test", revision: 1 } };
const alerts = { data_status: "partial", items: [] };

test("partial member-budget snapshots never dereference missing resources or invent alerts", async () => {
  const { memberBudgetSnapshotView, close } = await loadSnapshotUtilities();
  try {
  const missingBudget = memberBudgetSnapshotView({ members, notification, alerts });
  assert.equal(missingBudget.state, "unavailable");

  const missingNotification = memberBudgetSnapshotView({ budgets: budget, members, alerts });
  assert.equal(missingNotification.state, "partial");
  assert.equal(missingNotification.view.notification.state, "unavailable");

  const missingAlerts = memberBudgetSnapshotView({ budgets: budget, members, notification });
  assert.equal(missingAlerts.state, "partial");
  assert.equal(missingAlerts.view.alertsState, "unavailable");
  } finally {
    await close();
  }
});

test("notification HTTP failures project only safe honest states", async () => {
  const { safeMemberBudgetFailureState, close } = await loadSnapshotUtilities();
  try {
  assert.equal(safeMemberBudgetFailureState({ status: 404 }), "not_configured");
  assert.equal(safeMemberBudgetFailureState({ status: 403 }), "permission_required");
  assert.equal(safeMemberBudgetFailureState({ status: 503 }), "unavailable");
  } finally {
    await close();
  }
});
