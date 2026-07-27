import assert from "node:assert/strict";
import test from "node:test";

import { identityAccessViewModel, identityGroupSearchViewModel } from "./identityAccessViewModel.js";

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
