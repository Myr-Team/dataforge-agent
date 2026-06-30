import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FileSpreadsheet,
  FileText,
  RefreshCw,
  Plus,
  Table2,
  Search,
  Check,
  AlertTriangle,
  UploadCloud,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Send,
  Save,
  X,
  Info,
  History,
  Loader2,
  FileUp,
} from "lucide-react";
import {
  dwListFiles,
  dwFileContent,
  dwCreateFile,
  dwSaveCells,
  dwSaveContent,
  dwFileQuality,
  dwFieldMapping,
  dwSaveFieldMapping,
  dwFileHistory,
  dwAnalyzeFiles,
} from "./api.js";

const TABS = [
  { id: "table", label: "内容编辑" },
  { id: "quality", label: "数据质量" },
  { id: "mapping", label: "字段映射" },
  { id: "history", label: "版本历史" },
];

const CONNECTORS = [
  { id: "blob", name: "Azure Blob Storage", src: "/icons/azure-blob.svg", state: "available", hint: "对象存储 · 支持连接接入" },
  { id: "sql", name: "SQL Database", src: "/icons/sql-database.svg", state: "available", hint: "数据库 · 账号密码连接" },
  { id: "adl", name: "Azure Data Lake", src: "/icons/data-lake.svg", state: "planned", hint: "数据湖 · 计划上线" },
  { id: "upload", name: "CSV / Excel 上传", icon: FileUp, state: "available", hint: "本地上传文件" },
];

const PAGE = 100;

function fileIconFor(type, name = "") {
  const t = (type || (name.split(".").pop() || "")).toLowerCase();
  if (t === "md" || t === "markdown" || t === "txt") return <FileText size={15} className="fi fi-md" />;
  if (t === "xlsx") return <FileSpreadsheet size={15} className="fi fi-xlsx" />;
  return <FileSpreadsheet size={15} className="fi fi-csv" />;
}

