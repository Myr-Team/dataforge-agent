import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BookmarkPlus,
  Clock3,
  CircleHelp,
  Database,
  Download,
  ExternalLink,
  Gauge,
  Loader2,
  Pencil,
  PieChart,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  WalletCards,
  X,
} from "lucide-react";

import {
  acknowledgeFinOpsAnomaly,
  createFinOpsRemediationDraft,
  createFinOpsSavedView,
  createWorkspaceRoiScenario,
  finOpsExportUrl,
  loadFinOpsAgents,
  loadFinOpsBootstrap,
  loadFinOpsBreakdowns,
  loadFinOpsEvidence,
  loadFinOpsRemediationDraft,
  loadFinOpsRemediationDrafts,
  loadFinOpsRequest,
  loadFinOpsRequests,
  loadFinOpsRiskDecision,
  loadLatestFinOpsRiskScan,
  loadFinOpsRoiDecision,
  loadFinOpsRoiEconomics,
  loadFinOpsSavedViews,
  loadFinOpsTrends,
  promoteFinOpsRemediationDraft,
  reviewFinOpsRemediationDraft,
  runFinOpsRiskScan,
  suppressFinOpsAnomaly,
} from "./api.js";
import {
  cancelFinOpsDataLoad,
  finopsDataKey,
  readFinOpsData,
} from "./finopsDataStore.js";
import {
  finopsTabDataKey,
  finopsTabIntentHandlers,
  invalidateFinOpsMutation,
  loadFinOpsTab,
  prefetchFinOpsTab,
} from "./finopsNavigation.js";
import {
  useFinOpsComparisonLifecycle,
  useFinOpsIdlePreload,
  useFinOpsRefreshLifecycle,
  useFinOpsTabResource,
} from "./finopsPortalLifecycle.js";
import { FinOpsAssistant } from "./FinOpsAssistant.jsx";
import { ModelRoutingPage } from "./ModelRoutingPage.jsx";
import { RemediationDraftPanel } from "./finops/RemediationDraftPanel.jsx";
import { RiskDecisionPage } from "./finops/RiskDecisionPage.jsx";
import { RoiDecisionPage } from "./finops/RoiDecisionPage.jsx";
import {
  ViewportTooltip,
  useViewportTooltipAnchor,
} from "./finops/ViewportTooltip.jsx";
export { viewportTooltipPosition } from "./finops/ViewportTooltip.jsx";
import {
  REMEDIATION_RESELECT_MESSAGE,
  orchestrateRemediationMutation,
} from "./finops/remediationOrchestration.js";
import { remediationDraftView, riskDecisionView } from "./finopsDecisionViewModel.js";
import {
  executiveCostSummary,
  executiveOverviewView,
} from "./finopsExecutiveOverview.js";
import {
  applyDimensionFilter,
  filterChips,
  metricContext,
  metricTooltip,
  previousEqualWindow,
} from "./finopsInteraction.js";
import {
  CUSTOMER_INFRA_LABELS,
  FINOPS_TABS,
  finopsBootstrapViewData,
  finopsBudgetView,
  finopsBreakdownRows,
  evidenceRequestRef,
  finopsMetricCards,
  finopsRequestViewModel,
  finopsTrendViewModel,
  finopsBarPercent,
  niceFinOpsAxis,
  formatFinOpsCost,
  formatFinOpsDuration,
  formatFinOpsNumber,
  formatFinOpsPercent,
  formatRelativeUpdateTime,
  gatewayUnmatchedEvidence,
} from "./finopsViewModel.js";


const TAB_ICONS = {
  overview: Gauge,
  cost: WalletCards,
  roi: TrendingUp,
  risk: AlertTriangle,
};


const GENERAL_ASSISTANT_DESCRIPTORS = Object.freeze({
  overview: Object.freeze({ id: "operations_overview", label: "运营总览", kind: "overview" }),
  cost: Object.freeze({ id: "estimated_cost", label: "成本分析", kind: "cost" }),
  roi: Object.freeze({ id: "roi_ratio", label: "效能与 ROI", kind: "roi" }),
  risk: Object.freeze({ id: "risk_summary", label: "风险与优化", kind: "risk" }),
});


export function generalAssistantDescriptor(tab) {
  return GENERAL_ASSISTANT_DESCRIPTORS[tab] || GENERAL_ASSISTANT_DESCRIPTORS.overview;
}


function dateValue(date) {
  return date.toISOString().slice(0, 10);
}


function initialWindow() {
  const to = new Date();
  const from = new Date(to);
  from.setUTCDate(from.getUTCDate() - 30);
  return { from: dateValue(from), to: dateValue(to) };
}


function toIso(value, end = false) {
  return value ? `${value}T${end ? "23:59:59" : "00:00:00"}Z` : "";
}


function EmptyState({ children = "当前范围没有可展示的记录。" }) {
  return (
    <div className="finops-empty">
      <Database size={18} />
      <span>{children}</span>
    </div>
  );
}


function evidenceStatusLabel(status) {
  const normalized = String(status || "unavailable").toLowerCase();
  return {
    available: "完整",
    complete: "完整",
    completed: "已完成",
    observed: "已观测",
    measured: "已记录",
    recorded: "已记录",
    verified: "已验证",
    estimated: "估算",
    partial: "部分",
    incomplete: "证据不足",
    not_recorded: "未记录",
    not_configured: "未配置",
    unavailable: "待接入",
    unpriced: "未计价",
    no_samples: "暂无样本",
    reconciliation_pending: "待对账",
    open: "待处理",
    acknowledged: "已确认",
    suppressed: "已抑制",
    resolved: "已解决",
    stale: "已过期",
    failed: "失败",
    succeeded: "调用成功",
    ready: "分析完成",
    insufficient_data: "证据不足",
  }[normalized] || "未记录";
}


function EvidenceBadge({ status }) {
  const normalized = String(status || "unavailable").toLowerCase();
  const label = evidenceStatusLabel(normalized);
  return <span className={`finops-evidence ${normalized}`}>{label}</span>;
}


function Panel({ title, subtitle = "", children, className = "", action = null }) {
  return (
    <section className={`finops-panel ${className}`.trim()}>
      <header>
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {action ? <div className="finops-panel-action">{action}</div> : null}
      </header>
      <div className="finops-panel-body">{children}</div>
    </section>
  );
}


function formatMetricTooltipValue(row) {
  if (row.format === "currency") return formatFinOpsCost(row.value, "estimated");
  if (row.format === "percent") return formatFinOpsPercent(row.value);
  if (row.format === "duration") return formatFinOpsDuration(row.value);
  if (row.format === "text") return row.value;
  return formatFinOpsNumber(row.value);
}


function MetricHelp({ card, tooltip }) {
  const tooltipId = `finops-metric-tooltip-${card.id}`;
  const { anchorRef, open, toggle, anchorProps } = useViewportTooltipAnchor();
  return (
    <>
      <button
        {...anchorProps}
        ref={anchorRef}
        className="finops-help-trigger"
        type="button"
        aria-label={`${card.label}说明`}
        aria-describedby={tooltipId}
        aria-expanded={open}
        onClick={toggle}
      >
        <CircleHelp size={13} aria-hidden="true" />
      </button>
      <ViewportTooltip anchorRef={anchorRef} open={open} id={tooltipId} variant="finops-metric-tooltip-content">
        <header><b>{tooltip.title}</b><EvidenceBadge status={tooltip.evidenceState} /></header>
        {tooltip.rows.length ? (
          <dl>
            {tooltip.rows.map((row) => (
              <div key={row.label}><dt>{row.label}</dt><dd>{formatMetricTooltipValue(row)}</dd></div>
            ))}
          </dl>
        ) : <p>当前指标暂无更多可复核明细。</p>}
        <small>当前筛选范围 · {tooltip.dataStatus === "complete" ? "数据完整" : tooltip.dataStatus === "partial" ? "数据不完整" : "待接入"}</small>
      </ViewportTooltip>
    </>
  );
}


function MetricCards({
  cards = null,
  payload,
  scope,
  onEvidence = null,
  onAsk = null,
  onConfigurePricing = null,
}) {
  const visibleCards = cards || finopsMetricCards(payload);
  return (
    <section className="finops-metrics" aria-label="运营核心指标">
      {visibleCards.map((card) => {
        const tooltip = metricTooltip(card.metric);
        const context = metricContext(card.metric, scope);
        return (
          <article
            className={`finops-metric ${card.tone}`}
            key={card.id}
            aria-label={`${card.label} ${card.value}`}
          >
            <div className="finops-metric-header">
              <span className="finops-metric-label">
                <span>{card.label}</span>
                <MetricHelp card={card} tooltip={tooltip} />
              </span>
              <span className="finops-metric-meta">
                <EvidenceBadge status={card.metric.evidenceState} />
                {card.id === "cost" && onConfigurePricing ? (
                  <button
                    className="finops-price-edit"
                    type="button"
                    title="关联官方模型价格"
                    aria-label="关联官方模型价格"
                    onClick={(event) => {
                      event.stopPropagation();
                      onConfigurePricing();
                    }}
                  >
                    <Pencil size={12} />
                  </button>
                ) : null}
              </span>
            </div>
            <strong>{card.value}</strong>
            <small>{card.meta}</small>
            <div className="finops-metric-actions">
              {onAsk ? <button type="button" onClick={() => onAsk(context)}>问 AI</button> : null}
              {onEvidence ? (
                <button type="button" onClick={() => onEvidence({ reason: `${card.label}指标`, metricId: card.id })}>
                  查看证据
                </button>
              ) : null}
            </div>
          </article>
        );
      })}
    </section>
  );
}


function MetricSkeleton() {
  return (
    <section className="finops-metrics finops-metrics-skeleton" aria-label="正在加载运营指标">
      {Array.from({ length: 4 }, (_, index) => (
        <article className="finops-metric" key={index}>
          <i />
          <b />
          <i />
        </article>
      ))}
    </section>
  );
}


