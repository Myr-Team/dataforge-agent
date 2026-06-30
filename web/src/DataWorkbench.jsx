import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowUpDown,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Cloud,
  Database,
  FileSpreadsheet,
  FileText,
  FileUp,
  Filter,
  HardDrive,
  History,
  Info,
  LayoutGrid,
  List,
  Loader2,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Save,
  Search,
  Send,
  Settings2,
  Table2,
  Undo2,
  UploadCloud,
  X,
} from "lucide-react";
import {
  analyzeWorkspaceFiles,
  connectWorkspaceBlob,
  connectWorkspaceSql,
  createWorkspaceFile,
  loadConnectorCapabilities,
  loadWorkspaceFieldMapping,
  loadWorkspaceFileContent,
  loadWorkspaceFileHistory,
  loadWorkspaceFileQuality,
  loadWorkspaceFiles,
  saveWorkspaceFieldMapping,
  saveWorkspaceFileContent,
  saveWorkspaceTableCells,
} from "./api.js";

const TABS = [
  { id: "files", label: "文件库" },
  { id: "table", label: "表格编辑" },
  { id: "mapping", label: "字段映射" },
  { id: "quality", label: "数据质量" },
  { id: "connectors", label: "连接器" },
];

const CONNECTORS = [
  { id: "blob", name: "Azure Blob Storage", src: "/icons/azure-blob.svg", state: "available", hint: "对象存储，支持手填连接串真连接" },
  { id: "sql", name: "SQL Database", src: "/icons/sql-database.svg", state: "available", hint: "数据库，只读预览与导入" },
  { id: "adl", name: "Azure Data Lake", src: "/icons/data-lake.svg", state: "planned", hint: "保持 Demo，暂不接真连" },
  { id: "upload", name: "CSV / Excel Upload", icon: FileUp, state: "available", hint: "复用当前工作区上传入口" },
];

const TABLE_TYPES = new Set(["csv", "xlsx", "xlsm", "excel"]);
const MARKDOWN_TYPES = new Set(["md", "markdown", "txt", "text"]);
const PREVIEW_LIMIT = 100;

function workspaceIdFrom(workspaceId, dashboard) {
  return workspaceId || dashboard?.workspace_id || dashboard?.workspace?.workspace_id || "demo-corpus";
}

function flattenGroups(groups) {
  return (groups || []).flatMap((group) => (group.files || []).map((file) => ({ ...file, group: group.label })));
}

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 * 1024 * 1024) return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

function fileIcon(file) {
  const type = String(file?.type || file || "").toLowerCase();
  if (MARKDOWN_TYPES.has(type) || String(file?.name || file || "").endsWith(".md")) return <FileText size={15} className="fi fi-md" />;
  return <FileSpreadsheet size={15} className={type.includes("xlsx") ? "fi fi-xlsx" : "fi fi-csv"} />;
}

