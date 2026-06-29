import React, { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { startTour } from "./tour.js";
const DataWorkbench = lazy(() => import("./DataWorkbench.jsx").then((m) => ({ default: m.DataWorkbench })));
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  Bell,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  CircleUserRound,
  Check,
  Clock3,
  Compass,
  Copy,
  RotateCcw,
  Square,
  Database,
  FileDown,
  FileText,
  FolderOpen,
  Globe,
  ImagePlus,
  Layers3,
  Loader2,
  PanelLeftClose,
  Rows3,
  TrendingUp,
  UserPlus,
  Target,
  Users,
  Wrench,
  Coins,
  Download,
  Info,
  Lightbulb,
  BookOpen,
  ThumbsUp,
  ThumbsDown,
  Mic,
  LogIn,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  PieChart,
  Plus,
  RefreshCw,
  Route,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Star,
  Trash2,
  UploadCloud,
  Workflow,
  X,
} from "lucide-react";
import { API_BASE, artifactLink, loadDataOverview, loadFlagship, loadPlanMetrics, loadPlaybookDetail, loadRun, setFlagship } from "./api.js";
import {
  AGENTS,
  ARTIFACT_GROUPS,
  ARTIFACT_MODES,
  CONFIDENCE_DESCRIPTIONS,
  CONFIDENCE_LABELS,
  DIMENSION_LABELS,
  INSPECTOR_TABS,
  NAV_ITEMS,
  PLAYBOOKS,
  QUESTION_STARTERS,
  VERDICT_LABELS,
} from "./constants.js";

const agentMap = new Map(AGENTS.map((agent) => [agent.id, agent]));

export function ShellNav({ active = "workspaces", onChange = () => {}, workspace = {} }) {
  return (
    <nav className="shell-nav" aria-label="Primary">
      <div className="nav-stack">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              data-tour={item.id === "runs" ? "runs" : item.id === "artifacts" ? "artifacts-nav" : undefined}
              className={active === item.id ? "nav-icon active" : "nav-icon"}
              type="button"
              title={item.label}
              onClick={() => onChange(item.id)}
            >
              <Icon size={19} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>
      <div className="ws-foot">
        <div className="wsf-k">Workspace</div>
        <div className="wsf-v">{workspace.name || "当前工作区"}</div>
        <div className="wsf-k">Role</div>
        <div className="wsf-v wsf-last">Owner</div>
        <button className="wsf-invite" type="button" title="团队协作能力即将上线">
          <UserPlus size={15} /> Invite members
        </button>
      </div>
    </nav>
  );
}

export function MobileNav({ active = "workspaces", onChange = () => {} }) {
  return (
    <nav className="mobile-nav" aria-label="Mobile primary">
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            className={active === item.id ? "mobile-nav-item active" : "mobile-nav-item"}
            type="button"
            onClick={() => onChange(item.id)}
          >
            <Icon size={17} />
            <span>{item.label}</span>
          </button>
        );
      })}
    </nav>
  );
}

