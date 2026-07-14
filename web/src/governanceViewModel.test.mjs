import assert from "node:assert/strict";
import test from "node:test";

import {
  appendAuditPage,
  auditEventViewModel,
  chargebackViewModel,
  directorySelectionViewModel,
  governancePermissions,
  invitationLifecycleViewModel,
  memberDirectoryViewModel,
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

test("production Foundry status object preserves configured unverified semantics", () => {
  const model = roiViewModel({
    local: {
      status: "estimated",
      foundry_roi: {
        status: {
          state: "configured_unverified",
          configured: true,
          source: "foundry_roi_provider",
          observed_at: "2026-07-14T00:00:00Z",
          reason: "Provider proof awaits an externally signed attestation",
        },
        provider_snapshot: null,
        difference: null,
        reconciliation: { reconciled: false, reason: "provider snapshot unavailable" },
      },
    },
  });
  assert.equal(model.foundryConnectionState, "configured_unverified");
  assert.equal(model.providerStatus, "configured_unverified");
  assert.equal(model.provider.label, "已配置，证据未验证");
  assert.equal(model.provider.businessValue.text, "未记录");
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

test("chargeback renders only bounded server subject labels and never raw email fallback", () => {
  const safe = chargebackViewModel({
    members: [{ member: { subject_label: "member_0123456789abcdef0123456789abcdef01234567", actor_id: "owner-raw-oid", email: "owner@contoso.com", name: "Owner", status: "active" }, cost: { status: "unknown", total: null, currency: null, by_currency: {} } }],
    groups: [],
  });
  const unsafeFallback = chargebackViewModel({
    members: [{ member: { actor_id: "owner-raw-oid", email: "owner@contoso.com", name: "Owner", status: "active" }, cost: { status: "unknown", total: null, currency: null, by_currency: {} } }],
    groups: [],
  });
  assert.equal(safe.rows[0].memberLabel, "member_01234567…4567");
  assert.equal(unsafeFallback.rows[0].memberLabel, "成员（已脱敏）");
  assert.ok(!JSON.stringify([safe, unsafeFallback]).includes("contoso.com"));
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
  assert.deepEqual(governancePermissions({ permissions: { actions: { "audit.read": true, "chargeback.read": true, "invitation.read": true, "member.manage": true }, reasons: {} } }), {
    canReadAudit: true,
    canManageMembers: true,
    canReadChargeback: true,
    canReadInvitations: true,
    reasons: {},
  });
  const unknown = governancePermissions({ current_actor: { email: "owner@example.com" }, permissions: { actions: { "audit.read": true } } });
  assert.equal(unknown.canManageMembers, false);
  assert.equal(unknown.canReadChargeback, false);
  assert.equal(unknown.reasons["member.manage"], "服务端未提供 member.manage 权限");
});

test("invitation lifecycle preserves reloadable history without email-based merging", () => {
  const subject = "member_0123456789abcdef0123456789abcdef01234567";
  const rows = invitationLifecycleViewModel({ invitations: [
    { invitation_ref: "invite_1111111111111111111111111111111111111111", subject_label: subject, role: "viewer", state: "accepted", updated_at: "2026-07-14T01:00:00Z" },
    { invitation_ref: "invite_2222222222222222222222222222222222222222", subject_label: subject, role: "viewer", state: "failed", updated_at: "2026-07-14T02:00:00Z", email: "leak@contoso.com" },
    { invitation_ref: "invite_3333333333333333333333333333333333333333", subject_label: "member_89abcdef0123456789abcdef0123456789abcdef", role: "editor", state: "removed", updated_at: "2026-07-14T03:00:00Z" },
  ] });
  assert.deepEqual(rows.map((row) => row.state), ["accepted", "failed", "removed"]);
  assert.equal(rows[0].subjectLabel, "member_01234567…4567");
  assert.equal(rows[1].subjectLabel, "member_01234567…4567");
  assert.equal(rows.length, 3);
  assert.ok(!JSON.stringify(rows).includes("contoso.com"));
});

test("settings member rows use only server subject labels and discard raw identity fields", () => {
  const rows = memberDirectoryViewModel([
    {
      subject_label: "member_0123456789abcdef0123456789abcdef01234567",
      email: "owner@contoso.com",
      actor_id: "owner-raw-oid",
      tenant_id: "tenant-secret",
      name: "Owner Person",
      role: "owner",
      status: "active",
      usage: { runs: 2, total_tokens: 300 },
    },
    {
      email: "unsafe@contoso.com",
      actor_id: "unsafe-oid",
      tenant_id: "unsafe-tenant",
      role: "editor",
      status: "pending",
    },
  ]);

  assert.equal(rows[0].subjectLabel, "member_01234567…4567");
  assert.equal(rows[0].actionRef, "member_0123456789abcdef0123456789abcdef01234567");
  assert.equal(rows[1].subjectLabel, "成员（已脱敏）");
  assert.equal(rows[1].actionRef, "");
  assert.ok(!JSON.stringify(rows).includes("contoso.com"));
  assert.ok(!JSON.stringify(rows).includes("raw-oid"));
  assert.ok(!JSON.stringify(rows).includes("tenant-secret"));
  assert.ok(!JSON.stringify(rows).includes("Owner Person"));
});

test("directory selections retain only server references and pseudonyms", () => {
  const rows = directorySelectionViewModel({ users: [{
    selection_ref: "selection_0123456789abcdef0123456789abcdef01234567",
    subject_label: "member_89abcdef0123456789abcdef0123456789abcdef",
    display_name: "Private Directory Name",
    email: "private.directory@example.com",
    user_principal_name: "private.directory@example.com",
    id: "private-directory-oid",
  }] });

  assert.deepEqual(rows, [{
    selectionRef: "selection_0123456789abcdef0123456789abcdef01234567",
    subjectLabel: "member_89abcdef…cdef",
  }]);
  assert.ok(!JSON.stringify(rows).includes("Private Directory Name"));
  assert.ok(!JSON.stringify(rows).includes("example.com"));
  assert.ok(!JSON.stringify(rows).includes("private-directory-oid"));
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
