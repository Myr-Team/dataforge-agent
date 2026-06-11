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
  };
  return <svg {...common}>{paths[name]}</svg>;
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

function WorkspacePanel({ health, selectedDoc, onDocClick, docHits }) {
  return (
    <aside className="workspace-panel">
      <div className="brand">
        <div className="mark">D</div>
        <div>
          <strong>DataForge</strong>
          <span>产品化 Agent</span>
        </div>
      </div>
      <label className="search">
        <Icon name="search" />
        <input value="demo-corpus" readOnly aria-label="当前工作区" />
      </label>
      <section>
        <div className="section-title">运行状态</div>
        <div className={`health-card ${health.status}`}>
          <div>
            <strong>本地后端</strong>
            <span>{API_BASE}</span>
          </div>
          <em>{healthLabel(health)}</em>
        </div>
        {health.message ? <p className="health-note">{health.message}</p> : null}
      </section>
      <section>
        <div className="section-title">输入语料</div>
        <div className="doc-list">
          {documents.map(([file, label]) => (
            <button className={`doc-row ${selectedDoc === file ? "active" : ""}`} key={file} onClick={() => onDocClick(file)}>
              <Icon name="file" />
              <div>
                <strong>{file.replace("raw_docs/", "")}</strong>
                <span>{label}</span>
              </div>
            </button>
          ))}
        </div>
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
    </aside>
  );
}

function ChatPanel({ activeTab, setActiveTab, messages, input, setInput, run, running, artifacts, docHits, health }) {
  const artifactCount = Object.values(artifacts).filter(Boolean).length;
  return (
    <main className="chat-panel">
      <header className="topbar">
        <div>
          <h1>DataForge 工作台</h1>
          <span>{health.status === "ok" ? "已连接本地后端，可执行真实编排" : "等待本地后端响应"}</span>
        </div>
        <button className="primary" onClick={() => run("请基于当前工作区生成一个数据产品方案，包含 PDF、概念图和语音。")} disabled={running || health.status !== "ok"}>
          <Icon name="spark" />
          {running ? "运行中" : "生成完整产物"}
        </button>
      </header>
      <div className="tabs" role="tablist">
        {[
          ["chat", "对话", messages.length],
          ["artifacts", "产物", artifactCount],
          ["docs", "证据", docHits.length],
        ].map(([key, label, count]) => (
          <button className={activeTab === key ? "active" : ""} key={key} onClick={() => setActiveTab(key)} type="button">
            {label}<span>{count}</span>
          </button>
        ))}
      </div>
      {activeTab === "chat" ? <Messages messages={messages} /> : null}
      {activeTab === "artifacts" ? <ArtifactShelf artifacts={artifacts} /> : null}
      {activeTab === "docs" ? <EvidenceList hits={docHits} /> : null}
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          run(input);
        }}
      >
        <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="输入产品化问题，例如：为运营团队评估一个数据产品机会" />
        <button type="submit" disabled={running || health.status !== "ok" || !input.trim()} aria-label="发送">
          <Icon name="send" />
        </button>
      </form>
    </main>
  );
}

