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
  queryFinOpsAssistant,
} from "./api.js";
import {
  clearFinOpsAssistantHistoryCache,
  peekFinOpsAssistantHistory,
  prefetchFinOpsAssistantHistory,
  writeFinOpsAssistantHistory,
} from "./finopsAssistantHistory.js";
import { assistantFailureMessage, contextualAssistantQuestion } from "./finopsInteraction.js";


const DEFAULT_QUESTIONS = [
  "为什么发生变化？",
  "这是否属于异常？",
  "主要贡献来源是什么？",
  "有哪些可验证的优化方向？",
];

const COST_QUESTIONS = Object.freeze([
  "本月估算成本主要由哪些部门和模型贡献？",
  "价目覆盖率如何影响当前成本可信度？",
  "缓存命中率提升后可能节省多少？",
  "哪些高成本请求值得优先下钻？",
]);

const ROI_QUESTIONS = Object.freeze([
  "本期 ROI 测算使用了哪些投入假设？",
  "哪些收益已经观测，哪些仍待验证？",
  "模型成本变化会怎样影响回收周期？",
  "下一步应补充哪些业务结果证据？",
]);

const RISK_QUESTIONS = Object.freeze([
  "当前最高优先级风险的判定依据是什么？",
  "哪些请求构成了代表证据？",
  "建议先验证哪一项优化？",
  "哪些结论仍受证据不足限制？",
]);


export function assistantStarterQuestions(context = {}) {
  const metricId = String(context?.metric_id || "").toLowerCase();
  if (["estimated_cost", "cost", "unpriced_requests"].includes(metricId)) return [...COST_QUESTIONS];
  if (["roi_ratio", "roi", "monthly_net_benefit"].includes(metricId)) return [...ROI_QUESTIONS];
  if (metricId === "risk_summary" || context?.policy_type) return [...RISK_QUESTIONS];
  return [...DEFAULT_QUESTIONS];
}


