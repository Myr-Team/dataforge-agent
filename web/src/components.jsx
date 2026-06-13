import React, { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  Bell,
  CheckCircle2,
  ChevronDown,
  Database,
  FileDown,
  FileText,
  Loader2,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
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

export function ShellNav() {
  return (
    <nav className="shell-nav" aria-label="Primary">
      <div className="brand-mark">D</div>
      <div className="nav-stack">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.id} className={item.id === "workspaces" ? "nav-icon active" : "nav-icon"} type="button" title={item.label}>
              <Icon size={19} />
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

export function TopBar({ dashboard, workspaceId, onWorkspaceChange, onUpload, loading, user, authState, onLogout }) {
  const workspaces = dashboard?.workspaces || [];
  const health = dashboard?.health || {};
  const deps = health.dependencies || {};
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="window-lights" aria-hidden="true">
          <i className="red" /><i className="amber" /><i className="green" />
        </div>
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
        <button className="primary-button icon-label" type="button" onClick={onUpload}>
          <UploadCloud size={16} />
          上传数据
        </button>
        <button className="icon-button top-icon" type="button" title="通知">
          <Bell size={16} />
        </button>
        <div className={loading ? "sync-dot loading" : "sync-dot"} title={loading ? "同步中" : "已同步"}>
          {loading ? <Loader2 size={14} /> : <CheckCircle2 size={14} />}
        </div>
        <div className="user-menu">
          <div className="avatar" title={user?.email || "DataForge"}>
            {(user?.name || user?.email || "D").trim().slice(0, 1).toUpperCase()}
          </div>
          <div>
            <strong>{user?.name || "Demo User"}</strong>
            <span>{authState === "authenticated" ? user?.email || "Azure 登录" : "本地演示态"}</span>
          </div>
          <button className="icon-button" type="button" onClick={onLogout} title="退出登录">
            <LogOut size={15} />
          </button>
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
  const runs = dashboard?.runs || [];
  const conversations = dashboard?.conversations || [];
  const columns = workspace.columns || [];
  const signalColumns = columns.filter((column) => column.signal && column.signal !== "noise").slice(0, 8);
  const noisyColumns = columns.filter((column) => column.signal === "noise").slice(0, 5);
  const canDelete = workspaceId?.startsWith("upload-");
  const documents = workspace.documents || [];

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
        <p>{workspace.profile_summary || workspace.customer_summary || "等待数据画像。"}</p>
        <div className="metric-row">
          <Metric value={workspace.doc_count ?? documents.length ?? 0} label="数据集" />
          <Metric value={workspace.row_count ?? workspace.indexed_count ?? 0} label="行数" />
          <Metric value={workspace.format || "mixed"} label="格式" />
        </div>
        <div className="workspace-actions">
          <button className="primary-button" type="button" onClick={onUpload}>
            <Plus size={16} />
            添加数据
          </button>
          <button className="danger-button" type="button" onClick={onDeleteWorkspace} disabled={!canDelete || deleting}>
            {deleting ? <Loader2 size={15} /> : <Trash2 size={15} />}
            删除
          </button>
        </div>
      </section>

      <section className="pane-section">
        <div className="section-head"><span>数据画像</span><em>{signalColumns.length ? "已解析" : "等待信号"}</em></div>
        <DataPortrait signalColumns={signalColumns} noisyColumns={noisyColumns} />
      </section>

      <section className="pane-section">
        <div className="section-head"><span>数据集</span><em>{documents.length}</em></div>
        <div className="dataset-list">
          {documents.slice(0, 8).map((doc) => (
            <div className="dataset-row" key={doc.source_file || doc.name}>
              <FileText size={15} />
              <div>
                <strong>{doc.name || sanitizeSourceLabel(doc.source_file)}</strong>
                <span>{doc.format || "文件"} / {formatBytes(doc.bytes)}</span>
              </div>
              <em>{doc.status || "已解析"}</em>
            </div>
          ))}
          {!documents.length ? <p className="empty-copy">暂无文件。</p> : null}
        </div>
      </section>

      {(workspace.reference_images || []).length ? (
        <section className="pane-section">
          <div className="section-head"><span>参考图</span><em>{workspace.reference_images.length}</em></div>
          <div className="reference-strip">
            {workspace.reference_images.slice(0, 5).map((item) => (
              <img key={item.url || item.filename} src={absoluteApiUrl(item.url)} alt={item.filename || "reference"} />
            ))}
          </div>
        </section>
      ) : null}

      <div className="split-lists">
        <section className="pane-section">
          <div className="section-head"><span>运行记录</span><em>{runs.length}</em></div>
          <div className="compact-list">
            {runs.slice(0, 5).map((run) => (
              <div className="run-row" key={run.run_id || run.conversation_id}>
                <span className={run.status === "error" ? "run-dot" : "run-dot ok"} />
                <div>
                  <strong>{run.status || "completed"}</strong>
                  <span>{formatTime(run.created_at || run.updated_at)}</span>
                </div>
              </div>
            ))}
            {!runs.length ? <p className="empty-copy">暂无运行记录。</p> : null}
          </div>
        </section>
        <section className="pane-section">
          <div className="section-head"><span>会话</span><em>{conversations.length}</em></div>
          <div className="compact-list">
            {conversations.slice(0, 5).map((conversation) => (
              <button className="conversation-row" type="button" key={conversation.conversation_id} onClick={() => onOpenConversation(conversation.conversation_id)}>
                <span className="message-mini"><MessageSquare size={12} /></span>
                <span>{conversation.preview || conversation.title || conversation.conversation_id}</span>
              </button>
            ))}
            {!conversations.length ? <p className="empty-copy">暂无会话。</p> : null}
          </div>
        </section>
      </div>
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

function DataPortrait({ signalColumns, noisyColumns }) {
  const strength = Math.min(98, Math.max(12, signalColumns.length * 12 + 28));
  return (
    <div className="portrait-card">
      <div className="portrait-ring" style={{ "--value": `${strength}%` }}>
        <strong>{signalColumns.length ? strength : 0}</strong>
        <span>信号强度</span>
      </div>
      <div className="portrait-bars">
        {signalColumns.length ? signalColumns.slice(0, 4).map((column, index) => (
          <div className="portrait-bar" key={column.name}>
            <span>{column.friendly_label || column.name}</span>
            <i style={{ width: `${72 - index * 8}%` }} />
          </div>
        )) : <p className="empty-copy">暂无显著信号。</p>}
        {noisyColumns.length ? <em>噪声字段：{noisyColumns.map((item) => item.friendly_label || item.name).join("、")}</em> : null}
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

function AgentRoute({ trace, running, presentation }) {
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
      <div className="route-svg-wrap">
        <svg className="route-svg" viewBox="0 0 940 144" role="img" aria-label="Agent route">
          <defs>
            <linearGradient id="agent-flow-gradient" x1="0" x2="1">
              <stop offset="0%" stopColor="#0071e3" />
              <stop offset="52%" stopColor="#00a878" />
              <stop offset="100%" stopColor="#b26a00" />
            </linearGradient>
          </defs>
          <path className="route-line base" d="M70 58 C190 10 240 106 362 58 S536 10 656 58 792 106 870 58" />
          <path className={running || presentation.maxStage > 0 ? "route-line live" : "route-line"} d="M70 58 C190 10 240 106 362 58 S536 10 656 58 792 106 870 58" />
          {AGENTS.map((agent, index) => {
            const x = 70 + index * 160;
            const active = activeAgent === agent.id;
            const done = responded.has(agent.id);
            const Icon = agent.icon;
            return (
              <g key={agent.id} className={active ? "route-node active" : done ? "route-node done" : "route-node"}>
                <circle cx={x} cy="58" r="22" />
                <foreignObject x={x - 10} y="48" width="20" height="20">
                  <Icon size={18} strokeWidth={2} />
                </foreignObject>
                <text x={x} y="102" textAnchor="middle">{agent.zh}</text>
                <text className="route-node-role" x={x} y="120" textAnchor="middle">{agent.role}</text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="flow-wave" aria-hidden="true">
        {Array.from({ length: 42 }).map((_, index) => (
          <i key={index} style={{ "--i": index, "--h": `${18 + ((index * 17 + presentation.pulse) % 34)}px` }} />
        ))}
      </div>
      <div className="agent-row">
        {AGENTS.map((agent) => {
          const Icon = agent.icon;
          const active = activeAgent === agent.id;
          const done = responded.has(agent.id);
          return (
            <div key={agent.id} className={active ? "agent-chip active" : done ? "agent-chip done" : "agent-chip"}>
              <Icon size={16} />
              <div>
                <strong>{agent.zh}</strong>
                <span>{agent.name}</span>
              </div>
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
          return <ul key={index}>{items.map((item) => <li key={item}>{item}</li>)}</ul>;
        }
        return <p key={index}>{trimmed}</p>;
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

export function UploadModal({ open, busy, onClose, onSubmit }) {
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
  const addFiles = (list) => setFiles((items) => [...items, ...Array.from(list || [])]);
  return (
    <div className="modal-overlay" role="presentation">
      <div className="upload-modal" role="dialog" aria-modal="true" aria-label="上传数据">
        <div className="modal-head">
          <div>
            <strong>上传数据</strong>
            <span>CSV / Excel / JSON / MD / 参考图</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose} disabled={busy}>
            <X size={17} />
          </button>
        </div>
        <label className="modal-field">
          <span>工作区名称</span>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：华东门店运营数据" />
        </label>
        <label className="modal-field">
          <span>备注</span>
          <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="数据背景、目标客群、已有产物约束" rows={3} />
        </label>
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
          <strong>拖入文件或点击选择</strong>
          <span>支持多文件与参考图</span>
          <input type="file" multiple hidden onChange={(event) => addFiles(event.target.files)} />
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
          <button className="primary-button" type="button" disabled={busy || !files.length} onClick={() => onSubmit({ name, description, files })}>
            {busy ? <Loader2 size={15} /> : <UploadCloud size={15} />}
            上传入库
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
