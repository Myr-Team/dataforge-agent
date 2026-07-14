import assert from "node:assert/strict";
import test from "node:test";

import {
  auditPageFailure,
  auditPageSuccess,
  createGovernanceRequestGuard,
} from "./governanceRequestState.js";
import * as governanceState from "./governanceRequestState.js";

test("stale audit cursor response cannot commit after workspace generation changes", () => {
  const guard = createGovernanceRequestGuard();
  const workspaceA = guard.begin("workspace-a");
  const cursorRequest = guard.capture(workspaceA, "cursor-a");
  const workspaceB = guard.begin("workspace-b");

  assert.equal(guard.isCurrent(cursorRequest, "workspace-b"), false);
  assert.equal(guard.isCurrent(cursorRequest, "workspace-a"), false);
  assert.equal(guard.capture(workspaceB, "cursor-a", "workspace-a"), null);
  assert.equal(guard.capture(workspaceB, "cursor-b", "workspace-b")?.cursor, "cursor-b");

  assert.equal(typeof governanceState.workspaceBoundMemberContract, "function");
  assert.deepEqual(
    governanceState.workspaceBoundMemberContract("workspace-b", "workspace-a", [{ subject_label: "member_old" }], { permissions: { actions: { "member.manage": true } } }),
    { ready: false, rows: [], meta: null },
  );
  assert.deepEqual(
    governanceState.workspaceBoundGovernanceData("workspace-b", { workspaceId: "workspace-a", audit: { events: [{ revision: 2 }], next_cursor: "cursor-a" }, auditRetryCursor: "cursor-a" }),
    { workspaceId: "workspace-b", loading: true, trace: null, roi: null, chargeback: null, audit: null, auditRetryCursor: "", errors: {} },
  );
});

test("cursor failure preserves loaded events and retries the same cursor", () => {
  const current = {
    audit: { events: [{ revision: 4 }, { revision: 3 }], has_more: true, next_cursor: "cursor-3" },
    errors: {},
  };
  const failed = auditPageFailure(current, "cursor-3", "更早的审计事件读取失败，请重试");

  assert.deepEqual(failed.audit.events, [{ revision: 4 }, { revision: 3 }]);
  assert.equal(failed.audit.next_cursor, "cursor-3");
  assert.equal(failed.auditRetryCursor, "cursor-3");
  assert.equal(failed.errors.auditPage, "更早的审计事件读取失败，请重试");

  const retried = auditPageSuccess(failed, { events: [{ revision: 2 }], has_more: false, next_cursor: null });
  assert.deepEqual(retried.audit.events.map((event) => event.revision), [4, 3, 2]);
  assert.equal(retried.auditRetryCursor, "");
  assert.equal(retried.errors.auditPage, "");
});
