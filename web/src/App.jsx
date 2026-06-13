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
  AgentStudio,
  extractArtifacts,
  Inspector,
  NoticeStack,
  ShellNav,
  TopBar,
  UploadModal,
  WorkspacePane,
} from "./components.jsx";
import { PLAYBOOKS } from "./constants.js";

const DEFAULT_WORKSPACE = "demo-corpus";

export function App() {
  const [workspaceId, setWorkspaceId] = useState(DEFAULT_WORKSPACE);
  const [dashboard, setDashboard] = useState(null);
  const [dashboardLoading, setDashboardLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState("");
  const [messages, setMessages] = useState([]);
  const [trace, setTrace] = useState([]);
  const [streamText, setStreamText] = useState("");
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [producing, setProducing] = useState(false);
  const [finalArtifact, setFinalArtifact] = useState(null);
  const [artifacts, setArtifacts] = useState({});
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [selectedPlaybook, setSelectedPlaybook] = useState("opportunity-tree");
  const [artifactMode, setArtifactMode] = useState("report");
  const [inspectorTab, setInspectorTab] = useState("evidence");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadState, setUploadState] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState(null);
  const [user, setUser] = useState(null);
  const streamRef = useRef("");

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

  useEffect(() => {
    refreshDashboard(workspaceId);
  }, [workspaceId, refreshDashboard]);

  useEffect(() => {
    let cancelled = false;
    const authEndpoint = import.meta.env.VITE_AUTH_ME || "";
    if (!authEndpoint) {
      setUser(null);
      return () => {
        cancelled = true;
      };
    }
    fetch(authEndpoint, { headers: { Accept: "application/json" } })
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((data) => {
        const principal = Array.isArray(data) ? data[0] : data?.clientPrincipal || data;
        const claims = principal?.user_claims || [];
        const claim = (...keys) => {
          for (const key of keys) {
            const match = claims.find((item) => {
              const type = String(item.typ || "").toLowerCase();
              return type === key || type.endsWith(`/${key}`);
            });
            if (match?.val) return match.val;
          }
          return "";
        };
        const next = {
          name: claim("name", "displayname", "given_name"),
          email: claim("emailaddress", "preferred_username", "upn", "email") || principal?.user_id || "",
        };
        if (!cancelled && (next.name || next.email)) setUser(next);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const resetRunState = () => {
    streamRef.current = "";
    setStreamText("");
    setTrace([]);
    setFinalArtifact(null);
    setArtifacts({});
  };

  const changeWorkspace = (id) => {
    setWorkspaceId(id);
    setMessages([]);
    setActiveConversationId(null);
    resetRunState();
  };

  const run = async (rawMessage = input) => {
    const message = String(rawMessage || "").trim();
    if (!message || running) return;
    const userMessage = { role: "user", text: message, time: new Date().toISOString() };
    setMessages((items) => [...items, userMessage]);
    setInput("");
    setRunning(true);
    setInspectorTab("trace");
    resetRunState();

    const payload = {
      workspace_id: workspaceId,
      message,
      conversation_id: activeConversationId,
      playbook: currentPlaybook.prompt,
      artifact_mode: artifactMode,
      ui_context: {
        playbook_label: currentPlaybook.name,
        workspace_name: dashboard?.workspace?.name || workspaceId,
        requested_output: artifactMode,
      },
    };

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
          const text = event.data?.question || event.data?.text || "我需要更多目标信息。";
          setMessages((items) => [...items, { role: "assistant", text, time: new Date().toISOString() }]);
          setStreamText("");
        }
        if (event.event === "final") {
          const artifact = event.data?.artifact || {};
          const text = event.data?.text || artifact.answer?.text || streamRef.current || "已完成分析。";
          setFinalArtifact(artifact);
          setArtifacts(extractArtifacts(artifact));
          setMessages((items) => [...items, { role: "assistant", text, time: new Date().toISOString(), citations: artifact.citations || artifact.answer?.citations || [] }]);
          setStreamText("");
          streamRef.current = "";
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
      setStreamText("");
      streamRef.current = "";
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

  const upload = async ({ name, description, files }) => {
    setUploadState({ type: "loading", message: "正在上传并生成数据画像..." });
    try {
      const result = await uploadWorkspace({ name, description, files });
      setUploadState({ type: "done", message: `已创建工作区：${result.name}` });
      setUploadOpen(false);
      setWorkspaceId(result.workspace_id);
      setMessages([]);
      resetRunState();
      setTimeout(() => setUploadState(null), 2600);
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

  return (
    <div className="app-shell">
      <ShellNav />
      <div className="workbench">
        <TopBar
          dashboard={dashboard}
          workspaceId={workspaceId}
          onWorkspaceChange={changeWorkspace}
          onUpload={() => setUploadOpen(true)}
          loading={dashboardLoading}
          user={user}
        />
        <div className="workbench-grid">
          <WorkspacePane
            dashboard={dashboard}
            workspaceId={workspaceId}
            onUpload={() => setUploadOpen(true)}
            onDeleteWorkspace={removeWorkspace}
            onOpenConversation={openConversation}
            onRefresh={() => refreshDashboard(workspaceId)}
            deleting={deleting}
          />
          <AgentStudio
            dashboard={dashboard}
            messages={messages}
            trace={trace}
            streamText={streamText}
            running={running}
            input={input}
            setInput={setInput}
            onRun={run}
            selectedPlaybook={selectedPlaybook}
            setSelectedPlaybook={setSelectedPlaybook}
            artifactMode={artifactMode}
            setArtifactMode={setArtifactMode}
            finalArtifact={finalArtifact}
            onProduce={produce}
            producing={producing}
          />
          <Inspector
            tab={inspectorTab}
            setTab={setInspectorTab}
            trace={trace}
            finalArtifact={finalArtifact}
            artifacts={artifacts}
            running={running}
            producing={producing}
          />
        </div>
      </div>
      <UploadModal open={uploadOpen} busy={uploadState?.type === "loading"} onClose={() => setUploadOpen(false)} onSubmit={upload} />
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
