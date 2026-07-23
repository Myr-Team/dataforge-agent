import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Bot,
  Clock3,
  Database,
  Image as ImageIcon,
  Loader2,
  RefreshCw,
  Route,
  ShieldCheck,
  WalletCards,
  X,
} from "lucide-react";

import { loadMonitoringDashboard } from "./api.js";
import { canViewGovernance } from "./constants.js";
import { monitorDashboardViewModel } from "./monitorDashboardViewModel.js";

function todayDateValue() {
  return new Date().toISOString().slice(0, 10);
}

function shiftDate(days) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function initialWindow() {
  return { from: shiftDate(-6), to: todayDateValue() };
}

function toWindowIso(value, endOfDay = false) {
  const text = String(value || "").trim();
  if (!text) return "";
  return `${text}T${endOfDay ? "23:59:59" : "00:00:00"}Z`;
}

function clampWindow(next) {
  const from = String(next?.from || "").trim();
  const to = String(next?.to || "").trim();
  if (!from || !to) return { from, to };
  return from <= to ? { from, to } : { from: to, to: from };
}

function statusTone(value) {
  const status = String(value || "").trim().toLowerCase();
  if (["verified", "available", "evaluated"].includes(status)) return "ok";
  if (["pending_verification", "stale", "partial", "estimated"].includes(status)) return "warn";
  if (["malformed", "failed", "error"].includes(status)) return "error";
  return "neutral";
}

function metricIcon(cardId) {
  return {
    governed: ShieldCheck,
    calls: Activity,
    tokens: Bot,
    cost: WalletCards,
    cache: Database,
  }[cardId] || Activity;
}

function cardEntries(view) {
  return [
    { id: "governed", label: "受治理调用", ...view.cards.governed },
    { id: "tokens", label: "Tokens", ...view.cards.tokens },
    { id: "cost", label: "估算消耗", ...view.cards.cost },
    { id: "cache", label: "Redis 复用", ...view.cards.cache },
  ];
}

function formatAxisToken(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return "0";
  if (number >= 1000) return `${Math.round(number / 100) / 10}k`;
  return String(Math.round(number));
}

function MetricCard({ card }) {
  const Icon = metricIcon(card.id);
  return (
    <article className="monitor-card">
      <div className="monitor-card-head">
        <span>{card.label}</span>
        <Icon size={16} />
      </div>
      <strong>{card.value}</strong>
      <div className="monitor-card-foot">
        <span className={`monitor-badge ${card.tone || statusTone(card.badge)}`}>{card.badge}</span>
        <small>{card.meta}</small>
      </div>
    </article>
  );
}

function FrameState({ kind, message = "" }) {
  if (kind === "loading") {
    return (
      <div className="monitor-state">
        <Loader2 className="spin" size={18} />
        <span>加载中</span>
      </div>
    );
  }
  if (kind === "error") {
    return (
      <div className="monitor-state error">
        <span>{message || "读取失败"}</span>
      </div>
    );
  }
  return (
    <div className="monitor-state">
      <span>{message || "无数据"}</span>
    </div>
  );
}

