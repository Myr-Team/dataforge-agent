import React, { useEffect, useRef, useState } from "react";
import {
  Bot,
  Loader2,
  Send,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import {
  clearFinOpsAssistantConversation,
  loadFinOpsAssistantConversations,
  loadFinOpsAssistantMessages,
  queryFinOpsAssistant,
} from "./api.js";


const DEFAULT_QUESTIONS = [
  "为什么发生变化？",
  "这是否属于异常？",
  "主要贡献来源是什么？",
  "有哪些可验证的优化方向？",
];


export function FinOpsAssistant({
  context,
  openRequest = 0,
  onClearContext = null,
  onEvidence = null,
}) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState([]);
  const [conversationRef, setConversationRef] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (openRequest > 0) setOpen(true);
  }, [openRequest]);

  const workspaceId = context?.filters?.workspace_id || "";

  useEffect(() => {
    if (!open || !workspaceId) return undefined;
    const controller = new AbortController();
    loadFinOpsAssistantConversations(workspaceId, {
      signal: controller.signal,
    }).then(async (payload) => {
      const latest = Array.isArray(payload?.items) ? payload.items[0] : null;
      if (!latest?.conversation_ref) return;
      const history = await loadFinOpsAssistantMessages(
        latest.conversation_ref,
        workspaceId,
        { signal: controller.signal },
      );
      setConversationRef(latest.conversation_ref);
      setMessages(Array.isArray(history?.items) ? history.items : []);
    }).catch((error) => {
      if (error?.name !== "AbortError") setMessages([]);
    });
    return () => controller.abort();
  }, [open, workspaceId]);

  const ask = async (rawQuestion) => {
    const question = String(rawQuestion || "").trim();
    if (!question || !context || busy) return;
    const history = messages
      .slice(-6)
      .map((item) => ({ role: item.role, content: item.content }));
    setMessages((items) => [...items, { role: "user", content: question }]);
    setInput("");
    setBusy(true);
    try {
      const response = await queryFinOpsAssistant({
        question,
        metric_context: context,
        history,
        ...(conversationRef ? { conversation_ref: conversationRef } : {}),
      });
      if (response?.conversation_ref) {
        setConversationRef(response.conversation_ref);
      }
      setMessages((items) => [
        ...items,
        {
          role: "assistant",
          content: response?.answer || "当前分析暂不可用。",
          evidenceRefs: Array.isArray(response?.evidence_refs) ? response.evidence_refs : [],
          evidenceState: response?.evidence_state || "unavailable",
          suggestions: Array.isArray(response?.suggested_questions)
            ? response.suggested_questions
            : [],
        },
      ]);
    } catch (error) {
      setMessages((items) => [
        ...items,
        {
          role: "assistant",
          content: error instanceof Error ? error.message : "当前分析暂不可用。",
          evidenceRefs: [],
          evidenceState: "unavailable",
          suggestions: [],
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const latestSuggestions = messages
    .slice()
    .reverse()
    .find((item) => item.role === "assistant" && item.suggestions?.length)
    ?.suggestions;
  const suggestions = latestSuggestions || DEFAULT_QUESTIONS;
  const clearHistory = async () => {
    if (!conversationRef || !workspaceId || busy) return;
    setBusy(true);
    try {
      await clearFinOpsAssistantConversation(
        conversationRef,
        workspaceId,
      );
      setConversationRef("");
      setMessages([]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className="finops-ai-launcher"
        aria-label={open ? "关闭运营 AI" : "打开运营 AI"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Sparkles size={17} />
        <span>运营 AI</span>
      </button>
      {open ? (
        <section
          className="finops-ai-popover"
          role="dialog"
          aria-modal="false"
          aria-label="运营指标 AI 助手"
        >
          <header>
            <span><Bot size={17} /></span>
            <div>
              <b>运营 AI</b>
              <small>历史保留 30 天，可跨设备继续</small>
            </div>
            <span className="finops-ai-header-actions">
              <button
                type="button"
                onClick={clearHistory}
                disabled={!conversationRef || busy}
                aria-label="清空历史"
                title="清空历史"
              >
                <Trash2 size={14} />
              </button>
              <button type="button" onClick={() => setOpen(false)} aria-label="关闭运营 AI">
                <X size={16} />
              </button>
            </span>
          </header>
          <div className="finops-ai-context">
            <span>正在询问</span>
            <b>{context?.label || "当前运营页面"}</b>
            {context?.dimension_value ? <small>{context.dimension_value}</small> : null}
            {onClearContext && context?.metric_id !== "operations_overview" ? (
              <button type="button" onClick={onClearContext}>清除指标</button>
            ) : null}
          </div>
          <div className="finops-ai-messages" aria-live="polite">
            {!messages.length ? (
              <p>可以直接询问当前指标的变化原因、异常判断、贡献来源和优化方向。</p>
            ) : null}
            {messages.map((message, index) => (
              <article className={message.role} key={`${message.role}:${index}`}>
                <span>{message.content}</span>
                {message.role === "assistant" && message.evidenceRefs?.length && onEvidence ? (
                  <button type="button" onClick={() => onEvidence(`AI 回答 · ${message.evidenceRefs.length} 条证据`)}>
                    查看证据
                  </button>
                ) : null}
              </article>
            ))}
            {busy ? <div className="finops-ai-thinking"><Loader2 className="spin" size={14} />正在询问当前指标</div> : null}
          </div>
          <div className="finops-ai-suggestions">
            {suggestions.slice(0, 4).map((question) => (
              <button type="button" key={question} disabled={busy} onClick={() => ask(question)}>
                {question}
              </button>
            ))}
          </div>
          <form
            className="finops-ai-input"
            onSubmit={(event) => {
              event.preventDefault();
              ask(input);
            }}
          >
            <input
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              maxLength={600}
              placeholder="针对当前指标提问…"
              aria-label="向运营 AI 提问"
            />
            <button type="submit" disabled={busy || !input.trim()} aria-label="发送问题">
              <Send size={15} />
            </button>
          </form>
        </section>
      ) : null}
    </>
  );
}
