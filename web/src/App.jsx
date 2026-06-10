import React, { useMemo, useRef, useState } from "react";

const API_BASE =
  import.meta.env.VITE_API_BASE || "https://ca-dataforge-backend.thankfultree-c0fc8321.eastus2.azurecontainerapps.io";

const documents = [
  ["product_manual.md", "TrailSense RidgeBand"],
  ["sensor_data_dictionary.md", "Telemetry fields"],
  ["customer_feedback_summary.md", "Buyer signals"],
  ["market_research_notes.md", "Market notes"],
  ["privacy_and_consent_review.md", "Consent review"],
  ["operations_cost_notes.md", "Cost model"],
  ["partnership_pipeline.md", "Channels"],
];

const integrations = [
  ["AI Search", "Connected"],
  ["MCP Market", "Connected"],
  ["Speech", "Connected"],
  ["Code Interpreter", "Ready"],
];

const starterMessages = [
  {
    role: "user",
    text: "Create a full package with PDF, concept image, and audio for a data product from this workspace.",
  },
  {
    role: "assistant",
    text: "I will analyze the corpus, compare market signals, audit the feasibility claim, and produce the report, concept image, and audio summary.",
  },
];

const starterTrace = [
  { event: "ready", data: { workspace_id: "demo-corpus", conversation_id: "waiting" } },
  { event: "plan", data: { intent: "full_package", experts: ["df-corpus-analyst", "df-feasibility-analyst", "df-market-researcher", "df-producer", "df-auditor"] } },
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
    check: <><path d="M20 6 9 17l-5-5" /></>,
    spark: <><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z" /></>,
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

function WorkspacePanel() {
  return (
    <aside className="workspace-panel">
      <div className="brand">
        <div className="mark">D</div>
        <div>
          <strong>DataForge</strong>
          <span>Feasibility Agent</span>
        </div>
      </div>
      <label className="search">
        <Icon name="search" />
        <input value="demo-corpus" readOnly />
      </label>
      <section>
        <div className="section-title">Workspace</div>
        <div className="workspace-card">
          <strong>demo-corpus</strong>
          <span>8 docs indexed</span>
        </div>
      </section>
      <section>
        <div className="section-title">Documents</div>
        <div className="doc-list">
          {documents.map(([file, label]) => (
            <div className="doc-row" key={file}>
              <Icon name="file" />
              <div>
                <strong>{file}</strong>
                <span>{label}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
      <section>
        <div className="section-title">Integrations</div>
        <div className="integration-list">
          {integrations.map(([name, status]) => (
            <div className="integration" key={name}>
              <span>{name}</span>
              <em>{status}</em>
            </div>
          ))}
        </div>
      </section>
    </aside>
  );
}

function ChatPanel({ messages, input, setInput, run, running, artifacts }) {
  return (
    <main className="chat-panel">
      <header className="topbar">
        <div>
          <h1>DataForge Workspace Run</h1>
          <span>demo-corpus · outdoor analytics opportunity</span>
        </div>
        <button className="primary" onClick={() => run()} disabled={running}>
          <Icon name="spark" />
          {running ? "Running" : "Run full package"}
        </button>
      </header>
      <div className="tabs">
        <button className="active">Chat</button>
        <button>Artifacts <span>{Object.keys(artifacts).length}</span></button>
      </div>
      <div className="messages">
        {messages.map((message, index) => (
          <div className={`message ${message.role}`} key={`${message.role}-${index}`}>
            <div className="avatar">{message.role === "user" ? "AK" : "DF"}</div>
            <div>
              <div className="message-head">
                <strong>{message.role === "user" ? "You" : "DataForge"}</strong>
                <span>{message.time || "now"}</span>
              </div>
              <p>{message.text}</p>
            </div>
          </div>
        ))}
      </div>
      <ArtifactShelf artifacts={artifacts} />
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          run(input);
        }}
      >
        <input value={input} onChange={(event) => setInput(event.target.value)} />
        <button type="submit" disabled={running || !input.trim()} aria-label="Send">
          <Icon name="send" />
        </button>
      </form>
    </main>
  );
}

function ArtifactShelf({ artifacts }) {
  const items = [
    ["pdf", "PDF Report", "file", artifacts.pdf],
    ["concept_image", "Concept Image", "image", artifacts.concept_image],
    ["audio_summary", "Audio Summary", "audio", artifacts.audio_summary],
  ];
  return (
    <section className="artifact-shelf">
      <div className="shelf-title">Artifacts</div>
      <div className="artifact-grid">
        {items.map(([key, title, icon, artifact]) => (
          <article className="artifact" key={key}>
            <div className="artifact-head">
              <Icon name={icon} />
              <div>
                <strong>{title}</strong>
                <span>{artifact?.bytes ? `${Math.round(artifact.bytes / 1024)} KB` : "Waiting"}</span>
              </div>
            </div>
            {key === "concept_image" && artifact ? <img src={artifactLink(artifact)} alt="" /> : <div className="artifact-preview">{key === "audio_summary" ? <Icon name="audio" /> : <Icon name={icon} />}</div>}
            {key === "audio_summary" && artifact ? <audio src={artifactLink(artifact)} controls /> : null}
            <a className={artifact ? "artifact-action" : "artifact-action disabled"} href={artifact ? artifactLink(artifact) : undefined} target="_blank" rel="noreferrer">
              {key === "pdf" ? "Open PDF" : key === "concept_image" ? "View image" : "Play audio"}
            </a>
          </article>
        ))}
      </div>
    </section>
  );
}

function TracePanel({ trace, running }) {
  const counts = useMemo(() => {
    const total = trace.length;
    const tools = trace.filter((item) => item.event === "tool_call" || item.event === "tool_result").length;
    const audit = trace.filter((item) => item.event === "audit").length;
    return { total, tools, audit };
  }, [trace]);
  return (
    <aside className="trace-panel">
      <header>
        <div>
          <h2>Agent Trace</h2>
          <span>{running ? "Live" : "Ready"}</span>
        </div>
        <div className="trace-pills">
          <button className="active">All {counts.total}</button>
          <button>Tools {counts.tools}</button>
          <button>Audit {counts.audit}</button>
        </div>
      </header>
      <div className="trace-list">
        {trace.map((item, index) => (
          <TraceItem item={item} index={index} key={`${item.event}-${index}`} />
        ))}
      </div>
    </aside>
  );
}

function TraceItem({ item, index }) {
  const agent = item.event === "plan" ? "df-coordinator" : item.data?.agent || item.data?.intent || item.data?.name || item.event;
  const status = item.event === "audit" ? item.data?.verdict : item.event === "tool_result" ? "done" : item.event;
  const detail =
    item.event === "plan"
      ? `${item.data.intent} · ${(item.data.experts || []).join(", ")}`
      : item.event === "tool_result"
        ? `${item.data.name || "tool"} · ${item.data.count ?? item.data.bytes ?? "ok"}`
        : item.event === "final"
          ? item.data.text
          : item.event === "clarify"
            ? item.data.question
            : JSON.stringify(item.data || {}).slice(0, 140);
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
  const [messages, setMessages] = useState(starterMessages);
  const [trace, setTrace] = useState(starterTrace);
  const [input, setInput] = useState("Create a full package with PDF, concept image, and audio for a data product from this workspace.");
  const [running, setRunning] = useState(false);
  const [artifacts, setArtifacts] = useState({});
  const traceRef = useRef(null);

  async function run(message = input) {
    if (!message.trim() || running) return;
    setRunning(true);
    setTrace([]);
    setArtifacts({});
    setMessages([{ role: "user", text: message, time: "now" }]);
    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: "demo-corpus", message }),
      });
      if (!response.ok || !response.body) throw new Error(`Request failed: ${response.status}`);
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
          const final = parsed.events.find((event) => event.event === "final");
          if (final) {
            const proposal = final.data?.artifact?.proposal || {};
            setArtifacts({
              pdf: proposal.pdf,
              concept_image: proposal.concept_image,
              audio_summary: proposal.audio_summary,
            });
            setMessages((items) => [...items, { role: "assistant", text: final.data.text, time: "now" }]);
          }
        }
        requestAnimationFrame(() => traceRef.current?.scrollTo({ top: traceRef.current.scrollHeight }));
      }
    } catch (error) {
      const data = { message: error instanceof Error ? error.message : String(error) };
      setTrace((items) => [...items, { event: "error", data }]);
      setMessages((items) => [...items, { role: "assistant", text: "The run failed before completion. Check the trace panel for the error.", time: "now" }]);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="app-shell">
      <WorkspacePanel />
      <ChatPanel messages={messages} input={input} setInput={setInput} run={run} running={running} artifacts={artifacts} />
      <div ref={traceRef} className="trace-scroll">
        <TracePanel trace={trace} running={running} />
      </div>
    </div>
  );
}
