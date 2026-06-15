import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  Bell,
  CheckCircle2,
  ChevronDown,
  CircleUserRound,
  Clock3,
  Database,
  FileDown,
  FileText,
  FolderOpen,
  ImagePlus,
  Layers3,
  Loader2,
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
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { API_BASE, artifactLink } from "./api.js";
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

export function ShellNav({ active = "workspaces", onChange = () => {}, health = {} }) {
  const deps = health.dependencies || {};
  const status = [
    ["Foundry", deps.foundry],
    ["Search", Boolean(health.search_endpoint || deps.search)],
    ["Blob", deps.blob],
    ["MCP", deps.mcp],
  ];
  return (
    <nav className="shell-nav" aria-label="Primary">
      <div className="brand-mark">D</div>
      <div className="nav-stack">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
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
      <div className="nav-status" aria-label="服务状态">
        <span className="nav-status-head">服务状态</span>
        {status.map(([label, ok]) => (
          <span className="nav-status-row" key={label} title={`${label}: ${ok ? "正常" : "未连接"}`}>
            <i className={ok ? "nst-dot ok" : "nst-dot off"} />{label}
          </span>
        ))}
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

export function TopBar({ dashboard, workspaceId, onWorkspaceChange, onUpload, onNewConversation, loading, user, authState, onLogout }) {
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
        <div className="brand-wordmark">
          <strong>DataForge</strong>
          <span>Agent Workbench</span>
        </div>
        <label className="workspace-select">
          <Database size={16} />
          <select value={workspaceId} onChange={(event) => onWorkspaceChange(event.target.value)}>
            {workspaces.length ? (
              workspaces.map((workspace) => (
                <option key={workspace.workspace_id} value={workspace.workspace_id}>
                  {workspace.name || workspace.workspace_id}
                </option>
              ))
            ) : (
              <option value={workspaceId}>{workspaceId}</option>
            )}
          </select>
        </label>
      </div>
      <div className="topbar-actions">
        <button className="primary-button icon-label" type="button" onClick={() => { setMenuOpen(false); onUpload(); }}>
          <UploadCloud size={16} />
          上传数据
        </button>
        <button className="ghost-button icon-label top-new-chat" type="button" onClick={() => { setMenuOpen(false); onNewConversation(); }}>
          <MessageSquare size={15} />
          新会话
        </button>
        <button className="icon-button top-icon" type="button" title="通知">
          <Bell size={16} />
        </button>
        <div className={loading ? "sync-dot loading" : "sync-dot"} title={loading ? "同步中" : "已同步"}>
          {loading ? <Loader2 className="spin" size={14} /> : <CheckCircle2 size={14} />}
        </div>
        <div className="user-menu" ref={menuRef}>
          <button className="user-trigger" type="button" onClick={() => setMenuOpen((value) => !value)} aria-expanded={menuOpen}>
            <div className="avatar" title={user?.email || "DataForge"}>
              {(user?.name || user?.email || "D").trim().slice(0, 1).toUpperCase()}
            </div>
            <ChevronDown size={14} />
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

function StatusChip({ label, ok }) {
  const good = ok === true || ok === "ok" || ok === "configured";
  return (
    <span className={good ? "status-chip ok" : "status-chip muted"}>
      <i />
      <span>{label}</span>
      <em>{good ? "健康" : "检查"}</em>
    </span>
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
  const canDelete = workspaceId?.startsWith("upload-");
  const documents = workspace.documents || [];
  const created = workspace.created_at || workspace.updated_at || documents[0]?.created_at;
  const fields = columns.length || workspace.field_count || 0;
  const rows = workspace.row_count ?? workspace.indexed_count ?? workspace.doc_count ?? 0;
  const fillRate = workspace.fill_rate ?? workspace.field_fill_rate;
  const referenceImages = workspace.reference_images || [];

  return (
    <aside className="workspace-pane">
      <section className="pane-section workspace-hero">
        <div className="section-head">
          <span>工作区</span>
          <button className="icon-button" type="button" onClick={onRefresh} title="刷新">
            <RefreshCw size={15} />
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

      <section className="pane-section">
        <div className="section-head"><span>数据画像</span><em>{signalColumns.length ? "已解析" : "等待信号"}</em></div>
        <DataPortrait workspace={workspace} signalColumns={signalColumns} noisyColumns={noisyColumns} columns={columns} />
      </section>

      {signalColumns.length ? (
        <section className="pane-section">
          <div className="section-head"><span>关键信号 TOP5</span><em>{Math.min(5, signalColumns.length)}</em></div>
          <div className="signal-top">
            {signalColumns.slice(0, 5).map((column, index) => {
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
      <div className="portrait-ring" style={{ "--value": `${strength}%` }}>
        <strong>{strength}</strong>
        <span>整体信号可用度</span>
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

export function AgentStudio({
  dashboard,
  messages,
  trace,
  streamText,
  running,
  input,
  setInput,
  onRun,
  selectedPlaybook,
  setSelectedPlaybook,
  artifactMode,
  setArtifactMode,
  finalArtifact,
  artifacts,
  onProduce,
  onUploadReference,
  producing,
}) {
  const workspace = dashboard?.workspace || {};
  const feasibility = finalArtifact?.feasibility || {};
  const verdict = VERDICT_LABELS[feasibility.verdict] || VERDICT_LABELS[finalArtifact?.routing?.intent] || "待分析";
  const confidence = CONFIDENCE_LABELS[feasibility.overall_confidence] || feasibility.overall_confidence || "待验证";
  const evidence = useMemo(() => collectEvidence(finalArtifact), [finalArtifact]);
  const quality = useMemo(() => summarizeQuality(finalArtifact, trace, artifacts, evidence), [finalArtifact, trace, artifacts, evidence]);
  const presentation = useAgentPresentation(trace, running);

  return (
    <main className="agent-studio">
      <section className="studio-head">
        <div>
          <span className="eyeless-label">Agent Studio</span>
          <h1>{workspace.name || "DataForge"} · {verdict}</h1>
          <p>{workspace.customer_summary || workspace.profile_summary || "选择工作区后开始产品化分析。"}</p>
        </div>
        <div className="verdict-stack">
          <span className="verdict-pill">{verdict}</span>
          <span className={`confidence-pill ${feasibility.overall_confidence || "speculative"}`}>{confidence}</span>
        </div>
      </section>

      <AgentRoute trace={trace} running={running} presentation={presentation} />
      <QuestionStarter onRun={onRun} running={running} />
      <PlaybookBar selected={selectedPlaybook} onSelect={setSelectedPlaybook} artifactMode={artifactMode} onMode={setArtifactMode} />
      <QualityBar quality={quality} />

      <section className="answer-surface">
        <AnswerPanel messages={messages} streamText={streamText} running={running} presentation={presentation} onRun={onRun} />
        <FeasibilityStrip feasibility={feasibility} />
        <ActionBoard artifact={finalArtifact} selectedPlaybook={selectedPlaybook} onProduce={onProduce} producing={producing} />
      </section>

      <Composer input={input} setInput={setInput} running={running} onRun={onRun} selectedPlaybook={selectedPlaybook} />
    </main>
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
        selectedPlaybook={selectedPlaybook}
        setSelectedPlaybook={setSelectedPlaybook}
      />
    );
  }
  if (view === "artifacts") {
    return <ArtifactsCenter dashboard={dashboard} artifacts={artifacts} artifact={finalArtifact} onProduce={onProduce} producing={producing} onUploadReference={onUploadReference} />;
  }
  if (view === "runs") {
    return <RunsCenter dashboard={dashboard} trace={trace} running={running} />;
  }
  if (view === "settings") {
    return <SettingsCenter dashboard={dashboard} />;
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

  return (
    <main className="agent-studio dashboard-stage">
      <section className="dashboard-hero">
        <div>
          <span className="eyeless-label">Workspace Dashboard</span>
          <h1>{workspace.name || "数据产品化工作区"}</h1>
          <p>{workspace.customer_summary || workspace.profile_summary || "上传数据后，DataForge 会先生成数据画像、信号/噪声判断、检索索引和可追踪的 Agent 分析记录。"}</p>
        </div>
        <div className="dashboard-actions">
          <button className="ghost-button icon-label" type="button" onClick={onNewConversation}>
            <MessageSquare size={15} />
            新建会话
          </button>
          <button className="primary-button icon-label" type="button" onClick={() => onRun("请基于当前工作区，先自动分析这批数据可以产品化成什么机会，并说明证据强弱、市场推断和下一步。", { stayOnDashboard: true })}>
            <Sparkles size={15} />
            自动分析
          </button>
        </div>
      </section>

      <AgentRoute trace={trace} running={running} presentation={presentation} producing={producing} hasArtifacts={hasArtifacts} onProduce={onProduce} />

      <VerdictHero feasibility={feasibility} verdict={verdict} running={running} />

      <section className="studio-methods">
        <ActionPlanCards selected={selectedPlaybook} onSelect={setSelectedPlaybook} feasibility={feasibility} />
        <ActionBoard artifact={finalArtifact} selectedPlaybook={selectedPlaybook} onProduce={onProduce} producing={producing} />
      </section>
    </main>
  );
}

function DashboardMetrics({ workspace, documents }) {
  const created = workspace.created_at || workspace.updated_at || documents[0]?.created_at;
  const fields = workspace.columns?.length || workspace.field_count || 0;
  const rows = workspace.row_count ?? workspace.indexed_count ?? workspace.doc_count ?? 0;
  return (
    <section className="dashboard-metrics">
      <MetricCard icon={FolderOpen} label="工作区" value={workspace.workspace_id || "demo"} detail={created ? `创建于 ${formatTime(created)}` : "已连接"} />
      <MetricCard icon={Database} label="数据集" value={documents.length || workspace.doc_count || 0} detail="CSV / Excel / JSON / MD / 图片" />
      <MetricCard icon={Layers3} label="字段" value={fields} detail="用于画像、分布和信号判断" />
      <MetricCard icon={BarChart3} label="索引记录" value={rows} detail="已进入 Search / RAG 检索链路" />
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
    <article className="dash-panel pipeline-panel">
      <div className="dash-panel-head">
        <span>数据解析状态</span>
        <strong>从上传到 Agent 可用</strong>
      </div>
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
    </article>
  );
}

function BiSnapshotCard({ workspace, signalColumns }) {
  const rows = Number(workspace.row_count || workspace.indexed_count || workspace.doc_count || 0);
  const strength = Math.min(96, Math.max(18, signalColumns.length * 13 + Math.min(24, Math.log10(rows + 1) * 10)));
  const chart = signalColumns.length ? signalColumns : [{ name: "数据覆盖", friendly_label: "数据覆盖" }, { name: "字段完整", friendly_label: "字段完整" }, { name: "可检索性", friendly_label: "可检索性" }];
  return (
    <article className="dash-panel bi-panel">
      <div className="dash-panel-head">
        <span>BI 快照</span>
        <strong>自动画像与信号强度</strong>
      </div>
      <div className="bi-visual">
        <svg viewBox="0 0 220 112" aria-label="BI chart">
          <polyline points="8,86 42,72 76,78 110,45 144,54 178,28 212,36" />
          {chart.slice(0, 5).map((_, index) => (
            <rect key={index} x={18 + index * 38} y={94 - ((index + 3) * 11 % 58)} width="18" height={((index + 3) * 11 % 58) + 8} rx="3" />
          ))}
        </svg>
        <div className="bi-donut" style={{ "--value": `${strength}%` }}>
          <strong>{Math.round(strength)}</strong>
          <span>信号</span>
        </div>
      </div>
      <div className="bi-list">
        {chart.slice(0, 4).map((column, index) => (
          <div key={column.name || index}>
            <span>{column.friendly_label || column.name}</span>
            <i style={{ width: `${80 - index * 9}%` }} />
          </div>
        ))}
      </div>
    </article>
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
function VerdictHero({ feasibility, verdict, running }) {
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
  return (
    <section className={`verdict-hero tone-${tone}`}>
      <div className="vh-left">
        <span className="vh-label">可行性结论{running ? " · 实时" : ""}</span>
        <h2 className="vh-judgment">{verdict}</h2>
        {conf ? <span className={`vh-conf ${conf}`}>{CONFIDENCE_LABELS[conf] || conf}</span> : null}
        {opportunity && typeof opportunity === "string" ? (
          <p className="vh-opp">{opportunity}</p>
        ) : (
          <p className="vh-opp muted">发起一次分析后，这里给出机会判断、置信度与可落地建议。</p>
        )}
      </div>
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
    </section>
  );
}

function OpportunityRadarCard({ feasibility, signalColumns, verdict }) {
  const dimensions = feasibility?.dimensions || [];
  const items = dimensions.length ? dimensions.slice(0, 5) : [
    { name: "market", score: signalColumns.length ? 3 : 0, confidence: "speculative" },
    { name: "asset_data", score: signalColumns.length ? 4 : 0, confidence: "data_confirmed" },
    { name: "differentiation_risk", score: signalColumns.length ? 3 : 0, confidence: "speculative" },
  ];
  return (
    <article className="dash-panel radar-panel">
      <div className="dash-panel-head">
        <span>机会雷达</span>
        <strong>{verdict}</strong>
      </div>
      <div className="radar-list">
        {items.map((item) => (
          <div key={item.name}>
            <span>{DIMENSION_LABELS[item.name] || item.name}</span>
            <i><b style={{ width: `${Math.max(0, Math.min(5, Number(item.score || 0))) * 20}%` }} /></i>
            <em>{item.score || 0}/5</em>
          </div>
        ))}
      </div>
    </article>
  );
}

function AutoAnalysisLog({ trace, runs, running }) {
  const rows = trace.length ? trace.slice(-8).reverse() : (runs || []).slice(0, 6).map((run) => ({ event: run.status || "completed", data: { agent: "df-coordinator", name: run.run_id || run.conversation_id }, time: run.created_at || run.updated_at }));
  return (
    <section className="dash-panel auto-log">
      <div className="dash-panel-head">
        <span>自动化流程记录</span>
        <strong>{running ? "正在写入 Trace" : "最近运行"}</strong>
      </div>
      <div className="auto-log-list">
        {rows.map((item, index) => (
          <div className="auto-log-row" key={`${item.event}-${index}`}>
            <Clock3 size={14} />
            <div>
              <strong>{eventTitle(item)}</strong>
              <span>{item.data?.agent || item.data?.name || formatTime(item.time)}</span>
            </div>
          </div>
        ))}
        {!rows.length ? <p className="empty-copy">上传数据或发起一次分析后，这里会记录每个 Agent 与工具调用。</p> : null}
      </div>
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
      <AnswerPanel messages={messages} streamText={streamText} running={running} presentation={presentation} onRun={onRun} />
      <Composer input={input} setInput={setInput} running={running} onRun={onRun} selectedPlaybook={selectedPlaybook} />
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
          <p>完成一次分析后，可在这里生成企划书、路线图、实验计划、活动海报、周边设计图和语音摘要。</p>
        </div>
        <button className="primary-button icon-label" type="button" onClick={onProduce} disabled={!artifact || producing}>
          {producing ? <Loader2 className="spin" size={15} /> : <FileDown size={15} />}
          生成全套产物
        </button>
      </section>
      <section className="logo-callout">
        <ImagePlus size={22} />
        <div>
          <strong>{refs.length ? `已检测到 ${refs.length} 张参考图` : "生成海报或周边前，建议上传透明 PNG Logo"}</strong>
          <span>例如攀岩馆活动海报、会员周边 T 恤、赞助合作物料，都可以把 Logo 作为参考图交给图像生成 Agent。</span>
        </div>
        <button className="ghost-button icon-label" type="button" onClick={onUploadReference}>
          <UploadCloud size={15} />
          上传参考图
        </button>
      </section>
      <OutputPanel artifacts={artifacts} artifact={artifact} running={producing} />
    </main>
  );
}

// 一步的输出/结果摘要
function stepDetail(ev) {
  const d = ev.data || {};
  if (ev.event === "tool_result") return d.count != null ? `${d.count} 条结果` : d.status ? String(d.status) : "";
  if (ev.event === "tool_call") return d.name ? "" : "";
  if (ev.event === "model_response") return d.usage?.total_tokens ? `${d.usage.total_tokens} tokens` : "";
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

function RunsCenter({ dashboard, trace, running }) {
  const runs = dashboard?.runs || [];
  return (
    <main className="agent-studio runs-stage">
      <section className="dashboard-hero">
        <div>
          <span className="eyeless-label">Runs</span>
          <h1>运行记录</h1>
          <p>每个 Agent 的作用，以及它这一轮调用了什么工具、输出了什么——让结论的来路可追溯。</p>
        </div>
      </section>
      <section className="runs-body">
        <div className="runs-col">
          <div className="runs-col-head">本轮 Agent 协作明细</div>
          <AgentRunLog trace={trace} />
        </div>
        <div className="runs-col narrow">
          <div className="runs-col-head">历史运行</div>
          <div className="run-table">
            {runs.slice(0, 12).map((run) => (
              <article key={run.run_id || run.conversation_id}>
                <Activity size={16} />
                <div>
                  <strong>{run.status || "completed"}</strong>
                  <span>{run.run_id || run.conversation_id || "run"}</span>
                </div>
                <em>{formatTime(run.created_at || run.updated_at)}</em>
              </article>
            ))}
            {!runs.length ? <p className="empty-copy">暂无运行记录。</p> : null}
          </div>
        </div>
      </section>
    </main>
  );
}

function SettingsCenter({ dashboard }) {
  const health = dashboard?.health || {};
  const deps = health.dependencies || {};
  return (
    <main className="agent-studio settings-stage">
      <section className="dashboard-hero">
        <div>
          <span className="eyeless-label">Settings</span>
          <h1>系统状态</h1>
          <p>当前环境用于测试和演示。这里展示 Agent 运行所依赖的 Foundry、Search、Blob、MCP 等连接状态。</p>
        </div>
      </section>
      <section className="settings-grid">
        <MetricCard icon={Sparkles} label="Foundry" value={deps.foundry ? "健康" : "检查中"} detail={health.dependency_details?.foundry?.endpoint || "Azure AI Foundry"} />
        <MetricCard icon={Search} label="Search" value={health.search_endpoint || deps.search ? "健康" : "检查中"} detail="Azure AI Search" />
        <MetricCard icon={Database} label="Blob" value={deps.blob ? "健康" : "检查中"} detail="上传工作区与产物持久化" />
        <MetricCard icon={Route} label="MCP" value={deps.mcp ? "健康" : "检查中"} detail="白名单工具与市场信息" />
      </section>
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
      <div className="route-card-foot">
        <span>{current.zh}</span>
        <strong>{foot}</strong>
        {hasFinal && !producing && !hasArtifacts && onProduce ? (
          <button className="produce-cta" type="button" onClick={onProduce}>
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

function AnswerPanel({ messages, streamText, running, presentation, onRun }) {
  const visible = messages.length || streamText;
  const scrollRef = useRef(null);
  const bottomRef = useRef(null);
  // 新消息/流式更新时自动滚到底，不用手动滚
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    bottomRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, streamText, running]);
  return (
    <div className="answer-panel">
      <div className="answer-panel-head">
        <div>
          <span>AI 分析</span>
          <strong>{running ? "实时生成中" : "结果输出"}</strong>
        </div>
        <div className={running ? "typing-indicator live" : "typing-indicator"}>
          <i /><i /><i />
          <span>{running ? presentation.caption : "等待输入"}</span>
        </div>
      </div>
      {visible ? (
        <div className="message-stack" ref={scrollRef}>
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
                      : <RichText text={message.text} />}
                  {message.citations?.length ? <CitationInline citations={message.citations} /> : null}
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
            <article className="chat-message assistant streaming waiting">
              <div className="speaker">AI</div>
              <div className="message-body">
                <span className="typing-dots" aria-label="Agent 正在思考"><i /><i /><i /></span>
              </div>
            </article>
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
function highlightTokens(text, kp) {
  const parts = String(text || "").split(/(\[\d+\]|\d+(?:\.\d+)?\s*[%％]|\d+(?:[,，]\d{3})+|\d+\s*[-–~]\s*\d+\s*(?:元|万|亿|%|％)?|\d+(?:\.\d+)?\s*(?:元|万|亿|倍|个|天|条|分))/g);
  return parts.map((part, i) => {
    if (/^\[\d+\]$/.test(part)) return <sup key={`${kp}-${i}`} className="cite-mark">{part.replace(/[[\]]/g, "")}</sup>;
    if (/[%％]|元|万|亿|倍|[-–~]|[,，]\d{3}|个|天|条|分/.test(part) && /\d/.test(part)) return <mark key={`${kp}-${i}`} className="num-hl">{part}</mark>;
    return <React.Fragment key={`${kp}-${i}`}>{part}</React.Fragment>;
  });
}

// 行内 markdown：**粗体** + `代码` + 数字/引用高亮
function inlineNodes(text, kp = "i") {
  const segs = String(text || "").split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return segs.map((seg, i) => {
    if (/^\*\*[^*]+\*\*$/.test(seg)) return <strong key={`${kp}-b${i}`}>{highlightTokens(seg.slice(2, -2), `${kp}-b${i}`)}</strong>;
    if (/^`[^`]+`$/.test(seg)) return <code key={`${kp}-c${i}`}>{seg.slice(1, -1)}</code>;
    return <React.Fragment key={`${kp}-${i}`}>{highlightTokens(seg, `${kp}-${i}`)}</React.Fragment>;
  });
}

// 轻量 markdown 渲染：标题 / 无序列表 / 有序列表 / 段落（段内单换行→换行）+ 行内粗体与高亮
function RichText({ text }) {
  const lines = String(text || "").split("\n");
  const blocks = [];
  let para = [];
  let list = null;
  const flushPara = () => { if (para.length) { blocks.push({ type: "p", lines: para }); para = []; } };
  const flushList = () => { if (list) { blocks.push(list); list = null; } };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) { flushPara(); flushList(); continue; }
    let m;
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { flushPara(); flushList(); blocks.push({ type: "h", text: m[1] }); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { flushPara(); if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; } list.items.push(m[1]); }
    else if ((m = line.match(/^\s*\d+[.、)]\s+(.*)/))) { flushPara(); if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; } list.items.push(m[1]); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  if (!blocks.length) return null;
  return (
    <div className="rich-text">
      {blocks.map((b, i) => {
        if (b.type === "h") return <h3 key={i}>{inlineNodes(b.text, `h${i}`)}</h3>;
        if (b.type === "ul") return <ul key={i}>{b.items.map((it, j) => <li key={j}>{inlineNodes(it, `u${i}-${j}`)}</li>)}</ul>;
        if (b.type === "ol") return <ol key={i}>{b.items.map((it, j) => <li key={j}>{inlineNodes(it, `o${i}-${j}`)}</li>)}</ol>;
        return <p key={i}>{b.lines.map((ln, j) => <React.Fragment key={j}>{j > 0 ? <br /> : null}{inlineNodes(ln, `p${i}-${j}`)}</React.Fragment>)}</p>;
      })}
    </div>
  );
}

function CitationInline({ citations }) {
  return (
    <div className="citation-inline">
      {citations.slice(0, 5).map((citation, index) => (
        <span key={`${citation.ref || citation.source || index}`} className={`confidence-pill mini ${citation.confidence || "speculative"}`}>
          {index + 1}. {CONFIDENCE_LABELS[citation.confidence] || "证据"}
        </span>
      ))}
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
function ActionPlanCards({ selected, onSelect, feasibility }) {
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
  const steps = Array.isArray(feasibility?.action_plan) ? feasibility.action_plan : [];
  const gaps = Array.isArray(feasibility?.gap_list) ? feasibility.gap_list : [];
  return (
    <section className="action-plan">
      <div className="ap-head"><span>行动计划</span><em>PM 方法 · 点卡片看怎么用</em></div>
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
          <em>{info.what}</em>
        </div>
        {opp ? <p className="ap-detail-opp">针对机会：<b>{opp}</b></p> : null}
        <ul className="ap-detail-list">
          {(info.points || []).map((pt, i) => <li key={i}>{pt}</li>)}
        </ul>
        {(steps[0] || gaps[0]) ? (
          <div className="ap-detail-foot">
            {steps[0] ? <span className="ap-next">下一步 · {String(steps[0]).replace(/\s*\[\d+\]/g, "").slice(0, 54)}…</span> : null}
            {gaps[0] ? <span className="ap-gap">缺口 · {String(gaps[0]).slice(0, 36)}</span> : null}
          </div>
        ) : null}
      </div>
    </section>
  );
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
          <span>{playbook.name}</span>
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

function Composer({ input, setInput, running, onRun }) {
  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        onRun(input);
      }}
    >
      <div className="composer-field">
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="继续追问，例如：这个活动值得再办一次吗？"
          disabled={running}
        />
      </div>
      <button className="send-button" type="submit" disabled={running || !input.trim()}>
        {running ? <Loader2 className="spin" size={18} /> : <Send size={18} />}
      </button>
    </form>
  );
}

export function Inspector({ tab, setTab, trace, finalArtifact, artifacts, running, producing }) {
  const evidence = useMemo(() => collectEvidence(finalArtifact), [finalArtifact]);
  return (
    <aside className="inspector">
      <div className="inspector-tabs">
        {INSPECTOR_TABS.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.id} type="button" className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>
              <Icon size={15} />
              {item.label}
            </button>
          );
        })}
      </div>
      {tab === "evidence" ? <EvidencePanel evidence={evidence} running={running} /> : null}
      {tab === "trace" ? <TracePanel trace={trace} running={running} /> : null}
      {tab === "output" ? <OutputPanel artifacts={artifacts} artifact={finalArtifact} running={running || producing} /> : null}
    </aside>
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

function OutputPanel({ artifacts, artifact, running }) {
  const analysisReady = Boolean(artifact);
  const artifactMap = {
    pdf: artifacts.pdf,
    concept_image: artifacts.concept_image,
    audio_summary: artifacts.audio_summary,
  };
  return (
    <div className="inspector-body output-body">
      <div className="output-hint">
        <strong>{analysisReady ? "产物入口" : "先完成一次分析"}</strong>
        <span>{analysisReady ? "可按需要生成或打开项目产物。" : "完成分析后，这里会显示 PRD、路线图、实验计划、定价建议和项目书入口。"}</span>
      </div>
      {ARTIFACT_GROUPS.map((item) => {
        const Icon = item.icon;
        const file = artifactMap[item.id];
        const href = artifactLink(file);
        const generated = Boolean(href);
        return (
          <article className="output-card" key={item.id}>
            <div>
              <Icon size={18} />
              <strong>{item.title}</strong>
              <span>{file?.bytes ? `${Math.round(file.bytes / 1024)} KB` : generated ? "已生成" : running ? "生成中" : analysisReady ? "可生成" : "待分析"}</span>
            </div>
            <p>{item.description}</p>
            {item.id === "concept_image" && href ? <img src={href} alt="概念图产物" /> : null}
            {item.id === "audio_summary" && href ? <audio src={href} controls /> : null}
            <a className={href ? "output-link" : "output-link disabled"} href={href || undefined} target="_blank" rel="noreferrer">
              {href ? "打开" : "等待产物"} <ArrowUpRight size={14} />
            </a>
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
          {notice.type === "error" ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
          <span>{notice.message}</span>
          <button type="button" onClick={onDismiss}><X size={14} /></button>
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