function fmtBytes(b) {
  const n = Number(b || 0);
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function fmtTime(v) {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function DataWorkbench({ dashboard, onUpload, onOpenConversation }) {
  const workspaceId = dashboard?.workspace_id || dashboard?.workspace?.workspace_id || "";
  const [tab, setTab] = useState("table");
  const [groups, setGroups] = useState([]);
  const [storage, setStorage] = useState(null);
  const [filesLoading, setFilesLoading] = useState(false);

  const [active, setActive] = useState(null); // {id,name,type}
  const [openTabs, setOpenTabs] = useState([]);
  const [content, setContent] = useState(null); // table or markdown
  const [contentLoading, setContentLoading] = useState(false);
  const [rows, setRows] = useState([]); // editable table rows (current page)
  const [mdText, setMdText] = useState("");
  const [edits, setEdits] = useState({}); // "rowIdx:colName" -> value (page-relative rows + offset)
  const [offset, setOffset] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  const [quality, setQuality] = useState(null);
  const [mapping, setMapping] = useState(null);
  const [mapDraft, setMapDraft] = useState({});
  const [history, setHistory] = useState([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [toast, setToast] = useState("");
  const [q, setQ] = useState("");
  const [collapsed, setCollapsed] = useState({});
  const toastT = useRef(null);

  const showToast = useCallback((msg) => {
    setToast(msg);
    window.clearTimeout(toastT.current);
    toastT.current = window.setTimeout(() => setToast(""), 2600);
  }, []);

  const reloadFiles = useCallback(async () => {
    if (!workspaceId) return;
    setFilesLoading(true);
    try {
      const data = await dwListFiles(workspaceId);
      setGroups(data.groups || []);
      setStorage(data.storage || null);
      return data;
    } catch (e) {
      showToast(`加载文件库失败：${e.message}`);
    } finally {
      setFilesLoading(false);
    }
  }, [workspaceId, showToast]);

  // 初次/切工作区：拉文件库，默认打开第一个文件
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const data = await reloadFiles();
      if (cancelled || !data) return;
      const first = (data.groups || []).flatMap((g) => g.files || [])[0];
      if (first && !active) openFile(first);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  const isMd = active && ["md", "markdown", "txt"].includes(String(active.type || "").toLowerCase());

  const loadContent = useCallback(async (file, off = 0) => {
    setContentLoading(true);
    setEdits({});
    setDirty(false);
    try {
      const data = await dwFileContent(workspaceId, file.id, { limit: PAGE, offset: off });
      setContent(data);
      setOffset(off);
      if (data.kind === "markdown") setMdText(data.text || "");
      else setRows((data.rows || []).map((r) => [...r]));
    } catch (e) {
      showToast(`加载内容失败：${e.message}`);
    } finally {
      setContentLoading(false);
    }
  }, [workspaceId, showToast]);

  const loadSidePanels = useCallback(async (file) => {
    setQuality(null); setMapping(null); setHistory([]); setMapDraft({});
    dwFileQuality(workspaceId, file.id).then(setQuality).catch(() => {});
    dwFieldMapping(workspaceId, file.id).then(setMapping).catch(() => {});
    dwFileHistory(workspaceId, file.id).then((h) => setHistory(Array.isArray(h) ? h : [])).catch(() => {});
  }, [workspaceId]);

  const openFile = useCallback((file) => {
    setActive(file);
    setOpenTabs((tabs) => (tabs.find((t) => t.id === file.id) ? tabs : [...tabs, file]));
    setTab("table");
    loadContent(file, 0);
    loadSidePanels(file);
  }, [loadContent, loadSidePanels]);

  const closeTab = (file, e) => {
    e.stopPropagation();
    setOpenTabs((tabs) => {
      const next = tabs.filter((t) => t.id !== file.id);
      if (active?.id === file.id) {
        const nx = next[next.length - 1];
        if (nx) openFile(nx); else { setActive(null); setContent(null); }
      }
      return next;
    });
  };

  const onCellChange = (pageRowIdx, colName, value) => {
    setRows((rs) => { const copy = rs.map((r) => [...r]); const ci = colIndex(colName); if (ci >= 0) copy[pageRowIdx][ci] = value; return copy; });
    setEdits((m) => ({ ...m, [`${offset + pageRowIdx}:${colName}`]: value }));
    setDirty(true);
  };
  const columns = useMemo(() => (content?.columns || []).map((c) => (typeof c === "string" ? c : c.name)), [content]);
  const colIndex = (name) => columns.indexOf(name);

  const onMdChange = (v) => { setMdText(v); setDirty(true); };

  const save = async () => {
    if (!active || !dirty || saving) return;
    setSaving(true);
    try {
      if (isMd) {
        await dwSaveContent(workspaceId, active.id, mdText);
      } else {
        const editList = Object.entries(edits).map(([k, value]) => { const [row, col] = k.split(/:(.+)/); return { row: Number(row), col, value }; });
        if (editList.length) await dwSaveCells(workspaceId, active.id, editList);
      }
      setDirty(false); setEdits({});
      showToast("已保存");
      await loadContent(active, offset); // 重新载入校验持久化
      loadSidePanels(active);
      reloadFiles();
    } catch (e) {
      showToast(`保存失败：${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const createFile = async (kind) => {
    const name = window.prompt(kind === "md" ? "新建 Markdown 文件名" : "新建表格文件名", kind === "md" ? "新建笔记" : "新建表格");
    if (!name) return;
    try {
      const body = kind === "md" ? { name, type: "md", text: "# " + name + "\n\n" } : { name, kind: "table", columns: ["列1", "列2"], rows: [["", ""]] };
      const res = await dwCreateFile(workspaceId, body);
      showToast("已创建并入库");
      const data = await reloadFiles();
      const created = (data?.groups || []).flatMap((g) => g.files || []).find((f) => f.id === res.file?.id) || res.file;
      if (created?.id) openFile(created);
    } catch (e) {
      showToast(`新建失败：${e.message}`);
    }
  };

  const saveMapping = async () => {
    if (!active || !mapping) return;
    const m = {};
    for (const [src, target] of Object.entries(mapDraft)) if (target && target.trim()) m[src] = target.trim();
    try {
      const res = await dwSaveFieldMapping(workspaceId, active.id, m);
      setMapping(res); setMapDraft({});
      showToast("字段映射已保存");
      dwFileQuality(workspaceId, active.id).then(setQuality).catch(() => {});
    } catch (e) {
      showToast(`保存映射失败：${e.message}`);
    }
  };

  const sendToAnalysis = async () => {
    if (!active || analyzing) return;
    setAnalyzing(true);
    showToast("已发送到分析，Agent 正在处理…");
    try {
      const res = await dwAnalyzeFiles(workspaceId, [active.id], `请基于 ${active.name} 做一次可行性分析`);
      const cid = res?.conversation_id || res?.jump?.conversation_id;
      if (cid && onOpenConversation) onOpenConversation(cid);
    } catch (e) {
      showToast(`发送分析失败：${e.message}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const totalRows = content?.total_rows ?? rows.length;
  const totalCols = content?.total_cols ?? columns.length;
  const pageCount = Math.max(1, Math.ceil(totalRows / PAGE));
  const curPage = Math.floor(offset / PAGE);
  const gotoPage = (p) => { if (active && !isMd) loadContent(active, Math.max(0, Math.min(pageCount - 1, p)) * PAGE); };

  const filteredGroups = useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (!kw) return groups;
    return groups.map((g) => ({ ...g, files: (g.files || []).filter((f) => String(f.name || "").toLowerCase().includes(kw)) })).filter((g) => (g.files || []).length);
  }, [groups, q]);

  return (
    <main className="agent-studio data-stage">
      {toast ? <div className="dw-toast">{toast}</div> : null}

      <header className="dw-head">
        <div>
          <div className="dw-title"><h1>数据工作台</h1><Info size={16} className="dw-info" /></div>
          <p>在工作区内上传、新建、预览和编辑数据（Markdown / CSV / Excel）；保存后即入库，可被 Agent 分析引用。</p>
        </div>
        <div className="dw-save">
          {dirty ? <span className="dw-save-state dirty"><span className="dot" />有未保存更改</span> : <span className="dw-save-state ok"><Check size={14} />已保存</span>}
          <span className="dw-save-sub">{dirty ? "记得保存你的修改" : "所有更改已保存"}</span>
        </div>
      </header>

      <div className="dw-actions">
        <div className="dw-actions-l">
          <button className="dw-btn primary" type="button" onClick={() => onUpload && onUpload(workspaceId)}><UploadCloud size={15} />上传文件</button>
          <button className="dw-btn" type="button" onClick={() => createFile("md")}><FileText size={15} />新建 Markdown</button>
          <button className="dw-btn" type="button" onClick={() => createFile("table")}><Table2 size={15} />新建表格</button>
          <button className="dw-btn" type="button" disabled={!dirty || saving} onClick={save}>{saving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}保存更改</button>
          <button className="dw-btn ghost-blue" type="button" disabled={!active || analyzing} onClick={sendToAnalysis}>{analyzing ? <Loader2 size={15} className="spin" /> : <Send size={15} />}发送到分析</button>
        </div>
        <div className="dw-actions-r">
          <div className="dw-search"><Search size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索文件或字段…" /></div>
        </div>
      </div>

      <nav className="dw-tabs">
        {TABS.map((t) => (
          <button key={t.id} type="button" className={tab === t.id ? "dw-tab active" : "dw-tab"} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </nav>

      <div className="dw-body">
        {/* 左：文件库 */}
        <aside className="card dw-tree">
          <div className="dw-tree-head">
            <span className="t">文件库</span>
            <div className="dw-tree-acts">
              <button type="button" title="新建表格" onClick={() => createFile("table")}><Plus size={15} /></button>
              <button type="button" title="刷新" onClick={reloadFiles}>{filesLoading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}</button>
            </div>
          </div>
          <div className="dw-tree-body">
            {!filteredGroups.length && !filesLoading ? <p className="empty-copy" style={{ padding: "16px 12px" }}>暂无文件。上传或新建一个文件开始。</p> : null}
            {filteredGroups.map((g) => {
              const isCollapsed = collapsed[g.label];
              return (
                <div className="dw-group" key={g.label}>
                  <button type="button" className="dw-group-head" onClick={() => setCollapsed((m) => ({ ...m, [g.label]: !m[g.label] }))}>
                    <ChevronDown size={13} className="dw-group-caret" style={{ transform: isCollapsed ? "rotate(-90deg)" : "none" }} />{g.label}
                    <em style={{ marginLeft: "auto", fontStyle: "normal", color: "var(--faint)" }}>{(g.files || []).length}</em>
                  </button>
                  {!isCollapsed && (g.files || []).map((f) => (
                    <button key={f.id} type="button" className={active?.id === f.id ? "dw-file active" : "dw-file"} onClick={() => openFile(f)} title={f.name}>
                      {fileIconFor(f.type, f.name)}<span>{f.name}</span>
                      {f.record_count ? <em className="dw-file-rc">{f.record_count}</em> : null}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
          <div className="dw-tree-foot">
            <div className="dw-store">
              <span>{storage ? `${fmtBytes(storage.used_bytes)} / ${fmtBytes(storage.total_bytes)} 已使用` : "—"}</span>
              <div className="dw-store-bar"><i style={{ width: storage && storage.total_bytes ? `${Math.min(100, (storage.used_bytes / storage.total_bytes) * 100)}%` : "2%" }} /></div>
            </div>
          </div>
        </aside>

        {/* 中：编辑区 */}
        <section className="card dw-editor">
          <div className="dw-ftabs">
            {openTabs.map((f) => (
              <div key={f.id} className={active?.id === f.id ? "dw-ftab active" : "dw-ftab"} onClick={() => openFile(f)}>
                {fileIconFor(f.type, f.name)}<span>{f.name}</span><button type="button" className="dw-ftab-x" onClick={(e) => closeTab(f, e)}><X size={12} /></button>
              </div>
            ))}
          </div>

          {!active ? (
            <div className="empty-copy" style={{ padding: 40 }}>从左侧选择一个文件，或新建/上传文件。</div>
          ) : contentLoading ? (
            <div className="empty-copy" style={{ padding: 40, display: "flex", gap: 8, alignItems: "center", justifyContent: "center" }}><Loader2 size={16} className="spin" />加载内容…</div>
          ) : tab === "table" && isMd ? (
            <div className="dw-md">
              <div className="dw-md-bar">
                <span className="dw-md-name"><FileText size={14} />{active.name}</span>
                <span className="dw-md-time">{content?.total_chars != null ? `${content.total_chars} 字` : ""}</span>
              </div>
              <textarea className="dw-md-area" value={mdText} onChange={(e) => onMdChange(e.target.value)} />
            </div>
          ) : tab === "table" ? (
            <>
              <div className="dw-grid-wrap">
                <table className="dw-grid">
                  <thead>
                    <tr><th className="dw-rownum" />{columns.map((c, i) => <th key={i}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {rows.map((row, ri) => (
                      <tr key={ri}>
                        <td className="dw-rownum">{offset + ri + 1}</td>
                        {columns.map((c, ci) => (
                          <td key={ci} className="dw-cell">
                            <input className="dw-cell-in" value={row[ci] ?? ""} onChange={(e) => onCellChange(ri, c, e.target.value)} />
                          </td>
                        ))}
                      </tr>
                    ))}
                    {!rows.length ? <tr><td className="dw-rownum">1</td>{columns.map((c, ci) => <td key={ci} className="dw-cell" />)}</tr> : null}
                  </tbody>
                </table>
              </div>
              <div className="dw-grid-foot">
                <span>共 {totalRows} 行, {totalCols} 列</span>
                <span className="dw-rows-sel">第 {offset + 1}–{offset + rows.length} 行</span>
                <div className="dw-pager">
                  <button type="button" disabled={curPage === 0} onClick={() => gotoPage(0)}><ChevronsLeft size={14} /></button>
                  <button type="button" disabled={curPage === 0} onClick={() => gotoPage(curPage - 1)}><ChevronLeft size={14} /></button>
                  <span className="dw-page">{curPage + 1}</span><span className="dw-page-of">/ {pageCount}</span>
                  <button type="button" disabled={curPage >= pageCount - 1} onClick={() => gotoPage(curPage + 1)}><ChevronRight size={14} /></button>
                  <button type="button" disabled={curPage >= pageCount - 1} onClick={() => gotoPage(pageCount - 1)}><ChevronsRight size={14} /></button>
                </div>
              </div>
            </>
          ) : tab === "quality" ? (
            <QualityPanel quality={quality} />
          ) : tab === "mapping" ? (
            <MappingPanel mapping={mapping} mapDraft={mapDraft} setMapDraft={setMapDraft} onSave={saveMapping} />
          ) : (
            <HistoryPanel history={history} />
          )}
        </section>

        {/* 右：数据状态 */}
        <aside className="card dw-status">
          <div className="dw-status-head"><span className="t">数据状态</span></div>
          <div className="dw-sec">
            <div className="dw-sec-row"><span className="dw-sec-t">字段映射</span><span className="dw-sec-v">{mapping?.field_mapping ? `${mapping.field_mapping.mapped} / ${mapping.field_mapping.total} ` : "— "}<b className="ok">{mapping?.field_mapping ? `${Math.round(mapping.field_mapping.pct || 0)}%` : ""}</b></span></div>
            <div className="dw-prog"><i style={{ width: `${mapping?.field_mapping?.pct || 0}%` }} /></div>
            <button type="button" className="dw-link-btn" onClick={() => setTab("mapping")}>查看映射详情</button>
          </div>
          <div className="dw-sec">
            <div className="dw-sec-t">数据质量</div>
            <ul className="dw-qlist">
              <li><span>缺失值</span><span className="qv">{fmtPct(quality?.quality?.missing_pct)} {qIcon((quality?.quality?.missing_pct || 0) < 5)}</span></li>
              <li><span>重复值</span><span className="qv">{fmtPct(quality?.quality?.duplicate_pct)} {qIcon((quality?.quality?.duplicate_pct || 0) < 1)}</span></li>
              <li><span>异常值</span><span className="qv">{quality?.quality?.outlier_count ?? "—"} {qIcon((quality?.quality?.outlier_count || 0) === 0)}</span></li>
              <li><span>类型警告</span><span className="qv">{quality?.quality?.type_warnings ?? "—"} {qIcon((quality?.quality?.type_warnings || 0) === 0)}</span></li>
            </ul>
          </div>
          <div className="dw-sec">
            <div className="dw-sec-row"><span className="dw-sec-t">校验结果</span><span className={`dw-chip ${quality?.validation?.status === "passed" ? "ok" : "warn"}`}>{validationLabel(quality?.validation?.status)}</span></div>
            {quality?.validation?.checked_at ? <div className="dw-sec-sub">{fmtTime(quality.validation.checked_at)} 校验完成</div> : null}
          </div>
          <div className="dw-sec">
            <div className="dw-sec-t">最近修改</div>
            {history.length ? (
              <div className="dw-mod">
                <div className="dw-mod-av">{(history[0].user || "D").slice(0, 1)}</div>
                <div className="dw-mod-meta">
                  <div className="dw-mod-top"><span className="dw-mod-mail">{history[0].email || history[0].user || "DataForge"}</span><span className="dw-mod-time">{fmtTime(history[0].at)}</span></div>
                  <div className="dw-mod-desc">{history[0].change_summary || "—"}</div>
                </div>
              </div>
            ) : <div className="dw-sec-sub">暂无修改记录</div>}
            <button type="button" className="dw-link-btn" onClick={() => setTab("history")}><History size={13} />查看版本历史</button>
          </div>
        </aside>
      </div>

      {/* 底部：外部数据接入 */}
      <section className="card dw-connectors">
        <div className="dw-conn-head">
          <h2>外部数据接入</h2>
          <p>统一连接与管理企业对象存储、数据库等数据源；标注「计划上线」的连接器即将开放。</p>
        </div>
        <div className="dw-conn-grid">
          {CONNECTORS.map((c) => {
            const Icon = c.icon;
            const planned = c.state === "planned";
            return (
              <div className="dw-conn-card" key={c.id}>
                <div className="dw-conn-top">
                  <div className="dw-conn-ic">{c.src ? <img src={c.src} width="22" height="22" alt="" /> : <Icon size={20} />}</div>
                  {planned ? <span className="dw-badge planned">计划上线</span> : <span className="dw-badge ok">可用</span>}
                </div>
                <div className="dw-conn-name">{c.name}</div>
                <div className="dw-conn-status">{c.hint}</div>
                <button type="button" className={planned ? "dw-conn-btn" : "dw-conn-btn primary"} disabled={planned}
                  onClick={() => (planned ? null : c.id === "upload" ? (onUpload && onUpload(workspaceId)) : showToast(`${c.name} 连接：填入连接串/账号密码（即将开放向导）`))}>
                  {planned ? "敬请期待" : c.id === "upload" ? "上传文件" : "接入"}
                </button>
              </div>
            );
          })}
        </div>
      </section>
    </main>
  );
}

function fmtPct(v) { return v == null ? "—" : `${Number(v).toFixed(1)}%`; }
function qIcon(ok) { return ok ? <Check size={14} className="ok" /> : <AlertTriangle size={14} className="warn" />; }
function validationLabel(s) { return s === "passed" ? "通过" : s === "warn" ? "需复核" : s === "failed" ? "未通过" : "待校验"; }

function QualityPanel({ quality }) {
  if (!quality) return <div className="empty-copy" style={{ padding: 40 }}>暂无质量数据。</div>;
  const fields = quality.field_mapping?.fields || [];
  return (
    <div className="dw-panel">
      <div className="dw-grid-wrap">
        <table className="dw-grid">
          <thead><tr><th className="dw-rownum" /><th>字段</th><th>类型</th><th>缺失率</th><th>异常数</th><th>类型警告</th></tr></thead>
          <tbody>
            {fields.map((f, i) => (
              <tr key={i}><td className="dw-rownum">{i + 1}</td>
                <td className="dw-cell">{f.name}</td><td className="dw-cell">{f.type || "—"}</td>
                <td className="dw-cell">{f.missing_pct != null ? `${Number(f.missing_pct).toFixed(1)}%` : "—"}</td>
                <td className="dw-cell">{f.outlier_count ?? 0}</td>
                <td className="dw-cell">{f.type_warning ? "是" : "否"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MappingPanel({ mapping, mapDraft, setMapDraft, onSave }) {
  if (!mapping) return <div className="empty-copy" style={{ padding: 40 }}>暂无字段映射。</div>;
  const fields = mapping.field_mapping?.fields || [];
  return (
    <div className="dw-panel">
      <div className="dw-grid-wrap">
        <table className="dw-grid">
          <thead><tr><th className="dw-rownum" /><th>源字段</th><th>类型</th><th>目标字段（可改）</th><th>来源</th></tr></thead>
          <tbody>
            {fields.map((f, i) => (
              <tr key={i}><td className="dw-rownum">{i + 1}</td>
                <td className="dw-cell">{f.name}</td><td className="dw-cell">{f.type || "—"}</td>
                <td className="dw-cell"><input className="dw-cell-in" placeholder={f.target || "—"} value={mapDraft[f.name] ?? (f.target || "")} onChange={(e) => setMapDraft({ ...mapDraft, [f.name]: e.target.value })} /></td>
                <td className="dw-cell">{f.mapping_source === "user" ? "用户" : "自动"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ padding: "12px 14px" }}><button className="dw-btn primary" type="button" onClick={onSave}><Save size={15} />保存字段映射</button></div>
    </div>
  );
}

function HistoryPanel({ history }) {
  if (!history.length) return <div className="empty-copy" style={{ padding: 40 }}>暂无版本历史。保存修改后会在这里出现。</div>;
  return (
    <div className="dw-panel">
      <ul className="dw-hist">
        {history.map((h, i) => (
          <li key={i} className="dw-hist-row">
            <div className="dw-mod-av">{(h.user || "D").slice(0, 1)}</div>
            <div className="dw-mod-meta">
              <div className="dw-mod-top"><span className="dw-mod-mail">{h.email || h.user || "DataForge"}</span><span className="dw-mod-time">{fmtTime(h.at)}</span></div>
              <div className="dw-mod-desc">{h.change_summary || "—"}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
