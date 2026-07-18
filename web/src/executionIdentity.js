export function readyExecutionState(data, previousConversationId = null) {
  const origin = data?.origin || "conversation";
  const runId = data?.run_id || null;
  const isConversation = origin === "conversation" && Boolean(data?.conversation_id);
  return {
    runId,
    activeConversationId: isConversation ? data.conversation_id : previousConversationId,
    persistConversation: isConversation,
  };
}

export function executionRequestFields({ stayOnDashboard = false, executionOrigin, activeConversationId = null, newConversation = false } = {}) {
  const origin = executionOrigin || (stayOnDashboard ? "workspace_auto_analysis" : "conversation");
  const isConversation = origin === "conversation";
  return {
    conversation_id: isConversation && !newConversation ? activeConversationId : null,
    origin,
    persist_messages: isConversation,
  };
}
