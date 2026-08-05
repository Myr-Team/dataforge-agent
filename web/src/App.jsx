import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createArtifactJob,
  deleteWorkspace,
  listWorkspaces,
  loadConversation,
  loadDashboard,
  loadFinOpsBootstrap,
  loadWorkspaceAccess,
  loadLatestAnalysis,
  loadObservability,
  loadArtifactJob,
  loadArtifactJobs,
  loadGovernanceCapabilities,
  loadWorkspaceTasks,
  cancelTask,
  retryTask,
  loadRun,
  isTransientFetchError,
  streamChat,
  uploadWorkspace,
} from "./api.js";
import {
  prefetchFinOpsBootstrap,
} from "./finopsPreload.js";
import { clearFinOpsData } from "./finopsDataStore.js";
import {
  finopsAuthorizationBoundary,
  finopsPreloadScope,
  reconcileFinOpsAuthorizationScope,
  scheduleFinOpsPreload,
} from "./finopsNavigation.js";
import { TaskCenter, expireTaskNotifications, isCurrentWorkspaceTaskResponse, stampTaskNotifications, taskViewModel, terminalTaskNotifications } from "./TaskCenter.jsx";
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
import { normalizePrimaryView, PLAYBOOKS, resolvePrimaryView, VERDICT_LABELS } from "./constants.js";
import {
  executionMessageVisibility,
  executionRequestFields,
  filterCustomerConversationMessages,
  readyExecutionState,
} from "./executionIdentity.js";
import {
  matchingWorkspaceValue,
  workspaceBootstrapFailure,
} from "./workspaceBootstrap.js";

const DEFAULT_WORKSPACE = "demo-corpus";
const ARTIFACT_JOB_TERMINAL = new Set(["partial", "completed", "failed", "cancelled"]);
const DISMISSED_TASK_NOTIFICATIONS_KEY = "df-dismissed-task-notification-ids";

export async function performServerTaskAction({ task, workspaceId, currentWorkspaceId, postAction, refreshTasks, setActionState }) {
  const taskId = task?.task_id;
  const actionWorkspaceId = String(task?.workspace_id || workspaceId);
  if (!taskId || actionWorkspaceId !== currentWorkspaceId()) return;
  setActionState({ pending: true, error: "" });
  try {
    await postAction(taskId);
  } catch (error) {
    if (actionWorkspaceId === currentWorkspaceId()) {
      setActionState({ pending: false, error: error instanceof Error ? error.message : String(error) });
    }
    return;
  }
  if (actionWorkspaceId !== currentWorkspaceId()) return;
  setActionState({ pending: false, error: "" });
  try {
    await refreshTasks(workspaceId);
  } catch (error) {
    if (error?.name !== "AbortError" && actionWorkspaceId === currentWorkspaceId()) {
      setActionState({ pending: false, error: `任务列表刷新失败：${error instanceof Error ? error.message : String(error)}` });
    }
  }
}

const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function waitForArtifactJob(
  jobId,
  onUpdate,
  { timeoutMs = 20 * 60 * 1000, shouldCancel = () => false } = {},
) {
  const started = Date.now();
  let transientFailures = 0;
  while (Date.now() - started < timeoutMs) {
    if (shouldCancel()) return null;
    if (document.hidden) {
      await wait(500);
      continue;
    }
    let job;
    try {
      job = await loadArtifactJob(jobId);
      transientFailures = 0;
    } catch (error) {
      if (isTransientFetchError(error)) {
        transientFailures += 1;
        await wait(Math.min(5000, 800 * (2 ** Math.min(transientFailures, 3))));
        continue;
      }
      throw error;
    }
    if (shouldCancel()) return null;
    onUpdate?.(job);
    if (ARTIFACT_JOB_TERMINAL.has(job.status)) return job;
    await wait(1400);
  }
  throw new Error("产物任务仍在后台运行，可稍后回到产物中心查看。");
}

function artifactJobResult(job) {
  const artifacts = job?.artifacts || {};
  const artifactUrls = Object.fromEntries(
    Object.entries(artifacts)
      .map(([kind, value]) => [kind, value?.artifact_url])
      .filter(([, url]) => Boolean(url)),
  );
  return {
    ...artifacts,
    ...(job?.result_meta || {}),
    artifact_urls: artifactUrls,
    warnings: job?.warnings || [],
    job_id: job?.job_id,
    job_status: job?.status,
  };
}

const hasAnalysisDimensions = (artifact) => {
  const dims = artifact?.feasibility?.dimensions;
  return Array.isArray(dims) && dims.length > 0;
};