function Frame({ title, subtitle, children, loading = false, error = "", empty = false, hasData = false, className = "" }) {
  const blockingState = hasData ? "" : loading ? "loading" : error ? "error" : empty ? "empty" : "";
  return (
    <section className={`monitor-frame ${className}`.trim()}>
      <header className="monitor-frame-head">
        <div>
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
      </header>
      <div className="monitor-chart-frame">
        {blockingState ? <FrameState kind={blockingState} message={blockingState === "error" ? "读取失败" : "无数据"} /> : null}
        {!blockingState ? (
          <div className="monitor-data-layer" data-loading={loading ? "true" : "false"} aria-busy={loading ? "true" : "false"}>
            {children}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function TrendChart({ rows }) {
  const width = 640;
  const height = 248;
  const padding = 28;
  const maxTokens = Math.max(...rows.map((item) => Number(item.total_tokens || 0)), 1);
  const maxCalls = Math.max(...rows.map((item) => Number(item.calls || 0)), 1);
  const points = rows.map((item, index) => {
    const x = rows.length === 1 ? width / 2 : padding + ((width - padding * 2) * index) / (rows.length - 1);
    const y = height - padding - ((height - padding * 2) * Number(item.total_tokens || 0)) / maxTokens;
    return `${x},${y}`;
  }).join(" ");

  return (
    <div className="monitor-trend">
      <svg viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
        {[0, 0.5, 1].map((tick) => {
          const y = padding + (height - padding * 2) * tick;
          return <line key={tick} x1={padding} y1={y} x2={width - padding} y2={y} className="monitor-grid-line" />;
        })}
        {rows.length > 1 ? (
          <>
            <polyline points={points} className="monitor-line-shadow" />
            <polyline points={points} className="monitor-line" />
          </>
        ) : null}
        {rows.map((item, index) => {
          const x = rows.length === 1 ? width / 2 : padding + ((width - padding * 2) * index) / Math.max(rows.length - 1, 1);
          const tokenY = height - padding - ((height - padding * 2) * Number(item.total_tokens || 0)) / maxTokens;
          const barHeight = ((height - padding * 2) * Number(item.calls || 0)) / maxCalls;
          return (
            <g key={item.date || index}>
              <rect x={x - 12} y={height - padding - barHeight} width="24" height={Math.max(barHeight, 2)} rx="6" className="monitor-bar" />
              <circle cx={x} cy={tokenY} r="4" className="monitor-dot" />
            </g>
          );
        })}
      </svg>
      <div className="monitor-trend-legend">
        <span><i className="monitor-legend-swatch bars" />每日调用</span>
        <span><i className="monitor-legend-swatch line" />每日 Tokens</span>
      </div>
      <div className="monitor-trend-table">
        {rows.map((item) => (
          <div key={item.date} className="monitor-mini-row">
            <span>{item.date}</span>
            <b>{item.calls} 次</b>
            <em>{formatAxisToken(item.total_tokens)}</em>
          </div>
        ))}
      </div>
    </div>
  );
}

function RankedBars({ rows, labelKey, valueKey, valueLabel, icon }) {
  const maxValue = Math.max(...rows.map((item) => Number(item[valueKey] || 0)), 1);
  return (
    <div className="monitor-ranked-list">
      {rows.map((row, index) => (
        <div className="monitor-ranked-row" key={`${row[labelKey]}-${index}`}>
          <div className="monitor-ranked-label">
            {icon}
            <span>{row[labelKey]}</span>
          </div>
          <div className="monitor-ranked-track">
            <i style={{ width: `${Math.max(8, Math.round((Number(row[valueKey] || 0) / maxValue) * 100))}%` }} />
          </div>
          <div className="monitor-ranked-meta">
            <b>{row[valueKey]}</b>
            <small>{row.secondaryLabel || row.selectionLabel || valueLabel}</small>
          </div>
        </div>
      ))}
    </div>
  );
}

function CoveragePanel({ coverage, gateway, opportunity }) {
  return (
    <div className="monitor-coverage">
      <div className="monitor-coverage-metrics">
        <article>
          <Route size={16} />
          <div>
            <span>受治理文本调用</span>
            <strong>{coverage.governedTextLabel}</strong>
          </div>
        </article>
        <article>
          <ImageIcon size={16} />
          <div>
            <span>图像直连调用</span>
            <strong>{coverage.imageCallLabel}</strong>
          </div>
        </article>
      </div>
      <div className={`monitor-gateway-proof ${gateway.tone}`}>
        <div>
          <small>网关核验</small>
          <strong>{gateway.label}</strong>
        </div>
        <span>{gateway.workspaceCount > 1 ? gateway.scopeLabel : gateway.sourceLabel}</span>
        <dl>
          <div><dt>受治理调用</dt><dd>{gateway.callsLabel}</dd></div>
          <div><dt>网关 Tokens</dt><dd>{gateway.tokensLabel}</dd></div>
          <div><dt>最后观测</dt><dd>{gateway.lastObservedAt ? new Date(gateway.lastObservedAt).toLocaleString("zh-CN") : "未记录"}</dd></div>
        </dl>
      </div>
      <div className={`monitor-opportunity ${statusTone(opportunity.status)}`}>
        <small>{opportunity.kind ? `优化机会 / ${opportunity.kind}` : "优化机会"}</small>
        <strong>{opportunity.status === "available" ? "可推进" : "待验证"}</strong>
        <p>{opportunity.message}</p>
      </div>
    </div>
  );
}

function MemberTable({ rows }) {
  return (
    <div className="monitor-table">
      {rows.map((row) => (
        <div className="monitor-table-row" key={row.label}>
          <div className="monitor-member-cell">
            <span className="monitor-member-avatar">{String(row.label || "M").slice(0, 1).toUpperCase()}</span>
            <div>
              <b>{row.label}</b>
              <small>{row.calls} 次运行</small>
            </div>
          </div>
          <strong>{row.totalTokensLabel || "未记录"}</strong>
          <span>{row.costLabel}</span>
        </div>
      ))}
    </div>
  );
}

function RequestStatus({ state, label }) {
  return <span className={`monitor-request-status ${state}`}>{label}</span>;
}

function RecentRequests({ rows, onOpen }) {
  return (
    <div className="monitor-request-table" role="table" aria-label="最近请求">
      <div className="monitor-request-head" role="row">
        <span role="columnheader">时间</span>
        <span role="columnheader">路由 / 模型</span>
        <span role="columnheader">状态</span>
        <span role="columnheader">缓存</span>
        <span role="columnheader">用量</span>
        <span role="columnheader">耗时</span>
      </div>
      {rows.map((row, index) => (
        <button
          key={`${row.occurredAt}-${row.route}-${index}`}
          type="button"
          className="monitor-request-row"
          role="row"
          onClick={() => onOpen(row)}
          title="查看安全溯源详情"
        >
          <span role="cell" className="monitor-request-time"><Clock3 size={14} />{row.occurredLabel}</span>
          <span role="cell" className="monitor-request-route"><b>{row.route}</b><small>{row.deployment}</small></span>
          <span role="cell"><RequestStatus state={row.status} label={row.statusLabel} /></span>
          <span role="cell" className={`monitor-cache-state ${row.cacheState}`}>{row.cacheLabel}</span>
          <span role="cell" className="monitor-request-number">{row.tokensLabel}</span>
          <span role="cell" className="monitor-request-number">{row.durationLabel}</span>
        </button>
      ))}
    </div>
  );
}

function RequestDrawer({ request, onClose }) {
  if (!request) return null;
  const details = [
    ["发生时间", request.occurredLabel],
    ["工作区", request.workspaceLabel],
    ["成员归因", request.memberLabel],
    ["模型路由", request.route],
    ["模型部署", request.deployment],
    ["请求状态", request.statusLabel],
    ["缓存状态", request.cacheLabel],
    ["Token", request.tokensLabel],
    ["模型耗时", request.durationLabel],
    ["运行 Agent", request.traceAgent],
    ["追踪参考", request.traceLabel],
  ];
  return (
    <div className="drawer-overlay" role="presentation" onClick={onClose}>
      <aside className="side-drawer monitor-request-drawer" role="dialog" aria-modal="true" aria-label="请求溯源详情" onClick={(event) => event.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h3>请求溯源</h3>
            <p>仅显示安全的运行级元数据。</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭请求溯源" title="关闭"><X size={18} /></button>
        </header>
        <div className="drawer-body monitor-request-detail-list">
          {details.map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <b>{value}</b>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}

function EmptyMonitorShell({ title, message }) {
  return (
    <main className="agent-studio monitor-stage">
      <section className="monitor-page" data-testid="monitor-page">
        <div className="monitor-inline-error">
          <span>{title}</span>
          <small>{message}</small>
        </div>
      </section>
    </main>
  );
}

export function MonitorPage({ workspaceId = "", workspaceAccess = null }) {
  const isOwner = canViewGovernance(workspaceAccess);
  const [scope, setScope] = useState("current");
  const [windowValue, setWindowValue] = useState(() => initialWindow());
  const [snapshot, setSnapshot] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadSeed, setReloadSeed] = useState(0);
  const [selectedRequest, setSelectedRequest] = useState(null);
  const requestRef = useRef(0);

  useEffect(() => {
    setSelectedRequest(null);
  }, [workspaceId]);

  useEffect(() => {
    if (!isOwner || !workspaceId) {
      setSnapshot(null);
      setLoading(false);
      setError("");
      return undefined;
    }

    const controller = new AbortController();
    const currentRequest = requestRef.current + 1;
    requestRef.current = currentRequest;
    setLoading(true);
    setError("");

    loadMonitoringDashboard({
      scope,
      workspaceId,
      from: toWindowIso(windowValue.from, false),
      to: toWindowIso(windowValue.to, true),
      signal: controller.signal,
    }).then((data) => {
      if (requestRef.current !== currentRequest) return;
      setSnapshot(data);
    }).catch((fetchError) => {
      if (controller.signal.aborted || requestRef.current !== currentRequest) return;
      setError(fetchError instanceof Error ? fetchError.message : "读取失败");
      setSnapshot((previous) => previous);
    }).finally(() => {
      if (requestRef.current === currentRequest) setLoading(false);
    });

    return () => controller.abort();
  }, [isOwner, scope, workspaceId, windowValue.from, windowValue.to, reloadSeed]);

  const view = useMemo(() => monitorDashboardViewModel(snapshot || {}), [snapshot]);
  const cards = cardEntries(view);
  const dailyRows = Array.isArray(view.dailySeries) ? view.dailySeries : [];
  const modelRows = Array.isArray(view.modelRows) ? view.modelRows : [];
  const routeRows = Array.isArray(view.routeRows) ? view.routeRows : [];
  const memberRows = Array.isArray(view.memberRows) ? view.memberRows : [];
  const requestRows = Array.isArray(view.requestRows) ? view.requestRows : [];
  const generatedAt = snapshot?.freshness?.generated_at
    ? new Date(snapshot.freshness.generated_at).toLocaleString("zh-CN")
    : "未记录";
  const sources = Array.isArray(snapshot?.freshness?.sources) && snapshot.freshness.sources.length
    ? snapshot.freshness.sources.join(" / ")
    : "未记录";
  const hasSnapshot = !!snapshot;

  if (!isOwner) {
    return <EmptyMonitorShell title="不可用" message="仅工作区所有者可查看监视数据。" />;
  }

  if (!workspaceId) {
    return <EmptyMonitorShell title="无数据" message="当前未选中工作区。" />;
  }

  return (
    <main className="agent-studio monitor-stage">
      <section className="monitor-page" data-testid="monitor-page">
        <header className="monitor-hero">
          <div className="monitor-hero-copy">
            <span className="monitor-eyebrow">监视</span>
            <h1>调用治理与价值闭环</h1>
            <p>只展示已落库或已验证的调用、质量、成本与 ROI 证据，不在浏览器补算未知值。</p>
          </div>
          <div className="monitor-toolbar">
            <div className="monitor-segment" role="tablist" aria-label="监视范围">
              <button type="button" className={scope === "current" ? "active" : ""} onClick={() => setScope("current")}>当前工作区</button>
              <button type="button" className={scope === "portfolio" ? "active" : ""} onClick={() => setScope("portfolio")}>已拥有组合</button>
            </div>
            <div className="monitor-window">
              <button type="button" className="monitor-chip" onClick={() => setWindowValue(initialWindow())}>近 7 天</button>
              <button type="button" className="monitor-chip" onClick={() => setWindowValue({ from: shiftDate(-29), to: todayDateValue() })}>近 30 天</button>
              <label>
                <span>起始</span>
                <input type="date" value={windowValue.from} onChange={(event) => setWindowValue((value) => clampWindow({ ...value, from: event.target.value }))} />
              </label>
              <label>
                <span>结束</span>
                <input type="date" value={windowValue.to} onChange={(event) => setWindowValue((value) => clampWindow({ ...value, to: event.target.value }))} />
              </label>
              <button type="button" className="monitor-refresh" onClick={() => setReloadSeed((value) => value + 1)} title="刷新监视数据">
                {loading ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
              </button>
            </div>
          </div>
        </header>

        <div className="monitor-meta-strip">
          <span>范围：{view.scopeLabel}</span>
          <span>更新时间：{generatedAt}</span>
          <span>数据源：{sources}</span>
        </div>

        {error ? (
          <div className="monitor-inline-error">
            <span>读取失败</span>
            <button type="button" onClick={() => setReloadSeed((value) => value + 1)}>重试</button>
          </div>
        ) : null}

        <div className="monitor-kpis">
          {cards.map((card) => <MetricCard key={card.id} card={card} />)}
        </div>

        <div className="monitor-grid">
          <Frame title="调用趋势" subtitle="按天聚合调用量与 Tokens" loading={loading} error={error} empty={!dailyRows.length} hasData={hasSnapshot && dailyRows.length > 0}>
            <TrendChart rows={dailyRows} />
          </Frame>
          <Frame title="覆盖与机会" subtitle="只基于后端记录的治理边界与优化摘要" loading={loading} error={error} empty={!hasSnapshot} hasData={hasSnapshot}>
            <CoveragePanel coverage={view.coverage} gateway={view.gateway} opportunity={view.opportunity} />
          </Frame>
          <Frame title="模型消耗" subtitle="按模型部署汇总调用与 Tokens" loading={loading} error={error} empty={!modelRows.length} hasData={hasSnapshot && modelRows.length > 0}>
            <RankedBars rows={modelRows} labelKey="deployment" valueKey="calls" valueLabel="调用" icon={<Bot size={15} />} />
          </Frame>
          <Frame title="路由分布" subtitle="观测到的文本路由占比" loading={loading} error={error} empty={!routeRows.length} hasData={hasSnapshot && routeRows.length > 0}>
            <RankedBars rows={routeRows} labelKey="route" valueKey="calls" valueLabel="调用" icon={<Route size={15} />} />
          </Frame>
          <Frame title="成员归因" subtitle="按成员聚合运行次数、Tokens 与已记录成本" loading={loading} error={error} empty={!memberRows.length} hasData={hasSnapshot && memberRows.length > 0}>
            <MemberTable rows={memberRows} />
          </Frame>
        </div>

        <Frame
          className="monitor-request-frame"
          title="最近请求"
          subtitle="运行级溯源：路由、模型、用量、缓存与追踪参考。不会展示提示词、错误原文或原始身份标识。"
          loading={loading}
          error={error}
          empty={!requestRows.length}
          hasData={hasSnapshot && requestRows.length > 0}
        >
          <RecentRequests rows={requestRows} onOpen={setSelectedRequest} />
        </Frame>
      </section>
      <RequestDrawer request={selectedRequest} onClose={() => setSelectedRequest(null)} />
    </main>
  );
}
