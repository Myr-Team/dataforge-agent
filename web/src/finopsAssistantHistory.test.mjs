import assert from "node:assert/strict";
import test from "node:test";

import {
  clearFinOpsAssistantHistoryCache,
  peekFinOpsAssistantHistory,
  prefetchFinOpsAssistantHistory,
  writeFinOpsAssistantHistory,
} from "./finopsAssistantHistory.js";


test("assistant history prefetch is workspace-scoped, cached, and request-deduplicated", async () => {
  clearFinOpsAssistantHistoryCache("ws-prefetch");
  let conversationLoads = 0;
  let messageLoads = 0;
  const loaders = {
    loadConversations: async () => {
      conversationLoads += 1;
      await Promise.resolve();
      return { items: [{ conversation_ref: "conversation-1" }] };
    },
    loadMessages: async () => {
      messageLoads += 1;
      return { items: [{ role: "assistant", content: "cached answer" }] };
    },
  };

  const [first, second] = await Promise.all([
    prefetchFinOpsAssistantHistory("ws-prefetch", loaders),
    prefetchFinOpsAssistantHistory("ws-prefetch", loaders),
  ]);

  assert.deepEqual(first, second);
  assert.equal(conversationLoads, 1);
  assert.equal(messageLoads, 1);
  assert.equal(peekFinOpsAssistantHistory("ws-prefetch").messages[0].content, "cached answer");

  await prefetchFinOpsAssistantHistory("ws-prefetch", loaders);
  assert.equal(conversationLoads, 1);
  assert.equal(messageLoads, 1);
});


test("cross-device history restores structured assistant sections and evidence labels", async () => {
  clearFinOpsAssistantHistoryCache("ws-structured");
  const history = await prefetchFinOpsAssistantHistory("ws-structured", {
    loadConversations: async () => ({ items: [{ conversation_ref: "conversation-structured" }] }),
    loadMessages: async () => ({
      items: [{
        role: "assistant",
        content: "结论正文",
        metric_context_payload: {
          response_sections: {
            conclusion: "结论正文",
            basis: "证据依据",
            impact: "影响说明",
            recommendation: "建议动作",
            caveat: "判断边界",
          },
          evidence_refs: ["req_safe"],
          evidence_labels: ["销售分析 · 模型调用"],
          evidence_state: "observed",
          suggested_questions: ["下一步怎么验证？"],
        },
      }],
    }),
  });

  assert.deepEqual(history.messages[0].sections, {
    conclusion: "结论正文",
    basis: "证据依据",
    impact: "影响说明",
    recommendation: "建议动作",
    caveat: "判断边界",
  });
  assert.deepEqual(history.messages[0].evidenceRefs, ["req_safe"]);
  assert.deepEqual(history.messages[0].evidenceLabels, ["销售分析 · 模型调用"]);
});


test("assistant history writes and clears one workspace without touching another", () => {
  clearFinOpsAssistantHistoryCache();
  writeFinOpsAssistantHistory("ws-a", {
    conversationRef: "conversation-a",
    messages: [{ role: "user", content: "hello" }],
  });
  writeFinOpsAssistantHistory("ws-b", {
    conversationRef: "conversation-b",
    messages: [],
  });

  clearFinOpsAssistantHistoryCache("ws-a");

  assert.equal(peekFinOpsAssistantHistory("ws-a"), null);
  assert.equal(peekFinOpsAssistantHistory("ws-b").conversationRef, "conversation-b");
  clearFinOpsAssistantHistoryCache();
});


test("clearing history invalidates an older prefetch response", async () => {
  clearFinOpsAssistantHistoryCache("ws-clear-race");
  let resolveConversations;
  const conversations = new Promise((resolve) => { resolveConversations = resolve; });
  const pending = prefetchFinOpsAssistantHistory("ws-clear-race", {
    loadConversations: () => conversations,
    loadMessages: async () => ({ items: [] }),
  });
  await Promise.resolve();

  clearFinOpsAssistantHistoryCache("ws-clear-race");
  resolveConversations({ items: [] });
  await pending;

  assert.equal(peekFinOpsAssistantHistory("ws-clear-race"), null);
});
