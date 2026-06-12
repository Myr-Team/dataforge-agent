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
    case "route":
      return { icon: "pulse", text: `判定意图「${d.intent || "?"}」，${(d.experts || []).length ? `调度 ${(d.experts || []).length} 个智能体` : "协调器直接处理"}` };
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

// 账户头像 + 微软风格账户菜单（数据来自 Container Apps Easy Auth 的 /.auth/me；退出走 /.auth/logout）
function initialsOf(s) {
  const t = (s || "D").trim();
  if (/[一-鿿]/.test(t)) return t.slice(-1);
  const parts = t.replace(/@.*/, "").split(/[ ._-]+/).filter(Boolean);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || t[0]?.toUpperCase() || "D";
}
function AccountMenu() {
  const [me, setMe] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch("/.auth/me", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => {
        const p = Array.isArray(d) ? d[0] : (d?.clientPrincipal || d);
        const claims = p?.user_claims || [];
        const get = (...keys) => {
          for (const k of keys) {
            const c = claims.find((x) => {
              const t = (x.typ || "").toLowerCase();
              return t === k || t.endsWith("/" + k);
            });
            if (c?.val) return c.val;
          }
          return "";
        };
        const name = get("name", "displayname", "given_name") || "";
        const email = get("emailaddress", "preferred_username", "upn", "email") || p?.user_id || "";
        if (!cancelled && (name || email)) setMe({ name, email });
        else if (!cancelled) setMe(null);
      })
      .catch(() => { if (!cancelled) setMe(null); });
    return () => { cancelled = true; };
  }, []);
  if (!me) return <div className="mark">D</div>; // 未登录/本地预览：回退品牌标
  const initials = initialsOf(me.name || me.email);
  return (
    <div className="account">
      <button className="mark avatar" type="button" onClick={() => setOpen((v) => !v)} title={me.name || me.email} aria-haspopup="true" aria-expanded={open}>{initials}</button>
      {open ? (
        <>
          <div className="account-backdrop" onClick={() => setOpen(false)} />
          <div className="account-menu" role="menu">
            <div className="account-head">
              <div className="mark avatar lg">{initials}</div>
              <div className="account-id">
                <strong>{me.name || "已登录用户"}</strong>
                {me.email ? <span>{me.email}</span> : null}
              </div>
            </div>
            <a className="account-signout" href="/.auth/logout?post_logout_redirect_uri=/" role="menuitem">退出登录</a>
          </div>
        </>
      ) : null}
    </div>
  );
}