function HorizontalBars({
  rows,
  valueKey = "requests",
  valueFormatter = formatFinOpsNumber,
  dimension = "",
  onSelect = null,
}) {
  const axis = niceFinOpsAxis(rows.map((row) => Number(row[valueKey] || 0)), 4);
  if (!rows.length) return <EmptyState />;
  return (
    <div className="finops-bars">
      {rows.slice(0, 10).map((row) => (
        <button
          className="finops-bar-row"
          key={row.key}
          type="button"
          disabled={!dimension || !onSelect}
          onClick={() => onSelect?.({ dimension, value: row.key })}
          aria-label={`${row.key}，${valueFormatter(row[valueKey])}`}
        >
          <span title={row.key}>{row.key}</span>
          <div>
            <i
              className={Number(row[valueKey] || 0) > 0 ? "has-value" : ""}
              style={{ width: `${finopsBarPercent(row[valueKey], axis.max)}%` }}
            />
          </div>
          <b>{valueFormatter(row[valueKey])}</b>
        </button>
      ))}
    </div>
  );
}


function TrendColumn({
  row,
  metric,
  rawValue,
  value,
  height,
  comparisonRow,
  comparisonValue,
  comparisonHeight,
  rowEvents,
  parts,
  partTotal,
  formatValue,
}) {
  const { anchorRef, open, anchorProps } = useViewportTooltipAnchor();
  const tooltipId = `finops-trend-tooltip-${String(row.bucket).replace(/[^A-Za-z0-9_-]/g, "-")}`;
  return (
    <div
      {...anchorProps}
      ref={anchorRef}
      className="finops-trend-column"
      tabIndex={0}
      aria-label={`${row.label}，${formatValue(rawValue)}`}
      aria-describedby={tooltipId}
    >
      {comparisonRow ? (
        <i
          className="finops-trend-comparison"
          style={{ height: `${comparisonHeight}%` }}
          title={`上一周期 ${formatValue(comparisonValue)}`}
        />
      ) : null}
      <div className="finops-trend-plot">
        <b className="finops-trend-value">{formatValue(rawValue)}</b>
        <div className="finops-trend-bar-slot">
          <div
            className={`finops-trend-stack ${value > 0 ? "has-value" : ""}`}
            style={{ height: `${height}%` }}
          >
            {metric !== "total"
              ? <i className="input" style={{ height: "100%" }} />
              : parts.map((part) => part.value
                ? <i key={part.key} className={part.key} style={{ height: `${(part.value / partTotal) * 100}%` }} />
                : null)}
          </div>
        </div>
      </div>
      <span>{row.label.slice(5)}</span>
      <ViewportTooltip anchorRef={anchorRef} open={open} id={tooltipId} variant="finops-trend-tooltip-content">
        <b>{row.label}</b>
        {metric !== "total" ? (
          <span>{{ cost: "估算成本", requests: "调用次数", p95: "P95 延迟" }[metric]} <strong>{formatValue(rawValue)}</strong></span>
        ) : (
          <>
            <span>输入 <strong>{formatFinOpsNumber(row.series.input)}</strong></span>
            <span>输出 <strong>{formatFinOpsNumber(row.series.output)}</strong></span>
            <span>缓存 <strong>{formatFinOpsNumber(row.series.cached)}</strong></span>
            <span>推理 <strong>{formatFinOpsNumber(row.series.reasoning)}</strong></span>
            <span>合计 <strong>{formatFinOpsNumber(row.total)}</strong></span>
          </>
        )}
        <span>缓存命中 <strong>{formatFinOpsNumber(row.cache.hit, "0")}</strong></span>
        <span>缓存未命中 <strong>{formatFinOpsNumber(row.cache.miss, "0")}</strong></span>
        <span>绕过缓存 <strong>{formatFinOpsNumber(row.cache.bypassed, "0")}</strong></span>
        <span>避免 Token <strong>{formatFinOpsNumber(row.cache.avoidedTokens)}</strong></span>
        <span>估算节省 <strong>{formatFinOpsCost(row.cache.estimatedSavings, row.cache.status)}</strong></span>
        {rowEvents.length ? <span>运营事件 <strong>{rowEvents.length} 条</strong></span> : null}
      </ViewportTooltip>
    </div>
  );
}


function TrendBars({
  payload,
  metric = "total",
  comparisonPayload = null,
  events = [],
}) {
  const rows = finopsTrendViewModel(payload);
  const comparisonRows = finopsTrendViewModel(comparisonPayload || {});
  const metricValue = (row) => ({
    cost: row?.cost,
    requests: row?.requests,
    p95: row?.p95,
    total: row?.total,
  }[metric]);
  const formatValue = (value) => {
    if (metric === "cost") return formatFinOpsCost(value, value == null ? "unavailable" : "estimated");
    if (metric === "p95") return formatFinOpsDuration(value);
    return `${formatFinOpsNumber(value, "0")} ${metric === "total" ? "Token" : "次"}`;
  };
  const values = [
    ...rows.map((row) => Number(metricValue(row) || 0)),
    ...comparisonRows.map((row) => Number(metricValue(row) || 0)),
  ];
  const axis = niceFinOpsAxis(values, 4);
  const maximum = axis.max;
  if (!rows.length) return <EmptyState />;
  return (
    <div className="finops-trend-chart">
      <div className="finops-trend-legend">
        {metric === "total" ? (
          <>
            <span><i className="input" />输入</span>
            <span><i className="output" />输出</span>
            <span><i className="cached" />缓存</span>
            <span><i className="reasoning" />推理</span>
          </>
        ) : (
          <span><i className="input" />{{ cost: "估算成本", requests: "调用次数", p95: "P95 延迟" }[metric]}</span>
        )}
      </div>
      <div className="finops-trend-scale" aria-hidden="true">
        {axis.ticks.map((tick) => <span key={tick}>{formatValue(tick)}</span>)}
      </div>
      <div className="finops-trend-columns">
        {rows.slice(-14).map((row, visibleIndex, visibleRows) => {
          const rawValue = metricValue(row);
          const value = Number(rawValue || 0);
          const height = finopsBarPercent(value, maximum);
          const comparisonOffset = Math.max(0, comparisonRows.length - visibleRows.length);
          const comparisonRow = comparisonRows[comparisonOffset + visibleIndex];
          const comparisonValue = Number(metricValue(comparisonRow) || 0);
          const comparisonHeight = comparisonRow
            ? finopsBarPercent(comparisonValue, maximum)
            : 0;
          const rowDate = row.label.slice(0, 10);
          const rowEvents = events.filter((item) => String(item.observed_at || "").startsWith(rowDate));
          const parts = ["input", "output", "cached", "reasoning"].map((key) => ({
            key,
            value: Number(row.series[key] || 0),
          }));
          const partTotal = parts.reduce((sum, item) => sum + item.value, 0) || 1;
          return (
            <TrendColumn
              key={row.bucket}
              row={row}
              metric={metric}
              rawValue={rawValue}
              value={value}
              height={height}
              comparisonRow={comparisonRow}
              comparisonValue={comparisonValue}
              comparisonHeight={comparisonHeight}
              rowEvents={rowEvents}
              parts={parts}
              partTotal={partTotal}
              formatValue={formatValue}
            />
          );
        })}
      </div>
    </div>
  );
}


