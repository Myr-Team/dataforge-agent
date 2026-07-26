import assert from "node:assert/strict";
import test from "node:test";

import {
  executionMessageVisibility,
  executionRequestFields,
  filterCustomerConversationMessages,
  readyExecutionState,
} from "./executionIdentity.js";

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

test("automatic analysis stays out of customer-visible conversation messages", () => {
  assert.deepEqual(executionMessageVisibility({ stayOnDashboard: true }), {
    appendUser: false,
    appendAssistant: false,
  });
  assert.deepEqual(executionMessageVisibility({ stayOnDashboard: false }), {
    appendUser: true,
    appendAssistant: true,
  });
  assert.deepEqual(executionMessageVisibility({}), {
    appendUser: true,
    appendAssistant: true,
  });
});

test("historical restore removes only the exact legacy automatic-analysis prompt", () => {
  const legacy = "请基于当前工作区，先自动分析这批数据可以产品化成什么机会，并说明证据强弱、市场推断和下一步。";
  const messages = filterCustomerConversationMessages([
    { role: "user", text: legacy },
    { role: "assistant", text: "旧自动分析结果" },
    { role: "user", text: `${legacy} 但请只看华东区域。` },
  ]);

  assert.deepEqual(messages, [
    { role: "assistant", text: "旧自动分析结果" },
    { role: "user", text: `${legacy} 但请只看华东区域。` },
  ]);
});
