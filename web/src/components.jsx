import React, { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  Database,
  FileDown,
  FileText,
  Globe2,
  Image,
  Loader2,
  MessageSquare,
  Mic2,
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
  ARTIFACT_MODES,
  CONFIDENCE_LABELS,
  DIMENSION_LABELS,
  INSPECTOR_TABS,
  NAV_ITEMS,
  PLAYBOOKS,
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
    </nav>
  );
}

export function TopBar({ dashboard, workspaceId, onWorkspaceChange, onUpload, loading, user }) {
  const workspaces = dashboard?.workspaces || [];
  const health = dashboard?.health || {};
  const deps = health.dependencies || {};
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div>
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
        <button className="ghost-button icon-label" type="button" onClick={onUpload}>
          <UploadCloud size={16} />
          上传
        </button>
        <div className={loading ? "sync-dot loading" : "sync-dot"} title={loading ? "同步中" : "已同步"}>
          {loading ? <Loader2 size={14} /> : <CheckCircle2 size={14} />}
        </div>
        <div className="avatar" title={user?.email || "DataForge"}>
          {(user?.name || user?.email || "D").trim().slice(0, 1).toUpperCase()}
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
      {label}
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

  return (
    <aside className="workspace-pane">
      <section className="pane-section workspace-hero">
        <div className="section-head">
          <span>Workspace</span>
          <button className="icon-button" type="button" onClick={onRefresh} title="刷新">
            <RefreshCw size={15} />
          </button>
        </div>
        <h2>{workspace.name || workspaceId}</h2>
        <p>{workspace.profile_summary || workspace.customer_summary || "等待数据画像。"}</p>
        <div className="metric-row">
          <Metric label="Docs" value={workspace.doc_count ?? 0} />
          <Metric label="Rows" value={workspace.rows ?? 0} />
          <Metric label="Format" value={workspace.format || "mixed"} />
        </div>
        <div className="workspace-actions">
          <button className="primary-button" type="button" onClick={onUpload}>
            <Plus size={16} />
            添加数据
          </button>
          <button className="danger-button" type="button" disabled={!canDelete || deleting} onClick={onDeleteWorkspace}>
            {deleting ? <Loader2 size={15} /> : <Trash2 size={15} />}
            删除
          </button>
        </div>
      </section>

      <section className="pane-section">
        <div className="section-head"><span>Signals</span><em>{signalColumns.length}</em></div>
        <div className="signal-list">
          {signalColumns.length ? signalColumns.map((column) => (
            <span className="signal-chip" key={`${column.table || "table"}-${column.name}`}>
              {column.friendly_label || column.name}
              <em>{column.role || "signal"}</em>
            </span>
          )) : <p className="empty-copy">暂无显著信号。</p>}
        </div>
        {noisyColumns.length ? (
          <div className="noise-row">
            {noisyColumns.map((column) => <span key={column.name}>{column.friendly_label || column.name}</span>)}
          </div>
        ) : null}
      </section>

      <section className="pane-section">
        <div className="section-head"><span>Datasets</span><em>{(workspace.documents || []).length}</em></div>
        <div className="dataset-list">
          {(workspace.documents || []).slice(0, 8).map((doc) => (
            <div className="dataset-row" key={doc.source_file || doc.name}>
              <FileText size={15} />
              <div>
                <strong>{doc.name || doc.source_file}</strong>
                <span>{doc.format || "file"} · {formatBytes(doc.bytes)}</span>
              </div>
            </div>
          ))}
          {!(workspace.documents || []).length ? <p className="empty-copy">暂无文件。</p> : null}
        </div>
      </section>

      {(workspace.reference_images || []).length ? (
        <section className="pane-section">
          <div className="section-head"><span>References</span><em>{workspace.reference_images.length}</em></div>
          <div className="reference-strip">
            {workspace.reference_images.slice(0, 5).map((item) => (
              <img key={item.url || item.filename} src={absoluteApiUrl(item.url)} alt={item.filename || "reference"} />
            ))}
          </div>
        </section>
      ) : null}

      <section className="pane-section split-lists">
        <div>
          <div className="section-head"><span>Runs</span><em>{runs.length}</em></div>
          <div className="compact-list">
            {runs.slice(0, 5).map((run) => <RunRow run={run} key={run.run_id} />)}
            {!runs.length ? <p className="empty-copy">暂无运行。</p> : null}
          </div>
        </div>
        <div>
          <div className="section-head"><span>Conversations</span><em>{conversations.length}</em></div>
          <div className="compact-list">
            {conversations.slice(0, 5).map((item) => (
              <button className="conversation-row" type="button" key={item.conversation_id} onClick={() => onOpenConversation(item.conversation_id)}>
                <MessageIcon />
                <span>{item.title || "Untitled"}</span>
              </button>
            ))}
            {!conversations.length ? <p className="empty-copy">暂无会话。</p> : null}
          </div>
        </div>
      </section>
    </aside>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <strong>{String(value)}</strong>
      <span>{label}</span>
    </div>
  );
}

