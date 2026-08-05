import assert from "node:assert/strict";
import test from "node:test";

import {
  matchingWorkspaceValue,
  workspaceBootstrapFailure,
} from "./workspaceBootstrap.js";


test("same-workspace refresh keeps verified bootstrap state", () => {
  const verified = {
    workspace_id: "ws-a",
    sections: { finops: { visible: true } },
  };

  assert.equal(matchingWorkspaceValue(verified, "ws-a"), verified);
  assert.equal(matchingWorkspaceValue(verified, "ws-b"), null);
});


test("bootstrap failure is shown only without matching verified state", () => {
  const verified = { workspace_id: "ws-a", allowed: true };
  const failure = new Error("服务响应超时，请重试");

  assert.equal(workspaceBootstrapFailure(verified, "ws-a", failure), "");
  assert.equal(
    workspaceBootstrapFailure(verified, "ws-b", failure),
    "服务响应超时，请重试",
  );
});
