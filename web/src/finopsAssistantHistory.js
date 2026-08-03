import {
  loadFinOpsAssistantConversations,
  loadFinOpsAssistantMessages,
} from "./api.js";


const HISTORY_CACHE_TTL_MS = 5 * 60 * 1000;
const historyCache = new Map();
const historyRequests = new Map();
const historyGenerations = new Map();
let historyEpoch = 0;


function workspaceKey(workspaceId) {
  return String(workspaceId || "").trim();
}


function normalizeHistoryMessage(item = {}) {
  const payload = item?.metric_context_payload;
  if (item?.role !== "assistant" || !payload || typeof payload !== "object") {
    return item;
  }
  return {
    ...item,
    sections: payload.response_sections || null,
    evidenceRefs: Array.isArray(payload.evidence_refs) ? payload.evidence_refs : [],
    evidenceLabels: Array.isArray(payload.evidence_labels) ? payload.evidence_labels : [],
    evidenceState: payload.evidence_state || "unavailable",
    suggestions: Array.isArray(payload.suggested_questions) ? payload.suggested_questions : [],
  };
}


export function peekFinOpsAssistantHistory(workspaceId) {
  const key = workspaceKey(workspaceId);
  return key ? historyCache.get(key) || null : null;
}


export function writeFinOpsAssistantHistory(workspaceId, value = {}) {
  const key = workspaceKey(workspaceId);
  if (!key) return null;
  const next = {
    conversationRef: String(value?.conversationRef || ""),
    messages: Array.isArray(value?.messages) ? value.messages : [],
    loadedAt: Date.now(),
  };
  historyCache.set(key, next);
  return next;
}


export function clearFinOpsAssistantHistoryCache(workspaceId = "") {
  const key = workspaceKey(workspaceId);
  if (key) {
    historyGenerations.set(key, (historyGenerations.get(key) || 0) + 1);
    historyCache.delete(key);
    historyRequests.delete(key);
    return;
  }
  historyEpoch += 1;
  historyCache.clear();
  historyRequests.clear();
  historyGenerations.clear();
}


export function prefetchFinOpsAssistantHistory(workspaceId, options = {}) {
  const key = workspaceKey(workspaceId);
  if (!key) return Promise.resolve(null);
  const cached = historyCache.get(key);
  const force = options?.force === true;
  if (!force && cached && (Date.now() - cached.loadedAt) < HISTORY_CACHE_TTL_MS) {
    return Promise.resolve(cached);
  }
  if (historyRequests.has(key)) return historyRequests.get(key);

  const loadConversations = options?.loadConversations || loadFinOpsAssistantConversations;
  const loadMessages = options?.loadMessages || loadFinOpsAssistantMessages;
  const requestEpoch = historyEpoch;
  const requestGeneration = historyGenerations.get(key) || 0;
  const stillCurrent = () => (
    historyEpoch === requestEpoch
    && (historyGenerations.get(key) || 0) === requestGeneration
  );
  const request = Promise.resolve()
    .then(() => loadConversations(key))
    .then(async (payload) => {
      const latest = Array.isArray(payload?.items) ? payload.items[0] : null;
      const conversationRef = String(latest?.conversation_ref || "");
      if (!conversationRef) {
        return stillCurrent()
          ? writeFinOpsAssistantHistory(key, { conversationRef: "", messages: [] })
          : peekFinOpsAssistantHistory(key);
      }
      const history = await loadMessages(conversationRef, key);
      return stillCurrent()
        ? writeFinOpsAssistantHistory(key, {
          conversationRef,
          messages: Array.isArray(history?.items)
            ? history.items.map(normalizeHistoryMessage)
            : [],
        })
        : peekFinOpsAssistantHistory(key);
    })
    .finally(() => {
      if (historyRequests.get(key) === request) historyRequests.delete(key);
    });
  historyRequests.set(key, request);
  return request;
}