function RunRow({ run }) {
  return (
    <div className="run-row">
      <span className={run.status === "completed" ? "run-dot ok" : "run-dot"} />
      <div>
        <strong>{VERDICT_LABELS[run.verdict] || run.verdict || run.status || "run"}</strong>
        <span>{formatTime(run.time)} · {run.step_count || 0} steps</span>
      </div>
    </div>
  );
}

function MessageIcon() {
  return <span className="message-mini"><MessageSquare size={12} /></span>;
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
  onProduce,
  producing,
}) {
  const workspace = dashboard?.workspace || {};
  const feasibility = finalArtifact?.feasibility || {};
  const verdict = VERDICT_LABELS[feasibility.verdict] || VERDICT_LABELS[finalArtifact?.routing?.intent] || "待分析";
  const confidence = CONFIDENCE_LABELS[feasibility.overall_confidence] || feasibility.overall_confidence || "待验证";

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

      <AgentRoute trace={trace} running={running} />
      <PlaybookBar selected={selectedPlaybook} onSelect={setSelectedPlaybook} artifactMode={artifactMode} onMode={setArtifactMode} />

      <section className="answer-surface">
        <AnswerPanel messages={messages} streamText={streamText} running={running} />
        <FeasibilityStrip feasibility={feasibility} />
        <ActionBoard artifact={finalArtifact} selectedPlaybook={selectedPlaybook} onProduce={onProduce} producing={producing} />
      </section>

      <Composer input={input} setInput={setInput} running={running} onRun={onRun} selectedPlaybook={selectedPlaybook} />
    </main>
  );
}

function AgentRoute({ trace, running }) {
  const activeAgent = [...trace].reverse().find((item) => item.event === "role_change")?.data?.agent;
  const responded = new Set(trace.filter((item) => item.event === "model_response" || item.event === "tool_result" || item.event === "audit").map((item) => item.data?.agent).filter(Boolean));
  return (
    <section className="agent-route-card">
      <div className="route-svg-wrap">
        <svg className="route-svg" viewBox="0 0 900 92" role="img" aria-label="Agent route">
          <path className={running ? "route-line live" : "route-line"} d="M62 46 C190 2 255 90 372 46 S548 2 666 46 790 82 838 46" />
          {AGENTS.map((agent, index) => {
            const x = 62 + index * 155;
            const active = activeAgent === agent.id;
            const done = responded.has(agent.id);
            return (
              <g key={agent.id} className={active ? "route-node active" : done ? "route-node done" : "route-node"}>
                <circle cx={x} cy="46" r="16" />
                <text x={x} y="51" textAnchor="middle">{index + 1}</text>
              </g>
            );
          })}
        </svg>
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
                <strong>{agent.name}</strong>
                <span>{agent.role}</span>
              </div>
            </div>
          );
        })}
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