function Messages({ messages }) {
  if (!messages.length) {
    return (
      <div className="empty-state">
        <Icon name="pulse" />
        <strong>尚未开始运行</strong>
        <p>连接本地后端后，可以直接发起产品可行性评估。这里不会预置模拟对话。</p>
      </div>
    );
  }
  return (
    <div className="messages">
      {messages.map((message, index) => (
        <div className={`message ${message.role}`} key={`${message.role}-${index}`}>
          <div className="avatar">{message.role === "user" ? "你" : "DF"}</div>
          <div>
            <div className="message-head">
              <strong>{message.role === "user" ? "你" : "DataForge"}</strong>
              <span>{message.time || "刚刚"}</span>
            </div>
            <p>{message.text}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function ArtifactShelf({ artifacts }) {
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
          <article className="artifact" key={key}>
            <div className="artifact-head">
              <Icon name={icon} />
              <div>
                <strong>{title}</strong>
                <span>{artifact?.bytes ? `${Math.round(artifact.bytes / 1024)} KB` : "等待生成"}</span>
              </div>
            </div>
            {key === "concept_image" && artifact ? <img src={artifactLink(artifact)} alt="概念图产物" /> : <div className="artifact-preview"><Icon name={icon} /></div>}
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

function TracePanel({ trace, running, filter, setFilter }) {
  const counts = useMemo(() => {
    const total = trace.length;
    const tools = trace.filter((item) => item.event === "tool_call" || item.event === "tool_result").length;
    const models = trace.filter((item) => item.event === "model_response").length;
    const audit = trace.filter((item) => item.event === "audit").length;
    return { total, tools, models, audit };
  }, [trace]);
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
          <span>{running ? "实时运行" : "就绪"}</span>
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
        </div>
      </header>
      <div className="trace-list">
        {filtered.length ? filtered.map((item, index) => (
          <TraceItem item={item} index={index} key={`${item.event}-${index}`} />
        )) : <p className="empty-note">暂无追踪事件。</p>}
      </div>
    </aside>
  );
}

function TraceItem({ item, index }) {
  const agent = item.event === "plan" ? "df-coordinator" : item.data?.agent || item.data?.intent || item.data?.name || item.event;
  const status = item.event === "audit" ? item.data?.verdict : item.event === "tool_result" ? "完成" : item.event === "model_response" ? "模型返回" : item.event;
  const detail =
    item.event === "plan"
      ? `${item.data.intent} -> ${(item.data.experts || []).join(", ")}`
      : item.event === "model_response"
        ? `response_id: ${item.data.response_id || "n/a"} · tokens: ${item.data.usage?.total_tokens || 0}`
        : item.event === "tool_result"
          ? `${item.data.name || "tool"} -> ${item.data.count ?? item.data.bytes ?? "ok"}`
          : item.event === "final"
            ? item.data.text
            : item.event === "clarify"
              ? item.data.question
              : JSON.stringify(item.data || {}).slice(0, 160);
  return (
    <div className={`trace-item ${item.event}`}>
      <div className="time">{String(index + 1).padStart(2, "0")}</div>
      <div className="dot" />
      <div className="trace-body">
        <div>
          <strong>{agent}</strong>
          <span>{item.event}</span>
        </div>
        <p>{detail}</p>
      </div>
      <em>{status}</em>
    </div>
  );
}

export function App() {
  const [messages, setMessages] = useState([]);
  const [trace, setTrace] = useState([]);
  const [input, setInput] = useState("为运营团队评估一个数据产品机会。");
  const [running, setRunning] = useState(false);
  const [artifacts, setArtifacts] = useState({});
  const [activeTab, setActiveTab] = useState("chat");
  const [traceFilter, setTraceFilter] = useState("all");
  const [health, setHealth] = useState({ status: "checking", message: "" });
  const [selectedDoc, setSelectedDoc] = useState("");
  const [docHits, setDocHits] = useState([]);
  const traceRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    async function probe() {
      setHealth({ status: "checking", message: "" });
      try {
        const response = await fetch(`${API_BASE}/api/health`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!cancelled) setHealth({ status: data.ok ? "ok" : "error", message: data.search_endpoint ? "搜索服务已配置" : "使用本地/默认检索配置" });
      } catch (error) {
        if (!cancelled) setHealth({ status: "error", message: error instanceof Error ? error.message : String(error) });
      }
    }
    probe();
    const timer = window.setInterval(probe, 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  async function loadDoc(file) {
    setSelectedDoc(file);
    setActiveTab("docs");
    try {
      const response = await fetch(`${API_BASE}/api/search-pack-context`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: "demo-corpus", query: file.replace("raw_docs/", ""), top_k: 5 }),
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
    setActiveTab("chat");
    setMessages([{ role: "user", text: message, time: "刚刚" }]);
    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: "demo-corpus", message }),
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
          const searchResult = parsed.events.find((event) => event.event === "tool_result" && event.data?.name === "search_pack_context");
          if (searchResult) setActiveTab("chat");
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
            setMessages((items) => [...items, { role: "assistant", text: final.data.text, time: "刚刚" }]);
            if (proposal.pdf || proposal.concept_image || proposal.audio_summary) setActiveTab("artifacts");
          }
        }
        requestAnimationFrame(() => traceRef.current?.scrollTo({ top: traceRef.current.scrollHeight }));
      }
    } catch (error) {
      const data = { message: error instanceof Error ? error.message : String(error) };
      setTrace((items) => [...items, { event: "error", data }]);
      setMessages((items) => [...items, { role: "assistant", text: `运行失败：${data.message}`, time: "刚刚" }]);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="app-shell">
      <WorkspacePanel health={health} selectedDoc={selectedDoc} onDocClick={loadDoc} docHits={docHits} />
      <ChatPanel activeTab={activeTab} setActiveTab={setActiveTab} messages={messages} input={input} setInput={setInput} run={run} running={running} artifacts={artifacts} docHits={docHits} health={health} />
      <div ref={traceRef} className="trace-scroll">
        <TracePanel trace={trace} running={running} filter={traceFilter} setFilter={setTraceFilter} />
      </div>
    </div>
  );
}
