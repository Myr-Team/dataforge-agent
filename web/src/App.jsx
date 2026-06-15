import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  deleteWorkspace,
  loadConversation,
  loadDashboard,
  produceArtifacts,
  streamChat,
  uploadWorkspace,
} from "./api.js";
import {
  extractArtifacts,
  Inspector,
  MobileNav,
  NoticeStack,
  ShellNav,
  TopBar,
  UploadModal,
  WorkbenchMain,
  WorkspacePane,
} from "./components.jsx";
import { PLAYBOOKS } from "./constants.js";

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
  const streamRef = useRef("");
  const revealTimerRef = useRef(null);

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
    setFinalArtifact(null);
    setArtifacts({});
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
    resetRunState();
    setActiveView("conversations");
    setInspectorTab("trace");
  };

  const revealFinalText = (text, onComplete) => {
    const full = String(text || "");
    if (!full || streamRef.current.trim().length > 0) {
      onComplete?.();
      return;
    }
    const pieces = full.match(/[^。！？!?；;，,\n]+[。！？!?；;，,\n]?\s*/g) || [full];
    let index = 0;
    let current = "";
    setDemoReveal({ active: true, text: "" });
    revealTimerRef.current = window.setInterval(() => {
      current += pieces[index] || "";
      setDemoReveal({ active: true, text: current });
      index += 1;
      if (index >= pieces.length) {
        window.clearInterval(revealTimerRef.current);
        revealTimerRef.current = null;
        window.setTimeout(() => {
          setDemoReveal({ active: false, text: "" });
          onComplete?.();
        }, 320);
      }
    }, 170);
  };

  const run = async (rawMessage = input, opts = {}) => {
    const message = String(rawMessage || "").trim();
    if (!message || running || demoReveal.active) return;

    setMessages((items) => [...items, { role: "user", text: message, time: new Date().toISOString() }]);
    setInput("");
    setRunning(true);
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

    try {
      await streamChat(payload, (event) => {
        if (event.event === "ready" && event.data?.conversation_id) {
          setActiveConversationId(event.data.conversation_id);
        }
        if (event.event === "answer_delta" || event.event === "delta") {
          const delta = event.data?.delta || event.data?.text || "";
          if (delta) {
            streamRef.current += delta;
            setStreamText(streamRef.current);
          }
          return;
        }
        if (event.event !== "progress") {
          setTrace((items) => [...items, event]);
        }
        if (event.event === "tool_result" && event.data?.artifact_url) {
          mergeToolArtifact(event.data);
        }
        if (event.event === "clarify") {
          const text = event.data?.question || event.data?.text || "我需要更多目标信息，才能给出可靠建议。";
          setMessages((items) => [...items, { role: "assistant", text, time: new Date().toISOString() }]);
          setStreamText("");
        }
        if (event.event === "final") {
          const artifact = event.data?.artifact || {};
          const text = event.data?.text || artifact.answer?.text || streamRef.current || "已完成分析。";
          setFinalArtifact(artifact);
          setArtifacts(extractArtifacts(artifact));
          const commitFinal = () => {
            setMessages((items) => [
              ...items,
              {
                role: "assistant",
                text,
                time: new Date().toISOString(),
                citations: artifact.citations || artifact.answer?.citations || [],
              },
            ]);
            setStreamText("");
            streamRef.current = "";
          };
          revealFinalText(text, commitFinal);
          setInspectorTab("evidence");
        }
        if (event.event === "error") {
          const messageText = event.data?.message || "运行失败。";
          setNotice({ type: "error", message: messageText });
        }
      });
      refreshDashboard(workspaceId);
    } catch (error) {
      const messageText = error instanceof Error ? error.message : String(error);
      setTrace((items) => [...items, { event: "error", data: { message: messageText } }]);
      setMessages((items) => [...items, { role: "assistant", text: `运行失败：${messageText}`, time: new Date().toISOString() }]);
      setNotice({ type: "error", message: `运行失败：${messageText}` });
    } finally {
      setRunning(false);
      if (!revealTimerRef.current) {
        setStreamText("");
        streamRef.current = "";
      }
    }
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
      setMessages((data.messages || []).map((item) => ({ role: item.role, text: item.text, time: item.time, verdict: item.verdict })));
      resetRunState();
      setActiveView("conversations");
      setNotice({ type: "done", message: "会话已恢复。" });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ type: "error", message: `会话读取失败：${message}` });
    }
  };

  const produce = async () => {
    if (!finalArtifact) {
      setNotice({ type: "error", message: "没有可复用的分析报告。" });
      return;
    }
    setProducing(true);
    setInspectorTab("output");
    try {
      const result = await produceArtifacts({
        workspace_id: workspaceId,
        conversation_id: activeConversationId || finalArtifact.conversation_id,
        feasibility: finalArtifact.feasibility || {},
        corpus: finalArtifact.corpus || {},
        market: finalArtifact.market || {},
        audit: finalArtifact.audit || {},
        answer: finalArtifact.answer || {},
        proposal: finalArtifact.proposal || {},
        reference_images: finalArtifact.reference_images || [],
        narrative: finalArtifact.narrative,
        text: finalArtifact.answer?.text || finalArtifact.answer?.markdown,
      });
      const nextArtifact = { ...finalArtifact, proposal: result };
      setFinalArtifact(nextArtifact);
      setArtifacts(extractArtifacts(nextArtifact));
      setNotice({ type: "done", message: "产物已生成。" });
      refreshDashboard(workspaceId);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ type: "error", message: `生成产物失败：${message}` });
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
      <ShellNav active={activeView} onChange={setActiveView} />
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