export function publicAssistantContent(value) {
  return String(value || "")
    .replace(/\[\s*req_[A-Za-z0-9_-]+\s*\]/gi, "")
    .replace(/\breq_[A-Za-z0-9_-]+\b/gi, "运营证据")
    .replace(/\s+([，。；：,.!?])/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}


function assistantMessageSections(message = {}) {
  const value = message.sections || message.metric_context_payload?.response_sections;
  if (!value || typeof value !== "object") return null;
  const sections = {
    conclusion: publicAssistantContent(value.conclusion),
    basis: publicAssistantContent(value.basis),
    impact: publicAssistantContent(value.impact),
    recommendation: publicAssistantContent(value.recommendation),
    caveat: publicAssistantContent(value.caveat),
  };
  return Object.values(sections).every(Boolean) ? sections : null;
}


export function FinOpsAssistant({
  context,
  openRequest = 0,
  onClearContext = null,
  onEvidence = null,
}) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("quick");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [messages, setMessages] = useState([]);
  const [conversationRef, setConversationRef] = useState("");
  const inputRef = useRef(null);
  const interactionVersionRef = useRef(0);
  const lastOpenRequestRef = useRef(0);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const workspaceId = context?.filters?.workspace_id || "";

  useEffect(() => {
    interactionVersionRef.current = 0;
    if (!workspaceId) {
      setConversationRef("");
      setMessages([]);
      setHistoryLoading(false);
      return undefined;
    }
    const cached = peekFinOpsAssistantHistory(workspaceId);
    setConversationRef(cached?.conversationRef || "");
    setMessages(cached?.messages || []);
    setHistoryLoading(!cached);
    let active = true;
    const startVersion = interactionVersionRef.current;
    prefetchFinOpsAssistantHistory(workspaceId)
      .then((history) => {
        if (!active || interactionVersionRef.current !== startVersion) return;
        setConversationRef(history?.conversationRef || "");
        setMessages(history?.messages || []);
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) setHistoryLoading(false);
      });
    return () => { active = false; };
  }, [workspaceId]);

  const ask = async (rawQuestion) => {
    const question = String(rawQuestion || "").trim();
    if (!question || !context || busy) return;
    const persistentMessages = messages.filter((item) => !item.transient);
    const lastPersistent = persistentMessages[persistentMessages.length - 1];
    const pendingMessages = lastPersistent?.role === "user" && lastPersistent.content === question
      ? persistentMessages
      : [...persistentMessages, { role: "user", content: question }];
    const history = persistentMessages
      .slice(-6)
      .map((item) => ({
        role: item.role,
        content: (
          item.role === "assistant"
            ? publicAssistantContent(item.content)
            : String(item.content || "")
        ).slice(0, 600),
      }));
    interactionVersionRef.current += 1;
    setMessages(pendingMessages);
    setInput("");
    setBusy(true);
    try {
      const response = await queryFinOpsAssistant({
        question,
        mode,
        metric_context: context,
        history,
        ...(conversationRef ? { conversation_ref: conversationRef } : {}),
      });
      const nextConversationRef = response?.conversation_ref || conversationRef;
      if (nextConversationRef) setConversationRef(nextConversationRef);
      setMessages(() => {
        const next = [...pendingMessages, {
          role: "assistant",
          content: response?.answer || "当前分析暂不可用。",
          evidenceRefs: Array.isArray(response?.evidence_refs) ? response.evidence_refs : [],
          evidenceLabels: Array.isArray(response?.evidence_labels)
            ? response.evidence_labels.filter((value) => value && !/^req_/i.test(String(value)))
            : [],
          knowledgeCitations: Array.isArray(response?.knowledge_citations)
            ? response.knowledge_citations.filter((value) => String(value || "").startsWith("内部知识：")).slice(0, 4)
            : [],
          evidenceState: response?.evidence_state || "unavailable",
          sections: response?.sections || null,
          suggestions: Array.isArray(response?.suggested_questions)
            ? response.suggested_questions
            : [],
        }];
        writeFinOpsAssistantHistory(workspaceId, {
          conversationRef: nextConversationRef,
          messages: next,
        });
        return next;
      });
    } catch (error) {
      setMessages(() => {
        const next = [...pendingMessages, {
          role: "assistant",
          content: assistantFailureMessage(error),
          evidenceRefs: [],
          evidenceState: "unavailable",
          suggestions: [],
          retryQuestion: question,
          transient: true,
        }];
        return next;
      });
      writeFinOpsAssistantHistory(workspaceId, { conversationRef, messages: persistentMessages });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (
      openRequest <= 0
      || openRequest <= lastOpenRequestRef.current
      || !context
    ) return;
    lastOpenRequestRef.current = openRequest;
    const question = contextualAssistantQuestion(context);
    setOpen(true);
    setInput(question);
    Promise.resolve().then(() => ask(question));
  }, [context, openRequest]);

  const latestSuggestions = messages
    .slice()
    .reverse()
    .find((item) => item.role === "assistant" && item.suggestions?.length)
    ?.suggestions;
  const suggestions = latestSuggestions || assistantStarterQuestions(context);
  const clearHistory = async () => {
    if (!conversationRef || !workspaceId || busy) return;
    setBusy(true);
    try {
      await clearFinOpsAssistantConversation(
        conversationRef,
        workspaceId,
      );
      interactionVersionRef.current += 1;
      clearFinOpsAssistantHistoryCache(workspaceId);
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
          <div className="finops-ai-mode" role="group" aria-label="分析深度">
            <button
              type="button"
              className={mode === "quick" ? "active" : ""}
              aria-pressed={mode === "quick"}
              onClick={() => setMode("quick")}
              disabled={busy}
            >
              快速回答
            </button>
            <button
              type="button"
              className={mode === "deep" ? "active" : ""}
              aria-pressed={mode === "deep"}
              onClick={() => setMode("deep")}
              disabled={busy}
            >
              深入分析
            </button>
            <small>{mode === "quick" ? "适合指标解释" : "使用 FinOps 分析 Agent"}</small>
          </div>
          <div className="finops-ai-messages" aria-live="polite">
            {historyLoading ? <div className="finops-ai-history-sync"><Loader2 className="spin" size={12} />正在同步历史</div> : null}
            {!messages.length ? (
              <p>可以直接询问当前指标的变化原因、异常判断、贡献来源和优化方向。</p>
            ) : null}
            {messages.map((message, index) => (
              <article className={`${message.role}${message.transient ? " transient" : ""}`} key={`${message.role}:${index}`}>
                {message.role === "assistant" && assistantMessageSections(message) ? (
                  <dl className="finops-ai-answer-sections">
                    {[
                      ["conclusion", "结论"],
                      ["basis", "依据"],
                      ["impact", "影响"],
                      ["recommendation", "建议"],
                      ["caveat", "判断边界"],
                    ].map(([key, label]) => (
                      <div key={key}><dt>{label}</dt><dd>{assistantMessageSections(message)[key]}</dd></div>
                    ))}
                  </dl>
                ) : (
                  <span>
                    {message.role === "assistant"
                      ? publicAssistantContent(message.content)
                      : message.content}
                  </span>
                )}
                {message.role === "assistant" && message.evidenceLabels?.length ? (
                  <small className="finops-ai-evidence-labels">
                    <b>相关证据</b>
                    {message.evidenceLabels.slice(0, 3).map((label) => (
                      <em key={label}>{label}</em>
                    ))}
                  </small>
                ) : null}
                {message.role === "assistant" && message.knowledgeCitations?.length ? (
                  <small className="finops-ai-knowledge-citations">
                    <b>内部方法参考</b>
                    {message.knowledgeCitations.map((citation) => (
                      <em key={citation}>{citation}</em>
                    ))}
                  </small>
                ) : null}
                {message.role === "assistant" && message.evidenceRefs?.length && onEvidence ? (
                  <button type="button" onClick={() => onEvidence({ reason: `AI 回答 · ${message.evidenceRefs.length} 条证据`, evidenceRefs: message.evidenceRefs })}>
                    查看证据
                  </button>
                ) : null}
                {message.role === "assistant" && message.retryQuestion ? (
                  <button
                    type="button"
                    className="finops-ai-retry"
                    disabled={busy}
                    aria-label="重试本次提问"
                    onClick={() => ask(message.retryQuestion)}
                  >
                    重试
                  </button>
                ) : null}
              </article>
            ))}
            {busy ? <div className="finops-ai-thinking"><Loader2 className="spin" size={14} />{mode === "quick" ? "正在整理指标依据" : "正在执行深入分析"}</div> : null}
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