function columnLetter(index) {
  let n = index + 1;
  let out = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

function statusLabel(status) {
  return status === "indexed" ? "已入库" : "待复核";
}

function qualityStatusLabel(status) {
  if (status === "passed") return "通过";
  if (status === "failed") return "失败";
  return "提醒";
}

export function DataWorkbench({ dashboard, workspaceId: explicitWorkspaceId, onRun, onUploadAppend, onAnalysisResult }) {
  const workspaceId = workspaceIdFrom(explicitWorkspaceId, dashboard);
  const [tab, setTab] = useState("files");
  const [filePayload, setFilePayload] = useState(null);
  const [filesLoading, setFilesLoading] = useState(false);
  const [activeFileId, setActiveFileId] = useState("");
  const [openTabs, setOpenTabs] = useState([]);
  const [content, setContent] = useState(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  const [tableRows, setTableRows] = useState([]);
  const [markdownText, setMarkdownText] = useState("");
  const [edits, setEdits] = useState({});
  const [cell, setCell] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const [quality, setQuality] = useState(null);
  const [mapping, setMapping] = useState(null);
  const [mappingDraft, setMappingDraft] = useState({});
  const [history, setHistory] = useState([]);
  const [query, setQuery] = useState("");
  const [connectorState, setConnectorState] = useState({});
  const [connectorBusy, setConnectorBusy] = useState("");
  const [analyzing, setAnalyzing] = useState(false);

  const files = useMemo(() => flattenGroups(filePayload?.groups), [filePayload]);
  const activeFile = useMemo(() => files.find((file) => file.id === activeFileId) || null, [files, activeFileId]);
  const isMarkdown = content?.kind === "markdown" || MARKDOWN_TYPES.has(String(activeFile?.type || "").toLowerCase());
  const isTable = content?.kind === "table" || TABLE_TYPES.has(String(activeFile?.type || "").toLowerCase());
  const totalPages = Math.max(1, Math.ceil(Number(content?.total_rows || 0) / PREVIEW_LIMIT));
  const currentPage = Math.floor(offset / PREVIEW_LIMIT) + 1;
  const filteredGroups = useMemo(() => {
    const kw = query.trim().toLowerCase();
    return (filePayload?.groups || []).map((group) => ({
      ...group,
      files: kw
        ? (group.files || []).filter((file) => `${file.name || ""} ${file.type || ""}`.toLowerCase().includes(kw))
        : group.files || [],
    }));
  }, [filePayload, query]);

  const showToast = useCallback((msg) => {
    setToast(msg);
    window.clearTimeout(showToast._t);
    showToast._t = window.setTimeout(() => setToast(""), 2600);
  }, []);

  const refreshFiles = useCallback(async (preferredFileId = "") => {
    setFilesLoading(true);
    try {
      const data = await loadWorkspaceFiles(workspaceId);
      setFilePayload(data);
      const nextFiles = flattenGroups(data.groups);
      const nextActive = nextFiles.find((file) => file.id === preferredFileId) || nextFiles[0] || null;
      setActiveFileId(nextActive?.id || "");
      setOpenTabs((tabs) => {
        const kept = tabs.filter((id) => nextFiles.some((file) => file.id === id));
        return nextActive && !kept.includes(nextActive.id) ? [...kept, nextActive.id] : kept;
      });
    } catch (error) {
      showToast(`文件库加载失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setFilesLoading(false);
    }
  }, [showToast, workspaceId]);

  useEffect(() => {
    refreshFiles("");
    loadConnectorCapabilities(workspaceId)
      .then((data) => setConnectorState((state) => ({ ...state, capabilities: data.connectors || [] })))
      .catch(() => {});
  }, [refreshFiles, workspaceId]);

  useEffect(() => {
    if (!activeFileId) {
      setContent(null);
      setQuality(null);
      setMapping(null);
      setHistory([]);
      return;
    }
    let cancelled = false;
    setContentLoading(true);
    setDirty(false);
    setEdits({});
    setCell("");
    Promise.all([
      loadWorkspaceFileContent(workspaceId, activeFileId, { limit: PREVIEW_LIMIT, offset }),
      loadWorkspaceFileQuality(workspaceId, activeFileId).catch((error) => ({ error: error.message })),
      loadWorkspaceFieldMapping(workspaceId, activeFileId).catch((error) => ({ error: error.message })),
      loadWorkspaceFileHistory(workspaceId, activeFileId).catch(() => []),
    ]).then(([nextContent, nextQuality, nextMapping, nextHistory]) => {
      if (cancelled) return;
      setContent(nextContent);
      setTableRows((nextContent.rows || []).map((row) => [...row]));
      setMarkdownText(nextContent.text || "");
      setQuality(nextQuality);
      setMapping(nextMapping);
      setHistory(Array.isArray(nextHistory) ? nextHistory : []);
      const draft = {};
      for (const field of nextMapping?.field_mapping?.fields || []) {
        draft[field.name] = field.target || field.standard_name || "";
      }
      for (const [name, value] of Object.entries(nextMapping?.overrides || {})) {
        draft[name] = typeof value === "string" ? value : value?.target || "";
      }
      setMappingDraft(draft);
    }).catch((error) => {
      if (!cancelled) showToast(`内容加载失败：${error instanceof Error ? error.message : String(error)}`);
    }).finally(() => {
      if (!cancelled) setContentLoading(false);
    });
    return () => { cancelled = true; };
  }, [activeFileId, offset, showToast, workspaceId]);

  const openFile = (file) => {
    if (!file?.id) return;
    setActiveFileId(file.id);
    setOpenTabs((tabs) => (tabs.includes(file.id) ? tabs : [...tabs, file.id]));
    setOffset(0);
    setTab(MARKDOWN_TYPES.has(String(file.type || "").toLowerCase()) ? "table" : "table");
  };

  const closeTab = (fileId, event) => {
    event.stopPropagation();
    setOpenTabs((tabs) => {
      const next = tabs.filter((id) => id !== fileId);
      if (fileId === activeFileId) setActiveFileId(next[next.length - 1] || files[0]?.id || "");
      return next;
    });
  };

  const updateCell = (rowIndex, colIndex, value) => {
    const absoluteRow = Number(content?.offset || 0) + rowIndex;
    const columnName = content?.columns?.[colIndex]?.name || colIndex;
    setTableRows((rows) => rows.map((row, r) => (r === rowIndex ? row.map((cellValue, c) => (c === colIndex ? value : cellValue)) : row)));
    setEdits((items) => ({ ...items, [`${absoluteRow}:${colIndex}`]: { row: absoluteRow, col: columnName, value } }));
    setDirty(true);
  };

  const saveChanges = async () => {
    if (!activeFile || !dirty || saving) return;
    setSaving(true);
    try {
      if (isMarkdown) {
        await saveWorkspaceFileContent(workspaceId, activeFile.id, markdownText);
      } else if (isTable) {
        const changed = Object.values(edits);
        if (changed.length) await saveWorkspaceTableCells(workspaceId, activeFile.id, changed);
      }
      setDirty(false);
      setEdits({});
      showToast("已保存到后端");
      await refreshFiles(activeFile.id);
      const fresh = await loadWorkspaceFileContent(workspaceId, activeFile.id, { limit: PREVIEW_LIMIT, offset });
      setContent(fresh);
      setTableRows((fresh.rows || []).map((row) => [...row]));
      setMarkdownText(fresh.text || "");
      const [nextQuality, nextMapping, nextHistory] = await Promise.all([
        loadWorkspaceFileQuality(workspaceId, activeFile.id).catch((error) => ({ error: error.message })),
        loadWorkspaceFieldMapping(workspaceId, activeFile.id).catch((error) => ({ error: error.message })),
        loadWorkspaceFileHistory(workspaceId, activeFile.id).catch(() => []),
      ]);
      setQuality(nextQuality);
      setMapping(nextMapping);
      setHistory(Array.isArray(nextHistory) ? nextHistory : []);
    } catch (error) {
      showToast(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const createMarkdown = async () => {
    const name = window.prompt("Markdown 文件名", "untitled.md");
    if (!name) return;
    try {
      const result = await createWorkspaceFile(workspaceId, { name, type: "md", text: "# Untitled\n\n" });
      await refreshFiles(result.file?.id);
      showToast("Markdown 已创建并入库");
    } catch (error) {
      showToast(`新建失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const createTable = async () => {
    const name = window.prompt("表格文件名", "untitled.csv");
    if (!name) return;
    try {
      const result = await createWorkspaceFile(workspaceId, {
        name,
        kind: "table",
        columns: ["column_1", "column_2"],
        rows: [["", ""]],
      });
      await refreshFiles(result.file?.id);
      showToast("表格已创建并入库");
    } catch (error) {
      showToast(`新建失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const saveMapping = async () => {
    if (!activeFile) return;
    const fields = mapping?.field_mapping?.fields || [];
    try {
      const payload = fields.map((field) => ({ source: field.name, target: mappingDraft[field.name] || "" })).filter((item) => item.target);
      const next = await saveWorkspaceFieldMapping(workspaceId, activeFile.id, payload);
      setMapping(next);
      const nextQuality = await loadWorkspaceFileQuality(workspaceId, activeFile.id);
      setQuality(nextQuality);
      showToast("字段映射已保存");
    } catch (error) {
      showToast(`映射保存失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const sendToAnalysis = async () => {
    if (!activeFile || analyzing) return;
    setAnalyzing(true);
    try {
      const result = await analyzeWorkspaceFiles(workspaceId, {
        file_ids: [activeFile.id],
        message: `请基于数据工作台选中的文件 ${activeFile.name} 发起一次数据商机化分析。`,
      });
      showToast("已发送到分析");
      onAnalysisResult?.(result);
      if (!onAnalysisResult && onRun) onRun(`请分析数据工作台文件 ${activeFile.name}`);
    } catch (error) {
      showToast(`发送失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const connectExternal = async (connector) => {
    if (connector.id === "upload") {
      onUploadAppend?.();
      return;
    }
    if (connector.id === "adl") return;
    setConnectorBusy(connector.id);
    try {
      if (connector.id === "blob") {
        const connectionString = window.prompt("Azure Blob connection string（仅发送后端，不回显）", "");
        if (!connectionString) return;
        await connectWorkspaceBlob(workspaceId, { connection_string: connectionString });
      } else if (connector.id === "sql") {
        const server = window.prompt("SQL server", "");
        const database = server ? window.prompt("Database", "") : "";
        const username = database ? window.prompt("Username", "") : "";
        const password = username ? window.prompt("Password（仅发送后端，不回显）", "") : "";
        if (!server || !database || !username) return;
        await connectWorkspaceSql(workspaceId, { server, database, username, password });
      }
      setConnectorState((state) => ({ ...state, [connector.id]: "connected" }));
      showToast(`${connector.name} 已连接`);
    } catch (error) {
      showToast(`连接失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setConnectorBusy("");
    }
  };

  const storageUsed = Number(filePayload?.storage?.used_bytes || 0);
  const storageTotal = Number(filePayload?.storage?.total_bytes || 0);
  const storagePct = storageTotal ? Math.min(100, Math.max(1, (storageUsed / storageTotal) * 100)) : 0;
  const mappingStats = quality?.field_mapping || {};
  const q = quality?.quality || {};
  const validation = quality?.validation || {};
  const fields = mapping?.field_mapping?.fields || quality?.field_mapping?.fields || [];

  return (
    <main className="agent-studio data-stage">
      {toast ? <div className="dw-toast">{toast}</div> : null}

      <header className="dw-head">
        <div>
          <div className="dw-title"><h1>数据工作台</h1><Info size={16} className="dw-info" /></div>
          <p>在工作区内上传、创建、预览和轻量编辑数据，支持 Markdown、CSV、Excel 文件；外部 Blob / SQL 支持手填配置真连接。</p>
        </div>
        <div className="dw-save">
          {dirty ? <span className="dw-save-state dirty"><span className="dot" />有未保存更改</span> : <span className="dw-save-state ok"><Check size={14} />已保存</span>}
          <span className="dw-save-sub">{dirty ? "保存后会写入新版本并刷新质量统计" : "所有更改已保存"}</span>
        </div>
      </header>

      <div className="dw-actions">
        <div className="dw-actions-l">
          <button className="dw-btn primary" type="button" onClick={() => onUploadAppend?.()}><UploadCloud size={15} />上传文件</button>
          <button className="dw-btn" type="button" onClick={createMarkdown}><FileText size={15} />新建 Markdown</button>
          <button className="dw-btn" type="button" onClick={createTable}><Table2 size={15} />新建表格</button>
          <button className="dw-btn" type="button" disabled={!dirty || saving} onClick={saveChanges}>{saving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}保存更改</button>
          <button className="dw-btn ghost-blue" type="button" disabled={!activeFile || analyzing} onClick={sendToAnalysis}>{analyzing ? <Loader2 size={15} className="spin" /> : <Send size={15} />}发送到分析</button>
        </div>
        <div className="dw-actions-r">
          <div className="dw-search"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索文件或字段..." /></div>
          <div className="dw-iconset">
            <button className="dw-ic active" type="button" title="列表视图"><List size={16} /></button>
            <button className="dw-ic" type="button" title="网格视图"><LayoutGrid size={16} /></button>
            <button className="dw-ic" type="button" title="筛选"><Filter size={16} /></button>
          </div>
        </div>
      </div>

      <nav className="dw-tabs">
        {TABS.map((item) => (
          <button key={item.id} type="button" className={tab === item.id ? "dw-tab active" : "dw-tab"} onClick={() => setTab(item.id)}>{item.label}</button>
        ))}
      </nav>

      <div className="dw-body">
        <aside className="card dw-tree">
          <div className="dw-tree-head">
            <span className="t">文件库</span>
            <div className="dw-tree-acts">
              <button type="button" title="新建 Markdown" onClick={createMarkdown}><Plus size={15} /></button>
              <button type="button" title="刷新" onClick={() => refreshFiles(activeFileId)}>{filesLoading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}</button>
            </div>
          </div>
          <div className="dw-tree-body">
            {filteredGroups.map((group) => (
              <div className="dw-group" key={group.label}>
                <div className="dw-group-head"><ChevronDown size={13} />{group.label}</div>
                {(group.files || []).map((file) => (
                  <button key={file.id} type="button" className={activeFileId === file.id ? "dw-file active" : "dw-file"} onClick={() => openFile(file)}>
                    {fileIcon(file)}
                    <span>{file.name}</span>
                    <em className="dw-file-meta">{file.records ?? file.record_count ?? "-"} 行 / {file.fields ?? file.field_count ?? "-"} 字段</em>
                  </button>
                ))}
              </div>
            ))}
            {!files.length && !filesLoading ? <div className="dw-empty">当前工作区还没有文件。</div> : null}
          </div>
          <div className="dw-tree-foot">
            <div className="dw-store"><span>{formatBytes(storageUsed)} / {formatBytes(storageTotal)} 已使用</span><div className="dw-store-bar"><i style={{ width: `${storagePct}%` }} /></div></div>
          </div>
        </aside>

        <section className="card dw-editor">
          <div className="dw-ftabs">
            {openTabs.map((id) => {
              const file = files.find((item) => item.id === id);
              if (!file) return null;
              return (
                <div key={id} className={activeFileId === id ? "dw-ftab active" : "dw-ftab"} onClick={() => openFile(file)}>
                  {fileIcon(file)}<span>{file.name}</span><button type="button" className="dw-ftab-x" onClick={(event) => closeTab(id, event)}><X size={12} /></button>
                </div>
              );
            })}
            <button type="button" className="dw-ftab-add" onClick={createMarkdown}><Plus size={14} /></button>
          </div>

          {contentLoading ? (
            <div className="dw-loading"><Loader2 className="spin" size={18} />加载文件内容...</div>
          ) : !activeFile ? (
            <div className="dw-loading">请选择一个文件。</div>
          ) : tab === "mapping" ? (
            <div className="dw-panel-view">
              <div className="dw-panel-head">
                <div>
                  <div className="dw-panel-title"><Settings2 size={16} />字段映射</div>
                  <p className="dw-panel-sub">{activeFile.name} · {mappingStats.mapped ?? 0}/{mappingStats.total ?? 0} 已映射</p>
                </div>
                <button type="button" className="dw-btn ghost-blue" onClick={saveMapping}>保存映射</button>
              </div>
              <div className="dw-map-editor">
                {fields.map((field, index) => (
                  <label className="dw-map-row" key={field.name || index}>
                    <span>{field.name}</span>
                    <input value={mappingDraft[field.name] || ""} onChange={(e) => setMappingDraft((draft) => ({ ...draft, [field.name]: e.target.value }))} placeholder="目标字段名" />
                  </label>
                ))}
                {!fields.length ? <p className="empty-copy">没有可映射字段。</p> : null}
              </div>
            </div>
          ) : tab === "quality" ? (
            <div className="dw-panel-view">
              <div className="dw-panel-head">
                <div>
                  <div className="dw-panel-title"><AlertTriangle size={16} />数据质量</div>
                  <p className="dw-panel-sub">{activeFile.name} · {validation.checked_at ? new Date(validation.checked_at).toLocaleString() : "尚未校验"}</p>
                </div>
                <span className={`dw-chip ${validation.status === "passed" ? "ok" : ""}`}>{qualityStatusLabel(validation.status)}</span>
              </div>
              {quality?.error ? <p className="dw-status-error">{quality.error}</p> : (
                <>
                  <div className="dw-quality-grid">
                    <div className="dw-quality-metric"><span>缺失值</span><b>{q.missing_pct ?? 0}%</b></div>
                    <div className="dw-quality-metric"><span>重复值</span><b>{q.duplicate_pct ?? 0}%</b></div>
                    <div className="dw-quality-metric"><span>异常值</span><b>{q.outlier_count ?? 0}</b></div>
                    <div className="dw-quality-metric"><span>类型警告</span><b>{q.type_warnings ?? 0}</b></div>
                  </div>
                  <table className="dw-field-quality">
                    <thead><tr><th>字段</th><th>类型</th><th>映射状态</th><th>质量状态</th></tr></thead>
                    <tbody>
                      {fields.map((field, index) => (
                        <tr key={field.name || index}>
                          <td>{field.name}</td>
                          <td>{field.type || "-"}</td>
                          <td>{field.mapped ? "已映射" : "待映射"}</td>
                          <td>{field.warning || field.status || "通过"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!fields.length ? <p className="empty-copy">没有可展示的字段质量结果。</p> : null}
                </>
              )}
            </div>
          ) : isMarkdown ? (
            <div className="dw-md">
              <div className="dw-md-bar">
                <span className="dw-md-name"><FileText size={14} />{activeFile.name}</span>
                <div className="dw-md-bar-r">
                  <button type="button" className="dw-mini active">编辑</button>
                  <span className="dw-md-time">最近修改 {activeFile.updated_at ? new Date(activeFile.updated_at).toLocaleString() : "-"}</span>
                </div>
              </div>
              <textarea className="dw-md-area" value={markdownText} onChange={(e) => { setMarkdownText(e.target.value); setDirty(true); }} />
            </div>
          ) : (
            <>
              <div className="dw-toolbar">
                <button type="button" title="撤销"><Undo2 size={15} /></button>
                <button type="button" title="重做"><Undo2 size={15} /></button>
                <span className="dw-tb-sep" />
                <button type="button"><Filter size={14} />筛选</button>
                <button type="button"><ArrowUpDown size={14} />排序</button>
                <button type="button"><Table2 size={14} />冻结首行</button>
                <button type="button"><Settings2 size={14} />列配置</button>
                <button type="button" className="dw-tb-more"><MoreHorizontal size={15} />更多<ChevronDown size={13} /></button>
              </div>
              <div className="dw-grid-wrap">
                <table className="dw-grid">
                  <thead>
                    <tr><th className="dw-rownum" />{(content?.columns || []).map((_, index) => <th key={index}>{columnLetter(index)}</th>)}</tr>
                  </thead>
                  <tbody>
                    <tr className="dw-fieldrow">
                      <td className="dw-rownum">1</td>
                      {(content?.columns || []).map((col, index) => <td key={index} className="dw-fieldname">{col.name}</td>)}
                    </tr>
                    {tableRows.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        <td className="dw-rownum">{Number(content?.offset || 0) + rowIndex + 2}</td>
                        {(content?.columns || []).map((col, colIndex) => {
                          const id = `${columnLetter(colIndex)}${Number(content?.offset || 0) + rowIndex + 2}`;
                          return (
                            <td key={`${rowIndex}-${col.name || colIndex}`} className={id === cell ? "dw-cell sel" : "dw-cell"} onClick={() => setCell(id)}>
                              <input
                                className="dw-cell-input"
                                value={row[colIndex] ?? ""}
                                onFocus={() => setCell(id)}
                                onChange={(event) => updateCell(rowIndex, colIndex, event.target.value)}
                              />
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="dw-grid-foot">
                <span>共 {content?.total_rows ?? 0} 行，{content?.total_cols ?? 0} 列</span>
                <span className="dw-rows-sel">显示 {PREVIEW_LIMIT} 行<ChevronDown size={12} /></span>
                <div className="dw-pager">
                  <button type="button" disabled={offset === 0} onClick={() => setOffset(0)}><ChevronsLeft size={14} /></button>
                  <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PREVIEW_LIMIT))}><ChevronLeft size={14} /></button>
                  <span className="dw-page">{currentPage}</span><span className="dw-page-of">/ {totalPages}</span>
                  <button type="button" disabled={currentPage >= totalPages} onClick={() => setOffset(offset + PREVIEW_LIMIT)}><ChevronRight size={14} /></button>
                  <button type="button" disabled={currentPage >= totalPages} onClick={() => setOffset((totalPages - 1) * PREVIEW_LIMIT)}><ChevronsRight size={14} /></button>
                </div>
              </div>
            </>
          )}
        </section>

        <aside className="card dw-status">
          <div className="dw-status-head"><span className="t">数据状态</span><ChevronDown size={15} /></div>

          <div className="dw-sec">
            <div className="dw-sec-row"><span className="dw-sec-t">字段映射</span><span className="dw-sec-v">{mappingStats.mapped ?? 0} / {mappingStats.total ?? 0} <b className="ok">{mappingStats.pct ?? 0}%</b></span></div>
            <div className="dw-prog"><i style={{ width: `${Math.min(100, Number(mappingStats.pct || 0))}%` }} /></div>
            <button type="button" className="dw-link-btn" onClick={() => setTab("mapping")}>查看映射详情</button>
          </div>

          <div className="dw-sec">
            <div className="dw-sec-t">数据质量</div>
            {quality?.error ? <p className="dw-status-error">{quality.error}</p> : (
              <ul className="dw-qlist">
                <li><span>缺失值</span><span className="qv">{q.missing_pct ?? 0}% <Check size={14} className="ok" /></span></li>
                <li><span>重复值</span><span className="qv">{q.duplicate_pct ?? 0}% <Check size={14} className="ok" /></span></li>
                <li><span>异常值</span><span className="qv">{q.outlier_count ?? 0} <AlertTriangle size={14} className={(q.outlier_count || 0) ? "warn" : "ok"} /></span></li>
                <li><span>类型警告</span><span className="qv">{q.type_warnings ?? 0} <Check size={14} className="ok" /></span></li>
              </ul>
            )}
          </div>

          {tab === "mapping" ? (
            <div className="dw-sec">
              <div className="dw-sec-row"><span className="dw-sec-t">映射编辑</span><button type="button" className="dw-link-btn" onClick={saveMapping}>保存映射</button></div>
              <div className="dw-map-list">
                {fields.map((field) => (
                  <label className="dw-map-row" key={field.name}>
                    <span>{field.name}</span>
                    <input value={mappingDraft[field.name] || ""} onChange={(e) => setMappingDraft((draft) => ({ ...draft, [field.name]: e.target.value }))} placeholder="目标字段名" />
                  </label>
                ))}
                {!fields.length ? <p className="empty-copy">没有可映射字段。</p> : null}
              </div>
            </div>
          ) : null}

          <div className="dw-sec">
            <div className="dw-sec-row"><span className="dw-sec-t">校验结果</span><span className={`dw-chip ${validation.status === "passed" ? "ok" : ""}`}>{qualityStatusLabel(validation.status)}</span></div>
            <div className="dw-sec-sub">{validation.checked_at ? new Date(validation.checked_at).toLocaleString() : "-"} 校验完成</div>
          </div>

          <div className="dw-sec">
            <div className="dw-sec-t">最近修改</div>
            <div className="dw-history-list">
              {history.slice(0, 3).map((item, index) => (
                <div className="dw-mod" key={`${item.at}-${index}`}>
                  <div className="dw-mod-av">{String(item.user || "D").slice(0, 1).toUpperCase()}</div>
                  <div className="dw-mod-meta">
                    <div className="dw-mod-top"><span className="dw-mod-mail">{item.email || item.user || "DataForge"}</span><span className="dw-mod-time">{item.at ? new Date(item.at).toLocaleString() : "-"}</span></div>
                    <div className="dw-mod-desc">{item.change_summary || "文件版本更新"}</div>
                  </div>
                </div>
              ))}
            </div>
            <button type="button" className="dw-link-btn" onClick={() => setTab("quality")}><History size={13} />查看版本历史</button>
          </div>
        </aside>
      </div>

      <section className="card dw-connectors">
        <div className="dw-conn-head">
          <h2>外部数据接入</h2>
          <p>当前支持 Azure Blob Storage 和 SQL Database 手填配置真连接；Azure Data Lake 保持 Demo 入口。</p>
        </div>
        <div className="dw-conn-grid">
          {CONNECTORS.map((connector) => {
            const Icon = connector.icon;
            const planned = connector.state === "planned";
            const busy = connectorBusy === connector.id;
            const connected = connectorState[connector.id] === "connected";
            return (
              <div className="dw-conn-card" key={connector.id}>
                <div className="dw-conn-top">
                  <div className="dw-conn-ic">{connector.src ? <img src={connector.src} width="22" height="22" alt="" /> : <Icon size={20} />}</div>
                  {planned ? <span className="dw-badge planned">计划上线</span> : <span className="dw-badge ok">{connected ? "已连接" : "可用"}</span>}
                </div>
                <div className="dw-conn-name">{connector.name}</div>
                <div className="dw-conn-status">{connector.hint}</div>
                <button type="button" className={planned ? "dw-conn-btn" : "dw-conn-btn primary"} disabled={planned || busy} onClick={() => connectExternal(connector)}>
                  {busy ? <Loader2 size={14} className="spin" /> : null}
                  {planned ? "敬请期待" : connector.id === "upload" ? "上传文件" : connected ? "重新连接" : "连接"}
                </button>
              </div>
            );
          })}
        </div>
      </section>
    </main>
  );
}