function BreakdownTable({ rows, dimension = "", onSelect = null, compact = false }) {
  if (!rows.length) return <EmptyState />;
  return (
    <div
      className={`finops-table-scroll ${compact ? "finops-table-scroll-compact" : ""}`.trim()}
      tabIndex={0}
      aria-label="归因表格，可在窄屏横向滚动查看全部列"
    >
      <table className="finops-table">
        <thead>
          <tr>
            <th>维度</th>
            <th>调用</th>
            <th>Token</th>
            <th>估算成本</th>
            <th>错误率</th>
            <th>P95</th>
            <th>缓存命中率</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.key}
              className={dimension && onSelect ? "interactive" : ""}
              tabIndex={dimension && onSelect ? 0 : undefined}
              onClick={() => onSelect?.({ dimension, value: row.key })}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect?.({ dimension, value: row.key });
                }
              }}
            >
              <td><b>{row.key}</b></td>
              <td>{formatFinOpsNumber(row.requests, "0")}</td>
              <td>{formatFinOpsNumber(row.tokens)}</td>
              <td>{formatFinOpsCost(row.cost, row.cost == null ? "unavailable" : "estimated")}</td>
              <td>{formatFinOpsPercent(row.errorRate)}</td>
              <td>{formatFinOpsDuration(row.p95)}</td>
              <td>{formatFinOpsPercent(row.cacheHitRate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function AttentionList({ items, onEvidence = null }) {
  if (!items.length) return <EmptyState>当前没有需要立即关注的运营风险。</EmptyState>;
  return (
    <div className="finops-insights">
      {items.slice(0, 5).map((item) => (
        <div key={`${item.policy_type}:${item.observed_at || ""}`}>
          <AlertTriangle size={15} />
          <span>
            <b>{item.title || item.policy_type}</b>
            <small>观测 {formatFinOpsNumber(item.observed_value)} · 阈值 {formatFinOpsNumber(item.threshold_value)}</small>
          </span>
          <em>{item.severity}</em>
          {onEvidence ? (
            <button type="button" onClick={() => onEvidence({
              reason: item.title || "风险项",
              evidenceRefs: item.evidence_refs || [],
              policyType: item.policy_type || "",
            })}>
              查看证据
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}


function DataTrust({ trust = {} }) {
  const items = [
    {
      id: "pricing",
      label: "计价覆盖",
      value: trust.pricing?.coverage_pct,
      meta: `${formatFinOpsNumber(trust.pricing?.priced_requests, "0")} 已计价 · ${formatFinOpsNumber(trust.pricing?.unpriced_requests, "0")} 未计价`,
      state: trust.pricing?.state,
    },
    {
      id: "tokens",
      label: "Token 覆盖",
      value: trust.tokens?.coverage_pct,
      meta: `${formatFinOpsNumber(trust.tokens?.known_requests, "0")} 已记录 · ${formatFinOpsNumber(trust.tokens?.unknown_requests, "0")} 缺失`,
      state: trust.tokens?.state,
    },
    {
      id: "apim",
      label: CUSTOMER_INFRA_LABELS.reconciliation,
      value: trust.apim?.coverage_pct,
      meta: `${formatFinOpsNumber(trust.apim?.apim_governed_requests, "0")} 入口关联 · ${formatFinOpsNumber(trust.apim?.app_observed_requests, "0")} 应用观测`,
      state: trust.apim?.state,
    },
  ];
  const gateway = gatewayUnmatchedEvidence(trust);
  return (
    <div className="finops-trust-stack">
      <div className="finops-trust-grid">
        {items.map((item) => (
          <article key={item.id}>
            <header>
              <span>{item.label}</span>
              <EvidenceBadge status={item.state} />
            </header>
            <strong>{formatFinOpsPercent(item.value, "暂无样本")}</strong>
            <small>{item.meta}</small>
            <div aria-hidden="true"><i style={{ width: `${Math.max(0, Math.min(100, Number(item.value || 0)))}%` }} /></div>
          </article>
        ))}
      </div>
      {gateway ? <GatewayUnmatchedEvidence evidence={gateway} /> : null}
    </div>
  );
}


function GatewayUnmatchedEvidence({ evidence }) {
  return (
    <section className="finops-gateway-evidence" aria-label="未归属网关证据">
      <header>
        <span className="finops-gateway-evidence-title">
          <ShieldCheck size={14} aria-hidden="true" />
          未归属网关证据
        </span>
        <span className="finops-scope-tag" title="无法可靠归属租户或工作区，按系统范围统计">
          scope={evidence.scope}/system
        </span>
      </header>
      <dl className="finops-gateway-evidence-grid">
        <div>
          <dt>已关联请求</dt>
          <dd>{formatFinOpsNumber(evidence.linkedRequests, "0")}</dd>
        </div>
        <div>
          <dt>未关联网关错误</dt>
          <dd>
            {formatFinOpsNumber(evidence.unmatchedTotal, "0")}
            <small>
              {`4xx ${formatFinOpsNumber(evidence.clientErrors, "0")} · 5xx ${formatFinOpsNumber(evidence.serverErrors, "0")}`}
            </small>
          </dd>
        </div>
        <div>
          <dt>数据更新时间</dt>
          <dd>{formatRelativeUpdateTime(evidence.updatedAt)}</dd>
        </div>
      </dl>
      <p className="finops-gateway-evidence-note">
        {evidence.note
          || "网关侧未关联到任何应用运行的 4xx/5xx 聚合证据，不计入请求账本、错误率或成本。"}
      </p>
    </section>
  );
}


function ExecutiveDonutSlice({ segment, onOpenCost }) {
  const tooltipId = `finops-department-cost-${segment.id}`;
  const { anchorRef, open, anchorProps } = useViewportTooltipAnchor();
  const activate = (event) => {
    if (event.type === "keydown" && !["Enter", " "].includes(event.key)) return;
    if (event.type === "keydown") event.preventDefault();
    onOpenCost();
  };
  return (
    <>
      <circle
        {...anchorProps}
        ref={anchorRef}
        cx="21"
        cy="21"
        r="15.9155"
        fill="none"
        strokeWidth="6"
        className={`segment segment-${segment.colorIndex}`}
        strokeDasharray={`${segment.sharePct} ${Math.max(0, 100 - segment.sharePct)}`}
        strokeDashoffset={25 - segment.offsetPct}
        tabIndex="0"
        role="button"
        aria-label={`${segment.label}，估算成本 ${formatFinOpsCost(segment.value, segment.evidenceState)}，占比 ${formatFinOpsPercent(segment.sharePct)}`}
        aria-describedby={tooltipId}
        onClick={activate}
        onKeyDown={activate}
      />
      <ViewportTooltip anchorRef={anchorRef} open={open} id={tooltipId} variant="finops-donut-tooltip">
        <header><b>{segment.label}</b><EvidenceBadge status={segment.evidenceState} /></header>
        <dl>
          <div><dt>估算成本</dt><dd>{formatFinOpsCost(segment.value, segment.evidenceState)}</dd></div>
          <div><dt>当前占比</dt><dd>{formatFinOpsPercent(segment.sharePct)}</dd></div>
        </dl>
      </ViewportTooltip>
    </>
  );
}


function ExecutiveCostDonut({ composition, onOpenCost }) {
  if (!composition.segments.length) {
    return <EmptyState>当前范围没有可比较的部门成本。</EmptyState>;
  }
  return (
    <div className="finops-executive-donut-layout">
      <div className="finops-executive-donut-wrap">
        <svg
          className="finops-executive-donut"
          viewBox="0 0 42 42"
          role="img"
          aria-label="部门估算成本占比"
        >
          <circle className="track" cx="21" cy="21" r="15.9155" fill="none" strokeWidth="6" />
          {composition.segments.map((segment) => (
            <ExecutiveDonutSlice key={segment.id} segment={segment} onOpenCost={onOpenCost} />
          ))}
        </svg>
        <span>
          <b>{formatFinOpsCost(composition.total, composition.status)}</b>
          <small>部门估算成本</small>
        </span>
      </div>
      <div className="finops-executive-donut-legend">
        {composition.segments.map((segment) => (
          <button
            key={segment.id}
            type="button"
            onClick={onOpenCost}
            title={`${segment.label} · ${formatFinOpsCost(segment.value, segment.evidenceState)}`}
          >
            <i className={`segment-${segment.colorIndex}`} aria-hidden="true" />
            <span>{segment.label}</span>
            <b>{formatFinOpsPercent(segment.sharePct)}</b>
          </button>
        ))}
      </div>
      <button type="button" className="finops-panel-link" onClick={onOpenCost}>
        查看成本分析 <ArrowRight size={13} />
      </button>
    </div>
  );
}


function ExecutiveAttention({ items, onEvidence = null }) {
  if (!items.length) return <EmptyState>当前没有需要立即关注的事项。</EmptyState>;
  return (
    <div className="finops-executive-attention">
      {items.map((item, index) => (
        <article className={`finops-executive-attention-item ${item.tone}`} key={item.id}>
          <span className="finops-executive-attention-rank">{String(index + 1).padStart(2, "0")}</span>
          <span className="finops-executive-attention-copy">
            <b>{item.title}</b>
            <small>{item.detail}</small>
          </span>
          <EvidenceBadge status={item.status} />
          {onEvidence ? (
            <button
              type="button"
              onClick={() => onEvidence({
                reason: item.reason,
                evidenceRefs: item.evidenceRefs,
                policyType: "",
              })}
            >
              证据
            </button>
          ) : null}
        </article>
      ))}
    </div>
  );
}


function OverviewDrilldowns({ onNavigate, onPrefetch }) {
  const items = [
    { id: "cost", label: "成本分析", question: "成本来自哪里？", icon: PieChart },
    { id: "roi", label: "效能与 ROI", question: "投入是否产生价值？", icon: Sparkles },
    { id: "risk", label: "风险与优化", question: "现在应优先处理什么？", icon: AlertTriangle },
  ];
  return (
    <nav className="finops-executive-drilldowns" aria-label="运营分析下钻">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => onNavigate(item.id)}
            {...finopsTabIntentHandlers(item.id, onPrefetch)}
          >
            <span><Icon size={16} /></span>
            <span><b>{item.label}</b><small>{item.question}</small></span>
            <ArrowRight size={15} />
          </button>
        );
      })}
    </nav>
  );
}


function OverviewPage({
  data,
  scope,
  comparison,
  onEvidence = null,
  onAsk = null,
  onConfigurePricing = null,
  onNavigateTab,
  onPrefetchTab,
}) {
  const [trendMetric, setTrendMetric] = useState("cost");
  const view = executiveOverviewView(data);
  const anomalies = Array.isArray(data.anomalies?.items) ? data.anomalies.items : [];
  return (
    <section className="finops-executive-overview" aria-label="运营决策概览">
      <MetricCards cards={view.cards} payload={data.overview} scope={scope} onEvidence={onEvidence} onAsk={onAsk} onConfigurePricing={onConfigurePricing} />
      <div className="finops-executive-decision-grid">
        <Panel title="成本与调用趋势" subtitle="统一零基线，按真实数值比例呈现" className="finops-executive-trend">
          <div className="finops-trend-switch" aria-label="趋势指标">
            {[
              ["cost", "成本"],
              ["requests", "调用"],
              ["total", "Token"],
              ["p95", "P95"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={trendMetric === id ? "active" : ""}
                onClick={() => setTrendMetric(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <TrendBars metric={trendMetric} payload={data.trends} comparisonPayload={comparison} events={anomalies} />
        </Panel>
        <Panel title="部门成本构成" subtitle="当前筛选范围的估算成本">
          <ExecutiveCostDonut composition={view.costComposition} onOpenCost={() => onNavigateTab("cost")} />
        </Panel>
        <Panel title="需要关注" subtitle="当前最值得处理的三项">
          <ExecutiveAttention items={view.attention} onEvidence={onEvidence} />
        </Panel>
      </div>
      <OverviewDrilldowns onNavigate={onNavigateTab} onPrefetch={onPrefetchTab} />
    </section>
  );
}


function BudgetForecast({ payload }) {
  const budget = finopsBudgetView(payload);
  const progress = Math.max(0, Math.min(100, budget.usagePct || 0));
  return (
    <div className={`finops-budget-forecast ${budget.thresholdState}`}>
      <header>
        <span><b>{budget.name}</b><small>请求级估算成本，不代表实际账单</small></span>
        <EvidenceBadge status={budget.status} />
      </header>
      <div className="finops-budget-values">
        <span><small>已使用</small><b>{budget.spentLabel}</b></span>
        <span><small>预算</small><b>{budget.amountLabel}</b></span>
        <span><small>期末预测</small><b>{budget.forecastLabel}</b></span>
      </div>
      <div className="finops-budget-track"><i style={{ width: `${progress}%` }} /></div>
      <footer>
        <span>{budget.usagePct == null ? "预算进度暂不可用" : `已使用 ${formatFinOpsPercent(budget.usagePct)}`}</span>
        <span>预测置信度：{budget.confidence === "complete" ? "完整" : budget.confidence === "partial" ? "部分" : "不可用"}</span>
      </footer>
    </div>
  );
}


function CostPage({
  overviewData,
  detail,
  comparison,
  onDimensionSelect = null,
  onSaveView = null,
  exportUrl = "",
  onConfigurePricing = null,
}) {
  const agents = finopsBreakdownRows({ items: detail.agents?.agents || [] });
  const models = finopsBreakdownRows({ items: detail.agents?.models || [] });
  const summary = executiveCostSummary(overviewData.overview);
  return (
    <>
      <section className="finops-cost-summary" aria-label="成本分析口径">
        <div>
          <small>当前估算成本</small>
          <b>{summary.value}</b>
          <span>{summary.meta}</span>
        </div>
        <p>以下按部门、工作区、Agent 与模型解释成本来源；估算不代表云平台实际账单。</p>
        {onConfigurePricing ? (
          <button type="button" onClick={onConfigurePricing}>
            <Pencil size={14} />维护计价映射
          </button>
        ) : null}
      </section>
      <div className="finops-page-actions">
        <span>{detail.views?.count ? `${detail.views.count} 个已保存视图` : "可保存当前 IT / 财务筛选范围"}</span>
        {onSaveView ? <button type="button" onClick={onSaveView}><BookmarkPlus size={14} />保存财务视图</button> : null}
        {exportUrl ? <a href={exportUrl}><Download size={14} />导出 CSV</a> : null}
      </div>
      <div className="finops-grid">
        <Panel title="成本趋势" subtitle="请求级价目表估算，不代表云平台实际账单" className="span-2">
          <TrendBars payload={overviewData.trends} metric="cost" comparisonPayload={comparison} events={overviewData.anomalies?.items || []} />
        </Panel>
        <Panel title="部门成本归因" subtitle="部门与专案按同一账本口径聚合">
          <BreakdownTable rows={finopsBreakdownRows(overviewData.department)} dimension="department" onSelect={onDimensionSelect} compact />
        </Panel>
        <Panel title="专案成本归因" subtitle="每个 workspace 最多归属一个部门">
          <BreakdownTable rows={finopsBreakdownRows(detail.workspace)} compact />
        </Panel>
        <Panel title="Agent 成本归因" subtitle="Agent 只作为下钻维度">
          <HorizontalBars rows={agents} valueKey="cost" dimension="agent" onSelect={onDimensionSelect} valueFormatter={(value) => formatFinOpsCost(value, value == null ? "unavailable" : "estimated")} />
        </Panel>
        <Panel title="模型成本归因" subtitle="按 deployment 聚合">
          <HorizontalBars rows={models} valueKey="cost" dimension="model" onSelect={onDimensionSelect} valueFormatter={(value) => formatFinOpsCost(value, value == null ? "unavailable" : "estimated")} />
        </Panel>
      </div>
    </>
  );
}


export function RoiScenarioDialog({
  latestScenario = null,
  observedModelCost = null,
  loading = false,
  busy = false,
  error = "",
  onClose,
  onSave,
}) {
  const inputs = latestScenario?.inputs || {};
  const observedCost = typeof observedModelCost === "number" && Number.isFinite(observedModelCost)
    ? observedModelCost
    : "";
  const defaults = {
    hours_saved: inputs.hours_saved ?? "",
    hourly_value: inputs.hourly_value ?? "",
    avoided_loss_or_revenue: inputs.avoided_loss_or_revenue ?? "",
    implementation_cost: inputs.implementation_cost ?? "",
    monthly_fixed_cost: inputs.monthly_fixed_cost ?? "",
    model_cost: inputs.model_cost ?? observedCost,
    evaluation_months: inputs.evaluation_months ?? "",
  };
  const submit = (event) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const number = (name) => Number(values.get(name));
    onSave({
      title: String(values.get("title") || "").trim(),
      currency: "USD",
      hours_saved: number("hours_saved"),
      hourly_value: number("hourly_value"),
      avoided_loss_or_revenue: number("avoided_loss_or_revenue"),
      implementation_cost: number("implementation_cost"),
      monthly_fixed_cost: number("monthly_fixed_cost"),
      model_cost: number("model_cost"),
      evaluation_months: number("evaluation_months"),
      evidence_revision: Number(latestScenario?.revision || 0),
      ...(latestScenario?.scenario_id ? {
        previous_id: latestScenario.scenario_id,
        base_revision: latestScenario.revision,
      } : {}),
    });
  };
  return (
    <div className="finops-model-modal-backdrop" role="presentation" onMouseDown={() => !busy && onClose()}>
      <section
        className="finops-roi-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="finops-roi-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <p>DATAFORGE ROI</p>
            <h2 id="finops-roi-modal-title">调整 ROI 测算参数</h2>
            <span>保存后形成新的不可变版本；估算结果不会替代已验证业务结果。</span>
          </div>
          <button className="icon-button" type="button" aria-label="关闭 ROI 测算" disabled={busy} onClick={onClose}>
            <X size={17} />
          </button>
        </header>
        {loading ? (
          <div className="finops-roi-form-loading" role="status">
            <Loader2 className="spin" size={18} aria-hidden="true" />
            <span><b>正在读取最近一次情景参数…</b><small>读取完成后再填写，避免覆盖刚输入的内容。</small></span>
          </div>
        ) : (
          <form className="finops-roi-form" onSubmit={submit}>
            <fieldset disabled={busy}>
              <label className="wide"><span>情景名称</span><input name="title" maxLength={160} required defaultValue={latestScenario?.title || ""} placeholder="为本次测算命名" /></label>
              <label><span>每月节省工时</span><input name="hours_saved" type="number" min="0" step="0.1" required defaultValue={defaults.hours_saved} /></label>
              <label><span>每小时价值（USD）</span><input name="hourly_value" type="number" min="0" step="0.01" required defaultValue={defaults.hourly_value} /></label>
              <label><span>每月避免损失或新增价值</span><input name="avoided_loss_or_revenue" type="number" min="0" step="0.01" required defaultValue={defaults.avoided_loss_or_revenue} /></label>
              <label><span>一次性实施成本</span><input name="implementation_cost" type="number" min="0" step="0.01" required defaultValue={defaults.implementation_cost} /></label>
              <label><span>每月固定成本</span><input name="monthly_fixed_cost" type="number" min="0" step="0.01" required defaultValue={defaults.monthly_fixed_cost} /></label>
              <label><span>每月模型成本（USD）</span><input name="model_cost" type="number" min="0" step="0.0001" required defaultValue={defaults.model_cost} /></label>
              <label><span>评估周期（月）</span><input name="evaluation_months" type="number" min="1" max="120" step="1" required defaultValue={defaults.evaluation_months} /></label>
            </fieldset>
            <div className="finops-roi-formula wide">
              <b>{defaults.model_cost === "" ? "模型成本尚未记录，请补充后测算" : `当前模型成本 ${formatFinOpsCost(defaults.model_cost, "estimated")} / 月`}</b>
              <span>月度收益 = 节省工时 × 小时价值 + 避免损失；月度成本包含实施成本摊销、固定成本与当前模型成本。</span>
            </div>
            {error ? <p className="finops-roi-form-error wide" role="alert">{error}</p> : null}
            <footer className="wide">
              <button type="button" onClick={onClose} disabled={busy}>取消</button>
              <button type="submit" disabled={busy}>{busy ? "保存中…" : "保存新版本"}</button>
            </footer>
          </form>
        )}
      </section>
    </div>
  );
}


export function finOpsPortalStatusVisibility({
  tab,
  overviewLoading = false,
  overviewError = "",
  hasOverviewMetrics = false,
}) {
  const showOverviewStatus = !["roi", "risk"].includes(tab);
  return {
    showOverviewSkeleton: showOverviewStatus && overviewLoading,
    showOverviewStaleError: showOverviewStatus && Boolean(overviewError) && hasOverviewMetrics,
    showOverviewHardError: showOverviewStatus && Boolean(overviewError) && !hasOverviewMetrics,
  };
}


function EvidenceRequestCard({ detail, index, total }) {
  return (
    <article className="finops-evidence-request-card">
      <header>
        <span>证据 {index + 1} / {total}</span>
        <h3>{detail.title}</h3>
      </header>
      <section>
        <h4>业务信号</h4>
        <dl>
          <div><dt>操作</dt><dd>{detail.operation}</dd></div>
          <div><dt>结果</dt><dd><EvidenceBadge status={detail.status} /></dd></div>
          <div><dt>发生时间</dt><dd>{detail.occurredAt ? new Date(detail.occurredAt).toLocaleString("zh-CN") : "未记录"}</dd></div>
          <div><dt>入口状态</dt><dd>{detail.gatewayCoverage}</dd></div>
        </dl>
      </section>
      <section>
        <h4>运行与缓存</h4>
        <dl>
          <div><dt>响应时间</dt><dd>{detail.latency}</dd></div>
          <div><dt>Token</dt><dd>{formatFinOpsNumber(detail.tokens)}</dd></div>
          <div><dt>估算成本</dt><dd>{detail.cost}</dd></div>
          <div><dt>缓存判定</dt><dd>{detail.cache}</dd></div>
        </dl>
      </section>
      <section className="finops-business-evidence">
        <h4>业务请求 <EvidenceBadge status={detail.businessRequest.status} /></h4>
        <p>{detail.businessRequest.text}</p>
      </section>
      <section className="finops-business-evidence">
        <h4>最终可见回答 <EvidenceBadge status={detail.businessResponse.status} /></h4>
        <p>{detail.businessResponse.text}</p>
      </section>
      <section>
        <h4>处理过程</h4>
        <ol className="finops-evidence-timeline">
          {detail.timeline.map((item, timelineIndex) => (
            <li key={`${item.stage || "stage"}:${timelineIndex}`}>
              <i />
              <span><b>{item.label || "处理阶段"}</b><small>{item.latency_ms == null ? evidenceStatusLabel(item.status) : formatFinOpsDuration(item.latency_ms)}</small></span>
            </li>
          ))}
        </ol>
      </section>
      {detail.technical.items.length ? (
        <details className="finops-technical">
          <summary>技术信息（按需查看）</summary>
          <dl>
            {detail.technical.items.map((item) => (
              <div key={item.key}><dt>{item.label}</dt><dd>{item.value}</dd></div>
            ))}
          </dl>
        </details>
      ) : null}
      <div className="finops-evidence-links">
        {detail.links.foundryTrace ? (
          <a className="finops-monitor-link" href={detail.links.foundryTrace} target="_blank" rel="noreferrer">
            打开{CUSTOMER_INFRA_LABELS.trace} <ExternalLink size={14} />
          </a>
        ) : null}
        {detail.links.azureMonitor ? (
          <a className="finops-monitor-link" href={detail.links.azureMonitor} target="_blank" rel="noreferrer">
            打开{CUSTOMER_INFRA_LABELS.monitor} <ExternalLink size={14} />
          </a>
        ) : null}
      </div>
    </article>
  );
}


function EvidenceDrawer({
  state,
  onClose,
  restoreFocusRef,
}) {
  const drawerRef = useRef(null);
  const closeRef = useRef(null);
  const rawDetails = Array.isArray(state.details) ? state.details : [];
  const details = rawDetails.map((item) => finopsRequestViewModel(item));

  useEffect(() => {
    if (!state.open) return undefined;
    closeRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(
        drawerRef.current.querySelectorAll(
          'a[href], button:not([disabled]), details summary, [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (!focusable.length) {
        event.preventDefault();
        drawerRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      window.requestAnimationFrame(() => restoreFocusRef.current?.focus?.());
    };
  }, [onClose, restoreFocusRef, state.open]);

  if (!state.open) return null;
  return (
    <div
      className="finops-drawer-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        ref={drawerRef}
        className="finops-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="finops-evidence-title"
        tabIndex={-1}
      >
        <header>
          <div>
            <span>请求证据</span>
            <h2 id="finops-evidence-title">{details.length ? `${state.reason} · ${details.length} 条证据` : "正在获取请求证据"}</h2>
            {state.reason ? <p>查看原因：{state.reason}</p> : null}
          </div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="关闭请求证据">
            <X size={17} />
          </button>
        </header>
        <div className="finops-drawer-body">
          {state.loading ? (
            <div className="finops-drawer-state"><Loader2 className="spin" size={18} />正在读取最近的相关请求</div>
          ) : null}
          {!state.loading && state.error ? (
            <div className="finops-drawer-state error"><AlertTriangle size={18} />{state.error}</div>
          ) : null}
          {!state.loading && !state.error && !details.length ? (
            <div className="finops-drawer-state"><Database size={18} />当前筛选范围没有可用请求证据</div>
          ) : null}
          {details.length ? (
            <div className="finops-evidence-request-list">
              {state.details.map((item, index) => (
                <EvidenceRequestCard
                  key={item?.technical_refs?.request_ref || index}
                  detail={finopsRequestViewModel(item)}
                  index={index}
                  total={details.length}
                />
              ))}
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}


export function FinOpsPortal({
  workspaceId = "",
  preloadScopeKey = "",
  dataScope = {},
  permissions = {},
  initialTab = "overview",
  surface = "cost",
  onNavigateRisk = null,
}) {
  const initialWindowRef = useRef(initialWindow());
  const initialCacheRef = useRef(
    preloadScopeKey ? readFinOpsData(preloadScopeKey) : { status: "missing", value: null },
  );
  const initialView = finopsBootstrapViewData(initialCacheRef.current.value || {});
  const [tab, setTab] = useState(initialTab === "risk" ? "risk" : "overview");
  const [windowValue, setWindowValue] = useState(initialWindowRef.current);
  const [filters, setFilters] = useState({ departmentId: "", agentId: "", model: "" });
  const [overviewState, setOverviewState] = useState({
    dataScopeKey: "",
    loading: !initialCacheRef.current.value,
    updating: initialCacheRef.current.status === "stale_usable",
    error: "",
    cacheStatus: initialCacheRef.current.status,
    generatedAt: initialCacheRef.current.value?.freshness?.generated_at || "",
    data: initialView,
  });
  const [filterOptions, setFilterOptions] = useState(initialView.filterOptions);
  const [comparisonEnabled, setComparisonEnabled] = useState(false);
  const [selectedRiskId, setSelectedRiskId] = useState(undefined);
  const [remediationState, setRemediationState] = useState({
    open: false,
    opportunity: null,
    draft: null,
    busy: false,
    error: "",
  });
  const [governance, setGovernance] = useState({ busyId: "", error: "" });
  const [riskMutation, setRiskMutation] = useState({ busyId: "", error: "" });
  const [riskScanState, setRiskScanState] = useState({
    loading: false,
    busy: false,
    error: "",
    scan: null,
  });
  const [assistantState, setAssistantState] = useState({
    context: null,
    openRequest: 0,
  });
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [roiEditorOpen, setRoiEditorOpen] = useState(false);
  const [roiSaveState, setRoiSaveState] = useState({ busy: false, error: "" });
  const [roiDialogState, setRoiDialogState] = useState({
    loading: false,
    latestScenario: null,
    observedModelCost: null,
  });
  const [evidenceState, setEvidenceState] = useState({
    open: false,
    reason: "",
    loading: false,
    error: "",
    details: [],
  });
  const overviewSequence = useRef(0);
  const evidenceController = useRef(null);
  const evidenceTrigger = useRef(null);
  const roiDialogController = useRef(null);
  const remediationSequence = useRef(0);
  const remediationOpportunityRef = useRef(null);
  const remediationTrigger = useRef(null);
  const riskScanController = useRef(null);

  useEffect(() => {
    setTab(initialTab === "risk" ? "risk" : "overview");
  }, [initialTab]);

  const query = useMemo(() => ({
    from: toIso(windowValue.from),
    to: toIso(windowValue.to, true),
    workspaceId,
    departmentId: filters.departmentId,
    agentId: filters.agentId,
    model: filters.model,
  }), [filters, windowValue, workspaceId]);
  const queryScopeKey = useMemo(() => JSON.stringify([
    workspaceId,
    query.from,
    query.to,
    query.departmentId,
    query.agentId,
    query.model,
  ]), [
    query.agentId,
    query.departmentId,
    query.from,
    query.model,
    query.to,
    workspaceId,
  ]);
  const authorizationFingerprint = String(dataScope.authorizationFingerprint || "");
  const defaultScope = (
    windowValue.from === initialWindowRef.current.from
    && windowValue.to === initialWindowRef.current.to
    && !filters.departmentId
    && !filters.agentId
    && !filters.model
  );
  const assistantScope = useMemo(() => ({
    window: {
      from: query.from,
      to: query.to,
    },
    filters: {
      workspaceId,
      departmentId: filters.departmentId,
      agentId: filters.agentId,
      model: filters.model,
    },
  }), [filters, query.from, query.to, workspaceId]);
  const tabKeys = useMemo(() => Object.fromEntries(
    ["overview", "cost", "roi", "risk"].map((item) => [
      item,
      finopsTabDataKey(item, {
        scope: dataScope,
        query,
        defaultScope: item === "overview" && defaultScope,
      }),
    ]),
  ), [dataScope, defaultScope, query]);
  const tabLoaders = useMemo(() => ({
    overview: ({ signal, refresh: force }) => loadFinOpsBootstrap(
      query,
      { signal, refresh: force },
    ),
    cost: ({ signal, refresh: force }) => Promise.all([
      loadFinOpsBreakdowns("workspace", query, { signal, refresh: force }),
      loadFinOpsAgents(query, { signal, refresh: force }),
      loadFinOpsSavedViews({ workspaceId }, { signal, refresh: force }),
    ]).then(([workspace, agents, views]) => ({ workspace, agents, views })),
    roi: ({ signal, refresh: force }) => loadFinOpsRoiDecision(
      query,
      { signal, refresh: force },
    ),
    risk: ({ signal, refresh: force }) => loadFinOpsRiskDecision(
      query,
      { signal, refresh: force },
    ),
  }), [query, workspaceId]);
  const {
    consumeForce,
    manualRefresh: refresh,
    markSuccessful,
    refreshRequests: tabRefreshes,
    requestTabRefresh,
  } = useFinOpsRefreshLifecycle({
    authorizationFingerprint,
    queryScopeKey,
    currentTab: tab,
  });
  const overviewRefresh = tabRefreshes.overview;
  const detailRefresh = tabRefreshes[tab] || { version: 0, force: false, scopeKey: "" };
  const handleDetailSuccess = useCallback((_data, meta) => {
    markSuccessful(tab, meta?.storedAt || undefined);
  }, [markSuccessful, tab]);
  const [detailState, setDetailState] = useFinOpsTabResource({
    enabled: Boolean(workspaceId && tab !== "overview"),
    tab,
    cacheKey: tabKeys[tab],
    loader: tabLoaders[tab],
    scopeKey: queryScopeKey,
    refreshRequest: detailRefresh,
    consumeForce,
    onSuccess: handleDetailSuccess,
    initialState: {
      dataScopeKey: "",
      tab: "",
      loading: false,
      updating: false,
      error: "",
      data: {},
    },
  });
  useEffect(() => {
    if (tab !== "risk" || detailState.dataScopeKey !== queryScopeKey) return;
    const priorityIds = riskDecisionView(detailState.data).priorities
      .map((item) => item?.id)
      .filter(Boolean);
    setSelectedRiskId((current) => {
      if (current === undefined || current === null || priorityIds.includes(current)) return current;
      return priorityIds[0];
    });
  }, [detailState.data, detailState.dataScopeKey, queryScopeKey, tab]);
  const comparisonWindow = useMemo(
    () => previousEqualWindow({ from: query.from, to: query.to }),
    [query.from, query.to],
  );
  const comparisonEligible = Boolean(
    comparisonEnabled
    && workspaceId
    && ["overview", "cost"].includes(tab)
    && comparisonWindow,
  );
  const comparisonKey = useMemo(() => {
    if (!comparisonEligible) return "";
    return finopsDataKey({
      tenantScope: dataScope.tenantScope,
      permissionSummary: [
        ...(dataScope.permissions || []),
        ...(dataScope.authorizedWorkspaceScope || []),
      ],
      workspaceId,
      domain: `${tab}:comparison`,
      window: comparisonWindow,
      filters: {
        departmentId: query.departmentId,
        agentId: query.agentId,
        model: query.model,
      },
      schemaRevision: "finops-comparison-v1",
    });
  }, [comparisonEligible, comparisonWindow, dataScope, query.agentId, query.departmentId, query.model, tab, workspaceId]);
  const comparisonLoader = useCallback(({ signal, refresh: force }) => loadFinOpsTrends("day", {
    ...query,
    from: comparisonWindow?.from,
    to: comparisonWindow?.to,
  }, { signal, refresh: force }), [comparisonWindow, query]);
  const comparisonState = useFinOpsComparisonLifecycle({
    enabled: comparisonEligible,
    tab,
    cacheKey: comparisonKey,
    domain: `${tab}:comparison`,
    loader: comparisonLoader,
    refreshRequest: tab === "overview" ? overviewRefresh : detailRefresh,
    consumeForce,
  });
  useFinOpsIdlePreload({
    enabled: Boolean(
      overviewState.data?.overview?.metrics
      && overviewState.dataScopeKey === queryScopeKey
    ),
    tab: "roi",
    keys: tabKeys,
    loaders: tabLoaders,
  });
  const openAssistant = useCallback((context) => {
    setAssistantState((state) => ({
      context,
      openRequest: state.openRequest + 1,
    }));
  }, []);
  const openRoiAssistant = useCallback((context) => {
    openAssistant(metricContext({
      ...context,
      dataStatus: context?.dataStatus || context?.status || "unavailable",
      evidenceState: context?.evidenceState || context?.status || "unavailable",
    }, assistantScope));
  }, [assistantScope, openAssistant]);
  const selectDimension = useCallback((selection) => {
    setFilters((value) => applyDimensionFilter(value, selection));
  }, []);
  const activeFilterChips = useMemo(() => filterChips({
    departmentId: filters.departmentId,
    agentId: filters.agentId,
    model: filters.model,
  }), [filters]);

  useEffect(() => {
    remediationSequence.current += 1;
    setSelectedRiskId(undefined);
    setRiskMutation({ busyId: "", error: "" });
    remediationOpportunityRef.current = null;
    setRemediationState({ open: false, opportunity: null, draft: null, busy: false, error: "" });
  }, [queryScopeKey]);

  useEffect(() => {
    if (tab !== "risk" || !workspaceId) return undefined;
    riskScanController.current?.abort();
    const controller = new AbortController();
    riskScanController.current = controller;
    setRiskScanState({ loading: true, busy: false, error: "", scan: null });
    const load = async () => {
      try {
        const scan = await loadLatestFinOpsRiskScan(query, { signal: controller.signal });
        setRiskScanState({ loading: false, busy: false, error: "", scan });
      } catch (error) {
        if (error?.name === "AbortError") return;
        const initializeCurrentDemoScope = (
          error?.status === 404
          && workspaceId === "demo-corpus"
          && !query.departmentId
          && !query.agentId
          && !query.model
        );
        if (initializeCurrentDemoScope) {
          try {
            const scan = await runFinOpsRiskScan(query, { signal: controller.signal });
            setRiskScanState({ loading: false, busy: false, error: "", scan });
            return;
          } catch (scanError) {
            if (scanError?.name === "AbortError") return;
            setRiskScanState({
              loading: false,
              busy: false,
              error: scanError instanceof Error ? scanError.message : "风险扫描暂时无法执行",
              scan: null,
            });
            return;
          }
        }
        if (error?.status === 404) {
          setRiskScanState({ loading: false, busy: false, error: "", scan: null });
          return;
        }
        setRiskScanState({
          loading: false,
          busy: false,
          error: error instanceof Error ? error.message : "最近扫描结果读取失败",
          scan: null,
        });
      }
    };
    load();
    return () => controller.abort();
  }, [query, queryScopeKey, tab, workspaceId]);

  const runRiskScan = useCallback(async () => {
    if (!workspaceId || riskScanState.busy) return;
    riskScanController.current?.abort();
    const controller = new AbortController();
    riskScanController.current = controller;
    setRiskScanState((state) => ({ ...state, loading: false, busy: true, error: "" }));
    try {
      const scan = await runFinOpsRiskScan(query, { signal: controller.signal });
      setRiskScanState({ loading: false, busy: false, error: "", scan });
      requestTabRefresh("risk", { force: true });
    } catch (error) {
      if (error?.name === "AbortError") return;
      setRiskScanState((state) => ({
        ...state,
        loading: false,
        busy: false,
        error: error instanceof Error ? error.message : "风险扫描暂时无法执行",
      }));
    }
  }, [query, requestTabRefresh, riskScanState.busy, workspaceId]);
  const loadRoiDialogData = useCallback(async () => {
    roiDialogController.current?.abort();
    const controller = new AbortController();
    roiDialogController.current = controller;
    setRoiDialogState((state) => ({ ...state, loading: true }));
    try {
      const economics = await loadFinOpsRoiEconomics(query, { signal: controller.signal });
      const scenarios = Array.isArray(economics?.scenarios) ? economics.scenarios : [];
      const latestScenario = scenarios.reduce((latest, item) => (
        !latest || Number(item.revision || 0) > Number(latest.revision || 0)
          ? item
          : latest
      ), null);
      const investment = Array.isArray(economics?.funnel)
        ? economics.funnel.find((item) => item?.id === "investment")
        : null;
      const observedModelCost = typeof investment?.value === "number" && Number.isFinite(investment.value)
        ? investment.value
        : null;
      setRoiDialogState({ loading: false, latestScenario, observedModelCost });
    } catch (error) {
      if (error?.name === "AbortError") return;
      setRoiDialogState({ loading: false, latestScenario: null, observedModelCost: null });
      setRoiSaveState({
        busy: false,
        error: "历史参数读取失败，请手动补充本次测算参数后再保存。",
      });
    }
  }, [query]);
  const openRoiEditor = useCallback(() => {
    setRoiSaveState({ busy: false, error: "" });
    setRoiEditorOpen(true);
    loadRoiDialogData();
  }, [loadRoiDialogData]);
  const closeEvidence = useCallback(() => {
    evidenceController.current?.abort();
    evidenceController.current = null;
    setEvidenceState({
      open: false,
      reason: "",
      loading: false,
      error: "",
      details: [],
    });
  }, []);

  const openEvidence = useCallback(async (selection) => {
    if (permissions["finops.request_detail.read"] === false) return;
    const normalized = typeof selection === "string"
      ? { reason: selection, evidenceRefs: [], policyType: "" }
      : {
        reason: String(selection?.reason || "运营证据"),
        evidenceRefs: Array.isArray(selection?.evidenceRefs)
          ? selection.evidenceRefs
          : [],
        policyType: String(selection?.policyType || ""),
        metricId: String(selection?.metricId || ""),
      };
    evidenceController.current?.abort();
    const controller = new AbortController();
    evidenceController.current = controller;
    evidenceTrigger.current = document.activeElement;
    setEvidenceState({
      open: true,
      reason: normalized.reason,
      loading: true,
      error: "",
      details: [],
    });
    try {
      let items = [];
      let requestRefs = normalized.evidenceRefs.slice(0, 3);
      if (!requestRefs.length && (normalized.metricId || normalized.policyType)) {
        const subjectEvidence = await loadFinOpsEvidence(
          { metricId: normalized.metricId, policyType: normalized.policyType },
          query,
          { signal: controller.signal },
        );
        requestRefs = (Array.isArray(subjectEvidence?.items) ? subjectEvidence.items : [])
          .map((item) => item?.request_ref)
          .filter(Boolean)
          .slice(0, 3);
      }
      if (!requestRefs.length) {
        const list = await loadFinOpsRequests(
          { ...query, limit: 20 },
          { signal: controller.signal },
        );
        items = Array.isArray(list?.items) ? list.items : [];
        const requestRef = evidenceRequestRef({
          fallbackItems: [...items].reverse(),
        });
        requestRefs = requestRef ? [requestRef] : [];
      }
      if (!requestRefs.length) {
        setEvidenceState({
          open: true,
          reason: normalized.reason,
          loading: false,
          error: "",
          details: [],
        });
        return;
      }
      const details = await Promise.all(requestRefs.map((requestRef) => (
        loadFinOpsRequest(
          requestRef,
          query,
          { signal: controller.signal },
        )
      )));
      setEvidenceState({
        open: true,
        reason: normalized.reason,
        loading: false,
        error: "",
        details,
      });
    } catch (error) {
      if (error?.name === "AbortError") return;
      setEvidenceState({
        open: true,
        reason: normalized.reason,
        loading: false,
        error: error instanceof Error ? error.message : "请求证据读取失败",
        details: [],
      });
    }
  }, [permissions, query]);

  useEffect(() => {
    const key = tabKeys.overview;
    if (!workspaceId || !key) return undefined;
    const current = ++overviewSequence.current;
    const force = consumeForce("overview", "main", overviewRefresh);
    const lifecycle = loadFinOpsTab({
      tab: "overview",
      key,
      loader: tabLoaders.overview,
      force,
    });
    const cached = lifecycle.cache;
    if (cached.value) {
      const view = finopsBootstrapViewData(cached.value);
      setOverviewState({
        dataScopeKey: queryScopeKey,
        loading: false,
        updating: lifecycle.requested,
        error: cached.lastError ? "上次后台更新未完成，可稍后重试。" : "",
        cacheStatus: cached.status,
        generatedAt: cached.value?.freshness?.generated_at || "",
        data: view,
      });
      setFilterOptions(view.filterOptions);
      if (cached.storedAt) {
        markSuccessful("overview", cached.storedAt);
      }
    } else {
      setOverviewState({
        dataScopeKey: "",
        loading: true,
        updating: false,
        error: "",
        cacheStatus: cached.status,
        generatedAt: "",
        data: {},
      });
    }

    lifecycle.promise.then((payload) => {
      if (current !== overviewSequence.current) return;
      const view = finopsBootstrapViewData(payload);
      markSuccessful("overview");
      setOverviewState({
        dataScopeKey: queryScopeKey,
        loading: false,
        updating: false,
        error: "",
        cacheStatus: "fresh",
        generatedAt: payload?.freshness?.generated_at || "",
        data: view,
      });
      if (defaultScope) setFilterOptions(view.filterOptions);
    }).catch((error) => {
      if (error?.name === "AbortError" || current !== overviewSequence.current) return;
      setOverviewState((state) => ({
        ...state,
        // Stop the skeleton on hard failure so the retry surface is visible.
        loading: false,
        updating: false,
        error: error instanceof Error ? error.message : "运营数据更新失败",
      }));
    });
    return () => {
      if (lifecycle.ownsRequest) {
        cancelFinOpsDataLoad((_entry, entryKey) => entryKey === key);
      }
    };
  }, [consumeForce, defaultScope, markSuccessful, overviewRefresh, queryScopeKey, tabKeys, tabLoaders, workspaceId]);

  useEffect(() => () => {
    evidenceController.current?.abort();
    roiDialogController.current?.abort();
    riskScanController.current?.abort();
  }, []);

  const refreshRiskOnly = useCallback(() => {
    invalidateFinOpsMutation("risk_draft", { workspaceId });
    requestTabRefresh("risk", { force: false });
  }, [requestTabRefresh, workspaceId]);

  const loadCurrentRemediationDraft = useCallback(async ({ conflictMessage = "" } = {}) => {
    const opportunity = remediationOpportunityRef.current;
    if (!workspaceId || !opportunity?.id) return;
    const current = ++remediationSequence.current;
    setRemediationState((state) => ({ ...state, open: true, opportunity, busy: true, error: conflictMessage }));
    try {
      const list = await loadFinOpsRemediationDrafts({ workspaceId });
      const candidates = Array.isArray(list?.items) ? list.items : [];
      const stored = candidates.find((item) => (
        item?.source_opportunity_id === opportunity.id
        && item?.status !== "closed"
      ));
      const draft = stored?.draft_id
        ? await loadFinOpsRemediationDraft(stored.draft_id)
        : {
          workspace_id: workspaceId,
          source_opportunity_id: opportunity.id,
          base_version: opportunity.baseVersion,
          title: opportunity.label,
        };
      if (current !== remediationSequence.current) return;
      setRemediationState({
        open: true,
        opportunity,
        draft,
        busy: false,
        error: conflictMessage,
      });
      return draft;
    } catch (error) {
      if (current !== remediationSequence.current) return;
      setRemediationState((state) => ({
        ...state,
        open: true,
        opportunity,
        busy: false,
        error: conflictMessage || (error instanceof Error ? error.message : "整改草案读取失败"),
      }));
      return null;
    }
  }, [workspaceId]);

  const openRemediation = useCallback((opportunity) => {
    remediationTrigger.current = document.activeElement;
    remediationOpportunityRef.current = opportunity;
    setRemediationState({ open: true, opportunity, draft: null, busy: true, error: "" });
    loadCurrentRemediationDraft();
  }, [loadCurrentRemediationDraft]);

  const refreshCurrentRemediationOpportunity = useCallback(async ({ opportunityId, message }) => {
    const current = ++remediationSequence.current;
    setDetailState((state) => (
      state.tab === "risk" && state.dataScopeKey === queryScopeKey
        ? { ...state, updating: true, error: "" }
        : state
    ));
    try {
      const payload = await loadFinOpsTab({
        tab: "risk",
        key: tabKeys.risk,
        loader: tabLoaders.risk,
        force: true,
      }).promise;
      if (current !== remediationSequence.current) return null;
      markSuccessful("risk");
      const opportunity = riskDecisionView(payload).priorities.find((item) => (
        item.id === opportunityId && item.baseVersion
      ));
      setDetailState({
        dataScopeKey: queryScopeKey,
        tab: "risk",
        loading: false,
        updating: false,
        error: "",
        data: payload,
      });
      if (!opportunity) {
        remediationOpportunityRef.current = null;
        setRemediationState((state) => ({
          ...state,
          open: true,
          draft: {
            workspace_id: workspaceId,
            source_opportunity_id: opportunityId,
            base_version: "",
            title: state.opportunity?.label || "整改草案",
          },
          busy: false,
          error: REMEDIATION_RESELECT_MESSAGE,
        }));
        return null;
      }
      const draft = {
        workspace_id: workspaceId,
        source_opportunity_id: opportunity.id,
        base_version: opportunity.baseVersion,
        title: opportunity.label,
      };
      remediationOpportunityRef.current = opportunity;
      setRemediationState({
        open: true,
        opportunity,
        draft,
        busy: false,
        error: message,
      });
      return opportunity;
    } catch {
      if (current !== remediationSequence.current) return null;
      remediationOpportunityRef.current = null;
      setDetailState((state) => ({ ...state, updating: false }));
      setRemediationState((state) => ({
        ...state,
        open: true,
        draft: {
          workspace_id: workspaceId,
          source_opportunity_id: opportunityId,
          base_version: "",
          title: state.opportunity?.label || "整改草案",
        },
        busy: false,
        error: REMEDIATION_RESELECT_MESSAGE,
      }));
      return null;
    }
  }, [markSuccessful, queryScopeKey, tabKeys, tabLoaders, workspaceId]);

  const runRemediation = async (kind, payload = {}) => {
    const opportunity = remediationOpportunityRef.current;
    const view = remediationDraftView(remediationState.draft);
    setRemediationState((state) => ({ ...state, busy: true, error: "" }));
    const result = await orchestrateRemediationMutation({
      kind,
      workspaceId,
      opportunity,
      draft: view,
      reason: payload?.reason,
      clients: {
        create: createFinOpsRemediationDraft,
        review: reviewFinOpsRemediationDraft,
        promote: promoteFinOpsRemediationDraft,
      },
      reloadLatest: (message) => loadCurrentRemediationDraft({ conflictMessage: message }),
      refreshOpportunity: refreshCurrentRemediationOpportunity,
      refreshRisk: refreshRiskOnly,
    });
    if (result.status === "succeeded") {
      setRemediationState((state) => ({ ...state, open: true, draft: result.response, busy: false, error: "" }));
    } else if (result.status === "failed") {
      setRemediationState((state) => ({ ...state, busy: false, error: result.error }));
    }
  };

  const createRemediation = () => runRemediation("create");
  const reviewRemediation = (payload) => runRemediation("review", payload);
  const promoteRemediation = (payload) => runRemediation("promote", payload);

  const manageAnomaly = async (item, operation) => {
    if (!item?.anomalyId || riskMutation.busyId) return;
    let reason = "";
    if (operation === "suppress") {
      reason = window.prompt("请输入抑制原因（将写入治理审计）", "")?.trim() || "";
      if (!reason) return;
    }
    setRiskMutation({ busyId: item.anomalyId, error: "" });
    try {
      if (operation === "acknowledge") await acknowledgeFinOpsAnomaly(item.anomalyId);
      else await suppressFinOpsAnomaly(item.anomalyId, reason);
      refreshRiskOnly();
      setRiskMutation({ busyId: "", error: "" });
    } catch (error) {
      setRiskMutation({ busyId: "", error: "异常治理操作失败" });
    }
  };

  const saveCurrentView = async () => {
    setGovernance({ busyId: "save-view", error: "" });
    try {
      await createFinOpsSavedView({
        name: `财务视图 ${windowValue.from} 至 ${windowValue.to}`,
        audience: "finance",
        tab: "cost",
        filters: {
          workspace_id: workspaceId,
          ...(filters.departmentId ? { department_id: filters.departmentId } : {}),
          ...(filters.agentId ? { agent_id: filters.agentId } : {}),
          ...(filters.model ? { model: filters.model } : {}),
        },
      });
      invalidateFinOpsMutation("saved_cost_view", { workspaceId });
      requestTabRefresh("cost", { force: false });
      setGovernance({ busyId: "", error: "" });
    } catch (error) {
      setGovernance({
        busyId: "",
        error: error instanceof Error ? error.message : "视图保存失败",
      });
    }
  };

  const saveRoiScenario = async (payload) => {
    setRoiSaveState({ busy: true, error: "" });
    try {
      await createWorkspaceRoiScenario(workspaceId, payload);
      setRoiEditorOpen(false);
      setRoiSaveState({ busy: false, error: "" });
      setRoiDialogState({ loading: false, latestScenario: null, observedModelCost: null });
      invalidateFinOpsMutation("roi_scenario", { workspaceId });
      requestTabRefresh("roi", { force: false });
    } catch (error) {
      if (error?.status === 409) {
        setRoiSaveState({
          busy: false,
          error: "情景已由其他会话更新，正在重新载入最新版本，请确认后再次保存。",
        });
        requestTabRefresh("roi", { force: true });
        loadRoiDialogData();
        return;
      }
      setRoiSaveState({
        busy: false,
        error: error instanceof Error ? error.message : "ROI 情景保存失败",
      });
    }
  };

  const prefetchTab = useCallback((targetTab) => prefetchFinOpsTab(targetTab, {
    keys: tabKeys,
    loaders: tabLoaders,
  }).catch((error) => {
    if (error?.name !== "AbortError") console.warn("Operations tab preload failed", error);
    return null;
  }), [tabKeys, tabLoaders]);
  const activateTab = useCallback((targetTab) => {
    if (targetTab === "risk" && surface !== "risk" && onNavigateRisk) {
      onNavigateRisk();
      return;
    }
    if (targetTab !== "overview") {
      const cached = readFinOpsData(tabKeys[targetTab]);
      if (cached.value) {
        setDetailState({
          dataScopeKey: queryScopeKey,
          tab: targetTab,
          loading: false,
          updating: cached.status === "stale_usable",
          error: cached.lastError ? "上次后台更新未完成，可稍后重试。" : "",
          data: cached.value,
        });
        if (cached.storedAt) {
          markSuccessful(targetTab, cached.storedAt);
        }
      }
    }
    setTab(targetTab);
  }, [markSuccessful, onNavigateRisk, queryScopeKey, surface, tabKeys]);
  const handleModelSettingsChanged = useCallback((kind) => {
    invalidateFinOpsMutation(kind === "price" ? "price_setting" : "model_setting", {
      workspaceId,
    });
    requestTabRefresh(tab, { force: false });
  }, [requestTabRefresh, tab, workspaceId]);

  const generatedAt = overviewState.generatedAt || overviewState.data?.overview?.freshness?.generated_at;
  const overviewDataStatus = overviewState.data?.overview?.data_status || "unavailable";
  const generalAssistantContext = useMemo(() => {
    const descriptor = generalAssistantDescriptor(tab);
    return metricContext({
    id: descriptor.id,
    label: descriptor.label,
    value: null,
    unit: "",
    kind: descriptor.kind,
    dataStatus: overviewDataStatus,
    evidenceState: overviewDataStatus === "complete"
      ? "observed"
      : overviewDataStatus === "partial"
        ? "partial"
        : "unavailable",
  }, assistantScope);
  }, [assistantScope, overviewDataStatus, tab]);
  const visibleTabs = (surface === "risk" ? [] : FINOPS_TABS).filter((item) => {
    if (item.id === "risk") return false;
    if (item.id === "cost") return permissions["finops.cost.read"] !== false;
    if (item.id === "roi") return permissions["finops.roi.read"] !== false;
    return true;
  });
  const hasDetailData = (
    tab !== "overview"
    && detailState.tab === tab
    && Object.keys(detailState.data || {}).length > 0
  );
  const showDetailLoading = (
    tab !== "overview"
    && detailState.loading
    && !hasDetailData
  );
  const portalStatusVisibility = finOpsPortalStatusVisibility({
    tab,
    overviewLoading: overviewState.loading,
    overviewError: overviewState.error,
    hasOverviewMetrics: Boolean(overviewState.data?.overview?.metrics),
  });
  const pageGeneratedAt = ["roi", "risk"].includes(tab)
    ? detailState.data?.freshness?.generated_at
    : generatedAt;
  const pageUpdating = ["roi", "risk"].includes(tab)
    ? detailState.updating
    : overviewState.updating || detailState.updating;
  const canOpenEvidence = permissions["finops.request_detail.read"] !== false;
  const pageTitle = surface === "risk" ? "风险与优化" : "成本管理";
  const pageDescription = surface === "risk"
    ? "扫描运行证据、解释判定依据，并把建议与生产动作保持分离。"
    : "让 IT 与财务在同一视图理解成本、效能与投入价值。";

  return (
    <main className="finops-page">
      <header className="finops-head">
        <div className="finops-head-copy">
          <p>AI OPERATIONS</p>
          <h1>{pageTitle}</h1>
          <span>{pageDescription}</span>
        </div>
        <div className="finops-live">
          <span>{formatRelativeUpdateTime(pageGeneratedAt)}</span>
          <button
            type="button"
            onClick={refresh}
            disabled={pageUpdating}
            aria-label={pageUpdating ? "数据更新中" : "刷新运营数据"}
            title={pageUpdating ? "数据更新中" : "刷新运营数据"}
          >
            {pageUpdating ? <Loader2 className="spin" size={14} /> : <RefreshCw size={14} />}
            <span>{pageUpdating ? "更新中" : "刷新"}</span>
          </button>
        </div>
      </header>

      <section className="finops-toolbar">
        <div className="finops-date-range">
          <Clock3 size={15} />
          <input type="date" value={windowValue.from} onChange={(event) => setWindowValue((value) => ({ ...value, from: event.target.value }))} />
          <span>至</span>
          <input type="date" value={windowValue.to} onChange={(event) => setWindowValue((value) => ({ ...value, to: event.target.value }))} />
        </div>
        <select aria-label="部门筛选" value={filters.departmentId} onChange={(event) => setFilters((value) => ({ ...value, departmentId: event.target.value }))}>
          <option value="">全部部门</option>
          {(filterOptions?.filters?.departments || []).map((item) => <option key={item} value={item === "unassigned" ? "" : item}>{item === "unassigned" ? "未归属" : item}</option>)}
        </select>
        <select aria-label="Agent 筛选" value={filters.agentId} onChange={(event) => setFilters((value) => ({ ...value, agentId: event.target.value }))}>
          <option value="">全部 Agent</option>
          {(filterOptions?.filters?.agents || []).map((item) => <option key={item}>{item}</option>)}
        </select>
        <select aria-label="模型筛选" value={filters.model} onChange={(event) => setFilters((value) => ({ ...value, model: event.target.value }))}>
          <option value="">全部模型</option>
          {(filterOptions?.filters?.models || []).map((item) => <option key={item}>{item}</option>)}
        </select>
        <button
          type="button"
          className={`finops-compare-toggle ${comparisonEnabled ? "active" : ""}`}
          aria-pressed={comparisonEnabled}
          onClick={() => setComparisonEnabled((value) => !value)}
        >
          {comparisonState.loading ? <Loader2 className="spin" size={13} /> : null}
          对比上一周期
        </button>
      </section>
      {activeFilterChips.length ? (
        <div className="finops-filter-chips" aria-label="当前筛选">
          <span>当前范围</span>
          {activeFilterChips.map((chip) => (
            <button
              type="button"
              key={chip.key}
              onClick={() => setFilters((value) => ({ ...value, [chip.key]: "" }))}
              aria-label={`移除${chip.label}筛选 ${chip.value}`}
            >
              {chip.label}：{chip.value}
              <X size={12} />
            </button>
          ))}
          <button type="button" className="clear" onClick={() => setFilters({ departmentId: "", agentId: "", model: "" })}>
            清除全部
          </button>
        </div>
      ) : null}

      {visibleTabs.length ? <nav className="finops-tabs" aria-label="成本管理页面">
        {visibleTabs.map((item) => {
          const Icon = TAB_ICONS[item.id];
          return (
            <button
              key={item.id}
              type="button"
              className={tab === item.id ? "active" : ""}
              onClick={() => activateTab(item.id)}
              {...finopsTabIntentHandlers(item.id, prefetchTab)}
            >
              <Icon size={15} />
              {item.label}
            </button>
          );
        })}
      </nav> : null}

      <section className="finops-content" aria-busy={portalStatusVisibility.showOverviewSkeleton || showDetailLoading || detailState.updating ? "true" : "false"}>
        {portalStatusVisibility.showOverviewSkeleton ? <MetricSkeleton /> : null}
        {portalStatusVisibility.showOverviewStaleError
          ? <div className="finops-inline-error">更新失败，已保留上次数据：{overviewState.error}</div>
          : null}
        {portalStatusVisibility.showOverviewHardError
          ? <div className="finops-state finops-state-error"><AlertTriangle size={18} /><span>{overviewState.error}</span><button type="button" onClick={refresh}>重试</button></div>
          : null}
        {!overviewState.loading && overviewState.data?.overview?.metrics && tab === "overview"
          ? <OverviewPage data={overviewState.data} scope={assistantScope} comparison={comparisonState.data} onEvidence={canOpenEvidence ? openEvidence : null} onAsk={openAssistant} onConfigurePricing={() => setModelSettingsOpen(true)} onNavigateTab={activateTab} onPrefetchTab={prefetchTab} />
          : null}
        {showDetailLoading && !["roi", "risk"].includes(tab) ? <div className="finops-section-loading"><Loader2 className="spin" size={18} />正在读取当前页面</div> : null}
        {!showDetailLoading && !["roi", "risk"].includes(tab) && detailState.error && hasDetailData
          ? <div className="finops-inline-error">更新失败，已保留上次数据：{detailState.error}</div>
          : null}
        {!showDetailLoading && !["roi", "risk"].includes(tab) && detailState.error && !hasDetailData ? <div className="finops-state finops-state-error"><AlertTriangle size={18} /><span>{detailState.error}</span><button type="button" onClick={refresh}>重试</button></div> : null}
        {!showDetailLoading && hasDetailData && tab === "cost"
          ? <CostPage overviewData={overviewState.data} detail={detailState.data} scope={assistantScope} comparison={comparisonState.data} onEvidence={canOpenEvidence ? openEvidence : null} onAsk={openAssistant} onDimensionSelect={selectDimension} onSaveView={governance.busyId === "save-view" ? null : saveCurrentView} exportUrl={finOpsExportUrl("workspace", query)} onConfigurePricing={() => setModelSettingsOpen(true)} />
          : null}
        {tab === "roi" ? (
          <RoiDecisionPage
            payload={hasDetailData ? detailState.data : null}
            loading={detailState.loading || (!hasDetailData && !detailState.error)}
            updating={detailState.updating}
            error={detailState.error}
            onRetry={() => requestTabRefresh("roi", { force: true })}
            onAdjustScenario={openRoiEditor}
            onEvidence={canOpenEvidence ? openEvidence : null}
            onAsk={openRoiAssistant}
          />
        ) : null}
        {tab === "risk" ? (
          <RiskDecisionPage
            payload={hasDetailData ? detailState.data : null}
            loading={detailState.loading || (!hasDetailData && !detailState.error)}
            updating={detailState.updating}
            error={detailState.error}
            mutationError={riskMutation.error}
            busyId={riskMutation.busyId}
            selectedRiskId={selectedRiskId}
            onSelectRisk={setSelectedRiskId}
            onRetry={() => requestTabRefresh("risk", { force: true })}
            onEvidence={canOpenEvidence ? openEvidence : null}
            onAsk={openRoiAssistant}
            onCreateDraft={openRemediation}
            onAcknowledge={(item) => manageAnomaly(item, "acknowledge")}
            onSuppress={(item) => manageAnomaly(item, "suppress")}
            scan={riskScanState.scan}
            scanLoading={riskScanState.loading}
            scanBusy={riskScanState.busy}
            scanError={riskScanState.error}
            onRunScan={runRiskScan}
          />
        ) : null}
      </section>

      <footer className="finops-footnote">
        <WalletCards size={14} />
        <span>{surface === "risk" ? "扫描只读取当前授权范围内的运行证据，不会自动执行生产变更。" : "成本为 DataForge 价目表估算，不代表云平台实际账单；缺失证据不会补造数据。"}</span>
      </footer>
      <EvidenceDrawer
        state={evidenceState}
        onClose={closeEvidence}
        restoreFocusRef={evidenceTrigger}
      />
      <FinOpsAssistant
        context={assistantState.context || generalAssistantContext}
        openRequest={assistantState.openRequest}
        onClearContext={() => setAssistantState((state) => ({ ...state, context: null }))}
        onEvidence={canOpenEvidence ? openEvidence : null}
      />
      {remediationState.open ? (
        <RemediationDraftPanel
          draft={remediationState.draft}
          busy={remediationState.busy}
          error={remediationState.error}
          actionsEnabled={Boolean(detailState.data?.governance_capability?.actions_enabled)}
          restoreFocusRef={remediationTrigger}
          onClose={() => {
            remediationSequence.current += 1;
            setRemediationState((state) => ({ ...state, open: false, busy: false }));
          }}
          onCreate={createRemediation}
          onReload={() => loadCurrentRemediationDraft()}
          onReview={reviewRemediation}
          onPromote={promoteRemediation}
        />
      ) : null}
      {roiEditorOpen ? (
        <RoiScenarioDialog
          key={`${roiDialogState.latestScenario?.scenario_id || "new"}:${roiDialogState.latestScenario?.revision || 0}`}
          latestScenario={roiDialogState.latestScenario}
          observedModelCost={roiDialogState.observedModelCost}
          loading={roiDialogState.loading}
          busy={roiSaveState.busy}
          error={roiSaveState.error}
          onClose={() => {
            if (roiSaveState.busy) return;
            roiDialogController.current?.abort();
            setRoiEditorOpen(false);
            setRoiSaveState({ busy: false, error: "" });
            setRoiDialogState({ loading: false, latestScenario: null, observedModelCost: null });
          }}
          onSave={saveRoiScenario}
        />
      ) : null}
      {modelSettingsOpen ? (
        <div className="finops-model-modal-backdrop" role="presentation" onMouseDown={() => setModelSettingsOpen(false)}>
          <section className="finops-model-modal" role="dialog" aria-modal="true" aria-labelledby="finops-model-modal-title" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>MODEL & COST</p>
                <h2 id="finops-model-modal-title">模型分配与官方价格</h2>
                <span>配置保存后，新请求会按 Agent 和模型进入运营统计。</span>
              </div>
              <button className="icon-button" type="button" aria-label="关闭模型配置" onClick={() => setModelSettingsOpen(false)}><X size={17} /></button>
            </header>
            <ModelRoutingPage
              workspaceId={workspaceId}
              embedded
              onSettingsChanged={handleModelSettingsChanged}
            />
          </section>
        </div>
      ) : null}
    </main>
  );
}
