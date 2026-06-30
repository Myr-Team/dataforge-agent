import { useState } from "react";
import {
  FileSpreadsheet,
  FileText,
  RefreshCw,
  Plus,
  Undo2,
  Redo2,
  Filter,
  ArrowUpDown,
  Table2,
  Settings2,
  MoreHorizontal,
  Search,
  List,
  LayoutGrid,
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
  Database,
  HardDrive,
  Cloud,
  FileUp,
} from "lucide-react";

const FILE_GROUPS = [
  { label: "数据集", files: ["surrounding_env.xlsx", "device_events.csv", "signal_density.xlsx", "cost_structure.csv"] },
  { label: "文档", files: ["market_notes.md", "project_brief.md"] },
  { label: "临时文件", files: ["temp_data.csv"] },
];

const TABLE_COLS = ["场景类型", "地点类型", "区域", "人流密度", "停留时长(分钟)", "信号强度(dbm)"];
const TABLE_ROWS = [
  ["商业街区", "城市商业街", "老城商圈", "502", "23", "-68"],
  ["商业街区", "城市商业街", "高新区", "305", "18", "-72"],
  ["商业街区", "城市商业街", "核心商圈", "612", "25", "-65"],
  ["办公园区", "高新园区", "科技园A区", "287", "16", "-74"],
  ["办公园区", "高新园区", "科技园B区", "230", "14", "-75"],
  ["生活社区", "住宅社区", "大型社区", "186", "12", "-76"],
  ["生活社区", "住宅社区", "老旧小区", "98", "8", "-79"],
  ["文旅景区", "公园景区", "市中心公园", "432", "28", "-69"],
  ["文旅景区", "公园景区", "滨江公园", "267", "19", "-73"],
  ["交通枢纽", "地铁站", "地铁2号线站", "821", "9", "-63"],
];
const COL_LETTERS = ["A", "B", "C", "D", "E", "F"];

const TABS = [
  { id: "files", label: "文件库" },
  { id: "table", label: "表格编辑" },
  { id: "mapping", label: "字段映射" },
  { id: "quality", label: "数据质量" },
  { id: "connectors", label: "连接器" },
];

// 能落地的标 available（可接入），尚未就绪的标 planned（计划上线）
const CONNECTORS = [
  { id: "purview", name: "Microsoft Purview", src: "/icons/purview.svg", state: "available", hint: "数据治理与统一编目" },
  { id: "blob", name: "Azure Blob Storage", src: "/icons/azure-blob.svg", state: "available", hint: "对象存储 · 支持连接接入" },
  { id: "sql", name: "SQL Database", src: "/icons/sql-database.svg", state: "available", hint: "数据库 · 账号密码连接" },
  { id: "adl", name: "Azure Data Lake", src: "/icons/data-lake.svg", state: "planned", hint: "数据湖 · 计划上线" },
  { id: "upload", name: "CSV / Excel 上传", icon: FileUp, state: "available", hint: "本地上传文件" },
];

function fileIcon(name) {
  if (name.endsWith(".md")) return <FileText size={15} className="fi fi-md" />;
  if (name.endsWith(".xlsx")) return <FileSpreadsheet size={15} className="fi fi-xlsx" />;
  return <FileSpreadsheet size={15} className="fi fi-csv" />;
}