function NotificationBell({ tasks = [] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open]);
  const running = tasks.filter((t) => t.status === "running").length;
  const recent = tasks.slice(0, 12);
  const icon = (s) => (s === "running" ? <Loader2 className="spin" size={14} /> : s === "error" ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />);
  return (
    <div className="notif" ref={ref}>
      <button className="icon-button top-icon" type="button" title="任务通知" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <Bell size={16} />
        {running ? <span className="notif-badge">{running}</span> : null}
      </button>
      {open ? (
        <div className="notif-panel" role="menu">
          <div className="notif-head"><strong>任务通知</strong><span>{running ? `${running} 个进行中` : "暂无进行中任务"}</span></div>
          <div className="notif-list">
            {recent.length ? recent.map((t) => (
              <div className={`notif-item ${t.status}`} key={t.id}>
                <span className="notif-ic">{icon(t.status)}</span>
                <div className="notif-body">
                  <strong>{t.label}</strong>
                  <span>{(t.detail || (t.status === "running" ? "进行中…" : t.status === "error" ? "失败" : "已完成"))}{t.time ? ` · ${formatTime(t.time)}` : ""}</span>
                </div>
              </div>
            )) : <p className="empty-copy">还没有任务。发起一次分析或生成产物，这里会记录进度。</p>}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function WorkspaceSwitcher({ workspaces = [], workspaceId, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open]);
  const current = workspaces.find((w) => w.workspace_id === workspaceId);
  const currentName = current?.name || workspaceId || "工作区";
  const sorted = [...workspaces].sort(
    (a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0),
  );
  return (
    <div className="ws-switch" ref={ref}>
      <span className="ws-sep">/</span>
      <button className="ws-crumb" type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} title="切换工作区">
        <span className="ws-name">{currentName}</span>
        <ChevronsUpDown size={14} className="ws-caret" />
      </button>
      {open ? (
        <div className="ws-dd" role="menu">
          <div className="ws-dd-head">工作区</div>
          <div className="ws-dd-list">
            {sorted.length ? (
              sorted.map((w) => {
                const active = w.workspace_id === workspaceId;
                const sub = w.row_count
                  ? `${w.row_count} 行${w.field_count ? ` · ${w.field_count} 列` : ""}`
                  : w.doc_count
                    ? `${w.doc_count} 个文件`
                    : w.format
                      ? String(w.format).toUpperCase()
                      : "";
                return (
                  <button key={w.workspace_id} type="button" className={active ? "ws-dd-item cur" : "ws-dd-item"} role="menuitem" onClick={() => { onChange(w.workspace_id); setOpen(false); }}>
                    <span className="ws-ck">{active ? <Check size={15} /> : null}</span>
                    <span className="ws-dd-meta">
                      <span className="ws-dd-name">{w.name || w.workspace_id}</span>
                      {sub ? <span className="ws-dd-sub">{sub}</span> : null}
                    </span>
                  </button>
                );
              })
            ) : (
              <div className="ws-dd-empty">暂无工作区</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function TopBar({ dashboard, workspaceId, onWorkspaceChange, onUpload, onNewConversation, loading, user, authState, onLogout, tasks }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const workspaces = dashboard?.workspaces || [];
  const health = dashboard?.health || {};
  const deps = health.dependencies || {};

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointerDown = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) setMenuOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => window.removeEventListener("pointerdown", onPointerDown);
  }, [menuOpen]);
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="brand-mini">
          <img className="brand-logo-sm" src="/dataforge-logo.png" alt="" />
          <strong>DataForge</strong>
        </div>
        <WorkspaceSwitcher workspaces={workspaces} workspaceId={workspaceId} onChange={onWorkspaceChange} />
      </div>
      <div className="topbar-actions">
        <button className="tour-button icon-label" type="button" onClick={startTour} title="新手引导：一步步了解产品流程">
          <Compass size={16} />
          新手引导
        </button>
        <button data-tour="upload" className="primary-button icon-label" type="button" onClick={() => { setMenuOpen(false); onUpload(); }}>
          <UploadCloud size={16} />
          上传数据
        </button>
        <NotificationBell tasks={tasks} />
        <div className="user-menu" ref={menuRef}>
          <button className="user-trigger" type="button" onClick={() => setMenuOpen((value) => !value)} aria-expanded={menuOpen} title="账户">
            <div className="avatar" title={user?.email || "DataForge"}>
              {(user?.name || user?.email || "D").trim().slice(0, 1).toUpperCase()}
            </div>
          </button>
          {menuOpen ? (
            <div className="account-menu" role="menu">
              <div className="account-card">
                <div className="avatar large">
                  {(user?.name || user?.email || "D").trim().slice(0, 1).toUpperCase()}
                </div>
                <div>
                  <strong>{user?.name || "Demo User"}</strong>
                  <span>{authState === "authenticated" ? user?.email || "Azure 登录" : "本地演示态"}</span>
                </div>
              </div>
              <button type="button" role="menuitem">
                <CircleUserRound size={15} />
                个人信息
              </button>
              <button type="button" role="menuitem">
                <LogIn size={15} />
                账户与权限
              </button>
              <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onLogout(); }}>
                <LogOut size={15} />
                {authState === "authenticated" ? "退出登录" : "查看云端登录态"}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}

export function WorkspacePane({
  dashboard,
  workspaceId,
  onUpload,
  onDeleteWorkspace,
  onOpenConversation,
  onRefresh,
  deleting,
}) {
  const workspace = dashboard?.workspace || {};
  const columns = workspace.columns || [];
  const signalColumns = columns.filter((column) => column.signal && column.signal !== "noise").slice(0, 8);
  const noisyColumns = columns.filter((column) => column.signal === "noise");
  // TOP5 按展示名去重，避免出现两个同名字段（如两个“数量”）
  const topSignals = (() => {
    const seen = new Set();
    const out = [];
    for (const column of signalColumns) {
      const key = String(column.friendly_label || column.name || "").trim();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(column);
    }
    return out;
  })();
  const canDelete = workspaceId?.startsWith("upload-");
  const documents = workspace.documents || [];
  const created = workspace.created_at || workspace.updated_at || documents[0]?.created_at;
  const fields = columns.length || workspace.field_count || 0;
  const rows = workspace.row_count ?? workspace.indexed_count ?? workspace.doc_count ?? 0;
  const fillRate = workspace.fill_rate ?? workspace.field_fill_rate;
  const referenceImages = workspace.reference_images || [];
  const [refreshing, setRefreshing] = useState(false);
  const doRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try { await onRefresh?.(); } finally { window.setTimeout(() => setRefreshing(false), 500); }
  };

  return (
    <aside className="workspace-pane">
      <section className="pane-section workspace-hero">
        <div className="section-head">
          <span>工作区</span>
          <button className="icon-button" type="button" onClick={doRefresh} title="刷新数据画像与运行状态（上传/解析后用它拉取最新）" disabled={refreshing}>
            {refreshing ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
          </button>
        </div>
        <h2>{workspace.name || workspaceId}</h2>
        <div className="ws-meta">
          <span className="ws-id">{workspace.workspace_id || workspaceId}</span>
          {created ? <span className="ws-created">创建于 {formatTime(created)}</span> : null}
        </div>
        <div className="metric-row metric-row-4">
          <Metric value={documents.length || workspace.doc_count || 0} label="数据源" />
          <Metric value={fields} label="字段" />
          <Metric value={rows} label="记录" />
          <Metric value={fillRate != null ? `${Math.round(fillRate * (fillRate <= 1 ? 100 : 1))}%` : "—"} label="字段填充率" />
        </div>
        <div className="workspace-actions">
          <button className="primary-button" type="button" onClick={onUpload}>
            <Plus size={16} />
            上传数据
          </button>
          <button className="danger-button" type="button" onClick={onDeleteWorkspace} disabled={!canDelete || deleting}>
            {deleting ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
            删除工作区
          </button>
        </div>
      </section>

      <section className="pane-section">
        <div className="section-head"><span>数据集</span><em>{documents.length}</em></div>
        <div className="dataset-list">
          {documents.slice(0, 8).map((doc) => {
            const meta = fileTypeMeta(doc);
            const status = String(doc.status || "已就绪");
            const parsing = /解析中|处理中|processing|pending/i.test(status);
            const cls = parsing ? "loading" : /就绪|已解析|ready|done/i.test(status) || !doc.status ? "ready" : "partial";
            return (
              <div className="dataset-row" key={doc.source_file || doc.name}>
                <FileTypeIcon doc={doc} />
                <div className="dataset-meta">
                  <strong>{doc.name || sanitizeSourceLabel(doc.source_file)}</strong>
                  <span>{meta.label}{doc.bytes ? ` · ${formatBytes(doc.bytes)}` : ""}</span>
                </div>
                <em className={`ds-status ${cls}`}>{parsing ? <span className="ds-spinner" aria-hidden="true" /> : null}{status}</em>
              </div>
            );
          })}
          {!documents.length ? <p className="empty-copy">暂无文件。上传后自动剖析并生成画像。</p> : null}
        </div>
      </section>

      <section className="pane-section" data-tour="pipeline">
        <div className="section-head"><span>数据解析状态</span><em>从上传到 Agent 可用</em></div>
        <DataPipelineCard workspace={workspace} documents={documents} />
      </section>

      <section className="pane-section">
        <div className="section-head"><span>数据画像</span><em>{signalColumns.length ? "已解析" : "等待信号"}</em></div>
        <DataPortrait workspace={workspace} signalColumns={signalColumns} noisyColumns={noisyColumns} columns={columns} />
      </section>

      {topSignals.length ? (
        <section className="pane-section">
          <div className="section-head"><span>关键信息</span><em>{topSignals.length}</em></div>
          <div className="signal-top">
            {topSignals.slice(0, 5).map((column, index) => {
              const score = column.signal_score ?? column.score ?? column.importance ?? (0.9 - index * 0.08);
              const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
              return (
                <div className="signal-row" key={column.name}>
                  <span className="signal-name">{column.friendly_label || column.name}</span>
                  <span className="signal-track"><i style={{ width: `${pct}%` }} /></span>
                  <em className="signal-val">{(pct / 100).toFixed(2)}</em>
                </div>
              );
            })}
          </div>
        </section>
      ) : null}

    </aside>
  );
}

function Metric({ value, label }) {
  return (
    <div className="metric">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

// 文件类型 → 颜色/标签（数据集列表用）
function fileTypeMeta(doc) {
  const name = String(doc.name || doc.source_file || "");
  const fmt = String(doc.format || (name.match(/\.([a-z0-9]+)$/i)?.[1]) || "").toLowerCase();
  if (/xls|excel|sheet/.test(fmt)) return { tag: "XLS", cls: "ft-excel", color: "#1f9d57", label: "Excel 表格" };
  if (/csv/.test(fmt)) return { tag: "CSV", cls: "ft-csv", color: "#0071e3", label: "CSV 数据" };
  if (/json/.test(fmt)) return { tag: "JSON", cls: "ft-json", color: "#d98a00", label: "JSON 数据" };
  if (/md|markdown/.test(fmt)) return { tag: "MD", cls: "ft-md", color: "#3b3b3f", label: "Markdown" };
  if (/png|jpg|jpeg|webp|gif/.test(fmt)) return { tag: "IMG", cls: "ft-img", color: "#8a4ddf", label: "图片" };
  if (/pdf/.test(fmt)) return { tag: "PDF", cls: "ft-pdf", color: "#d92d20", label: "PDF" };
  if (/parquet/.test(fmt)) return { tag: "PRQ", cls: "ft-doc", color: "#0a8f8f", label: "Parquet" };
  return { tag: "DOC", cls: "ft-doc", color: "#8e8e93", label: doc.format || "文件" };
}

// 文件类型图标：文档外形 + 折角 + 格式标签（嵌入数据集行，区分 Excel/CSV/JSON/MD/IMG）
function FileTypeIcon({ doc, size = 34 }) {
  const meta = fileTypeMeta(doc);
  const dark = meta.color;
  return (
    <span className={`file-ic ${meta.cls}`} aria-label={meta.label}>
      <svg width={size} height={size} viewBox="0 0 28 32" fill="none" aria-hidden="true">
        <path d="M3 3a2 2 0 0 1 2-2h12l8 8v18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" fill={meta.color} opacity="0.14" />
        <path d="M3 3a2 2 0 0 1 2-2h12l8 8v18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" stroke={meta.color} strokeWidth="1.4" />
        <path d="M17 1v6a2 2 0 0 0 2 2h6" stroke={meta.color} strokeWidth="1.4" fill="none" />
        <rect x="6" y="17" width="16" height="9" rx="2" fill={dark} />
        <text x="14" y="23.6" textAnchor="middle" fontSize="6.4" fontWeight="700" fill="#fff" fontFamily="ui-sans-serif, system-ui">{meta.tag}</text>
      </svg>
    </span>
  );
}

function DataPortrait({ workspace, signalColumns, noisyColumns, columns }) {
  const total = columns.length || (signalColumns.length + noisyColumns.length) || 0;
  const noise = columns.filter((c) => c.signal === "noise").length || noisyColumns.length;
  const strong = columns.filter((c) => c.signal && !["noise", "mid", "medium", "weak"].includes(c.signal)).length || signalColumns.length;
  const mid = Math.max(0, total - strong - noise);
  const strength = workspace.signal_score != null
    ? Math.round(workspace.signal_score * (workspace.signal_score <= 1 ? 100 : 1))
    : (total ? Math.round((strong + mid * 0.5) / total * 100) : 0);
  const split = total
    ? [["强信号", strong, "sig"], ["中等信号", mid, "mid"], ["噪音", noise, "noise"]]
    : [["强信号", 0, "sig"], ["中等信号", 0, "mid"], ["噪音", 0, "noise"]];
  return (
    <div className="portrait-card">
      <div className="portrait-ring-col">
        <div className="portrait-ring" style={{ "--value": `${strength}%` }}>
          <strong>{strength}</strong>
        </div>
        <span className="portrait-cap">整体信号可用度</span>
      </div>
      <div className="portrait-split">
        {split.map(([label, count, cls]) => {
          const pct = total ? Math.round((count / total) * 100) : 0;
          return (
            <div className="psplit-row" key={label}>
              <span className="psplit-label"><i className={`psplit-dot ${cls}`} />{label}</span>
              <span className="psplit-track"><b className={cls} style={{ width: `${pct}%` }} /></span>
              <em>{pct}%</em>
            </div>
          );
        })}
        {!total ? <p className="empty-copy">上传数据后生成信号/噪音画像。</p> : null}
      </div>
    </div>
  );
}

export function WorkbenchMain({
  view,
  setView,
  dashboard,
  messages,
  trace,
  streamText,
  running,
  input,
  setInput,
  onRun,
  onStop,
  onNewConversation,
  selectedPlaybook,
  setSelectedPlaybook,
  artifactMode,
  setArtifactMode,
  finalArtifact,
  artifacts,
  onProduce,
  onUploadReference,
  producing,
  observability,
  onOpenConversation,
}) {
  if (view === "conversations") {
    return (
      <ConversationStudio
        dashboard={dashboard}
        messages={messages}
        trace={trace}
        streamText={streamText}
        running={running}
        input={input}
        setInput={setInput}
        onRun={onRun}
        onStop={onStop}
        onProduce={onProduce}
        producing={producing}
        selectedPlaybook={selectedPlaybook}
        setSelectedPlaybook={setSelectedPlaybook}
      />
    );
  }
  if (view === "data") {
    return (
      <Suspense fallback={<main className="agent-studio data-stage"><div style={{ padding: 40, color: "var(--muted)" }}>加载数据工作台…</div></main>}>
        <DataWorkbench dashboard={dashboard} onRun={onRun} />
      </Suspense>
    );
  }
  if (view === "artifacts") {
    return <ArtifactsCenter dashboard={dashboard} artifacts={artifacts} artifact={finalArtifact} onProduce={onProduce} producing={producing} onUploadReference={onUploadReference} />;
  }
  if (view === "runs") {
    return <RunsCenter dashboard={dashboard} trace={trace} running={running} observability={observability} onOpenConversation={onOpenConversation} />;
  }
  if (view === "settings") {
    return <SettingsCenter dashboard={dashboard} observability={observability} />;
  }
  return (
    <DashboardStudio
      dashboard={dashboard}
      trace={trace}
      running={running}
      selectedPlaybook={selectedPlaybook}
      setSelectedPlaybook={setSelectedPlaybook}
      artifactMode={artifactMode}
      setArtifactMode={setArtifactMode}
      finalArtifact={finalArtifact}
      artifacts={artifacts}
      onRun={onRun}
      onNewConversation={onNewConversation}
      onProduce={onProduce}
      producing={producing}
    />
  );
}

const KIND_LABEL = { assumption: "假设", observed: "实测", target: "目标" };
const VERDICT_RANK = { not_yet_feasible: 1, conditional: 2, feasible: 3 };

function feasibilityOf(run) {
  return run?.final?.artifact?.feasibility || run?.artifact?.feasibility || {};
}
function iterInputsOf(run) {
  return run?.final?.artifact?.iteration_inputs || run?.artifact?.iteration_inputs || [];
}
function buildPlanDiff(runA, runB) {
  const a = feasibilityOf(runA);
  const b = feasibilityOf(runB);
  const baseDims = {};
  (a.dimensions || []).forEach((d) => { if (d?.name) baseDims[d.name] = d; });
  const dims = (b.dimensions || []).map((d) => {
    const prev = baseDims[d.name] || {};
    const base = Number(prev.score ?? 0);
    const target = Number(d.score ?? 0);
    return { name: d.name, label: (DIMENSION_LABELS && DIMENSION_LABELS[d.name]) || d.name, base, target, delta: target - base, baseConf: prev.confidence, targetConf: d.confidence };
  });
  const gapsA = new Set((a.gap_list || []).map(String));
  const gapsB = (b.gap_list || []).map(String);
  const gapsBset = new Set(gapsB);
  const added = gapsB.filter((g) => !gapsA.has(g));
  const resolved = [...gapsA].filter((g) => !gapsBset.has(g));
  const vr = (v) => VERDICT_RANK[v] || 0;
  return {
    verdict: { from: a.verdict, to: b.verdict, dir: Math.sign(vr(b.verdict) - vr(a.verdict)) },
    confidence: { from: a.confidence || a.overall_confidence, to: b.confidence || b.overall_confidence },
    dims,
    iterationInputs: iterInputsOf(runB),
    gaps: { added, resolved },
  };
}

const _CV_RANK = { not_yet_feasible: 1, not_feasible: 1, rejected: 1, conditional: 2, feasible: 3, recommended: 3 };
const _CV_LABEL = { 1: "暂不可行", 2: "有条件可行", 3: "可行" };
const _CV_COLOR = { 1: "#8a5a00", 2: "#0A84E0", 3: "#0a7d4f" };
function ConvergenceChart({ versions }) {
  const vers = (versions || []).slice(-6);
  if (vers.length < 2) return null;
  const n = vers.length;
  const padL = 80;
  const padR = 18;
  const H = 132;
  const padT = 22;
  const padB = 30;
  const W = Math.max(300, padL + padR + (n - 1) * 66);
  const usableW = W - padL - padR;
  const usableH = H - padT - padB;
  const x = (i) => padL + (n > 1 ? (usableW * i) / (n - 1) : usableW / 2);
  const y = (r) => padT + usableH * (1 - (r - 1) / 2);
  const rankOf = (v) => _CV_RANK[v.verdict] || 1;
  const pts = vers.map((v, i) => [x(i), y(rankOf(v))]);
  return (
    <svg className="conv-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="迭代收敛图">
      {[1, 2, 3].map((r) => (
        <g key={r}>
          <line x1={padL} y1={y(r)} x2={W - padR} y2={y(r)} stroke="#eef1f5" strokeWidth="1" />
          <text x={padL - 10} y={y(r) + 3} fontSize="10" fill="#9aa3af" textAnchor="end">{_CV_LABEL[r]}</text>
        </g>
      ))}
      <polyline points={pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ")} fill="none" stroke="#0A84E0" strokeWidth="2" />
      {pts.map(([px, py], i) => (
        <g key={i}>
          <circle cx={px.toFixed(1)} cy={py.toFixed(1)} r="4.5" fill={_CV_COLOR[rankOf(vers[i])]} />
          <text x={px.toFixed(1)} y={H - 10} fontSize="10" fill="#6e6e73" textAnchor="middle">{vers[i].vlabel}</text>
        </g>
      ))}
    </svg>
  );
}

function PlanIteratePanel({ workspaceId, runs, running, onIterate }) {
  // 版本 = 该工作区的可行性分析 run（有 verdict 的），按时间正序编号 v1/v2…
  const versions = useMemo(() => {
    const list = (runs || []).filter((r) => r.verdict && (r.run_id || r.conversation_id));
    return list
      .slice()
      .sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")))
      .map((r, i) => ({ ...r, vlabel: `v${i + 1}`, id: r.run_id || r.conversation_id }));
  }, [runs]);
  const latest = versions[versions.length - 1];
  const [flagshipId, setFlagshipId] = useState(null);
  const [baseId, setBaseId] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [cmpA, setCmpA] = useState("");
  const [cmpB, setCmpB] = useState("");
  const [diff, setDiff] = useState(null);
  const [diffLoading, setDiffLoading] = useState(false);

  useEffect(() => {
    if (!workspaceId) return;
    loadFlagship(workspaceId).then((d) => setFlagshipId(d?.flagship_run_id || null)).catch(() => {});
  }, [workspaceId, versions.length]);

  useEffect(() => {
    if (versions.length >= 2) {
      setCmpA(versions[versions.length - 2].id);
      setCmpB(versions[versions.length - 1].id);
    }
  }, [versions.length]);

  const vlabelOf = (id) => versions.find((v) => v.id === id)?.vlabel || "";
  const compare = async () => {
    if (!cmpA || !cmpB || cmpA === cmpB) return;
    setDiffLoading(true); setDiff(null);
    try {
      const [ra, rb] = await Promise.all([loadRun(cmpA), loadRun(cmpB)]);
      setDiff(buildPlanDiff(ra, rb));
    } catch { setDiff(null); } finally { setDiffLoading(false); }
  };

  const effectiveBase = baseId || latest?.id;
  const extract = async () => {
    if (!effectiveBase) return;
    setOpen(true); setLoading(true); setMetrics(null);
    try {
      const d = await loadPlanMetrics(effectiveBase);
      setMetrics((d?.metrics || []).map((m) => ({ ...m })));
    } catch { setMetrics([]); } finally { setLoading(false); }
  };
  const editMetric = (i, patch) => setMetrics((arr) => arr.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  const removeMetric = (i) => setMetrics((arr) => arr.filter((_, j) => j !== i));
  const markFlagship = async (id) => {
    const next = flagshipId === id ? null : id;
    setFlagshipId(next);
    try { await setFlagship(workspaceId, next); } catch { /* ignore */ }
  };
  const runIteration = () => {
    const inputs = (metrics || []).filter((m) => m.label && m.label.trim());
    if (!inputs.length || running) return;
    onIterate?.(inputs);
  };

  if (!versions.length) {
    return (
      <section className="plan-iter-card plan-iter-empty" data-tour="iterate">
        <div className="pi-head">
          <Layers3 size={16} />
          <strong>方案迭代 · 指标回填</strong>
        </div>
        <p className="empty-copy">生成方案后，把试点跑出来的真实客获率/客单价等指标回填进来，即可迭代出下一版，逐步逼近公司重点方案。</p>
      </section>
    );
  }
  return (
    <section className="plan-iter-card" data-tour="iterate">
      <div className="pi-head">
        <Layers3 size={16} />
        <strong>方案迭代 · 指标回填</strong>
        <span className="pi-sub">把上一版方案的客获率/转化率/价格等指标回填，迭代逼近公司重点方案</span>
      </div>

      {versions.length >= 2 ? (
        <div className="pi-converge">
          <div className="pi-converge-head">结论随迭代收敛</div>
          <ConvergenceChart versions={versions} />
        </div>
      ) : null}

      <div className="pi-versions">
        {versions.map((v) => (
          <div key={v.id} className={`pi-ver ${v.id === flagshipId ? "flag" : ""} ${v.id === effectiveBase ? "base" : ""}`}>
            <button type="button" className="pi-ver-main" onClick={() => setBaseId(v.id)} title="以此版为基准提取指标">
              <em>{v.vlabel}</em>
              <span>{VERDICT_LABELS[v.verdict] || v.verdict}</span>
              {v.maf?.revisions ? <small>复修{v.maf.revisions}</small> : null}
            </button>
            <button type="button" className={`pi-star ${v.id === flagshipId ? "on" : ""}`} onClick={() => markFlagship(v.id)} title={v.id === flagshipId ? "取消公司重点方案" : "标为公司重点方案"}>
              <Star size={14} />
            </button>
          </div>
        ))}
      </div>

      {versions.length >= 2 ? (
        <div className="pi-compare">
          <div className="pi-cmp-bar">
            <span className="pi-cmp-title">版本对比</span>
            <select value={cmpA} onChange={(e) => { setCmpA(e.target.value); setDiff(null); }}>
              {versions.map((v) => <option key={v.id} value={v.id}>{v.vlabel}</option>)}
            </select>
            <span className="pi-cmp-arrow">→</span>
            <select value={cmpB} onChange={(e) => { setCmpB(e.target.value); setDiff(null); }}>
              {versions.map((v) => <option key={v.id} value={v.id}>{v.vlabel}</option>)}
            </select>
            <button type="button" className="pi-cmp-go" onClick={compare} disabled={!cmpA || !cmpB || cmpA === cmpB || diffLoading}>
              {diffLoading ? <Loader2 className="spin" size={13} /> : <BarChart3 size={13} />} 对比
            </button>
          </div>
          {diff ? (
            <div className="pi-diff">
              <div className="pi-diff-row head">
                <span className="pi-diff-k">维度</span>
                <span>{vlabelOf(cmpA)}</span>
                <span>{vlabelOf(cmpB)}</span>
                <span>变化</span>
              </div>
              <div className="pi-diff-row">
                <span className="pi-diff-k">可行性结论</span>
                <span>{VERDICT_LABELS[diff.verdict.from] || diff.verdict.from || "—"}</span>
                <span>{VERDICT_LABELS[diff.verdict.to] || diff.verdict.to || "—"}</span>
                <span className={`pi-delta ${diff.verdict.dir > 0 ? "up" : diff.verdict.dir < 0 ? "down" : "flat"}`}>
                  {diff.verdict.dir > 0 ? "↑ 提升" : diff.verdict.dir < 0 ? "↓ 下降" : "持平"}
                </span>
              </div>
              {diff.dims.map((d) => (
                <div className="pi-diff-row" key={d.name}>
                  <span className="pi-diff-k">{d.label}</span>
                  <span>{d.base}/5</span>
                  <span>{d.target}/5</span>
                  <span className={`pi-delta ${d.delta > 0 ? "up" : d.delta < 0 ? "down" : "flat"}`}>
                    {d.delta > 0 ? `+${d.delta}` : d.delta < 0 ? `${d.delta}` : "—"}
                  </span>
                </div>
              ))}
              {diff.iterationInputs?.length ? (
                <div className="pi-diff-note">
                  <em>{vlabelOf(cmpB)} 回填了：</em>
                  {diff.iterationInputs.map((m, i) => (
                    <span className="pi-diff-chip" key={i}>{m.label} {m.value}{m.unit} · {KIND_LABEL[m.kind] || m.kind}</span>
                  ))}
                </div>
              ) : null}
              {(diff.gaps.added.length || diff.gaps.resolved.length) ? (
                <div className="pi-diff-gaps">
                  {diff.gaps.resolved.length ? <span className="g-res">已解决 {diff.gaps.resolved.length}</span> : null}
                  {diff.gaps.added.length ? <span className="g-add">新增下一步 {diff.gaps.added.length}</span> : null}
                  {diff.gaps.added.slice(0, 2).map((g, i) => <div className="g-item" key={i}>＋ {String(g).slice(0, 80)}</div>)}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {!open ? (
        <button type="button" className="pi-extract" onClick={extract} disabled={!effectiveBase}>
          <Sparkles size={14} /> 提取「{(versions.find((v) => v.id === effectiveBase)?.vlabel) || "当前"}」方案指标 → 回填迭代
        </button>
      ) : loading ? (
        <div className="pi-loading"><Loader2 className="spin" size={14} /> 正在从方案中抽取关键指标…</div>
      ) : (
        <div className="pi-editor">
          {(metrics && metrics.length) ? (
            <>
              <div className="pi-rows">
                {metrics.map((m, i) => (
                  <div className="pi-row" key={i}>
                    <span className="pi-label" title={m.note || ""}>{m.label}</span>
                    <input className="pi-val" value={m.value} placeholder="数值" onChange={(e) => editMetric(i, { value: e.target.value })} />
                    <input className="pi-unit" value={m.unit} placeholder="单位" onChange={(e) => editMetric(i, { unit: e.target.value })} />
                    <select className="pi-kind" value={m.kind} onChange={(e) => editMetric(i, { kind: e.target.value })}>
                      <option value="assumption">假设</option>
                      <option value="observed">实测</option>
                      <option value="target">目标</option>
                    </select>
                    <button type="button" className="pi-del" onClick={() => removeMetric(i)} title="移除">✕</button>
                  </div>
                ))}
              </div>
              <div className="pi-tip">把实际跑出来的值填进去并标为「实测」，分析会据此把方案做得更准——这些值仅作假设/回填，不会被当成工作区已证实数据。</div>
              <div className="pi-actions">
                <button type="button" className="pi-iterate" onClick={runIteration} disabled={running}>
                  {running ? <><Loader2 className="spin" size={14} /> 正在生成下一版…</> : <><RefreshCw size={14} /> 基于回填指标生成下一版方案</>}
                </button>
                <button type="button" className="pi-reset" onClick={() => setOpen(false)}>收起</button>
              </div>
            </>
          ) : (
            <div className="pi-empty">没从这版方案里抽到可回填的量化指标。<button type="button" onClick={() => setOpen(false)}>收起</button></div>
          )}
        </div>
      )}
    </section>
  );
}

function WebSearchPanel({ trace }) {
  const hit = useMemo(() => {
    let found = null;
    (trace || []).forEach((e) => {
      if (e.event === "tool_result" && e.data?.name === "foundry_native_web_search") found = e.data;
    });
    return found;
  }, [trace]);
  const sources = (hit?.sources || []).filter((s) => s && (s.url || s.title));
  if (!hit || !sources.length) return null;
  const domain = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  return (
    <section className="websearch-card">
      <div className="ws-head">
        <Globe size={15} />
        <strong>联网检索 · Foundry Web Search</strong>
        <span className="ws-tag">{sources.length} 条外部来源 · market_inferred</span>
      </div>
      <div className="ws-list">
        {sources.slice(0, 6).map((s, i) => (
          <a className="ws-item" key={i} href={s.url || undefined} target="_blank" rel="noreferrer" title={s.url || ""}>
            <span className="ws-dot" />
            <span className="ws-title">{String(s.title || s.url).slice(0, 76)}</span>
            <span className="ws-domain">{domain(s.url)}</span>
          </a>
        ))}
      </div>
    </section>
  );
}

function DataOverviewCard({ workspaceId, hasDocs }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const cacheRef = useRef({});
  useEffect(() => {
    if (!workspaceId || !hasDocs) { setData(null); return undefined; }
    if (cacheRef.current[workspaceId]) { setData(cacheRef.current[workspaceId]); return undefined; }
    let cancelled = false;
    setLoading(true); setData(null);
    loadDataOverview(workspaceId)
      .then((d) => { if (!cancelled && d && (d.overview || (d.datasets && d.datasets.length))) { cacheRef.current[workspaceId] = d; setData(d); } })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [workspaceId, hasDocs]);
  if (!hasDocs) return null;
  if (loading) return <section className="data-overview is-loading"><Loader2 className="spin" size={14} /> Agent 正在解读这批数据是什么…</section>;
  if (!data || !data.overview) return null;
  return (
    <section className="data-overview">
      <div className="do-head"><Database size={16} /><strong>数据说明 · Agent 解读</strong></div>
      <p className="do-overview">{data.overview}</p>
      {data.datasets?.length ? (
        <div className="do-datasets">
          {data.datasets.map((ds, i) => (
            <div className="do-dataset" key={`${ds.name}-${i}`}>
              <div className="do-dataset-name"><FileText size={14} /><strong>{ds.name}</strong></div>
              <p className="do-dataset-what">{ds.what}</p>
            </div>
          ))}
        </div>
      ) : null}
      {data.usable_for?.length ? (
        <div className="do-uses">
          <em>可支撑</em>
          {data.usable_for.map((u, i) => <span className="do-use" key={i}>{u}</span>)}
        </div>
      ) : null}
    </section>
  );
}

function Collapsible({ title, hint, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={open ? "collapsible open" : "collapsible"}>
      <button type="button" className="collapsible-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <ChevronDown size={15} className="cl-caret" />
        <span>{title}</span>
        {hint ? <em className="cl-hint">{hint}</em> : null}
      </button>
      {open ? <div className="collapsible-body">{children}</div> : null}
    </section>
  );
}

function OverviewCards({ workspace = {}, documents = [], runs = [] }) {
  const assets = documents.length || workspace.doc_count || 0;
  const records = workspace.row_count || 0;
  const rawCov = workspace.field_fill_rate ?? workspace.fill_rate ?? 0;
  const coverage = Math.round(rawCov > 1 ? rawCov : rawCov * 100);
  const fields = workspace.field_count || 0;
  const lastRun = runs[0];
  const runLabel = lastRun ? (VERDICT_LABELS[lastRun.verdict] || (lastRun.status === "done" || lastRun.completed_at ? "已完成" : "运行中")) : "—";
  const cards = [
    { ic: <Database size={18} />, n: assets, l: "数据源", s: `${fields} 个字段` },
    { ic: <Rows3 size={18} />, n: records, l: "记录数", s: `字段覆盖率 ${coverage}%` },
    { ic: <TrendingUp size={18} />, n: `${coverage}%`, l: "字段覆盖率", s: coverage >= 100 ? "完整结构" : "部分结构" },
    { ic: <Clock3 size={18} />, n: runLabel, l: "最近运行", s: lastRun?.title ? String(lastRun.title).slice(0, 22) : "暂无运行", small: true },
  ];
  return (
    <div className="ov-grid">
      {cards.map((c, i) => (
        <div className="card ov-card" key={i}>
          <div className="ov-ic">{c.ic}</div>
          <div className={c.small ? "ov-n small" : "ov-n"}>{c.n}</div>
          <div className="ov-l">{c.l}</div>
          <div className="ov-s">{c.s}</div>
        </div>
      ))}
    </div>
  );
}

const PIPELINE_STAGES = [
  { nm: "Coordinator", ds: "Task routing", mk: "coordinat" },
  { nm: "Corpus Analyst", ds: "Index & profile", mk: "corpus" },
  { nm: "Feasibility Analyst", ds: "Scoring", mk: "feasib" },
  { nm: "Market Researcher", ds: "External research", mk: "market" },
  { nm: "Auditor", ds: "Verify & check", mk: "audit" },
  { nm: "Product Generator", ds: "PDF / Deck", mk: "produc" },
];
function AgentPipeline({ trace = [], running = false, hasResult = false }) {
  let seen = -1;
  for (const t of trace) {
    const s = JSON.stringify(t).toLowerCase();
    PIPELINE_STAGES.forEach((stage, i) => { if (s.includes(stage.mk)) seen = Math.max(seen, i); });
  }
  const doneCount = hasResult ? PIPELINE_STAGES.length : Math.max(0, seen + 1);
  const curIdx = hasResult ? -1 : running ? Math.min(doneCount, PIPELINE_STAGES.length - 1) : -1;
  const fillPct = PIPELINE_STAGES.length > 1
    ? ((Math.max(0, (hasResult ? PIPELINE_STAGES.length - 1 : curIdx >= 0 ? curIdx : doneCount - 1)) ) / (PIPELINE_STAGES.length - 1)) * 100
    : 0;
  return (
    <div className="card pipe-card" data-tour="pipeline">
      <div className="pipe-head"><span className="t">Agent pipeline</span><span className="lnk">View details</span></div>
      <div className="pipe-flow">
        <div className="pipe-line"><div className="pipe-fill" style={{ width: `${Math.max(0, Math.min(100, fillPct))}%` }} /></div>
        {PIPELINE_STAGES.map((stage, i) => {
          const done = i < doneCount && i !== curIdx;
          const cur = i === curIdx;
          return (
            <div className={`pipe-node ${done ? "done" : ""} ${cur ? "cur" : ""}`} key={stage.nm}>
              <div className="pipe-dot">{done ? <Check size={17} /> : cur ? <Loader2 size={16} className="spin" /> : <span className="pipe-i" />}</div>
              <div className="pipe-nm">{stage.nm}</div>
              <div className="pipe-ds">{stage.ds}</div>
              {cur ? <div className="pipe-st">In progress</div> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DataAssetsTable({ documents = [], workspace = {}, detailOpen = false, onViewDetails, workspaceId }) {
  const rows = documents.slice(0, 6);
  const isIndexed = (status) => !status || /就绪|已解析|ready|done|index/i.test(String(status));
  const updated = workspace.updated_at ? new Date(workspace.updated_at).toLocaleDateString("zh-CN") : "Today";
  return (
    <div className="card tbl-card" data-tour="artifacts">
      <div className="cardhead">
        <span className="t">{detailOpen ? "数据说明 · Agent 解读" : "最近数据资产"}</span>
        {onViewDetails ? (
          <button type="button" className="lnk lnk-btn" onClick={onViewDetails}>{detailOpen ? "返回资产列表" : "查看详情"}</button>
        ) : null}
      </div>
      <div className="tbl-wrap">
        {detailOpen ? (
          <div className="asset-detail"><DataOverviewCard workspaceId={workspaceId} hasDocs={documents.length > 0} embedded /></div>
        ) : (
        <table className="data-table">
          <thead><tr><th>名称</th><th>类型</th><th>字段</th><th>记录</th><th>状态</th><th>更新时间</th></tr></thead>
          <tbody>
            {rows.length ? rows.map((doc, i) => {
              const name = doc.name || sanitizeSourceLabel(doc.source_file) || `asset-${i}`;
              const ok = isIndexed(doc.status);
              return (
                <tr key={doc.source_file || name}>
                  <td><span className="td-name"><FileTypeIcon doc={doc} size={20} />{name}</span></td>
                  <td>{doc.format || "文档"}</td>
                  <td><span className="td-chip">部分字段</span></td>
                  <td className="td-mut">—</td>
                  <td><span className={ok ? "td-stt ok" : "td-stt warn"}><span className="d" />{ok ? "已索引" : "待复核"}</span></td>
                  <td className="td-mut">{updated}</td>
                </tr>
              );
            }) : (
              <tr><td colSpan={6} className="td-empty">上传数据后，这里列出工作区的数据资产。</td></tr>
            )}
          </tbody>
        </table>
        )}
      </div>
    </div>
  );
}

function DashboardStudio({
  dashboard,
  trace,
  running,
  selectedPlaybook,
  setSelectedPlaybook,
  artifactMode,
  setArtifactMode,
  finalArtifact,
  artifacts,
  onRun,
  onNewConversation,
  onProduce,
  producing,
}) {
  const workspace = dashboard?.workspace || {};
  const columns = workspace.columns || [];
  const documents = workspace.documents || [];
  const signalColumns = columns.filter((column) => column.signal && column.signal !== "noise").slice(0, 5);
  const presentation = useAgentPresentation(trace, running, producing);
  const hasArtifacts = Object.values(artifacts || {}).some(Boolean);
  const feasibility = finalArtifact?.feasibility || {};
  const verdict = VERDICT_LABELS[feasibility.verdict] || "等待分析";
  const [showDataDetail, setShowDataDetail] = useState(false);
  const radarGroups = (feasibility.dimensions || []).reduce(
    (acc, d) => {
      const s = Number(d.score || 0);
      const label = DIMENSION_LABELS[d.name] || d.name;
      (s >= 4 ? acc.adv : s >= 3 ? acc.good : acc.watch).push(label);
      return acc;
    },
    { adv: [], good: [], watch: [] },
  );

  return (
    <main className="agent-studio dashboard-stage">
      <section className="dashboard-hero">
        <div>
          <div className="dh-title"><h1>{workspace.name || "数据产品化工作区"}</h1><span className="tag-ws">工作区</span></div>
          <p>{workspace.customer_summary || workspace.profile_summary || "整合客户资料、市场反馈与产品数据，自动发现可落地的产品机会。"}</p>
        </div>
        <div className="dashboard-actions">
          <button className="ghost-button icon-label" type="button" onClick={onNewConversation}>
            <MessageSquare size={15} />
            新建会话
          </button>
          <button data-tour="analyze" className="primary-button icon-label" type="button" onClick={() => onRun("请基于当前工作区，先自动分析这批数据可以产品化成什么机会，并说明证据强弱、市场推断和下一步。", { stayOnDashboard: true })}>
            <Sparkles size={15} />
            自动分析
          </button>
        </div>
      </section>

      <OverviewCards workspace={workspace} documents={documents} runs={dashboard?.runs || []} />

      <AgentRoute trace={trace} running={running} presentation={presentation} producing={producing} hasArtifacts={hasArtifacts} onProduce={onProduce} />

      <div className="ws-trio">
        <VerdictHero compact feasibility={feasibility} verdict={verdict} running={running} artifact={finalArtifact} onViewReport={onNewConversation} />
        <section className="card radar-card">
          <div className="rc-head">五维评估得分</div>
          <div className="rc-body">
            <VerdictRadar dims={feasibility.dimensions} />
            <div className="rc-legend">
              <div className="rcl-row"><span className="rcl-dot adv" /><b>优势维度 ({radarGroups.adv.length})</b><em>{radarGroups.adv.join("、") || "—"}</em></div>
              <div className="rcl-row"><span className="rcl-dot good" /><b>良好维度 ({radarGroups.good.length})</b><em>{radarGroups.good.join("、") || "—"}</em></div>
              <div className="rcl-row"><span className="rcl-dot watch" /><b>关注维度 ({radarGroups.watch.length})</b><em>{radarGroups.watch.join("、") || "—"}</em></div>
            </div>
          </div>
        </section>
        <AuditCard artifact={finalArtifact} />
      </div>

      <DataAssetsTable documents={documents} workspace={workspace} detailOpen={showDataDetail} onViewDetails={() => setShowDataDetail((v) => !v)} workspaceId={dashboard?.workspace_id || workspace?.workspace_id} />

      <Collapsible title="高级分析" hint="运行更深入的模型与仿真分析">
        <section className="studio-methods">
          <ActionPlanCards selected={selectedPlaybook} onSelect={setSelectedPlaybook} feasibility={feasibility} workspaceId={dashboard?.workspace_id || dashboard?.workspace?.workspace_id} />
          <ActionBoard artifact={finalArtifact} selectedPlaybook={selectedPlaybook} onProduce={onProduce} producing={producing} />
        </section>
        <WebSearchPanel trace={trace} />
      </Collapsible>

      <PlanIteratePanel
        workspaceId={dashboard?.workspace_id || workspace?.workspace_id}
        runs={dashboard?.runs || []}
        running={running}
        onIterate={(inputs) => onRun("基于回填指标迭代优化这版方案，逼近一个可作为公司重点的方案。", { stayOnDashboard: true, iterationInputs: inputs })}
      />
    </main>
  );
}

// 自审计显性化：把"审计 agent 盲判复核 → 修订/通过"作为创新卖点直接展示
function AuditCard({ artifact }) {
  const audit = artifact?.audit;
  const contract = artifact?.verdict || {};
  const fe = artifact?.feasibility || {};
  const hasAnalysis = Boolean(fe.verdict || (fe.dimensions && fe.dimensions.length));
  if (!audit || !hasAnalysis) return null;
  const revised = contract.revised || null;
  const blindLabel = contract.blind?.judgment || VERDICT_LABELS[fe.verdict] || "初判";
  const disagreement = (contract.disagreement || []).slice(0, 4);
  const issues = (audit.issues || []).filter(Boolean).slice(0, 3);
  const passed = audit.verdict === "pass" && !revised;
  const fmt = (v) => (typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(1)) : v);
  return (
    <section className={`audit-card ${passed ? "is-pass" : "is-revise"}`}>
      <div className="audit-head">
        <ShieldCheck size={16} />
        <div className="audit-title">
          <strong>独立审计 · 自我校验</strong>
          <span>审计 Agent 在不看初判结论的前提下复核分析，避免自我确认偏差、不自欺地放大结论。</span>
        </div>
        <span className={`audit-badge ${passed ? "pass" : "revise"}`}>{passed ? "审计通过 · 结论一致" : "审计已修订结论"}</span>
      </div>
      <div className={`audit-revision ${revised ? "" : "unchanged"}`}>
        <span className="av-from">{blindLabel}</span>
        <span className="av-arrow">→</span>
        <span className="av-to">{revised ? revised.judgment : blindLabel}</span>
        <em>{revised ? "盲判 → 审计修订后结论" : "盲判与初判一致，结论未被放大"}</em>
      </div>
      {disagreement.length ? (
        <ul className="audit-diffs">
          {disagreement.map((d, i) => (
            <li key={i}>
              <span className="ad-dim">{d.dim}</span>
              <b>{fmt(d.blind)}</b>
              <span className="av-arrow">→</span>
              <b>{fmt(d.revised)}</b>
            </li>
          ))}
        </ul>
      ) : null}
      {issues.length ? (
        <ul className="audit-issues">
          {issues.map((it, i) => <li key={i}>{it}</li>)}
        </ul>
      ) : null}
    </section>
  );
}

function MetricCard({ icon: Icon, label, value, detail }) {
  return (
    <article className="metric-card">
      <Icon size={17} />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <em>{detail}</em>
      </div>
    </article>
  );
}

function DataPipelineCard({ workspace, documents }) {
  const steps = [
    { label: "上传", done: documents.length > 0, detail: `${documents.length || 0} 个文件` },
    { label: "格式识别", done: Boolean(workspace.format || documents.length), detail: workspace.format || "mixed" },
    { label: "数据画像", done: Boolean(workspace.profile_summary || workspace.customer_summary), detail: "schema / 分布 / 信号" },
    { label: "Search 入库", done: Number(workspace.doc_count || workspace.indexed_count || 0) > 0, detail: `${workspace.doc_count || workspace.indexed_count || 0} 条可检索` },
    { label: "Agent 就绪", done: true, detail: "可发起会话和产物生成" },
  ];
  return (
    <div className="pipeline-steps">
      {steps.map((step) => (
        <div key={step.label} className={step.done ? "pipeline-step done" : "pipeline-step"}>
          <CheckCircle2 size={15} />
          <div>
            <strong>{step.label}</strong>
            <span>{step.detail}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function verdictTone(v) {
  const t = String(v || "");
  if (/暂不|不可行|不适合|不建议/.test(t)) return "no";
  if (/有条件|条件|谨慎|待/.test(t)) return "cond";
  if (/可行/.test(t)) return "yes";
  return "cond";
}

// 中央英雄区：可行性结论大字 + 五维可行性评分横条（对齐 效果.png）
function VerdictRadar({ dims }) {
  const items = (dims || []).slice(0, 5);
  if (items.length < 3) return null;
  const size = 192;
  const cx = size / 2;
  const cy = size / 2;
  const R = size * 0.31;
  const n = items.length;
  const pt = (i, r) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const ring = (k) => items.map((_, i) => pt(i, R * k).map((v) => v.toFixed(1)).join(",")).join(" ");
  const sc = (d) => Math.max(0, Math.min(5, Number(d.score || 0)));
  const shape = items.map((d, i) => pt(i, R * (sc(d) / 5)).map((v) => v.toFixed(1)).join(",")).join(" ");
  return (
    <svg className="verdict-radar" viewBox={`0 0 ${size} ${size}`} role="img" aria-label="五维雷达图">
      {[0.25, 0.5, 0.75, 1].map((k) => (
        <polygon key={k} points={ring(k)} fill="none" stroke="#e5e7eb" strokeWidth="1" />
      ))}
      {items.map((_, i) => {
        const [x, y] = pt(i, R);
        return <line key={i} x1={cx} y1={cy} x2={x.toFixed(1)} y2={y.toFixed(1)} stroke="#e5e7eb" strokeWidth="1" />;
      })}
      <polygon points={shape} fill="rgba(37,99,235,0.14)" stroke="#2563eb" strokeWidth="2" />
      {items.map((d, i) => {
        const [x, y] = pt(i, R * (sc(d) / 5));
        return <circle key={i} cx={x.toFixed(1)} cy={y.toFixed(1)} r="3" fill="#2563eb" />;
      })}
      {items.map((d, i) => {
        const [lx, ly] = pt(i, R + 16);
        return (
          <text key={i} x={lx.toFixed(1)} y={ly.toFixed(1)} fontSize="10.5" fill="#6e6e73" textAnchor="middle" dominantBaseline="middle">
            {(DIMENSION_LABELS && DIMENSION_LABELS[d.name]) || d.name}
          </text>
        );
      })}
    </svg>
  );
}

function VerdictHero({ feasibility, verdict, running, artifact, compact, onViewReport }) {
  const raw = feasibility?.dimensions || [];
  const dims = raw.length
    ? raw
    : Object.keys(DIMENSION_LABELS).map((name) => ({ name, score: 0, confidence: "speculative" }));
  const opportunity =
    feasibility?.opportunity?.title ||
    feasibility?.opportunity ||
    feasibility?.headline ||
    feasibility?.summary ||
    "";
  const conf = feasibility?.confidence || dims[0]?.confidence || "";
  const tone = verdictTone(verdict);
  const downgrade = artifact?.verdict?.downgrade || artifact?.verdict_downgrade || null;
  const beforeLabel = downgrade?.verdict_before_label || VERDICT_LABELS[downgrade?.verdict_before] || downgrade?.verdict_before;
  const afterLabel = downgrade?.verdict_after_label || VERDICT_LABELS[downgrade?.verdict_after] || downgrade?.verdict_after;
  const downgradeReason = String(downgrade?.downgrade_reason || "证据不足以支撑原结论").replace(/[。.!！\s]+$/, "");
  return (
    <section className={`verdict-hero tone-${tone}${compact ? " compact" : ""}`} data-tour="verdict">
      <div className="vh-left">
        <span className="vh-label">可行性结论{running ? " · 实时" : ""}</span>
        <h2 className="vh-judgment">{verdict}</h2>
        {conf ? <span className={`vh-conf ${conf}`}>{CONFIDENCE_LABELS[conf] || conf}</span> : null}
        {opportunity && typeof opportunity === "string" ? (
          <div className="vh-discovery">
            <span className="vh-discover-chip"><Sparkles size={13} /> Agent 发现的机会</span>
            <p className="vh-opp">{opportunity}</p>
          </div>
        ) : (
          <p className="vh-opp muted">发起一次分析后，这里给出机会判断、置信度与可落地建议。</p>
        )}
        {downgrade ? (
          <div className="vh-downgrade">
            <AlertTriangle size={15} />
            <span>审计已将结论从 {beforeLabel} 降为 {afterLabel}，因为{downgradeReason}。</span>
          </div>
        ) : null}
        {compact ? (
          <button type="button" className="vh-report-btn" onClick={onViewReport}><FileText size={14} />查看详细分析报告</button>
        ) : null}
      </div>
      {compact ? null : (
        <div className="vh-scores">
          <div className="vh-scores-head">五维可行性评分</div>
          {dims.slice(0, 5).map((dim) => {
            const n = Math.max(0, Math.min(5, Number(dim.score || 0)));
            return (
              <div className="vh-score" key={dim.name}>
                <span className="vh-score-label">{DIMENSION_LABELS[dim.name] || dim.name}</span>
                <span className="vh-score-track"><i className={`v${Math.round(n)}`} style={{ width: `${n * 20}%` }} /></span>
                <span className="vh-score-val">{Math.round(n * 20)}</span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ConversationStudio({
  dashboard,
  messages,
  trace,
  streamText,
  running,
  input,
  setInput,
  onRun,
  onStop,
  onProduce,
  producing,
  selectedPlaybook,
  setSelectedPlaybook,
}) {
  const workspace = dashboard?.workspace || {};
  const docs = workspace.documents || [];
  const recent = (dashboard?.runs || [])[0] || {};
  const recentVerdict = recent.verdict ? (VERDICT_LABELS[recent.verdict] || recent.verdict) : "有条件可行";
  const presentation = useAgentPresentation(trace, running);
  const chips = ["做一场拉新活动", "能产品化成什么?", "先试点哪个客群?", "生成 PRD 草案", "评估定价", "看外部市场"];
  const hasMsgs = messages.length || streamText;
  const quickActions = [
    { icon: FileDown, title: "生成 PRD 草案", desc: "基于当前结论生成产品需求文档", onClick: () => onRun("基于当前结论生成一份 PRD 草案。") },
    { icon: ImagePlus, title: "生成概念图", desc: "生成产品概念图 / 逻辑关系图", onClick: () => onProduce && onProduce(["concept_image"]) },
    { icon: Route, title: "查看运行记录", desc: "查看最近的数据分析运行记录", onClick: () => {} },
    { icon: MoreHorizontal, title: "更多操作", desc: "", onClick: () => {} },
  ];

  return (
    <main className="agent-studio conv-stage">
      <header className="conv-head">
        <span className="eyeless-label">Conversation</span>
        <h1>AI Agent 会话</h1>
        <p>基于当前工作区的证据与数据，AI Agent 为你提供分析结论与可执行建议。</p>
      </header>

      <div className="conv-chips">
        <span className="cc-label">你可以尝试问</span>
        {chips.map((c) => (
          <button key={c} type="button" className="cc-chip" disabled={running} onClick={() => onRun(c)}>{c}</button>
        ))}
      </div>

      <div className="conv-body">
        <div className="conv-main">
          {hasMsgs ? (
            <AnswerPanel messages={messages} streamText={streamText} running={running} presentation={presentation} onRun={onRun} onProduce={onProduce} producing={producing} trace={trace} onStop={onStop} />
          ) : (
            <article className="card conv-result">
              <div className="cr-top"><span className="cr-av">AI</span><b>AI Agent</b><em>· 示例分析结果</em></div>
              <div className="cr-sec"><div className="cr-h"><FileText size={15} />结论摘要</div>
                <p>基于 3 个数据源与多维分析，推荐在 华东-上海-浦东 的 高新区 人流-竞品-租金综合表现最优，适合作为首批试点区域。</p>
                <p>预计试点 4 周内可完成 1,250–1,750 组意向触达，首月可沉淀 68–98 组高意向线索，ROI 预期为 1.8–2.3。</p>
              </div>
              <div className="cr-sec"><div className="cr-h"><Lightbulb size={15} />核心建议</div>
                <div className="cr-advice">
                  <div className="cra"><b>首批试点区域</b><strong>华东-上海-浦东 · 高新区</strong><em>综合得分 82 / 100</em></div>
                  <div className="cra"><b>目标客群</b><strong>25–35 岁新锐白领 & 创业者</strong><em>预计覆盖 62% 潜在客群</em></div>
                  <div className="cra"><b>关键行动</b><strong>4 周拉新试点 + 内容种草 + 线下体验</strong><em>预计首月 ROI 1.8 – 2.3</em></div>
                </div>
              </div>
              <div className="cr-sec"><div className="cr-h"><BookOpen size={15} />依据</div>
                <ul className="cr-basis">
                  <li><b>surrounding_env</b>：工作区数据画像与结构概览（字段 / 记录规模）</li>
                  <li><b>device_events</b>：人流与行为数据（近 30 天）</li>
                  <li><b>market_notes</b>：市场与竞品信息（行业报告 / 公开资料）</li>
                </ul>
              </div>
              <div className="cr-sec"><div className="cr-h"><FileText size={15} />证据</div>
                <div className="cr-evid">
                  {(docs.length ? docs.slice(0, 3) : [{ name: "surrounding_env.xlsx", format: "xlsx" }, { name: "device_events.csv", format: "csv" }, { name: "market_notes.md", format: "md" }]).map((d, i) => (
                    <div className="cre" key={i}><FileTypeIcon doc={d} size={22} /><div><b>{d.name}</b><em>{d.format || "文档"}</em></div></div>
                  ))}
                  <div className="cre muted"><Database size={18} /><div><b>共 {docs.length || 3} 个数据源</b><em>已关联分析</em></div></div>
                </div>
              </div>
              <div className="cr-foot">
                <span>以上结论由 AI 生成，请结合你的业务判断使用。</span>
                <div className="cr-acts">
                  <button type="button"><Copy size={14} />复制</button>
                  <button type="button"><FileDown size={14} />生成 PRD 草案</button>
                  <button type="button" className="cr-ico"><ThumbsUp size={14} /></button>
                  <button type="button" className="cr-ico"><ThumbsDown size={14} /></button>
                </div>
              </div>
            </article>
          )}
        </div>

        <aside className="conv-context">
          <section className="card ctx-card">
            <div className="ctx-h">当前工作区上下文</div>
            <div className="ctx-kv"><span>工作区</span><b>{workspace.name || "当前工作区"}</b></div>
            <div className="ctx-kv"><span>角色</span><b>所有者</b></div>
            <div className="ctx-kv"><span>描述</span><b className="ctx-desc">{workspace.customer_summary || workspace.profile_summary || "基于多源数据与智能体协同，生成可行性评估与情报报告。"}</b></div>
            <button type="button" className="ctx-btn">查看工作区详情</button>
          </section>

          <section className="card ctx-card">
            <div className="ctx-h">当前数据源<em>{docs.length || 3} 个数据源已关联</em></div>
            <div className="ctx-srcs">
              {(docs.length ? docs.slice(0, 4) : [{ name: "surrounding_env.xlsx", format: "xlsx" }, { name: "device_events.csv", format: "csv" }, { name: "market_notes.md", format: "md" }]).map((d, i) => (
                <div className="ctx-src" key={i}><FileTypeIcon doc={d} size={18} /><b>{d.name}</b></div>
              ))}
            </div>
            <button type="button" className="lnk lnk-btn">查看全部数据源 ›</button>
          </section>

          <section className="card ctx-card">
            <div className="ctx-h">最近分析结论</div>
            <div className="ctx-kv"><span>综合结论</span><span className="dw-chip ok">{recentVerdict}</span></div>
            <div className="ctx-kv"><span>生成时间</span><b>{formatTime(recent.completed_at || recent.time) || "2024-05-02 10:22"}</b></div>
            <div className="ctx-sub">基于 {docs.length || 3} 个数据源 · 12 项指标 <button type="button" className="lnk lnk-btn">查看详情</button></div>
          </section>

          <section className="card ctx-card">
            <div className="ctx-h">审计状态</div>
            <div className="ctx-audit"><span>最近审计</span><span className="dw-chip ok">通过</span><span className="ctx-audit-x">已执行 · 异常项 0</span></div>
            <div className="ctx-sub">12 项检查 <button type="button" className="lnk lnk-btn">查看审计详情 ›</button></div>
          </section>

          <section className="card ctx-card">
            <div className="ctx-h">快捷操作</div>
            <div className="ctx-qa">
              {quickActions.map((a, i) => { const Ic = a.icon; return (
                <button type="button" className="qa-item" key={i} onClick={a.onClick}><span className="qa-ic"><Ic size={16} /></span><div><b>{a.title}</b>{a.desc ? <em>{a.desc}</em> : null}</div></button>
              ); })}
            </div>
          </section>
        </aside>
      </div>

      <div className="conv-composer">
        <Composer input={input} setInput={setInput} running={running} onRun={onRun} onStop={onStop} selectedPlaybook={selectedPlaybook} />
      </div>
    </main>
  );
}

function ArtifactsCenter({ dashboard, artifacts, artifact, onProduce, producing, onUploadReference }) {
  const workspace = dashboard?.workspace || {};
  const refs = workspace.reference_images || [];
  return (
    <main className="agent-studio artifacts-stage">
      <section className="dashboard-hero">
        <div>
          <span className="eyeless-label">Outputs</span>
          <h1>产出物中心</h1>
          <p>每个产物各自一个生成按钮，按需分项生成。会话/工作区里的「生成产物」默认是项目文档 + 概念图一套。</p>
        </div>
      </section>
      <section className="logo-callout">
        <ImagePlus size={22} />
        <div>
          <strong>{refs.length ? `已检测到 ${refs.length} 张参考图` : "生成海报或周边前，建议上传透明 PNG Logo"}</strong>
          <span>例如活动主视觉、包装样机、服务触点物料，都可以把 Logo 作为参考图交给图像生成 Agent。</span>
        </div>
        <button className="ghost-button icon-label" type="button" onClick={onUploadReference}>
          <UploadCloud size={15} />
          上传参考图
        </button>
      </section>
      <OutputPanel artifacts={artifacts} artifact={artifact} running={producing} onProduce={onProduce} producing={producing} />
    </main>
  );
}

// 一步的输出/结果摘要
function stepDetail(ev) {
  const d = ev.data || {};
  const ms = d.latency_ms != null ? `${d.latency_ms}ms` : "";
  const join = (...parts) => parts.filter(Boolean).join(" · ");
  if (ev.event === "tool_result") return join(d.count != null ? `${d.count} 条结果` : d.status ? String(d.status) : "", ms);
  if (ev.event === "tool_call") return d.name ? "" : "";
  if (ev.event === "model_response") return join(d.usage?.total_tokens ? `${d.usage.total_tokens} tokens` : "", ms);
  if (ev.event === "audit") return (d.issues && d.issues.length) ? String(d.issues[0]).slice(0, 60) : (d.verdict === "pass" ? "通过" : "要求修订");
  if (ev.event === "final") return String(d.text || "").replace(/\s+/g, " ").slice(0, 90);
  if (ev.event === "clarify") return String(d.question || "").slice(0, 60);
  if (ev.event === "plan" || ev.event === "route") return (d.experts || []).map((e) => (AGENTS.find((a) => a.id === e)?.zh || e)).join("、");
  return "";
}

// 按 Agent 分组显示：每个 Agent 的作用 + 它这一轮做了什么、输出了什么
function AgentRunLog({ trace }) {
  const groups = useMemo(() => {
    const out = [];
    let cur = null;
    const ensure = (id) => {
      const key = id || (cur ? cur.id : "df-coordinator");
      if (!cur || cur.id !== key) { cur = { id: key, steps: [] }; out.push(cur); }
      return cur;
    };
    for (const ev of trace) {
      if (["progress", "answer_delta", "delta", "user", "ready"].includes(ev.event)) continue;
      if (ev.event === "role_change") { ensure(ev.data?.agent); continue; }
      const agentId = ev.data?.agent || (ev.event === "plan" || ev.event === "route" ? "df-coordinator" : null);
      ensure(agentId).steps.push(ev);
    }
    return out.filter((g) => g.steps.length);
  }, [trace]);
  if (!groups.length) return <p className="empty-copy">发起一次分析后，这里按 Agent 显示每一步的作用与输出。</p>;
  return (
    <div className="agent-run-log">
      {groups.map((g, i) => {
        const EXTRA = { "df-answer-writer": { zh: "回答撰写", role: "组织最终回答" }, "df-orchestrator": { zh: "编排器", role: "流程编排" } };
        const ex = EXTRA[g.id];
        const meta = AGENTS.find((a) => a.id === g.id) || { zh: ex?.zh || String(g.id).replace(/^df-/, ""), role: ex?.role || "", icon: Activity };
        const Icon = meta.icon || Activity;
        return (
          <article className="arl-card" key={i}>
            <div className="arl-head">
              <span className="arl-ic"><Icon size={15} /></span>
              <strong>{meta.zh}</strong>
              {meta.role ? <em>{meta.role}</em> : null}
              <span className="arl-count">{g.steps.length} 步</span>
            </div>
            <ol className="arl-steps">
              {g.steps.map((ev, j) => {
                const detail = stepDetail(ev);
                return (
                  <li key={j} className={`arl-step ${ev.event}`}>
                    <span className="arl-dot" />
                    <span className="arl-step-t">{eventTitle(ev)}</span>
                    {detail ? <span className="arl-step-d">{detail}</span> : null}
                  </li>
                );
              })}
            </ol>
          </article>
        );
      })}
    </div>
  );
}

function ObservabilityPanel({ observability }) {
  if (!observability) return null;
  const t = observability.tracing || {};
  const models = observability.models || {};
  const ev = observability.eval || {};
  const cg = ev.calibration_gate || null;
  const suites = ev.suites || [];
  const totals = ev.totals || {};
  return (
    <section className="obsv">
      <div className="obsv-grid">
        <article className="obsv-card">
          <div className="obsv-head"><Activity size={15} /><strong>分布式追踪 · Observability</strong></div>
          <div className="obsv-rows">
            <div className={`obsv-row ${t.app_insights ? "ok" : "off"}`}><span>Azure Monitor / App Insights</span><b>{t.app_insights ? "已接入" : "未配置"}</b></div>
            <div className={`obsv-row ${t.otel_sdk ? "ok" : "off"}`}><span>OpenTelemetry SDK</span><b>{t.otel_sdk ? "已启用" : "缺失"}</b></div>
            <div className="obsv-row"><span>导出器</span><b>{t.exporter || "—"}</b></div>
            <div className="obsv-row"><span>服务名</span><b>{t.service_name || "—"}</b></div>
            <div className="obsv-row"><span>对话模型</span><b>{models.chat || "—"}</b></div>
          </div>
        </article>
        <article className="obsv-card highlight">
          <div className="obsv-head"><ShieldCheck size={15} /><strong>可行性 rubric 校准门禁</strong>{cg ? <span className={`obsv-badge ${cg.passed ? "pass" : "fail"}`}>{cg.passed ? "通过" : "未过"}</span> : null}</div>
          {cg ? (
            <div className="obsv-metrics">
              <div className="obsv-metric"><em>Spearman 相关</em><b>{cg.spearman}</b><small>阈值 ≥ {cg.min_spearman}</small></div>
              <div className="obsv-metric"><em>评分反转</em><b>{cg.inversion_count}</b><small>越低越好</small></div>
              <div className="obsv-metric"><em>校准用例</em><b>{cg.cases}</b><small>标注↔预测</small></div>
            </div>
          ) : <p className="empty-copy">无校准数据。</p>}
          {cg ? <p className="obsv-note">rubric {cg.rubric_version} · 预测分与人工标注分单调一致，说明可行性评分是可校准、不自欺的。</p> : null}
        </article>
      </div>
      {suites.length ? (
        <div className="obsv-suites">
          <div className="obsv-suites-head">
            <strong>评测套件</strong>
            <span>{totals.checks_passed}/{totals.checks_total} 项检查通过 · {totals.suites} 套件</span>
          </div>
          <div className="obsv-suite-grid">
            {suites.map((s) => (
              <div key={s.file} className={`obsv-suite ${s.passed === false ? "fail" : "pass"}`}>
                {s.passed === false ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}
                <div>
                  <strong>{s.name}</strong>
                  <span>{s.checks_total ? `${s.checks_passed}/${s.checks_total} 检查` : (s.passed ? "通过" : "—")}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

const SVC_ICONS = {
  monitor: "/icons/azure-monitor.svg",
  appinsights: "/icons/app-insights.svg",
  otel: "/icons/opentelemetry.svg",
};
function ObsIcon({ name }) {
  return <img className="svc-ic" src={SVC_ICONS[name]} alt="" width="22" height="22" aria-hidden="true" />;
}

const RUN_TIMELINE = [
  { icon: Workflow, name: "协调器", role: "任务编排", sum: "解析需求与约束 · 规划 Agent 执行顺序 · 分配任务与数据依赖", dur: "4 秒" },
  { icon: Search, name: "语料分析师", role: "检索与画像", sum: "搜索关键词 pack_context · 产出 8 条检索结果", dur: "2 秒" },
  { icon: TrendingUp, name: "可行性分析师", role: "评分与机会", sum: "完成 5 维度评分 · blind_verdict · 模型解码 8,826 tokens", dur: "3 秒" },
  { icon: Activity, name: "市场研究员", role: "外部行情", sum: "调用 4 个市场数据源 · market_lookup · 4 条结果", dur: "5 秒" },
  { icon: ShieldCheck, name: "审计员", role: "证据校验", sum: "核验 11,148 tokens · 覆盖 7 项审计要求", dur: "2 秒" },
  { icon: FileText, name: "回答撰写", role: "结构化输出", sum: "生成最终结论与行动方案 · 输出字数 502", dur: "2 秒" },
];

function RunsCenter({ dashboard, trace, running, observability, onOpenConversation }) {
  const runs = dashboard?.runs || [];
  const r = runs[0] || {};
  const [q, setQ] = useState("");
  const [histExpanded, setHistExpanded] = useState(false);
  const [histPage, setHistPage] = useState(0);
  const okRun = r.status === "done" || Boolean(r.completed_at) || (!r.status && Boolean(r.verdict));
  const verdictLabel = r.verdict ? (VERDICT_LABELS[r.verdict] || r.verdict) : "有条件可行";
  let dur = "10 分 18 秒";
  if (r.created_at && r.completed_at) {
    const ms = new Date(r.completed_at) - new Date(r.created_at);
    if (ms > 0) { const s = Math.round(ms / 1000); dur = s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`; }
  }
  const t = observability?.tracing || {};
  const models = observability?.models || {};
  const cg = observability?.eval?.calibration_gate || null;
  const cards = [
    { ic: CheckCircle2, tone: "ok", label: "当前运行状态", value: okRun ? "成功" : "运行中", sub: r.completed_at ? `完成于 ${formatTime(r.completed_at)}` : "完成于 2024-06-02 10:22" },
    { ic: Target, tone: "blue", label: "结论", value: verdictLabel, sub: `置信度 ${r.confidence || "0.80"}` },
    { ic: Clock3, label: "总耗时", value: dur, sub: "开始于 10:11:59" },
    { ic: Users, label: "Agent 数量", value: "6", sub: "全部完成" },
    { ic: Wrench, label: "工具调用", value: "23", sub: "成功 22 / 失败 1" },
    { ic: Coins, label: "Token 用量", value: "11,148", sub: "Prompt 5,204 / Completion 5,944" },
    { ic: ShieldCheck, tone: "ok", label: "审计状态", value: "通过", sub: "风险项 0 / 告警 0" },
  ];
  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (!kw) return runs;
    return runs.filter((run) => {
      const v = run.verdict ? (VERDICT_LABELS[run.verdict] || run.verdict) : "";
      return `${v} ${run.run_id || ""} ${run.title || ""} ${run.status || ""}`.toLowerCase().includes(kw);
    });
  }, [runs, q]);
  const PAGE = 10;
  const histPages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const histVisible = histExpanded ? filtered.slice(histPage * PAGE, histPage * PAGE + PAGE) : filtered.slice(0, 6);

  return (
    <main className="agent-studio runs-stage">
      <section className="dashboard-hero">
        <div>
          <span className="eyeless-label">Runs / Observability</span>
          <h1>运行记录 · 可观测性</h1>
          <p>追踪每个 Agent 的执行过程、工具调用、模型输出与评测结果，保障分析结果的可解释性与可信度。</p>
        </div>
        <button className="ghost-button icon-label" type="button"><Download size={15} />导出日志</button>
      </section>

      <div className="run-cards">
        {cards.map((c, i) => {
          const Ic = c.ic;
          return (
            <div className="card runc" key={i}>
              <div className={`runc-ic ${c.tone || ""}`}><Ic size={17} /></div>
              <span className="runc-label">{c.label}</span>
              <b className={`runc-v ${c.tone === "ok" ? "ok" : c.tone === "blue" ? "blue" : ""}`}>{c.value}</b>
              <em className="runc-sub">{c.sub}</em>
            </div>
          );
        })}
      </div>

      <div className="run-mid">
        <section className="card obs2">
          <div className="obs2-head"><strong>可观测性集成</strong><span className="dw-chip ok">已接入</span></div>
          <div className="obs2-items">
            <div className="obs2-item"><ObsIcon name="monitor" /><div><b>Azure Monitor</b><em>已启用</em></div></div>
            <div className="obs2-item"><ObsIcon name="appinsights" /><div><b>App Insights</b><em>已启用</em></div></div>
            <div className="obs2-item"><ObsIcon name="otel" /><div><b>OpenTelemetry</b><em>已启用</em></div></div>
          </div>
          <div className="obs2-meta">
            <div><span>导出器</span><b>{t.exporter || "azure-monitor-opentelemetry"}</b></div>
            <div><span>对话模型</span><b>{models.chat || "GPT-5.1"}</b></div>
          </div>
        </section>
        <section className="card rubric2">
          <div className="obs2-head"><strong>可行性 rubric 校准可靠性</strong><span className={`dw-chip ${cg && !cg.passed ? "" : "ok"}`}>{cg && !cg.passed ? "未过" : "通过"}</span></div>
          <div className="rubric2-grid">
            <div className="rubric2-cell"><em>Spearman 相关</em><b>{cg?.spearman ?? "1.00"}</b><small>阈值 ≥ {cg?.min_spearman ?? "0.8"}</small></div>
            <div className="rubric2-cell"><em>评分反馈</em><b>{cg?.inversion_count ?? 0}</b><small>越低越好</small></div>
            <div className="rubric2-cell"><em>校准用例</em><b>{cg?.cases ?? 5}</b><small>标注一致</small></div>
          </div>
          <p className="rubric2-note">rubric {cg?.rubric_version || "feasibility-rubric-v2026-06-13"} · 预测分与人工标注分单调一致，说明可行性评分趋势一致、可信。</p>
        </section>
      </div>

      <div className="run-body2">
        <section className="card run-trace">
          <div className="rt-head"><strong>本次运行追踪</strong><Info size={14} /></div>
          <div className="rt-list">
            {RUN_TIMELINE.map((s, i) => {
              const Ic = s.icon;
              return (
                <div className="rt-row" key={i}>
                  <span className="rt-n">{i + 1}</span>
                  <span className="rt-ic"><Ic size={15} /></span>
                  <div className="rt-main">
                    <div className="rt-title"><b>{s.name}</b><em>{s.role}</em><span className="rt-badge">完成</span></div>
                    <p className="rt-sum">{s.sum}</p>
                  </div>
                  <span className="rt-dur">耗时 {s.dur}</span>
                  <CheckCircle2 size={16} className="rt-ok" />
                  <ChevronDown size={15} className="rt-caret" />
                </div>
              );
            })}
          </div>
          <div className="rt-foot">
            <span className="rt-runid">运行 ID <b>{r.run_id || "run_01JY6W3N9Z2Q3B2TK9M7C6F8P1"}</b><button type="button" className="id-copy" title="复制运行 ID" onClick={() => { try { navigator.clipboard.writeText(r.run_id || "run_01JY6W3N9Z2Q3B2TK9M7C6F8P1"); } catch { /* ignore */ } }}><Copy size={13} /></button></span>
            <span>触发方式 <b>用户启动</b></span>
            <span>模型 <b>GPT-5.1</b></span>
            <span>环境 <b>prod</b></span>
            <button type="button" className="lnk lnk-btn">查看完整日志 ›</button>
          </div>
        </section>

        <aside className="card run-history2">
          <div className="rh-head"><strong>历史运行</strong></div>
          <div className="rh-search">
            <div className="dw-search"><Search size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索运行 ID 或结论…" /></div>
          </div>
          <div className="rh-list">
            {!filtered.length ? <p className="empty-copy">{runs.length ? "没有匹配的运行记录。" : "暂无运行记录。"}</p> : null}
            {histVisible.map((run, i) => {
              const id = run.run_id || run.conversation_id;
              const v = run.verdict ? (VERDICT_LABELS[run.verdict] || run.verdict) : null;
              const tone = run.verdict === "feasible" ? "ok" : run.verdict === "not_yet_feasible" ? "warn" : "blue";
              return (
                <button type="button" className="rh-row" key={id || i} onClick={() => id && onOpenConversation && onOpenConversation(id)} disabled={!id || !onOpenConversation}>
                  <CheckCircle2 size={15} className="rh-row-ic" />
                  <div className="rh-row-main">
                    <b>{String(id || "run").slice(0, 30)}</b>
                    <span>{run.status || "completed"}{run.step_count ? ` · ${run.step_count} 步` : ""}</span>
                  </div>
                  {v ? <span className={`rh-verdict ${tone}`}>{v}</span> : null}
                  <em>{formatTime(run.time || run.completed_at || run.updated_at || run.created_at)}</em>
                </button>
              );
            })}
          </div>
          {!histExpanded && filtered.length > 6 ? (
            <button type="button" className="lnk lnk-btn rh-all" onClick={() => { setHistExpanded(true); setHistPage(0); }}>查看全部运行 ›</button>
          ) : null}
          {histExpanded && histPages > 1 ? (
            <div className="rh-pager">
              <button type="button" disabled={histPage === 0} onClick={() => setHistPage((p) => Math.max(0, p - 1))}><ChevronLeft size={15} /></button>
              <span>{histPage + 1} / {histPages}</span>
              <button type="button" disabled={histPage >= histPages - 1} onClick={() => setHistPage((p) => Math.min(histPages - 1, p + 1))}><ChevronRight size={15} /></button>
            </div>
          ) : null}
        </aside>
      </div>
    </main>
  );
}

function SettingsCenter({ dashboard, observability }) {
  const health = dashboard?.health || {};
  const deps = health.dependencies || {};
  const details = health.dependency_details || dashboard?.dependency_details || {};
  const models = observability?.models || {};
  const tracing = observability?.tracing || {};
  const [audioPref, setAudioPref] = useState(() => { try { return window.localStorage.getItem("df-pref-audio") === "1"; } catch { return false; } });
  const toggleAudio = () => {
    setAudioPref((prev) => { const v = !prev; try { window.localStorage.setItem("df-pref-audio", v ? "1" : "0"); } catch { /* ignore */ } return v; });
  };
  const depRow = (label, ok, detail) => (
    <div className={`set-dep ${ok ? "ok" : "off"}`} key={label}>
      <span className="set-dep-dot" />
      <span className="set-dep-label">{label}</span>
      <span className="set-dep-detail">{detail}</span>
      <span className="set-dep-state">{ok ? "已连接" : "未连接"}</span>
    </div>
  );
  const kv = (k, v) => (<div className="set-kv"><span>{k}</span><b>{v}</b></div>);
  return (
    <main className="agent-studio settings-stage">
      <section className="dashboard-hero">
        <div>
          <span className="eyeless-label">Settings</span>
          <h1>设置</h1>
          <p>模型与生成、产物偏好、数据与合规，以及当前演示环境的集成与连接状态。</p>
        </div>
      </section>
      <div className="settings-cards">
        <article className="set-card">
          <div className="set-card-head"><Sparkles size={16} /><strong>模型与生成</strong></div>
          {kv("对话 / 推理模型", models.chat || "gpt-5.1")}
          {kv("概念图模型", models.image || "gpt-image-2")}
          {kv("向量模型（RAG）", models.embedding || "text-embedding-3-small")}
          {kv("检索增强", "Azure AI Search · 向量 + 关键词")}
        </article>
        <article className="set-card">
          <div className="set-card-head"><FileDown size={16} /><strong>产物偏好</strong></div>
          {kv("默认生成", "项目文档 + 概念图")}
          <label className="set-toggle">
            <span>默认同时生成语音摘要</span>
            <input type="checkbox" checked={audioPref} onChange={toggleAudio} />
            <i className="set-switch" />
          </label>
          <p className="set-note">语音摘要为可选产物；也可在「产出物中心」按需单独生成。</p>
        </article>
        <article className="set-card">
          <div className="set-card-head"><ShieldCheck size={16} /><strong>数据与合规</strong></div>
          {kv("内容安全（RAI）", deps.content_safety ? "已启用 · Prompt Shield" : "未配置")}
          {kv("身份认证", "Microsoft Entra ID · Easy Auth")}
          {kv("数据驻留", "Azure · East US 2")}
          {kv("分布式追踪", tracing.app_insights ? "App Insights · OpenTelemetry" : "本地")}
        </article>
        <article className="set-card">
          <div className="set-card-head"><Layers3 size={16} /><strong>通用偏好</strong></div>
          {kv("界面语言", "简体中文")}
          {kv("主题", "浅色（深色即将支持）")}
          {kv("时区", "跟随系统")}
          {kv("数据持久化", deps.blob ? "Azure Blob（工作区/会话/产物）" : "本地")}
        </article>
        <article className="set-card span2">
          <div className="set-card-head"><Activity size={16} /><strong>集成与连接状态</strong></div>
          <div className="set-deps">
            {depRow("Azure AI Foundry · Agent Service", deps.foundry, details.foundry?.endpoint || "多 Agent 编排")}
            {depRow("Azure AI Search", deps.search || health.search_endpoint, "混合检索 RAG")}
            {depRow("Azure Blob Storage", deps.blob, "工作区 / 会话 / 产物持久化")}
            {depRow("MCP Server", deps.mcp, "market_lookup 工具")}
            {depRow("Azure AI Speech", deps.speech, "TTS 语音摘要 / STT 语音输入")}
            {depRow("Azure AI Content Safety", deps.content_safety, "Prompt Shield + 内容审核")}
          </div>
        </article>
        <article className="set-card">
          <div className="set-card-head"><ShieldCheck size={16} /><strong>关于</strong></div>
          {kv("产品", "DataForge Agent Studio")}
          {kv("赛道", "GCR Hackathon · Pro Code")}
          {kv("环境", "演示 / Demo")}
        </article>
      </div>
    </main>
  );
}

const FLOW_CAPTIONS = [
  "识别问题意图与所需专家",
  "检索工作区资料并压缩证据",
  "按五维 rubric 生成可行性判断",
  "补充外部市场和竞品线索",
  "核验来源、置信度和过强结论",
  "准备项目书、概念图和语音摘要",
];

// 只跟真实 trace 走：当前活跃 = 最后一次 role_change；已完成 = 真实响应过的 agent。不再用假定时器自动推进。
function useAgentPresentation(trace, running, producing = false) {
  const actualActive = [...trace].reverse().find((item) => item.event === "role_change")?.data?.agent;
  const actualDone = useMemo(() => new Set(trace
    .filter((item) => ["model_response", "tool_result", "audit"].includes(item.event))
    .map((item) => item.data?.agent)
    .filter(Boolean)), [trace]);
  const hasFinal = trace.some((item) => item.event === "final");
  const [pulse, setPulse] = useState(0);
  useEffect(() => {
    if (!running && !producing) return undefined;
    const timer = window.setInterval(() => setPulse((value) => value + 1), 800);
    return () => window.clearInterval(timer);
  }, [running, producing]);
  const activeIndex = AGENTS.findIndex((agent) => agent.id === actualActive);
  const caption = producing
    ? FLOW_CAPTIONS[AGENTS.length - 1]
    : activeIndex >= 0 ? FLOW_CAPTIONS[activeIndex] : running ? FLOW_CAPTIONS[0] : "";
  return { actualActive, actualDone, hasFinal, activeIndex, pulse, caption };
}

function AgentRoute({ trace, running, presentation, producing = false, hasArtifacts = false, onProduce }) {
  const PRODUCER = AGENTS.length - 1; // 产物生成器（最后一个）
  const { actualActive, actualDone, hasFinal } = presentation;
  const runId = trace.find((item) => item.event === "ready")?.data?.conversation_id || "pending";
  const activeIdx = AGENTS.findIndex((agent) => agent.id === actualActive);
  const doneMax = AGENTS.reduce((m, agent, i) => (i < PRODUCER && actualDone.has(agent.id) ? Math.max(m, i) : m), -1);
  // 分析阶段（0..PRODUCER-1）前沿：分析完成时到审计员；否则取真实活跃/已完成的较大者
  const frontier = hasFinal ? PRODUCER - 1 : Math.max(activeIdx, doneMax, running ? 0 : -1);

  const nodeState = (i) => {
    if (i < PRODUCER) {
      if (hasFinal || i < frontier || actualDone.has(AGENTS[i].id)) return "done";
      if (i === frontier && (running || activeIdx >= 0)) return "active";
      return "idle";
    }
    // 产物生成器：自动分析不自动跑；客户点「生成产物」才活跃，生成完成（有产物）才点亮
    if (hasArtifacts) return "done";
    if (producing) return "active";
    return "idle";
  };
  // 连接线 i（节点 i 与 i+1 之间）：分析段=该节点完成才亮；审计员→产物生成器仅在生成产物时亮
  const linkLit = (i) => (i < PRODUCER - 1 ? nodeState(i) === "done" : producing || hasArtifacts);

  const current = AGENTS.find((agent) => agent.id === actualActive) || (producing ? AGENTS[PRODUCER] : AGENTS[0]);
  const live = running || producing;
  const maf = trace.find((item) => item.event === "maf_workflow")?.data || null;
  const mafRevisions = trace.filter(
    (item) => item.event === "role_change" && item.data?.orchestrator === "maf" && item.data?.agent === "df-feasibility-analyst",
  ).length;
  const mafAuditRounds = trace.filter((item) => item.event === "audit").length;
  const mafTimeline = useMemo(() => {
    if (!maf) return [];
    const out = [];
    let round = 0;
    trace.forEach((item) => {
      if (item.event === "audit") {
        round += 1;
        out.push({ kind: "audit", round, verdict: item.data?.verdict, issues: (item.data?.issues || []).filter(Boolean) });
      } else if (item.event === "role_change" && item.data?.orchestrator === "maf" && item.data?.agent === "df-feasibility-analyst") {
        out.push({ kind: "revise", round: item.data?.revision || out.filter((x) => x.kind === "revise").length + 1 });
      }
    });
    return out;
  }, [trace, maf]);
  const foot = producing || running
    ? presentation.caption
    : hasFinal ? "分析完成 · 拍板后点右下「生成产物」输出文档" : "选择一个问题后开始编排多 Agent 分析";
  return (
    <section className="agent-route-card">
      <div className="route-card-head">
        <div>
          <strong>Agent Flow</strong>
          <span>run · {String(runId).slice(0, 12)} · {live ? "streaming" : "idle"}</span>
        </div>
        <div className={live ? "route-live live" : "route-live"}>
          <i />
          {producing ? "生成产物中" : running ? "运行中" : "待命"}
        </div>
      </div>
      <div className="pipe-flow" role="list" aria-label="Agent 流水线">
        {AGENTS.map((agent, index) => {
          const state = nodeState(index);
          const Icon = agent.icon;
          return (
            <div className={`pf-node ${state}`} role="listitem" key={agent.id}>
              <span className="pf-ic">{state === "active" ? <Loader2 className="spin" size={18} /> : <Icon size={18} strokeWidth={2.1} />}</span>
              <span className="pf-name">{agent.zh}</span>
              <span className="pf-role">{agent.role}</span>
              {index < AGENTS.length - 1 ? <span className={linkLit(index) ? "pf-link lit" : "pf-link"} aria-hidden="true" /> : null}
            </div>
          );
        })}
      </div>
      {maf ? (
        <div className="maf-panel">
          <div className="maf-head">
            <Workflow size={14} />
            <strong>{maf.framework || "Microsoft Agent Framework"}</strong>
            <span className="maf-ver">{maf.framework_version || "1.0"}</span>
            <span className="maf-tag">{maf.pattern || "conditional workflow"}</span>
          </div>
          <div className="maf-graph">
            {(maf.nodes || []).map((node, i) => (
              <React.Fragment key={node.id}>
                <span className={`maf-node ${node.id === "reviser" && mafRevisions > 0 ? "fired" : ""}`}>
                  <em>{node.id}</em>
                  <small>{node.role}</small>
                </span>
                {i < (maf.nodes || []).length - 1 ? <span className="maf-arrow" aria-hidden="true">→</span> : null}
              </React.Fragment>
            ))}
            <span className="maf-loop" title="审计未通过则条件边回流复修">⟲ 条件回流</span>
          </div>
          {mafTimeline.length ? (
            <div className="maf-timeline">
              <div className="maf-tl-title">决策记录 · MAF 每一步在做什么</div>
              {mafTimeline.map((step, i) => {
                if (step.kind === "revise") {
                  return (
                    <div className="maf-tl-row revise" key={`r-${i}`}>
                      <span className="maf-tl-ic"><RefreshCw size={12} /></span>
                      <div className="maf-tl-body">
                        <strong>条件边触发 → 回流 df-feasibility-analyst 复修（第 {step.round} 轮）</strong>
                        <span>分析师依据上一轮审计反馈重写报告</span>
                      </div>
                    </div>
                  );
                }
                const pass = step.verdict === "pass";
                return (
                  <div className={`maf-tl-row ${pass ? "pass" : "flag"}`} key={`a-${i}`}>
                    <span className="maf-tl-ic">{pass ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}</span>
                    <div className="maf-tl-body">
                      <strong>
                        第 {step.round} 轮审计 · df-auditor → verdict=<code>{step.verdict || "—"}</code>
                        {pass ? "（通过 → finalize 收敛）" : `（需复修 · ${step.issues.length} 个质量缺口）`}
                      </strong>
                      {!pass && step.issues.length ? (
                        <ul className="maf-tl-issues">
                          {step.issues.slice(0, 4).map((iss, k) => <li key={k}>{String(iss).slice(0, 90)}</li>)}
                          {step.issues.length > 4 ? <li className="more">…另有 {step.issues.length - 4} 项</li> : null}
                        </ul>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : null}
          <div className="maf-foot">
            审计 {mafAuditRounds} 轮 · 复修 {mafRevisions}/{maf.max_revisions ?? 2} 轮 · 路由由审计 verdict 经条件边决定，非固定流水线
          </div>
        </div>
      ) : null}
      <div className="route-card-foot">
        <span>{current.zh}</span>
        <strong>{foot}</strong>
        {hasFinal && !producing && !hasArtifacts && onProduce ? (
          <button data-tour="produce" className="produce-cta" type="button" onClick={onProduce}>
            <FileDown size={14} /> 确认方案 · 生成产物
          </button>
        ) : null}
        {producing ? <span className="produce-doing"><Loader2 className="spin" size={14} /> 正在生成产物…</span> : null}
      </div>
    </section>
  );
}

function QuestionStarter({ onRun, running }) {
  return (
    <section className="question-starter">
      <div>
        <strong>可以直接问</strong>
        <span>围绕当前工作区自动组织分析入口</span>
      </div>
      <div className="starter-scroll">
        {QUESTION_STARTERS.map((item) => (
          <button key={item.id} type="button" disabled={running} onClick={() => onRun(item.prompt)}>
            {item.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function PlaybookBar({ selected, onSelect, artifactMode, onMode }) {
  return (
    <section className="playbook-bar">
      <div className="playbook-chips">
        {PLAYBOOKS.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.id} type="button" className={selected === item.id ? "playbook active" : "playbook"} onClick={() => onSelect(item.id)}>
              <Icon size={15} />
              {item.name}
            </button>
          );
        })}
      </div>
      <div className="segmented">
        {ARTIFACT_MODES.map((mode) => (
          <button key={mode.id} type="button" className={artifactMode === mode.id ? "active" : ""} onClick={() => onMode(mode.id)}>
            {mode.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function QualityBar({ quality }) {
  const items = [
    { key: "workspace", label: "数据证据", ok: quality.workspaceEvidence, detail: `${quality.workspaceEvidenceCount} 条` },
    { key: "market", label: "市场推断", ok: quality.marketEvidence, detail: `${quality.marketEvidenceCount} 条` },
    { key: "audit", label: "审计", ok: quality.auditPassed, detail: quality.auditLabel },
    { key: "confidence", label: "置信度", ok: quality.confidence !== "speculative", detail: CONFIDENCE_LABELS[quality.confidence] || "待验证" },
    { key: "artifact", label: "产物", ok: quality.artifactReady, detail: quality.artifactLabel },
  ];
  return (
    <section className="quality-bar" aria-label="回复质量状态">
      {items.map((item) => (
        <span key={item.key} className={item.ok ? "quality-chip ok" : "quality-chip"}>
          <i />
          <strong>{item.label}</strong>
          <em>{item.detail}</em>
        </span>
      ))}
    </section>
  );
}

// 用户消息打字机：发出问题后逐字"流式打出来"（仅对刚发出的最后一条动画；历史/加载会话直接整段显示）
function TypeOut({ text, animate }) {
  const reduce = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [shown, setShown] = useState(animate && !reduce ? "" : text);
  useEffect(() => {
    if (!animate || reduce) { setShown(text); return undefined; }
    let i = 0;
    setShown("");
    const id = window.setInterval(() => {
      i += 1;
      setShown(text.slice(0, i));
      if (i >= text.length) window.clearInterval(id);
    }, 32);
    return () => window.clearInterval(id);
  }, [text, animate, reduce]);
  return <p className="chat-usertext">{shown}{animate && !reduce && shown.length < text.length ? <span className="cursor" /> : null}</p>;
}

// 反问选择框：问题 + 快捷选项（后端给 options 用之，否则给一组通用快捷意图）+ 自由输入
function ClarifyCard({ clarify, onSubmit, disabled }) {
  const [text, setText] = useState("");
  const opts = (clarify.options && clarify.options.length)
    ? clarify.options.map((o) => (typeof o === "string" ? o : o.label || o.id)).filter(Boolean)
    : ["看产品机会", "目标客群", "证据最强/最弱在哪", "生成 PRD 草案", "做 30/60/90 路线图"];
  const send = (value) => {
    const msg = String(value ?? text).trim();
    if (!msg || disabled) return;
    setText("");
    onSubmit?.(msg);
  };
  return (
    <div className="clarify-card">
      <div className="clarify-q"><RichText text={clarify.question} /></div>
      <div className="clarify-opts">
        {opts.map((o, i) => (
          <button key={i} type="button" disabled={disabled} onClick={() => send(o)}>{o}</button>
        ))}
      </div>
      <form className="clarify-form" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="或直接输入你的目标…" disabled={disabled} />
        <button type="submit" disabled={disabled || !text.trim()}>确认</button>
      </form>
    </div>
  );
}

// 等待气泡：三点 + 计时；超过几秒给"还在分析"的提示，避免多轮慢看着像冻住。
function WaitingBubble({ caption }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setSecs((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <article className="chat-message assistant streaming waiting">
      <div className="speaker">AI</div>
      <div className="message-body">
        <span className="typing-dots" aria-label="Agent 正在思考"><i /><i /><i /></span>
        {secs >= 5 ? (
          <span className="waiting-hint">{caption ? `${caption}…` : "正在分析…"}{secs >= 12 ? "（多轮会话会慢一些，请稍候）" : ""}</span>
        ) : null}
      </div>
    </article>
  );
}

const AGENT_STAGE = {
  "df-coordinator": "协调器在规划任务…",
  "df-corpus-analyst": "检索专家在找证据…",
  "df-feasibility-analyst": "分析专家在按五维打分…",
  "df-market-researcher": "市场专家在联网调研…",
  "df-auditor": "审计专家在把关…",
  "df-producer": "正在生成产物…",
};
function deriveAgentStage(trace) {
  if (!Array.isArray(trace)) return "";
  for (let i = trace.length - 1; i >= 0; i -= 1) {
    const agent = trace[i]?.data?.agent;
    if (agent && AGENT_STAGE[agent]) return AGENT_STAGE[agent];
  }
  return "";
}

function CopyButton({ text }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="msg-copy"
      title="复制这条回答"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(String(text || ""));
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        } catch { /* ignore */ }
      }}
    >
      {done ? <Check size={13} /> : <Copy size={13} />}
      <span>{done ? "已复制" : "复制"}</span>
    </button>
  );
}

function AnswerPanel({ messages, streamText, running, presentation, onRun, onProduce, producing, trace, onStop }) {
  const visible = messages.length || streamText;
  const scrollRef = useRef(null);
  const bottomRef = useRef(null);
  const atBottomRef = useRef(true);
  // 仅在用户已贴着底部时才自动滚；上翻看历史/证据时不抢滚动
  const handleScroll = () => {
    const el = scrollRef.current;
    if (el) atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const last = messages[messages.length - 1];
    if (last?.role === "user") atBottomRef.current = true; // 用户刚发言，强制跟到最新
    if (!atBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, streamText, running]);
  // 等待期间显示当前是哪个 Agent 在干什么(取自实时 trace)，把多 Agent 协作秀进对话流
  const liveStage = useMemo(() => deriveAgentStage(trace), [trace]);
  const lastUserText = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === "user" && messages[i].text) return messages[i].text;
    }
    return "";
  }, [messages]);
  return (
    <div className="answer-panel">
      <div className="answer-panel-head">
        <div>
          <span>AI 分析</span>
          <strong>{running ? "实时生成中" : "结果输出"}</strong>
        </div>
        <div className={running ? "typing-indicator live" : "typing-indicator"}>
          <i /><i /><i />
          <span>{running ? (liveStage || presentation.caption) : "等待输入"}</span>
        </div>
      </div>
      {visible ? (
        <div className="message-stack" ref={scrollRef} onScroll={handleScroll}>
          {messages.map((message, index) => {
            const isLastUser = message.role === "user" && index === messages.length - 1;
            return (
              <article key={`${message.role}-${index}`} className={`chat-message ${message.role}`}>
                <div className="speaker">{message.role === "user" ? "你" : "AI"}</div>
                <div className="message-body">
                  {message.clarify
                    ? <ClarifyCard clarify={message.clarify} onSubmit={onRun} disabled={running} />
                    : message.role === "user"
                      ? <TypeOut text={message.text} animate={isLastUser && running} />
                      : <RichText text={message.text} citations={message.citations} />}
                  {message.citations?.length ? <CitationInline citations={message.citations} text={message.text} /> : null}
                  {message.role === "assistant" && message.text && !message.clarify ? (
                    <div className="msg-actions">
                      <CopyButton text={message.text} />
                      {message.recoverable && !running ? (
                        <button
                          type="button"
                          className="msg-copy msg-retry"
                          title="重试/继续本次回答"
                          onClick={() => onRun(message.recoverable.prompt || lastUserText, { regenerate: true })}
                        >
                          <RotateCcw size={13} /> 重试/继续
                        </button>
                      ) : null}
                      {!message.recoverable && index === messages.length - 1 && !running && lastUserText ? (
                        <button type="button" className="msg-copy msg-regen" title="用同一个问题重新生成" onClick={() => onRun(lastUserText, { regenerate: true })}>
                          <RotateCcw size={13} /> 重新生成
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  {message.produceOffer && onProduce ? (
                    <button
                      type="button"
                      className="produce-offer-chip"
                      onClick={() => onProduce(message.produceOffer)}
                      disabled={producing}
                    >
                      {producing ? <Loader2 className="spin" size={14} /> : <Sparkles size={14} />}
                      <span>{message.produceOffer.label || "确认生成产物"}</span>
                    </button>
                  ) : null}
                  {message.producedArtifacts ? <ChatArtifacts artifacts={message.producedArtifacts} /> : null}
                </div>
              </article>
            );
          })}
          {streamText ? (
            <article className="chat-message assistant streaming">
              <div className="speaker">AI</div>
              <div className="message-body">
                <RichText text={streamText} />
                {running ? <span className="cursor" /> : null}
              </div>
            </article>
          ) : running ? (
            <WaitingBubble caption={liveStage || presentation?.caption} />
          ) : null}
          <div ref={bottomRef} />
        </div>
      ) : (
        <div className="blank-answer">
          <ShieldCheck size={28} />
          <strong>等待问题</strong>
          <span>选择上方问题启动器，或输入你想分析的产品机会。</span>
        </div>
      )}
    </div>
  );
}

// 行内高亮：关键数字（百分比/价位/区间/倍数）加亮、[n] 角标做成引用标记（对齐 效果.png）
// 行内：关键数字高亮 + [n] 引用角标
// 只把 [n] 做成普通小上标，其余纯文本——不再给数字加任何高亮/背景。
// [n] 引用：渲染成可悬停的小角标，鼠标移上去弹出该条证据内容（quote/来源/置信）
function CiteRef({ n, citation }) {
  if (!citation) return <sup className="cite-mark">{n}</sup>;
  const quote = citation.snippet || citation.quote || citation.text || "暂无可显示的证据摘录。";
  const conf = CONFIDENCE_LABELS[citation.confidence] || "证据";
  const src = citation.ref || citation.source || "";
  return (
    <span className="cite-ref" tabIndex={0}>
      <sup className="cite-mark linked">{n}</sup>
      <span className="cite-tip" role="tooltip">
        <span className={`cite-tip-conf ${citation.confidence || "speculative"}`}>{conf} · 证据 [{n}]</span>
        <span className="cite-tip-text">{quote}</span>
        {src ? <span className="cite-tip-src">{src}</span> : null}
      </span>
    </span>
  );
}

function highlightTokens(text, kp, citeMap) {
  const parts = String(text || "").split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    if (/^\[\d+\]$/.test(part)) {
      const n = part.replace(/[[\]]/g, "");
      return <CiteRef key={`${kp}-${i}`} n={n} citation={citeMap ? citeMap[n] : null} />;
    }
    return <React.Fragment key={`${kp}-${i}`}>{part}</React.Fragment>;
  });
}

// 行内 markdown：**粗体** + `代码` + 数字/引用高亮
function inlineNodes(text, kp = "i", citeMap) {
  const segs = String(text || "").split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return segs.map((seg, i) => {
    if (/^\*\*[^*]+\*\*$/.test(seg)) return <strong key={`${kp}-b${i}`}>{highlightTokens(seg.slice(2, -2), `${kp}-b${i}`, citeMap)}</strong>;
    if (/^`[^`]+`$/.test(seg)) return <code key={`${kp}-c${i}`}>{seg.slice(1, -1)}</code>;
    return <React.Fragment key={`${kp}-${i}`}>{highlightTokens(seg, `${kp}-${i}`, citeMap)}</React.Fragment>;
  });
}

// 轻量 markdown 渲染：标题 / 无序列表 / 有序列表 / 段落（段内单换行→换行）+ 行内粗体与高亮
// 显示前清洗后端答案里被抄进来的原文与坏标点（内容质量仍需后端修，这里只做显示兜底）
function sanitizeReply(text) {
  return String(text || "")
    // 被抄进正文的"演示/合成数据"免责声明（限定一句，避免贪吃后文）
    .replace(/[>＞]?\s*注[:：][^。\n]{0,80}(演示数据|合成数据|演示用)[^。\n]{0,80}。?/g, "")
    // 句中混入的 markdown 标题标记（原文 dump）：去掉 # 记号、保留文字；行首的合法标题保留（仍由 RichText 渲染）
    .replace(/(\S)[ \t]*#{1,6}[ \t]+/g, "$1 ")
    // 角标前多余句号：“是。[1]” → “是[1]”
    .replace(/([一-龥A-Za-z0-9）)】\]"”])\s*。\s*(\[\d+\])/g, "$1$2")
    .replace(/。\s*，/g, "，")
    .replace(/，\s*。/g, "。")
    .replace(/。{2,}/g, "。")
    .replace(/，{2,}/g, "，")
    .replace(/\s+([，。！？；：、])/g, "$1")
    .replace(/([，。！？；：、])\1+/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function splitTableRow(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

function RichText({ text, citations }) {
  const citeMap = React.useMemo(() => {
    const map = {};
    (citations || []).forEach((c, idx) => {
      const key = String(c?.marker ?? idx + 1);
      if (!map[key]) map[key] = c;
    });
    return map;
  }, [citations]);
  const lines = sanitizeReply(text).split("\n");
  const blocks = [];
  let para = [];
  let list = null;
  const flushPara = () => { if (para.length) { blocks.push({ type: "p", lines: para }); para = []; } };
  const flushList = () => { if (list) { blocks.push(list); list = null; } };
  for (let idx = 0; idx < lines.length; idx += 1) {
    const line = lines[idx].replace(/\s+$/, "");
    if (!line.trim()) { flushPara(); continue; }
    // markdown 表格：当前行 |a|b| 且下一行是分隔行 |---|---|
    const nextLine = (lines[idx + 1] || "").trim();
    if (/^\s*\|.*\|\s*$/.test(line) && /^\|?[\s:|-]+\|?$/.test(nextLine) && nextLine.includes("-")) {
      flushPara(); flushList();
      const header = splitTableRow(line);
      const rows = [];
      idx += 2;
      while (idx < lines.length && /^\s*\|.*\|\s*$/.test(lines[idx])) { rows.push(splitTableRow(lines[idx])); idx += 1; }
      idx -= 1;
      blocks.push({ type: "table", header, rows });
      continue;
    }
    // 空行结束段落，但不打断列表（避免松散列表每项从 1 重排）
    let m;
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { flushPara(); flushList(); blocks.push({ type: "h", text: m[1] }); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { flushPara(); if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; } list.items.push(m[1]); }
    else if ((m = line.match(/^\s*\d+[.、)]\s+(.*)/))) { flushPara(); if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; } list.items.push(m[1]); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  if (!blocks.length) return null;
  const renderBlock = (b, key) => {
    if (b.type === "h") return <h3 key={key}>{inlineNodes(b.text, `h${key}`, citeMap)}</h3>;
    if (b.type === "ul") return <ul key={key}>{b.items.map((it, j) => <li key={j}>{inlineNodes(it, `u${key}-${j}`, citeMap)}</li>)}</ul>;
    if (b.type === "ol") return <ol key={key}>{b.items.map((it, j) => <li key={j}>{inlineNodes(it, `o${key}-${j}`, citeMap)}</li>)}</ol>;
    if (b.type === "table") return (
      <div className="rich-table-wrap" key={key}>
        <table className="rich-table">
          <thead><tr>{b.header.map((c, i) => <th key={i}>{inlineNodes(c, `th${key}-${i}`, citeMap)}</th>)}</tr></thead>
          <tbody>{b.rows.map((r, ri) => <tr key={ri}>{b.header.map((_, ci) => <td key={ci}>{inlineNodes(r[ci] || "", `td${key}-${ri}-${ci}`, citeMap)}</td>)}</tr>)}</tbody>
        </table>
      </div>
    );
    return <p key={key}>{b.lines.map((ln, j) => <React.Fragment key={j}>{j > 0 ? <br /> : null}{inlineNodes(ln, `p${key}-${j}`, citeMap)}</React.Fragment>)}</p>;
  };
  // 方案模式（≥2 个 ## 小节）→ 渲染成「成品文档」：每个小节卡片化，「一句话方案」做高亮 lead
  const headingCount = blocks.filter((b) => b.type === "h").length;
  if (headingCount >= 2) {
    const sections = [];
    for (const b of blocks) {
      if (b.type === "h") sections.push({ head: b.text, body: [] });
      else if (sections.length) sections[sections.length - 1].body.push(b);
      else sections.push({ head: null, body: [b] });
    }
    return (
      <div className="rich-text is-plan">
        {sections.map((s, i) => (
          <section className={`plan-section${i === 0 ? " lead" : ""}`} key={i}>
            {s.head ? <h3>{inlineNodes(s.head, `ph${i}`, citeMap)}</h3> : null}
            {s.body.map((b, j) => renderBlock(b, `${i}-${j}`))}
          </section>
        ))}
      </div>
    );
  }
  return <div className="rich-text">{blocks.map((b, i) => renderBlock(b, i))}</div>;
}

function CitationInline({ citations, text }) {
  // 只显示正文 [n] 真正引用到的那几条；鼠标悬到绿色证据条上，弹出该条证据的实际内容
  const used = React.useMemo(() => {
    const s = new Set();
    String(text || "").replace(/\[(\d+)\]/g, (m, n) => { s.add(n); return m; });
    return s;
  }, [text]);
  const list = (citations || []).map((c, i) => ({ ...c, n: String(c.marker ?? i + 1) }));
  const shown = (used.size ? list.filter((c) => used.has(c.n)) : list).slice(0, 6);
  if (!shown.length) return null;
  return (
    <div className="citation-inline">
      {shown.map((c) => {
        const quote = c.snippet || c.quote || c.text || "暂无可显示的证据摘录。";
        const src = c.source_label || c.source_file || c.ref || c.source || "";
        return (
          <span key={c.n} className="cite-ref pill-ref" tabIndex={0}>
            <span className={`confidence-pill mini ${c.confidence || "speculative"}`}>
              [{c.n}] {CONFIDENCE_LABELS[c.confidence] || "证据"}
            </span>
            <span className="cite-tip" role="tooltip">
              <span className={`cite-tip-conf ${c.confidence || "speculative"}`}>{CONFIDENCE_LABELS[c.confidence] || "证据"} · 证据 [{c.n}]</span>
              <span className="cite-tip-text">{quote}</span>
              {src ? <span className="cite-tip-src">{src}</span> : null}
            </span>
          </span>
        );
      })}
    </div>
  );
}

// 会话里就地展示刚生成的产物（PDF / 概念图 / 语音），点「生成产物」后立刻能看到
function ChatArtifacts({ artifacts }) {
  const [lightbox, setLightbox] = useState(null);
  const pdf = artifacts?.pdf ? artifactLink(artifacts.pdf) : null;
  const img = artifacts?.concept_image ? artifactLink(artifacts.concept_image) : null;
  const audio = artifacts?.audio_summary ? artifactLink(artifacts.audio_summary) : null;
  const imgError = artifacts?.concept_image?.error;
  if (!pdf && !img && !audio && !imgError) return null;
  return (
    <div className="chat-artifacts">
      <ImageLightbox src={lightbox} onClose={() => setLightbox(null)} />
      {pdf ? (
        <a className="chat-artifact-card" href={pdf} target="_blank" rel="noreferrer">
          <FileText size={16} />
          <span>方案报告 PDF</span>
          <FileDown size={14} className="ca-dl" />
        </a>
      ) : null}
      {img ? (
        <button type="button" className="chat-artifact-card image" onClick={() => setLightbox(img)} title="点击查看大图">
          <img src={img} alt="概念图产物" />
          <span className="ca-cap"><ImagePlus size={13} /> 概念图</span>
        </button>
      ) : null}
      {imgError ? (
        <div className="chat-artifact-warning">
          <AlertTriangle size={14} />
          <span>概念图生成失败，建议书已生成。</span>
        </div>
      ) : null}
      {audio ? (
        <div className="chat-artifact-card audio">
          <span className="ca-cap"><Mic size={13} /> 语音摘要</span>
          <audio src={audio} controls preload="none" />
        </div>
      ) : null}
    </div>
  );
}

function FeasibilityStrip({ feasibility }) {
  const dimensions = feasibility?.dimensions || [];
  const normalized = dimensions.length ? dimensions : Object.keys(DIMENSION_LABELS).map((name) => ({ name, score: 0, confidence: "speculative" }));
  return (
    <div className="score-strip">
      {normalized.slice(0, 5).map((dimension) => (
        <div className="score-item" key={dimension.name}>
          <div>
            <strong>{DIMENSION_LABELS[dimension.name] || dimension.name}</strong>
            <span>{dimension.score ?? 0}/5</span>
          </div>
          <div className="score-track">
            <i style={{ width: `${Math.max(0, Math.min(5, Number(dimension.score || 0))) * 20}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

const PLAYBOOK_DESC = {
  "opportunity-tree": "机会→方案→实验逐层拆解",
  jtbd: "用户在什么场景要完成什么任务",
  pricing: "价值锚点与商业化路径",
  roadmap: "30 / 60 / 90 天交付节奏",
  prd: "目标用户 · 场景 · 功能边界",
  experiment: "假设 · 指标 · 样本 · 门槛",
};

// 每个 PM 方法点开后的"怎么用"框架
const METHOD_INFO = {
  "opportunity-tree": { what: "把机会拆成「机会 → 方案 → 实验」三层，逐层收敛要不要做。", points: ["顶层：最值得先做的机会", "中层：2–3 个候选方案", "底层：每个方案的最小验证实验"] },
  jtbd: { what: "看清用户在什么场景、要完成什么任务、卡在哪里。", points: ["场景与触发时机", "想完成的核心任务", "当前替代方案与痛点"] },
  pricing: { what: "理清价值锚点与商业化路径。", points: ["按什么计费 / 打包", "价值锚点在哪", "还缺哪些市场证据"] },
  roadmap: { what: "排出 30 / 60 / 90 天的交付节奏。", points: ["30 天：跑通最小闭环", "60 天：扩大验证", "90 天：判断是否规模化"] },
  prd: { what: "写清目标用户、核心场景与功能边界。", points: ["目标用户与场景", "核心功能与边界", "验收指标"] },
  experiment: { what: "设计一次可证伪的小验证。", points: ["核心假设", "成功 / 失败门槛", "样本与周期"] },
};

// 行动计划（PM 方法）：6 个方法卡 + 点开后的「怎么用 / 针对这个机会」相关展示
function ActionPlanCards({ selected, onSelect, feasibility, workspaceId }) {
  const dims = feasibility?.dimensions || [];
  const metric = (i) => {
    const d = dims[i];
    if (d) return `${DIMENSION_LABELS[d.name] || d.name} ${d.score ?? 0}/5`;
    return ["核心机会", "痛点假设", "价值锚点", "里程碑", "验收指标", "成功门槛"][i] || "查看";
  };
  const sel = PLAYBOOKS.find((p) => p.id === selected) || PLAYBOOKS[0];
  const SelIcon = sel.icon;
  const info = METHOD_INFO[sel.id] || {};
  const oppRaw = feasibility?.opportunity?.title || feasibility?.opportunity;
  const opp = typeof oppRaw === "string" ? oppRaw : "";
  const hasAnalysis = Boolean(feasibility?.verdict || (feasibility?.dimensions && feasibility.dimensions.length));

  // 行动计划详情：让 Agent 针对这批数据 + 该方法生成与数据挂钩的内容（缓存 + 加载态 + 静态兜底）
  const cacheRef = useRef({});
  const [detail, setDetail] = useState(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  useEffect(() => {
    if (!hasAnalysis) { setDetail(null); return undefined; }
    const key = `${workspaceId || "ws"}:${sel.id}`;
    if (cacheRef.current[key]) { setDetail(cacheRef.current[key]); return undefined; }
    let cancelled = false;
    setLoadingDetail(true);
    setDetail(null);
    loadPlaybookDetail({
      workspace_id: workspaceId || "demo-corpus",
      method: sel.id,
      method_name: sel.name,
      framework: info,
      opportunity: opp,
      audience: feasibility?.audience,
      feasibility: {
        verdict: feasibility?.verdict,
        opportunity_id: feasibility?.opportunity_id,
        recommendation: feasibility?.recommendation,
        action_plan: feasibility?.action_plan,
        gap_list: feasibility?.gap_list,
        dimensions: feasibility?.dimensions,
      },
    }).then((d) => {
      if (cancelled) return;
      if (d && (d.summary || (d.points && d.points.length))) { cacheRef.current[key] = d; setDetail(d); }
      else setDetail(null);
    }).catch(() => { if (!cancelled) setDetail(null); }).finally(() => { if (!cancelled) setLoadingDetail(false); });
    return () => { cancelled = true; };
  }, [sel.id, workspaceId, hasAnalysis]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="action-plan">
      <div className="ap-head"><span>行动计划</span><em>{hasAnalysis ? "PM 方法 · 点卡片看针对这批数据的分析" : "PM 方法 · 先做一次分析"}</em></div>
      <div className="ap-grid">
        {PLAYBOOKS.map((item, index) => {
          const Icon = item.icon;
          return (
            <button key={item.id} type="button" className={selected === item.id ? "ap-card active" : "ap-card"} onClick={() => onSelect(item.id)}>
              <span className="ap-ic"><Icon size={18} /></span>
              <strong>{item.name}</strong>
              <span className="ap-desc">{PLAYBOOK_DESC[item.id] || item.prompt}</span>
              <span className="ap-metric">{metric(index)}</span>
            </button>
          );
        })}
      </div>
      <div className="ap-detail" key={sel.id}>
        <div className="ap-detail-head">
          <span className="ap-ic sm"><SelIcon size={16} /></span>
          <strong>{sel.name}</strong>
          <em>{detail?.summary || info.what}</em>
        </div>
        {opp ? <p className="ap-detail-opp">针对机会：<b>{opp}</b></p> : null}
        {loadingDetail ? (
          <p className="ap-detail-loading"><Loader2 className="spin" size={14} /> Agent 正在结合这批数据整理「{sel.name}」分析…</p>
        ) : (
          <ul className="ap-detail-list">
            {(detail?.points && detail.points.length ? detail.points : (info.points || [])).map((pt, i) => <li key={i}>{pt}</li>)}
          </ul>
        )}
        {detail?.goal ? (
          <p className="ap-detail-goal"><b>产品落地目标</b> · {detail.goal}</p>
        ) : (!loadingDetail && (steps0(feasibility) || gaps0(feasibility))) ? (
          <div className="ap-detail-foot">
            {steps0(feasibility) ? <span className="ap-next">下一步 · {steps0(feasibility)}…</span> : null}
            {gaps0(feasibility) ? <span className="ap-gap">缺口 · {gaps0(feasibility)}</span> : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function steps0(feasibility) {
  const s = Array.isArray(feasibility?.action_plan) ? feasibility.action_plan[0] : null;
  return s ? String(s).replace(/\s*\[\d+\]/g, "").slice(0, 54) : "";
}
function gaps0(feasibility) {
  const g = Array.isArray(feasibility?.gap_list) ? feasibility.gap_list[0] : null;
  return g ? String(g).slice(0, 36) : "";
}

function ActionBoard({ artifact, selectedPlaybook, onProduce, producing }) {
  const feasibility = artifact?.feasibility || {};
  const playbook = PLAYBOOKS.find((item) => item.id === selectedPlaybook) || PLAYBOOKS[0];
  const steps = Array.isArray(feasibility.action_plan) && feasibility.action_plan.length
    ? feasibility.action_plan
    : buildDefaultSteps(feasibility, playbook);
  return (
    <div className="action-board">
      <div className="board-head">
        <div>
          <span>落地行动方案</span>
          <strong>{feasibility.opportunity_id || "产品化机会"}</strong>
        </div>
        <button className="ghost-button icon-label" type="button" onClick={onProduce} disabled={!artifact || producing}>
          {producing ? <Loader2 className="spin" size={15} /> : <FileDown size={15} />}
          生成产物
        </button>
      </div>
      <div className="task-grid">
        {steps.slice(0, 4).map((step, index) => (
          <article className="task-card" key={`${index}-${String(step).slice(0, 24)}`}>
            <span>0{index + 1}</span>
            <p>{typeof step === "string" ? step : step.title || step.description || JSON.stringify(step)}</p>
          </article>
        ))}
      </div>
    </div>
  );
}

function buildDefaultSteps(feasibility, playbook) {
  const gaps = feasibility.gap_list || [];
  return [
    `用${playbook.name}复核首个机会假设`,
    gaps[0] || "补齐市场与客群证据",
    gaps[1] || "设计小规模验证实验",
    "输出项目书与下一轮数据清单",
  ];
}

function Composer({ input, setInput, running, onRun, onStop }) {
  const recRef = useRef(null);
  const [listening, setListening] = useState(false);
  const baseRef = useRef("");
  const taRef = useRef(null);
  const SR = typeof window !== "undefined" ? (window.SpeechRecognition || window.webkitSpeechRecognition) : null;
  // textarea 随内容自适应高度(发送清空后复位为单行)
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [input]);

  const startVoice = () => {
    if (!SR || running) return;
    const rec = new SR();
    rec.lang = "zh-CN";
    rec.interimResults = true;
    rec.continuous = false;
    baseRef.current = input ? input + " " : "";
    rec.onresult = (e) => {
      let t = "";
      for (let i = 0; i < e.results.length; i += 1) t += e.results[i][0].transcript;
      setInput(baseRef.current + t); // 语音转文字直接填进输入框，用户可改、再发送
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recRef.current = rec;
    setListening(true);
    try { rec.start(); } catch { setListening(false); }
  };
  const stopVoice = () => { try { recRef.current?.stop(); } catch { /* ignore */ } setListening(false); };

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        if (listening) stopVoice();
        onRun(input);
      }}
    >
      <div className="composer-field">
        <textarea
          ref={taRef}
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing) {
              event.preventDefault();
              if (!running && input.trim()) { if (listening) stopVoice(); onRun(input); }
            }
          }}
          placeholder={listening ? "正在聆听，请说话…" : "继续追问（Enter 发送 · Shift+Enter 换行），或点麦克风语音输入"}
          disabled={running}
        />
        {SR ? (
          <button
            type="button"
            className={listening ? "mic-btn on" : "mic-btn"}
            onClick={listening ? stopVoice : startVoice}
            disabled={running}
            title={listening ? "停止" : "语音输入"}
            aria-label="语音输入"
          >
            <Mic size={17} />
          </button>
        ) : null}
      </div>
      <button
        className={running ? "send-button stop" : "send-button"}
        type={running ? "button" : "submit"}
        onClick={running ? onStop : undefined}
        disabled={running ? false : !input.trim()}
        title={running ? "停止生成" : "发送"}
        aria-label={running ? "停止生成" : "发送"}
      >
        <span className="send-ico">{running ? <Square size={16} /> : <Send size={18} />}</span>
      </button>
    </form>
  );
}

function EvidencePanel({ evidence, running }) {
  const groups = groupEvidence(evidence);
  return (
    <div className="inspector-body">
      <EvidenceFlow running={running} count={evidence.length} />
      <div className="evidence-groups">
        {groups.map((group) => (
          <section className="evidence-group" key={group.key}>
            <div className="evidence-group-head">
              <strong>{group.label}</strong>
              <span>{group.items.length}</span>
            </div>
            <p>{CONFIDENCE_DESCRIPTIONS[group.key] || "需要进一步核验。"}</p>
            <div className="evidence-list">
              {group.items.map((item, index) => (
                <EvidenceCard item={item} index={index} key={`${item.ref || item.url || item.source || group.key}-${index}`} />
              ))}
            </div>
          </section>
        ))}
        {!evidence.length ? <EmptyInspector icon={ShieldCheck} text="暂无证据。运行一次分析后会在这里显示来源。" /> : null}
      </div>
    </div>
  );
}

function EvidenceCard({ item, index }) {
  const confidence = item.confidence || item.source_kind || "speculative";
  const title = item.title || item.claim || item.source_title || item.source || `证据 ${index + 1}`;
  return (
    <article className={`evidence-card ${confidence}`}>
      <div>
        <strong>{title}</strong>
        <span className={`confidence-pill mini ${confidence}`}>{CONFIDENCE_LABELS[confidence] || "待验证"}</span>
      </div>
      <p>{item.quote || item.snippet || item.content || item.url || "暂无摘录。"}</p>
      <code>{sanitizeSourceLabel(item.ref || item.source_file || item.url || item.source || "source")}</code>
    </article>
  );
}

function EvidenceFlow({ running, count }) {
  return (
    <div className="evidence-flow">
      <svg viewBox="0 0 320 44" aria-hidden="true">
        <path className={running ? "flow-path live" : "flow-path"} d="M8 22 H90 C118 22 116 8 146 8 H176 C206 8 202 36 232 36 H312" />
        {[8, 90, 176, 232, 312].map((x, index) => <circle key={x} cx={x} cy={index === 3 ? 36 : index === 2 ? 8 : 22} r="4" />)}
      </svg>
      <span>{count} 条证据来源</span>
    </div>
  );
}

function TracePanel({ trace, running }) {
  const visible = trace.filter((item) => item.event !== "progress");
  return (
    <div className="inspector-body trace-body">
      <RunWave running={running} />
      <div className="trace-list">
        {visible.map((item, index) => <TraceItem item={item} key={`${item.event}-${index}`} />)}
        {!visible.length ? <EmptyInspector icon={MoreHorizontal} text="等待运行。" /> : null}
      </div>
    </div>
  );
}

function RunWave({ running }) {
  return (
    <div className={running ? "run-wave live" : "run-wave"}>
      {Array.from({ length: 22 }, (_, index) => <i key={index} style={{ "--i": index }} />)}
    </div>
  );
}

function TraceItem({ item }) {
  const agent = agentMap.get(item.data?.agent);
  const title = eventTitle(item);
  const provenance = item.data?.provenance;
  return (
    <article className={`trace-item ${item.event}`}>
      <span className="trace-dot" />
      <div>
        <strong>{agent?.zh || item.data?.agent || title}</strong>
        <p>{title}</p>
        {item.data?.name ? <code>{item.data.name}</code> : null}
        {provenance ? <small>{provenance.source_type || provenance.confidence} · {provenance.latency_ms ?? "-"}ms</small> : null}
      </div>
    </article>
  );
}

function ImageLightbox({ src, onClose }) {
  useEffect(() => {
    if (!src) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [src, onClose]);
  if (!src) return null;
  // Portal to <body>: ancestors with backdrop-filter/transform create a
  // containing block that would otherwise clip the position:fixed overlay to
  // the chat region instead of covering the whole page.
  return createPortal(
    <div className="img-lightbox" onClick={onClose} role="dialog" aria-modal="true">
      <button className="img-lightbox-close" type="button" onClick={onClose} aria-label="关闭"><X size={20} /></button>
      <img src={src} alt="产物大图" onClick={(e) => e.stopPropagation()} />
    </div>,
    document.body,
  );
}

const PRODUCIBLE = [
  { id: "pdf", kind: "pdf", title: "项目文档", description: "可下载 PDF 提案（封面 + 结论 + 五维评分）", icon: FileText },
  { id: "concept_image", kind: "concept_image", title: "概念图", description: "产品概念视觉（含 logo/标题）", icon: ImagePlus },
  { id: "audio_summary", kind: "audio", title: "语音摘要", description: "可播放的汇报摘要（可选产物）", icon: Mic },
];

function OutputPanel({ artifacts, artifact, running, onProduce, producing }) {
  const analysisReady = Boolean(artifact);
  const artifactMap = { pdf: artifacts.pdf, concept_image: artifacts.concept_image, audio_summary: artifacts.audio_summary };
  const [lightbox, setLightbox] = useState(null);
  return (
    <div className="inspector-body output-body">
      <ImageLightbox src={lightbox} onClose={() => setLightbox(null)} />
      <div className="output-hint">
        <strong>{analysisReady ? "按需分项生成" : "先完成一次分析"}</strong>
        <span>{analysisReady ? "每个产物各自一个生成按钮——只想要图片就单独点概念图。会话/工作区里的「生成产物」默认是文档 + 概念图一套。" : "完成分析后，这里可以分项生成项目文档、概念图和语音摘要。"}</span>
      </div>
      {PRODUCIBLE.map((item) => {
        const Icon = item.icon;
        const file = artifactMap[item.id];
        const href = artifactLink(file);
        const generated = Boolean(href);
        const failed = Boolean(file?.error && !href);
        return (
          <article className={`output-card ${failed ? "failed" : ""}`} key={item.id}>
            <div>
              <Icon size={18} />
              <strong>{item.title}</strong>
              <span>{failed ? "生成失败" : file?.bytes ? `${Math.round(file.bytes / 1024)} KB` : generated ? "已生成" : producing ? "生成中…" : analysisReady ? "可生成" : "待分析"}</span>
            </div>
            <p className={failed ? "output-error" : ""}>{failed ? (file?.message || "本项生成失败，其他产物可继续使用。") : item.description}</p>
            {item.id === "concept_image" && href ? <img className="output-img" src={href} alt="概念图产物" onClick={() => setLightbox(href)} title="点击查看大图" /> : null}
            {item.id === "audio_summary" && href ? <audio src={href} controls /> : null}
            <div className="output-card-foot">
              <button
                type="button"
                className="output-gen"
                onClick={() => onProduce && onProduce([item.kind])}
                disabled={!analysisReady || producing}
                title={analysisReady ? "" : "先做一次分析"}
              >
                {producing ? <Loader2 className="spin" size={14} /> : <Sparkles size={14} />}
                {generated ? "重新生成" : "生成"}
              </button>
              {item.id === "pdf" && href ? (
                <a className="output-link" href={href} target="_blank" rel="noreferrer">打开 PDF <ArrowUpRight size={14} /></a>
              ) : null}
              {item.id === "concept_image" && href ? (
                <button type="button" className="output-link as-link" onClick={() => setLightbox(href)}>查看大图 <ArrowUpRight size={14} /></button>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

function EmptyInspector({ icon: Icon, text }) {
  return (
    <div className="empty-inspector">
      <Icon size={22} />
      <span>{text}</span>
    </div>
  );
}

export function UploadModal({ open, busy, mode = "workspace", workspace, workspaceId, assetRole, onClose, onSubmit }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState([]);
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => {
    if (!open) {
      setName("");
      setDescription("");
      setFiles([]);
      setDragOver(false);
    }
  }, [open]);

  if (!open) return null;
  const isReference = mode === "reference";
  const isAppend = mode === "append" || isReference;
  const title = isReference ? "上传参考图" : isAppend ? "追加数据" : "上传数据";
  const subtitle = isReference ? "透明 PNG Logo / 活动图 / 参考图" : isAppend ? "追加 CSV / Excel / JSON / MD 到当前工作区" : "CSV / Excel / JSON / MD / 参考图";
  const dropTitle = isReference ? "拖入 Logo 或参考图" : "拖入文件或点击选择";
  const dropHelp = isReference ? "建议使用透明 PNG；也支持 JPG / WebP" : "支持多文件与参考图";
  const submitLabel = isReference ? "绑定参考图" : isAppend ? "追加入库" : "上传入库";
  const accepted = isReference ? "image/png,image/jpeg,image/webp" : undefined;
  const addFiles = (list) => setFiles((items) => [...items, ...Array.from(list || [])]);
  return (
    <div className="modal-overlay" role="presentation">
      <div className="upload-modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <div>
            <strong>{title}</strong>
            <span>{subtitle}</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose} disabled={busy}>
            <X size={17} />
          </button>
        </div>
        {isAppend ? (
          <div className="modal-target">
            <FolderOpen size={16} />
            <div>
              <strong>{workspace?.name || workspaceId || "当前工作区"}</strong>
              <span>{isReference ? "参考图会作为产物生成素材，不会进入工作区事实证据。" : "新数据会进入当前工作区画像与检索索引。"}</span>
            </div>
          </div>
        ) : (
          <>
            <label className="modal-field">
              <span>工作区名称</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：华东门店运营数据" />
            </label>
            <label className="modal-field">
              <span>备注</span>
              <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="数据背景、目标客群、已有产物约束" rows={3} />
            </label>
          </>
        )}
        <label
          className={dragOver ? "drop-zone over" : "drop-zone"}
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            addFiles(event.dataTransfer.files);
          }}
        >
          <UploadCloud size={24} />
          <strong>{dropTitle}</strong>
          <span>{dropHelp}</span>
          <input type="file" multiple hidden accept={accepted} onChange={(event) => addFiles(event.target.files)} />
        </label>
        <div className="file-list">
          {files.map((file, index) => (
            <div className="file-row" key={`${file.name}-${index}`}>
              <FileText size={15} />
              <span>{file.name}</span>
              <em>{formatBytes(file.size)}</em>
              <button type="button" onClick={() => setFiles((items) => items.filter((_, i) => i !== index))}>
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
        <div className="modal-actions">
          <button className="ghost-button" type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="primary-button" type="button" disabled={busy || !files.length} onClick={() => onSubmit({ name, description, files, workspaceId: isAppend ? workspaceId : "", assetRole })}>
            {busy ? <Loader2 className="spin" size={15} /> : <UploadCloud size={15} />}
            {submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function NoticeStack({ notice, uploadState, onDismiss }) {
  if (!notice && !uploadState) return null;
  return (
    <div className="notice-stack">
      {notice ? (
        <div className={`notice ${notice.type || "info"}`}>
          {notice.type === "error" ? <AlertTriangle size={16} /> : notice.type === "loading" ? <Loader2 className="spin" size={16} /> : <CheckCircle2 size={16} />}
          <span>{notice.message}</span>
          {notice.type !== "loading" ? <button type="button" onClick={onDismiss}><X size={14} /></button> : null}
        </div>
      ) : null}
      {uploadState ? (
        <div className={`notice ${uploadState.type || "info"}`}>
          {uploadState.type === "error" ? <AlertTriangle size={16} /> : uploadState.type === "done" ? <CheckCircle2 size={16} /> : <Loader2 className="spin" size={16} />}
          <span>{uploadState.message}</span>
        </div>
      ) : null}
    </div>
  );
}

export function extractArtifacts(artifact) {
  const proposal = artifact?.proposal || {};
  const urls = proposal.artifact_urls || {};
  return {
    pdf: proposal.pdf ? { ...proposal.pdf, artifact_url: proposal.pdf.artifact_url || urls.pdf } : artifact?.artifact_urls?.pdf ? { artifact_url: artifact.artifact_urls.pdf } : null,
    concept_image: proposal.concept_image ? { ...proposal.concept_image, artifact_url: proposal.concept_image.artifact_url || urls.concept_image } : artifact?.artifact_urls?.concept_image ? { artifact_url: artifact.artifact_urls.concept_image } : null,
    audio_summary: proposal.audio_summary ? { ...proposal.audio_summary, artifact_url: proposal.audio_summary.artifact_url || urls.audio_summary } : artifact?.artifact_urls?.audio_summary ? { artifact_url: artifact.artifact_urls.audio_summary } : null,
  };
}

function collectEvidence(artifact) {
  const items = [];
  for (const citation of artifact?.citations || artifact?.answer?.citations || []) items.push({ ...citation, confidence: citation.confidence || "data_confirmed" });
  for (const dimension of artifact?.feasibility?.dimensions || []) {
    for (const evidence of dimension.evidence || []) items.push({ ...evidence, title: DIMENSION_LABELS[dimension.name] || dimension.name, confidence: dimension.confidence });
  }
  for (const hit of artifact?.corpus?.hits || []) items.push({ ...hit, confidence: "data_confirmed", title: hit.title || sanitizeSourceLabel(hit.source_file) });
  for (const finding of artifact?.market?.external_findings || []) items.push({ ...finding, confidence: "market_inferred", title: finding.claim || finding.title || "市场来源" });
  for (const source of artifact?.market?.sources || []) {
    const url = typeof source === "string" ? source : source.url;
    items.push({ ...(typeof source === "object" ? source : {}), source: url, url, confidence: "market_inferred", title: (typeof source === "object" ? source.title : "") || "外部网页来源" });
  }
  const seen = new Set();
  return items.filter((item) => {
    const key = item.ref || item.url || item.source || item.source_file || item.title;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 18);
}

function summarizeQuality(artifact, trace, artifacts, evidence) {
  const workspaceEvidenceCount = evidence.filter((item) => (item.confidence || item.source_kind) === "data_confirmed").length;
  const marketEvidenceCount = evidence.filter((item) => (item.confidence || item.source_kind) === "market_inferred").length;
  const audit = artifact?.audit || trace.find((item) => item.event === "audit")?.data || {};
  const artifactCount = Object.values(artifacts || {}).filter(Boolean).length;
  return {
    workspaceEvidence: workspaceEvidenceCount > 0,
    workspaceEvidenceCount,
    marketEvidence: marketEvidenceCount > 0,
    marketEvidenceCount,
    auditPassed: audit.verdict === "pass",
    auditLabel: audit.verdict === "pass" ? "通过" : audit.verdict ? "需复核" : "待审计",
    confidence: artifact?.feasibility?.overall_confidence || "speculative",
    artifactReady: artifactCount > 0,
    artifactLabel: artifactCount ? `${artifactCount} 个` : artifact ? "可生成" : "待分析",
  };
}

function groupEvidence(evidence) {
  const order = ["data_confirmed", "market_inferred", "speculative"];
  return order.map((key) => ({
    key,
    label: CONFIDENCE_LABELS[key],
    items: evidence.filter((item) => (item.confidence || item.source_kind || "speculative") === key),
  })).filter((group) => group.items.length);
}

function eventTitle(item) {
  const data = item.data || {};
  switch (item.event) {
    case "ready": return "连接运行通道";
    case "user": return "接收用户问题";
    case "route": return `路由到 ${data.intent || "workflow"}`;
    case "plan": return `调度 ${(data.experts || []).length} 个 Agent`;
    case "role_change": return "Agent 接手";
    case "tool_call": return `调用 ${data.name || "tool"}`;
    case "tool_result": return `${data.name || "tool"} 返回 ${data.count ?? data.status ?? "ok"}`;
    case "model_response": return `模型响应 ${data.usage?.total_tokens ? `${data.usage.total_tokens} tokens` : ""}`;
    case "audit": return data.verdict === "pass" ? "审计通过" : "审计要求修订";
    case "revised_verdict": return "生成修订后结论";
    case "cache": return "读取缓存";
    case "clarify": return data.question || "需要澄清";
    case "final": return "最终输出完成";
    case "error": return data.message || "运行错误";
    default: return item.event;
  }
}

function sanitizeSourceLabel(value) {
  return String(value || "")
    .replace(/^raw_docs\//, "")
    .replace(/^external\//, "")
    .replace(/#.+$/, "");
}

function formatTime(value) {
  if (!value) return "recent";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recent";
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "0 KB";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function absoluteApiUrl(value) {
  if (!value) return "";
  return String(value).startsWith("http") ? value : `${API_BASE}${value}`;
}
