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

export function ShellNav({ active = "workspaces", onChange = () => {} }) {
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
      <button className="nav-icon collapse" type="button" title="收起">
        <ChevronDown size={18} />
      </button>
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
        <div className="health-chips">
          <StatusChip label="Foundry" ok={deps.foundry} />
          <StatusChip label="Search" ok={health.search_endpoint || deps.search} />
          <StatusChip label="Blob" ok={deps.blob} />
        </div>
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
          {loading ? <Loader2 size={14} /> : <CheckCircle2 size={14} />}
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
            {deleting ? <Loader2 size={15} /> : <Trash2 size={15} />}
            删除工作区
          </button>
        </div>
      </section>

      <section className="pane-section">
        <div className="section-head"><span>数据集</span><em>{documents.length}</em></div>
        <div className="dataset-list">
          {documents.slice(0, 8).map((doc) => {
            const meta = fileTypeMeta(doc);
            const ready = !doc.status || /就绪|已解析|ready|done/i.test(String(doc.status));
            return (
              <div className="dataset-row" key={doc.source_file || doc.name}>
                <FileTypeIcon doc={doc} />
                <div className="dataset-meta">
                  <strong>{doc.name || sanitizeSourceLabel(doc.source_file)}</strong>
                  <span>{meta.label}{doc.bytes ? ` · ${formatBytes(doc.bytes)}` : ""}</span>
                </div>
                <em className={ready ? "ds-status ready" : "ds-status partial"}>{doc.status || "已就绪"}</em>
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
        <AnswerPanel messages={messages} streamText={streamText} running={running} presentation={presentation} />
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
  const presentation = useAgentPresentation(trace, running);
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

      <AgentRoute trace={trace} running={running} presentation={presentation} />

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
          <p>围绕「{workspace.name || "当前工作区"}」直接提问。Agent 会根据工作区证据、市场推断和审计结果回答，不把外部来源当作内部事实。</p>
        </div>
        <PlaybookBar selected={selectedPlaybook} onSelect={setSelectedPlaybook} artifactMode="chat" onMode={() => {}} />
      </section>
      <QuestionStarter onRun={onRun} running={running} />
      <AnswerPanel messages={messages} streamText={streamText} running={running} presentation={presentation} />
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
          {producing ? <Loader2 size={15} /> : <FileDown size={15} />}
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

function RunsCenter({ dashboard, trace, running }) {
  const runs = dashboard?.runs || [];
  return (
    <main className="agent-studio runs-stage">
      <section className="dashboard-hero">
        <div>
          <span className="eyeless-label">Runs</span>
          <h1>运行记录</h1>
          <p>记录 Agent 路由、工具调用、模型响应、审计和产物生成步骤，便于客户理解结论从哪里来。</p>
        </div>
      </section>
      <AutoAnalysisLog trace={trace} runs={runs} running={running} />
      <section className="run-table">
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

function useAgentPresentation(trace, running) {
  const actualActive = [...trace].reverse().find((item) => item.event === "role_change")?.data?.agent;
  const actualDone = useMemo(() => new Set(trace
    .filter((item) => ["model_response", "tool_result", "audit", "final"].includes(item.event))
    .map((item) => item.data?.agent)
    .filter(Boolean)), [trace]);
  const [stage, setStage] = useState(0);
  const [pulse, setPulse] = useState(0);

  useEffect(() => {
    if (!running && !trace.length) {
      setStage(0);
      return undefined;
    }
    const timer = window.setInterval(() => {
      setStage((value) => {
        const target = actualActive ? Math.max(AGENTS.findIndex((agent) => agent.id === actualActive), value) : value;
        return Math.min(AGENTS.length - 1, Math.max(target, value + (running ? 1 : 0)));
      });
      setPulse((value) => value + 1);
    }, 720);
    return () => window.clearInterval(timer);
  }, [running, trace.length, actualActive]);

  const activeAgent = actualActive || AGENTS[Math.min(stage, AGENTS.length - 1)]?.id || AGENTS[0].id;
  const doneAgents = new Set(actualDone);
  AGENTS.slice(0, Math.min(stage, AGENTS.length - 1)).forEach((agent) => doneAgents.add(agent.id));
  if (trace.some((item) => item.event === "final")) AGENTS.forEach((agent) => doneAgents.add(agent.id));
  const activeIndex = AGENTS.findIndex((agent) => agent.id === activeAgent);
  const captions = [
    "识别问题意图与所需专家",
    "检索工作区资料并压缩证据",
    "按五维 rubric 生成可行性判断",
    "补充外部市场和竞品线索",
    "核验来源、置信度和过强结论",
    "准备项目书、概念图和语音摘要",
  ];
  return {
    activeAgent,
    activeIndex: Math.max(0, activeIndex),
    doneAgents,
    maxStage: stage,
    pulse,
    caption: captions[Math.max(0, activeIndex)] || "多 Agent 协同执行中",
  };
}

function AgentRoute({ trace, running, presentation, compact = false }) {
  const activeAgent = presentation.activeAgent;
  const responded = presentation.doneAgents;
  const current = AGENTS.find((agent) => agent.id === activeAgent) || AGENTS[0];
  const runId = trace.find((item) => item.event === "ready")?.data?.conversation_id || "pending";
  return (
    <section className="agent-route-card">
      <div className="route-card-head">
        <div>
          <strong>Agent Flow</strong>
          <span>run · {String(runId).slice(0, 12)} · {running ? "streaming" : "idle"}</span>
        </div>
        <div className={running ? "route-live live" : "route-live"}>
          <i />
          {running ? "运行中" : "待命"}
        </div>
      </div>
      <div className="pipe-flow" role="list" aria-label="Agent 流水线">
        {AGENTS.map((agent, index) => {
          const active = activeAgent === agent.id;
          const done = responded.has(agent.id);
          const Icon = agent.icon;
          const state = active ? "active" : done ? "done" : "idle";
          return (
            <div className={`pf-node ${state}`} role="listitem" key={agent.id}>
              <span className="pf-ic"><Icon size={18} strokeWidth={2.1} /></span>
              <span className="pf-name">{agent.zh}</span>
              <span className="pf-role">{agent.role}</span>
              {index < AGENTS.length - 1 ? <span className={done ? "pf-link lit" : "pf-link"} aria-hidden="true" /> : null}
            </div>
          );
        })}
      </div>
      <div className="route-card-foot">
        <span>{current.zh}</span>
        <strong>{running ? presentation.caption : "选择一个问题后开始编排多 Agent 分析"}</strong>
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

function AnswerPanel({ messages, streamText, running, presentation }) {
  const visible = messages.length || streamText;
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
        <div className="message-stack">
          {messages.map((message, index) => (
            <article key={`${message.role}-${index}`} className={`chat-message ${message.role}`}>
              <div className="speaker">{message.role === "user" ? "你" : "AI"}</div>
              <div className="message-body">
                <RichText text={message.text} />
                {message.citations?.length ? <CitationInline citations={message.citations} /> : null}
              </div>
            </article>
          ))}
          {streamText ? (
            <article className="chat-message assistant streaming">
              <div className="speaker">AI</div>
              <div className="message-body">
                <RichText text={streamText} />
                {running ? <span className="cursor" /> : null}
              </div>
            </article>
          ) : null}
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
function highlightInline(text) {
  const parts = String(text || "").split(/(\[\d+\]|\d+(?:\.\d+)?\s*[%％]|\d+(?:[,，]\d{3})+|\d+\s*[-–~]\s*\d+\s*(?:元|万|亿|%|％)?|\d+(?:\.\d+)?\s*(?:元|万|亿|倍|个|天|条|分))/g);
  return parts.map((part, i) => {
    if (/^\[\d+\]$/.test(part)) return <sup key={i} className="cite-mark">{part.replace(/[[\]]/g, "")}</sup>;
    if (/[%％]|元|万|亿|倍|[-–~]|[,，]\d{3}|个|天|条|分/.test(part) && /\d/.test(part)) return <mark key={i} className="num-hl">{part}</mark>;
    return part;
  });
}

function RichText({ text }) {
  const blocks = String(text || "").split(/\n{2,}/).filter(Boolean);
  if (!blocks.length) return null;
  return (
    <div className="rich-text">
      {blocks.map((block, index) => {
        const trimmed = block.trim();
        if (/^#{1,3}\s/.test(trimmed)) return <h3 key={index}>{trimmed.replace(/^#{1,3}\s/, "")}</h3>;
        if (/^[-*]\s/m.test(trimmed)) {
          const items = trimmed.split("\n").map((line) => line.replace(/^[-*]\s*/, "").trim()).filter(Boolean);
          return <ul key={index}>{items.map((item, i) => <li key={i}>{highlightInline(item)}</li>)}</ul>;
        }
        return <p key={index}>{highlightInline(trimmed)}</p>;
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

// 行动计划（PM 方法）：6 个方法卡，点选驱动下方 ActionBoard（对齐 效果.png）
function ActionPlanCards({ selected, onSelect, feasibility }) {
  const dims = feasibility?.dimensions || [];
  const metric = (i) => {
    const d = dims[i];
    if (d) return `${DIMENSION_LABELS[d.name] || d.name} ${d.score ?? 0}/5`;
    return ["核心机会", "痛点假设", "价值锚点", "里程碑", "验收指标", "成功门槛"][i] || "查看";
  };
  return (
    <section className="action-plan">
      <div className="ap-head"><span>行动计划</span><em>PM 方法</em></div>
      <div className="ap-grid">
        {PLAYBOOKS.map((item, index) => {
          const Icon = item.icon;
          return (
            <button key={item.id} type="button" className={selected === item.id ? "ap-card active" : "ap-card"} onClick={() => onSelect(item.id)}>
              <span className="ap-ic"><Icon size={18} /></span>
              <strong>{item.name}</strong>
              <span className="ap-desc">{PLAYBOOK_DESC[item.id] || item.prompt}</span>
              <span className="ap-metric">{metric(index)}</span>
              <span className="ap-link">查看{item.name} →</span>
            </button>
          );
        })}
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
          {producing ? <Loader2 size={15} /> : <FileDown size={15} />}
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

function Composer({ input, setInput, running, onRun, selectedPlaybook }) {
  const playbook = PLAYBOOKS.find((item) => item.id === selectedPlaybook) || PLAYBOOKS[0];
  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        onRun(input);
      }}
    >
      <div className="composer-field">
        <span>{playbook.name}</span>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="分析这份数据最适合产品化成什么机会？"
          disabled={running}
        />
      </div>
      <button className="send-button" type="submit" disabled={running || !input.trim()}>
        {running ? <Loader2 size={18} /> : <Send size={18} />}
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
            {busy ? <Loader2 size={15} /> : <UploadCloud size={15} />}
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
          {uploadState.type === "error" ? <AlertTriangle size={16} /> : uploadState.type === "done" ? <CheckCircle2 size={16} /> : <Loader2 size={16} />}
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