function WorkspacePanel({ health, selectedDoc, onDocClick, docHits, workspace, workspaces, onWorkspaceChange, onUpload, upload, wsDetail }) {
  const cur = workspaces.find((w) => w.id === workspace);
  return (
    <aside className="workspace-panel">
      <div className="brand">
        <AccountMenu />
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
          ) : wsDetail && !wsDetail.loading ? (
            <div className="ws-detail">
              {wsDetail.description ? <p className="ws-desc">{wsDetail.description}</p> : null}
              <div className="ws-meta">
                {wsDetail.format ? <span>{String(wsDetail.format).toUpperCase()}</span> : null}
                {wsDetail.rows != null ? <span>{wsDetail.rows} 行</span> : null}
                {(wsDetail.doc_count ?? wsDetail.documents?.length) != null ? <span>{wsDetail.doc_count ?? wsDetail.documents.length} 篇</span> : null}
              </div>
              {Array.isArray(wsDetail.columns) && wsDetail.columns.length ? (
                <div className="ws-cols">
                  {wsDetail.columns.slice(0, 14).map((c, i) => {
                    const cn = typeof c === "string" ? c : c.name;
                    const tag = typeof c === "string" ? "" : (c.signal || c.role || "");
                    return <span className="ws-col" key={`${cn}-${i}`}>{cn}{tag ? <em>{tag}</em> : null}</span>;
                  })}
                </div>
              ) : null}
              {Array.isArray(wsDetail.documents) && wsDetail.documents.length ? (
                <div className="doc-list">
                  {wsDetail.documents.map((doc, i) => {
                    const file = typeof doc === "string" ? doc : (doc.source_file || doc.file || doc.name || doc.title || `doc-${i}`);
                    const label = typeof doc === "string" ? "" : (doc.title || doc.label || doc.description || "");
                    return (
                      <button className={`doc-row ${selectedDoc === file ? "active" : ""}`} key={`${file}-${i}`} onClick={() => onDocClick(file)} type="button">
                        <Icon name="file" />
                        <div><strong>{String(file).replace("raw_docs/", "")}</strong>{label ? <span>{label}</span> : null}</div>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="ws-content-note">
              <Icon name="database" />
              <div>
                <strong>{cur?.name || workspace}</strong>
                <span>{wsDetail?.loading ? "正在载入数据画像…" : "已接入。点击下方提问即可开始分析；该工作区的字段画像会显示在这里。"}</span>
              </div>
            </div>
          )}
        </section>
        <section>
          <div className="section-title">命中片段</div>
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

function ChatPanel({ activeTab, setActiveTab, messages, trace, running, input, setInput, run, artifacts, docHits, health, onUpload, onProduce, stream, producing, onCite }) {
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
          ["docs", "原文出处", docHits.length],
        ].map(([key, label, count]) => (
          <button className={activeTab === key ? "active" : ""} key={key} onClick={() => setActiveTab(key)} type="button" role="tab" aria-selected={activeTab === key}>
            {label}{count ? <span>{count}</span> : null}
          </button>
        ))}
      </div>
      <div className="panel-scroll">
        {activeTab === "chat" ? <ChatStream messages={messages} trace={trace} running={running} run={run} onUpload={onUpload} onProduce={onProduce} artifacts={artifacts} stream={stream} producing={producing} onCite={onCite} /> : null}
        {activeTab === "artifacts" ? <ArtifactShelf artifacts={artifacts} running={running || producing} /> : null}
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

const CONF_LABEL = { data_confirmed: "数据确证", market_inferred: "市场推断", speculative: "推测" };
function confLabel(c) { return CONF_LABEL[c] || c || ""; }

// 把正文里内嵌的注解抽成结构化 citations，正文用占位角标替代。支持：
//   [ref: raw_docs/xx#yy，conf] / [source: 文本，conf] / [gap: "文本"，conf] / 裸 [raw_docs/...]
// 若后端已提供结构化 citations（WP-R2 后），正文里本就是 [n]，直接沿用。
function extractCitations(text, existing) {
  const t = text || "";
  // 后端结构化 citations（batch5+）：正文是 [n] 标记，把它们转成可点角标，并把字段映射成前端用的形状
  if (existing && existing.length) {
    const cites = existing.map((c) => ({
      marker: c.marker,
      kind: c.source_type === "market" ? "source" : "ref",
      source_file: c.source_file || "",
      chunk_id: c.chunk_id || "",
      label: c.source_type === "market" ? (c.source_file || c.snippet || "外部来源") : String(c.source_file || "").replace(/^.*\//, ""),
      confidence: c.confidence || "",
      snippet: c.snippet || "",
      source_url: c.source_url || "",
    }));
    const maxM = Math.max(0, ...cites.map((c) => c.marker || 0));
    const clean = t.replace(/\[(\d+)\]/g, (m, n) => (Number(n) >= 1 && Number(n) <= maxM ? `@@CITE:${n}@@` : m));
    return { text: clean, cites };
  }
  const cites = []; const seen = new Map();
  const clean = t.replace(/\[([^\][]+)\]/g, (whole, inner) => {
    const km = inner.match(/^\s*(ref|source|evidence|gap|引用|来源|证据|缺口)\s*[:：]\s*(.*)$/is);
    let kind = "ref"; let body = inner.trim();
    if (km) {
      const w = km[1].toLowerCase();
      kind = (w === "gap" || w === "缺口") ? "gap" : (w === "source" || w === "来源") ? "source" : "ref";
      body = km[2].trim();
    } else if (!/(raw_docs|external)\//.test(inner)) {
      return whole;
    }
    let confidence = ""; const parts = body.split(/[，,]\s*/);
    if (parts.length > 1 && /confirmed|inferred|speculative/i.test(parts[parts.length - 1])) {
      confidence = parts.pop().trim().toLowerCase(); body = parts.join("，");
    }
    let source_file = ""; let chunk_id = ""; let label = body;
    const pm = body.match(/((?:raw_docs|external)\/[^\s，,#]+)(?:#([^\s，,]+))?/);
    if (pm) { source_file = pm[1]; chunk_id = pm[2] || ""; if (kind === "ref") label = source_file.replace(/^.*\//, ""); }
    label = label.replace(/^["'“”]+|["'“”]+$/g, "").trim();
    const key = `${kind}|${source_file}|${label}`;
    let idx = seen.get(key);
    if (idx == null) { idx = cites.length + 1; seen.set(key, idx); cites.push({ marker: idx, kind, source_file, chunk_id, label, confidence }); }
    return `@@CITE:${idx}@@`;
  });
  return { text: clean, cites };
}

// 行内：解析 **加粗** 与引用占位角标（@@CITE:n@@ 唯一占位符，不误伤正文数字）
function renderInline(str, onCiteMarker) {
  const nodes = []; const re = /\*\*([^*]+)\*\*|@@CITE:(\d+)@@/g;
  let m, last = 0, k = 0;
  while ((m = re.exec(str))) {
    if (m.index > last) nodes.push(str.slice(last, m.index));
    if (m[1] != null) nodes.push(<strong key={k++}>{m[1]}</strong>);
    else nodes.push(<sup key={k++} className="cite-chip" onClick={() => onCiteMarker(Number(m[2]))} title="查看出处">[{m[2]}]</sup>);
    last = m.index + m[0].length;
  }
  if (last < str.length) nodes.push(str.slice(last));
  return nodes;
}

// 富文本回答：轻量 markdown（标题/要点/加粗）+ 引用角标 + 底部"依据来源"芯片
function RichAnswer({ text, citations, onCite }) {
  const { text: clean, cites } = useMemo(() => extractCitations(text, citations), [text, citations]);
  const byMarker = (n) => { const c = cites.find((x) => x.marker === n); if (c) onCite(c); };
  const blocks = useMemo(() => {
    const out = []; let list = null;
    const flush = () => { if (list) { out.push({ type: "ul", items: list }); list = null; } };
    for (const raw of clean.split(/\n/)) {
      const line = raw.trimEnd();
      if (!line.trim()) { flush(); continue; }
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      const li = line.match(/^\s*[-*•]\s+(.*)$/) || line.match(/^\s*\d+[.、]\s+(.*)$/);
      if (h) { flush(); out.push({ type: "h", level: h[1].length, text: h[2] }); }
      else if (li) { (list = list || []).push(li[1]); }
      else { flush(); out.push({ type: "p", text: line }); }
    }
    flush(); return out;
  }, [clean]);
  return (
    <div className="rich-answer">
      {blocks.map((b, i) =>
        b.type === "h" ? <p key={i} className={`ra-h ra-h${b.level}`}>{renderInline(b.text, byMarker)}</p>
          : b.type === "ul" ? <ul key={i} className="ra-ul">{b.items.map((it, j) => <li key={j}>{renderInline(it, byMarker)}</li>)}</ul>
            : <p key={i} className="ra-p">{renderInline(b.text, byMarker)}</p>,
      )}
      {cites.length ? (
        <div className="ra-cites">
          <span className="ra-cites-label">依据来源</span>
          <div className="ra-cite-row">
            {cites.map((c) => (
              <button key={c.marker} className={`ra-cite ${c.kind || "ref"}`} type="button" onClick={() => onCite(c)} title={c.source_file || c.label}>
                <span className="ra-cite-n">[{c.marker}]</span>
                <span className="ra-cite-src">{((c.kind === "ref" && c.source_file ? c.source_file : (c.label || c.source_file || "来源"))).replace(/^raw_docs\//, "")}</span>
                {c.kind === "gap" ? <span className="ra-cite-kind">缺口</span> : c.kind === "source" ? <span className="ra-cite-kind">外部</span> : null}
                {c.confidence ? <span className={`ra-cite-conf ${c.confidence}`}>{confLabel(c.confidence)}</span> : null}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ChatStream({ messages, trace, running, run, onUpload, onProduce, artifacts, stream, producing, onCite }) {
  const endRef = useRef(null);
  // 消息/段落变化时平滑滚动；逐字流期间用 instant（每帧 smooth 滚动会与流式重渲染抢主线程，造成读流背压拖慢整轮）
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "end" });
  }, [messages.length, trace.length, running]);
  useEffect(() => {
    if (stream) endRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [stream]);

  const hasArtifacts = Object.values(artifacts || {}).some(Boolean);
  // 最后一条助手消息的下标（产物按钮 / 打字机只挂在它上面）
  const lastAsstIdx = messages.map((m) => m.role).lastIndexOf("assistant");
  const showProduce = lastAsstIdx >= 0 && !running && !hasArtifacts;

  if (!messages.length && !running) {
    return <Welcome run={run} onUpload={onUpload} />;
  }
  // 按时间顺序逐条渲染（多轮对话不再被拆成"先所有提问、后所有回复"）
  return (
    <div className="chat-stream">
      {messages.map((m, i) =>
        m.role === "user" ? (
          <Bubble key={`m-${i}`} role="user" name="你" time={m.time}>
            <p className="bubble-text">{m.text}</p>
          </Bubble>
        ) : (
          <Bubble key={`m-${i}`} role="assistant" name="DataForge" time={m.time}>
            <RichAnswer text={m.text} citations={m.citations} onCite={onCite} />
            {i === lastAsstIdx && showProduce ? (
              <div className="produce-cta">
                <button className="primary" type="button" onClick={onProduce} disabled={producing}>
                  <Icon name="spark" /> {producing ? "生成中…" : "据此生成完整产物"}
                </button>
                <span>{producing ? "正在复用本版分析生成产物，无需重跑…" : "满意这版分析？生成项目书 PDF、概念图与语音摘要"}</span>
              </div>
            ) : null}
          </Bubble>
        ),
      )}
      {running ? <ActivityTimeline trace={trace} running={running} /> : null}
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

const EVENT_LABELS = {
  ready: "就绪", user: "提问", plan: "规划", route: "路由", role_change: "切换", tool_call: "调用工具",
  tool_result: "工具结果", model_response: "模型推理", audit: "审计", clarify: "澄清", final: "完成", error: "错误",
};

const INTENT_LABEL = {
  feasibility_analysis: "可行性分析", followup_edit: "续写/改写", smalltalk_or_meta: "寒暄/元信息",
  clarify_needed: "需要澄清", corpus_qa: "语料问答", product_feasibility: "产品可行性",
};
function intentLabel(x) { return INTENT_LABEL[x] || x || "?"; }

// 把扁平事件流按"哪个智能体在执行"聚合成段：一个 plan/role_change 开一段，
// 其后的 tool_call/tool_result/model_response/audit 等子步都并入该段（progress/逐字流不入段）。
function buildSegments(trace) {
  const segs = [];
  let cur = null;
  const open = (agent, kind, data) => { cur = { agent, kind, data: data || {}, events: [] }; segs.push(cur); };
  for (const item of trace) {
    const ev = item.event;
    if (ev === "answer_delta" || ev === "delta" || ev === "progress") continue;
    if (ev === "plan") { open("df-coordinator", "plan", item.data); cur.events.push(item); continue; }
    if (ev === "role_change") { open(item.data?.agent, "agent", item.data); cur.events.push(item); continue; }
    if (ev === "final") { open("__final__", "final", item.data); cur.events.push(item); continue; }
    if (ev === "error") { open("__error__", "error", item.data); cur.events.push(item); continue; }
    if (!cur) open(item.data?.agent || "__intro__", "intro", item.data);
    cur.events.push(item);
  }
  return segs;
}

function segMatchesFilter(ev, filter) {
  if (filter === "tools") return ev.event === "tool_call" || ev.event === "tool_result";
  if (filter === "models") return ev.event === "model_response";
  if (filter === "audit") return ev.event === "audit";
  return true;
}

// 单条子步的中文描述
function stepText(item) {
  const d = item.data || {};
  switch (item.event) {
    case "ready": return "已连接编排器";
    case "user": return `收到提问：${d.text || ""}`;
    case "plan": return `规划意图「${intentLabel(d.intent)}」→ ${(d.experts || []).map(agentLabel).join("、") || "协调器直接处理"}`;
    case "route": return `判定意图「${intentLabel(d.intent)}」${(d.experts || []).length ? `→ 调度 ${(d.experts || []).map(agentLabel).join("、")}` : "→ 协调器直接处理，不惊动分析师"}${d.reason ? `（${d.reason}）` : ""}`;
    case "role_change": return `${agentLabel(d.agent)} 接手${d.revision ? `（第 ${d.revision} 次修订）` : ""}`;
    case "tool_call": return `调用工具 ${d.name || "tool"}`;
    case "tool_result": return `工具 ${d.name || "tool"} 返回 · ${d.count ?? (d.bytes ? `${Math.round(d.bytes / 1024)}KB` : "ok")}`;
    case "model_response": return `推理完成 · ${d.usage?.total_tokens ?? 0} tokens · ${String(d.response_id || "n/a").slice(0, 16)}…`;
    case "audit": return `审计：${d.verdict === "pass" ? "通过" : "打回修订"}${d.issues?.length ? ` — ${d.issues[0]}` : ""}`;
    case "clarify": return `需要澄清：${d.question || ""}`;
    case "final": return d.text || "已生成最终结论";
    case "error": return `运行出错：${d.message || ""}`;
    default: return EVENT_LABELS[item.event] || item.event;
  }
}

function segStatus(seg, active) {
  if (seg.kind === "final") return { label: "完成", tone: "final" };
  if (seg.kind === "error") return { label: "出错", tone: "error" };
  const audit = [...seg.events].reverse().find((e) => e.event === "audit");
  if (audit) return audit.data?.verdict === "pass" ? { label: "审计通过", tone: "ok" } : { label: "打回修订", tone: "warn" };
  if (active) return { label: "进行中", tone: "active" };
  return { label: "已完成", tone: "done" };
}

function segSummary(seg) {
  if (seg.kind === "final") return seg.data?.text ? String(seg.data.text).slice(0, 80) : "已生成最终结论";
  if (seg.kind === "plan") return `${seg.data?.intent || "规划"} · 调度 ${(seg.data?.experts || []).length} 个智能体`;
  if (seg.kind === "error") return seg.data?.message || "运行出错";
  const tools = seg.events.filter((e) => e.event === "tool_call").length;
  const tok = seg.events.filter((e) => e.event === "model_response").reduce((s, e) => s + (e.data?.usage?.total_tokens || 0), 0);
  const bits = [];
  if (tools) bits.push(`${tools} 次工具调用`);
  if (tok) bits.push(`${tok} tokens 推理`);
  return bits.join(" · ") || `${seg.events.length} 个事件`;
}

function segHead(seg) {
  if (seg.kind === "final") return { name: "最终结论", role: "汇总输出" };
  if (seg.kind === "plan") return { name: "协调器", role: "意图路由" };
  if (seg.kind === "error") return { name: "运行错误", role: "" };
  if (seg.kind === "intro") return { name: "编排器", role: "初始化" };
  return { name: agentLabel(seg.agent), role: AGENTS[seg.agent]?.role || "" };
}

// 一个智能体一张卡：运行中自动展开逐行显示子步；完成后折叠成一行，点标题可重新展开。
function TraceSegment({ seg, index, active, expanded, filter, onToggle }) {
  const head = segHead(seg);
  const status = segStatus(seg, active);
  const steps = filter === "all" ? seg.events : seg.events.filter((e) => segMatchesFilter(e, filter));
  return (
    <div className={`trace-item ${seg.kind} ${active ? "active" : ""} ${expanded ? "open" : ""}`} style={{ "--i": index }}>
      <div className="trace-rail">
        <span className={`dot ${active ? "loading" : "done"}`} />
      </div>
      <div className="trace-card seg-card">
        <button className="seg-head" type="button" onClick={onToggle} aria-expanded={expanded}>
          <span className="seg-name">{head.name}</span>
          {head.role ? <span className="seg-role">{head.role}</span> : null}
          <span className={`seg-status ${status.tone}`}>{status.label}</span>
          <span className="seg-count">{seg.events.length} 步</span>
          <span className={`seg-caret ${expanded ? "open" : ""}`} aria-hidden="true">▸</span>
        </button>
        {expanded ? (
          <ol className="seg-steps">
            {steps.map((e, i) => {
              const d = describeEvent(e) || {};
              return (
                <li className={`seg-step ${d.tone || ""}`} key={i}>
                  <span className="seg-step-ico"><Icon name={d.icon || "spark"} /></span>
                  <span className="seg-step-tx">{stepText(e)}</span>
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="seg-summary">{segSummary(seg)}</p>
        )}
      </div>
    </div>
  );
}

function TracePanel({ trace, running, filter, setFilter, scrollRef, openSegs, toggleSeg }) {
  const counts = useMemo(() => ({
    total: trace.filter((t) => t.event !== "progress" && t.event !== "answer_delta" && t.event !== "delta").length,
    tools: trace.filter((item) => item.event === "tool_call" || item.event === "tool_result").length,
    models: trace.filter((item) => item.event === "model_response").length,
    audit: trace.filter((item) => item.event === "audit").length,
  }), [trace]);
  const segments = useMemo(() => buildSegments(trace), [trace]);
  return (
    <aside className="trace-panel">
      <header>
        <div>
          <h2>智能体追踪</h2>
          {(() => {
            const done = trace.some((t) => t.event === "final");
            return <span className={running ? "live" : done ? "ok" : ""}>{running ? "运行中" : done ? `已完成 · ${segments.length} 个智能体` : "待运行"}</span>;
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
        {segments.length ? segments.map((seg, index) => {
          const active = running && index === segments.length - 1 && seg.kind !== "final";
          const expanded = filter !== "all" ? true : (openSegs[index] ?? active);
          return (
            <TraceSegment
              key={index}
              seg={seg}
              index={index}
              active={active}
              expanded={expanded}
              filter={filter}
              onToggle={() => toggleSeg(index, !(openSegs[index] ?? active))}
            />
          );
        }) : <p className="empty-note">运行后这里按智能体分组显示协作过程。</p>}
      </div>
    </aside>
  );
}

// 上传弹窗：客户命名 + 描述 + 多文件（可滚动列表）+ 确认/取消
function UploadModal({ open, onClose, onSubmit, busy }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);
  useEffect(() => { if (open) { setName(""); setDescription(""); setFiles([]); } }, [open]);
  if (!open) return null;
  const addFiles = (list) => {
    const arr = Array.from(list || []);
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name + f.size));
      return [...prev, ...arr.filter((f) => !seen.has(f.name + f.size))];
    });
  };
  const submit = () => { if (files.length) onSubmit({ name: name.trim(), description: description.trim(), files }); };
  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>接入我的数据</h3>
          <button className="modal-x" type="button" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <label className="modal-field">
          <span>工作区名称</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="给这份数据起个名字，例如：俱乐部会员数据" />
        </label>
        <label className="modal-field">
          <span>数据描述（可选）</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} placeholder="简单描述这份数据是什么、想用它做什么产品" />
        </label>
        <div
          className={`drop-zone ${dragOver ? "over" : ""}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files); }}
        >
          <Icon name="upload" />
          <strong>上传我的数据</strong>
          <span>点击选择或拖拽文件到此<br />支持 CSV / Excel(.xlsx/.xls) / JSON / Markdown</span>
          <input ref={inputRef} type="file" multiple accept=".csv,.xlsx,.xls,.json,.md,.txt" hidden
            onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
        </div>
        {files.length ? (
          <div className="file-list">
            {files.map((f, i) => (
              <div className="file-row" key={f.name + i}>
                <Icon name="file" />
                <span className="file-name">{f.name}</span>
                <span className="file-size">{f.size > 1024 ? `${Math.round(f.size / 1024)} KB` : `${f.size} B`}</span>
                <button className="file-del" type="button" onClick={() => setFiles((p) => p.filter((_, j) => j !== i))} aria-label="移除">×</button>
              </div>
            ))}
          </div>
        ) : null}
        <div className="modal-actions">
          <button className="ghost" type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="primary" type="button" onClick={submit} disabled={busy || !files.length}>
            {busy ? "上传中…" : `确认上传${files.length ? `（${files.length}）` : ""}`}
          </button>
        </div>
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
  const [producing, setProducing] = useState(false);
  const [openSegs, setOpenSegs] = useState({}); // 用户手动展开/折叠的智能体段（按段下标覆盖默认）
  const toggleSeg = (i, val) => setOpenSegs((m) => ({ ...m, [i]: val }));
  const [uploadModal, setUploadModal] = useState(false);
  const [wsDetail, setWsDetail] = useState(null); // 非内置工作区的画像详情（GET /api/workspaces/{id}）
  const traceRef = useRef(null);
  const lastAnalysisRef = useRef(null);

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

  // 客户在上传弹窗里命名 + 写描述 + 选多文件，确认后逐个接入到同一个命名工作区
  async function uploadBundle({ name, description, files }) {
    if (!files || !files.length) return;
    setUpload({ status: "uploading", stage: 1, message: `正在上传 ${files.length} 个文件…` });
    try {
      let wid = null; let last = null;
      for (let i = 0; i < files.length; i++) {
        const form = new FormData();
        form.append("file", files[i]);
        if (name) form.append("name", name);
        if (description) form.append("description", description);
        if (wid) form.append("workspace_id", wid);
        const res = await fetch(`${API_BASE}/api/upload`, { method: "POST", body: form });
        if (!res.ok) throw new Error(res.status === 404 ? "数据接入服务尚未就绪，请稍后再试" : `上传失败：HTTP ${res.status}`);
        last = await res.json();
        wid = last.workspace_id || last.id || wid;
        setUpload({ status: "uploading", stage: 2, message: `已接入 ${i + 1}/${files.length} 个文件…` });
      }
      const dispName = name || last?.name || wid;
      setUpload({ status: "done", message: `已接入「${dispName}」`, summary: last });
      if (wid) {
        setWorkspaces((ws) => (ws.some((w) => w.id === wid)
          ? ws.map((w) => (w.id === wid ? { ...w, name: dispName, docs: last?.indexed_count ?? w.docs } : w))
          : [...ws, { id: wid, name: dispName, docs: last?.indexed_count }]));
        setWorkspace(wid);
        setMessages([]); setTrace([]); setArtifacts({}); setDocHits([]); setSelectedDoc("");
      }
      setUploadModal(false);
    } catch (e) {
      setUpload({ status: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }
  const triggerUpload = () => { setUpload(null); setUploadModal(true); };

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

  // 切到非内置工作区时，拉取其画像详情（schema/行数/文档），用于左侧"工作区内容"
  useEffect(() => {
    if (!workspace || workspace === "demo-corpus") { setWsDetail(null); return; }
    let cancelled = false;
    setWsDetail({ loading: true });
    fetch(`${API_BASE}/api/workspaces/${encodeURIComponent(workspace)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => { if (!cancelled) setWsDetail(d); })
      .catch(() => { if (!cancelled) setWsDetail(null); });
    return () => { cancelled = true; };
  }, [workspace]);

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
    setOpenSegs({}); // 新一轮运行重置分组展开状态
    setActiveTab("chat");
    // 追加本轮提问（不再清空历史，保留多轮对话）
    setMessages((items) => [...items, { role: "user", text: message, time: "刚刚" }]);
    let streamed = false;
    // 逐字流：把众多 answer_delta 攒进 streamText，按帧（rAF）合并刷新，避免上千次重渲染
    let streamText = "";
    let rafId = 0;
    const flushStream = () => { rafId = 0; setStream(streamText); };
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
          // answer_delta/delta 只喂中间对话框的逐字流；progress 是心跳，二者都不进右侧追踪
          const traceEvents = parsed.events.filter((e) => e.event !== "answer_delta" && e.event !== "delta" && e.event !== "progress");
          if (traceEvents.length) setTrace((items) => [...items, ...traceEvents]);
          for (const ev of parsed.events) {
            if (ev.event === "answer_delta" || ev.event === "delta") {
              streamed = true;
              const piece = ev.data?.delta ?? ev.data?.text ?? "";
              if (piece) { streamText += piece; if (!rafId) rafId = requestAnimationFrame(flushStream); }
            }
          }
          const clarify = parsed.events.find((event) => event.event === "clarify");
          if (clarify) {
            setMessages((items) => [...items, { role: "assistant", text: clarify.data.question || clarify.data.text || clarify.data.reason || "我需要多了解一点你的目标，方便给出更有据的分析。", time: "刚刚" }]);
          }
          const final = parsed.events.find((event) => event.event === "final");
          if (final) {
            const art = final.data?.artifact || {};
            const proposal = art.proposal || {};
            const corpusHits = art.corpus?.hits || [];
            setDocHits(corpusHits);
            // 记下本版分析，供"据此生成完整产物"复用（不重跑 6 agent）
            lastAnalysisRef.current = {
              workspace_id: art.workspace_id || workspace,
              conversation_id: art.conversation_id || null,
              feasibility: art.feasibility || {},
              corpus: art.corpus || {},
              market: art.market || {},
            };
            setArtifacts({
              pdf: proposal.pdf,
              concept_image: proposal.concept_image,
              audio_summary: proposal.audio_summary,
            });
            if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
            setMessages((items) => [...items, { role: "assistant", text: final.data.text, time: "刚刚", streamed, citations: art.citations || [] }]);
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

  // 据本版分析直接出产物：调 /api/produce 复用上一版报告，不重跑 6 agent
  async function produce() {
    const a = lastAnalysisRef.current;
    if (!a || producing || running) return;
    setProducing(true);
    setActiveTab("artifacts");
    try {
      const res = await fetch(`${API_BASE}/api/produce`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(a),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      setArtifacts({ pdf: d.pdf, concept_image: d.concept_image, audio_summary: d.audio_summary });
    } catch (e) {
      setActiveTab("chat");
      setMessages((items) => [...items, { role: "assistant", text: `生成产物失败：${e instanceof Error ? e.message : String(e)}`, time: "刚刚" }]);
    } finally {
      setProducing(false);
    }
  }

  return (
    <div className="app-shell">
      <UploadModal open={uploadModal} onClose={() => setUploadModal(false)} onSubmit={uploadBundle} busy={upload?.status === "uploading"} />
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
        wsDetail={wsDetail}
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
        producing={producing}
        onCite={(c) => {
          const url = c.source_url || (/^https?:\/\//.test(c.source_file || "") ? c.source_file : "");
          if (url) window.open(url, "_blank", "noopener");
          else if (c.source_file) loadDoc(c.source_file);
        }}
      />
      <div className="trace-scroll">
        <TracePanel trace={trace} running={running} filter={traceFilter} setFilter={setTraceFilter} scrollRef={traceRef} openSegs={openSegs} toggleSeg={toggleSeg} />
      </div>
    </div>
  );
}
