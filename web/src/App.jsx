import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteWorkspace,
  loadConversation,
  loadDashboard,
  loadObservability,
  loadRun,
  produceArtifacts,
  streamChat,
  uploadWorkspace,
} from "./api.js";
import {
  extractArtifacts,
  MobileNav,
  NoticeStack,
  ShellNav,
  TopBar,
  UploadModal,
  WorkbenchMain,
  WorkspacePane,
} from "./components.jsx";
import { PLAYBOOKS, VERDICT_LABELS } from "./constants.js";

const DEFAULT_WORKSPACE = "demo-corpus";

// 预览样例（?demo=1）：仅用于在云端接口就绪前，眼看工作区 BI 看板的填充效果；真实数据由后端接口替换。
const DEMO_SEED = typeof window !== "undefined" && /[?&]demo=1/.test(window.location.search);
const DEMO_DASHBOARD = {
  workspace_id: "ws-demo-electronics",
  workspace: {
    workspace_id: "ws_01J7Z3B7V98F2KQ8X0FX90",
    name: "消费电子新品机会评估",
    created_at: "2025-06-13T10:42:00Z",
    format: "mixed",
    doc_count: 5,
    row_count: 128,
    field_count: 128,
    indexed_count: 128,
    fill_rate: 0.487,
    signal_score: 0.84,
    customer_summary: "5 份消费电子销售/调研/竞品数据已接入，整体信号可用度 84，价格敏感度与复购周期信号最强。",
    documents: [
      { name: "消费电子销售_2024Q1-Q4.xlsx", format: "xlsx", bytes: 8720000, status: "已就绪" },
      { name: "用户调研反馈_清洗版.csv", format: "csv", bytes: 2310000, status: "已就绪" },
      { name: "竞品价格表_2025-06.json", format: "json", bytes: 512000, status: "部分字段" },
      { name: "产品PRD_初稿_v1.2.md", format: "md", bytes: 48000, status: "已就绪" },
      { name: "成本结构明细_供应链.xlsx", format: "xlsx", bytes: 1240000, status: "已就绪" },
    ],
    columns: [
      { name: "price_sensitivity", friendly_label: "价格敏感度", signal: "strong", signal_score: 0.86 },
      { name: "repurchase_cycle", friendly_label: "复购周期", signal: "strong", signal_score: 0.78 },
      { name: "config_pref", friendly_label: "配置偏好", signal: "strong", signal_score: 0.72 },
      { name: "channel_conc", friendly_label: "渠道集中度", signal: "strong", signal_score: 0.65 },
      { name: "after_sales", friendly_label: "售后口碑", signal: "strong", signal_score: 0.61 },
      ...Array.from({ length: 12 }, (_, i) => ({ name: `s${i}`, friendly_label: `信号字段${i + 1}`, signal: "strong" })),
      ...Array.from({ length: 6 }, (_, i) => ({ name: `m${i}`, friendly_label: `中等字段${i + 1}`, signal: "mid" })),
      ...Array.from({ length: 2 }, (_, i) => ({ name: `n${i}`, friendly_label: `噪音字段${i + 1}`, signal: "noise" })),
    ],
    reference_images: [],
  },
  runs: [],
  conversations: [],
  health: { ok: true, dependencies: { foundry: true, search: true, blob: true, mcp: true } },
};