function AnswerPanel({ messages, streamText, running }) {
  const visible = messages.length || streamText;
  return (
    <div className="answer-panel">
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
          <span>上传数据或选择工作区后开始。</span>
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

export function Inspector({
  tab,
  setTab,
  trace,
  finalArtifact,
  artifacts,
  running,
  producing,
}) {
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
      {tab === "output" ? <OutputPanel artifacts={artifacts} running={running || producing} /> : null}
    </aside>
  );
}

function EvidencePanel({ evidence, running }) {
  return (
    <div className="inspector-body">
      <EvidenceFlow running={running} count={evidence.length} />
      <div className="evidence-list">
        {evidence.map((item, index) => (
          <article className="evidence-card" key={`${item.ref || item.source || index}`}>
            <div>
              <strong>{item.title || item.claim || item.source || `证据 ${index + 1}`}</strong>
              <span className={`confidence-pill mini ${item.confidence || item.source_kind || "speculative"}`}>
                {CONFIDENCE_LABELS[item.confidence || item.source_kind] || item.source_kind || "待验证"}
              </span>
            </div>
            <p>{item.quote || item.snippet || item.content || item.url || "无摘录。"}</p>
            <code>{item.ref || item.source_file || item.url || item.source || "source"}</code>
          </article>
        ))}
        {!evidence.length ? <EmptyInspector icon={ShieldCheck} text="暂无证据。" /> : null}
      </div>
    </div>
  );
}

function EvidenceFlow({ running, count }) {
  return (
    <div className="evidence-flow">
      <svg viewBox="0 0 320 44" aria-hidden="true">
        <path className={running ? "flow-path live" : "flow-path"} d="M8 22 H90 C118 22 116 8 146 8 H176 C206 8 202 36 232 36 H312" />
        {[8, 90, 176, 232, 312].map((x, index) => <circle key={x} cx={x} cy={index === 3 ? 36 : index === 2 ? 8 : 22} r="4" />)}
      </svg>
      <span>{count} evidence items</span>
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
  return (
    <article className={`trace-item ${item.event}`}>
      <span className="trace-dot" />
      <div>
        <strong>{agent?.name || item.data?.agent || title}</strong>
        <p>{title}</p>
        {item.data?.name ? <code>{item.data.name}</code> : null}
      </div>
    </article>
  );
}

function OutputPanel({ artifacts, running }) {
  const items = [
    { id: "pdf", title: "项目书", icon: FileText, artifact: artifacts.pdf },
    { id: "concept_image", title: "概念图", icon: Image, artifact: artifacts.concept_image },
    { id: "audio_summary", title: "语音摘要", icon: Mic2, artifact: artifacts.audio_summary },
  ];
  return (
    <div className="inspector-body output-body">
      {items.map((item) => {
        const Icon = item.icon;
        const href = artifactLink(item.artifact);
        return (
          <article className="output-card" key={item.id}>
            <div>
              <Icon size={18} />
              <strong>{item.title}</strong>
              <span>{item.artifact?.bytes ? `${Math.round(item.artifact.bytes / 1024)} KB` : running ? "生成中" : "待生成"}</span>
            </div>
            {item.id === "concept_image" && href ? <img src={href} alt="概念图产物" /> : null}
            {item.id === "audio_summary" && href ? <audio src={href} controls /> : null}
            <a className={href ? "output-link" : "output-link disabled"} href={href || undefined} target="_blank" rel="noreferrer">
              打开 <ArrowUpRight size={14} />
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
            <span>CSV / Excel / JSON / MD / Reference images</span>
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
          <button
            className="primary-button"
            type="button"
            disabled={busy || !files.length}
            onClick={() => onSubmit({ name, description, files })}
          >
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
  for (const citation of artifact?.citations || artifact?.answer?.citations || []) items.push(citation);
  for (const dimension of artifact?.feasibility?.dimensions || []) {
    for (const evidence of dimension.evidence || []) items.push({ ...evidence, title: DIMENSION_LABELS[dimension.name] || dimension.name, confidence: dimension.confidence });
  }
  for (const hit of artifact?.corpus?.hits || []) items.push({ ...hit, confidence: "data_confirmed", title: hit.title || hit.source_file });
  for (const finding of artifact?.market?.external_findings || []) items.push({ ...finding, confidence: "market_inferred", title: finding.claim || finding.title || "市场来源" });
  for (const source of artifact?.market?.sources || []) items.push({ source, url: source, confidence: "market_inferred", title: "Foundry web source" });
  const seen = new Set();
  return items.filter((item) => {
    const key = item.ref || item.url || item.source || item.source_file || item.title;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 16);
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

function formatTime(value) {
  if (!value) return "recent";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recent";
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "0 KB";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function absoluteApiUrl(url) {
  if (!url) return "";
  if (url.startsWith("http")) return url;
  if (url.startsWith("/")) return `${API_BASE}${url}`;
  return url;
}
