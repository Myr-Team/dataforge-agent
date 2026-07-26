const LEGACY_AUTO_ANALYSIS_PROMPT = "请基于当前工作区，先自动分析这批数据可以产品化成什么机会，并说明证据强弱、市场推断和下一步。";


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


export function executionMessageVisibility({ stayOnDashboard = false } = {}) {
  const hidden = stayOnDashboard === true;
  return {
    appendUser: !hidden,
    appendAssistant: !hidden,
  };
}


export function filterCustomerConversationMessages(messages = []) {
  return (Array.isArray(messages) ? messages : []).filter(
    (item) => !(
      item?.role === "user"
      && String(item?.text || "").trim() === LEGACY_AUTO_ANALYSIS_PROMPT
    ),
  );
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