export function App() {
  const [workspaceId, setWorkspaceId] = useState(DEFAULT_WORKSPACE);
  const [dashboard, setDashboard] = useState(null);
  const [dashboardLoading, setDashboardLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState("");
  const [messages, setMessages] = useState([]);
  const [trace, setTrace] = useState([]);
  const [streamText, setStreamText] = useState("");
  const [demoReveal, setDemoReveal] = useState({ active: false, text: "" });
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [producing, setProducing] = useState(false);
  const [finalArtifact, setFinalArtifact] = useState(null);
  const [artifacts, setArtifacts] = useState({});
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [selectedPlaybook, setSelectedPlaybook] = useState("opportunity-tree");
  const [artifactMode, setArtifactMode] = useState("report");
  const [inspectorTab, setInspectorTab] = useState("evidence");
  const [activeView, setActiveView] = useState("workspaces");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadContext, setUploadContext] = useState({ mode: "workspace", workspaceId: "" });
  const [uploadState, setUploadState] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState(null);
  const [user, setUser] = useState({ name: "Demo User", email: "local.demo@dataforge" });
  const [authState, setAuthState] = useState("local");
  const [observability, setObservability] = useState(null);
  const [tasks, setTasks] = useState(() => { try { return JSON.parse(window.localStorage.getItem("df-tasks") || "[]"); } catch { return []; } });
  const pushTask = useCallback((task) => {
    setTasks((list) => {
      const next = [{ time: new Date().toISOString(), ...task }, ...list].slice(0, 30);
      try { window.localStorage.setItem("df-tasks", JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  }, []);
  const updateTask = useCallback((id, patch) => {
    setTasks((list) => {
      const next = list.map((t) => (t.id === id ? { ...t, ...patch } : t));
      try { window.localStorage.setItem("df-tasks", JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  }, []);
  // 恢复会话时，若消息没带 citations，从对应 run 的 artifact 证据池补上，保证 [n] 悬停索引可用
  const withRunCitations = useCallback(async (convId, msgs) => {
    const needs = msgs.some((m) => m.role === "assistant" && (!m.citations || !m.citations.length) && /\[\d+\]/.test(String(m.text || "")));
    if (!needs || !convId) return msgs;
    try {
      const run = await loadRun(convId);
      const pool = run?.final?.artifact?.citations || run?.artifact?.citations || [];
      if (!pool.length) return msgs;
      return msgs.map((m) => (m.role === "assistant" && (!m.citations || !m.citations.length) && /\[\d+\]/.test(String(m.text || "")) ? { ...m, citations: pool } : m));
    } catch { return msgs; }
  }, []);
  const streamRef = useRef("");
  const revealTimerRef = useRef(null);
  const abortRef = useRef(null);

  const currentPlaybook = useMemo(
    () => PLAYBOOKS.find((item) => item.id === selectedPlaybook) || PLAYBOOKS[0],
    [selectedPlaybook],
  );

  const refreshDashboard = useCallback(async (id = workspaceId) => {
    setDashboardLoading(true);
    setDashboardError("");
    try {
      const data = await loadDashboard(id);
      setDashboard(data);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setDashboardError(message);
      setNotice({ type: "error", message: `工作区加载失败：${message}` });
    } finally {
      setDashboardLoading(false);
    }
  }, [workspaceId]);

  const clearReveal = () => {
    if (revealTimerRef.current) window.clearInterval(revealTimerRef.current);
    revealTimerRef.current = null;
    setDemoReveal({ active: false, text: "" });
  };

  const resetRunState = () => {
    streamRef.current = "";
    clearReveal();
    setStreamText("");
    setTrace([]);
    setArtifacts({});
    // 注意：不清 finalArtifact —— 它只保存"最近一次可行性分析"，聊天/换轮不应丢掉看板上的结论与五维。
  };

  const openWorkspaceUpload = () => {
    setUploadContext({ mode: "workspace", workspaceId: "" });
    setUploadOpen(true);
  };

  const openAppendUpload = () => {
    setUploadContext({ mode: "append", workspaceId });
    setUploadOpen(true);
  };

  const openReferenceUpload = () => {
    setUploadContext({ mode: "reference", workspaceId, assetRole: "logo" });
    setUploadOpen(true);
  };

  useEffect(() => {
    if (DEMO_SEED) { setDashboard(DEMO_DASHBOARD); setDashboardLoading(false); return; }
    refreshDashboard(workspaceId);
  }, [workspaceId, refreshDashboard]);

  // 异步摄取轮询：上传后/选中仍在解析的工作区时，每 ~3.5s 刷新看板，
  // 让数据集状态「解析中→已就绪」与数据画像/TOP5 自动填充；封顶 ~2 分钟，避免个别卡住的文件无限轮询。
  const ingestPollRef = useRef(0);
  useEffect(() => {
    if (DEMO_SEED) return undefined;
    const docs = dashboard?.workspace?.documents || [];
    const processing = docs.some((d) => /解析中|处理中|processing|pending/i.test(String(d.status || "")));
    if (!processing) { ingestPollRef.current = 0; return undefined; }
    if (!ingestPollRef.current) ingestPollRef.current = Date.now();
    if (Date.now() - ingestPollRef.current > 120000) return undefined;
    const timer = window.setTimeout(() => refreshDashboard(workspaceId), 3500);
    return () => window.clearTimeout(timer);
  }, [dashboard, workspaceId, refreshDashboard]);

  // 恢复该工作区上次的可行性分析 + Agent Flow 流水线状态（刷新/换工作区后看板结论、五维、流水线都不丢）
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(`df-analysis:${workspaceId}`);
      setFinalArtifact(raw ? JSON.parse(raw) : null);
    } catch { setFinalArtifact(null); }
    setArtifacts({});
    try {
      const rawTrace = window.localStorage.getItem(`df-trace:${workspaceId}`);
      setTrace(rawTrace ? JSON.parse(rawTrace) : []);
    } catch { setTrace([]); }
  }, [workspaceId]);

  // 本浏览器没有缓存时，用后端保存的 last_analysis 兜底，让工作区在任意设备
  // 打开都能看到上次的可行性结论/五维/审计（之前只读本地缓存，换浏览器就空白）。
  useEffect(() => {
    if (finalArtifact || running) return;
    const la = dashboard?.workspace?.last_analysis || dashboard?.last_analysis;
    if (!la || !(la.verdict || (la.dimensions && la.dimensions.length))) return;
    setFinalArtifact({
      feasibility: {
        verdict: la.verdict,
        overall_confidence: la.overall_confidence,
        confidence: la.overall_confidence,
        dimensions: la.dimensions || [],
        gap_list: la.gap_list || [],
        action_plan: la.action_plan || [],
        recommendation: la.recommendation,
        opportunity_id: la.opportunity_id,
      },
      audit: la.audit || {},
      citations: la.citations || [],
      conversation_id: la.conversation_id,
      recommendation: la.recommendation,
    });
  }, [dashboard, finalArtifact, running, workspaceId]);

  // 持久化 Agent Flow 轨迹（有真实运行事件时），刷新后流水线状态保持
  useEffect(() => {
    if (!trace.length || running) return;
    if (!trace.some((e) => ["model_response", "audit", "final"].includes(e.event))) return;
    try { window.localStorage.setItem(`df-trace:${workspaceId}`, JSON.stringify(trace.slice(-44))); } catch { /* ignore */ }
  }, [trace, running, workspaceId]);

  // 恢复该工作区上次的会话内容（后端已持久化会话，刷新/换工作区后从后端拉回，不再清空）
  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    setActiveConversationId(null);
    let convId = null;
    try { convId = window.localStorage.getItem(`df-conv:${workspaceId}`); } catch { convId = null; }
    if (!convId) return undefined;
    loadConversation(convId).then(async (data) => {
      if (cancelled || !data) return;
      let msgs = (data.messages || []).map((item) => ({ role: item.role, text: item.text, time: item.time, verdict: item.verdict, citations: item.citations || [] }));
      msgs = await withRunCitations(convId, msgs);
      if (!cancelled && msgs.length) { setMessages(msgs); setActiveConversationId(convId); }
    }).catch(() => { /* 会话已删除或不可达，忽略 */ });
    return () => { cancelled = true; };
  }, [workspaceId]);

  // notice 自动淡出（非加载态 3.5s 后自动消失，不用手动点关）
  useEffect(() => {
    if (!notice || notice.type === "loading") return undefined;
    const t = window.setTimeout(() => setNotice(null), 3500);
    return () => window.clearTimeout(t);
  }, [notice]);

  // 可观测性 + 评测快照（用于 Runs 视图展示 tracing/eval）
  useEffect(() => {
    let cancelled = false;
    loadObservability().then((d) => { if (!cancelled) setObservability(d); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const configuredEndpoint = import.meta.env.VITE_AUTH_ME || "";
    const isLocal = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
    if (!configuredEndpoint && isLocal) {
      setUser({ name: "Demo User", email: "local.demo@dataforge" });
      setAuthState("local");
      return () => {
        cancelled = true;
      };
    }
    const endpoint = configuredEndpoint || "/.auth/me";
    fetch(endpoint, { headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("auth unavailable"))))
      .then((data) => {
        const principal = Array.isArray(data) ? data[0] : data?.clientPrincipal || data;
        const claims = principal?.user_claims || principal?.claims || [];
        const claim = (...keys) => {
          for (const key of keys) {
            const match = claims.find((item) => {
              const type = String(item.typ || item.type || "").toLowerCase();
              return type === key || type.endsWith(`/${key}`);
            });
            if (match?.val || match?.value) return match.val || match.value;
          }
          return "";
        };
        const next = {
          name: claim("name", "displayname", "given_name") || principal?.userDetails || principal?.user_name || "DataForge User",
          email: claim("emailaddress", "preferred_username", "upn", "email") || principal?.user_id || "",
        };
        if (!cancelled) {
          setUser(next);
          setAuthState("authenticated");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setUser({ name: "Demo User", email: "local.demo@dataforge" });
          setAuthState("local");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => () => clearReveal(), []);

  const changeWorkspace = (id) => {
    setWorkspaceId(id);
    setMessages([]);
    setActiveConversationId(null);
    resetRunState();
    setActiveView("workspaces");
  };

  const startNewConversation = () => {
    setMessages([]);
    setActiveConversationId(null);
    try { window.localStorage.removeItem(`df-conv:${workspaceId}`); } catch { /* ignore */ }
    resetRunState();
    setActiveView("conversations");
    setInspectorTab("trace");
  };

  // 后端实际只发 1 个 answer_delta + final（非逐字），所以由前端对最终答案做**逐字**打字机式流式揭示。
  const revealFinalText = (text, onComplete) => {
    const full = String(text || "");
    if (!full) { onComplete?.(); return; }
    streamRef.current = "";   // 丢弃中途的局部 delta，统一用打字机揭示全文
    setStreamText("");
    let i = 0;
    const step = Math.max(2, Math.round(full.length / 140)); // 长文本自动加速，总时长可控
    setDemoReveal({ active: true, text: "" });
    revealTimerRef.current = window.setInterval(() => {
      i += step;
      setDemoReveal({ active: true, text: full.slice(0, i) });
      if (i >= full.length) {
        window.clearInterval(revealTimerRef.current);
        revealTimerRef.current = null;
        window.setTimeout(() => {
          setDemoReveal({ active: false, text: "" });
          onComplete?.();
        }, 150);
      }
    }, 22);
  };

  const run = async (rawMessage = input, opts = {}) => {
    const message = String(rawMessage || "").trim();
    if (!message || running || demoReveal.active) return;

    if (opts.regenerate) {
      // 重新生成：保留原问题，移除上一条 AI 回答后重跑
      setMessages((items) => {
        const copy = [...items];
        while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
        return copy;
      });
    } else {
      setMessages((items) => [...items, { role: "user", text: message, time: new Date().toISOString() }]);
    }
    setInput("");
    setRunning(true);
    // 任务通知：自动分析记一条"可行性分析"任务（进行中→完成/失败）
    const taskId = opts.stayOnDashboard ? `analysis-${Date.now()}` : null;
    if (taskId) pushTask({ id: taskId, label: "可行性分析", detail: "分析进行中…", status: "running" });
    // 自动分析（stayOnDashboard）留在工作区看板里就地跑，只有手动会话/绘画才跳到会话视图
    if (!opts.stayOnDashboard) {
      setActiveView("conversations");
      setInspectorTab("trace");
    }
    resetRunState();

    // 自动分析(看板)= 完整报告 + 五维评分；会话提问 = 简洁问答(chat)，不重跑五维、答案更短
    const isAuto = !!opts.stayOnDashboard;
    const mode = isAuto ? "report" : "chat";
    const payload = {
      workspace_id: workspaceId,
      message,
      conversation_id: activeConversationId,
      artifact_mode: mode,
      ui_context: {
        workspace_name: dashboard?.workspace?.name || workspaceId,
        requested_output: mode,
        mode: isAuto ? "auto_analysis" : "conversation",
      },
    };
    if (isAuto) {
      payload.playbook = currentPlaybook.prompt;
      payload.ui_context.playbook_label = currentPlaybook.name;
    }
    if (Array.isArray(opts.iterationInputs) && opts.iterationInputs.length) {
      payload.ui_context.iteration_inputs = opts.iterationInputs;
      payload.ui_context.mode = "auto_analysis";
    }
    let deltaCount = 0;
    let terminalEvent = false;
    let streamErrorMessage = "";
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(payload, (event) => {
        if (event.event === "ready" && event.data?.conversation_id) {
          setActiveConversationId(event.data.conversation_id);
          try { window.localStorage.setItem(`df-conv:${workspaceId}`, event.data.conversation_id); } catch { /* ignore */ }
        }
        if (event.event === "answer_delta" || event.event === "delta") {
          const delta = event.data?.delta || event.data?.text || "";
          if (delta) {
            streamRef.current += delta;
            deltaCount += 1;
            // 后端现在是真 token 流式：逐块实时显示（首块即显，营造真实"边想边写"）。
            setStreamText(streamRef.current);
          }
          return;
        }
        if (event.event !== "progress") {
          setTrace((items) => [...items, event]);
        }
        // 不在分析期把内联产物自动填进来——产物生成器停在待命，由客户点「生成产物」(produce) 才出产物。
        if (event.event === "clarify") {
          terminalEvent = true;
          const cd = event.data || {};
          const c = cd.clarify || cd; // 兼容 {clarify:{...}} 与扁平结构
          const question = c.question || cd.question || cd.text || "我需要多了解一点你的目标，才能给出更有据的分析。";
          const options = Array.isArray(c.options) ? c.options.filter((o) => o && (o.label || o.id || typeof o === "string")) : [];
          setMessages((items) => [...items, { role: "assistant", text: "", time: new Date().toISOString(), clarify: { question, options } }]);
          setStreamText("");
        }
        if (event.event === "final") {
          terminalEvent = true;
          const artifact = event.data?.artifact || {};
          const text = event.data?.text || artifact.answer?.text || streamRef.current || "已完成分析。";
          // finalArtifact 只在"真正的可行性分析"时更新（含 verdict/五维），聊天/问答不覆盖它——
          // 这样换工作区/聊天后，看板结论与「生成产物」仍基于上次分析，不会拿"你好"这种回复去生成。
          const fe = artifact.feasibility || {};
          const isAnalysis = Boolean(fe.verdict || (fe.dimensions && fe.dimensions.length) || fe.scores);
          if (isAnalysis) {
            setFinalArtifact(artifact);
            try { window.localStorage.setItem(`df-analysis:${workspaceId}`, JSON.stringify(artifact)); } catch { /* ignore */ }
          }
          if (taskId) updateTask(taskId, { status: "done", detail: isAnalysis ? `结论：${VERDICT_LABELS[fe.verdict] || "已完成"}` : "已完成" });
          // 不在分析阶段自动填充产物：产物生成器停在待命，由客户拍板后点「生成产物」才生成（见 produce()）。
          // 对话里 agent 识别到「想要产物」时，后端给 produce_offer → 在消息下挂一个确认生成按钮
          const produceOffer = artifact.produce_offer || event.data?.produce_offer || null;
          const commitFinal = () => {
            setMessages((items) => [
              ...items,
              {
                role: "assistant",
                text,
                time: new Date().toISOString(),
                citations: artifact.citations || artifact.answer?.citations || [],
                produceOffer,
              },
            ]);
            setStreamText("");
            streamRef.current = "";
          };
          // 已经真流式逐块显示过 → 直接用清洗后的 final 文本落定，不再重复打字机动画；
          // 没有流式（少见的兜底）才用客户端打字机揭示。
          if (deltaCount >= 2) commitFinal();
          else revealFinalText(text, commitFinal);
          setInspectorTab("evidence");
        }
        if (event.event === "error") {
          const messageText = event.data?.message || "运行失败。";
          terminalEvent = true;
          streamErrorMessage = messageText;
          setNotice({ type: "error", message: messageText });
        }
      }, controller.signal);
      if (streamErrorMessage) {
        throw new Error(streamErrorMessage);
      }
      if (!terminalEvent) {
        throw new Error("连接已断开，未收到最终结果。请重试。");
      }
      refreshDashboard(workspaceId);
    } catch (error) {
      if (error?.name === "AbortError" || controller.signal.aborted) {
        // 用户主动点了「停止生成」——不当作错误
        setMessages((items) => [...items, { role: "assistant", text: "已停止本次生成。", time: new Date().toISOString() }]);
        if (taskId) updateTask(taskId, { status: "done", detail: "已停止" });
      } else {
        const messageText = error instanceof Error ? error.message : String(error);
        setTrace((items) => [...items, { event: "error", data: { message: messageText } }]);
        setMessages((items) => [...items, { role: "assistant", text: `运行失败：${messageText}`, time: new Date().toISOString() }]);
        setNotice({ type: "error", message: `运行失败：${messageText}` });
        if (taskId) updateTask(taskId, { status: "error", detail: "分析失败" });
      }
    } finally {
      abortRef.current = null;
      setRunning(false);
      if (!revealTimerRef.current) {
        setStreamText("");
        streamRef.current = "";
      }
    }
  };

  // 停止生成：中止当前 SSE 请求(run 的 catch 会识别为主动停止)
  const stop = () => {
    if (abortRef.current) abortRef.current.abort();
  };

  const mergeToolArtifact = (data) => {
    const name = String(data.name || "");
    const entry = { artifact_url: data.artifact_url, bytes: data.bytes, mode: data.mode };
    setArtifacts((items) => {
      if (name.includes("pdf")) return { ...items, pdf: entry };
      if (name.includes("image")) return { ...items, concept_image: entry };
      if (name.includes("audio") || name.includes("narrate")) return { ...items, audio_summary: entry };
      return items;
    });
  };

  const upload = async ({ name, description, files, workspaceId: targetWorkspaceId, assetRole }) => {
    const isReference = uploadContext.mode === "reference";
    const isAppend = Boolean(targetWorkspaceId);
    const list = Array.from(files || []);
    if (!list.length) return;
    try {
      // 逐个上传：首个建工作区、其余 append（每请求一个文件、秒回），上传后由轮询自动把
      // 「解析中→已就绪」和画像填充刷出来；后端已修复并发摄取，无需再等待串行化。
      let wid = targetWorkspaceId || null;
      let result = null;
      for (let i = 0; i < list.length; i++) {
        const stepMsg = isReference
          ? "正在上传参考图并绑定到当前工作区..."
          : list.length > 1
            ? `正在接入第 ${i + 1}/${list.length} 个文件：${list[i].name}…`
            : "正在上传并生成数据画像...";
        setUploadState({ type: "loading", message: stepMsg });
        result = await uploadWorkspace({ name, description, files: [list[i]], workspaceId: wid, assetRole });
        wid = result.workspace_id || wid;
      }
      setUploadState({
        type: "done",
        message: isReference
          ? "参考图已绑定到当前工作区。"
          : isAppend
            ? `已更新工作区：${result.name}`
            : `已接入 ${list.length} 个文件：${result.name}`,
      });
      setUploadOpen(false);
      setWorkspaceId(result.workspace_id);
      if (!isAppend) {
        setMessages([]);
        resetRunState();
      }
      await refreshDashboard(result.workspace_id);
      window.setTimeout(() => setUploadState(null), 2600);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setUploadState({ type: "error", message: `上传失败：${message}` });
    }
  };

  const removeWorkspace = async () => {
    if (!workspaceId?.startsWith("upload-")) {
      setNotice({ type: "error", message: "内置工作区不能删除。" });
      return;
    }
    setDeleting(true);
    try {
      await deleteWorkspace(workspaceId);
      setNotice({ type: "done", message: "工作区已删除。" });
      changeWorkspace(DEFAULT_WORKSPACE);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ type: "error", message: `删除失败：${message}` });
    } finally {
      setDeleting(false);
    }
  };

  const openConversation = async (conversationId) => {
    try {
      const data = await loadConversation(conversationId);
      setActiveConversationId(conversationId);
      try { window.localStorage.setItem(`df-conv:${workspaceId}`, conversationId); } catch { /* ignore */ }
      let msgs = (data.messages || []).map((item) => ({ role: item.role, text: item.text, time: item.time, verdict: item.verdict, citations: item.citations || [] }));
      msgs = await withRunCitations(conversationId, msgs);
      setMessages(msgs);
      resetRunState();
      setActiveView("conversations");
      setNotice({ type: "done", message: "会话已恢复。" });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ type: "error", message: `会话读取失败：${message}` });
    }
  };

  const hasAnalysisArtifact = (a) => {
    const fe = a?.feasibility || {};
    return Boolean(fe.verdict || (fe.dimensions && fe.dimensions.length) || fe.scores);
  };

  // 没有现成分析时，先静默跑一次可行性分析，拿到 artifact 再用于生成产物（让会话里“直接产出”可用）
  const ensureAnalysisArtifact = () =>
    new Promise((resolve, reject) => {
      let captured = null;
      let streamErrorMessage = "";
      streamChat(
        {
          workspace_id: workspaceId,
          message: "基于当前工作区资料做一次可行性分析，用于生成产物。",
          conversation_id: activeConversationId,
          artifact_mode: "report",
          ui_context: { workspace_name: dashboard?.workspace?.name || workspaceId, requested_output: "report", mode: "auto_analysis" },
        },
        (event) => {
          if (event.event === "ready" && event.data?.conversation_id) setActiveConversationId(event.data.conversation_id);
          if (event.event === "final") captured = event.data?.artifact || null;
          if (event.event === "error") streamErrorMessage = event.data?.message || "分析失败。";
        },
      )
        .then(() => {
          if (streamErrorMessage) reject(new Error(streamErrorMessage));
          else if (!captured) reject(new Error("分析流已断开，未收到可生成产物的结果。"));
          else resolve(captured);
        })
        .catch(reject);
    });

  const produce = async (kindsArg) => {
    if (producing) return;
    const KIND_LABEL = { pdf: "项目文档 PDF", concept_image: "概念图", audio: "语音摘要" };
    // kindsArg：产物类型数组（产物页按钮），或会话 chip 的 offer 对象，或缺省→文档+概念图
    let kinds;
    if (Array.isArray(kindsArg)) kinds = kindsArg.filter((k) => KIND_LABEL[k]);
    else if (kindsArg && kindsArg.kind === "poster") kinds = ["concept_image"];
    else {
      kinds = ["pdf", "concept_image"];
      try { if (window.localStorage.getItem("df-pref-audio") === "1") kinds.push("audio"); } catch { /* ignore */ }
    }
    if (!kinds.length) kinds = ["pdf", "concept_image"];
    const prodTaskId = `produce-${Date.now()}`;
    pushTask({ id: prodTaskId, label: "生成产物", detail: `${kinds.map((k) => KIND_LABEL[k]).join(" / ")} · 进行中…`, status: "running" });
    setProducing(true);
    setInspectorTab("output");
    try {
      let base = finalArtifact;
      if (!hasAnalysisArtifact(base)) {
        setNotice({ type: "loading", message: "先快速分析当前工作区，再生成产物…" });
        base = await ensureAnalysisArtifact();
        if (hasAnalysisArtifact(base)) {
          setFinalArtifact(base);
          try { window.localStorage.setItem(`df-analysis:${workspaceId}`, JSON.stringify(base)); } catch { /* ignore */ }
        }
      }
      if (!hasAnalysisArtifact(base)) {
        setNotice({ type: "error", message: "这个工作区暂时无法生成产物：没有可分析的有效数据。" });
        return;
      }
      setNotice({ type: "loading", message: `正在生成${kinds.map((k) => KIND_LABEL[k]).join(" / ")}…` });
      const result = await produceArtifacts({
        workspace_id: workspaceId,
        conversation_id: activeConversationId || base.conversation_id,
        feasibility: base.feasibility || {},
        corpus: base.corpus || {},
        market: base.market || {},
        audit: base.audit || {},
        answer: base.answer || {},
        proposal: base.proposal || {},
        reference_images: base.reference_images || [],
        narrative: base.narrative,
        text: base.answer?.text || base.answer?.markdown,
        kinds,
      });
      // 合并：produce 只返回本次生成的产物，保留之前已生成的，别覆盖丢失
      const prevProposal = base.proposal || {};
      const mergedProposal = {
        ...prevProposal,
        ...result,
        artifact_urls: { ...(prevProposal.artifact_urls || {}), ...(result.artifact_urls || {}) },
      };
      const nextArtifact = { ...base, proposal: mergedProposal };
      setFinalArtifact(nextArtifact);
      const arts = extractArtifacts(nextArtifact);
      setArtifacts(arts);
      // 在会话里就地展示产物，点了能立刻看到 PDF / 概念图 / 语音，而不是“好像没反应”
      if (arts && (arts.pdf || arts.concept_image || arts.audio_summary)) {
        setMessages((items) => [
          ...items,
          { role: "assistant", time: new Date().toISOString(), text: "产物已生成，可直接查看 / 下载：", producedArtifacts: arts },
        ]);
      }
      setNotice({ type: "done", message: "产物已生成。" });
      updateTask(prodTaskId, { status: "done", detail: `${kinds.map((k) => KIND_LABEL[k]).join(" / ")} · 已生成` });
      refreshDashboard(workspaceId);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ type: "error", message: `生成产物失败：${message}` });
      updateTask(prodTaskId, { status: "error", detail: "生成失败" });
    } finally {
      setProducing(false);
    }
  };

  const logout = () => {
    if (authState !== "authenticated") {
      setNotice({ type: "done", message: "当前是本地演示态，云端登录后这里会退出 Azure 会话。" });
      return;
    }
    const url = import.meta.env.VITE_AUTH_LOGOUT || "/.auth/logout?post_logout_redirect_uri=/";
    window.location.assign(url);
  };

  const displayRunning = running || demoReveal.active;

  return (
    <div className="app-shell">
      <ShellNav active={activeView} onChange={setActiveView} health={dashboard?.health} />
      <div className="workbench">
        <TopBar
          dashboard={dashboard}
          workspaceId={workspaceId}
          onWorkspaceChange={changeWorkspace}
          onUpload={openWorkspaceUpload}
          onNewConversation={startNewConversation}
          loading={dashboardLoading || displayRunning || producing}
          user={user}
          authState={authState}
          onLogout={logout}
          tasks={tasks}
        />
        <MobileNav active={activeView} onChange={setActiveView} />
        <div className="workbench-grid">
          <WorkspacePane
            dashboard={dashboard}
            workspaceId={workspaceId}
            onUpload={openAppendUpload}
            onDeleteWorkspace={removeWorkspace}
            onOpenConversation={openConversation}
            onRefresh={() => refreshDashboard(workspaceId)}
            deleting={deleting}
          />
          <WorkbenchMain
            view={activeView}
            setView={setActiveView}
            dashboard={dashboard}
            messages={messages}
            trace={trace}
            streamText={streamText || demoReveal.text}
            running={displayRunning}
            input={input}
            setInput={setInput}
            onRun={run}
            onStop={stop}
            selectedPlaybook={selectedPlaybook}
            setSelectedPlaybook={setSelectedPlaybook}
            artifactMode={artifactMode}
            setArtifactMode={setArtifactMode}
            finalArtifact={finalArtifact}
            artifacts={artifacts}
            onProduce={produce}
            onUploadReference={openReferenceUpload}
            onNewConversation={startNewConversation}
            producing={producing}
            observability={observability}
            onOpenConversation={openConversation}
          />
        </div>
      </div>
      <UploadModal
        open={uploadOpen}
        busy={uploadState?.type === "loading"}
        mode={uploadContext.mode}
        workspace={dashboard?.workspace}
        workspaceId={uploadContext.workspaceId}
        assetRole={uploadContext.assetRole}
        onClose={() => setUploadOpen(false)}
        onSubmit={upload}
      />
      <NoticeStack
        notice={dashboardError ? { type: "error", message: dashboardError } : notice}
        uploadState={uploadState}
        onDismiss={() => {
          setNotice(null);
          setDashboardError("");
        }}
      />
    </div>
  );
}

export default App;
