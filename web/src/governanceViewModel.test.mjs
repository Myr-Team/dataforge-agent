import assert from "node:assert/strict";
import test from "node:test";

import {
  appendAuditPage,
  auditEventViewModel,
  chargebackViewModel,
  governancePermissions,
  invitationLifecycleViewModel,
  roiViewModel,
  traceStatusLabel,
} from "./governanceViewModel.js";

test("configured monitoring is not labelled connected without delivery proof", () => {
  assert.equal(traceStatusLabel({ state: "partial" }), "已配置，尚未确认遥测到达");
  assert.notEqual(traceStatusLabel({ state: "partial" }), traceStatusLabel({ state: "connected" }));
});

test("local and Foundry ROI evidence states stay separate", () => {
  const model = roiViewModel({
    local: { status: "measured", business_value: { total: 120, currency: "CNY", status: "measured" } },
    provider: { status: "not_configured" },
  });
  assert.equal(model.localStatus, "measured");
  assert.equal(model.providerStatus, "not_configured");
  assert.equal(model.local.businessValue.text, "CNY 120.00");
  assert.equal(model.provider.businessValue.text, "未记录");
});

test("Foundry verified evidence never promotes an estimated local snapshot", () => {
  const model = roiViewModel({
    local: { status: "estimated", foundry_roi: { status: "connected", provider_snapshot: { status: "verified" } } },
  });
  assert.equal(model.localStatus, "estimated");
  assert.equal(model.providerStatus, "verified");
});

test("Foundry provider business value uses the provider amount contract", () => {
  const model = roiViewModel({
    local: {
      status: "estimated",
      foundry_roi: {
        status: "connected",
        provider_snapshot: { status: "verified", business_value: { amount: 1750, currency: "CNY", unit: "currency" } },
      },
    },
  });
  assert.equal(model.provider.businessValue.text, "CNY 1,750.00");
  assert.equal(model.local.businessValue.text, "未记录");
});

test("chargeback retains evidence status and currency without inventing zero", () => {
  const model = chargebackViewModel({
    members: [
      { member: { actor_id: "actor_0123456789abcdef0123456789abcdef01234567", status: "unknown_or_departed" }, groups: 1, cost: { total: null, status: "partial", currency: null, by_currency: { USD: 1.25 } } },
    ],
    groups: [{ member: { actor_id: "actor_0123456789abcdef0123456789abcdef01234567" }, total_tokens: 321, cost: { total: null, status: "partial", by_currency: { USD: 1.25 } } }],
  });
  assert.equal(model.rows[0].tokenText, "321");
  assert.equal(model.rows[0].costText, "USD 1.25（部分已计价）");
  assert.equal(model.rows[0].evidenceStatus, "partial");
  assert.ok(!model.rows[0].costText.includes("0.00"));
});

test("empty chargeback is reported as unrecorded", () => {
  const model = chargebackViewModel({ members: [], groups: [], totals: { total: null, status: "unknown", by_currency: {} } });
  assert.deepEqual(model.rows, []);
  assert.equal(model.totalCostText, "未记录");
});

test("null chargeback evidence never becomes a reported zero", () => {
  const model = chargebackViewModel({
    members: [{ member: { actor_id: "actor_0123456789abcdef0123456789abcdef01234567", status: "unknown_or_departed" }, cost: { total: null, status: "partial", by_currency: { USD: null } } }],
    groups: [{ member: { actor_id: "actor_0123456789abcdef0123456789abcdef01234567" }, total_tokens: null }],
    totals: { total: null, status: "partial", by_currency: { USD: null } },
  });
  assert.equal(model.rows[0].tokenText, "未记录");
  assert.equal(model.rows[0].costText, "未记录");
  assert.equal(model.totalCostText, "未记录");
});

test("audit detail ignores raw actor email OID correlation and secret-like fields", () => {
  const text = JSON.stringify(auditEventViewModel({
    actor_hash: "owner@contoso.com",
    actor: { email: "owner@contoso.com", oid: "raw-oid" },
    action: "connector.sync",
    resource_type: "connector",
    resource_id: "Password=secret",
    correlation: { request_id: "Bearer raw-token", run_id: "run-secret" },
    reason_code: "allowed",
    at: "2026-07-14T01:00:00Z",
    revision: 8,
  }));
  assert.ok(text.includes("actor_已脱敏"));
  assert.ok(text.includes("res_已脱敏"));
  assert.ok(!text.includes("contoso.com"));
  assert.ok(!text.includes("raw-oid"));
  assert.ok(!text.includes("Password"));
  assert.ok(!text.includes("Bearer"));
  assert.ok(!text.includes("run-secret"));
});

test("valid backend pseudonyms are rendered in bounded form", () => {
  const model = auditEventViewModel({
    actor_hash: "actor_0123456789abcdef0123456789abcdef01234567",
    resource_id: "res_89abcdef0123456789abcdef0123456789abcdef",
    action: "file.create",
    resource_type: "file",
    result: "allowed",
    revision: 1,
  });
  assert.equal(model.actor, "actor_01234567…4567");
  assert.equal(model.resource, "res_89abcdef…cdef");
});

test("audit pagination appends immutably and deduplicates revisions", () => {
  const first = { events: [{ revision: 4 }, { revision: 3 }], has_more: true, next_cursor: "cursor-3" };
  const second = { events: [{ revision: 3 }, { revision: 2 }], has_more: false, next_cursor: null };
  const merged = appendAuditPage(first, second);
  assert.deepEqual(merged.events.map((event) => event.revision), [4, 3, 2]);
  assert.equal(merged.has_more, false);
  assert.deepEqual(first.events.map((event) => event.revision), [4, 3]);
  assert.notStrictEqual(merged.events, first.events);
});

test("permissions come only from server permission fields", () => {
  assert.deepEqual(governancePermissions({ permissions: { role: "admin", can_read: true, can_update: false, can_delete: false } }), {
    role: "admin",
    canReadAudit: true,
    canManageMembers: true,
    canReadChargeback: true,
    reason: "",
  });
  assert.equal(governancePermissions({ current_actor: { email: "owner@example.com" } }).canManageMembers, false);
  assert.equal(governancePermissions({ current_actor: { email: "owner@example.com" } }).reason, "需要工作区所有者或管理员权限");
});

test("invitation lifecycle preserves pending accepted failed expired and revoked states", () => {
  const rows = invitationLifecycleViewModel([
    { email: "pending@example.com", role: "viewer", status: "pending", invitation_id: "invite-1" },
    { email: "active@example.com", role: "editor", status: "active", invitation_id: "invite-2" },
  ], [
    { email: "failed@example.com", role: "viewer", state: "failed" },
    { email: "expired@example.com", role: "viewer", state: "expired" },
    { email: "revoked@example.com", role: "viewer", state: "revoked" },
  ]);
  assert.deepEqual(rows.map((row) => row.state), ["pending", "accepted", "failed", "expired", "revoked"]);
});

test("customer-facing governance labels are valid UTF-8 without literal question marks or mojibake", () => {
  const labels = [
    traceStatusLabel({ state: "connected" }),
    traceStatusLabel({ state: "partial" }),
    traceStatusLabel({ state: "not_configured" }),
    traceStatusLabel({ state: "unavailable" }),
    roiViewModel({ local: { status: "verified" }, provider: { status: "not_configured" } }).local.label,
  ].join("|");
  assert.ok(!labels.includes("?"));
  assert.ok(!/[锟斤拷]|(?:Ã|Â|â€)/u.test(labels));
  assert.ok(!labels.includes("�"));
});
