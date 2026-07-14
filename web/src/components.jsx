import React, { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { startTour } from "./tour.js";
import { MAF_MODES, deriveMafViewModel, mafEventData, mafRevisionNumber, mafStatusLabel, mafStatusTone } from "./mafViewModel.js";
import { formatGovernanceTokenLabel, formatGovernanceTokens } from "./governanceUsage.js";
import {
  auditEventViewModel,
  chargebackViewModel,
  governancePermissions,
  invitationLifecycleViewModel,
  memberDirectoryViewModel,
  roiViewModel,
  traceViewModel,
} from "./governanceViewModel.js";
import { auditPageFailure, auditPageSuccess, createGovernanceRequestGuard } from "./governanceRequestState.js";
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
  Boxes,
  Server,
  Cpu,
  HardDrive,
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
import { API_BASE, artifactLink, compareExperiments, loadDataOverview, loadExperiments, loadFlagship, loadPlanMetrics, loadPlaybookDetail, loadRun, setFlagship, loadArtifactsList, loadRunSummary, loadRunTrace, runLogUrl, loadRunLog, loadSystemStatus, loadWorkspaceSettings, loadMembers, searchEntraUsers, inviteEntraMember, removeMember, updateMemberRole, loadWorkspaceTraceStatus, loadWorkspaceRoi, loadWorkspaceChargeback, loadWorkspaceGovernanceAuditEvents, loadWorkspaceInvitationHistory } from "./api.js";
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
const DEFAULT_OWNER_NAME = "傅子豪";
const DEFAULT_OWNER_EMAIL = "fuzihao@gdjiuyun.onmicrosoft.com";

function cleanUserValue(value) {
  return String(value || "").trim();
}

function normalizedCurrentUser(user = {}, { allowDefault = true } = {}) {
  const rawName = cleanUserValue(user?.name);
  const rawEmail = cleanUserValue(user?.email);
  const isPlaceholderEmail = !rawEmail || /^(local\.demo@dataforge|owner@example\.com|fuzh084711@gmail\.com)$/i.test(rawEmail);
  const email = isPlaceholderEmail ? (allowDefault ? DEFAULT_OWNER_EMAIL : "") : rawEmail;
  let name = rawName && rawName !== "Demo User" ? rawName : "";
  if (!name && email) name = email.split("@", 1)[0].replace(/[._-]+/g, " ");
  if (email.toLowerCase() === DEFAULT_OWNER_EMAIL) name = DEFAULT_OWNER_NAME;
  return {
    name: name || (allowDefault ? DEFAULT_OWNER_NAME : ""),
    email,
    reliable: !isPlaceholderEmail,
  };
}

function memberInitial(name, email) {
  const source = cleanUserValue(name).replace(/（你）$/, "") || cleanUserValue(email) || "成";
  return source.slice(0, 1).toUpperCase();
}

function memberRoleLabel(role) {
  const key = cleanUserValue(role).toLowerCase();
  if (key === "owner") return "所有者";
  if (key === "admin") return "管理员";
  if (key === "editor") return "编辑者";
  if (key === "viewer") return "查看者";
  return cleanUserValue(role) || "成员";
}

export function ShellNav({ active = "workspaces", onChange = () => {}, workspace = {}, onInviteMembers = () => {} }) {
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
        <button className="wsf-invite" type="button" title="打开成员、权限和用量溯源" onClick={onInviteMembers}>
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

function NotificationBell({ count = 0, onOpen = () => {} }) {
  return (
    <button className="icon-button top-icon" type="button" title="打开全局任务中心" aria-label="打开全局任务中心" onClick={onOpen}>
      <Bell size={16} />
      {count ? <span className="notif-badge">{count}</span> : null}
    </button>
  );
}

function WorkspaceSwitcher({ workspaces = [], workspaceId, onChange, onDelete, deleting = false }) {
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
  const handleDelete = (event, workspace) => {
    event.preventDefault();
    event.stopPropagation();
    if (!onDelete || !workspace?.workspace_id?.startsWith("upload-")) return;
    const name = workspace.name || workspace.workspace_id;
    const confirmed = window.confirm(`删除工作区「${name}」？\n\n这会移除该工作区的文件、运行索引和产物记录。`);
    if (!confirmed) return;
    setOpen(false);
    onDelete(workspace.workspace_id);
  };
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
                const canDelete = w.workspace_id?.startsWith("upload-");
                return (
                  <div key={w.workspace_id} className={active ? "ws-dd-row cur" : "ws-dd-row"} role="none">
                    <button type="button" className="ws-dd-item" role="menuitem" onClick={() => { onChange(w.workspace_id); setOpen(false); }}>
                      <span className="ws-ck">{active ? <Check size={15} /> : null}</span>
                      <span className="ws-dd-meta">
                        <span className="ws-dd-name">{w.name || w.workspace_id}</span>
                        {sub ? <span className="ws-dd-sub">{sub}</span> : null}
                      </span>
                    </button>
                    {canDelete ? (
                      <button
                        type="button"
                        className="ws-dd-delete"
                        title={`删除工作区：${w.name || w.workspace_id}`}
                        aria-label={`删除工作区：${w.name || w.workspace_id}`}
                        disabled={deleting}
                        onClick={(event) => handleDelete(event, w)}
                      >
                        <Trash2 size={14} />
                      </button>
                    ) : null}
                  </div>
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

export function TopBar({ dashboard, workspaceId, onWorkspaceChange, onUpload, onNewConversation, onDeleteWorkspace, loading, deleting = false, user, authState, onLogout, tasks = [], onOpenTaskCenter }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const workspaces = dashboard?.workspaces || [];
  const health = dashboard?.health || {};
  const deps = health.dependencies || {};
  const account = normalizedCurrentUser(user);

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
        <WorkspaceSwitcher workspaces={workspaces} workspaceId={workspaceId} onChange={onWorkspaceChange} onDelete={onDeleteWorkspace} deleting={deleting} />
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
        <NotificationBell count={tasks.filter((task) => ["queued", "running"].includes(task.status)).length} onOpen={onOpenTaskCenter} />
        <div className="user-menu" ref={menuRef}>
          <button className="user-trigger" type="button" onClick={() => setMenuOpen((value) => !value)} aria-expanded={menuOpen} title="账户">
            <div className="avatar" title="账户">
              {memberInitial(account.name, "")}
            </div>
          </button>
          {menuOpen ? (
            <div className="account-menu" role="menu">
              <div className="account-card">
                <div className="avatar large">
                  {memberInitial(account.name, "")}
                </div>
                <div>
                  <strong>{account.name}</strong>
                  <span>已登录账户</span>
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

// 渲染兜底：任一页面渲染抛错时显示提示而不是整页白屏
class ViewErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidUpdate(prev) { if (prev.view !== this.props.view && this.state.error) this.setState({ error: null }); }
  render() {
    if (this.state.error) {
      return (
        <main className="agent-studio" style={{ display: "grid", placeItems: "center", padding: 40 }}>
          <div style={{ textAlign: "center", color: "var(--muted)", maxWidth: 420 }}>
            <AlertTriangle size={28} style={{ color: "var(--amber)" }} />
            <h2 style={{ margin: "12px 0 6px", fontSize: 18, color: "var(--ink)" }}>这个页面出了点问题</h2>
            <p style={{ fontSize: 13, lineHeight: 1.6 }}>页面渲染时遇到异常，已避免整页白屏。可刷新页面或切换到其他页面后重试。</p>
            <button className="ghost-button" type="button" style={{ marginTop: 14 }} onClick={() => window.location.reload()}>刷新页面</button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}

export function WorkbenchMain(props) {
  return (
    <ViewErrorBoundary view={props.view}>
      <WorkbenchMainInner {...props} />
    </ViewErrorBoundary>
  );
}

function WorkbenchMainInner({
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
  artifactRefreshKey,
  onProduce,
  onUploadReference,
  producing,
  observability,
  onOpenConversation,
  onAppendUpload,
  tasks,
  user,
  settingsInitialTab,
  onWorkspaceDataChanged,
  onOpenTaskCenter,
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
        <DataWorkbench dashboard={dashboard} onUpload={onAppendUpload} onOpenConversation={onOpenConversation} onRun={onRun} user={user} tasks={tasks} onOpenTaskCenter={onOpenTaskCenter} onWorkspaceDataChanged={onWorkspaceDataChanged} />
      </Suspense>
    );
  }
  if (view === "artifacts") {
    return <ArtifactsCenter dashboard={dashboard} artifacts={artifacts} artifact={finalArtifact} artifactRefreshKey={artifactRefreshKey} onProduce={onProduce} producing={producing} onUploadReference={onUploadReference} />;
  }
  if (view === "runs") {
    return <RunsCenter dashboard={dashboard} trace={trace} running={running} observability={observability} onOpenConversation={onOpenConversation} tasks={tasks} />;
  }
  if (view === "settings") {
    return <SettingsCenter dashboard={dashboard} observability={observability} user={user} initialTab={settingsInitialTab} />;
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
const ARTIFACT_KIND_LABEL = { pdf: "项目文档", concept_image: "概念图", audio_summary: "语音摘要", audio: "语音摘要", pilot_plan: "试点设计", action_plan: "行动清单", roadmap: "路线图", validation_plan: "验证计划" };

function feasibilityOf(run) {
  return run?.final?.artifact?.feasibility || run?.artifact?.feasibility || run?.feasibility || {};
}
function iterInputsOf(run) {
  return run?.iteration_inputs || run?.final?.artifact?.iteration_inputs || run?.artifact?.iteration_inputs || [];
}
function artifactOf(run) {
  return run?.final?.artifact || run?.artifact || {};
}
function artifactUrlsOf(run) {
  const artifact = artifactOf(run);
  return artifact?.proposal?.artifact_urls || run?.artifact_urls || {};
}
function citationLabelsOf(run) {
  const artifact = artifactOf(run);
  const items = artifact?.citations || artifact?.answer?.citations || [];
  return (Array.isArray(items) ? items : [])
    .map((item) => item?.title || item?.source_file || item?.ref || item?.marker)
    .filter(Boolean)
    .slice(0, 4);
}
function producedKindLabels(run) {
  const keys = [
    ...((run?.produced_kinds || []).map(String)),
    ...Object.keys(run?.artifact_urls || {}),
    ...Object.keys(artifactUrlsOf(run) || {}),
  ].filter(Boolean);
  return [...new Set(keys)].map((key) => ARTIFACT_KIND_LABEL[key] || key);
}
function versionSummaryText(run, fallback) {
  if (!run) return fallback || "";
  const inputs = iterInputsOf(run).filter((m) => m?.label);
  const kind = run.version_kind || run.versionKind;
  if (kind === "plan_draft") {
    const draft = run.artifact?.plan_draft?.text || run.final?.artifact?.plan_draft?.text;
    if (draft) {
      const firstLine = String(draft).split(/\r?\n/).map((s) => s.trim()).find(Boolean);
      if (firstLine) return `本版沉淀了一版可执行方案：${firstLine.replace(/^#+\s*/, "").slice(0, 80)}`;
    }
    return run.summary || "本版把会话反馈整理成方案草案，可继续生成项目文档、验证计划或回填试点指标。";
  }
  if (kind === "artifact_generation") {
    const labels = producedKindLabels(run);
    return labels.length
      ? `本版把上一轮分析沉淀为${labels.join("、")}，用于评审交付；试点回填后可继续生成下一版。`
      : "本版是产物沉淀版本，记录了当前方案已进入交付环节，可继续回填指标推进下一轮。";
  }
  if (inputs.length) {
    const preview = inputs.slice(0, 4).map((m) => `${m.label}${m.value ? ` ${m.value}${m.unit || ""}` : ""}`).join("、");
    return `本版基于回填指标重新判断：${preview}；用于观察结论、缺口和五维评分是否收敛。`;
  }
  return run.summary || run.recommendation || run.title || fallback || "";
}
function isPlanVersionRun(run) {
  if (!run?.verdict || !(run.run_id || run.conversation_id)) return false;
  const hasArtifacts = Boolean((run.produced_kinds || []).length || Object.keys(run.artifact_urls || {}).length);
  if (run.version_kind === "plan_draft") return true;
  if (run.version_kind === "artifact_generation") return hasArtifacts;
  if (iterInputsOf(run).length) return true;
  const status = String(run.status || "").toLowerCase();
  return !["followup", "followup_edit"].includes(status);
}
function buildPlanDiff(runA, runB) {
  const a = feasibilityOf(runA);
  const b = feasibilityOf(runB);
  const dimsA = Array.isArray(a.dimensions) ? a.dimensions : [];
  const dimsB = Array.isArray(b.dimensions) ? b.dimensions : [];
  const byNameA = {};
  const byNameB = {};
  dimsA.forEach((d) => { if (d?.name) byNameA[d.name] = d; });
  dimsB.forEach((d) => { if (d?.name) byNameB[d.name] = d; });
  const names = [...new Set([...dimsA.map((d) => d?.name).filter(Boolean), ...dimsB.map((d) => d?.name).filter(Boolean)])];
  const dims = names.map((name) => {
    const prev = byNameA[name] || {};
    const d = byNameB[name] || {};
    const base = prev.score === undefined || prev.score === null ? null : Number(prev.score);
    const target = d.score === undefined || d.score === null ? null : Number(d.score);
    const delta = Number.isFinite(base) && Number.isFinite(target) ? target - base : null;
    return { name, label: (DIMENSION_LABELS && DIMENSION_LABELS[name]) || name, base, target, delta, baseConf: prev.confidence, targetConf: d.confidence };
  });
  const gapsA = new Set((a.gap_list || []).map(String));
  const gapsB = (b.gap_list || []).map(String);
  const gapsBset = new Set(gapsB);
  const added = gapsB.filter((g) => !gapsA.has(g));
  const resolved = [...gapsA].filter((g) => !gapsBset.has(g));
  const vr = (v) => VERDICT_RANK[v] || 0;
  const urlsA = artifactUrlsOf(runA);
  const urlsB = artifactUrlsOf(runB);
  const artifactKinds = [...new Set([...Object.keys(urlsA || {}), ...Object.keys(urlsB || {})])].filter((key) => urlsA?.[key] || urlsB?.[key]);
  return {
    from: { title: runA?.title, summary: runA?.summary, versionKind: runA?.version_kind, producedKinds: runA?.produced_kinds || [] },
    to: { title: runB?.title, summary: runB?.summary, versionKind: runB?.version_kind, producedKinds: runB?.produced_kinds || [] },
    verdict: { from: a.verdict, to: b.verdict, dir: Math.sign(vr(b.verdict) - vr(a.verdict)) },
    confidence: { from: a.confidence || a.overall_confidence, to: b.confidence || b.overall_confidence },
    dims,
    iterationInputs: iterInputsOf(runB),
    artifacts: {
      kinds: artifactKinds,
      added: artifactKinds.filter((kind) => !urlsA?.[kind] && urlsB?.[kind]),
      fromCount: Object.values(urlsA || {}).filter(Boolean).length,
      toCount: Object.values(urlsB || {}).filter(Boolean).length,
    },
    evidence: { from: citationLabelsOf(runA), to: citationLabelsOf(runB) },
    gaps: { added, resolved },
  };
}

function buildExperimentDiff(payload) {
  const from = payload?.from || {};
  const to = payload?.to || {};
  const fromDecision = from.decision || {};
  const toDecision = to.decision || {};
  const byName = (items) => Object.fromEntries((items || []).filter((item) => item?.name).map((item) => [item.name, item]));
  const dimsA = byName(fromDecision.dimensions);
  const dimsB = byName(toDecision.dimensions);
  const names = [...new Set([...Object.keys(dimsA), ...Object.keys(dimsB)])];
  const dims = names.map((name) => {
    const base = dimsA[name]?.score == null ? null : Number(dimsA[name].score);
    const target = dimsB[name]?.score == null ? null : Number(dimsB[name].score);
    return {
      name,
      label: (DIMENSION_LABELS && DIMENSION_LABELS[name]) || name,
      base,
      target,
      delta: Number.isFinite(base) && Number.isFinite(target) ? target - base : null,
    };
  });
  const gapsA = new Set((fromDecision.gaps || from.gaps || []).map(String));
  const gapsB = (toDecision.gaps || to.gaps || []).map(String);
  const artifactUrls = (version) => (version?.attachments?.artifacts || []).flatMap((item) => Object.entries(item?.urls || {}).filter(([, url]) => url));
  const fromArtifacts = artifactUrls(from);
  const toArtifacts = artifactUrls(to);
  const fromKinds = new Set(fromArtifacts.map(([kind]) => kind));
  const toKinds = [...new Set(toArtifacts.map(([kind]) => kind))];
  const evidenceDelta = payload?.evidence_delta || to.evidence_delta || { added: [], removed: [], strengthened: [], contradicted: [] };
  return {
    from: { title: from.title, summary: from.decision_delta?.summary, versionKind: "analysis" },
    to: { title: to.title, summary: to.decision_delta?.summary, versionKind: "analysis" },
    verdict: {
      from: fromDecision.verdict,
      to: toDecision.verdict,
      dir: Math.sign((VERDICT_RANK[toDecision.verdict] || 0) - (VERDICT_RANK[fromDecision.verdict] || 0)),
    },
    dims,
    iterationInputs: (to.metrics || []).map((item) => ({ label: item.metric_name, value: item.value, unit: item.unit, kind: item.kind, provenance: item.provenance })),
    artifacts: { added: toKinds.filter((kind) => !fromKinds.has(kind)), fromCount: fromArtifacts.length, toCount: toArtifacts.length },
    evidence: { from: (from.evidence || []).map((item) => item.ref), to: (to.evidence || []).map((item) => item.ref) },
    gaps: { added: gapsB.filter((gap) => !gapsA.has(gap)), resolved: [...gapsA].filter((gap) => !gapsB.includes(gap)) },
    evidence_delta: evidenceDelta,
    decisionDelta: payload?.decision_delta || to.decision_delta || {},
  };
}

const _CV_RANK = { not_yet_feasible: 1, not_feasible: 1, rejected: 1, conditional: 2, feasible: 3, recommended: 3 };
const _CV_LABEL = { 1: "暂不可行", 2: "有条件可行", 3: "可行" };
const _CV_COLOR = { 1: "#8a5a00", 2: "#0A84E0", 3: "#0a7d4f" };
function ConvergenceChart({ versions, flagshipId }) {
  const vers = (versions || []).slice(-8);
  if (vers.length < 2) return null;
  const n = vers.length;
  const padL = 90;
  const padR = 30;
  const H = 250;
  const padT = 40;
  const padB = 50;
  // 宽幅 viewBox（约 5:1）匹配又宽又扁的图框；配合 CSS aspect-ratio + width:100% 撑满整框、不留白
  const W = 1280;
  const usableW = W - padL - padR;
  const usableH = H - padT - padB;
  const x = (i) => padL + (n > 1 ? (usableW * i) / (n - 1) : usableW / 2);
  const y = (r) => padT + usableH * (1 - (r - 1) / 2);
  const rankOf = (v) => _CV_RANK[v.verdict] || 1;
  const pts = vers.map((v, i) => [x(i), y(rankOf(v))]);
  const firstRank = rankOf(vers[0]);
  const lastRank = rankOf(vers[n - 1]);
  const delta = lastRank - firstRank;
  const gid = "cvg-" + n;
  const baseY = y(1);
  const linePts = pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const areaD = `M ${pts[0][0].toFixed(1)},${baseY.toFixed(1)} L ` + pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" L ") + ` L ${pts[n - 1][0].toFixed(1)},${baseY.toFixed(1)} Z`;
  return (
    <svg className="conv-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="迭代收敛图" preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2563eb" stopOpacity="0.20" />
          <stop offset="100%" stopColor="#2563eb" stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* 目标线：公司重点方案档位（可行） */}
      <line x1={padL} y1={y(3)} x2={W - padR} y2={y(3)} stroke="#0a7d4f" strokeWidth="1.2" strokeDasharray="5 4" opacity="0.55" />
      <text x={W - padR} y={y(3) - 7} fontSize="10.5" fill="#0a7d4f" textAnchor="end" fontWeight="600">目标 · 公司重点</text>
      {[1, 2, 3].map((r) => (
        <g key={r}>
          {r !== 3 ? <line x1={padL} y1={y(r)} x2={W - padR} y2={y(r)} stroke="#eef1f5" strokeWidth="1" /> : null}
          <text x={padL - 16} y={y(r) + 3} fontSize="10.5" fill="#9aa3af" textAnchor="end">{_CV_LABEL[r]}</text>
        </g>
      ))}
      <path d={areaD} fill={`url(#${gid})`} />
      <polyline points={linePts} fill="none" stroke="#2563eb" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
      {pts.map(([px, py], i) => {
        const isFlag = vers[i].id === flagshipId;
        const isLast = i === n - 1;
        return (
          <g key={i}>
            {(isFlag || isLast) ? <circle cx={px.toFixed(1)} cy={py.toFixed(1)} r="8" fill={_CV_COLOR[rankOf(vers[i])]} opacity="0.16" /> : null}
            <circle cx={px.toFixed(1)} cy={py.toFixed(1)} r={isFlag || isLast ? 5.2 : 4} fill={_CV_COLOR[rankOf(vers[i])]} stroke="#fff" strokeWidth="1.5" />
            <text x={px.toFixed(1)} y={(py - 12).toFixed(1)} fontSize="10" fill={_CV_COLOR[rankOf(vers[i])]} textAnchor="middle" fontWeight="600">{_CV_LABEL[rankOf(vers[i])]}</text>
            <text x={px.toFixed(1)} y={H - 12} fontSize="11" fill={isFlag ? "#b8860b" : "#6e6e73"} textAnchor="middle" fontWeight={isFlag || isLast ? "700" : "500"}>{vers[i].vlabel}{isFlag ? " ★" : ""}</text>
          </g>
        );
      })}
      {/* 改进幅度标注 */}
      <g transform={`translate(${padL}, 12)`}>
        {delta > 0 ? (
          <text fontSize="11.5" fill="#0a7d4f" fontWeight="700">↑ 迭代 {n} 版 · 可行性跃迁 {delta} 档，逼近公司重点方案</text>
        ) : (
          <text fontSize="11.5" fill="#6e6e73" fontWeight="600">迭代 {n} 版 · 结论稳定在「{_CV_LABEL[lastRank]}」，需补证据再上一档</text>
        )}
      </g>
    </svg>
  );
}

function PlanIteratePanel({ workspaceId, runs, running, onIterate }) {
  const runVersions = useMemo(() => {
    const list = (runs || []).filter(isPlanVersionRun);
    return list
      .slice()
      .sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")))
      .map((r, i) => ({ ...r, vlabel: `v${i + 1}`, id: r.run_id || r.conversation_id }));
  }, [runs]);
  const [experimentLedger, setExperimentLedger] = useState(null);
  const versions = useMemo(() => {
    const canonical = Array.isArray(experimentLedger?.versions) ? experimentLedger.versions : [];
    if (!canonical.length) return runVersions;
    return canonical.map((item) => ({
      ...item,
      id: item.run_id || item.version_id,
      vlabel: item.label || `V${item.ordinal || 1}`,
      verdict: item.decision?.verdict,
      confidence: item.decision?.confidence,
      summary: item.decision_delta?.summary,
      time: item.created_at,
      experimentVersion: true,
    }));
  }, [experimentLedger, runVersions]);
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
  const [customRows, setCustomRows] = useState([]); // 用户自定义对比字段 {label,a,b}
  const addCustomRow = () => setCustomRows((rs) => [...rs, { label: "", a: "", b: "" }]);
  const editCustomRow = (i, patch) => setCustomRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const delCustomRow = (i) => setCustomRows((rs) => rs.filter((_, j) => j !== i));
  const numDelta = (a, b) => { const x = parseFloat(a), y = parseFloat(b); if (Number.isNaN(x) || Number.isNaN(y)) return null; return y - x; };

  useEffect(() => {
    if (!workspaceId) { setExperimentLedger(null); return; }
    let cancelled = false;
    loadExperiments(workspaceId)
      .then((data) => { if (!cancelled) setExperimentLedger(data); })
      .catch(() => { if (!cancelled) setExperimentLedger(null); });
    return () => { cancelled = true; };
  }, [workspaceId, runs?.length]);

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
  const versionKindLabel = (v) => {
    const kind = v?.version_kind || v?.versionKind;
    if (kind === "artifact_generation") return "产物版";
    if (kind === "plan_draft") return "方案版";
    return "分析版";
  };
  const scoreText = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(1).replace(/\.0$/, "") : "—";
  const compare = async () => {
    if (!cmpA || !cmpB || cmpA === cmpB) return;
    setDiffLoading(true); setDiff(null);
    try {
      if (experimentLedger?.versions?.length) {
        const result = await compareExperiments(workspaceId, `version:${cmpA}`, `version:${cmpB}`);
        setDiff(buildExperimentDiff(result));
      } else {
        const [ra, rb] = await Promise.all([loadRun(cmpA), loadRun(cmpB)]);
        setDiff(buildPlanDiff(ra, rb));
      }
    } catch { setDiff(null); } finally { setDiffLoading(false); }
  };

  const effectiveBase = baseId || latest?.id;
  const extract = async () => {
    if (!effectiveBase) return;
    setOpen(true); setLoading(true); setMetrics(null);
    try {
      const source = versions.find((v) => v.id === effectiveBase);
      const metricRunId = source?.version_kind === "artifact_generation" && source?.source_run_id ? source.source_run_id : effectiveBase;
      const d = await loadPlanMetrics(metricRunId);
      setMetrics((d?.metrics || []).map((m) => ({
        ...m,
        source_ref: m?.source?.file_id || m?.source?.run_id || "",
      })));
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
    const inputs = (metrics || [])
      .filter((m) => m.label && m.label.trim())
      .map((metric) => ({
        ...metric,
        ...(metric.kind === "observed" && metric.source_ref
          ? { source: { file_id: metric.source_ref } }
          : {}),
      }));
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
  const selected = versions.find((v) => v.id === effectiveBase) || latest || {};
  const selVerdict = VERDICT_LABELS[selected.verdict] || selected.verdict || "—";
  const verdictTone = (vd) => (vd === "feasible" || vd === "recommended" ? "ok" : vd === "conditional" ? "blue" : "warn");
  const selSummary = versionSummaryText(
    selected,
    `第 ${selected.vlabel ? selected.vlabel.replace(/\D/g, "") : versions.length} 版结论为「${selVerdict}」，回填客获率/转化率/价格/成本等关键商业指标可推进下一版判断。`,
  );
  const PENDING_METRICS = ["客获率", "转化率", "价格", "成本", "毛利", "目标客群"];
  const baseLabel = selected.vlabel || "当前";
  return (
    <section className="plan-iter-card pi2" data-tour="iterate">
      {/* 顶部：标题+说明 | 主按钮 */}
      <div className="pi2-top">
        <div className="pi2-titlewrap">
          <div className="pi2-title"><Layers3 size={16} /><strong>方案迭代 · 指标回填</strong></div>
          <p className="pi2-desc">追踪每版方案的可行性变化，并将客获率、转化率、价格等关键指标回填到迭代记录中。</p>
        </div>
        <button type="button" className="pi2-extract" onClick={extract} disabled={!effectiveBase}>
          <Sparkles size={15} /> 提取「{baseLabel}」方案指标并回填
        </button>
      </div>

      {/* 当前选中版本摘要（整行） */}
      <aside className="pi2-summary pi2-summary-full">
        <div className="pi2-sum-grid">
          <div className="pi2-sum-row"><span>当前选中版本</span><b className="pi2-sel">{selected.vlabel || "—"}</b></div>
          <div className="pi2-sum-row"><span>结论</span><span className={`dw-chip ${verdictTone(selected.verdict)}`}>{selVerdict}</span></div>
          <div className="pi2-sum-row"><span>最近操作</span><b className="pi2-faint">{open && metrics && metrics.length ? "已提取指标，待回填" : "尚未回填指标"}</b></div>
        </div>
        <div className="pi2-sum-block"><span>结论摘要</span><p>{selSummary}</p></div>
        <div className="pi2-sum-block"><span>待回填指标</span><div className="pi2-chips">{PENDING_METRICS.map((m) => <span className="pi2-chip" key={m}>{m}</span>)}</div></div>
      </aside>

      {/* 版本轨道：紧凑 stepper */}
      <div className="pi2-track">
        {versions.map((v, i) => (
          <React.Fragment key={v.id}>
            <button type="button" className={`pi2-pill ${v.id === effectiveBase ? "cur" : ""}`} onClick={() => setBaseId(v.id)} title={`以 ${v.vlabel} 为基准`}>
              <span className={`pi2-pdot ${verdictTone(v.verdict)}`} />
              <b>{v.vlabel}</b><em>{VERDICT_LABELS[v.verdict] || v.verdict}</em>
              {["artifact_generation", "plan_draft"].includes(v.version_kind) ? <small className="pi2-kind">{versionKindLabel(v)}</small> : null}
              <span className={`pi2-star ${v.id === flagshipId ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); markFlagship(v.id); }} title={v.id === flagshipId ? "取消公司重点" : "标为公司重点"}><Star size={12} /></span>
            </button>
            {i < versions.length - 1 ? <span className="pi2-sep">—</span> : null}
          </React.Fragment>
        ))}
      </div>

      {/* 版本对比：一条工具栏 + 差异摘要 */}
      {versions.length >= 2 ? (
        <div className="pi2-cmp">
          <span className="pi2-cmp-t">版本对比</span>
          <select value={cmpA} onChange={(e) => { setCmpA(e.target.value); setDiff(null); }}>
            {versions.map((v) => <option key={v.id} value={v.id}>{v.vlabel}</option>)}
          </select>
          <span className="pi2-cmp-arrow">→</span>
          <select value={cmpB} onChange={(e) => { setCmpB(e.target.value); setDiff(null); }}>
            {versions.map((v) => <option key={v.id} value={v.id}>{v.vlabel}</option>)}
          </select>
          <button type="button" className="pi2-cmp-go" onClick={compare} disabled={!cmpA || !cmpB || cmpA === cmpB || diffLoading}>
            {diffLoading ? <Loader2 className="spin" size={13} /> : <BarChart3 size={13} />} 对比
          </button>
          {diff ? (
            <span className="pi2-cmp-sum">
              {diff.verdict.dir > 0 ? "结论提升" : diff.verdict.dir < 0 ? "结论下降" : "结论未改善"}
              {diff.artifacts.added.length ? `，新增${diff.artifacts.added.map((k) => ARTIFACT_KIND_LABEL[k] || k).join("、")}` : diff.gaps.added.length ? `，仍缺 ${String(diff.gaps.added[0]).slice(0, 28)}` : ""}
            </span>
          ) : null}
        </div>
      ) : null}

      {diff ? (
        <div className="pi-diff">
          <div className="pi-diff-row head">
            <span>对比项</span><span>{vlabelOf(cmpA)} · {versionKindLabel(diff.from)}</span><span>{vlabelOf(cmpB)} · {versionKindLabel(diff.to)}</span><span>变化</span>
          </div>
          <div className="pi-diff-row">
            <span className="pi-diff-k">结论档位</span>
            <span>{VERDICT_LABELS[diff.verdict.from] || diff.verdict.from || "—"}</span>
            <span>{VERDICT_LABELS[diff.verdict.to] || diff.verdict.to || "—"}</span>
            <b className={diff.verdict.dir > 0 ? "up" : diff.verdict.dir < 0 ? "down" : ""}>{diff.verdict.dir > 0 ? "提升" : diff.verdict.dir < 0 ? "下降" : "持平"}</b>
          </div>
          {diff.dims.length ? diff.dims.map((d) => (
            <div className="pi-diff-row" key={d.name}>
              <span className="pi-diff-k">{d.label}</span>
              <span>{scoreText(d.base)} / 5</span>
              <span>{scoreText(d.target)} / 5</span>
              <b className={d.delta > 0 ? "up" : d.delta < 0 ? "down" : ""}>{d.delta === null ? "—" : d.delta > 0 ? `+${d.delta}` : d.delta}</b>
            </div>
          )) : (
            <div className="pi-diff-note">
              <em>维度评分</em>
              <span className="pi-diff-chip">这两版缺少可对齐的五维分，已用摘要、产物和回填指标辅助对比。</span>
            </div>
          )}
          <div className="pi-diff-note">
            <em>产物变化</em>
            <span className="pi-diff-chip">{diff.artifacts.fromCount || 0} → {diff.artifacts.toCount || 0} 个产物</span>
            {diff.artifacts.added.length ? diff.artifacts.added.map((kind) => <span className="pi-diff-chip" key={kind}>新增 {ARTIFACT_KIND_LABEL[kind] || kind}</span>) : <span className="pi-diff-chip">未新增产物</span>}
          </div>
          {diff.iterationInputs.length ? (
            <div className="pi-diff-note">
              <em>回填指标</em>
              {diff.iterationInputs.slice(0, 5).map((m, i) => <span className="pi-diff-chip" key={`${m.label}-${i}`}>{m.label}: {m.value || "—"}{m.unit || ""}</span>)}
            </div>
          ) : null}
          <div className="pi-diff-gaps">
            <span className="g-res">已解决 {diff.gaps.resolved.length}</span>
            <span className="g-add">新增/保留 {diff.gaps.added.length}</span>
            {diff.gaps.resolved.slice(0, 2).map((g, i) => <span className="g-item" key={`r-${i}`}>解决：{g}</span>)}
            {diff.gaps.added.slice(0, 2).map((g, i) => <span className="g-item" key={`a-${i}`}>仍需验证：{g}</span>)}
            {!diff.gaps.resolved.length && !diff.gaps.added.length ? <span className="g-item">两版没有显式缺口变化；请结合回填指标或产物变化判断。</span> : null}
          </div>
          <div className="pi-diff-note">
            <em>证据来源</em>
            {(diff.evidence.to.length ? diff.evidence.to : ["暂无可展示来源"]).map((item, i) => <span className="pi-diff-chip" key={`${item}-${i}`}>{String(item).slice(0, 28)}</span>)}
          </div>
          {diff.evidence_delta ? (
            <div className="pi-diff-note">
              <em>证据变化</em>
              {(diff.evidence_delta.added || []).slice(0, 3).map((item) => <span className="pi-diff-chip up" key={`added-${item.ref}`}>新增 {item.ref}</span>)}
              {(diff.evidence_delta.removed || []).slice(0, 2).map((item) => <span className="pi-diff-chip" key={`removed-${item.ref}`}>移除 {item.ref}</span>)}
              {(diff.evidence_delta.strengthened || []).slice(0, 2).map((item) => <span className="pi-diff-chip up" key={`strong-${item.ref}`}>增强 {item.ref}</span>)}
              {!(diff.evidence_delta.added || []).length && !(diff.evidence_delta.removed || []).length && !(diff.evidence_delta.strengthened || []).length
                ? <span className="pi-diff-chip">{diff.decisionDelta?.summary || "暂无可比较的新证据"}</span>
                : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* 指标回填编辑器（提取后展开） */}
      {!open ? null : loading ? (
        <div className="pi-loading"><Loader2 className="spin" size={14} /> 正在从方案中抽取关键指标…</div>
      ) : (
        <div className="pi-editor">
          {true ? (
            <>
              <div className="pi-rows">
                {(metrics || []).map((m, i) => (
                  <div className="pi-row" key={i}>
                    <input className="pi-label-in" value={m.label} placeholder="指标名" title={m.note || ""} onChange={(e) => editMetric(i, { label: e.target.value })} />
                    <input className="pi-val" value={m.value} placeholder="数值" onChange={(e) => editMetric(i, { value: e.target.value })} />
                    <input className="pi-unit" value={m.unit} placeholder="单位" onChange={(e) => editMetric(i, { unit: e.target.value })} />
                    <select className="pi-kind" value={m.kind} onChange={(e) => editMetric(i, { kind: e.target.value })}>
                      <option value="assumption">假设</option>
                      <option value="observed">实测</option>
                      <option value="target">目标</option>
                    </select>
                    <input className="pi-source" value={m.source_ref || ""} placeholder="来源文件或运行 ID" onChange={(e) => editMetric(i, { source_ref: e.target.value })} disabled={m.kind !== "observed"} />
                    <button type="button" className="pi-del" onClick={() => removeMetric(i)} title="移除">✕</button>
                  </div>
                ))}
                {!(metrics && metrics.length) ? <div className="pi-empty-hint">没从这版方案自动抽到量化指标，可在下方「添加指标」手动回填。</div> : null}
              </div>
              <button type="button" className="pi-addmetric" onClick={() => setMetrics((arr) => [...(arr || []), { label: "", value: "", unit: "", kind: "observed", source_ref: "" }])}><Plus size={13} /> 添加指标</button>
              <div className="pi-tip">实测指标需要填写来源文件或运行 ID；没有来源的值会保留为“用户报告、未验证”，不会推动结论升档。</div>
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

function normalizeMarketSource(source, fallbackTitle = "外部网页来源") {
  if (!source) return null;
  if (typeof source === "string") return { url: source, title: source };
  const url = source.url || source.source || source.link || source.href || "";
  const title = source.title || source.name || source.claim || source.snippet || fallbackTitle;
  if (!url && !title) return null;
  return { ...source, url, title };
}

function webSearchFromArtifact(artifact) {
  const market = artifact?.market || {};
  const provenance = market?.tool_provenance?.foundry_native_web_search || {};
  const sources = [];
  const seen = new Set();
  const add = (item, fallbackTitle) => {
    const normalized = normalizeMarketSource(item, fallbackTitle);
    if (!normalized) return;
    const key = normalized.url || normalized.title;
    if (!key || seen.has(key)) return;
    seen.add(key);
    sources.push(normalized);
  };
  (market.sources || []).forEach((s) => add(s));
  (provenance.sources || []).forEach((s) => add(s));
  (market.external_findings || []).forEach((finding) => add(finding, finding?.claim || "市场参考"));
  if (!sources.length && !market?._llm?.error && !market?.positioning_note) return null;
  return {
    name: "foundry_native_web_search",
    count: market.external_findings?.length || sources.length,
    sources,
    mode: market?._llm?.mode,
    verification: market?._llm?.verification,
    error: market?._llm?.error,
    provenance,
    restored_from_artifact: true,
  };
}

function WebSearchPanel({ trace, artifact }) {
  const hit = useMemo(() => {
    let found = null;
    (trace || []).forEach((e) => {
      if (e.event === "tool_result" && e.data?.name === "foundry_native_web_search") found = e.data;
    });
    if (found && Array.isArray(found.sources) && found.sources.length) return found;
    return webSearchFromArtifact(artifact) || found;
  }, [trace, artifact]);
  const sources = (hit?.sources || []).map((s) => normalizeMarketSource(s)).filter((s) => s && (s.url || s.title));
  if (!hit || (!sources.length && !hit.error)) return null;
  const domain = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
  return (
    <section className="websearch-card">
      <div className="ws-head">
        <Globe size={15} />
        <strong>联网检索 · Foundry Web Search</strong>
        <span className="ws-tag">{sources.length} 条外部来源 · market_inferred{hit.restored_from_artifact ? " · 已保存" : ""}</span>
      </div>
      {sources.length ? (
        <div className="ws-list">
          {sources.slice(0, 6).map((s, i) => (
            <a className="ws-item" key={i} href={s.url || undefined} target="_blank" rel="noreferrer" title={s.url || ""}>
              <span className="ws-dot" />
              <span className="ws-title">{String(s.title || s.url).slice(0, 76)}</span>
              <span className="ws-domain">{domain(s.url)}</span>
            </a>
          ))}
        </div>
      ) : (
        <div className="ws-empty">本次联网检索未返回可展示来源；市场判断仍按 market_inferred 处理，不提升为工作区事实。</div>
      )}
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

      <WebSearchPanel trace={trace} artifact={finalArtifact} />

      <Collapsible title="高级分析" hint="运行更深入的模型与仿真分析">
        <section className="studio-methods">
          <ActionPlanCards selected={selectedPlaybook} onSelect={setSelectedPlaybook} feasibility={feasibility} workspaceId={dashboard?.workspace_id || dashboard?.workspace?.workspace_id} />
          <ActionBoard artifact={finalArtifact} selectedPlaybook={selectedPlaybook} onProduce={onProduce} producing={producing} />
        </section>
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
  if (!audit || !hasAnalysis) {
    return (
      <section className="audit-card is-placeholder">
        <div className="audit-head">
          <ShieldCheck size={16} />
          <div className="audit-title">
            <strong>独立审计 · 待运行</strong>
            <span>完成一次分析后，这里会展示盲判复核、降档原因和复修结果。</span>
          </div>
          <span className="audit-badge">待审计</span>
        </div>
        <div className="audit-revision unchanged">
          <span className="av-from">初判</span>
          <span className="av-arrow">→</span>
          <span className="av-to">审计结论</span>
          <em>等待真实证据进入审计链路</em>
        </div>
      </section>
    );
  }
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
  const displayItems = items.length >= 3
    ? items
    : Object.keys(DIMENSION_LABELS).slice(0, 5).map((name) => ({ name, score: 0 }));
  const size = 192;
  const cx = size / 2;
  const cy = size / 2;
  const R = size * 0.31;
  const n = displayItems.length;
  const pt = (i, r) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const ring = (k) => displayItems.map((_, i) => pt(i, R * k).map((v) => v.toFixed(1)).join(",")).join(" ");
  const sc = (d) => Math.max(0, Math.min(5, Number(d.score || 0)));
  const shape = displayItems.map((d, i) => pt(i, R * (sc(d) / 5)).map((v) => v.toFixed(1)).join(",")).join(" ");
  return (
    <svg className={`verdict-radar ${items.length < 3 ? "is-placeholder" : ""}`} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="五维雷达图">
      {[0.25, 0.5, 0.75, 1].map((k) => (
        <polygon key={k} points={ring(k)} fill="none" stroke="#e5e7eb" strokeWidth="1" />
      ))}
      {displayItems.map((_, i) => {
        const [x, y] = pt(i, R);
        return <line key={i} x1={cx} y1={cy} x2={x.toFixed(1)} y2={y.toFixed(1)} stroke="#e5e7eb" strokeWidth="1" />;
      })}
      <polygon points={shape} fill="rgba(37,99,235,0.14)" stroke="#2563eb" strokeWidth="2" />
      {displayItems.map((d, i) => {
        const [x, y] = pt(i, R * (sc(d) / 5));
        return <circle key={i} cx={x.toFixed(1)} cy={y.toFixed(1)} r="3" fill="#2563eb" />;
      })}
      {displayItems.map((d, i) => {
        const [lx, ly] = pt(i, R + 16);
        return (
          <text key={i} x={lx.toFixed(1)} y={ly.toFixed(1)} fontSize="10.5" fill="#6e6e73" textAnchor="middle" dominantBaseline="middle">
            {(DIMENSION_LABELS && DIMENSION_LABELS[d.name]) || d.name}
          </text>
        );
      })}
      {items.length < 3 ? <text x={cx} y={cy} fontSize="12" fill="#6e6e73" textAnchor="middle">等待分析</text> : null}
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
            {downgrade?.kind === "dimension" ? (
              <span>审计已将{downgrade.dimension || "一项评分"}从 {downgrade.score_before} 调整为 {downgrade.score_after}，因为{downgradeReason}。</span>
            ) : (
              <span>审计已将结论从 {beforeLabel} 降为 {afterLabel}，因为{downgradeReason}。</span>
            )}
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
  const presentation = useAgentPresentation(trace, running);
  return (
    <main className="agent-studio conversation-stage">
      <section className="conversation-head">
        <div>
          <span className="eyeless-label">Conversation</span>
          <h1>AI Agent 会话</h1>
          <p>围绕「{workspace.name || "当前工作区"}」直接提问，Agent 会结合工作区证据、记住整段对话来回答，不把外部来源当作内部事实。</p>
        </div>
      </section>
      <QuestionStarter onRun={onRun} running={running} />
      <AnswerPanel messages={messages} streamText={streamText} running={running} presentation={presentation} onRun={onRun} onProduce={onProduce} producing={producing} trace={trace} onStop={onStop} />
      <Composer input={input} setInput={setInput} running={running} onRun={onRun} onStop={onStop} selectedPlaybook={selectedPlaybook} />
    </main>
  );
}

const OUTPUT_PRODUCTS = [
  { id: "pdf", icon: FileText, cls: "prod-pdf", title: "项目文档", desc: "下载完整的项目提案，包含封面、结论、五维评分与详细分析。", tags: ["封面与概览", "五维评分", "详细分析", "附录与数据来源"] },
  { id: "concept_image", icon: ImagePlus, cls: "prod-img", title: "概念图", desc: "生成产品概念图与视觉参考，助力定位与汇报展示。", tags: ["概念视觉", "Logo 占位", "配色参考", "场景示意"] },
  { id: "audio_summary", icon: Mic, cls: "prod-audio", title: "语音摘要", desc: "将关键结论与建议生成语音摘要，便于快速收听与分享。", tags: ["关键结论", "行动建议", "风险提示", "时长 2-5 分钟"] },
  { id: "roadmap", icon: Route, cls: "prod-route", title: "路线图 / 验证计划", desc: "生成路线图与验证计划，明确关键里程碑与验证实验。", tags: ["阶段路线图", "关键里程碑", "验证实验", "资源与风险"] },
];
function ArtifactsCenter({ dashboard, artifacts, artifact, artifactRefreshKey = 0, onProduce, producing, onUploadReference }) {
  const hasAnalysis = Boolean(artifact?.feasibility?.verdict);
  const workspaceId = dashboard?.workspace_id || dashboard?.workspace?.workspace_id || "";
  const [recent, setRecent] = useState(null);
  const [artifactJobs, setArtifactJobs] = useState([]);
  const [dirOpen, setDirOpen] = useState(false);
  const [lightbox, setLightbox] = useState(null);
  const reloadRecent = React.useCallback(() => {
    if (!workspaceId) return;
    loadArtifactsList(workspaceId)
      .then((d) => { setRecent(d.artifacts || []); setArtifactJobs(d.jobs || []); })
      .catch(() => { setRecent([]); setArtifactJobs([]); });
  }, [workspaceId]);
  useEffect(() => { reloadRecent(); }, [reloadRecent, producing, artifactRefreshKey]);
  const generatedItems = useMemo(() => {
    const typeByKind = { pdf: "pdf", concept_image: "png", audio_summary: "mp3", pilot_plan: "md", action_plan: "md", roadmap: "md", validation_plan: "md" };
    return Object.entries(artifacts || {})
      .filter(([, value]) => value && !value.error && artifactLink(value))
      .map(([kind, value]) => {
        const href = artifactLink(value);
        const rawName = value.name || value.filename || value.title || decodeURIComponent(String(href || "").split("/").pop() || kind);
        return {
          ...value,
          kind,
          name: rawName,
          type: value.type || typeByKind[kind] || kind,
          url: value.url || value.artifact_url,
          artifact_url: value.artifact_url || value.url,
          status: value.status || "ready",
          created_at: value.created_at || new Date().toISOString(),
          local_generated: true,
        };
      });
  }, [artifacts]);
  const recentItems = useMemo(() => {
    const seen = new Set();
    return [...generatedItems, ...((recent || []))]
      .filter((item) => {
        const key = artifactLink(item) || item.url || item.artifact_url || item.name;
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
      });
  }, [generatedItems, recent]);
  const isImage = (a) => /^(png|jpg|jpeg|webp|image)$/i.test(String(a.type || "")) || /\.(png|jpe?g|webp)$/i.test(String(a.name || ""));
  const visibleJobs = artifactJobs.filter((job) => job.status !== "completed").slice(0, 4);
  const openArtifact = (a) => { const href = artifactLink(a); if (!href) return; if (isImage(a)) setLightbox(href); else window.open(href, "_blank", "noopener"); };
  return (
    <main className="agent-studio outputs-stage">
      <header className="conv-head">
        <span className="eyeless-label">Outputs</span>
        <h1>产物中心</h1>
        <p>所有产物均基于当前工作区的分析与结论自动生成，支持下载、分享与重新生成。</p>
      </header>

      <section className="card out-status">
        <div className="os-item"><div className="os-ic blue"><Sparkles size={18} /></div><div><span>当前分析状态</span><b>{hasAnalysis ? "分析已完成" : "等待分析"}</b><em>{producing ? "正在生成产物，请勿重复点击" : "产物会基于最近一次真实分析生成"}</em></div></div>
        <div className="os-item"><div className={hasAnalysis ? "os-ic ok" : "os-ic amber"}><ShieldCheck size={18} /></div><div><span>审核状态</span><b className={hasAnalysis ? "ok" : ""}>{hasAnalysis ? "已通过" : "待审计"}</b><em>{hasAnalysis ? "基于最近一次分析结果" : "完成分析后才会进入审计"}</em></div></div>
        <div className="os-item"><div className="os-ic amber"><Lightbulb size={18} /></div><div><span>建议操作</span><b>上传透明 PNG Logo</b><em>建议上传透明 PNG 格式 Logo，以生成更专业的概念图</em></div></div>
        <button className="dw-btn" type="button" onClick={onUploadReference}><UploadCloud size={15} />上传 Logo</button>
      </section>

      <div className="out-body">
        <section className="out-main">
          <div className="out-sec-h">可生成的产物</div>
          {OUTPUT_PRODUCTS.map((p) => {
            const Ic = p.icon;
            return (
              <article className="card out-prod" key={p.id}>
                <div className={`out-prod-ic ${p.cls}`}><Ic size={22} /></div>
                <div className="out-prod-main">
                  <b>{p.title}</b>
                  <p>{p.desc}</p>
                  <div className="out-tags">{p.tags.map((t) => <span key={t} className="out-tag">{t}</span>)}</div>
                </div>
                <div className="out-prod-r">
                  <span className="out-badge"><span className="d" />{producing ? "生成中" : hasAnalysis ? "可生成" : "需先分析"}</span>
                  <button className="dw-btn primary" type="button" disabled={producing} onClick={() => onProduce && onProduce([p.id])}>
                    {producing ? <Loader2 size={15} className="spin" /> : <Sparkles size={15} />}生成
                  </button>
                </div>
              </article>
            );
          })}
          <p className="out-note"><Info size={13} /> 以上产物均基于当前分析结果生成。若底层数据或分析结论发生变化，请重新生成以确保准确性。</p>
        </section>

        <aside className="card out-recent">
          <div className="cardhead"><span className="t">最近产物</span><button type="button" className="lnk lnk-btn" onClick={reloadRecent}>刷新</button></div>
          {visibleJobs.length ? (
            <div className="out-job-list">
              {visibleJobs.map((job) => (
                <div className="out-job" key={job.job_id}>
                  <span className={`out-job-dot ${job.status}`} />
                  <div><b>{job.display_name || "产物任务"}</b><em>{(job.requested_kinds || []).map((kind) => ARTIFACT_KIND_LABEL[kind] || kind).join(" / ")}</em></div>
                  <span className={`dw-chip ${["queued", "running"].includes(job.status) ? "probing" : job.status === "partial" ? "warn" : ""}`}>
                    {["queued", "running"].includes(job.status) ? "生成中" : job.status === "partial" ? "部分完成" : "可重试"}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
          <div className="out-recent-list">
            {recent === null ? <p className="empty-copy" style={{ padding: 16 }}><Loader2 size={14} className="spin" /> 加载产物…</p> : null}
            {recent !== null && !recentItems.length ? <p className="empty-copy" style={{ padding: 16 }}>暂无产物。生成后会在这里出现。</p> : null}
            {recentItems.slice(0, 6).map((a, i) => (
              <button type="button" className="out-rec" key={i} onClick={() => openArtifact(a)} title="点击在线查看">
                <FileTypeIcon doc={{ name: a.name }} size={22} />
                <div className="out-rec-main"><b>{a.name}</b><em>{(a.type || "").toUpperCase()}{a.bytes ? ` · ${formatBytes(a.bytes)}` : ""}</em></div>
                <span className="out-rec-time">{formatTime(a.created_at)}</span>
                <span className={`dw-chip ${a.status === "ready" || !a.status ? "ok" : ""}`}>{a.status === "ready" || !a.status ? "已完成" : a.status}</span>
              </button>
            ))}
          </div>
          <button type="button" className="out-open" onClick={() => setDirOpen(true)}><FolderOpen size={15} />打开产物目录<ChevronRight size={14} /></button>
        </aside>
      </div>

      <ImageLightbox src={lightbox} onClose={() => setLightbox(null)} />
      <SideDrawer open={dirOpen} title="产物目录" onClose={() => setDirOpen(false)}>
        {!recentItems.length ? <p className="empty-copy">该工作区暂无产物。</p> : (
          <div className="drawer-list">
            {recentItems.map((a, i) => {
              const href = artifactLink(a);
              return (
                <a className="drawer-row" key={i} href={href || undefined} target="_blank" rel="noreferrer" style={href ? undefined : { pointerEvents: "none", opacity: .6 }}>
                  <FileTypeIcon doc={{ name: a.name }} size={20} />
                  <div className="drawer-row-main"><b>{a.name}</b><em>{(a.type || "").toUpperCase()}{a.bytes ? ` · ${formatBytes(a.bytes)}` : ""} · {formatTime(a.created_at)}</em></div>
                  {href ? <Download size={15} /> : null}
                </a>
              );
            })}
          </div>
        )}
      </SideDrawer>
    </main>
  );
}

// 通用右侧抽屉
function SideDrawer({ open, title, onClose, children }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="drawer-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <aside className="side-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head"><strong>{title}</strong><button type="button" className="icon-button" onClick={onClose}><X size={17} /></button></div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}

function mafAgentMeta(id) {
  return AGENTS.find((agent) => agent.id === id) || { id, zh: String(id || "Agent").replace(/^df-/, ""), role: "", icon: Activity };
}

function MafCollaborationView({ model, compact = false }) {
  if (!model) return null;
  const modeLabel = MAF_MODES.find((item) => item.id === model.mode)?.label || model.mode || "未记录";
  return (
    <div className={`maf-collab ${compact ? "compact" : ""}`}>
      <div className="maf-collab-head">
        <span><Workflow size={14} /><strong>MAF 动态协作</strong></span>
        <em>{modeLabel}{model.maxRevisions != null ? ` · 最多复修 ${model.maxRevisions} 轮` : ""}</em>
      </div>
      <div className="maf-mode-segments" aria-label="协作模式">
        {MAF_MODES.map((item) => <span className={item.id === model.mode ? "active" : ""} key={item.id}>{item.label}</span>)}
      </div>
      <div className="maf-participants">
        <div className="maf-section-label">实际参与</div>
        <div className="maf-agent-strip" role="list">
          {model.agents.map((agent) => {
            const meta = mafAgentMeta(agent.id);
            const Icon = meta.icon || Activity;
            const status = mafStatusLabel(agent.status);
            const tokenTotal = agent.tokens?.total ?? agent.tokens?.total_tokens;
            return (
              <div className={`maf-agent-state ${agent.tone}`} role="listitem" key={agent.id}>
                <span className="maf-agent-icon">{agent.tone === "running" ? <Loader2 className="spin" size={14} /> : <Icon size={14} />}</span>
                <span><b>{meta.zh}</b><small className={`maf-status ${agent.tone}`}>{status}</small></span>
                <em>
                  {agent.durationMs != null ? formatTraceDuration(agent.durationMs) : ""}
                  {tokenTotal != null ? `${agent.durationMs != null ? " · " : ""}${Number(tokenTotal).toLocaleString()} tokens` : ""}
                  {agent.retries != null ? ` · 重试 ${agent.retries}` : ""}
                </em>
                {agent.error ? <i>{String(agent.error)}</i> : null}
                {agent.tools.length ? <i className="tools">工具：{agent.tools.join("、")}</i> : null}
              </div>
            );
          })}
          {!model.agents.length ? <span className="maf-empty">未记录参与 Agent</span> : null}
        </div>
        {model.skippedAgents.length ? <p className="maf-skipped">未调用：{model.skippedAgents.map((id) => mafAgentMeta(id).zh).join("、")}</p> : null}
      </div>
      {model.branches.length ? (
        <div className="maf-collab-section">
          <div className="maf-section-label"><Users size={13} /> 并行分支</div>
          <div className="maf-branch-lanes">
            {model.branches.map((branch) => (
              <div className={`maf-branch ${String(branch.status || "unknown").toLowerCase()}`} key={branch.id}>
                <span className="maf-branch-line" />
                <b>{branch.id}</b>
                <span>{branch.agentId ? mafAgentMeta(branch.agentId).zh : "Agent 未记录"}</span>
                <em className={`maf-status ${mafStatusTone(branch.status)}`}>{mafStatusLabel(branch.status)}{branch.durationMs != null ? ` · ${formatTraceDuration(branch.durationMs)}` : ""}</em>
                {branch.error ? <i>{String(branch.error)}</i> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {model.handoffs.length ? (
        <div className="maf-collab-section">
          <div className="maf-section-label"><Route size={13} /> 任务交接</div>
          <div className="maf-handoffs">
            {model.handoffs.map((handoff, index) => (
              <div className="maf-handoff" key={`${handoff.source}-${handoff.target}-${index}`}>
                <b>{mafAgentMeta(handoff.source).zh}</b><ArrowUpRight size={14} /><b>{mafAgentMeta(handoff.target).zh}</b>
                <span className={`maf-status ${mafStatusTone(handoff.status)}`}>{mafStatusLabel(handoff.status)}</span>
                {handoff.reasons.length ? <em>{handoff.reasons.join(" · ")}</em> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {model.reviews.length ? (
        <div className="maf-collab-section">
          <div className="maf-section-label"><ShieldCheck size={13} /> 复核轮次</div>
          <div className="maf-review-rounds">
            {model.reviews.map((review) => (
              <div className="maf-review-round" key={review.round}>
                <b>第 {review.round} 轮</b>
                <span>{review.verdict || mafStatusLabel(review.status)}</span>
                <em className={`maf-status ${mafStatusTone(review.status)}`}>{mafStatusLabel(review.status)}</em>
                {review.error ? <i>{String(review.error)}</i> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {model.fallback ? (
        <div className="maf-fallback"><AlertTriangle size={14} /><span><b>已记录回退</b><em>{model.fallback.error_category || model.fallback.reason || model.fallback.status || "原因未记录"}</em></span></div>
      ) : null}
    </div>
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
const SETTINGS_ICON_SRCS = [
  ...Object.values(SVC_ICONS),
  "/icons/foundry.svg",
  "/icons/ai-search.svg",
  "/icons/azure-blob.svg",
  "/icons/speech.svg",
  "/icons/content-safety.svg",
];
function ObsIcon({ name }) {
  return <img className="svc-ic" src={SVC_ICONS[name]} alt="" width="22" height="22" aria-hidden="true" />;
}

function formatTraceDuration(ms) {
  const n = Number(ms || 0);
  if (!n) return "";
  if (n >= 60000) return `${Math.floor(n / 60000)} 分 ${Math.round((n % 60000) / 1000)} 秒`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)} 秒`;
  return `${Math.round(n)} ms`;
}

const TRACE_EVENT_LABELS = {
  ready: "接收请求",
  user: "读取输入",
  route: "识别意图",
  followup: "跟进回复",
  followup_edit: "跟进回复",
  plan: "规划路径",
  role_change: "切换 Agent",
  tool_call: "调用工具",
  tool_result: "工具返回",
  model_response: "模型响应",
  audit: "审计检查",
  final: "生成结果",
  clarify: "澄清问题",
  followup: "跟进回复",
  progress: "执行进度",
  cache: "读取缓存",
  blind_verdict: "生成初判",
  maf_workflow: "多智能体编排",
  maf_plan: "MAF 协作计划",
  maf_agent_started: "MAF Agent 启动",
  maf_agent_completed: "MAF Agent 完成",
  maf_agent_failed: "MAF Agent 异常",
  maf_branch_started: "MAF 分支启动",
  maf_branch_joined: "MAF 分支汇合",
  maf_handoff: "MAF 任务交接",
  maf_review: "MAF 复核",
  maf_fallback: "MAF 回退",
  error: "异常事件",
};

function traceEventLabel(event) {
  const raw = String(event || "").trim();
  const key = raw.toLowerCase();
  return TRACE_EVENT_LABELS[key] || raw.replace(/_/g, " ") || "执行事件";
}

function traceStatusLabel(status) {
  const raw = String(status || "").trim();
  const key = raw.toLowerCase();
  if (!raw) return "未记录";
  if (["done", "ok", "success", "completed", "complete", "joined"].includes(key)) return "完成";
  if (["followup_edit", "followup"].includes(key)) return "完成";
  if (["running", "pending", "started", "streaming"].includes(key)) return "进行中";
  if (["error", "failed", "fail"].includes(key)) return "异常";
  return raw;
}

function runStatusLabel(status, okRun) {
  const raw = String(status || "").trim();
  const key = raw.toLowerCase();
  if (!raw) return okRun ? "成功" : "运行中";
  if (["done", "ok", "success", "completed", "complete"].includes(key)) return "成功";
  if (["followup_edit", "followup"].includes(key)) return "轻量跟进完成";
  if (["running", "pending", "started", "streaming"].includes(key)) return "运行中";
  if (["error", "failed", "fail"].includes(key)) return "异常";
  return raw;
}

function auditStatusLabel(status, runStatus) {
  const raw = String(status || "").trim();
  const key = raw.toLowerCase();
  const routeKey = String(runStatus || "").trim().toLowerCase();
  if (!raw || key === "pass" || key === "passed") return "通过";
  if (key === "unknown") return ["followup_edit", "followup"].includes(routeKey) ? "轻量跟进" : "未记录";
  if (key === "warn" || key === "warning") return "有提示";
  if (key === "fail" || key === "failed") return "未通过";
  return raw;
}

function auditStatusSub(status, runStatus, audit) {
  const key = String(status || "").trim().toLowerCase();
  const routeKey = String(runStatus || "").trim().toLowerCase();
  if (key === "unknown" && ["followup_edit", "followup"].includes(routeKey)) return "跟进轮未触发完整审计";
  return `风险项 ${audit.risks ?? 0} / 告警 ${audit.warnings ?? 0}`;
}

function runEvidenceLabel(value, fallback = "run_store") {
  const raw = String(value || "").trim();
  if (!raw) return fallback;
  return raw
    .replace("run.started_at -> run.completed_at", "started_at / completed_at")
    .replace("run.started_at -> run.updated_at", "started_at / updated_at")
    .replace("run.models[].usage or steps[].data.usage", "模型 usage 汇总")
    .replace("run.models[].usage", "模型 usage 汇总")
    .replace("run.steps[].data.usage", "步骤 usage 汇总")
    .replace("unique run.steps[].data.agent / target_expert / name", "步骤里的 Agent 标识")
    .replace("run.steps event=tool_call/tool_result", "工具调用步骤")
    .replace("run.steps", "运行步骤");
}

function cleanTraceSummary(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (/^[a-z_]+:\s*$/i.test(text)) return "";
  if (/^route:\s*(unknown)?$/i.test(text)) return "";
  if (/^route:\s*followup_edit$/i.test(text)) return "已识别为跟进讨论，采用轻量路径。";
  if (/^model:\s*[\w-]+(?:\s+tokens=0)?$/i.test(text)) return "";
  if (/^tool_result:\s*workspace_evidence$/i.test(text)) return "已汇总当前工作区证据与数据画像。";
  if (/^(progress|进度)[:：]\s*running$/i.test(text)) return "正在处理当前跟进任务。";
  return text;
}

function traceDetailText(detail) {
  const event = String(detail?.rawEvent || detail?.event || "").toLowerCase();
  const agent = String(detail?.agent || detail?.name || "").trim();
  const tool = String(detail?.toolName || "").trim();
  if (event === "ready") return "已建立运行通道，并创建本次运行上下文。";
  if (event === "user") return "已接收用户输入，准备进入意图识别与任务拆解。";
  if (event === "route") return "已识别任务类型，并选择后续执行路径。";
  if (event === "plan") return "已拆解执行步骤，并准备分配给相关 Agent。";
  if (event === "role_change") return agent ? `切换到 ${agent}，开始处理该阶段任务。` : "已切换到下一位 Agent 处理该阶段任务。";
  if (event === "tool_call") return tool ? `发起工具调用：${tool}。` : "已发起检索、数据或生成类工具调用。";
  if (event === "tool_result") return "工具已返回结果，输出进入后续分析。";
  if (event === "model_response") return agent ? `${agent} 已完成模型响应。` : "模型响应已完成并写入本轮上下文。";
  if (event === "audit") return "审计环节已检查证据一致性与结论边界。";
  if (event === "final") return "最终回答已生成，并写入运行结果。";
  if (event === "clarify") return "当前信息不足，已转入澄清问题。";
  if (event === "followup") return "已根据当前工作区上下文生成轻量跟进回复。";
  if (event.includes("error")) return "该步骤出现异常，详情可查看完整日志。";
  return "已记录该阶段的执行事件。";
}

function traceDetailTitle(detail, index) {
  const label = traceEventLabel(detail?.rawEvent || detail?.event || detail?.status);
  return `${index + 1}. ${label}${detail?.dur ? ` · ${detail.dur}` : ""}`;
}

function traceStepView(step) {
  const eventData = mafEventData(step);
  const agentId = String(step?.agent || step?.agent_id || eventData.agent_id || eventData.agent || "").trim();
  const role = String(step?.role || step?.event || "").trim();
  if (agentId) {
    const meta = AGENTS.find((item) => item.id === agentId);
    const writer = agentId.toLowerCase().includes("answer") || agentId.toLowerCase().includes("writer");
    return {
      key: `agent:${agentId}`,
      icon: traceIcon(agentId || role),
      name: meta?.zh || (writer ? "回答撰写" : agentId),
      role: meta?.role || (writer ? "结构化输出" : role),
    };
  }
  const s = role.toLowerCase();
  if (["ready", "user"].includes(s)) return { key: "system:entry", icon: Workflow, name: "运行入口", role: "请求接收" };
  if (["route", "plan"].includes(s)) return { key: "system:route", icon: Route, name: "路由与计划", role: "任务拆解" };
  if (s.includes("followup")) return { key: "system:followup", icon: Activity, name: "跟进回复", role: "轻量路径" };
  if (s.includes("audit") || s.includes("revised")) return { key: "system:audit", icon: ShieldCheck, name: "审计与修订", role: "证据校验" };
  if (s.includes("tool")) return { key: "system:tools", icon: Wrench, name: "工具调用", role: "外部能力" };
  if (s.includes("final")) return { key: "system:final", icon: FileText, name: "最终输出", role: "结果汇总" };
  if (s.includes("error")) return { key: "system:error", icon: AlertTriangle, name: "异常事件", role: "运行告警" };
  return { key: `system:${s || "event"}`, icon: Activity, name: "系统事件", role: role || "运行记录" };
}

function groupTraceRows(items) {
  const groups = [];
  const seen = new Map();
  for (const item of items || []) {
    const key = item.groupKey || `${item.name || "Agent"}::${item.role || ""}`;
    let group = seen.get(key);
    if (!group) {
      group = {
        key,
        icon: item.icon,
        name: item.name || "Agent",
        role: item.role || "",
        status: item.status || "未记录",
        statusTone: item.statusTone || "neutral",
        sum: item.sum || "",
        durationMs: 0,
        details: [],
      };
      seen.set(key, group);
      groups.push(group);
    }
    group.status = item.status || group.status || "未记录";
    group.statusTone = item.statusTone || group.statusTone || "neutral";
    if (!group.sum && item.sum) group.sum = item.sum;
    group.durationMs += Number(item.durationMs || 0);
    group.details.push(item);
  }
  return groups.map((group) => ({
    ...group,
    dur: group.durationMs ? formatTraceDuration(group.durationMs) : (group.details.length === 1 ? group.details[0].dur : ""),
  }));
}

function GovernanceInlineState({ loading, error, empty, emptyText, onRetry, children }) {
  if (loading) return <div className="gov-inline-state loading"><Loader2 size={14} className="spin" /><span>正在读取服务端证据</span></div>;
  if (error) {
    return (
      <div className="gov-inline-state error" role="alert">
        <AlertTriangle size={14} />
        <span>{error}</span>
        <button type="button" onClick={onRetry}><RefreshCw size={13} />重试</button>
      </div>
    );
  }
  if (empty) return <div className="gov-inline-state"><Info size={14} /><span>{emptyText || "未记录"}</span></div>;
  return children;
}

function GovernanceSectionHead({ icon: Icon, title, description, badge }) {
  return (
    <div className="gov-section-head">
      <div className="gov-section-title"><Icon size={16} /><div><h3>{title}</h3><p>{description}</p></div></div>
      {badge ? <span className={`gov-status ${badge.tone || "neutral"}`}>{badge.label}</span> : null}
    </div>
  );
}

const auditActionLabels = {
  "file.create": "创建文件",
  "file.edit": "编辑文件",
  "file.delete": "删除文件",
  "analysis.run": "运行分析",
  "message.create": "创建消息",
  "member.update": "更新成员",
  "member.remove": "移除成员",
  "invitation.create": "创建邀请",
  "invitation.send": "发送邀请",
  "invitation.fail": "邀请失败",
  "invitation.revoke": "撤销邀请",
  "connector.sync": "同步连接器",
  "artifact.generate": "生成产物",
};

function GovernanceSummaryPanel({ data, invitationState, permissionsPayload, permissionState, windowValue, onWindowChange, onRetry, onInvitationRetry, onPermissionRetry, onLoadMore, loadingMore }) {
  const trace = traceViewModel(data.trace || {});
  const roi = roiViewModel({ local: data.roi || {} });
  const chargeback = chargebackViewModel(data.chargeback || {});
  const permissions = governancePermissions(permissionsPayload || {});
  const invitations = invitationLifecycleViewModel(invitationState.data || {});
  const auditEvents = (data.audit?.events || []).map(auditEventViewModel);
  const errors = data.errors || {};
  const traceBadge = data.loading ? null : { label: trace.label, tone: trace.tone };
  const localBadge = data.loading ? null : { label: roi.local.label, tone: roi.local.tone };
  const providerBadge = data.loading ? null : { label: roi.provider.label, tone: roi.provider.tone };
  return (
    <div className="gov-workspace" data-testid="governance-frontend">
      <div className="gov-toolbar">
        <div>
          <h2>治理证据</h2>
          <p>状态来自当前工作区的服务端权限、遥测、ROI、归因和不可变审计接口。</p>
        </div>
        <div className="gov-window" aria-label="治理统计时间范围">
          <label><span>从</span><input type="date" value={windowValue.from} onChange={(event) => onWindowChange({ ...windowValue, from: event.target.value })} /></label>
          <label><span>至（不含）</span><input type="date" value={windowValue.to} onChange={(event) => onWindowChange({ ...windowValue, to: event.target.value })} /></label>
          <button type="button" className="icon-button gov-refresh" title="刷新治理证据" aria-label="刷新治理证据" onClick={onRetry} disabled={data.loading}>
            {data.loading ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />}
          </button>
        </div>
      </div>

      <section className="gov-section" aria-label="Azure 遥测送达">
        <GovernanceSectionHead icon={Activity} title="Azure 遥测送达" description="区分配置、本地发出与远端确认；只有匹配的 Azure Monitor 证据才标记送达。" badge={traceBadge} />
        <GovernanceInlineState loading={data.loading} error={errors.trace} onRetry={onRetry}>
          <dl className="gov-facts">
            <div><dt>配置状态</dt><dd>{trace.state === "not_configured" ? "未配置" : "已配置"}</dd></div>
            <div><dt>本地发出</dt><dd>{trace.localEmitAt ? formatTime(trace.localEmitAt) : "未记录"}</dd></div>
            <div><dt>导出回调</dt><dd>{trace.exporterState === "succeeded" ? "已记录成功" : trace.exporterState === "failed" ? "已记录失败" : "未记录"}</dd></div>
            <div><dt>远端送达</dt><dd>{trace.deliveredAt ? formatTime(trace.deliveredAt) : "未确认"}</dd></div>
          </dl>
          {trace.errorType ? <p className="gov-evidence-note error">验证失败类型：{trace.errorType}</p> : null}
          {trace.transactionUrl ? <a className="gov-external-link" href={trace.transactionUrl} target="_blank" rel="noreferrer">在 Azure Monitor 查看<ArrowUpRight size={13} /></a> : null}
        </GovernanceInlineState>
      </section>

      <div className="gov-roi-band">
        <section className="gov-section" aria-label="本地 ROI">
          <GovernanceSectionHead icon={TrendingUp} title="本地 ROI" description="由工作区运行、价格配置和来源关联结果计算，不接受 Foundry 数值覆盖。" badge={localBadge} />
          <GovernanceInlineState loading={data.loading} error={errors.roi} onRetry={onRetry}>
            <dl className="gov-facts compact">
              <div><dt>业务价值</dt><dd>{roi.local.businessValue.text}</dd></div>
              <div><dt>模型成本</dt><dd>{roi.local.costText}</dd></div>
              <div><dt>Token</dt><dd>{roi.local.tokenText}</dd></div>
              <div><dt>结果证据</dt><dd>{roi.local.outcomeCount ? `${roi.local.outcomeCount} 条` : "未记录"}</dd></div>
            </dl>
            <p className="gov-evidence-note">{roi.local.unverifiedCount ? `${roi.local.unverifiedCount} 条结果尚未独立验证。` : roi.localStatus === "verified" ? "所有窗口内结果均有独立验证记录。" : "当前状态不会因 Foundry 证据而提升。"}</p>
          </GovernanceInlineState>
        </section>

        <section className="gov-section" aria-label="Foundry ROI">
          <GovernanceSectionHead icon={Server} title="Foundry ROI" description="独立展示外部签名快照；未配置、发现验证与数值验证互不替代。" badge={providerBadge} />
          <GovernanceInlineState loading={data.loading} error={errors.roi} onRetry={onRetry}>
            <dl className="gov-facts compact">
              <div><dt>连接证据</dt><dd>{roi.foundryConnectionState === "connected" ? "快照签名已验证" : roi.provider.label}</dd></div>
              <div><dt>业务价值</dt><dd>{roi.provider.businessValue.text}</dd></div>
              <div><dt>供应方状态</dt><dd>{roi.provider.label}</dd></div>
              <div><dt>本地差异</dt><dd>{roi.difference?.amount != null && roi.difference?.currency ? `${roi.difference.currency} ${roi.difference.amount}` : "未生成"}</dd></div>
            </dl>
            <p className="gov-evidence-note">Foundry 记录始终与本地 ROI 分开，不会合并或提升本地证据状态。</p>
          </GovernanceInlineState>
        </section>
      </div>

      <section className="gov-section" aria-label="成员 Token 与成本归因">
        <GovernanceSectionHead icon={Coins} title="成员 Token 与成本归因" description="按可信成员身份和已记录用量归因；未知价格、混合币种和部分证据保持原状态。" badge={!data.loading && permissions.canReadChargeback ? { label: chargeback.totalCostText, tone: chargeback.evidenceStatus === "complete" ? "ok" : "warn" } : null} />
        {permissionState.loading ? (
          <GovernanceInlineState loading />
        ) : permissionState.error ? (
          <GovernanceInlineState error={permissionState.error} onRetry={onPermissionRetry} />
        ) : errors.chargeback && !data.loading ? (
          <GovernanceInlineState error={errors.chargeback} onRetry={onRetry} />
        ) : !permissions.canReadChargeback && !data.loading ? (
          <div className="gov-restricted"><ShieldCheck size={15} /><span>{permissions.reasons["chargeback.read"]}</span></div>
        ) : (
          <GovernanceInlineState loading={data.loading} error={errors.chargeback} empty={!chargeback.rows.length} emptyText="所选时间范围内没有可归因的成员用量。" onRetry={onRetry}>
            <div className="gov-table-wrap">
              <table className="gov-table">
                <thead><tr><th>成员</th><th>Token</th><th>成本与币种</th><th>证据状态</th></tr></thead>
                <tbody>{chargeback.rows.map((row, index) => <tr key={`${row.memberLabel}-${index}`}><td>{row.memberLabel}</td><td>{row.tokenText}</td><td>{row.costText}</td><td><span className={`gov-status ${row.evidenceStatus === "complete" ? "ok" : "warn"}`}>{row.evidenceStatus === "complete" ? "已计价" : row.evidenceStatus === "partial" ? "部分已计价" : "未记录"}</span></td></tr>)}</tbody>
              </table>
            </div>
          </GovernanceInlineState>
        )}
        {chargeback.truncated ? <p className="gov-evidence-note warn">结果已达到服务端读取上限；当前表格不是完整历史。</p> : null}
      </section>

      <section className="gov-section" aria-label="邀请生命周期">
        <GovernanceSectionHead icon={UserPlus} title="邀请生命周期" description="从不可变邀请日志读取历史状态；生命周期不代表访问权限，界面只显示服务端伪名。" />
        {permissionState.loading ? (
          <GovernanceInlineState loading />
        ) : permissionState.error ? (
          <GovernanceInlineState error={permissionState.error} onRetry={onPermissionRetry} />
        ) : !permissions.canReadInvitations ? (
          <div className="gov-restricted"><ShieldCheck size={15} /><span>{permissions.reasons["invitation.read"]}</span></div>
        ) : (
          <GovernanceInlineState loading={invitationState.loading} error={invitationState.error} empty={!invitations.length} emptyText="当前邀请日志尚无历史记录。" onRetry={onInvitationRetry}>
            <div className="gov-table-wrap">
              <table className="gov-table">
                <thead><tr><th>受邀主体</th><th>角色</th><th>状态</th><th>更新时间</th></tr></thead>
                <tbody>{invitations.map((row) => <tr key={row.invitationRef}><td><b>{row.subjectLabel}</b></td><td>{row.role}</td><td><span className={`gov-status ${row.tone}`}>{row.stateLabel}</span></td><td>{row.updatedAt ? formatTime(row.updatedAt) : "未记录"}</td></tr>)}</tbody>
              </table>
            </div>
          </GovernanceInlineState>
        )}
      </section>

      <section className="gov-section" aria-label="不可变审计事件">
        <GovernanceSectionHead icon={ShieldCheck} title="不可变审计事件" description="按服务端游标分页读取；界面不提供更新或删除，并只显示后端假名。" badge={!data.loading && permissions.canReadAudit ? { label: `${auditEvents.length} 条已加载`, tone: "info" } : null} />
        {permissionState.loading ? (
          <GovernanceInlineState loading />
        ) : permissionState.error ? (
          <GovernanceInlineState error={permissionState.error} onRetry={onPermissionRetry} />
        ) : errors.audit && !data.loading ? (
          <GovernanceInlineState error={errors.audit} onRetry={onRetry} />
        ) : !permissions.canReadAudit && !data.loading ? (
          <div className="gov-restricted"><ShieldCheck size={15} /><span>{permissions.reasons["audit.read"]}</span></div>
        ) : (
          <GovernanceInlineState loading={data.loading} error={errors.audit} empty={!auditEvents.length} emptyText="当前工作区尚无不可变审计事件。" onRetry={onRetry}>
            <div className="gov-audit-list">
              {auditEvents.map((event, index) => (
                <article className="gov-audit-row" key={`${event.revision || "event"}-${index}`}>
                  <span className={`gov-result ${event.result}`}>{event.result === "allowed" ? "允许" : event.result === "denied" ? "拒绝" : event.result === "failed" ? "失败" : "未记录"}</span>
                  <div><b>{auditActionLabels[event.action] || event.action}</b><span>{event.actor} · {event.resourceType} / {event.resource}</span></div>
                  <div className="gov-audit-meta"><span>#{event.revision || "-"}</span><time>{event.at ? formatTime(event.at) : "未记录"}</time></div>
                </article>
              ))}
            </div>
          </GovernanceInlineState>
        )}
        {permissions.canReadAudit && errors.auditPage ? <GovernanceInlineState error={errors.auditPage} onRetry={onLoadMore} /> : null}
        {permissions.canReadAudit && data.audit?.has_more && !data.auditRetryCursor ? <button type="button" className="gov-load-more" onClick={onLoadMore} disabled={loadingMore}>{loadingMore ? <Loader2 size={14} className="spin" /> : <ChevronDown size={14} />}加载更早事件</button> : null}
      </section>
    </div>
  );
}

function RunsCenter({ dashboard, trace, running, observability, onOpenConversation, tasks }) {
  const runs = dashboard?.runs || [];
  const workspaceId = dashboard?.workspace_id || dashboard?.workspace?.workspace_id || "";
  const r = runs[0] || {};
  const runId = r.run_id || r.conversation_id || "";
  const [q, setQ] = useState("");
  const [histExpanded, setHistExpanded] = useState(false);
  const [histPage, setHistPage] = useState(0);
  const [summary, setSummary] = useState(null);
  const [rtrace, setRtrace] = useState(null);
  const [tracePage, setTracePage] = useState(0);
  const [traceOpen, setTraceOpen] = useState({});
  const [logOpen, setLogOpen] = useState(false);
  const [logText, setLogText] = useState("");
  const runMaf = useMemo(() => deriveMafViewModel(rtrace || [], summary?.maf), [rtrace, summary?.maf]);
  useEffect(() => {
    if (!runId) return;
    setSummary(null); setRtrace(null); setTracePage(0); setTraceOpen({});
    loadRunSummary(runId).then(setSummary).catch(() => {});
    loadRunTrace(runId).then((d) => setRtrace(Array.isArray(d) ? d : (d?.trace || []))).catch(() => {});
  }, [runId]);
  const openLog = () => {
    setLogOpen(true); setLogText("");
    loadRunLog(runId, "text").then((d) => setLogText(typeof d === "string" ? d : (d?.text || JSON.stringify(d, null, 2)))).catch((e) => setLogText(`加载日志失败：${e.message}`));
  };
  const okRun = r.status === "done" || Boolean(r.completed_at) || (!r.status && Boolean(r.verdict));
  const verdictLabel = r.verdict ? (VERDICT_LABELS[r.verdict] || r.verdict) : "未记录";
  const t = observability?.tracing || {};
  const models = observability?.models || {};
  const cg = observability?.eval?.calibration_gate || null;
  const sm = summary || {};
  const startedAt = sm.started_at || r.started_at || r.created_at;
  const finishedAt = sm.finished_at || r.finished_at || r.completed_at || r.updated_at;
  const directDuration = Number(sm.duration_ms || r.duration_ms || 0);
  const rangedDuration = startedAt && finishedAt ? new Date(finishedAt) - new Date(startedAt) : 0;
  const smDur = directDuration > 0
    ? formatTraceDuration(directDuration)
    : rangedDuration > 0
      ? formatTraceDuration(rangedDuration)
      : (okRun ? "未记录" : "计算中");
  const tc = sm.tool_calls || {};
  const tk = sm.tokens || {};
  const au = sm.audit || {};
  const basis = sm.evidence || {};
  const statusValue = runStatusLabel(sm.status || r.status, okRun);
  const auditValue = auditStatusLabel(au.status, sm.status || r.status);
  const cards = [
    { ic: CheckCircle2, tone: "ok", label: "当前运行状态", value: statusValue, sub: sm.finished_at ? `完成于 ${formatTime(sm.finished_at)}` : r.completed_at ? `完成于 ${formatTime(r.completed_at)}` : "", basis: runEvidenceLabel(basis.source, "run_store") },
    { ic: Target, tone: "blue", label: "结论", value: sm.verdict ? (VERDICT_LABELS[sm.verdict] || sm.verdict) : verdictLabel, sub: `置信度 ${sm.confidence || r.confidence || "未记录"}`, basis: "artifact.feasibility" },
    { ic: Clock3, label: "总耗时", value: smDur, sub: sm.started_at ? `开始于 ${formatTime(sm.started_at)}` : "", basis: runEvidenceLabel(basis.duration) },
    { ic: Users, label: "Agent 数量", value: sm.agent_count != null ? String(sm.agent_count) : "未记录", sub: sm.agent_count != null ? "按真实步骤去重" : "等待后端写入", basis: runEvidenceLabel(basis.agent_count) },
    { ic: Wrench, label: "工具调用", value: tc.total != null ? String(tc.total) : "未记录", sub: tc.total != null ? `成功 ${tc.ok ?? 0} / 失败 ${tc.fail ?? 0}` : "等待工具步骤", basis: runEvidenceLabel(basis.tool_calls) },
    { ic: Coins, label: "Token 用量", value: (tk.total != null ? tk.total.toLocaleString() : "未记录"), sub: tk.total != null ? `Prompt ${tk.prompt ?? 0} / Completion ${tk.completion ?? 0}` : "等待模型 usage", basis: runEvidenceLabel(basis.tokens) },
    { ic: ShieldCheck, tone: "ok", label: "审计状态", value: auditValue, sub: auditStatusSub(au.status, sm.status || r.status, au), basis: "artifact.audit" },
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
        <a className="ghost-button icon-label" href={runId ? runLogUrl(runId, "text") : undefined} target="_blank" rel="noreferrer" style={runId ? undefined : { pointerEvents: "none", opacity: .5 }}><Download size={15} />导出日志</a>
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
              {c.basis ? <small className="runc-basis">依据：{c.basis}</small> : null}
            </div>
          );
        })}
      </div>

      <div className="run-mid">
        <section className="card obs2">
          <div className="obs2-head"><strong>可观测性集成</strong><span className={`dw-chip ${t.app_insights && t.otel_sdk ? "ok" : "warn"}`}>{t.app_insights && t.otel_sdk ? "已配置" : "未完整配置"}</span></div>
          <div className="obs2-items">
            <div className="obs2-item"><ObsIcon name="monitor" /><div><b>Azure Monitor</b><em>{t.app_insights ? "已配置" : "未配置"}</em></div></div>
            <div className="obs2-item"><ObsIcon name="appinsights" /><div><b>App Insights</b><em>{t.app_insights ? "已配置" : "未配置"}</em></div></div>
            <div className="obs2-item"><ObsIcon name="otel" /><div><b>OpenTelemetry</b><em>{t.otel_sdk ? "已启用" : "未配置"}</em></div></div>
          </div>
          <div className="obs2-meta">
            <div><span>导出器</span><b>{t.exporter || "未记录"}</b></div>
            <div><span>对话模型</span><b>{models.chat || "未记录"}</b></div>
          </div>
        </section>
        <section className="card rubric2">
          <div className="obs2-head"><strong>可行性 rubric 校准可靠性</strong><span className={`dw-chip ${cg?.passed ? "ok" : "warn"}`}>{cg ? (cg.passed ? "通过" : "未过") : "未记录"}</span></div>
          <div className="rubric2-grid">
            <div className="rubric2-cell"><em>Spearman 相关</em><b>{cg?.spearman ?? "未记录"}</b><small>{cg ? `阈值 ≥ ${cg.min_spearman}` : "阈值未记录"}</small></div>
            <div className="rubric2-cell"><em>评分反馈</em><b>{cg?.inversion_count ?? "未记录"}</b><small>越低越好</small></div>
            <div className="rubric2-cell"><em>校准用例</em><b>{cg?.cases ?? "未记录"}</b><small>标注一致</small></div>
          </div>
          <p className="rubric2-note">{cg ? `rubric ${cg.rubric_version || "未记录"} · 预测分与人工标注分单调一致，说明可行性评分趋势一致、可信。` : "本工作区尚未返回 rubric 校准结果，不能据此宣称评分已校准。"}</p>
        </section>
      </div>

      <div className="run-body2">
        <section className="card run-trace">
          <div className="rt-head"><strong>本次运行追踪</strong><span className="rt-source">来源：{runEvidenceLabel(basis.trace, "后端运行步骤")}</span><Info size={14} /></div>
          <MafCollaborationView model={runMaf} compact />
          {(() => {
            const list = (rtrace && rtrace.length) ? rtrace.map((s) => {
              const view = traceStepView(s);
              const rawEvent = s.event || s.role || "";
              const detail = s.detail || s.data || {};
              const mafDetail = mafEventData(s);
              const isMafEvent = String(rawEvent).startsWith("maf_") && rawEvent !== "maf_workflow";
              const statusRaw = isMafEvent ? mafDetail.status : (s.status || detail.status);
              const measuredDuration = isMafEvent
                ? (rawEvent === "maf_agent_completed" ? mafDetail.duration_ms : null)
                : (s.duration_ms ?? detail.duration_ms);
              const item = {
                groupKey: view.key,
                icon: view.icon,
                name: view.name,
                role: view.role,
                status: traceStatusLabel(statusRaw),
                statusTone: mafStatusTone(statusRaw),
                durationMs: Number(measuredDuration ?? 0),
                dur: formatTraceDuration(measuredDuration),
                rawEvent,
                event: traceEventLabel(rawEvent),
                agent: s.agent || s.agent_id || detail.agent_id || detail.agent || "",
                toolName: s.name || detail.name || detail.tool || "",
                detail,
                source: s.source || "",
                evidence: s.evidence || {},
                tokens: s.tokens || {},
              };
              return {
                ...item,
                sum: cleanTraceSummary(s.summary) || traceDetailText(item),
              };
            }) : (rtrace === null ? null : []);
            const TPER = 8;
            const grouped = list ? groupTraceRows(list) : null;
            const tpages = grouped ? Math.max(1, Math.ceil(grouped.length / TPER)) : 1;
            const cur = Math.min(tracePage, tpages - 1);
            const shown = grouped ? grouped.slice(cur * TPER, cur * TPER + TPER) : [];
            return (
              <>
                <div className="rt-list">
                  {list === null ? <p className="empty-copy" style={{ padding: 14 }}><Loader2 size={14} className="spin" /> 加载追踪…</p> : null}
                  {list && !list.length ? <p className="empty-copy" style={{ padding: 14 }}>这次运行没有后端步骤记录；新运行会按 run_store.steps 动态显示。</p> : null}
                  {shown.map((s, i) => {
                    const Ic = s.icon;
                    const rowIndex = cur * TPER + i + 1;
                    const open = Boolean(traceOpen[s.key]);
                    const rowRunning = s.statusTone === "running";
                    const rowFailed = s.statusTone === "failed";
                    const rowCompleted = s.statusTone === "completed";
                    return (
                      <div className={open ? "rt-xrow open" : "rt-xrow"} key={s.key || i}>
                        <button type="button" className="rt-rowmain" onClick={() => setTraceOpen((m) => ({ ...m, [s.key]: !m[s.key] }))}>
                          <span className="rt-n">{rowIndex}</span>
                          <span className="rt-ic"><Ic size={15} /></span>
                          <div className="rt-main">
                            <div className="rt-title"><b>{s.name}</b>{s.role ? <em>{s.role}</em> : null}<span className={`rt-badge ${s.statusTone}`}>{s.status}</span>{s.details.length > 1 ? <span className="rt-count">{s.details.length} 条</span> : null}</div>
                            {s.sum ? <p className="rt-sum">{s.sum}</p> : null}
                          </div>
                          <span className="rt-dur">{s.dur ? `耗时 ${s.dur}` : "耗时未记录"}</span>
                          {rowRunning ? <Loader2 size={16} className="rt-state running spin" /> : rowFailed ? <AlertTriangle size={16} className="rt-state failed" /> : rowCompleted ? <CheckCircle2 size={16} className="rt-state completed" /> : <Activity size={16} className="rt-state unknown" />}
                          <ChevronDown size={16} className="rt-caret" />
                        </button>
                        {open ? (
                          <div className="rt-detail">
                            {s.details.map((d, j) => (
                              <div className="rt-det-sec" key={`${s.key}-${j}`}>
                                <b>{traceDetailTitle(d, j)}</b>
                                <div>
                                  <p>{cleanTraceSummary(d.sum) || traceDetailText(d)}</p>
                                  <small className="rt-det-meta">
                                    来源：{runEvidenceLabel(d.source, "run_store.steps")}
                                    {d.tokens?.total ? ` · ${d.tokens.total.toLocaleString()} tokens` : ""}
                                    {d.evidence?.duration ? ` · ${runEvidenceLabel(d.evidence.duration, "step.time")}` : ""}
                                  </small>
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                {grouped && tpages > 1 ? (
                  <div className="rh-pager" style={{ justifyContent: "center" }}>
                    <button type="button" disabled={cur === 0} onClick={() => setTracePage(cur - 1)}><ChevronLeft size={15} /></button>
                    <span>{cur + 1} / {tpages}</span>
                    <button type="button" disabled={cur >= tpages - 1} onClick={() => setTracePage(cur + 1)}><ChevronRight size={15} /></button>
                  </div>
                ) : null}
              </>
            );
          })()}
          <div className="rt-foot">
            <span className="rt-runid">运行 ID <b>{runId || "—"}</b>{runId ? <button type="button" className="id-copy" title="复制运行 ID" onClick={() => { try { navigator.clipboard.writeText(runId); } catch { /* ignore */ } }}><Copy size={13} /></button> : null}</span>
            <span>触发方式 <b>用户启动</b></span>
            <span>模型 <b>{models.chat || "GPT-5.1"}</b></span>
            <button type="button" className="lnk lnk-btn" disabled={!runId} onClick={openLog}>查看完整日志 ›</button>
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
                <button type="button" className="rh-row" key={id || i} onClick={() => id && onOpenConversation && onOpenConversation(id)} disabled={!id || !onOpenConversation} title={id}>
                  <CheckCircle2 size={15} className="rh-row-ic" />
                  <div className="rh-row-main">
                    <b>{runDisplayName(run)}</b>
                    <span>{runStatusLabel(run.status, Boolean(run.completed_at || run.time))}{run.step_count ? ` · ${run.step_count} 步` : ""}</span>
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

      <SideDrawer open={logOpen} title="完整运行日志" onClose={() => setLogOpen(false)}>
        <div style={{ marginBottom: 10 }}><a className="dw-btn" href={runId ? runLogUrl(runId, "text") : undefined} target="_blank" rel="noreferrer"><Download size={14} />下载日志</a></div>
        {logText ? <pre className="log-pre">{logText}</pre> : <p className="empty-copy"><Loader2 size={14} className="spin" /> 加载日志…</p>}
      </SideDrawer>
    </main>
  );
}

// 历史运行展示名：优先后端 title/摘要；否则用「结论 · 日期」汇总，不直接暴露运行 ID（ID 仅用于搜索）
function runDisplayName(run) {
  const t = run.title || run.summary || run.question;
  if (t && String(t).trim()) return String(t).trim().slice(0, 36);
  const v = run.verdict ? (VERDICT_LABELS[run.verdict] || run.verdict) : "可行性分析";
  const when = formatTime(run.time || run.completed_at || run.created_at);
  return `${v}${when ? ` · ${when}` : ""}`;
}

// 服务图标：SVG 加载失败时回退到 lucide 图标，避免出现裂图
function SvcIcon({ src, size = 26, fallback: Fallback = Server }) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  if (failed || !src) return <span className="set-conn-lic"><Fallback size={Math.round(size * 0.76)} /></span>;
  return (
    <span className={loaded ? "set-conn-svc loaded" : "set-conn-svc"} style={{ width: size, height: size }}>
      <Fallback size={Math.round(size * 0.7)} />
      <img className="svc-ic" src={src} width={size} height={size} alt="" loading="eager" decoding="async" fetchPriority="low" onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
    </span>
  );
}

function traceIcon(label) {
  const s = String(label || "").toLowerCase();
  if (s.includes("协调") || s.includes("orchestr") || s.includes("coordinat") || s.includes("route") || s.includes("plan")) return Workflow;
  if (s.includes("语料") || s.includes("corpus") || s.includes("检索") || s.includes("search") || s.includes("retriev")) return Search;
  if (s.includes("可行") || s.includes("feasib") || s.includes("评分") || s.includes("score")) return TrendingUp;
  if (s.includes("市场") || s.includes("market")) return Activity;
  if (s.includes("审计") || s.includes("audit") || s.includes("校验") || s.includes("shield")) return ShieldCheck;
  if (s.includes("回答") || s.includes("writer") || s.includes("produc") || s.includes("输出")) return FileText;
  return Activity;
}

function displayMembersFromApi(rawMembers) {
  return memberDirectoryViewModel(rawMembers).map((member) => ({
    ...member,
    initial: member.owner ? "我" : "成",
    roleLabel: memberRoleLabel(member.role),
    statusLabel: memberStatusLabel(member.status),
  }));
}

function memberStatusLabel(status) {
  const key = cleanUserValue(status).toLowerCase();
  if (key === "active") return "已激活";
  if (key === "pending") return "待加入";
  return cleanUserValue(status) || "成员";
}

function initialGovernanceWindow() {
  const to = new Date();
  to.setUTCDate(to.getUTCDate() + 1);
  const from = new Date(to);
  from.setUTCDate(from.getUTCDate() - 30);
  return { from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10) };
}

function governanceIsoWindow(value) {
  const from = new Date(`${value.from}T00:00:00.000Z`);
  const to = new Date(`${value.to}T00:00:00.000Z`);
  if (!Number.isFinite(from.getTime()) || !Number.isFinite(to.getTime()) || from >= to) return null;
  return { from: from.toISOString(), to: to.toISOString() };
}

function SettingsCenter({ dashboard, observability, user, initialTab = "about" }) {
  const health = dashboard?.health || {};
  const workspaceId = dashboard?.workspace_id || dashboard?.workspace?.workspace_id || "";
  const [tab, setTab] = useState("about");
  const [probing, setProbing] = useState(false);
  const [probedAt, setProbedAt] = useState(null);
  const [sys, setSys] = useState(null);
  const [memberRows, setMemberRows] = useState([]);
  const [memberMeta, setMemberMeta] = useState(null);
  const [membersLoading, setMembersLoading] = useState(false);
  const [memberLoadError, setMemberLoadError] = useState("");
  const [memberAction, setMemberAction] = useState("");
  const [memberNotice, setMemberNotice] = useState("");
  const [memberError, setMemberError] = useState("");
  const [inviteForm, setInviteForm] = useState({ email: "", name: "", role: "editor" });
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [directoryState, setDirectoryState] = useState({ connected: null, users: [], error: null });
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [sendGraphInvite, setSendGraphInvite] = useState(false);
  const [governanceData, setGovernanceData] = useState({ loading: true, trace: null, roi: null, chargeback: null, audit: null, auditRetryCursor: "", errors: {} });
  const [governanceWindow, setGovernanceWindow] = useState(initialGovernanceWindow);
  const [governanceLoadingMore, setGovernanceLoadingMore] = useState(false);
  const [invitationState, setInvitationState] = useState({ loading: true, data: null, error: "" });
  const governanceGuard = useRef(createGovernanceRequestGuard());
  const governanceToken = useRef(null);
  const memberRequestVersion = useRef(0);
  const invitationRequestVersion = useRef(0);
  const [workspaceSettings, setWorkspaceSettings] = useState(null);
  const [settingsDrawer, setSettingsDrawer] = useState(null);
  const applyMemberPayload = (data) => {
    setMemberRows(Array.isArray(data?.members) ? data.members : []);
    setMemberMeta(data || null);
  };
  const memberPermissions = governancePermissions(memberMeta || {});
  const memberPermissionReason = membersLoading ? "正在读取服务端操作权限" : (memberLoadError || memberPermissions.reasons["member.manage"]);
  const loadMembersContract = async () => {
    if (!workspaceId) return;
    const requestVersion = ++memberRequestVersion.current;
    setMembersLoading(true);
    setMemberLoadError("");
    try {
      const data = await loadMembers(workspaceId);
      if (requestVersion !== memberRequestVersion.current) return;
      applyMemberPayload(data);
    } catch {
      if (requestVersion !== memberRequestVersion.current) return;
      setMemberRows([]);
      setMemberMeta(null);
      setMemberLoadError("成员与操作权限读取失败，请重试");
    } finally {
      if (requestVersion === memberRequestVersion.current) setMembersLoading(false);
    }
  };
  const loadGovernanceEvidence = async () => {
    if (!workspaceId) return;
    const windowQuery = governanceIsoWindow(governanceWindow);
    if (!windowQuery) {
      setGovernanceData((current) => ({ ...current, loading: false, errors: { trace: "请选择有效时间范围", roi: "请选择有效时间范围", chargeback: "请选择有效时间范围", audit: "请选择有效时间范围" } }));
      return;
    }
    const requestToken = governanceGuard.current.begin(workspaceId);
    governanceToken.current = requestToken;
    setGovernanceLoadingMore(false);
    setGovernanceData((current) => ({ ...current, loading: true, auditRetryCursor: "", errors: {} }));
    const [traceResult, roiResult, auditResult, chargebackResult] = await Promise.allSettled([
      loadWorkspaceTraceStatus(workspaceId),
      loadWorkspaceRoi(workspaceId, windowQuery),
      memberPermissions.canReadAudit ? loadWorkspaceGovernanceAuditEvents(workspaceId, { limit: 25 }) : Promise.resolve(null),
      memberPermissions.canReadChargeback ? loadWorkspaceChargeback(workspaceId, windowQuery) : Promise.resolve(null),
    ]);
    if (!governanceGuard.current.isCurrent(requestToken, workspaceId)) return;
    const next = { loading: false, trace: null, roi: null, chargeback: null, audit: null, auditRetryCursor: "", errors: {} };
    if (traceResult.status === "fulfilled") next.trace = traceResult.value;
    else next.errors.trace = "遥测送达状态读取失败，请重试";
    if (roiResult.status === "fulfilled") next.roi = roiResult.value;
    else next.errors.roi = "ROI 证据读取失败，请重试";
    if (memberPermissions.canReadAudit && auditResult.status === "fulfilled") next.audit = auditResult.value;
    else if (memberPermissions.canReadAudit) {
      next.errors.audit = "审计事件读取失败，请重试";
    }
    if (memberPermissions.canReadChargeback && chargebackResult.status === "fulfilled") next.chargeback = chargebackResult.value;
    else if (memberPermissions.canReadChargeback) next.errors.chargeback = "成员归因读取失败，请重试";
    if (governanceGuard.current.isCurrent(requestToken, workspaceId)) setGovernanceData(next);
  };
  const loadInvitationHistory = async () => {
    if (!workspaceId || membersLoading || memberLoadError || !memberPermissions.canReadInvitations) return;
    const requestVersion = ++invitationRequestVersion.current;
    setInvitationState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const data = await loadWorkspaceInvitationHistory(workspaceId);
      if (requestVersion === invitationRequestVersion.current) setInvitationState({ loading: false, data, error: "" });
    } catch {
      if (requestVersion === invitationRequestVersion.current) setInvitationState((current) => ({ ...current, loading: false, error: "邀请历史读取失败，请重试" }));
    }
  };
  const loadMoreGovernanceAudit = async () => {
    const cursor = governanceData.auditRetryCursor || governanceData.audit?.next_cursor;
    const baseToken = governanceToken.current;
    if (!workspaceId || !cursor || !baseToken || governanceLoadingMore) return;
    const requestToken = governanceGuard.current.capture(baseToken, cursor);
    setGovernanceLoadingMore(true);
    try {
      const page = await loadWorkspaceGovernanceAuditEvents(workspaceId, { limit: 25, cursor });
      if (!governanceGuard.current.isCurrent(requestToken, workspaceId)) return;
      setGovernanceData((current) => auditPageSuccess(current, page));
    } catch {
      if (!governanceGuard.current.isCurrent(requestToken, workspaceId)) return;
      setGovernanceData((current) => auditPageFailure(current, cursor, "更早的审计事件读取失败，请重试"));
    } finally {
      if (governanceGuard.current.isCurrent(requestToken, workspaceId)) setGovernanceLoadingMore(false);
    }
  };
  useEffect(() => { loadSystemStatus().then(setSys).catch(() => {}); }, []);
  useEffect(() => {
    if (initialTab === "members") setTab("members");
  }, [initialTab]);
  useEffect(() => {
    if (!workspaceId) {
      setMemberRows([]);
      setMemberMeta(null);
      setMemberLoadError("");
      memberRequestVersion.current += 1;
      invitationRequestVersion.current += 1;
      governanceGuard.current.begin("");
      setGovernanceLoadingMore(false);
      setGovernanceData({ loading: false, trace: null, roi: null, chargeback: null, audit: null, auditRetryCursor: "", errors: {} });
      setInvitationState({ loading: false, data: null, error: "" });
      return undefined;
    }
    loadMembersContract();
    return () => { memberRequestVersion.current += 1; };
  }, [workspaceId]);
  useEffect(() => {
    if (!workspaceId) return undefined;
    let cancelled = false;
    loadWorkspaceSettings(workspaceId)
      .then((data) => { if (!cancelled) setWorkspaceSettings(data); })
      .catch(() => { if (!cancelled) setWorkspaceSettings(null); });
    return () => { cancelled = true; };
  }, [workspaceId]);
  useEffect(() => {
    loadGovernanceEvidence();
    return () => { governanceGuard.current.begin(""); };
  }, [workspaceId, governanceWindow.from, governanceWindow.to, memberPermissions.canReadAudit, memberPermissions.canReadChargeback]);
  useEffect(() => {
    if (membersLoading || memberLoadError) return undefined;
    if (!memberPermissions.canReadInvitations) {
      invitationRequestVersion.current += 1;
      setInvitationState({ loading: false, data: null, error: "" });
      return undefined;
    }
    loadInvitationHistory();
    return () => { invitationRequestVersion.current += 1; };
  }, [workspaceId, membersLoading, memberLoadError, memberPermissions.canReadInvitations]);
  useEffect(() => {
    const nodes = SETTINGS_ICON_SRCS.map((href) => {
      const node = document.createElement("link");
      node.rel = "preload";
      node.as = "image";
      node.href = href;
      node.type = "image/svg+xml";
      document.head.appendChild(node);
      return node;
    });
    return () => nodes.forEach((node) => node.remove());
  }, []);
  const systemStatus = sys || {};
  const deps = systemStatus.dependencies || health.dependencies || {};
  const models = systemStatus.models || observability?.models || {};
  const storage = workspaceSettings?.storage || {};
  const usedBytes = Number(storage.used_bytes || 0);
  const totalBytes = Number(storage.total_bytes || 0);
  const storagePct = totalBytes > 0 ? Math.min(100, Math.round((usedBytes / totalBytes) * 100)) : null;
  const modelCount = Object.values(models).filter(Boolean).length;
  const release = systemStatus.release || {};
  const tracing = systemStatus.observability?.tracing || observability?.tracing || {};
  const rag = systemStatus.rag || {};
  const contentSafetyLabel = deps.content_safety === true ? "已启用 · Prompt Shield" : deps.content_safety === false ? "未连接" : "未记录";
  const tracingLabel = tracing.app_insights && tracing.otel_sdk ? "App Insights · OpenTelemetry" : "未完整配置";
  const ragLabel = rag.configured ? "Azure AI Search · 向量 + 关键词" : "未连接";
  const stateLabel = (value) => value === true ? "健康" : value === false ? "异常" : "未记录";
  const stateClass = (value) => value === true ? "ok" : value === false ? "warn" : "";
  const dependencyState = (key, fallback) => {
    if (Object.prototype.hasOwnProperty.call(deps, key)) return Boolean(deps[key]);
    if (fallback !== undefined) return Boolean(fallback);
    return null;
  };
  const reprobe = () => {
    if (probing) return;
    setProbing(true);
    loadSystemStatus().then(setSys).catch(() => {}).finally(() => {
      window.setTimeout(() => { setProbing(false); setProbedAt(new Date()); }, 1200);
    });
  };
  const submitDirectorySearch = (event) => {
    event.preventDefault();
    if (!workspaceId || directoryLoading) return;
    if (!memberPermissions.canManageMembers) {
      setMemberError(memberPermissionReason);
      return;
    }
    setDirectoryLoading(true);
    setMemberError("");
    searchEntraUsers(workspaceId, directoryQuery, 8)
      .then((data) => {
        setDirectoryState({
          connected: Boolean(data?.connected),
          users: Array.isArray(data?.users) ? data.users : [],
          error: data?.error || null,
        });
        if (data?.connected === false) {
          setMemberNotice("Microsoft Graph 尚未连接或缺少授权，仍可手填邮箱加入工作区。");
        } else if (!data?.users?.length) {
          setMemberNotice("未找到匹配的 Entra 用户，可以继续手填邮箱邀请。");
        } else {
          setMemberNotice("");
        }
      })
      .catch((error) => {
        setDirectoryState({ connected: false, users: [], error: { message: error instanceof Error ? error.message : String(error || "") } });
        setMemberError(error instanceof Error ? error.message : String(error || "Entra 用户搜索失败"));
      })
      .finally(() => setDirectoryLoading(false));
  };
  const pickDirectoryUser = (account) => {
    const email = cleanUserValue(account?.email || account?.user_principal_name).toLowerCase();
    if (!email) return;
    setInviteForm((form) => ({
      ...form,
      email,
      name: cleanUserValue(account?.display_name),
    }));
    setMemberNotice("已选择 Entra 用户，确认角色后即可加入当前工作区。");
  };
  const submitInvite = (event) => {
    event.preventDefault();
    if (!workspaceId || memberAction) return;
    if (!memberPermissions.canManageMembers) {
      setMemberError(memberPermissionReason);
      return;
    }
    const email = cleanUserValue(inviteForm.email).toLowerCase();
    if (!email || !email.includes("@")) {
      setMemberError("请输入有效的成员邮箱。");
      setMemberNotice("");
      return;
    }
    setMemberAction("invite");
    setMemberError("");
    setMemberNotice("");
    inviteEntraMember(workspaceId, {
      email,
      name: cleanUserValue(inviteForm.name),
      role: inviteForm.role || "editor",
      send_email: sendGraphInvite,
      fallback_to_workspace_member: true,
      redirect_url: window.location.origin,
    })
      .then((data) => {
        applyMemberPayload(data);
        loadInvitationHistory();
        setInviteForm({ email: "", name: "", role: "editor" });
        const graphStatus = data?.graph_invite?.status;
        if (sendGraphInvite && graphStatus === "sent") {
          setMemberNotice("成员已加入工作区，Entra 邀请邮件已发送。");
        } else if (sendGraphInvite && graphStatus && graphStatus !== "skipped") {
          setMemberNotice(`成员已加入工作区；Entra 邮件未发送：${data?.graph_invite?.error?.message || "需要管理员授权 Graph 权限"}`);
        } else {
          setMemberNotice("成员已加入工作区列表，可用于后续协作、用量和审计溯源展示。");
        }
      })
      .catch((error) => {
        setMemberError(error instanceof Error ? error.message : String(error || "邀请失败"));
      })
      .finally(() => setMemberAction(""));
  };
  const removeWorkspaceMember = (subjectLabel) => {
    const target = cleanUserValue(subjectLabel);
    if (!workspaceId || !target || memberAction) return;
    if (!memberPermissions.canManageMembers) {
      setMemberError(memberPermissionReason);
      return;
    }
    setMemberAction(`remove:${target}`);
    setMemberError("");
    setMemberNotice("");
    removeMember(workspaceId, target)
      .then((data) => {
        applyMemberPayload(data);
        loadInvitationHistory();
        setMemberNotice("成员已从当前工作区移除。");
      })
      .catch((error) => {
        setMemberError(error instanceof Error ? error.message : String(error || "移除失败"));
      })
      .finally(() => setMemberAction(""));
  };
  const updateWorkspaceMemberRole = (subjectLabel, role) => {
    const target = cleanUserValue(subjectLabel);
    if (!workspaceId || !target || memberAction) return;
    if (!memberPermissions.canManageMembers) {
      setMemberError(memberPermissionReason);
      return;
    }
    setMemberAction(`role:${target}`);
    setMemberError("");
    setMemberNotice("");
    updateMemberRole(workspaceId, target, role)
      .then((data) => {
        applyMemberPayload(data);
        loadInvitationHistory();
        setMemberNotice("成员角色已更新，后续运行会按新角色展示协作与用量归因。");
      })
      .catch((error) => {
        setMemberError(error instanceof Error ? error.message : String(error || "角色更新失败"));
      })
      .finally(() => setMemberAction(""));
  };
  const openSettingsHelp = (kind) => setSettingsDrawer(kind);
  const connectors = [
    { src: "/icons/foundry.svg", name: "Azure AI Foundry Agent Service", desc: "Agent 执行与编排服务", ok: dependencyState("foundry") },
    { src: "/icons/ai-search.svg", name: "Azure AI Search", desc: "向量检索与搜索服务", ok: dependencyState("search", health.search_endpoint) },
    { src: "/icons/azure-blob.svg", name: "Azure Blob Storage", desc: "数据与文件存储服务", ok: dependencyState("blob") },
    { icon: Server, name: "MCP Server", desc: "外部工具与能力连接器", ok: dependencyState("mcp") },
    { src: "/icons/speech.svg", name: "Azure AI Speech", desc: "语音识别与合成服务", ok: dependencyState("speech") },
    { src: "/icons/content-safety.svg", name: "Azure AI Content Safety", desc: "内容安全与风险检测", ok: dependencyState("content_safety") },
  ];
 const connOk = connectors.filter((c) => c.ok === true).length;
 const connKnown = connectors.filter((c) => typeof c.ok === "boolean").length;
  const probeSummary = connKnown < connectors.length
    ? "已记录 " + connKnown + " / " + connectors.length + " 个连接器"
    : connOk === connectors.length
      ? "全部连接器健康"
      : connOk + " / " + connectors.length + " 个连接器可用";
  const members = useMemo(() => displayMembersFromApi(memberRows), [memberRows]);
  const usageTotals = memberMeta?.usage?.totals || {};
  const settingsDrawerCopy = {
    models: {
      title: "模型与生成管理",
      body: [
        "当前演示环境由后端统一管理模型部署，前端只展示实际连接状态。",
        "对话、分析、概念图与语音摘要分别走独立配置，后续可以在这里开放模型切换、默认产物类型和生成成本策略。",
      ],
    },
    compliance: {
      title: "数据与合规管理",
      body: [
        "登录入口由 Microsoft Entra ID 和 Container Apps Easy Auth 保护；运行、会话、产物会记录 actor，用于审计和用量归因。",
        "内容安全、Blob 持久化、Search 检索和 App Insights 追踪保持后端统一配置，避免前端暴露敏感凭证。",
      ],
    },
    preferences: {
      title: "工作区偏好",
      body: [
        "当前版本已保存工作区、会话、数据文件和产物；主题、语言和默认产物偏好仍由平台统一配置。",
        "下一步可以把工作区默认 logo、报告语气、默认时区和产物命名规则做成可配置项。",
      ],
    },
    terms: {
      title: "服务协议",
      body: [
        "DataForge 当前为演示环境：分析结论用于业务验证和方案讨论，不替代正式经营、法律或财务决策。",
        "平台会标注证据来源、市场推断和缺口；用户仍需要基于真实试点数据复核最终落地决策。",
      ],
    },
    privacy: {
      title: "隐私政策",
      body: [
        "上传数据、会话记录和产物保存在工作区范围内，用于检索、分析、版本迭代和审计展示。",
        "外部连接器凭证只发送到后端连接会话，不写入前端状态、不返回给浏览器；生产使用前仍应配置正式密钥管理和权限边界。",
      ],
    },
  };
  const kv = (k, v) => (<div className="set-kv" key={k}><span>{k}</span><b>{v}</b></div>);
  const cfgCard = (icon, title, rows, desc, manageKind) => (
    <section className="card set-cfg">
      <div className="set-cfg-h">
        {icon}
        <strong>{title}</strong>
        <button type="button" className="lnk lnk-btn" onClick={() => openSettingsHelp(manageKind)}>管理</button>
      </div>
      <div className="set-cfg-rows">{rows.map(([k, v]) => kv(k, v))}</div>
      <p className="set-cfg-desc">{desc}</p>
    </section>
  );
  return (
    <main className="agent-studio settings-stage">
      <header className="conv-head">
        <span className="eyeless-label">Settings</span>
        <h1>设置</h1>
        <p>管理模型与生成、数据与合规、系统偏好与集成连接，确保平台安全稳定运行。</p>
      </header>

      <div className="set-stats">
        <div className="card set-stat"><div className="set-stat-ic blue"><Boxes size={18} /></div><b>模型服务</b><div className="set-stat-kv"><span>状态</span><span className={"dw-chip " + (modelCount > 0 ? "ok" : "")}>{modelCount > 0 ? "健康" : "未记录"}</span></div><em>已配置模型 {modelCount} 个</em></div>
        <div className="card set-stat"><div className="set-stat-ic blue"><Database size={18} /></div><b>数据存储</b><div className="set-stat-kv"><span>状态</span><span className={"dw-chip " + (totalBytes > 0 ? "ok" : "")}>{totalBytes > 0 ? "健康" : "未记录"}</span></div><em>{totalBytes > 0 ? "已用 " + formatBytes(usedBytes) + " / " + formatBytes(totalBytes) + "（" + storagePct + "%）" : "容量未返回"}</em></div>
        <div className="card set-stat"><div className="set-stat-ic blue"><ShieldCheck size={18} /></div><b>内容安全</b><div className="set-stat-kv"><span>状态</span><span className={"dw-chip " + stateClass(deps.content_safety)}>{stateLabel(deps.content_safety)}</span></div><em>{deps.content_safety === true ? "Prompt Shield 已启用" : deps.content_safety === false ? "服务未连接" : "状态未返回"}</em></div>
        <div className="card set-stat"><div className="set-stat-ic blue"><Server size={18} /></div><b>连接器状态</b><div className="set-stat-kv"><span>状态</span><span className={"dw-chip " + (connKnown === connectors.length && connOk === connectors.length ? "ok" : "warn")}>{connKnown < connectors.length ? "部分未记录" : connOk === connectors.length ? "全部正常" : "部分异常"}</span></div><em>已连接 {connOk} / {connectors.length}（已记录 {connKnown}）</em></div>
      </div>

      <div className="set-cfgs">
        {cfgCard(<Sparkles size={16} />, "模型与生成", [["对话 / 推理模型", models.chat || "未记录"], ["概念图模型", models.image || "未记录"], ["向量模型（RAG）", models.embedding || "未记录"], ["检索增强", ragLabel], ["默认生成音频摘要", "已禁用"]], "控制模型选择、检索增强与生成输出行为。", "models")}
        {cfgCard(<ShieldCheck size={16} />, "数据与合规", [["内容安全（RAI）", contentSafetyLabel], ["身份认证", "Microsoft Entra ID · Easy Auth"], ["数据驻留", "Azure · East US 2"], ["分布式追踪", tracingLabel], ["审计日志保留", "180 天"]], "保障数据安全、合规与可观测性。", "compliance")}
        {cfgCard(<Cpu size={16} />, "工作区偏好", [["界面语言", "简体中文"], ["主题", "浅色（深色即将支持）"], ["时区", "跟随系统"], ["数据持久化", "Azure Blob（工作区/会话/产物）"], ["默认时区显示", "跟随系统"]], "自定义界面语言、主题、时区与数据持久化偏好。", "preferences")}
      </div>

      <div className={`set-bottom ${tab === "governance" ? "governance-active" : ""}`}>
        <section className="card set-conn">
          <div className="set-conn-h"><strong>集成与连接状态</strong>
            <button type="button" className="set-refresh" onClick={reprobe} disabled={probing}>
              <RefreshCw size={14} className={probing ? "spin" : ""} /> {probing ? "检测中…" : "刷新状态"}
            </button>
          </div>
          <div className="set-conn-grid">
            {connectors.map((c, i) => {
              const Ic = c.icon;
              return (
                <div className={probing ? "set-conn-card probing" : "set-conn-card"} key={i}>
                  {c.src ? <SvcIcon src={c.src} size={26} fallback={Server} /> : <span className="set-conn-lic"><Ic size={20} /></span>}
                  <div className="set-conn-main"><b>{c.name}</b><em>{c.desc}</em></div>
                  {probing ? <span className="dw-chip probing"><Loader2 size={11} className="spin" /> 检测中</span> : <span className={c.ok ? "dw-chip ok" : "dw-chip"}>{c.ok ? "已连接" : "未连接"}</span>}
                </div>
              );
            })}
          </div>
          <p className="set-cfg-desc">{probedAt ? "上次探测：" + formatTime(probedAt.toISOString()) + " · " + probeSummary : "管理平台所依赖的外部服务与连接器，确保数据流转与能力调用正常。"}</p>
        </section>

        <section className="card set-about">
          <div className="set-tabs">
            <button type="button" className={tab === "about" ? "set-tab active" : "set-tab"} onClick={() => setTab("about")}>关于</button>
            <button type="button" className={tab === "members" ? "set-tab active" : "set-tab"} onClick={() => setTab("members")}>成员与权限</button>
            <button type="button" className={tab === "governance" ? "set-tab active" : "set-tab"} onClick={() => setTab("governance")}>治理与 ROI</button>
          </div>
          {tab === "about" ? (
            <div className="set-about-body">
              {kv("产品名称", "DataForge")}
              {kv("版本", release.version || "未记录")}
              {kv("构建编号", release.build || "未记录")}
              {kv("部署环境", release.environment || "未记录")}
              <div className="set-kv"><span>服务协议</span><button type="button" className="lnk lnk-btn" onClick={() => openSettingsHelp("terms")}>查看服务协议</button></div>
              <div className="set-kv"><span>隐私政策</span><button type="button" className="lnk lnk-btn" onClick={() => openSettingsHelp("privacy")}>查看隐私政策</button></div>
            </div>
          ) : tab === "governance" ? (
            <div className="set-governance">
              <GovernanceSummaryPanel
                data={governanceData}
                invitationState={invitationState}
                permissionsPayload={memberMeta}
                permissionState={{ loading: membersLoading, error: memberLoadError }}
                windowValue={governanceWindow}
                onWindowChange={setGovernanceWindow}
                onRetry={loadGovernanceEvidence}
                onInvitationRetry={loadInvitationHistory}
                onPermissionRetry={loadMembersContract}
                onLoadMore={loadMoreGovernanceAudit}
                loadingMore={governanceLoadingMore}
              />
            </div>
          ) : (
            <div className="set-members">
              <div className="set-members-head">
                <span>成员（{members.length}）{membersLoading ? " · 同步中" : ""}</span>
                <span className="member-mode">工作区成员 · Entra 登录后归因</span>
              </div>
              {memberLoadError ? <div className="member-msg error">{memberLoadError}<button type="button" className="gov-inline-retry" onClick={loadMembersContract}><RefreshCw size={13} />重试</button></div> : null}
              <form className="member-directory-search" onSubmit={submitDirectorySearch}>
                <div className="member-directory-input">
                  <Search size={14} />
                  <input
                    type="search"
                    value={directoryQuery}
                    onChange={(event) => setDirectoryQuery(event.target.value)}
                    disabled={!memberPermissions.canManageMembers}
                    placeholder="搜索 Entra 用户或邮箱"
                  />
                </div>
                <button type="submit" disabled={directoryLoading || !memberPermissions.canManageMembers} title={!memberPermissions.canManageMembers ? memberPermissionReason : ""}>
                  {directoryLoading ? <Loader2 size={14} className="spin" /> : <Search size={14} />}
                  搜索
                </button>
              </form>
              {directoryState.connected === false ? (
                <div className="member-msg warn">
                  Graph 目录未连接：{directoryState.error?.message || "需要启用 token store 或配置 Graph app-only 权限。"}
                </div>
              ) : null}
              {directoryState.users?.length ? (
                <div className="member-directory-results">
                  {directoryState.users.map((account) => (
                    <button
                      type="button"
                      key={account.id || account.email}
                      onClick={() => pickDirectoryUser(account)}
                    >
                      <span className="mbr-av">{memberInitial(account.display_name, "")}</span>
                      <span>
                        <b>{account.display_name || "Entra 用户"}</b>
                        <em>选择后填入邀请邮箱</em>
                      </span>
                      <small>{account.user_type || "Entra"}</small>
                    </button>
                  ))}
                </div>
              ) : null}
              <form className="member-invite-form" onSubmit={submitInvite}>
                <input
                  type="email"
                  value={inviteForm.email}
                  onChange={(event) => setInviteForm((form) => ({ ...form, email: event.target.value }))}
                  placeholder="成员邮箱"
                  autoComplete="email"
                  disabled={!memberPermissions.canManageMembers}
                />
                <input
                  type="text"
                  value={inviteForm.name}
                  onChange={(event) => setInviteForm((form) => ({ ...form, name: event.target.value }))}
                  placeholder="姓名（可选）"
                  disabled={!memberPermissions.canManageMembers}
                />
                <select
                  value={inviteForm.role}
                  onChange={(event) => setInviteForm((form) => ({ ...form, role: event.target.value }))}
                  aria-label="成员角色"
                  disabled={!memberPermissions.canManageMembers}
                >
                  <option value="admin">管理员</option>
                  <option value="editor">编辑者</option>
                  <option value="viewer">查看者</option>
                </select>
                <button type="submit" disabled={memberAction === "invite" || !memberPermissions.canManageMembers} title={!memberPermissions.canManageMembers ? memberPermissionReason : ""}>
                  {memberAction === "invite" ? <Loader2 size={14} className="spin" /> : <UserPlus size={14} />}
                  邀请
                </button>
              </form>
              <label className="member-mail-toggle">
                <input
                  type="checkbox"
                  checked={sendGraphInvite}
                  onChange={(event) => setSendGraphInvite(event.target.checked)}
                  disabled={!memberPermissions.canManageMembers}
                />
                <span>发送 Entra 邀请邮件</span>
              </label>
              {!memberPermissions.canManageMembers ? <div className="member-permission-note"><ShieldCheck size={13} />{memberPermissionReason}</div> : null}
              {memberError ? <div className="member-msg error">{memberError}</div> : null}
              {memberNotice ? <div className="member-msg ok">{memberNotice}</div> : null}
              <div className="member-usage-strip">
                <span>Runs <b>{formatCount(usageTotals.runs)}</b></span>
                <span>Tokens <b>{formatGovernanceTokens(usageTotals)}</b></span>
                <span>Source <b>{memberMeta?.source || "easy_auth"}</b></span>
              </div>
              {members.map((m, i) => (
                <div className="set-member" key={i}>
                  <span className="mbr-av">{m.initial}</span>
                  <div className="mbr-main">
                    <b>{m.subjectLabel}</b>
                    <em>服务端安全成员标签</em>
                    <small>{formatCount(m.usage?.runs)} runs · {formatGovernanceTokenLabel(m.usage)}{m.lastSeenAt ? ` · ${formatTime(m.lastSeenAt)}` : ""}</small>
                  </div>
                  <span className={`dw-chip ${m.status === "active" ? "ok" : "warn"}`}>{m.statusLabel}</span>
                  {m.owner ? (
                    <span className="dw-chip ok">{m.roleLabel}</span>
                  ) : (
                    <label className="mbr-role-select" title="修改成员角色">
                      <select
                        value={m.role || "viewer"}
                        disabled={!m.actionRef || memberAction === `role:${m.actionRef}` || !memberPermissions.canManageMembers}
                        onChange={(event) => updateWorkspaceMemberRole(m.actionRef, event.target.value)}
                        aria-label={`${m.subjectLabel} 的成员角色`}
                      >
                        <option value="admin">管理员</option>
                        <option value="editor">编辑者</option>
                        <option value="viewer">查看者</option>
                      </select>
                      {memberAction === `role:${m.actionRef}` ? <Loader2 size={13} className="spin" /> : <ChevronDown size={13} />}
                    </label>
                  )}
                  {!m.owner ? (
                    <button
                      type="button"
                      className="mbr-remove"
                      title="从当前工作区移除"
                      disabled={!m.actionRef || memberAction === `remove:${m.actionRef}` || !memberPermissions.canManageMembers}
                      onClick={() => removeWorkspaceMember(m.actionRef)}
                    >
                      {memberAction === `remove:${m.actionRef}` ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />}
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
      <SideDrawer
        open={Boolean(settingsDrawer)}
        title={settingsDrawerCopy[settingsDrawer]?.title || "设置说明"}
        onClose={() => setSettingsDrawer(null)}
      >
        <div className="settings-info-drawer">
          {(settingsDrawerCopy[settingsDrawer]?.body || []).map((line, index) => <p key={index}>{line}</p>)}
        </div>
      </SideDrawer>
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

  const live = running || producing;
  const dynamicMaf = useMemo(() => deriveMafViewModel(trace), [trace]);
  const maf = dynamicMaf ? null : trace.find((item) => item.event === "maf_workflow")?.data || null;
  const dynamicCurrent = dynamicMaf?.agents.find((agent) => agent.tone === "running") || dynamicMaf?.agents.at(-1);
  const current = dynamicCurrent
    ? mafAgentMeta(dynamicCurrent.id)
    : AGENTS.find((agent) => agent.id === actualActive) || (producing ? AGENTS[PRODUCER] : AGENTS[0]);
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
  const dynamicFoot = dynamicMaf
    ? (dynamicMaf.reasonCodes.length
      ? dynamicMaf.reasonCodes.join(" · ")
      : `协作模式：${MAF_MODES.find((item) => item.id === dynamicMaf.mode)?.label || dynamicMaf.mode || "未记录"}`)
    : foot;
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
      {dynamicMaf ? <MafCollaborationView model={dynamicMaf} /> : (
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
      )}
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
        <strong>{dynamicFoot}</strong>
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
  const safe = sanitizeReply(text == null ? "" : String(text));
  const reduce = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [shown, setShown] = useState(animate && !reduce ? "" : safe);
  useEffect(() => {
    if (!animate || reduce) { setShown(safe); return undefined; }
    let i = 0;
    setShown("");
    const id = window.setInterval(() => {
      i += 1;
      setShown(safe.slice(0, i));
      if (i >= safe.length) window.clearInterval(id);
    }, 32);
    return () => window.clearInterval(id);
  }, [safe, animate, reduce]);
  return <p className="chat-usertext">{shown}{animate && !reduce && shown.length < safe.length ? <span className="cursor" /> : null}</p>;
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
    // Backend removes these at the source; keep old history and in-flight stream chunks clean too.
    .replace(/(^|\n)(\s*(?:(?:[-*+]\s+|\d+[.)]\s+))?)(?:[”’」』]+|"(?=[，,;；:：、\-–—]))[，,;；:：、\-–—]*\s*/g, "$1$2")
    // 被抄进正文的"演示/合成数据"免责声明（限定一句，避免贪吃后文）
    .replace(/[>＞]?\s*注[:：][^。\n]{0,80}(演示数据|合成数据|演示用)[^。\n]{0,80}。?/g, "")
    // 旧 run 曾把联网检索结构化 JSON 拼进正文；显示层兜底剥离，来源仍由结构化 market.sources 面板展示。
    .replace(/^[ \t]*(?:[-*][ \t]*)?外部市场只作为参考[：:][ \t]*\{[\s\S]*?(?=^[ \t]*\*\*评分\*\*|^[ \t]*##|$(?![\s\S]))/gm, "")
    .replace(/^[ \t]*(?:[-*][ \t]*)?市场补充[：:][ \t]*\{[\s\S]*?(?=^[ \t]*##|$(?![\s\S]))/gm, "")
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
          placeholder={listening ? "正在聆听，请说话…" : "继续追问（Enter 发送 · Shift+Enter 换行），或从上方选择推荐问题"}
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
          {notice.action ? <button className="notice-action" type="button" onClick={notice.action}>{notice.actionLabel || "查看"}</button> : null}
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
  const data = mafEventData(item);
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
    case "maf_plan": return `MAF 协作计划${data.mode || data.pattern ? ` · ${data.mode || data.pattern}` : ""}`;
    case "maf_agent_started": return `${mafAgentMeta(data.agent_id || data.agent).zh} 开始执行`;
    case "maf_agent_completed": return `${mafAgentMeta(data.agent_id || data.agent).zh} · ${mafStatusLabel(data.status)}`;
    case "maf_agent_failed": return `${mafAgentMeta(data.agent_id || data.agent).zh} · ${mafStatusLabel(data.status || "failed")}`;
    case "maf_branch_started": return `分支 ${data.branch_id || "未记录"} 启动`;
    case "maf_branch_joined": return `分支 ${data.branch_id || "未记录"} · ${mafStatusLabel(data.status)}`;
    case "maf_handoff": return `${mafAgentMeta(data.source_agent_id).zh} 交接给 ${mafAgentMeta(data.target_agent_id).zh}`;
    case "maf_review": return `第 ${mafRevisionNumber(data) || "未记录"} 轮复核 · ${mafStatusLabel(data.status)}`;
    case "maf_fallback": return `MAF 回退 · ${data.error_category || data.reason || mafStatusLabel(data.status)}`;
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

function formatCount(value) {
  const num = Number(value || 0);
  if (!Number.isFinite(num) || num <= 0) return "0";
  if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
  if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
  return String(Math.round(num));
}

function formatCurrencyUsd(value) {
  const num = Number(value || 0);
  if (!Number.isFinite(num)) return "$0.00";
  if (Math.abs(num) >= 1000) return `$${num.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  return `$${num.toFixed(2)}`;
}

function absoluteApiUrl(value) {
  if (!value) return "";
  return String(value).startsWith("http") ? value : `${API_BASE}${value}`;
}
