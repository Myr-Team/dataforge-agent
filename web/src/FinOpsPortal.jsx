import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BookmarkPlus,
  Clock3,
  CircleHelp,
  Database,
  Download,
  ExternalLink,
  Gauge,
  Loader2,
  Pencil,
  RefreshCw,
  ShieldCheck,
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
  loadFinOpsRemediationDraft,
  loadFinOpsRemediationDrafts,
  loadFinOpsRequest,
  loadFinOpsRequests,
  loadFinOpsRiskDecision,
  loadFinOpsRoiDecision,
  loadFinOpsRoiEconomics,
  loadFinOpsSavedViews,
  loadFinOpsTrends,
  promoteFinOpsRemediationDraft,
  reviewFinOpsRemediationDraft,
  suppressFinOpsAnomaly,
} from "./api.js";
import {
  prefetchFinOpsBootstrap,
  readFinOpsBootstrap,
} from "./finopsPreload.js";
import { invalidateFinOpsData } from "./finopsDataStore.js";
import { FinOpsAssistant } from "./FinOpsAssistant.jsx";
import { ModelRoutingPage } from "./ModelRoutingPage.jsx";
import { RemediationDraftPanel } from "./finops/RemediationDraftPanel.jsx";
import { RiskDecisionPage } from "./finops/RiskDecisionPage.jsx";
import { RoiDecisionPage } from "./finops/RoiDecisionPage.jsx";
import {
  REMEDIATION_RESELECT_MESSAGE,
  orchestrateRemediationMutation,
} from "./finops/remediationOrchestration.js";
import { remediationDraftView, riskDecisionView } from "./finopsDecisionViewModel.js";
import {
  applyDimensionFilter,
  filterChips,
  metricContext,
  metricTooltip,
  previousEqualWindow,
} from "./finopsInteraction.js";
import {
  CUSTOMER_INFRA_LABELS,
  FINOPS_REFRESH_MS,
  FINOPS_TABS,
  finopsBootstrapViewData,
  finopsBudgetView,
  finopsBreakdownRows,
  finopsDoughnutSegments,
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


function EvidenceBadge({ status }) {
  const normalized = String(status || "unavailable").toLowerCase();
  const label = {
    available: "完整",
    complete: "完整",
    observed: "已观测",
    measured: "已记录",
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
    ready: "分析完成",
    insufficient_data: "证据不足",
  }[normalized] || normalized;
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


function MetricCards({
  payload,
  scope,
  onEvidence = null,
  onAsk = null,
  onConfigurePricing = null,
}) {
  return (
    <section className="finops-metrics" aria-label="运营核心指标">
      {finopsMetricCards(payload).map((card) => {
        const tooltip = metricTooltip(card.metric);
        const context = metricContext(card.metric, scope);
        const tooltipId = `finops-metric-tooltip-${card.id}`;
        return (
          <article
            className={`finops-metric ${card.tone}`}
            key={card.id}
            aria-label={`${card.label} ${card.value}`}
          >
            <div className="finops-metric-header">
              <span className="finops-metric-label">
                <span>{card.label}</span>
                <button
                  className="finops-help-trigger"
                  type="button"
                  aria-label={`${card.label}说明`}
                  aria-describedby={tooltipId}
                >
                  <CircleHelp size={13} aria-hidden="true" />
                </button>
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
                <button type="button" onClick={() => onEvidence(`${card.label}指标`)}>
                  查看证据
                </button>
              ) : null}
            </div>
            <div className="finops-metric-tooltip" id={tooltipId} role="tooltip">
              <header><b>{tooltip.title}</b><EvidenceBadge status={tooltip.evidenceState} /></header>
              {tooltip.rows.length ? (
                <dl>
                  {tooltip.rows.map((row) => (
                    <div key={row.label}><dt>{row.label}</dt><dd>{formatMetricTooltipValue(row)}</dd></div>
                  ))}
                </dl>
              ) : <p>当前指标暂无更多可复核明细。</p>}
              <small>当前筛选范围 · {tooltip.dataStatus === "complete" ? "数据完整" : tooltip.dataStatus === "partial" ? "数据不完整" : "待接入"}</small>
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
      {Array.from({ length: 8 }, (_, index) => (
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
            <div
              className="finops-trend-column"
              key={row.bucket}
              tabIndex={0}
              aria-label={`${row.label}，${formatValue(rawValue)}`}
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
              <div className="finops-trend-tooltip" role="tooltip">
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
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


function BreakdownTable({ rows, dimension = "", onSelect = null }) {
  if (!rows.length) return <EmptyState />;
  return (
    <div className="finops-table-scroll">
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


function OverviewPage({
  data,
  scope,
  comparison,
  onEvidence = null,
  onAsk = null,
  onDimensionSelect = null,
  onConfigurePricing = null,
}) {
  const [trendMetric, setTrendMetric] = useState("total");
  const departmentRows = finopsBreakdownRows(data.department);
  const anomalies = Array.isArray(data.anomalies?.items) ? data.anomalies.items : [];
  return (
    <>
      <MetricCards payload={data.overview} scope={scope} onEvidence={onEvidence} onAsk={onAsk} onConfigurePricing={onConfigurePricing} />
      <div className="finops-grid finops-grid-wide">
        <Panel title="使用与成本趋势" subtitle="统一零基线，数值与柱高按真实数据比例呈现" className="span-2">
          <div className="finops-trend-switch" aria-label="趋势指标">
            {[
              ["total", "Token"],
              ["requests", "调用"],
              ["cost", "成本"],
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
        <Panel title="数据可信度" subtitle="明确哪些数字已记录、已计价并完成请求对账">
          <DataTrust trust={data.overview?.trust || data.trust || {}} />
        </Panel>
        <Panel title="需要关注" subtitle="仅显示可下钻或可修正的事项">
          <AttentionList items={anomalies} onEvidence={onEvidence} />
        </Panel>
        <Panel title="部门成本与运行质量" subtitle="未映射 workspace 统一进入“未归属”">
          <BreakdownTable rows={departmentRows} dimension="department" onSelect={onDimensionSelect} />
        </Panel>
      </div>
    </>
  );
}


function AllocationDoughnut({ rows, title }) {
  const segments = finopsDoughnutSegments(rows, "cost");
  if (!segments.length) return <EmptyState>当前范围没有可计价的分摊数据。</EmptyState>;
  let cursor = 0;
  const gradient = segments.map((segment) => {
    const start = cursor;
    cursor += segment.sharePct;
    return `var(--finops-chart-${segment.colorIndex}) ${start}% ${cursor}%`;
  }).join(", ");
  return (
    <div className="finops-doughnut-layout">
      <div
        className="finops-doughnut"
        style={{ background: `conic-gradient(${gradient})` }}
        role="img"
        aria-label={`${title}，${segments.map((item) => `${item.key} ${item.sharePct}%`).join("，")}`}
      >
        <span><b>{segments.length}</b><small>个分类</small></span>
      </div>
      <div className="finops-doughnut-legend">
        {segments.slice(0, 6).map((item) => (
          <div key={item.key}>
            <i style={{ background: `var(--finops-chart-${item.colorIndex})` }} />
            <span title={item.key}>{item.key}</span>
            <b>{item.sharePct}%</b>
          </div>
        ))}
      </div>
    </div>
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
  scope,
  comparison,
  onEvidence = null,
  onAsk = null,
  onDimensionSelect = null,
  onSaveView = null,
  exportUrl = "",
  onConfigurePricing = null,
}) {
  const agents = finopsBreakdownRows({ items: detail.agents?.agents || [] });
  const models = finopsBreakdownRows({ items: detail.agents?.models || [] });
  return (
    <>
      <MetricCards payload={overviewData.overview} scope={scope} onEvidence={onEvidence} onAsk={onAsk} onConfigurePricing={onConfigurePricing} />
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
          <BreakdownTable rows={finopsBreakdownRows(overviewData.department)} dimension="department" onSelect={onDimensionSelect} />
        </Panel>
        <Panel title="专案成本归因" subtitle="每个 workspace 最多归属一个部门">
          <BreakdownTable rows={finopsBreakdownRows(detail.workspace)} />
        </Panel>
        <Panel title="Agent 成本归因" subtitle="Agent 只作为下钻维度">
          <HorizontalBars rows={agents} valueKey="cost" dimension="agent" onSelect={onDimensionSelect} valueFormatter={(value) => formatFinOpsCost(value, value == null ? "unavailable" : "estimated")} />
        </Panel>
        <Panel title="模型成本归因" subtitle="按 deployment 聚合">
          <HorizontalBars rows={models} valueKey="cost" dimension="model" onSelect={onDimensionSelect} valueFormatter={(value) => formatFinOpsCost(value, value == null ? "unavailable" : "estimated")} />
        </Panel>
        <Panel title="Agent 成本结构" subtitle="分类占比使用同一估算账本">
          <AllocationDoughnut rows={agents} title="Agent 成本结构" />
        </Panel>
        <Panel title="模型成本结构" subtitle="单一分类仍保留完整圆环">
          <AllocationDoughnut rows={models} title="模型成本结构" />
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


export function scheduleRoiOnlyRefresh({ invalidate, forceRef, bump }) {
  invalidate((entry) => entry?.domain === "roi");
  forceRef.current = true;
  bump();
}


export function scheduleRiskOnlyRefresh({ invalidate, forceRef, bump }) {
  invalidate((entry) => entry?.domain === "risk");
  forceRef.current = true;
  bump();
}


function EvidenceDrawer({
  state,
  onClose,
  restoreFocusRef,
}) {
  const drawerRef = useRef(null);
  const closeRef = useRef(null);
  const detail = state.detail ? finopsRequestViewModel(state.detail) : null;

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
            <h2 id="finops-evidence-title">{detail?.title || "正在获取请求证据"}</h2>
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
          {!state.loading && !state.error && !detail ? (
            <div className="finops-drawer-state"><Database size={18} />当前筛选范围没有可用请求证据</div>
          ) : null}
          {detail ? (
            <>
              <section>
                <h3>请求概况</h3>
                <dl>
                  <div><dt>操作</dt><dd>{detail.operation}</dd></div>
                  <div><dt>状态</dt><dd><EvidenceBadge status={detail.status} /></dd></div>
                  <div><dt>发生时间</dt><dd>{detail.occurredAt ? new Date(detail.occurredAt).toLocaleString("zh-CN") : "未记录"}</dd></div>
                  <div><dt>网关覆盖</dt><dd>{detail.gatewayCoverage}</dd></div>
                </dl>
              </section>
              <section>
                <h3>运行指标</h3>
                <dl>
                  <div><dt>响应时间</dt><dd>{detail.latency}</dd></div>
                  <div><dt>Token</dt><dd>{formatFinOpsNumber(detail.tokens)}</dd></div>
                  <div><dt>估算成本</dt><dd>{detail.cost}</dd></div>
                  <div><dt>缓存</dt><dd>{detail.cache}</dd></div>
                </dl>
              </section>
              <section className="finops-business-evidence">
                <h3>业务请求 <EvidenceBadge status={detail.businessRequest.status} /></h3>
                <p>{detail.businessRequest.text}</p>
              </section>
              <section className="finops-business-evidence">
                <h3>最终可见回答 <EvidenceBadge status={detail.businessResponse.status} /></h3>
                <p>{detail.businessResponse.text}</p>
              </section>
              <section>
                <h3>处理过程</h3>
                <ol className="finops-evidence-timeline">
                  {detail.timeline.map((item, index) => (
                    <li key={`${item.stage || "stage"}:${index}`}>
                      <i />
                      <span><b>{item.label || "处理阶段"}</b><small>{item.latency_ms == null ? item.status : formatFinOpsDuration(item.latency_ms)}</small></span>
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
            </>
          ) : null}
        </div>
      </aside>
    </div>
  );
}


export function FinOpsPortal({
  workspaceId = "",
  preloadScopeKey = "",
  permissions = {},
}) {
  const initialWindowRef = useRef(initialWindow());
  const initialCacheRef = useRef(
    preloadScopeKey ? readFinOpsBootstrap(preloadScopeKey) : { status: "missing", value: null },
  );
  const initialView = finopsBootstrapViewData(initialCacheRef.current.value || {});
  const [tab, setTab] = useState("overview");
  const [windowValue, setWindowValue] = useState(initialWindowRef.current);
  const [filters, setFilters] = useState({ departmentId: "", agentId: "", model: "" });
  const [overviewState, setOverviewState] = useState({
    dataScopeKey: "",
    loading: !initialCacheRef.current.value,
    updating: Boolean(initialCacheRef.current.value),
    error: "",
    cacheStatus: initialCacheRef.current.status,
    generatedAt: initialCacheRef.current.value?.freshness?.generated_at || "",
    data: initialView,
  });
  const [detailState, setDetailState] = useState({
    dataScopeKey: "",
    tab: "",
    loading: false,
    updating: false,
    error: "",
    data: {},
  });
  const [filterOptions, setFilterOptions] = useState(initialView.filterOptions);
  const [comparisonEnabled, setComparisonEnabled] = useState(false);
  const [comparisonState, setComparisonState] = useState({ loading: false, data: null });
  const [refreshKey, setRefreshKey] = useState(0);
  const [roiRefreshKey, setRoiRefreshKey] = useState(0);
  const [riskRefreshKey, setRiskRefreshKey] = useState(0);
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
    detail: null,
  });
  const overviewSequence = useRef(0);
  const detailSequence = useRef(0);
  const evidenceController = useRef(null);
  const evidenceTrigger = useRef(null);
  const roiDialogController = useRef(null);
  const roiForceRefresh = useRef(false);
  const riskForceRefresh = useRef(false);
  const remediationSequence = useRef(0);
  const remediationOpportunityRef = useRef(null);

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

  const refresh = useCallback(() => setRefreshKey((value) => value + 1), []);
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
  const openRiskAssistant = useCallback((context) => {
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
      detail: null,
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
      detail: null,
    });
    try {
      let items = [];
      let requestRef = evidenceRequestRef({
        evidenceRefs: normalized.evidenceRefs,
      });
      if (!requestRef) {
        const list = await loadFinOpsRequests(
          { ...query, limit: 20 },
          { signal: controller.signal },
        );
        items = Array.isArray(list?.items) ? list.items : [];
        requestRef = evidenceRequestRef({
          fallbackItems: [...items].reverse(),
        });
      }
      if (!requestRef) {
        setEvidenceState({
          open: true,
          reason: normalized.reason,
          loading: false,
          error: "",
          detail: null,
        });
        return;
      }
      const detail = await loadFinOpsRequest(
        requestRef,
        query,
        { signal: controller.signal },
      );
      setEvidenceState({
        open: true,
        reason: normalized.reason,
        loading: false,
        error: "",
        detail,
      });
    } catch (error) {
      if (error?.name === "AbortError") return;
      setEvidenceState({
        open: true,
        reason: normalized.reason,
        loading: false,
        error: error instanceof Error ? error.message : "请求证据读取失败",
        detail: null,
      });
    }
  }, [permissions, query]);

  useEffect(() => {
    if (!workspaceId) return undefined;
    const current = ++overviewSequence.current;
    const controller = new AbortController();
    const cached = defaultScope && preloadScopeKey
      ? readFinOpsBootstrap(preloadScopeKey)
      : { status: "missing", value: null };
    if (cached.value) {
      const view = finopsBootstrapViewData(cached.value);
      setOverviewState({
        dataScopeKey: queryScopeKey,
        loading: false,
        updating: true,
        error: "",
        cacheStatus: cached.status,
        generatedAt: cached.value?.freshness?.generated_at || "",
        data: view,
      });
      setFilterOptions(view.filterOptions);
    } else {
      setOverviewState((state) => ({
        ...state,
        dataScopeKey: state.dataScopeKey === queryScopeKey ? queryScopeKey : "",
        loading: !(state.dataScopeKey === queryScopeKey && state.data?.overview?.metrics),
        updating: Boolean(state.dataScopeKey === queryScopeKey && state.data?.overview?.metrics),
        error: "",
        data: state.dataScopeKey === queryScopeKey ? state.data : {},
      }));
    }

    const load = ({ signal }) => loadFinOpsBootstrap(query, { signal });
    const request = defaultScope && preloadScopeKey
      ? prefetchFinOpsBootstrap(preloadScopeKey, load, { force: true })
      : load({ signal: controller.signal });
    request.then((payload) => {
      if (current !== overviewSequence.current) return;
      const view = finopsBootstrapViewData(payload);
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
    return () => controller.abort();
  }, [defaultScope, preloadScopeKey, query, queryScopeKey, refreshKey, workspaceId]);

  useEffect(() => {
    if (!workspaceId || tab === "overview") {
      setDetailState({
        dataScopeKey: "",
        tab: "",
        loading: false,
        updating: false,
        error: "",
        data: {},
      });
      return undefined;
    }
    const current = ++detailSequence.current;
    const controller = new AbortController();
    setDetailState((state) => {
      const keepCurrent = state.tab === tab
        && state.dataScopeKey === queryScopeKey
        && Object.keys(state.data || {}).length > 0;
      return {
        dataScopeKey: keepCurrent ? queryScopeKey : "",
        tab,
        loading: !keepCurrent,
        updating: keepCurrent,
        error: "",
        data: keepCurrent ? state.data : {},
      };
    });
    const requests = {
      cost: () => Promise.all([
        loadFinOpsBreakdowns("workspace", query, { signal: controller.signal }),
        loadFinOpsAgents(query, { signal: controller.signal }),
        loadFinOpsSavedViews({ workspaceId }, { signal: controller.signal }),
      ]).then(([workspace, agents, views]) => ({
        workspace,
        agents,
        views,
      })),
      roi: () => {
        const force = roiForceRefresh.current;
        roiForceRefresh.current = false;
        return loadFinOpsRoiDecision(
          query,
          { signal: controller.signal, refresh: force },
        );
      },
      risk: () => {
        const force = riskForceRefresh.current;
        riskForceRefresh.current = false;
        return loadFinOpsRiskDecision(
          query,
          { signal: controller.signal, refresh: force },
        );
      },
    };
    requests[tab]().then((data) => {
      if (current === detailSequence.current) {
        setDetailState({
          dataScopeKey: queryScopeKey,
          tab,
          loading: false,
          updating: false,
          error: "",
          data,
        });
      }
    }).catch((error) => {
      if (error?.name !== "AbortError" && current === detailSequence.current) {
        setDetailState((state) => ({
          ...state,
          tab,
          loading: false,
          updating: false,
          error: error instanceof Error ? error.message : "页面数据读取失败",
          data: state.tab === tab && state.dataScopeKey === queryScopeKey
            ? state.data
            : {},
        }));
      }
    });
    return () => controller.abort();
  }, [query, queryScopeKey, refreshKey, riskRefreshKey, roiRefreshKey, tab, workspaceId]);

  useEffect(() => {
    if (!comparisonEnabled || !workspaceId) {
      setComparisonState({ loading: false, data: null });
      return undefined;
    }
    const comparisonWindow = previousEqualWindow({ from: query.from, to: query.to });
    if (!comparisonWindow) {
      setComparisonState({ loading: false, data: null });
      return undefined;
    }
    const controller = new AbortController();
    setComparisonState((state) => ({ ...state, loading: true }));
    loadFinOpsTrends("day", {
      ...query,
      from: comparisonWindow.from,
      to: comparisonWindow.to,
    }, { signal: controller.signal }).then((data) => {
      setComparisonState({ loading: false, data });
    }).catch((error) => {
      if (error?.name !== "AbortError") setComparisonState({ loading: false, data: null });
    });
    return () => controller.abort();
  }, [comparisonEnabled, query, refreshKey, workspaceId]);

  useEffect(() => {
    let lastRefreshAt = Date.now();
    const run = () => {
      if (document.hidden) return;
      lastRefreshAt = Date.now();
      refresh();
    };
    const timer = window.setInterval(run, FINOPS_REFRESH_MS);
    const onVisibilityChange = () => {
      if (!document.hidden && Date.now() - lastRefreshAt >= FINOPS_REFRESH_MS) run();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refresh]);

  useEffect(() => () => {
    evidenceController.current?.abort();
    roiDialogController.current?.abort();
  }, []);

  const refreshRiskOnly = useCallback(() => {
    scheduleRiskOnlyRefresh({
      invalidate: invalidateFinOpsData,
      forceRef: riskForceRefresh,
      bump: () => setRiskRefreshKey((value) => value + 1),
    });
  }, []);

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
      const payload = await loadFinOpsRiskDecision(query, { refresh: true });
      if (current !== remediationSequence.current) return null;
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
  }, [query, queryScopeKey, workspaceId]);

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
      refresh();
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
      scheduleRoiOnlyRefresh({
        invalidate: invalidateFinOpsData,
        forceRef: roiForceRefresh,
        bump: () => setRoiRefreshKey((value) => value + 1),
      });
    } catch (error) {
      if (error?.status === 409) {
        setRoiSaveState({
          busy: false,
          error: "情景已由其他会话更新，正在重新载入最新版本，请确认后再次保存。",
        });
        scheduleRoiOnlyRefresh({
          invalidate: invalidateFinOpsData,
          forceRef: roiForceRefresh,
          bump: () => setRoiRefreshKey((value) => value + 1),
        });
        loadRoiDialogData();
        return;
      }
      setRoiSaveState({
        busy: false,
        error: error instanceof Error ? error.message : "ROI 情景保存失败",
      });
    }
  };

  const generatedAt = overviewState.generatedAt || overviewState.data?.overview?.freshness?.generated_at;
  const overviewDataStatus = overviewState.data?.overview?.data_status || "unavailable";
  const generalAssistantContext = useMemo(() => metricContext({
    id: "operations_overview",
    label: FINOPS_TABS.find((item) => item.id === tab)?.label || "运营总览",
    value: null,
    unit: "",
    kind: "overview",
    dataStatus: overviewDataStatus,
    evidenceState: overviewDataStatus === "complete"
      ? "observed"
      : overviewDataStatus === "partial"
        ? "partial"
        : "unavailable",
  }, assistantScope), [assistantScope, overviewDataStatus, tab]);
  const visibleTabs = FINOPS_TABS.filter((item) => {
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

  return (
    <main className="finops-page">
      <header className="finops-head">
        <div className="finops-head-copy">
          <p>AI OPERATIONS</p>
          <h1>运营管理</h1>
          <span>让 IT 与财务在同一视图理解成本、效能、价值与风险。</span>
        </div>
        <div className="finops-live">
          <i />
          <span>
            {formatRelativeUpdateTime(pageGeneratedAt)}
            {pageUpdating ? " · 正在更新" : ""}
          </span>
          <button type="button" onClick={refresh} title="刷新"><RefreshCw size={15} /></button>
        </div>
      </header>

      <section className="finops-toolbar">
        <div className="finops-date-range">
          <Clock3 size={15} />
          <input type="date" value={windowValue.from} onChange={(event) => setWindowValue((value) => ({ ...value, from: event.target.value }))} />
          <span>至</span>
          <input type="date" value={windowValue.to} onChange={(event) => setWindowValue((value) => ({ ...value, to: event.target.value }))} />
        </div>
        <select value={filters.departmentId} onChange={(event) => setFilters((value) => ({ ...value, departmentId: event.target.value }))}>
          <option value="">全部部门</option>
          {(filterOptions?.filters?.departments || []).map((item) => <option key={item} value={item === "unassigned" ? "" : item}>{item === "unassigned" ? "未归属" : item}</option>)}
        </select>
        <select value={filters.agentId} onChange={(event) => setFilters((value) => ({ ...value, agentId: event.target.value }))}>
          <option value="">全部 Agent</option>
          {(filterOptions?.filters?.agents || []).map((item) => <option key={item}>{item}</option>)}
        </select>
        <select value={filters.model} onChange={(event) => setFilters((value) => ({ ...value, model: event.target.value }))}>
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

      <nav className="finops-tabs" aria-label="运营管理页面">
        {visibleTabs.map((item) => {
          const Icon = TAB_ICONS[item.id];
          return (
            <button key={item.id} type="button" className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>
              <Icon size={15} />
              {item.label}
            </button>
          );
        })}
      </nav>

      <section className="finops-content" aria-busy={portalStatusVisibility.showOverviewSkeleton || showDetailLoading || detailState.updating ? "true" : "false"}>
        {portalStatusVisibility.showOverviewSkeleton ? <MetricSkeleton /> : null}
        {portalStatusVisibility.showOverviewStaleError
          ? <div className="finops-inline-error">更新失败，已保留上次数据：{overviewState.error}</div>
          : null}
        {portalStatusVisibility.showOverviewHardError
          ? <div className="finops-state finops-state-error"><AlertTriangle size={18} /><span>{overviewState.error}</span><button type="button" onClick={refresh}>重试</button></div>
          : null}
        {!overviewState.loading && overviewState.data?.overview?.metrics && tab === "overview"
          ? <OverviewPage data={overviewState.data} scope={assistantScope} comparison={comparisonState.data} onEvidence={canOpenEvidence ? openEvidence : null} onAsk={openAssistant} onDimensionSelect={selectDimension} onConfigurePricing={() => setModelSettingsOpen(true)} />
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
            onRetry={() => {
              roiForceRefresh.current = true;
              setRoiRefreshKey((value) => value + 1);
            }}
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
            onRetry={() => {
              riskForceRefresh.current = true;
              setRiskRefreshKey((value) => value + 1);
            }}
            onEvidence={canOpenEvidence ? openEvidence : null}
            onCreateDraft={openRemediation}
            onAcknowledge={(item) => manageAnomaly(item, "acknowledge")}
            onSuppress={(item) => manageAnomaly(item, "suppress")}
            onAsk={openRiskAssistant}
          />
        ) : null}
      </section>

      <footer className="finops-footnote">
        <WalletCards size={14} />
        <span>成本为 DataForge 价目表估算，不代表云平台实际账单；缺失证据不会补造数据。</span>
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
            <ModelRoutingPage workspaceId={workspaceId} embedded />
          </section>
        </div>
      ) : null}
    </main>
  );
}
