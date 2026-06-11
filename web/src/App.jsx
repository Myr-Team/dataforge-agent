import React, { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

const documents = [
  ["raw_docs/product_manual.md", "TrailSense RidgeBand"],
  ["raw_docs/sensor_data_dictionary.md", "遥测字段"],
  ["raw_docs/customer_feedback_summary.md", "客户信号"],
  ["raw_docs/internal_technical_wiki.md", "技术平台"],
  ["raw_docs/market_research_notes.md", "市场笔记"],
  ["raw_docs/privacy_and_consent_review.md", "隐私与同意"],
  ["raw_docs/operations_cost_notes.md", "运营成本"],
  ["raw_docs/partnership_pipeline.md", "合作渠道"],
];

// 六个智能体的中文名 + 角色描述（用于追踪与活动流展示）
const AGENTS = {
  "df-coordinator": { name: "协调器", role: "意图路由" },
  "df-corpus-analyst": { name: "语料分析师", role: "证据检索" },
  "df-feasibility-analyst": { name: "可行性分析师", role: "打分与判负" },
  "df-market-researcher": { name: "市场研究员", role: "竞品对比" },
  "df-producer": { name: "产物生成器", role: "PDF / 概念图 / 语音" },
  "df-auditor": { name: "审计员", role: "证据核验" },
};

function agentLabel(id) {
  return AGENTS[id]?.name || id || "智能体";
}

const REDUCED_MOTION =
  typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;

function Icon({ name }) {
  const common = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" };
  const paths = {
    send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6" /></>,
    image: <><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="8.5" cy="10.5" r="1.5" /><path d="m21 15-5-5L5 21" /></>,
    audio: <><path d="M12 3v18" /><path d="M8 8v8" /><path d="M16 7v10" /><path d="M4 11v2" /><path d="M20 10v4" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></>,
    play: <path d="M8 5v14l11-7Z" />,
    check: <path d="M20 6 9 17l-5-5" />,
    spark: <path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z" />,
    pulse: <><path d="M3 12h4l2-6 4 12 2-6h6" /></>,
    tool: <><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4l-6 6 2.7 2.7 6-6a4 4 0 0 0 5.4-5.4l-2.3 2.3-1.8-.5-.5-1.8Z" /></>,
    brain: <><path d="M12 5a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8V17a2 2 0 0 0 4 0" /><path d="M12 5a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8V17a2 2 0 0 1-4 0" /></>,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" /></>,
    alert: <><path d="M12 9v4" /><path d="M12 17h.01" /><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></>,
    upload: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 9l5-5 5 5" /><path d="M12 4v12" /></>,
    database: <><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14a9 3 0 0 0 18 0V5" /><path d="M3 12a9 3 0 0 0 18 0" /></>,
    download: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5" /><path d="M12 15V3" /></>,
  };
  return <svg {...common} aria-hidden="true">{paths[name]}</svg>;
}

function parseSse(buffer) {
  const chunks = buffer.split("\n\n");
  const rest = chunks.pop() || "";
  const events = chunks
    .map((chunk) => {
      const eventLine = chunk.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = chunk.split("\n").find((line) => line.startsWith("data: "));
      if (!eventLine) return null;
      let data = dataLine ? dataLine.slice(6) : "";
      try {
        data = JSON.parse(data);
      } catch {
        data = { text: data };
      }
      return { event: eventLine.slice(7), data };
    })
    .filter(Boolean);
  return { events, rest };
}

function artifactLink(artifact) {
  if (!artifact) return "";
  const url = artifact.artifact_url || artifact.audio_blob_url || artifact.pdf_blob_url || artifact.concept_image_blob_url || "";
  if (url.startsWith("http")) return url;
  return `${API_BASE}${url}`;
}

function healthLabel(health) {
  if (health.status === "checking") return "检测中";
  if (health.status === "ok") return "已连接";
  return "未连接";
}

// 把一条 SSE 事件翻译成活动流里的一行（友好中文 + 图标 + 语气）
function describeEvent(item) {
  const d = item.data || {};
  const who = agentLabel(d.agent || (item.event === "plan" ? "df-coordinator" : ""));
  switch (item.event) {
    case "ready":
      return { icon: "spark", text: "已连接编排器，开始运行" };
    case "plan":
      return { icon: "pulse", text: `协调器规划意图「${d.intent || "?"}」，调度 ${(d.experts || []).length} 个智能体` };
    case "role_change":
      return { icon: "spark", text: `${who} 接手${d.revision ? `（第 ${d.revision} 次修订）` : ""}`, head: true };
    case "tool_call":
      return { icon: "tool", text: `${who} 调用工具 ${d.name || "tool"}` };
    case "tool_result":
      return { icon: "check", text: `工具 ${d.name || "tool"} 返回 · ${d.count ?? (d.bytes ? `${Math.round(d.bytes / 1024)}KB` : "ok")}` };
    case "model_response":
      return { icon: "brain", text: `${who} 推理完成 · ${d.usage?.total_tokens ?? 0} tokens` };
    case "audit":
      return { icon: "shield", text: `审计结论：${d.verdict === "pass" ? "通过" : "打回修订"}${d.issues?.length ? ` · ${d.issues[0]}` : ""}`, tone: d.verdict === "pass" ? "ok" : "warn" };
    case "clarify":
      return { icon: "alert", text: `需要澄清：${d.question || ""}`, tone: "warn" };
    case "error":
      return { icon: "alert", text: `运行出错：${d.message || ""}`, tone: "error" };
    default:
      return null;
  }
}

function UploadStatus({ upload }) {
  if (!upload) return null;
  const stages = ["上传文件", "识别格式", "剖析内容", "接入 AI Search", "就绪"];
  const idx = upload.status === "error" ? -1 : upload.status === "done" ? stages.length : (upload.stage ?? 1);
  return (
    <div className={`upload-status ${upload.status}`}>
      <div className="us-head">
        <Icon name={upload.status === "error" ? "alert" : upload.status === "done" ? "check" : "upload"} />
        <span>{upload.message}</span>
      </div>
      {upload.status !== "error" ? (
        <ol className="us-stages">
          {stages.map((s, i) => (
            <li key={s} className={i < idx ? "done" : i === idx ? "active" : ""}>{s}</li>
          ))}
        </ol>
      ) : null}
      {upload.summary ? (
        <p className="us-summary">{upload.summary.format ? `格式 ${upload.summary.format} · ` : ""}{upload.summary.indexed_count ?? upload.summary.rows ?? "?"} 条已入库</p>
      ) : null}
    </div>
  );
}

// 服务状态：真实可知的(后端/AI Search)直接显示；其余由 /api/health.dependencies 上报后点亮
function ServiceStatus({ health }) {
  const dep = health.dependencies || {};
  const st = (v) => (v === true || v === "ok" ? "ok" : v === false ? "warn" : v ? "ok" : "pending");
  const rows = [
    ["后端编排器", health.status === "ok" ? "ok" : health.status === "checking" ? "pending" : "warn"],
    ["AI Search", health.status === "ok" ? (health.search_endpoint ? "ok" : "warn") : "pending"],
    ["Foundry 模型", st(dep.foundry)],
    ["MCP 市场服务", st(dep.mcp)],
    ["Azure Speech", st(dep.speech)],
    ["Blob 存储", st(dep.blob)],
  ];
  const label = { ok: "正常", warn: "异常", pending: "待上报" };
  return (
    <div className="svc-list">
      {rows.map(([name, s]) => (
        <div className="svc-row" key={name}>
          <span className={`svc-dot ${s}`} />
          <span className="svc-name">{name}</span>
          <em className={s}>{label[s]}</em>
        </div>
      ))}
    </div>
  );
}

function WorkspacePanel({ health, selectedDoc, onDocClick, docHits, workspace, workspaces, onWorkspaceChange, onUpload, upload }) {
  const cur = workspaces.find((w) => w.id === workspace);
  return (
    <aside className="workspace-panel">
      <div className="brand">
        <div className="mark">D</div>
        <div>
          <strong>DataForge</strong>
          <span>产品化 Agent</span>
        </div>
      </div>
      <section className="ws-section">
        <div className="section-title">工作区</div>
        <label className="ws-select">
          <Icon name="database" />
          <select value={workspace} onChange={(e) => onWorkspaceChange(e.target.value)} aria-label="选择工作区">
            {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}{w.docs != null ? ` · ${w.docs} 篇` : ""}</option>)}
          </select>
        </label>
        <button className="ws-upload" type="button" onClick={onUpload} title="上传 CSV / Excel / JSON，自动识别并接入">
          <Icon name="upload" /> 上传我的数据（CSV / Excel / JSON）
        </button>
      </section>
      <div className="panel-body">
        <UploadStatus upload={upload} />
        <section>
          <div className="section-title">服务状态</div>
          <ServiceStatus health={health} />
          {health.message ? <p className="health-note">{health.message}</p> : null}
        </section>
        <section>
          <div className="section-title">工作区内容</div>
          {workspace === "demo-corpus" ? (
            <div className="doc-list">
              {documents.map(([file, label]) => (
                <button className={`doc-row ${selectedDoc === file ? "active" : ""}`} key={file} onClick={() => onDocClick(file)} type="button">
                  <Icon name="file" />
                  <div>
                    <strong>{file.replace("raw_docs/", "")}</strong>
                    <span>{label}</span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="ws-content-note">
              <Icon name="database" />
              <div>
                <strong>{cur?.name || workspace}</strong>
                <span>已接入{cur?.docs != null ? `，共 ${cur.docs} 篇画像文档` : ""}。这里展示该工作区的数据画像；直接在右侧提问即可。</span>
              </div>
            </div>
          )}
        </section>
        <section>
          <div className="section-title">文档命中</div>
          <div className="doc-hit-list">
            {docHits.length ? docHits.slice(0, 3).map((hit) => (
              <article className="doc-hit" key={hit.id || `${hit.source_file}-${hit.chunk_id}`}>
                <strong>{hit.title || hit.source_file}</strong>
                <span>{hit.source_file}#{hit.chunk_id}</span>
                <p>{hit.content}</p>
              </article>
            )) : <p className="empty-note">点击左侧文档后显示真实检索片段。</p>}
          </div>
        </section>
      </div>
    </aside>
  );
}

// 顶部细进度条：根据 plan 里的智能体数 vs 已 role_change 的数量
function ProgressBar({ trace, running }) {
  const plan = trace.find((t) => t.event === "plan");
  const total = plan ? (plan.data.experts || []).length : 0;
  const done = trace.filter((t) => t.event === "role_change" && !t.data?.revision).length;
  const hasFinal = trace.some((t) => t.event === "final");
  const hasError = trace.some((t) => t.event === "error" || t.event === "clarify");
  const pct = hasFinal ? 100 : total ? Math.min(95, Math.round((done / total) * 100)) : running ? 12 : 0;
  if (!running && !hasFinal && !hasError) return null;
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className={`progress-bar ${hasError ? "error" : hasFinal ? "done" : "active"}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function ChatPanel({ activeTab, setActiveTab, messages, trace, running, input, setInput, run, artifacts, docHits, health, onUpload, onProduce, stream }) {
  const artifactCount = Object.values(artifacts).filter(Boolean).length;
  const disabled = running || health.status !== "ok";
  return (
    <main className="chat-panel">
      <header className="topbar">
        <div>
          <h1>DataForge 工作台</h1>
          <span>{health.status === "ok" ? "已连接后端，可执行真实多智能体编排" : "等待后端响应…"}</span>
        </div>
        {running ? <span className="run-badge"><span className="run-dot" />运行中…</span> : null}
      </header>
      <ProgressBar trace={trace} running={running} />
      <div className="tabs" role="tablist">
        {[
          ["chat", "对话", messages.length],
          ["artifacts", "产物", artifactCount],
          ["docs", "证据", docHits.length],
        ].map(([key, label, count]) => (
          <button className={activeTab === key ? "active" : ""} key={key} onClick={() => setActiveTab(key)} type="button" role="tab" aria-selected={activeTab === key}>
            {label}{count ? <span>{count}</span> : null}
          </button>
        ))}
      </div>
      <div className="panel-scroll">
        {activeTab === "chat" ? <ChatStream messages={messages} trace={trace} running={running} run={run} onUpload={onUpload} onProduce={onProduce} artifacts={artifacts} stream={stream} /> : null}
        {activeTab === "artifacts" ? <ArtifactShelf artifacts={artifacts} running={running} /> : null}
        {activeTab === "docs" ? <EvidenceList hits={docHits} /> : null}
      </div>
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          const text = input;
          setInput("");
          run(text);
        }}
      >
        <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="输入产品化问题，例如：为运营团队评估一个数据产品机会" aria-label="提问输入框" />
        <button type="submit" disabled={disabled || !input.trim()} aria-label="发送">
          <Icon name="send" />
        </button>
      </form>
    </main>
  );
}

// 打字机：final 文本逐字显示（reduced-motion 时直接整段呈现）
function Typewriter({ text }) {
  const [shown, setShown] = useState(REDUCED_MOTION ? text : "");
  useEffect(() => {
    if (REDUCED_MOTION) { setShown(text); return; }
    setShown("");
    if (!text) return;
    let i = 0;
    const id = window.setInterval(() => {
      i += 2;
      setShown(text.slice(0, i));
      if (i >= text.length) window.clearInterval(id);
    }, 18);
    return () => window.clearInterval(id);
  }, [text]);
  return <p className="bubble-text">{shown}{!REDUCED_MOTION && shown.length < text.length ? <span className="caret" /> : null}</p>;
}

// 实时活动流：把 SSE 事件渲染成"智能体正在做什么"的时间线
function ActivityTimeline({ trace, running }) {
  const steps = useMemo(
    () => trace.map((item, i) => ({ ...describeEvent(item), key: `${item.event}-${i}`, raw: item })).filter((s) => s && s.text),
    [trace],
  );
  if (!steps.length && !running) return null;
  return (
    <div className="activity">
      <div className="activity-head">
        <span className={`live-dot ${running ? "on" : ""}`} />
        {running ? "智能体协作中…" : "本次运行轨迹"}
      </div>
      <ol className="activity-list">
        {steps.map((s, i) => {
          const isLast = i === steps.length - 1;
          const active = running && isLast;
          return (
            <li className={`activity-item ${s.tone || ""} ${s.head ? "head" : ""} ${active ? "active" : ""}`} key={s.key}>
              <span className="ai-icon"><Icon name={s.icon || "spark"} /></span>
              <span className="ai-text">{s.text}</span>
            </li>
          );
        })}
        {running ? (
          <li className="activity-item shimmer">
            <span className="ai-icon"><Icon name="pulse" /></span>
            <span className="ai-text">正在推理，请稍候…</span>
          </li>
        ) : null}
      </ol>
    </div>
  );
}

function Welcome({ run, onUpload }) {
  const examples = [
    "这个工作区能做哪些数据产品？请评估可行性",
    "帮我从这些数据里找一个可落地的产品机会",
    "生成一份完整产物：项目书 PDF + 概念图 + 语音摘要",
  ];
  return (
    <div className="welcome">
      <div className="welcome-mark"><Icon name="spark" /></div>
      <strong>我是 DataForge，你的数据产品化助手</strong>
      <p>把你的数据（CSV / Excel / JSON）交给我——我会自动识别格式、剖析内容、接入检索，再用多智能体帮你分析「这些数据能做什么产品、是否可行」，并诚实指出缺口。</p>
      <button className="primary welcome-upload" type="button" onClick={onUpload}><Icon name="upload" /> 上传我的数据</button>
      <div className="welcome-examples">
        <span>或先试试这些问题：</span>
        {examples.map((q, i) => (
          <button key={i} className="chip" type="button" onClick={() => run(q)}>{q}</button>
        ))}
      </div>
    </div>
  );
}

function ChatStream({ messages, trace, running, run, onUpload, onProduce, artifacts, stream }) {
  const endRef = useRef(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "end" });
  }, [messages, trace, running, stream]);

  const userMsgs = messages.filter((m) => m.role === "user");
  const asstMsgs = messages.filter((m) => m.role === "assistant");
  const hasArtifacts = Object.values(artifacts || {}).some(Boolean);
  // 仅当：有助手回复、不在运行、且尚未生成产物时，才在回复下方渐出"生成产物"按钮
  const showProduce = asstMsgs.length > 0 && !running && !hasArtifacts;

  if (!messages.length && !running) {
    return <Welcome run={run} onUpload={onUpload} />;
  }
  return (
    <div className="chat-stream">
      {userMsgs.map((m, i) => (
        <Bubble key={`u-${i}`} role="user" name="你" time={m.time}><p className="bubble-text">{m.text}</p></Bubble>
      ))}
      <ActivityTimeline trace={trace} running={running} />
      {asstMsgs.map((m, i) => {
        const last = i === asstMsgs.length - 1;
        return (
          <Bubble key={`a-${i}`} role="assistant" name="DataForge" time={m.time}>
            {last && !REDUCED_MOTION && !m.streamed ? <Typewriter text={m.text} /> : <p className="bubble-text">{m.text}</p>}
            {last && showProduce ? (
              <div className="produce-cta">
                <button className="primary" type="button" onClick={onProduce}>
                  <Icon name="spark" /> 据此生成完整产物
                </button>
                <span>满意这版分析？生成项目书 PDF、概念图与语音摘要</span>
              </div>
            ) : null}
          </Bubble>
        );
      })}
      {stream ? (
        <Bubble role="assistant" name="DataForge" time="刚刚">
          <p className="bubble-text">{stream}<span className="caret" /></p>
        </Bubble>
      ) : null}
      <div ref={endRef} />
    </div>
  );
}

function Bubble({ role, name, time, children }) {
  return (
    <div className={`message ${role}`}>
      <div className="avatar">{role === "user" ? "你" : "DF"}</div>
      <div className="bubble">
        <div className="message-head">
          <strong>{name}</strong>
          <span>{time || "刚刚"}</span>
        </div>
        {children}
      </div>
    </div>
  );
}

function ArtifactShelf({ artifacts, running }) {
  const items = [
    ["pdf", "项目建议书", "file", artifacts.pdf],
    ["concept_image", "概念图", "image", artifacts.concept_image],
    ["audio_summary", "语音摘要", "audio", artifacts.audio_summary],
  ];
  return (
    <section className="artifact-shelf">
      <div className="shelf-title">真实产物</div>
      <div className="artifact-grid">
        {items.map(([key, title, icon, artifact]) => (
          <article className={`artifact ${!artifact && running ? "loading" : ""}`} key={key}>
            <div className="artifact-head">
              <Icon name={icon} />
              <div>
                <strong>{title}</strong>
                <span>{artifact?.bytes ? `${Math.round(artifact.bytes / 1024)} KB` : running ? "生成中…" : "等待生成"}</span>
              </div>
            </div>
            {key === "concept_image" && artifact ? (
              <img src={artifactLink(artifact)} alt="概念图产物" />
            ) : (
              <div className={`artifact-preview ${!artifact && running ? "shimmer" : ""}`}>{artifact || !running ? <Icon name={icon} /> : null}</div>
            )}
            {key === "audio_summary" && artifact ? <audio src={artifactLink(artifact)} controls /> : null}
            <a className={artifact ? "artifact-action" : "artifact-action disabled"} href={artifact ? artifactLink(artifact) : undefined} target="_blank" rel="noreferrer">
              {key === "pdf" ? "打开 PDF" : key === "concept_image" ? "查看图片" : "播放语音"}
            </a>
          </article>
        ))}
      </div>
    </section>
  );
}

function EvidenceList({ hits }) {
  if (!hits.length) {
    return <div className="empty-state compact"><strong>暂无证据片段</strong><p>点击语料文档或发起运行后，这里显示真实命中的片段。</p></div>;
  }
  return (
    <div className="evidence-list">
      {hits.map((hit) => (
        <article className="evidence-card" key={hit.id || `${hit.source_file}-${hit.chunk_id}`}>
          <strong>{hit.title || hit.source_file}</strong>
          <span>{hit.source_file}#{hit.chunk_id}</span>
          <p>{hit.content}</p>
        </article>
      ))}
    </div>
  );
}

// 导出本次运行的"留痕"：逐条记录每个 agent 的操作（含 response_id/tokens/审计结论等）
function exportTrace(trace) {
  const ts = new Date();
  const lines = ["# DataForge 运行留痕（Agent 操作记录）", `导出时间：${ts.toLocaleString("zh-CN")}`, `事件总数：${trace.length}`, ""];
  trace.forEach((t, i) => {
    const d = t.data || {};
    const who = d.agent ? agentLabel(d.agent) : t.event === "plan" ? "协调器" : "";
    const desc = describeEvent(t);
    lines.push(`### ${String(i + 1).padStart(2, "0")} · ${EVENT_LABELS[t.event] || t.event}${who ? ` · ${who}` : ""}`);
    if (desc?.text) lines.push(`- ${desc.text}`);
    if (d.response_id) lines.push(`- response_id: \`${d.response_id}\` · tokens: ${d.usage?.total_tokens ?? "?"}`);
    if (t.event === "audit") lines.push(`- 审计：${d.verdict}${d.issues?.length ? ` — ${d.issues.join("；")}` : ""}`);
    lines.push("");
  });
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `dataforge-trace-${ts.toISOString().slice(0, 19).replace(/[:T]/g, "")}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function TracePanel({ trace, running, filter, setFilter, scrollRef }) {
  const counts = useMemo(() => ({
    total: trace.length,
    tools: trace.filter((item) => item.event === "tool_call" || item.event === "tool_result").length,
    models: trace.filter((item) => item.event === "model_response").length,
    audit: trace.filter((item) => item.event === "audit").length,
  }), [trace]);
  const filtered = trace.filter((item) => {
    if (filter === "tools") return item.event === "tool_call" || item.event === "tool_result";
    if (filter === "models") return item.event === "model_response";
    if (filter === "audit") return item.event === "audit";
    return true;
  });
  return (
    <aside className="trace-panel">
      <header>
        <div>
          <h2>智能体追踪</h2>
          {(() => {
            const done = trace.some((t) => t.event === "final");
            return <span className={running ? "live" : done ? "ok" : ""}>{running ? "运行中" : done ? `已完成 · ${trace.length} 步` : "待运行"}</span>;
          })()}
        </div>
        <div className="trace-pills">
          {[
            ["all", `全部 ${counts.total}`],
            ["tools", `工具 ${counts.tools}`],
            ["models", `模型 ${counts.models}`],
            ["audit", `审计 ${counts.audit}`],
          ].map(([key, label]) => (
            <button className={filter === key ? "active" : ""} key={key} onClick={() => setFilter(key)} type="button">{label}</button>
          ))}
          {trace.length > 0 ? (
            <button className="trace-export" type="button" onClick={() => exportTrace(trace)} title="导出本次运行的 Agent 操作留痕（Markdown）">
              <Icon name="download" /> 留痕
            </button>
          ) : null}
        </div>
      </header>
      <div className="trace-list" ref={scrollRef}>
        {filtered.length ? filtered.map((item, index) => (
          <TraceItem item={item} index={index} active={running && index === filtered.length - 1} key={`${item.event}-${index}`} />
        )) : <p className="empty-note">运行后这里显示逐帧的智能体事件。</p>}
      </div>
    </aside>
  );
}

const EVENT_LABELS = {
  ready: "就绪", user: "提问", plan: "规划", role_change: "切换", tool_call: "调用工具",
  tool_result: "工具结果", model_response: "模型推理", audit: "审计", clarify: "澄清", final: "完成", error: "错误",
};

function TraceItem({ item, index, active }) {
  const agent = item.event === "plan" ? "协调器" : agentLabel(item.data?.agent) || item.data?.intent || item.data?.name || EVENT_LABELS[item.event] || item.event;
  const status = item.event === "audit" ? (item.data?.verdict === "pass" ? "通过" : "修订") : item.event === "tool_result" ? "完成" : item.event === "model_response" ? "模型" : item.event === "final" ? "结束" : null;
  const detail =
    item.event === "plan"
      ? `${item.data.intent} → ${(item.data.experts || []).map(agentLabel).join("、")}`
      : item.event === "model_response"
        ? `response_id ${String(item.data.response_id || "n/a").slice(0, 18)}… · ${item.data.usage?.total_tokens || 0} tokens`
        : item.event === "tool_result"
          ? `${item.data.name || "tool"} → ${item.data.count ?? item.data.bytes ?? "ok"}`
          : item.event === "final"
            ? item.data.text
            : item.event === "clarify"
              ? item.data.question
              : item.event === "role_change"
                ? `${agentLabel(item.data?.agent)} 接手${item.data?.revision ? `（修订 ${item.data.revision}）` : ""}`
                : item.event === "tool_call"
                  ? `${item.data?.name || "tool"}`
                  : "";
  return (
    <div className={`trace-item ${item.event} ${active ? "active" : ""}`} style={{ "--i": index }}>
      <div className="trace-rail">
        <span className={`dot ${active ? "loading" : "done"}`} />
      </div>
      <div className="trace-card">
        <div className="trace-top">
          <strong>{agent}</strong>
          <span className="evt">{EVENT_LABELS[item.event] || item.event}</span>
          {status ? <em>{status}</em> : null}
          <span className="seq mono">{String(index + 1).padStart(2, "0")}</span>
        </div>
        {detail ? <p>{detail}</p> : null}
      </div>
    </div>
  );
}

export function App() {
  const [messages, setMessages] = useState([]);
  const [trace, setTrace] = useState([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [artifacts, setArtifacts] = useState({});
  const [activeTab, setActiveTab] = useState("chat");
  const [traceFilter, setTraceFilter] = useState("all");
  const [health, setHealth] = useState({ status: "checking", message: "" });
  const [selectedDoc, setSelectedDoc] = useState("");
  const [docHits, setDocHits] = useState([]);
  const [workspace, setWorkspace] = useState("demo-corpus");
  const [workspaces, setWorkspaces] = useState([{ id: "demo-corpus", name: "演示语料 demo-corpus" }]);
  const [upload, setUpload] = useState(null);
  const [stream, setStream] = useState("");
  const traceRef = useRef(null);
  const fileRef = useRef(null);

  // 拉取可用工作区（后端未提供该接口时用已知工作区兜底）
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/workspaces`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => {
        const list = (d.workspaces || d || []).map((w) => ({ id: w.workspace_id || w.id, name: w.name || w.workspace_id || w.id, docs: w.doc_count }));
        if (!cancelled && list.length) setWorkspaces(list);
      })
      .catch(() => {
        if (!cancelled) setWorkspaces([
          { id: "demo-corpus", name: "演示语料 demo-corpus" },
          { id: "user-excel-corpus", name: "我的 Excel 语料" },
          { id: "excel-corpus", name: "示例 Excel 语料" },
        ]);
      });
    return () => { cancelled = true; };
  }, []);

  async function handleUpload(file) {
    if (!file) return;
    setUpload({ status: "uploading", stage: 1, message: `正在上传 ${file.name}…` });
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API_BASE}/api/upload`, { method: "POST", body: form });
      if (!res.ok) throw new Error(res.status === 404 ? "数据接入服务尚未就绪，请稍后再试" : `上传失败：HTTP ${res.status}`);
      const data = await res.json();
      const wid = data.workspace_id || data.id;
      setUpload({ status: "done", message: `已接入「${data.name || wid}」`, summary: data });
      if (wid) {
        setWorkspaces((ws) => (ws.some((w) => w.id === wid) ? ws : [...ws, { id: wid, name: data.name || wid, docs: data.indexed_count }]));
        setWorkspace(wid);
        setMessages([]); setTrace([]); setArtifacts({}); setDocHits([]);
      }
    } catch (e) {
      setUpload({ status: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }
  const triggerUpload = () => fileRef.current?.click();

  useEffect(() => {
    let cancelled = false;
    async function probe() {
      try {
        const response = await fetch(`${API_BASE}/api/health`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!cancelled) setHealth({ status: data.ok ? "ok" : "error", message: data.search_endpoint ? "搜索服务已配置" : "使用本地/默认检索配置", search_endpoint: data.search_endpoint, dependencies: data.dependencies });
      } catch (error) {
        if (!cancelled) setHealth({ status: "error", message: error instanceof Error ? error.message : String(error) });
      }
    }
    probe();
    const timer = window.setInterval(probe, 30000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  // 追踪面板自动滚到底（新条目插入后平滑跟随）
  useEffect(() => {
    const el = traceRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: REDUCED_MOTION ? "auto" : "smooth" });
  }, [trace]);

  async function loadDoc(file) {
    setSelectedDoc(file);
    setActiveTab("docs");
    try {
      const response = await fetch(`${API_BASE}/api/search-pack-context`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspace, query: file.replace("raw_docs/", ""), top_k: 5 }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setDocHits(data.hits || []);
    } catch (error) {
      setDocHits([{ title: "检索失败", source_file: file, chunk_id: "error", content: error instanceof Error ? error.message : String(error) }]);
    }
  }

  async function run(message = input) {
    if (!message.trim() || running) return;
    setRunning(true);
    setTrace([]);
    setArtifacts({});
    setStream("");
    setActiveTab("chat");
    setMessages([{ role: "user", text: message, time: "刚刚" }]);
    let streamed = false;
    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspace, message }),
      });
      if (!response.ok || !response.body) throw new Error(`请求失败：${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSse(buffer);
        buffer = parsed.rest;
        if (parsed.events.length) {
          setTrace((items) => [...items, ...parsed.events]);
          for (const ev of parsed.events) {
            if (ev.event === "answer_delta" || ev.event === "delta") {
              streamed = true;
              const piece = ev.data?.delta ?? ev.data?.text ?? "";
              if (piece) setStream((s) => s + piece);
            }
          }
          const clarify = parsed.events.find((event) => event.event === "clarify");
          if (clarify) {
            setMessages((items) => [...items, { role: "assistant", text: clarify.data.question || clarify.data.text || clarify.data.reason || "我需要多了解一点你的目标，方便给出更有据的分析。", time: "刚刚" }]);
          }
          const final = parsed.events.find((event) => event.event === "final");
          if (final) {
            const proposal = final.data?.artifact?.proposal || {};
            const corpusHits = final.data?.artifact?.corpus?.hits || [];
            setDocHits(corpusHits);
            setArtifacts({
              pdf: proposal.pdf,
              concept_image: proposal.concept_image,
              audio_summary: proposal.audio_summary,
            });
            setMessages((items) => [...items, { role: "assistant", text: final.data.text, time: "刚刚", streamed }]);
            setStream("");
          }
        }
      }
    } catch (error) {
      const data = { message: error instanceof Error ? error.message : String(error) };
      setTrace((items) => [...items, { event: "error", data }]);
      setMessages((items) => [...items, { role: "assistant", text: `运行失败：${data.message}`, time: "刚刚" }]);
    } finally {
      setRunning(false);
    }
  }

  // 在最终分析满意后，据此生成完整产物（沿用上一条提问 + full_package 触发词）
  function produce() {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    const base = lastUser?.text || "基于当前工作区的分析";
    run(`${base}（请据此生成完整产物：项目书 PDF、概念图、语音摘要）`);
  }

  return (
    <div className="app-shell">
      <input
        ref={fileRef}
        type="file"
        accept=".csv,.xlsx,.xls,.json,.md,.txt"
        hidden
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) handleUpload(f); }}
      />
      <WorkspacePanel
        health={health}
        selectedDoc={selectedDoc}
        onDocClick={loadDoc}
        docHits={docHits}
        workspace={workspace}
        workspaces={workspaces}
        onWorkspaceChange={(w) => { setWorkspace(w); setMessages([]); setTrace([]); setArtifacts({}); setDocHits([]); setSelectedDoc(""); }}
        onUpload={triggerUpload}
        upload={upload}
      />
      <ChatPanel
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        messages={messages}
        trace={trace}
        running={running}
        input={input}
        setInput={setInput}
        run={run}
        artifacts={artifacts}
        docHits={docHits}
        health={health}
        onUpload={triggerUpload}
        onProduce={produce}
        stream={stream}
      />
      <div className="trace-scroll">
        <TracePanel trace={trace} running={running} filter={traceFilter} setFilter={setTraceFilter} scrollRef={traceRef} />
      </div>
    </div>
  );
}
