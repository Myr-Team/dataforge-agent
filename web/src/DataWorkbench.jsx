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
  Trash2,
  Rows3,
  Columns3,
  Database,
  Cloud,
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
  dwDeleteFile,
  dwBlobConnect,
  dwBlobStatus,
  dwBlobContainers,
  dwBlobItems,
  dwBlobPreview,
  dwBlobImport,
  dwSqlConnect,
  dwSqlStatus,
  dwSqlTables,
  dwSqlPreview,
  dwSqlImport,
  dwDisconnectConnector,
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
  if (t === "sql" || t === "database") return <Database size={15} className="fi fi-sql" />;
  if (t === "blob") return <Cloud size={15} className="fi fi-blob" />;
  if (t === "md" || t === "markdown" || t === "txt") return <FileText size={15} className="fi fi-md" />;
  if (t === "xlsx") return <FileSpreadsheet size={15} className="fi fi-xlsx" />;
  return <FileSpreadsheet size={15} className="fi fi-csv" />;
}

function typeFromName(name = "", contentType = "") {
  const suffix = String(name || "").split(".").pop()?.toLowerCase() || "";
  if (["csv", "xlsx", "md", "markdown", "txt", "json"].includes(suffix)) return suffix === "markdown" ? "md" : suffix;
  const content = String(contentType || "").toLowerCase();
  if (content.includes("csv")) return "csv";
  if (content.includes("json")) return "json";
  if (content.includes("markdown") || content.includes("text/plain")) return "md";
  if (content.includes("spreadsheet") || content.includes("excel")) return "xlsx";
  return "blob";
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

function isPast(v) {
  if (!v) return false;
  const t = new Date(v).getTime();
  return Number.isNaN(t) ? false : t <= Date.now();
}

function isConnectorSessionError(error) {
  return /connector session not found or expired|session not found|expired/i.test(String(error?.message || error || ""));
}

function historyUser(item, currentUser) {
  const rawName = String(item?.user || "").trim();
  const rawEmail = String(item?.email || "").trim();
  const loginName = String(currentUser?.name || "").trim();
  const loginEmail = String(currentUser?.email || "").trim();
  const isPlaceholder = !rawEmail && (!rawName || rawName === "DataForge");
  const name = isPlaceholder ? (loginName || loginEmail || "DataForge") : (rawName || rawEmail || loginName || "DataForge");
  const email = isPlaceholder ? loginEmail : rawEmail;
  return {
    name,
    email,
    initial: String(name || email || "D").trim().slice(0, 1).toUpperCase(),
  };
}

export function DataWorkbench({ dashboard, onUpload, onOpenConversation, onRun, user }) {
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
  const [tableColumns, setTableColumns] = useState([]);
  const [mdText, setMdText] = useState("");
  const [edits, setEdits] = useState({}); // "rowIdx:colName" -> value (page-relative rows + offset)
  const [tableOps, setTableOps] = useState({});
  const [selectedCell, setSelectedCell] = useState(null);
  const [contextMenu, setContextMenu] = useState(null);
  const [colWidths, setColWidths] = useState({});
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
  const [createModal, setCreateModal] = useState(null);
  const [connectorModal, setConnectorModal] = useState(null);
  const [connectorBusy, setConnectorBusy] = useState(false);
  const [connectorResult, setConnectorResult] = useState(null);
  const [externalGroups, setExternalGroups] = useState([]);
  const [importingExternal, setImportingExternal] = useState(false);
  const [externalRestoredKey, setExternalRestoredKey] = useState("");
  const toastT = useRef(null);
  const externalStorageKey = workspaceId ? `df-dataworkbench-external:${workspaceId}` : "";

  const showToast = useCallback((msg) => {
    setToast(msg);
    window.clearTimeout(toastT.current);
    toastT.current = window.setTimeout(() => setToast(""), 2600);
  }, []);

  const clearConnectorState = useCallback((kind = null, message = "") => {
    const normalizedKind = kind ? String(kind).toLowerCase() : null;
    setExternalGroups((items) => normalizedKind ? items.filter((group) => group.externalKind !== normalizedKind) : []);
    setConnectorResult((current) => {
      if (!normalizedKind || current?.kind === normalizedKind) return null;
      return current;
    });
    setConnectorModal(null);
    setConnectorBusy(false);
    setImportingExternal(false);
    setExternalRestoredKey(externalStorageKey);
    if (!normalizedKind || active?.externalKind === normalizedKind) {
      setActive(null);
      setContent(null);
      setRows([]);
      setTableColumns([]);
      setMdText("");
      setQuality(null);
      setMapping(null);
      setHistory([]);
      setSelectedCell(null);
      setDirty(false);
      setContextMenu(null);
    }
    try {
      if (externalStorageKey) {
        if (!normalizedKind) {
          window.sessionStorage.removeItem(externalStorageKey);
        } else {
          const saved = JSON.parse(window.sessionStorage.getItem(externalStorageKey) || "{}");
          const nextGroups = (Array.isArray(saved.externalGroups) ? saved.externalGroups : [])
            .filter((group) => group.externalKind !== normalizedKind);
          const nextConnector = saved.connectorResult?.kind === normalizedKind ? null : saved.connectorResult;
          if (nextGroups.length || nextConnector) {
            window.sessionStorage.setItem(externalStorageKey, JSON.stringify({ externalGroups: nextGroups, connectorResult: nextConnector }));
          } else {
            window.sessionStorage.removeItem(externalStorageKey);
          }
        }
      }
    } catch {
      // Ignore browser storage failures; UI state is already cleared.
    }
    if (message) showToast(message);
  }, [active, externalStorageKey, showToast]);

  const disconnectConnector = useCallback(async (kind = connectorResult?.kind, connectionId = connectorResult?.connection_id) => {
    const normalizedKind = kind ? String(kind).toLowerCase() : "";
    if (!normalizedKind) {
      clearConnectorState(null, "外部连接已断开。");
      return;
    }
    try {
      if (connectionId) await dwDisconnectConnector(workspaceId, { kind: normalizedKind, connection_id: connectionId });
    } catch {
      // The backend session may already be gone after refresh/redeploy; clearing local state is still correct.
    }
    clearConnectorState(normalizedKind, "外部连接已断开。");
  }, [clearConnectorState, connectorResult, workspaceId]);

  useEffect(() => {
    if (!contextMenu) return undefined;
    const close = () => setContextMenu(null);
    const onKeyDown = (event) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [contextMenu]);

  useEffect(() => {
    if (!externalStorageKey) {
      setExternalGroups([]);
      setConnectorResult(null);
      setExternalRestoredKey("");
      return;
    }
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(externalStorageKey) || "{}");
      const restoredConnector = saved.connectorResult || null;
      if (!restoredConnector?.kind || !restoredConnector?.connection_id || isPast(restoredConnector.expires_at)) {
        setExternalGroups([]);
        setConnectorResult(null);
        if (restoredConnector?.connection_id) showToast("外部连接会话已过期，请重新连接。");
      } else {
        setExternalGroups(Array.isArray(saved.externalGroups) ? saved.externalGroups : []);
        setConnectorResult(restoredConnector);
      }
    } catch {
      setExternalGroups([]);
      setConnectorResult(null);
    }
    setExternalRestoredKey(externalStorageKey);
  }, [externalStorageKey, showToast]);

  useEffect(() => {
    if (!externalStorageKey || externalRestoredKey !== externalStorageKey) return;
    try {
      if (!externalGroups.length && !connectorResult) {
        window.sessionStorage.removeItem(externalStorageKey);
        return;
      }
      window.sessionStorage.setItem(
        externalStorageKey,
        JSON.stringify({
          externalGroups,
          connectorResult: connectorResult ? {
            kind: connectorResult.kind,
            connection_id: connectorResult.connection_id,
            status: connectorResult.status,
            database: connectorResult.database,
            expires_at: connectorResult.expires_at,
            connected_at: connectorResult.connected_at,
          } : null,
        }),
      );
    } catch {
      // Session-only convenience; never block the workbench on storage quota.
    }
  }, [externalGroups, connectorResult, externalStorageKey, externalRestoredKey]);

  useEffect(() => {
    if (!workspaceId || !connectorResult?.kind || !connectorResult?.connection_id) return undefined;
    if (externalRestoredKey !== externalStorageKey) return undefined;
    if (isPast(connectorResult.expires_at)) {
      clearConnectorState(connectorResult.kind, "外部连接会话已过期，请重新连接。");
      return undefined;
    }
    let cancelled = false;
    const validate = connectorResult.kind === "blob" ? dwBlobStatus : dwSqlStatus;
    validate(workspaceId, connectorResult.connection_id)
      .then((status) => {
        if (cancelled) return;
        if (status?.expires_at && status.expires_at !== connectorResult.expires_at) {
          setConnectorResult((current) => (
            current?.connection_id === connectorResult.connection_id ? { ...current, expires_at: status.expires_at } : current
          ));
        }
      })
      .catch(() => {
        if (!cancelled) clearConnectorState(connectorResult.kind, "外部连接会话已失效，请重新连接。");
      });
    return () => { cancelled = true; };
  }, [
    workspaceId,
    externalStorageKey,
    externalRestoredKey,
    connectorResult?.kind,
    connectorResult?.connection_id,
    connectorResult?.expires_at,
    clearConnectorState,
  ]);

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
  const isJson = active && String(active.type || "").toLowerCase() === "json";
  const isTextPreview = isMd || isJson || content?.kind === "markdown" || content?.kind === "json";
  const isExternal = Boolean(active?.external);

  const loadContent = useCallback(async (file, off = 0) => {
    setContentLoading(true);
    setEdits({});
    setTableOps({});
    setSelectedCell(null);
    setContextMenu(null);
    setDirty(false);
    try {
      const data = await dwFileContent(workspaceId, file.id, { limit: PAGE, offset: off });
      setContent(data);
      setOffset(off);
      if (data.kind === "markdown" || data.kind === "json") {
        setMdText(data.text || "");
        setRows([]);
        setTableColumns([]);
      } else {
        const nextColumns = (data.columns || []).map((c) => (typeof c === "string" ? c : c.name));
        setTableColumns(nextColumns);
        setRows((data.rows || []).map((r) => [...r]));
        setColWidths((old) => {
          const next = {};
          for (const col of nextColumns) next[col] = old[col] || 136;
          return next;
        });
      }
    } catch (e) {
      showToast(`加载内容失败：${e.message}`);
    } finally {
      setContentLoading(false);
    }
  }, [workspaceId, showToast]);

  const loadExternalContent = useCallback(async (file, off = 0) => {
    const source = file?.source || {};
    setContentLoading(true);
    setEdits({});
    setTableOps({});
    setSelectedCell(null);
    setContextMenu(null);
    setDirty(false);
    setQuality(null);
    setMapping(null);
    setHistory([]);
    setMapDraft({});
    try {
      const data = file.externalKind === "blob"
        ? await dwBlobPreview(workspaceId, source.connection_id, source.container, source.blob, { limit: PAGE, offset: off })
        : await dwSqlPreview(workspaceId, source.connection_id, source.table, PAGE);
      setContent(data);
      setOffset(file.externalKind === "blob" ? off : 0);
      if (data.kind === "markdown" || data.kind === "json") {
        setMdText(data.text || "");
        setRows([]);
        setTableColumns([]);
      } else {
        const nextColumns = (data.columns || []).map((c) => (typeof c === "string" ? c : c.name));
        setTableColumns(nextColumns);
        setRows((data.rows || []).map((r) => [...r]));
        setColWidths((old) => {
          const next = {};
          for (const col of nextColumns) next[col] = old[col] || 136;
          return next;
        });
      }
    } catch (e) {
      if (isConnectorSessionError(e)) {
        clearConnectorState(file.externalKind, "外部连接会话已失效，请重新连接。");
        return;
      }
      showToast(`外部数据预览失败：${e.message}`);
    } finally {
      setContentLoading(false);
    }
  }, [workspaceId, showToast, clearConnectorState]);

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
    if (file.external) {
      loadExternalContent(file, 0);
    } else {
      loadContent(file, 0);
      loadSidePanels(file);
    }
  }, [loadContent, loadExternalContent, loadSidePanels]);

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
  const columns = tableColumns;
  const colIndex = (name) => columns.indexOf(name);
  const hasTableOps = Object.values(tableOps).some((value) => Array.isArray(value) && value.length);

  const onMdChange = (v) => {
    setMdText(v);
    if (!isJson) setDirty(true);
  };

  const save = async () => {
    if (!active || !dirty || saving) return;
    setSaving(true);
    try {
      if (isMd) {
        await dwSaveContent(workspaceId, active.id, mdText);
      } else {
        const editList = hasTableOps
          ? rows.flatMap((row, rowIdx) => columns.map((col, colIdx) => ({ row: offset + rowIdx, col, value: row[colIdx] ?? "" })))
          : Object.entries(edits).map(([k, value]) => { const [row, col] = k.split(/:(.+)/); return { row: Number(row), col, value }; });
        const payload = { edits: editList, ...tableOps };
        if (editList.length || Object.keys(tableOps).length) await dwSaveCells(workspaceId, active.id, payload);
      }
      setDirty(false); setEdits({}); setTableOps({}); setContextMenu(null);
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
    setCreateModal({
      kind,
      name: kind === "md" ? "新建笔记" : "新建表格",
      columns: "列1, 列2",
      text: kind === "md" ? "# 新建笔记\n\n" : "",
    });
  };

  const submitCreateFile = async () => {
    if (!createModal?.name?.trim()) {
      showToast("请输入文件名");
      return;
    }
    try {
      const name = createModal.name.trim();
      const body = createModal.kind === "md"
        ? { name, type: "md", text: createModal.text || `# ${name}\n\n` }
        : { name, kind: "table", columns: createModal.columns.split(",").map((x) => x.trim()).filter(Boolean), rows: [["", ""]] };
      const res = await dwCreateFile(workspaceId, body);
      showToast("已创建并入库");
      setCreateModal(null);
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

  const selectedCol = selectedCell ? columns[selectedCell.col] : null;

  const uniqueColumnName = (base) => {
    let name = base || `column_${columns.length + 1}`;
    let n = 2;
    while (columns.includes(name)) {
      name = `${base}_${n}`;
      n += 1;
    }
    return name;
  };

  const insertRowAt = (pageRowIdx, where = "below") => {
    if (!columns.length) return;
    const insertAtPage = Math.max(0, Math.min(rows.length, pageRowIdx + (where === "below" ? 1 : 0)));
    const insertAtFile = offset + insertAtPage;
    const values = columns.map(() => "");
    setRows((rs) => [...rs.slice(0, insertAtPage), values, ...rs.slice(insertAtPage)]);
    setTableOps((ops) => ({ ...ops, add_rows: [...(ops.add_rows || []), { index: insertAtFile, values }] }));
    setSelectedCell({ row: insertAtPage, col: Math.max(0, selectedCell?.col || 0) });
    setContextMenu(null);
    setDirty(true);
  };

  const deleteRowAt = (pageRowIdx) => {
    if (pageRowIdx == null || pageRowIdx < 0 || pageRowIdx >= rows.length) {
      showToast("先选中要删除的行");
      return;
    }
    const absolute = offset + pageRowIdx;
    setRows((rs) => rs.filter((_, idx) => idx !== pageRowIdx));
    setTableOps((ops) => ({ ...ops, delete_rows: [...(ops.delete_rows || []), absolute] }));
    setEdits({});
    setSelectedCell(null);
    setContextMenu(null);
    setDirty(true);
  };

  const insertColumnAt = (colIdx, where = "right") => {
    const insertAt = Math.max(0, Math.min(columns.length, colIdx + (where === "right" ? 1 : 0)));
    const base = `column_${columns.length + 1}`;
    const name = uniqueColumnName(base);
    setTableColumns((cols) => [...cols.slice(0, insertAt), name, ...cols.slice(insertAt)]);
    setRows((rs) => rs.map((row) => [...row.slice(0, insertAt), "", ...row.slice(insertAt)]));
    setColWidths((m) => ({ ...m, [name]: 136 }));
    setTableOps((ops) => ({ ...ops, add_cols: [...(ops.add_cols || []), { index: insertAt, name, values: rows.map(() => "") }] }));
    setSelectedCell({ row: Math.max(0, selectedCell?.row || 0), col: insertAt });
    setContextMenu(null);
    setDirty(true);
  };

  const deleteColumnAt = (colIdx) => {
    const col = columns[colIdx];
    if (!col) {
      showToast("先选中要删除的列");
      return;
    }
    if (columns.length <= 1) {
      showToast("至少保留一列");
      return;
    }
    setTableColumns((cols) => cols.filter((_, idx) => idx !== colIdx));
    setRows((rs) => rs.map((row) => row.filter((_, idx) => idx !== colIdx)));
    setTableOps((ops) => ({ ...ops, delete_cols: [...(ops.delete_cols || []), col] }));
    setEdits({});
    setSelectedCell(null);
    setContextMenu(null);
    setDirty(true);
  };

  const addRow = () => {
    insertRowAt(selectedCell?.row ?? Math.max(0, rows.length - 1), "below");
  };

  const deleteRow = () => {
    if (!selectedCell) {
      showToast("先选中要删除的行");
      return;
    }
    deleteRowAt(selectedCell.row);
  };

  const addColumn = () => {
    insertColumnAt(selectedCell?.col ?? Math.max(0, columns.length - 1), "right");
  };

  const deleteColumn = () => {
    if (!selectedCol) {
      showToast("先选中要删除的列");
      return;
    }
    deleteColumnAt(columns.indexOf(selectedCol));
  };

  const openTableContextMenu = (event, row, col, target = "cell") => {
    if (isExternal) return;
    if (!columns.length) return;
    event.preventDefault();
    const safeRow = Math.max(0, Math.min(rows.length - 1, row ?? 0));
    const safeCol = Math.max(0, Math.min(columns.length - 1, col ?? 0));
    setSelectedCell({ row: safeRow, col: safeCol });
    const menuWidth = 220;
    const menuHeight = 248;
    setContextMenu({
      x: Math.min(event.clientX, window.innerWidth - menuWidth - 8),
      y: Math.min(event.clientY, window.innerHeight - menuHeight - 8),
      row: safeRow,
      col: safeCol,
      target,
    });
  };

  const resizeColumn = (name, startX) => {
    const start = colWidths[name] || 136;
    const onMove = (event) => {
      const width = Math.max(90, Math.min(320, start + event.clientX - startX));
      setColWidths((m) => ({ ...m, [name]: width }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const updateSelectedCell = (value) => {
    if (!selectedCell || !selectedCol) return;
    onCellChange(selectedCell.row, selectedCol, value);
  };

  const deleteActiveFile = async () => {
    if (!active) return;
    try {
      await dwDeleteFile(workspaceId, active.id);
      showToast("文件已删除");
      setOpenTabs((tabs) => tabs.filter((t) => t.id !== active.id));
      setActive(null);
      setContent(null);
      await reloadFiles();
    } catch (e) {
      showToast(`删除失败：${e.message}`);
    }
  };

  const sendToAnalysis = async () => {
    if (!active || analyzing) return;
    setAnalyzing(true);
    const message = `请基于数据工作台文件 ${active.name} 做一次可行性分析，说明证据强弱、机会、风险缺口和下一步验证计划。`;
    showToast("已发送到分析，正在打开会话…");
    try {
      if (onRun) {
        await onRun(message, {
          stayOnDashboard: false,
          artifactMode: "report",
          newConversation: true,
          uiContext: {
            entrypoint: "data_workbench",
            mode: "data_workbench_analysis",
            selected_file_ids: [active.id],
            selected_files: [{ id: active.id, name: active.name, type: active.type, status: active.status }],
          },
        });
      } else {
        const res = await dwAnalyzeFiles(workspaceId, [active.id], message);
        const cid = res?.conversation_id || res?.jump?.conversation_id;
        if (cid && onOpenConversation) onOpenConversation(cid);
      }
    } catch (e) {
      showToast(`发送分析失败：${e.message}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const upsertExternalGroups = useCallback((kind, nextGroups) => {
    setExternalGroups((current) => [
      ...current.filter((group) => group.externalKind !== kind),
      ...nextGroups,
    ]);
  }, []);

  const buildBlobExternalGroups = useCallback(async (connectionId, containers) => {
    const visibleContainers = (containers || []).slice(0, 12);
    return Promise.all(visibleContainers.map(async (container) => {
      const name = container.name || container;
      try {
        const data = await dwBlobItems(workspaceId, connectionId, name, "", 100);
        return {
          label: `Blob · ${name}`,
          external: true,
          externalKind: "blob",
          files: (data.blobs || []).map((item) => ({
            id: `external:blob:${connectionId}:${name}:${item.name}`,
            name: item.name,
            type: typeFromName(item.name, item.content_type),
            bytes: item.bytes,
            updated_at: item.updated_at,
            status: "external",
            external: true,
            externalKind: "blob",
            source: {
              connection_id: connectionId,
              container: name,
              blob: item.name,
            },
          })),
        };
      } catch {
        return { label: `Blob · ${name}`, external: true, externalKind: "blob", files: [] };
      }
    }));
  }, [workspaceId]);

  const buildSqlExternalGroup = useCallback((connectionId, tables, database) => ({
    label: `SQL · ${database || "database"}`,
    external: true,
    externalKind: "sql",
    files: (tables || []).map((table) => {
      const id = table.id || `${table.schema}.${table.name}`;
      return {
        id: `external:sql:${connectionId}:${id}`,
        name: id,
        type: "sql",
        status: "external",
        external: true,
        externalKind: "sql",
        source: {
          connection_id: connectionId,
          table: id,
          database,
        },
      };
    }),
  }), []);

  const findImportedFile = (data, res, source) => {
    const files = (data?.groups || []).flatMap((group) => group.files || []);
    const doc = (res?.upload?.documents || [])[0] || {};
    const candidateIds = new Set([doc.id, doc.file_id, doc.document_id, res?.file?.id].filter(Boolean).map(String));
    const candidateNames = new Set([doc.name, doc.filename, res?.file?.name, source?.name].filter(Boolean).map(String));
    return files.find((file) => candidateIds.has(String(file.id))) ||
      files.find((file) => candidateNames.has(String(file.name))) ||
      null;
  };

  const importExternalSource = async (source = active) => {
    if (!source?.external || importingExternal) return;
    setImportingExternal(true);
    try {
      let res;
      if (source.externalKind === "blob") {
        res = await dwBlobImport(workspaceId, {
          connection_id: source.source.connection_id,
          container: source.source.container,
          blob: source.source.blob,
        });
      } else {
        res = await dwSqlImport(workspaceId, {
          connection_id: source.source.connection_id,
          table: source.source.table,
        });
      }
      showToast("已导入文件库");
      const data = await reloadFiles();
      const imported = findImportedFile(data, res, source);
      if (imported) openFile(imported);
    } catch (e) {
      if (isConnectorSessionError(e)) {
        clearConnectorState(source.externalKind, "外部连接会话已失效，请重新连接。");
        return;
      }
      showToast(`导入失败：${e.message}`);
    } finally {
      setImportingExternal(false);
    }
  };

  const submitConnector = async (event) => {
    event.preventDefault();
    if (!connectorModal || connectorBusy) return;
    setConnectorBusy(true);
    const form = new FormData(event.currentTarget);
    const value = (key) => String(form.get(key) || "").trim();
    try {
      if (connectorModal === "blob") {
        const connected = await dwBlobConnect(workspaceId, {
          connection_string: value("connection_string"),
          account: value("account"),
          sas: value("sas"),
        });
        const containers = connected.containers?.length ? connected.containers : (await dwBlobContainers(workspaceId, connected.connection_id)).containers;
        const nextGroups = await buildBlobExternalGroups(connected.connection_id, containers || []);
        upsertExternalGroups("blob", nextGroups);
        setConnectorResult({
          kind: "blob",
          connection_id: connected.connection_id,
          status: connected.status,
          expires_at: connected.expires_at,
          connected_at: new Date().toISOString(),
          containers: containers || [],
          groups: nextGroups,
        });
        setConnectorModal(null);
        const firstExternal = nextGroups.flatMap((group) => group.files || [])[0];
        if (firstExternal) openFile(firstExternal);
        showToast("Blob 已连接，凭证仅保存在服务端会话");
      } else if (connectorModal === "sql") {
        const connected = await dwSqlConnect(workspaceId, {
          server: value("server"),
          database: value("database"),
          username: value("username"),
          password: value("password"),
          connection_string: value("connection_string"),
        });
        const tables = connected.tables?.length ? connected.tables : (await dwSqlTables(workspaceId, connected.connection_id)).tables;
        const nextGroup = buildSqlExternalGroup(connected.connection_id, tables || [], value("database"));
        upsertExternalGroups("sql", [nextGroup]);
        setConnectorResult({
          kind: "sql",
          connection_id: connected.connection_id,
          status: connected.status,
          expires_at: connected.expires_at,
          connected_at: new Date().toISOString(),
          database: value("database"),
          tables: tables || [],
          groups: [nextGroup],
        });
        setConnectorModal(null);
        const firstExternal = (nextGroup.files || [])[0];
        if (firstExternal) openFile(firstExternal);
        showToast("SQL 已连接，凭证仅保存在服务端会话");
      }
    } catch (e) {
      showToast(`连接失败：${e.message}`);
    } finally {
      setConnectorBusy(false);
    }
  };

  const importConnectorItem = async (item) => {
    if (!connectorResult) return;
    setConnectorBusy(true);
    try {
      let res;
      if (connectorResult.kind === "blob") {
        res = await dwBlobImport(workspaceId, { connection_id: connectorResult.connection_id, container: item.container, blob: item.name });
      } else {
        res = await dwSqlImport(workspaceId, { connection_id: connectorResult.connection_id, table: item.id || `${item.schema}.${item.name}` });
      }
      showToast("已导入文件库");
      const data = await reloadFiles();
      const imported = (data?.groups || []).flatMap((g) => g.files || []).find((f) => f.id === res?.upload?.documents?.[0]?.id);
      if (imported) openFile(imported);
    } catch (e) {
      if (isConnectorSessionError(e)) {
        clearConnectorState(connectorResult.kind, "外部连接会话已失效，请重新连接。");
        return;
      }
      showToast(`导入失败：${e.message}`);
    } finally {
      setConnectorBusy(false);
    }
  };

  const totalRows = Math.max(content?.total_rows ?? rows.length, offset + rows.length);
  const totalCols = columns.length || content?.total_cols || 0;
  const pageCount = Math.max(1, Math.ceil(totalRows / PAGE));
  const curPage = Math.floor(offset / PAGE);
  const gotoPage = (p) => { if (active && !isMd) loadContent(active, Math.max(0, Math.min(pageCount - 1, p)) * PAGE); };

  const displayGroups = useMemo(() => [...groups, ...externalGroups], [groups, externalGroups]);

  const filteredGroups = useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (!kw) return displayGroups;
    return displayGroups.map((g) => ({ ...g, files: (g.files || []).filter((f) => String(f.name || "").toLowerCase().includes(kw)) })).filter((g) => (g.files || []).length);
  }, [displayGroups, q]);
  const latestHistoryUser = history.length ? historyUser(history[0], user) : null;

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
          <button className="dw-btn" type="button" disabled={!dirty || saving || isExternal} onClick={save}>{saving ? <Loader2 size={15} className="spin" /> : <Save size={15} />}保存更改</button>
          <button className="dw-btn ghost-blue" type="button" disabled={!active || analyzing || isExternal} onClick={sendToAnalysis}>{analyzing ? <Loader2 size={15} className="spin" /> : <Send size={15} />}发送到分析</button>
          <button className="dw-btn" type="button" disabled={!active || isExternal} onClick={deleteActiveFile}><Trash2 size={15} />删除文件</button>
        </div>
        <div className="dw-actions-r">
          <div className="dw-search"><Search size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索文件或字段…" /></div>
        </div>
      </div>

      <nav className="dw-tabs">
        {TABS.map((t) => (
          <button key={t.id} type="button" className={tab === t.id ? "dw-tab active" : "dw-tab"} disabled={isExternal && t.id !== "table"} onClick={() => setTab(t.id)}>{t.label}</button>
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
                    <button key={f.id} type="button" className={`${active?.id === f.id ? "dw-file active" : "dw-file"}${f.external ? " external" : ""}`} onClick={() => openFile(f)} title={f.name}>
                      {fileIconFor(f.type, f.name)}<span>{f.name}</span>
                      {f.external ? <em className="dw-file-ext">外部</em> : null}
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
          ) : tab === "table" && isTextPreview ? (
            <div className="dw-md">
              <div className="dw-md-bar">
                <span className="dw-md-name"><FileText size={14} />{active.name}</span>
                <span className="dw-md-time">{content?.total_chars != null ? `${content.total_chars} 字` : ""}</span>
              </div>
              {isExternal ? (
                <div className="dw-external-tools compact">
                  <span className="dw-source-chip">Blob 只读预览</span>
                  <span className="dw-source-path">{`${active.source?.container}/${active.source?.blob}`}</span>
                  <button type="button" className="dw-btn primary" disabled={importingExternal} onClick={() => importExternalSource(active)}>
                    {importingExternal ? <Loader2 size={15} className="spin" /> : <FileUp size={15} />}
                    导入到文件库
                  </button>
                </div>
              ) : null}
              <textarea className="dw-md-area" value={mdText} readOnly={isJson || isExternal} onChange={(e) => onMdChange(e.target.value)} />
              {isJson ? <div className="dw-json-note">JSON 当前支持预览与质量校验；需要修改时请导入为表格或上传新版本。</div> : null}
            </div>
          ) : tab === "table" ? (
            <>
              {isExternal ? (
                <div className="dw-external-tools">
                  <span className="dw-source-chip">{active.externalKind === "sql" ? "SQL 只读预览" : "Blob 只读预览"}</span>
                  <span className="dw-source-path">{active.externalKind === "sql" ? active.source?.table : `${active.source?.container}/${active.source?.blob}`}</span>
                  <button type="button" className="dw-btn primary" disabled={importingExternal} onClick={() => importExternalSource(active)}>
                    {importingExternal ? <Loader2 size={15} className="spin" /> : <FileUp size={15} />}
                    导入到文件库
                  </button>
                </div>
              ) : (
                <div className="dw-table-tools">
                <button type="button" className="dw-tool-btn" onClick={addRow}><Rows3 size={14} />新增行</button>
                <button type="button" className="dw-tool-btn" onClick={deleteRow} disabled={!selectedCell}><Trash2 size={14} />删除行</button>
                <button type="button" className="dw-tool-btn" onClick={addColumn}><Columns3 size={14} />新增列</button>
                <button type="button" className="dw-tool-btn" onClick={deleteColumn} disabled={!selectedCol}><Trash2 size={14} />删除列</button>
                <div className="dw-formula">
                  <span>{selectedCell ? `${selectedCol}${offset + selectedCell.row + 1}` : "fx"}</span>
                  <input
                    value={selectedCell && selectedCol ? rows[selectedCell.row]?.[selectedCell.col] ?? "" : ""}
                    onChange={(e) => updateSelectedCell(e.target.value)}
                    disabled={!selectedCell}
                    placeholder="选择单元格后编辑内容"
                  />
                </div>
                </div>
              )}
              <div className="dw-grid-wrap">
                <table className="dw-grid">
                  <colgroup>
                    <col style={{ width: 54 }} />
                    {columns.map((c) => <col key={c} style={{ width: colWidths[c] || 136 }} />)}
                  </colgroup>
                  <thead>
                    <tr><th className="dw-rownum" />{columns.map((c, i) => (
                      <th key={i} className={selectedCell?.col === i ? "sel-col" : ""} onContextMenu={(e) => openTableContextMenu(e, selectedCell?.row ?? 0, i, "column")}>
                        <span>{c}</span>
                        <i className="dw-col-resize" onMouseDown={(e) => { e.preventDefault(); resizeColumn(c, e.clientX); }} />
                      </th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {rows.map((row, ri) => (
                      <tr key={ri}>
                        <td className={selectedCell?.row === ri ? "dw-rownum active" : "dw-rownum"} onContextMenu={(e) => openTableContextMenu(e, ri, selectedCell?.col ?? 0, "row")}>{offset + ri + 1}</td>
                        {columns.map((c, ci) => (
                          <td key={ci} className={selectedCell?.row === ri && selectedCell?.col === ci ? "dw-cell sel" : "dw-cell"} onClick={() => setSelectedCell({ row: ri, col: ci })} onContextMenu={(e) => openTableContextMenu(e, ri, ci, "cell")}>
                            <input className="dw-cell-in" value={row[ci] ?? ""} readOnly={isExternal} onFocus={() => setSelectedCell({ row: ri, col: ci })} onChange={(e) => { if (!isExternal) onCellChange(ri, c, e.target.value); }} />
                          </td>
                        ))}
                      </tr>
                    ))}
                    {!rows.length ? <tr><td className="dw-rownum" onContextMenu={(e) => openTableContextMenu(e, 0, selectedCell?.col ?? 0, "row")}>1</td>{columns.map((c, ci) => <td key={ci} className="dw-cell" onContextMenu={(e) => openTableContextMenu(e, 0, ci, "cell")} />)}</tr> : null}
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
            <HistoryPanel history={history} currentUser={user} />
          )}
        </section>

        {/* 右：数据状态 */}
        <aside className="card dw-status">
          <div className="dw-status-head"><span className="t">数据状态</span></div>
          {isExternal ? (
            <>
              <div className="dw-sec dw-external-status">
                <div className="dw-sec-row"><span className="dw-sec-t">外部来源</span><span className="dw-chip warn">只读</span></div>
                <div className="dw-ext-source">
                  <b>{active.externalKind === "sql" ? "SQL Database" : "Blob Storage"}</b>
                  <span>{active.externalKind === "sql" ? active.source?.database || "database" : active.source?.container}</span>
                  <em>{active.externalKind === "sql" ? active.source?.table : active.source?.blob}</em>
                </div>
              </div>
              <div className="dw-sec">
                <div className="dw-sec-t">预览状态</div>
                <ul className="dw-qlist">
                  <li><span>行数</span><span className="qv">{content?.total_rows ?? rows.length}</span></li>
                  <li><span>列数</span><span className="qv">{content?.total_cols ?? columns.length}</span></li>
                  <li><span>模式</span><span className="qv">预览后导入</span></li>
                </ul>
              </div>
              <div className="dw-sec">
                <button type="button" className="dw-btn primary dw-import-wide" disabled={importingExternal} onClick={() => importExternalSource(active)}>
                  {importingExternal ? <Loader2 size={15} className="spin" /> : <FileUp size={15} />}
                  导入到文件库
                </button>
                <div className="dw-sec-sub">导入后会成为本地文件，可编辑、保存并发送到分析。</div>
              </div>
            </>
          ) : (
            <>
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
                <div className="dw-mod-av">{latestHistoryUser.initial}</div>
                <div className="dw-mod-meta">
                  <div className="dw-mod-top"><span className="dw-mod-mail" title={latestHistoryUser.email || latestHistoryUser.name}>{latestHistoryUser.name}</span><span className="dw-mod-time">{fmtTime(history[0].at)}</span></div>
                  <div className="dw-mod-desc">{history[0].change_summary || "—"}</div>
                </div>
              </div>
            ) : <div className="dw-sec-sub">暂无修改记录</div>}
            <button type="button" className="dw-link-btn" onClick={() => setTab("history")}><History size={13} />查看版本历史</button>
          </div>
            </>
          )}
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
            const connected = connectorResult?.kind === c.id;
            return (
              <div className="dw-conn-card" key={c.id}>
                <div className="dw-conn-top">
                  <div className="dw-conn-ic">{c.src ? <img src={c.src} width="22" height="22" alt="" /> : <Icon size={20} />}</div>
                  {planned ? <span className="dw-badge planned">计划上线</span> : connected ? <span className="dw-badge connected">已连接</span> : <span className="dw-badge ok">可用</span>}
                </div>
                <div className="dw-conn-name">{c.name}</div>
                <div className="dw-conn-status">{c.hint}</div>
                {connected && connectorResult?.expires_at ? <div className="dw-conn-exp">有效期至 {fmtTime(connectorResult.expires_at)}</div> : null}
                <div className="dw-conn-actions">
                  <button type="button" className={planned ? "dw-conn-btn" : "dw-conn-btn primary"} disabled={planned}
                    onClick={() => {
                      if (planned) return;
                      if (c.id === "upload") {
                        onUpload && onUpload(workspaceId);
                      } else {
                        setConnectorModal(c.id);
                      }
                    }}>
                    {planned ? "敬请期待" : c.id === "upload" ? "上传文件" : connected ? "重新接入" : "接入"}
                  </button>
                  {connected ? (
                    <button type="button" className="dw-conn-btn danger" disabled={connectorBusy} onClick={() => disconnectConnector(c.id, connectorResult.connection_id)}>
                      断开
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </section>
      {createModal ? (
        <CreateFileModal
          state={createModal}
          setState={setCreateModal}
          onClose={() => setCreateModal(null)}
          onSubmit={submitCreateFile}
        />
      ) : null}
      {connectorModal ? (
        <ConnectorModal
          kind={connectorModal}
          busy={connectorBusy}
          result={connectorResult}
          onClose={() => { setConnectorModal(null); }}
          onSubmit={submitConnector}
          onImport={importConnectorItem}
          onListBlob={async (container) => {
            if (!connectorResult?.connection_id) return;
            setConnectorBusy(true);
            try {
              const data = await dwBlobItems(workspaceId, connectorResult.connection_id, container.name || container);
              setConnectorResult((prev) => ({ ...prev, blobs: (data.blobs || []).map((item) => ({ ...item, container: container.name || container })) }));
            } catch (e) {
              if (isConnectorSessionError(e)) {
                clearConnectorState("blob", "外部连接会话已失效，请重新连接。");
                return;
              }
              showToast(`列出 Blob 失败：${e.message}`);
            } finally {
              setConnectorBusy(false);
            }
          }}
        />
      ) : null}
      {contextMenu ? (
        <div
          className="dw-context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          role="menu"
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.preventDefault()}
        >
          <button type="button" role="menuitem" onClick={() => insertRowAt(contextMenu.row, "above")}><Rows3 size={14} />在上方插入行</button>
          <button type="button" role="menuitem" onClick={() => insertRowAt(contextMenu.row, "below")}><Rows3 size={14} />在下方插入行</button>
          <button type="button" role="menuitem" onClick={() => deleteRowAt(contextMenu.row)} disabled={!rows.length}><Trash2 size={14} />删除当前行</button>
          <i aria-hidden="true" />
          <button type="button" role="menuitem" onClick={() => insertColumnAt(contextMenu.col, "left")}><Columns3 size={14} />在左侧插入列</button>
          <button type="button" role="menuitem" onClick={() => insertColumnAt(contextMenu.col, "right")}><Columns3 size={14} />在右侧插入列</button>
          <button type="button" role="menuitem" onClick={() => deleteColumnAt(contextMenu.col)} disabled={columns.length <= 1}><Trash2 size={14} />删除当前列</button>
        </div>
      ) : null}
    </main>
  );
}

function CreateFileModal({ state, setState, onClose, onSubmit }) {
  const isMd = state.kind === "md";
  const Icon = isMd ? FileText : Table2;
  return (
    <div className="modal-overlay" role="presentation">
      <div className="upload-modal dw-small-modal" role="dialog" aria-modal="true" aria-label={isMd ? "新建 Markdown" : "新建表格"}>
        <div className="modal-head">
          <div>
            <strong>{isMd ? "新建 Markdown" : "新建表格"}</strong>
            <span>创建后立即进入文件库，可编辑并发送到分析。</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="关闭">
            <X size={17} />
          </button>
        </div>
        <label className="modal-field">
          <span>文件名</span>
          <input value={state.name} onChange={(e) => setState((m) => ({ ...m, name: e.target.value }))} autoFocus />
        </label>
        {isMd ? (
          <label className="modal-field">
            <span>初始内容</span>
            <textarea rows={6} value={state.text} onChange={(e) => setState((m) => ({ ...m, text: e.target.value }))} />
          </label>
        ) : (
          <label className="modal-field">
            <span>列名（逗号分隔）</span>
            <input value={state.columns} onChange={(e) => setState((m) => ({ ...m, columns: e.target.value }))} />
          </label>
        )}
        <div className="modal-actions">
          <button className="ghost-button" type="button" onClick={onClose}>取消</button>
          <button className="primary-button icon-label" type="button" onClick={onSubmit}>
            <Icon size={15} />
            创建
          </button>
        </div>
      </div>
    </div>
  );
}

function ConnectorModal({ kind, busy, result, onClose, onSubmit, onImport, onListBlob }) {
  const isBlob = kind === "blob";
  const title = isBlob ? "接入 Azure Blob Storage" : "接入 SQL Database";
  const Icon = isBlob ? Cloud : Database;
  return (
    <div className="modal-overlay" role="presentation">
      <form className="upload-modal dw-connector-modal" role="dialog" aria-modal="true" aria-label={title} onSubmit={onSubmit}>
        <div className="modal-head">
          <div>
            <strong>{title}</strong>
            <span>凭证只发送到后端会话，不会保存在浏览器状态、日志或响应里。</span>
          </div>
          <button className="icon-button" type="button" onClick={onClose} disabled={busy} aria-label="关闭">
            <X size={17} />
          </button>
        </div>
        {isBlob ? (
          <>
            <label className="modal-field"><span>连接字符串</span><textarea name="connection_string" rows={3} placeholder="DefaultEndpointsProtocol=..." /></label>
            <div className="dw-modal-split">
              <label className="modal-field"><span>Storage Account</span><input name="account" autoComplete="off" /></label>
              <label className="modal-field"><span>SAS Token</span><input name="sas" type="password" autoComplete="new-password" /></label>
            </div>
          </>
        ) : (
          <>
            <label className="modal-field"><span>连接字符串（可选）</span><textarea name="connection_string" rows={2} placeholder="DRIVER=...;SERVER=..." /></label>
            <div className="dw-modal-split">
              <label className="modal-field"><span>Server</span><input name="server" autoComplete="off" /></label>
              <label className="modal-field"><span>Database</span><input name="database" autoComplete="off" /></label>
            </div>
            <div className="dw-modal-split">
              <label className="modal-field"><span>Username</span><input name="username" autoComplete="off" /></label>
              <label className="modal-field"><span>Password</span><input name="password" type="password" autoComplete="new-password" /></label>
            </div>
          </>
        )}
        {result ? (
          <div className="dw-connector-result">
            {isBlob ? (
              <>
                {(result.containers || []).map((item) => (
                  <button className="dw-result-row" type="button" key={item.name} onClick={() => onListBlob(item)}>
                    <Cloud size={14} />{item.name}<span>列出</span>
                  </button>
                ))}
                {(result.blobs || []).map((item) => (
                  <button className="dw-result-row" type="button" key={`${item.container}/${item.name}`} onClick={() => onImport(item)}>
                    <FileText size={14} />{item.name}<span>导入</span>
                  </button>
                ))}
              </>
            ) : (
              (result.tables || []).map((item) => (
                <button className="dw-result-row" type="button" key={item.id || `${item.schema}.${item.name}`} onClick={() => onImport(item)}>
                  <Database size={14} />{item.id || `${item.schema}.${item.name}`}<span>导入</span>
                </button>
              ))
            )}
          </div>
        ) : null}
        <div className="modal-actions">
          <button className="ghost-button" type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="primary-button icon-label" type="submit" disabled={busy}>
            {busy ? <Loader2 className="spin" size={15} /> : <Icon size={15} />}
            连接
          </button>
        </div>
      </form>
    </div>
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

function HistoryPanel({ history, currentUser }) {
  if (!history.length) return <div className="empty-copy" style={{ padding: 40 }}>暂无版本历史。保存修改后会在这里出现。</div>;
  return (
    <div className="dw-panel">
      <ul className="dw-hist">
        {history.map((h, i) => {
          const display = historyUser(h, currentUser);
          return (
            <li key={i} className="dw-hist-row">
              <div className="dw-mod-av">{display.initial}</div>
              <div className="dw-mod-meta">
                <div className="dw-mod-top"><span className="dw-mod-mail" title={display.email || display.name}>{display.name}</span><span className="dw-mod-time">{fmtTime(h.at)}</span></div>
                <div className="dw-mod-desc">{h.change_summary || "—"}</div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
