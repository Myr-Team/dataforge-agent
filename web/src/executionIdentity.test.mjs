import assert from "node:assert/strict";
import test from "node:test";

import { executionRequestFields, readyExecutionState } from "./executionIdentity.js";

test("automatic analysis ready event preserves the active conversation", () => {
  const state = readyExecutionState(
    { run_id: "run-auto-5", conversation_id: null, origin: "workspace_auto_analysis" },
    "conversation-human",
  );

  assert.equal(state.runId, "run-auto-5");
  assert.equal(state.activeConversationId, "conversation-human");
  assert.equal(state.persistConversation, false);
});

test("human conversation ready event selects only its returned conversation", () => {
  const state = readyExecutionState(
    { run_id: "run-chat-5", conversation_id: "conversation-5", origin: "conversation" },
    null,
  );

  assert.equal(state.runId, "run-chat-5");
  assert.equal(state.activeConversationId, "conversation-5");
  assert.equal(state.persistConversation, true);
});

test("data workbench analysis does not inherit the active conversation", () => {
  const request = executionRequestFields({
    stayOnDashboard: true,
    executionOrigin: "data_send_analysis",
    activeConversationId: "conversation-human",
  });

  assert.deepEqual(request, {
    conversation_id: null,
    origin: "data_send_analysis",
    persist_messages: false,
  });
});
