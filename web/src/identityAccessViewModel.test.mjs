import assert from "node:assert/strict";
import test from "node:test";

import { identityAccessViewModel, identityGroupSearchViewModel, identitySessionViewModel } from "./identityAccessViewModel.js";

test("trusted Entra session explains the current workspace role without exposing internal ids", () => {
  const view = identitySessionViewModel({
    authState: "authenticated",
    user: {
      name: "傅先生",
      email: "demo.admin@example.com",
      identityProvider: "microsoft_entra",
      identitySource: "trusted_proxy",
      oid: "must-not-surface",
    },
    access: {
      allowed: true,
      role: "owner",
      reason_code: "owner_match",
      tenant_ref: "must-not-surface",
    },
  });

  assert.equal(view.trusted, true);
  assert.equal(view.displayName, "傅先生");
  assert.equal(view.identityLabel, "Microsoft Entra ID");
  assert.equal(view.roleLabel, "工作区所有者");
  assert.equal(view.authorizationLabel, "工作区创建者授权");
  assert.equal(JSON.stringify(view).includes("must-not-surface"), false);
});

test("unavailable production identity never falls back to a local demo label", () => {
  const view = identitySessionViewModel({ authState: "unavailable", user: {}, access: null });

  assert.equal(view.trusted, false);
  assert.equal(view.displayName, "身份信息暂不可用");
  assert.equal(view.identityLabel, "等待可信登录信息");
  assert.equal(view.roleLabel, "尚未核验");
});

test("identity access uses friendly group names and workspace-scoped mappings", () => {
  const view = identityAccessViewModel({
    mapping_count: 2,
    permissions: {
      "User.ReadBasic.All": "configured",
      "GroupMember.Read.All": "unavailable",
    },
    membership_resolution: {
      claims: "enabled",
      overage_fallback: "enabled",
      failure_mode: "explicit_membership_only",
    },
    mappings: [{
      mapping_id: "mapping_finance",
      display_name: "财务运营组",
      role: "viewer",
      workspace_ids: ["ws-current"],
      priority: 100,
      enabled: true,
      revision: 2,
    }, {
      mapping_id: "mapping_other",
      display_name: "其他组",
      role: "editor",
      workspace_ids: ["ws-other"],
      priority: 100,
      enabled: true,
      revision: 1,
    }],
  }, "ws-current");

  assert.equal(view.mappings.length, 1);
  assert.equal(view.mappings[0].name, "财务运营组");
  assert.equal(view.permissions.groupMembership.ready, false);
  assert.equal(JSON.stringify(view).includes("group_id"), false);
});

test("group search keeps raw id only in transient selection data", () => {
  const result = identityGroupSearchViewModel({
    groups: [{ id: "raw-group-id", display_name: "Finance" }],
  });

  assert.deepEqual(result, [{ id: "raw-group-id", name: "Finance" }]);
});