export function DataWorkbench({ dashboard, onRun }) {
  const workspace = dashboard?.workspace || {};
  const [tab, setTab] = useState("files");
  const [activeFile, setActiveFile] = useState("surrounding_env.xlsx");
  const [openTabs, setOpenTabs] = useState(["surrounding_env.xlsx", "device_events.csv", "market_notes.md"]);
  const [cell, setCell] = useState("A2");
  const [dirty, setDirty] = useState(false);
  const [toast, setToast] = useState("");
  const isMd = activeFile.endsWith(".md");

  const showToast = (msg) => { setToast(msg); window.clearTimeout(showToast._t); showToast._t = window.setTimeout(() => setToast(""), 2400); };
  const openFile = (name) => {
    setActiveFile(name);
    setOpenTabs((tabs) => (tabs.includes(name) ? tabs : [...tabs, name]));
    setTab(name.endsWith(".md") ? "table" : "table");
  };
  const closeTab = (name, e) => {
    e.stopPropagation();
    setOpenTabs((tabs) => {
      const next = tabs.filter((t) => t !== name);
      if (name === activeFile && next.length) setActiveFile(next[next.length - 1]);
      return next;
    });
  };

  return (
    <main className="agent-studio data-stage">
      {toast ? <div className="dw-toast">{toast}</div> : null}

      {/* 标题区 */}
      <header className="dw-head">
        <div>
          <div className="dw-title"><h1>数据工作台</h1><Info size={16} className="dw-info" /></div>
          <p>在工作区内上传、创建、预览和轻量编辑数据，支持 Markdown、CSV、Excel 文件；外部数据源当前作为 Demo 接口展示。</p>
        </div>
        <div className="dw-save">
          {dirty ? (
            <span className="dw-save-state dirty"><span className="dot" />有未保存更改</span>
          ) : (
            <span className="dw-save-state ok"><Check size={14} />已保存</span>
          )}
          <span className="dw-save-sub">{dirty ? "记得保存你的修改" : "所有更改已保存"}</span>
        </div>
      </header>

      {/* 操作按钮区 */}
      <div className="dw-actions">
        <div className="dw-actions-l">
          <button className="dw-btn primary" type="button" onClick={() => showToast("Demo：上传文件入口")}><UploadCloud size={15} />上传文件</button>
          <button className="dw-btn" type="button" onClick={() => { openFile("untitled.md"); setDirty(true); }}><FileText size={15} />新建 Markdown</button>
          <button className="dw-btn" type="button" onClick={() => { openFile("untitled.csv"); setDirty(true); }}><Table2 size={15} />新建表格</button>
          <button className="dw-btn" type="button" disabled={!dirty} onClick={() => { setDirty(false); showToast("已保存"); }}><Save size={15} />保存更改</button>
          <button className="dw-btn ghost-blue" type="button" onClick={() => showToast("该数据将进入 Agent Flow 分析流程")}><Send size={15} />发送到分析</button>
        </div>
        <div className="dw-actions-r">
          <div className="dw-search"><Search size={15} /><input placeholder="搜索文件或字段…" /></div>
          <div className="dw-iconset">
            <button className="dw-ic active" type="button" title="列表视图"><List size={16} /></button>
            <button className="dw-ic" type="button" title="网格视图"><LayoutGrid size={16} /></button>
            <button className="dw-ic" type="button" title="筛选"><Filter size={16} /></button>
          </div>
        </div>
      </div>

      {/* Tab 导航 */}
      <nav className="dw-tabs">
        {TABS.map((t) => (
          <button key={t.id} type="button" className={tab === t.id ? "dw-tab active" : "dw-tab"} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </nav>

      {/* 三栏主体 */}
      <div className="dw-body">
        {/* 左：文件资源树 */}
        <aside className="card dw-tree">
          <div className="dw-tree-head">
            <span className="t">文件库</span>
            <div className="dw-tree-acts">
              <button type="button" title="新建" onClick={() => showToast("Demo：新建文件")}><Plus size={15} /></button>
              <button type="button" title="刷新" onClick={() => showToast("已刷新")}><RefreshCw size={14} /></button>
            </div>
          </div>
          <div className="dw-tree-body">
            {FILE_GROUPS.map((g) => (
              <div className="dw-group" key={g.label}>
                <div className="dw-group-head"><ChevronDown size={13} />{g.label}</div>
                {g.files.map((f) => (
                  <button key={f} type="button" className={activeFile === f ? "dw-file active" : "dw-file"} onClick={() => openFile(f)}>
                    {fileIcon(f)}<span>{f}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
          <div className="dw-tree-foot">
            <div className="dw-store"><span>12.4 MB / 5 GB 已使用</span><div className="dw-store-bar"><i style={{ width: "12%" }} /></div></div>
          </div>
        </aside>

        {/* 中：编辑区 */}
        <section className="card dw-editor">
          <div className="dw-ftabs">
            {openTabs.map((f) => (
              <div key={f} className={activeFile === f ? "dw-ftab active" : "dw-ftab"} onClick={() => setActiveFile(f)}>
                {fileIcon(f)}<span>{f}</span><button type="button" className="dw-ftab-x" onClick={(e) => closeTab(f, e)}><X size={12} /></button>
              </div>
            ))}
            <button type="button" className="dw-ftab-add" onClick={() => showToast("Demo：打开/新建文件")}><Plus size={14} /></button>
          </div>

          {isMd ? (
            <div className="dw-md">
              <div className="dw-md-bar">
                <span className="dw-md-name"><FileText size={14} />{activeFile}</span>
                <div className="dw-md-bar-r">
                  <button type="button" className="dw-mini active">编辑</button>
                  <button type="button" className="dw-mini">预览</button>
                  <span className="dw-md-time">最近修改 2024-06-21 18:32</span>
                </div>
              </div>
              <textarea className="dw-md-area" defaultValue={"# 市场速记\n\n- 选址信号：核心商圈人流密度显著高于老城商圈\n- 信号强度(dbm)与停留时长存在正相关趋势，待进一步验证\n- 待补充：外部竞品门店分布与租金数据\n"} onChange={() => setDirty(true)} />
            </div>
          ) : (
            <>
              <div className="dw-toolbar">
                <button type="button" title="撤销"><Undo2 size={15} /></button>
                <button type="button" title="重做"><Redo2 size={15} /></button>
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
                    <tr><th className="dw-rownum" /> {COL_LETTERS.map((c) => <th key={c}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    <tr className="dw-fieldrow">
                      <td className="dw-rownum">1</td>
                      {TABLE_COLS.map((c, i) => <td key={i} className="dw-fieldname">{c}</td>)}
                    </tr>
                    {TABLE_ROWS.map((row, ri) => (
                      <tr key={ri} className={cell.startsWith(String(ri + 2)) || cell.endsWith(String(ri + 2)) ? "" : ""}>
                        <td className={`dw-rownum ${`A${ri + 2}` === cell || cell.endsWith(`${ri + 2}`) ? "active" : ""}`}>{ri + 2}</td>
                        {row.map((v, ci) => {
                          const id = `${COL_LETTERS[ci]}${ri + 2}`;
                          return <td key={ci} className={id === cell ? "dw-cell sel" : "dw-cell"} onClick={() => setCell(id)}>{v}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="dw-grid-foot">
                <span>共 457 行, 24 列</span>
                <span className="dw-rows-sel">显示前 100 行 <ChevronDown size={12} /></span>
                <div className="dw-pager">
                  <button type="button"><ChevronsLeft size={14} /></button>
                  <button type="button"><ChevronLeft size={14} /></button>
                  <span className="dw-page">1</span><span className="dw-page-of">/ 5</span>
                  <button type="button"><ChevronRight size={14} /></button>
                  <button type="button"><ChevronsRight size={14} /></button>
                </div>
              </div>
            </>
          )}
        </section>

        {/* 右：数据状态面板 */}
        <aside className="card dw-status">
          <div className="dw-status-head"><span className="t">数据状态</span><ChevronDown size={15} /></div>

          <div className="dw-sec">
            <div className="dw-sec-row"><span className="dw-sec-t">字段映射</span><span className="dw-sec-v">6 / 6 <b className="ok">100%</b></span></div>
            <div className="dw-prog"><i style={{ width: "100%" }} /></div>
            <button type="button" className="dw-link-btn" onClick={() => showToast("Demo：字段映射详情")}>查看映射详情</button>
          </div>

          <div className="dw-sec">
            <div className="dw-sec-t">数据质量</div>
            <ul className="dw-qlist">
              <li><span>缺失值</span><span className="qv">0.3% <Check size={14} className="ok" /></span></li>
              <li><span>重复值</span><span className="qv">0.0% <Check size={14} className="ok" /></span></li>
              <li><span>异常值</span><span className="qv">2 <AlertTriangle size={14} className="warn" /></span></li>
              <li><span>类型警告</span><span className="qv">0 <Check size={14} className="ok" /></span></li>
            </ul>
          </div>

          <div className="dw-sec">
            <div className="dw-sec-row"><span className="dw-sec-t">校验结果</span><span className="dw-chip ok">通过</span></div>
            <div className="dw-sec-sub">2024-06-21 18:32 校验完成</div>
          </div>

          <div className="dw-sec">
            <div className="dw-sec-t">最近修改</div>
            <div className="dw-mod">
              <div className="dw-mod-av">F</div>
              <div className="dw-mod-meta">
                <div className="dw-mod-top"><span className="dw-mod-mail">fuzih…@company.com</span><span className="dw-mod-time">2024-06-21 18:32</span></div>
                <div className="dw-mod-desc">更新了 3 行数据，修改了 2 列</div>
              </div>
            </div>
            <button type="button" className="dw-link-btn" onClick={() => showToast("Demo：版本历史")}><History size={13} />查看版本历史</button>
          </div>
        </aside>
      </div>

      {/* 底部：外部数据接入 */}
      <section className="card dw-connectors">
        <div className="dw-conn-head">
          <h2>外部数据接入</h2>
          <p>统一连接与管理企业数据治理、对象存储、数据库等数据源；标注「计划上线」的连接器即将开放。</p>
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
                <button
                  type="button"
                  className={planned ? "dw-conn-btn" : "dw-conn-btn primary"}
                  disabled={planned}
                  onClick={() => (planned ? null : showToast(c.id === "upload" ? "上传文件入口" : `接入 ${c.name}`))}
                >
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