export function App() {
  const [workspaceId, setWorkspaceId] = useState(DEFAULT_WORKSPACE);
  const [dashboard, setDashboard] = useState(null);
  const [workspaceAccess, setWorkspaceAccess] = useState(null);
  const [governanceCapabilities, setGovernanceCapabilities] = useState(null);
  const [governanceCapabilitiesError, setGovernanceCapabilitiesError] = useState("");
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
  const [artifactRefreshKey, setArtifactRefreshKey] = useState(0);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [selectedPlaybook, setSelectedPlaybook] = useState("opportunity-tree");
  const [artifactMode, setArtifactMode] = useState("report");
  const [inspectorTab, setInspectorTab] = useState("evidence");
  const [activeView, setActiveView] = useState("workspaces");
  const [settingsInitialTab, setSettingsInitialTab] = useState("about");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadContext, setUploadContext] = useState({ mode: "workspace", workspaceId: "" });
  const [uploadState, setUploadState] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [notice, setNotice] = useState(null);
  const [user, setUser] = useState({
    name: "Demo User",
    email: "local.demo@dataforge",
    tenantScope: "local-demo",
  });
  const [authState, setAuthState] = useState("local");
  const [observability, setObservability] = useState(null);
  const activeViewRef = useRef(activeView);
  const [tasks, setTasks] = useState([]);
  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false);
  const [taskNotifications, setTaskNotifications] = useState([]);
  const [taskActions, setTaskActions] = useState({});
  // 恢复会话时，若消息没带 citations，从对应 run 的 artifact 证据池补上，保证 [n] 悬停索引可用
  const withRunContext = useCallback(async (convId, msgs) => {
    const needs = msgs.some((m) => m.role === "assistant" && (!m.citations || !m.citations.length) && /\[\d+\]/.test(String(m.text || "")));
    if (!convId) return msgs;
    try {
      const run = await loadRun(convId);
      const pool = run?.final?.artifact?.citations || run?.artifact?.citations || [];
      const latestAssistantIndex = msgs.map((m) => m.role).lastIndexOf("assistant");
      return msgs.map((m, index) => {
        const next = needs && pool.length && m.role === "assistant" && (!m.citations || !m.citations.length) && /\[\d+\]/.test(String(m.text || ""))
          ? { ...m, citations: pool }
          : m;
        return index === latestAssistantIndex && run?.trace
          ? { ...next, trace: { ...run.trace, run_id: run.run_id || convId } }
          : next;
      });
    } catch { return msgs; }
  }, []);
  const streamRef = useRef("");
  const traceReferenceRef = useRef(null);
  const revealTimerRef = useRef(null);
  const abortRef = useRef(null);
  const taskSnapshotRef = useRef(new Map());
  const taskHydratedRef = useRef(false);
  const dismissedTaskNotificationsRef = useRef(new Set());
  const workspaceIdRef = useRef(workspaceId);
  const taskRequestRef = useRef(0);
  const dashboardRequestRef = useRef(0);
  const taskAbortRef = useRef(null);
  const finopsAuthorizationRef = useRef("");
  const finopsScope = useMemo(
    () => finopsPreloadScope({
      authState,
      workspaceId,
      user,
      capabilities: governanceCapabilities,
      workspaceAccess,
    }),
    [authState, governanceCapabilities, user, workspaceAccess, workspaceId],
  );
  const finopsAuthorizationKey = useMemo(() => finopsAuthorizationBoundary({
    authState,
    workspaceId,
    user,
    capabilities: governanceCapabilities,
    workspaceAccess,
  }), [authState, governanceCapabilities, user, workspaceAccess, workspaceId]);
  const finopsPortalScope = useMemo(() => (
    finopsScope && finopsAuthorizationKey
      ? { ...finopsScope, authorizationFingerprint: finopsAuthorizationKey }
      : null
  ), [finopsAuthorizationKey, finopsScope]);
  const preloadFinOps = useCallback(() => {
    if (!finopsScope) return Promise.resolve(null);
    // Hover/focus/touch intent handlers must not leave an unhandled rejection.
    return prefetchFinOpsBootstrap(
      finopsScope.key,
      ({ signal }) => loadFinOpsBootstrap(
        { workspaceId: finopsScope.workspaceId },
        { signal },
      ),
    ).catch((error) => {
      if (error?.name !== "AbortError") {
        console.warn("FinOps bootstrap preload failed", error);
      }
      return null;
    });
  }, [finopsScope]);

  useEffect(() => {
    activeViewRef.current = activeView;
  }, [activeView]);

  useEffect(() => {
    workspaceIdRef.current = workspaceId;
  }, [workspaceId]);

  useEffect(() => {
    finopsAuthorizationRef.current = reconcileFinOpsAuthorizationScope(
      finopsAuthorizationRef.current,
      finopsAuthorizationKey,
      clearFinOpsData,
    );
  }, [finopsAuthorizationKey]);

  useEffect(() => {
    if (!finopsScope) return undefined;
    const cancelScheduled = scheduleFinOpsPreload(
      () => preloadFinOps().catch((error) => {
        if (error?.name !== "AbortError") {
          console.warn("FinOps bootstrap preload failed", error);
        }
      }),
      window,
    );
    return cancelScheduled;
  }, [finopsScope, preloadFinOps]);

  const currentPlaybook = useMemo(
    () => PLAYBOOKS.find((item) => item.id === selectedPlaybook) || PLAYBOOKS[0],
    [selectedPlaybook],
  );

  const refreshDashboard = useCallback(async (id = workspaceId) => {
    const sequence = ++dashboardRequestRef.current;
    const isCurrentRequest = () => (
      sequence === dashboardRequestRef.current
      && id === workspaceIdRef.current
    );
    setDashboardLoading(true);
    setDashboardError("");
    setGovernanceCapabilitiesError("");
    setWorkspaceAccess((current) => matchingWorkspaceValue(current, id));
    setGovernanceCapabilities((current) => matchingWorkspaceValue(current, id));

    const accessPromise = loadWorkspaceAccess(id)
      .then((access) => {
        if (isCurrentRequest()) setWorkspaceAccess(access);
        return access;
      })
      .catch(() => null);
    const capabilitiesPromise = loadGovernanceCapabilities(id)
      .then((capabilities) => {
        if (isCurrentRequest()) {
          setGovernanceCapabilities(capabilities);
          setGovernanceCapabilitiesError("");
        }
        return capabilities;
      })
      .catch((error) => {
        if (isCurrentRequest()) {
          setGovernanceCapabilitiesError((current) => current || workspaceBootstrapFailure(null, id, error));
        }
        return null;
      });
    try {
      const [data] = await Promise.all([
        loadDashboard(id),
        accessPromise,
        capabilitiesPromise,
      ]);
      if (isCurrentRequest()) {
        setDashboard(data);
        setDashboardError(data?.fallback_error ? String(data.fallback_error) : "");
      }
    } catch (error) {
      if (!isCurrentRequest()) return;
      const message = error instanceof Error ? error.message : String(error);
      if (/Workspace not found/i.test(message)) {
        try {
          const workspaces = await listWorkspaces();
          const fallback = workspaces.find((item) => item?.workspace_id)?.workspace_id;
          if (fallback && fallback !== id) {
            setWorkspaceId(fallback);
            setNotice({ type: "done", message: "当前工作区不可用，已切换到最近的可用工作区。" });
            return;
          }
        } catch {
          // Fall through to the original load error.
        }
      }
      setDashboardError(message);
      setNotice({ type: "error", message: `工作区加载失败：${message}` });
    } finally {
      if (isCurrentRequest()) setDashboardLoading(false);
    }
  }, [workspaceId]);

  const retryGovernanceCapabilities = useCallback(async () => {
    const id = workspaceIdRef.current;
    setGovernanceCapabilitiesError("");
    try {
      const capabilities = await loadGovernanceCapabilities(id);
      if (id !== workspaceIdRef.current) return;
      setGovernanceCapabilities(capabilities);
    } catch (error) {
      if (id !== workspaceIdRef.current) return;
      setGovernanceCapabilitiesError(
        workspaceBootstrapFailure(null, id, error),
      );
    }
  }, []);

  const refreshTasks = useCallback(async (id = workspaceId) => {
    const sequence = ++taskRequestRef.current;
    taskAbortRef.current?.abort();
    const controller = new AbortController();
    taskAbortRef.current = controller;
    const fetched = await loadWorkspaceTasks(id, { signal: controller.signal });
    if (!isCurrentWorkspaceTaskResponse({
      requestSequence: sequence,
      currentSequence: taskRequestRef.current,
      requestWorkspaceId: id,
      currentWorkspaceId: workspaceIdRef.current,
      aborted: controller.signal.aborted,
    })) return;
    const next = Array.isArray(fetched) ? fetched : [];
    const previous = taskSnapshotRef.current;
    const dismissed = dismissedTaskNotificationsRef.current;
    const terminalUpdates = terminalTaskNotifications(next, previous, taskHydratedRef.current, dismissed);
    taskSnapshotRef.current = new Map(next.map((task) => {
      const model = taskViewModel(task);
      return [model.taskId, model.notificationId];
    }));
    taskHydratedRef.current = true;
    setTasks(next);
    if (terminalUpdates.length) {
      setTaskNotifications((current) => [
        ...stampTaskNotifications(terminalUpdates),
        ...expireTaskNotifications(current),
      ].slice(0, 3));
    }
  }, [workspaceId]);

  const dismissTaskNotification = useCallback((notificationId) => {
    if (!notificationId) return;
    dismissedTaskNotificationsRef.current.add(notificationId);
    setTaskNotifications((current) => current.filter((task) => taskViewModel(task).notificationId !== notificationId));
    try {
      window.localStorage.setItem(DISMISSED_TASK_NOTIFICATIONS_KEY, JSON.stringify([...dismissedTaskNotificationsRef.current]));
    } catch {
      // Dismissal is convenience state only; task truth remains on the server.
    }
  }, []);

  useEffect(() => {
    if (!taskNotifications.length) return undefined;
    const now = Date.now();
    const expiresAt = Math.min(...taskNotifications.map((task) => Number(task?.notification_expires_at)).filter(Number.isFinite));
    if (!Number.isFinite(expiresAt)) return undefined;
    const timer = window.setTimeout(() => {
      setTaskNotifications((current) => expireTaskNotifications(current));
    }, Math.max(0, expiresAt - now) + 20);
    return () => window.clearTimeout(timer);
  }, [taskNotifications]);

  const performTaskAction = useCallback((task, action) => {
    const taskId = task?.task_id;
    return performServerTaskAction({
      task,
      workspaceId,
      currentWorkspaceId: () => workspaceIdRef.current,
      postAction: action,
      refreshTasks,
      setActionState: (value) => {
        if (!taskId) return;
        setTaskActions((current) => ({ ...current, [taskId]: value }));
      },
    });
  }, [refreshTasks, workspaceId]);

  const openTaskResult = useCallback((task) => {
    if (task?.workspace_id !== workspaceIdRef.current) return;
    const destination = taskViewModel(task).destination;
    if (destination) setActiveView(destination);
    if (destination) setTaskDrawerOpen(false);
  }, []);

  const closeTaskCenter = useCallback(() => setTaskDrawerOpen(false), []);

  const clearReveal = () => {
    if (revealTimerRef.current) window.clearInterval(revealTimerRef.current);
    revealTimerRef.current = null;
    setDemoReveal({ active: false, text: "" });
  };

  const resetRunState = () => {
    streamRef.current = "";
    traceReferenceRef.current = null;
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
    refreshDashboard(workspaceId);
  }, [workspaceId, refreshDashboard]);

  useEffect(() => {
    taskAbortRef.current?.abort();
    taskHydratedRef.current = false;
    taskSnapshotRef.current = new Map();
    setTasks([]);
    setTaskNotifications([]);
    setTaskActions({});
    try {
      const stored = JSON.parse(window.localStorage.getItem(DISMISSED_TASK_NOTIFICATIONS_KEY) || "[]");
      dismissedTaskNotificationsRef.current = new Set(Array.isArray(stored) ? stored.filter((value) => typeof value === "string") : []);
    } catch {
      dismissedTaskNotificationsRef.current = new Set();
    }
    refreshTasks(workspaceId).catch(() => {});
  }, [workspaceId, refreshTasks]);

  useEffect(() => {
    const onVisibilityChange = () => {
      if (!document.hidden) refreshTasks(workspaceId).catch(() => {});
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [workspaceId, refreshTasks]);

  useEffect(() => {
    if (document.hidden || !tasks.some((task) => ["queued", "running"].includes(task.status))) return undefined;
    const timer = window.setInterval(() => refreshTasks(workspaceId).catch(() => {}), 2500);
    return () => window.clearInterval(timer);
  }, [tasks, workspaceId, refreshTasks]);

  useEffect(() => {
    let cancelled = false;
    const recoverArtifactJobs = async () => {
      try {
        const data = await loadArtifactJobs(workspaceId);
        const active = (data?.jobs || []).filter((job) => ["queued", "running"].includes(job.status));
        if (!active.length || cancelled || workspaceId !== workspaceIdRef.current) return;
        setProducing(true);
        await Promise.all(active.map((job) => waitForArtifactJob(
          job.job_id,
          () => refreshTasks(workspaceId).catch(() => {}),
          { shouldCancel: () => cancelled || workspaceId !== workspaceIdRef.current },
        )));
        if (cancelled || workspaceId !== workspaceIdRef.current) return;
        setArtifactRefreshKey((value) => value + 1);
        refreshDashboard(workspaceId);
        refreshTasks(workspaceId).catch(() => {});
      } catch (error) {
        if (isTransientFetchError(error)) return;
      } finally {
        if (!cancelled && workspaceId === workspaceIdRef.current) setProducing(false);
      }
    };
    recoverArtifactJobs();
    return () => { cancelled = true; };
  }, [workspaceId, refreshDashboard, refreshTasks]);

  // 异步摄取轮询：上传后/选中仍在解析的工作区时，每 ~3.5s 刷新看板，
  // 让数据集状态「解析中→已就绪」与数据画像/TOP5 自动填充；封顶 ~2 分钟，避免个别卡住的文件无限轮询。
  const ingestPollRef = useRef(0);
  useEffect(() => {
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
    let cancelled = false;
    let restoredArtifact = null;
    let restoredTrace = [];
    try {
      const raw = window.localStorage.getItem(`df-analysis:${workspaceId}`);
      const parsed = raw ? JSON.parse(raw) : null;
      restoredArtifact = hasAnalysisDimensions(parsed) ? parsed : null;
    } catch { restoredArtifact = null; }
    setFinalArtifact(restoredArtifact);
    setArtifacts({});
    try {
      const rawTrace = window.localStorage.getItem(`df-trace:${workspaceId}`);
      restoredTrace = rawTrace ? JSON.parse(rawTrace) : [];
    } catch { restoredTrace = []; }
    setTrace(Array.isArray(restoredTrace) ? restoredTrace : []);
    loadLatestAnalysis(workspaceId)
      .then((data) => {
        if (cancelled || !data?.found || !hasAnalysisDimensions(data.artifact)) return;
        setFinalArtifact({
          ...data.artifact,
          run_id: data.run_id || data.artifact?.run_id || null,
          origin: data.origin || data.artifact?.origin || null,
          conversation_id: data.conversation_id ?? data.artifact?.conversation_id ?? null,
        });
        const nextTrace = Array.isArray(data.trace) && data.trace.length ? data.trace : (Array.isArray(restoredTrace) ? restoredTrace : []);
        setTrace(nextTrace);
        try { window.localStorage.setItem(`df-analysis:${workspaceId}`, JSON.stringify(data.artifact)); } catch { /* ignore */ }
        if (nextTrace.length) {
          try { window.localStorage.setItem(`df-trace:${workspaceId}`, JSON.stringify(nextTrace.slice(-44))); } catch { /* ignore */ }
        }
      })
      .catch(() => { /* overview fallback below still handles older dashboard snapshots */ });
    return () => { cancelled = true; };
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
      run_id: la.run_id || null,
      origin: la.origin || null,
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
      let msgs = filterCustomerConversationMessages(
        (data.messages || []).map((item) => ({ role: item.role, text: item.text, time: item.time, verdict: item.verdict, citations: item.citations || [] })),
      );
      msgs = await withRunContext(convId, msgs);
      if (!cancelled && msgs.length) { setMessages(msgs); setActiveConversationId(convId); }
    }).catch(() => { /* 会话已删除或不可达，忽略 */ });
    return () => { cancelled = true; };
  }, [workspaceId]);

  // notice 自动淡出（非加载态 3.5s 后自动消失，不用手动点关）
  useEffect(() => {
    if (!notice || notice.type === "loading") return undefined;
    const t = window.setTimeout(() => setNotice(null), notice.action ? 9000 : 3500);
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
      setUser({ name: "Demo User", email: "local.demo@dataforge", tenantScope: "local-demo" });
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
          tenantScope: claim("tenantid", "tid"),
        };
        if (!cancelled) {
          setUser(next);
          setAuthState("authenticated");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setUser({ name: "Demo User", email: "local.demo@dataforge", tenantScope: "local-demo" });
          setAuthState("local");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    try {
      const email = String(user?.email || "").trim();
      if (authState === "authenticated" && email && email.includes("@")) {
        window.localStorage.setItem(
          "df-current-user",
          JSON.stringify({
            name: String(user?.name || "").trim(),
            email,
          }),
        );
      } else {
        window.localStorage.removeItem("df-current-user");
      }
    } catch {
      // Local storage can be unavailable in restricted browser modes.
    }
  }, [authState, user]);

  useEffect(() => () => clearReveal(), []);

  const changeWorkspace = (id) => {
    setWorkspaceAccess((current) => matchingWorkspaceValue(current, id));
    setGovernanceCapabilities((current) => matchingWorkspaceValue(current, id));
    setGovernanceCapabilitiesError("");
    setWorkspaceId(id);
    setMessages([]);
    setActiveConversationId(null);
    resetRunState();
    setActiveView("workspaces");
  };

  const changePrimaryView = (view) => {
    const nextView = resolvePrimaryView(view, governanceCapabilities);
    setActiveView(nextView);
  };

  useEffect(() => {
    const nextView = normalizePrimaryView(activeView);
    if (nextView !== activeView) {
      setActiveView(nextView);
      return;
    }
    const safeView = resolvePrimaryView(nextView, governanceCapabilities);
    if (governanceCapabilities && safeView !== nextView) {
      setActiveView(safeView);
    }
  }, [activeView, governanceCapabilities]);

  const renderView = resolvePrimaryView(activeView, governanceCapabilities);

  const openMembersSettings = () => {
    setActiveView("members");
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
    const isAuto = opts.stayOnDashboard === true;
    const messageVisibility = executionMessageVisibility(opts);

    if (opts.regenerate) {
      // 重新生成：保留原问题，移除上一条 AI 回答后重跑
      setMessages((items) => {
        const copy = [...items];
        while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
        return copy;
      });
    } else if (messageVisibility.appendUser) {
      setMessages((items) => [...items, { role: "user", text: message, time: new Date().toISOString() }]);
    }
    setInput("");
    setRunning(true);
    // 任务通知：自动分析记一条"可行性分析"任务（进行中→完成/失败）
    // 自动分析（stayOnDashboard）留在工作区看板里就地跑，只有手动会话/绘画才跳到会话视图
    if (!opts.stayOnDashboard) {
      setActiveView("conversations");
      setInspectorTab("trace");
    }
    resetRunState();

    // 自动分析(看板)= 完整报告 + 五维评分；会话提问 = 简洁问答(chat)，不重跑五维、答案更短
    const mode = opts.artifactMode || (isAuto ? "report" : "chat");
    const executionFields = executionRequestFields({
      stayOnDashboard: isAuto,
      executionOrigin: opts.executionOrigin,
      activeConversationId,
      newConversation: opts.newConversation,
    });
    const payload = {
      workspace_id: workspaceId,
      message,
      ...executionFields,
      artifact_mode: mode,
      ui_context: {
        workspace_name: dashboard?.workspace?.name || workspaceId,
        requested_output: mode,
        mode: executionFields.origin === "conversation" ? "conversation" : "auto_analysis",
        ...(opts.uiContext || {}),
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
        if (event.event === "task_meta" && event.data?.task_id) {
          refreshTasks(workspaceId).catch(() => {});
          return;
        }
        if (event.event === "ready") {
          const execution = readyExecutionState(event.data, activeConversationId);
          traceReferenceRef.current = event.data?.trace
            ? { ...event.data.trace, run_id: execution.runId }
            : null;
          if (execution.persistConversation) {
            setActiveConversationId(execution.activeConversationId);
            try { window.localStorage.setItem(`df-conv:${workspaceId}`, execution.activeConversationId); } catch { /* ignore */ }
          }
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
          if (!isAuto) {
            setMessages((items) => [...items, { role: "assistant", text: "", time: new Date().toISOString(), clarify: { question, options }, trace: traceReferenceRef.current }]);
          }
          setStreamText("");
        }
        if (event.event === "final") {
          terminalEvent = true;
          const artifact = {
            ...(event.data?.artifact || {}),
            run_id: event.data?.run_id || event.data?.artifact?.run_id || null,
            origin: event.data?.origin || event.data?.artifact?.origin || null,
            conversation_id: event.data?.conversation_id ?? event.data?.artifact?.conversation_id ?? null,
          };
          const text = event.data?.text || artifact.answer?.text || streamRef.current || "已完成分析。";
          // finalArtifact 只在"真正的可行性分析"时更新（含 verdict/五维），聊天/问答不覆盖它——
          // 这样换工作区/聊天后，看板结论与「生成产物」仍基于上次分析，不会拿"你好"这种回复去生成。
          const fe = artifact.feasibility || {};
          const isAnalysis = Boolean(fe.verdict || (fe.dimensions && fe.dimensions.length) || fe.scores);
          if (isAnalysis) {
            setFinalArtifact(artifact);
            try { window.localStorage.setItem(`df-analysis:${workspaceId}`, JSON.stringify(artifact)); } catch { /* ignore */ }
          }
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
                trace: traceReferenceRef.current || artifact.trace || null,
              },
            ]);
            setStreamText("");
            streamRef.current = "";
          };
          // 已经真流式逐块显示过 → 直接用清洗后的 final 文本落定，不再重复打字机动画；
          // 没有流式（少见的兜底）才用客户端打字机揭示。
          if (!isAuto) {
            if (deltaCount >= 2) commitFinal();
            else revealFinalText(text, commitFinal);
          } else {
            setStreamText("");
            streamRef.current = "";
          }
          if (!isAuto && activeViewRef.current !== "conversations") {
            setNotice({
              type: "done",
              message: "AI 回复已生成完成。",
              actionLabel: "查看会话",
              action: () => {
                setActiveView("conversations");
                setNotice(null);
              },
            });
          }
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
      refreshTasks(workspaceId).catch(() => {});
    } catch (error) {
      if (terminalEvent) {
        refreshDashboard(workspaceId);
        return;
      }
      if (error?.name === "AbortError" || controller.signal.aborted) {
        // 用户主动点了「停止生成」——不当作错误
        if (messageVisibility.appendAssistant) {
          setMessages((items) => [...items, { role: "assistant", text: "已停止本次生成。", time: new Date().toISOString() }]);
        }
      } else {
        const messageText = error instanceof Error ? error.message : String(error);
        setTrace((items) => [...items, { event: "error", data: { message: messageText } }]);
        const partialText = String(streamRef.current || streamText || "").trim();
        if (partialText && isTransientFetchError(error)) {
          if (messageVisibility.appendAssistant) {
            setMessages((items) => [
              ...items,
              {
                role: "assistant",
                text: partialText,
                time: new Date().toISOString(),
                recoverable: { prompt: message, message: messageText },
              },
            ]);
          }
          setNotice({ type: "done", message: "连接中断，已保留已生成内容，可重试/继续。" });
        } else {
          if (messageVisibility.appendAssistant) {
            setMessages((items) => [...items, { role: "assistant", text: `运行失败：${messageText}`, time: new Date().toISOString() }]);
          }
          setNotice({ type: "error", message: `运行失败：${messageText}` });
        }
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
      await refreshTasks(result.workspace_id);
      window.setTimeout(() => setUploadState(null), 2600);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setUploadState({ type: "error", message: `上传失败：${message}` });
    }
  };

  const removeWorkspace = async (targetWorkspaceId = workspaceId) => {
    if (!targetWorkspaceId?.startsWith("upload-")) {
      setNotice({ type: "error", message: "内置工作区不能删除。" });
      return;
    }
    setDeleting(true);
    try {
      await deleteWorkspace(targetWorkspaceId);
      setNotice({ type: "done", message: "工作区已删除。" });
      if (targetWorkspaceId === workspaceId) {
        changeWorkspace(DEFAULT_WORKSPACE);
      } else {
        await refreshDashboard(workspaceId);
      }
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
      let msgs = filterCustomerConversationMessages(
        (data.messages || []).map((item) => ({ role: item.role, text: item.text, time: item.time, verdict: item.verdict, citations: item.citations || [] })),
      );
      msgs = await withRunContext(conversationId, msgs);
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
          conversation_id: null,
          origin: "workspace_auto_analysis",
          persist_messages: false,
          artifact_mode: "report",
          ui_context: { workspace_name: dashboard?.workspace?.name || workspaceId, requested_output: "report", mode: "auto_analysis" },
        },
        (event) => {
          if (event.event === "task_meta" && event.data?.task_id) {
            refreshTasks(workspaceId).catch(() => {});
            return;
          }
          if (event.event === "final") {
            captured = {
              ...(event.data?.artifact || {}),
              run_id: event.data?.run_id || event.data?.artifact?.run_id || null,
              origin: event.data?.origin || event.data?.artifact?.origin || null,
              conversation_id: event.data?.conversation_id ?? event.data?.artifact?.conversation_id ?? null,
            };
          }
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
    const KIND_LABEL = { pdf: "项目文档 PDF", concept_image: "概念图", audio: "语音摘要", roadmap: "路线图", validation_plan: "验证计划" };
    const KIND_ALIAS = { audio_summary: "audio" };
    // kindsArg：产物类型数组（产物页按钮），或会话 chip 的 offer 对象，或缺省→文档+概念图
    let kinds;
    if (Array.isArray(kindsArg)) kinds = kindsArg.map((k) => KIND_ALIAS[k] || k).filter((k) => KIND_LABEL[k]);
    else if (kindsArg && kindsArg.kind === "poster") kinds = ["concept_image"];
    else if (kindsArg && kindsArg.kind === "proposal") kinds = ["pdf", "concept_image", "audio"];
    else {
      kinds = ["pdf", "concept_image"];
      try { if (window.localStorage.getItem("df-pref-audio") === "1") kinds.push("audio"); } catch { /* ignore */ }
    }
    if (!kinds.length) kinds = ["pdf", "concept_image"];
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
      const job = await createArtifactJob({
        workspace_id: workspaceId,
        source_run_id: base.run_id || base.source_run_id || activeConversationId || base.conversation_id,
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
      await refreshTasks(workspaceId);
      const completedJob = await waitForArtifactJob(job.job_id, () => refreshTasks(workspaceId).catch(() => {}));
      const result = artifactJobResult(completedJob);
      if (completedJob.status === "failed" && !Object.keys(result.artifact_urls || {}).length) {
        const firstError = Object.values(completedJob.errors || {})[0];
        throw new Error(firstError?.message || "产物后台任务失败，可重新生成。");
      }
      // 合并：produce 只返回本次生成的产物，保留之前已生成的，别覆盖丢失
      const prevProposal = base.proposal || {};
      const warnings = [
        ...((prevProposal.warnings || []).filter(Boolean)),
        ...((result.warnings || []).filter(Boolean)),
      ];
      const mergedProposal = {
        ...prevProposal,
        ...result,
        artifact_urls: { ...(prevProposal.artifact_urls || {}), ...(result.artifact_urls || {}) },
        warnings,
      };
      const nextArtifact = { ...base, proposal: mergedProposal };
      setFinalArtifact(nextArtifact);
      const arts = extractArtifacts(nextArtifact);
      setArtifacts(arts);
      setArtifactRefreshKey((value) => value + 1);
      const warningText = warnings.map((w) => w?.message).filter(Boolean).join("；");
      // 在会话里就地展示产物，点了能立刻看到 PDF / 概念图 / 语音，而不是“好像没反应”
      if (arts && (arts.pdf || arts.concept_image || arts.audio_summary)) {
        setMessages((items) => [
          ...items,
          {
            role: "assistant",
            time: new Date().toISOString(),
            text: warningText ? `产物已部分生成：${warningText}` : "产物已生成，可直接查看 / 下载：",
            producedArtifacts: arts,
          },
        ]);
      }
      setNotice({ type: "done", message: warningText || "产物已生成。" });
      if (activeViewRef.current !== "artifacts") {
        setNotice({
          type: warningText ? "error" : "done",
          message: warningText ? `产物已部分生成：${warningText}` : "产物已生成，已同步到产物中心。",
          actionLabel: "查看产物",
          action: () => {
            setActiveView("artifacts");
            setNotice(null);
          },
        });
      }
      refreshDashboard(workspaceId);
      refreshTasks(workspaceId).catch(() => {});
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setNotice({ type: "error", message: `生成产物失败：${message}` });
      refreshTasks(workspaceId).catch(() => {});
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
      <TopBar
        dashboard={dashboard}
        workspaceId={workspaceId}
        onWorkspaceChange={changeWorkspace}
        onUpload={openWorkspaceUpload}
        onNewConversation={startNewConversation}
        onDeleteWorkspace={removeWorkspace}
        loading={dashboardLoading || displayRunning || producing}
        deleting={deleting}
        user={user}
        authState={authState}
        onLogout={logout}
        tasks={tasks}
        onOpenTaskCenter={() => setTaskDrawerOpen(true)}
      />
      <ShellNav active={renderView} onChange={changePrimaryView} workspace={dashboard?.workspace} access={workspaceAccess} capabilities={governanceCapabilities} onInviteMembers={openMembersSettings} onFinOpsIntent={preloadFinOps} />
      <div className="workbench">
        <MobileNav active={renderView} onChange={changePrimaryView} capabilities={governanceCapabilities} onFinOpsIntent={preloadFinOps} />
        <div className="workbench-grid">
          <WorkbenchMain
            view={renderView}
            setView={setActiveView}
            dashboard={dashboard}
            dashboardLoading={dashboardLoading}
            dashboardError={dashboardError}
            onRetryDashboard={() => refreshDashboard(workspaceId)}
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
            artifactRefreshKey={artifactRefreshKey}
            onProduce={produce}
            onUploadReference={openReferenceUpload}
            onAppendUpload={openAppendUpload}
            onNewConversation={startNewConversation}
            producing={producing}
            observability={observability}
            onOpenConversation={openConversation}
            tasks={tasks}
            user={user}
            settingsInitialTab={settingsInitialTab}
            onWorkspaceDataChanged={() => refreshDashboard(workspaceId)}
            onOpenTaskCenter={() => setTaskDrawerOpen(true)}
            workspaceAccess={workspaceAccess}
            governanceCapabilities={governanceCapabilities}
            governanceCapabilitiesError={governanceCapabilitiesError}
            onRetryGovernanceCapabilities={retryGovernanceCapabilities}
            finopsPreloadScope={finopsPortalScope}
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
      <TaskCenter
        open={taskDrawerOpen}
        tasks={tasks}
        notifications={taskNotifications}
        actions={taskActions}
        onClose={closeTaskCenter}
        onCancel={(task) => performTaskAction(task, cancelTask)}
        onRetry={(task) => performTaskAction(task, retryTask)}
        onOpenResult={openTaskResult}
        onDismissNotification={dismissTaskNotification}
      />
    </div>
  );
}

export default App;
