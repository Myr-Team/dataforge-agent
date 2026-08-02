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
