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
  createFinOpsSavedView,
  finOpsExportUrl,
  loadFinOpsActions,
  loadFinOpsAgents,
  loadFinOpsAnomalies,
  loadFinOpsBootstrap,
  loadFinOpsBreakdowns,
  loadFinOpsOpportunities,
  loadFinOpsRecommendations,
  loadFinOpsRequest,
  loadFinOpsRequests,
  loadFinOpsRoiEconomics,
  loadFinOpsSavedViews,
  loadFinOpsTrends,
  loadWorkspaceCostValue,
  loadWorkspaceRoi,
  suppressFinOpsAnomaly,
  transitionFinOpsAction,
} from "./api.js";
import {
  prefetchFinOpsBootstrap,
  readFinOpsBootstrap,
} from "./finopsPreload.js";
import { FinOpsAssistant } from "./FinOpsAssistant.jsx";
import { ModelRoutingPage } from "./ModelRoutingPage.jsx";
import {
  applyDimensionFilter,
  filterChips,
  metricContext,
  metricTooltip,
  previousEqualWindow,
} from "./finopsInteraction.js";
import {
  FINOPS_TABS,
  finopsBootstrapViewData,
  finopsBudgetView,
  finopsBreakdownRows,
  finopsDoughnutSegments,
  finopsMetricCards,
  finopsOpportunityRows,
  finopsRequestViewModel,
  finopsRoiEconomicsView,
  finopsTrendViewModel,
  formatFinOpsCost,
  formatFinOpsDuration,
  formatFinOpsNumber,
  formatFinOpsPercent,
  formatRelativeUpdateTime,
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


function Panel({ title, subtitle = "", children, className = "" }) {
  return (
    <section className={`finops-panel ${className}`.trim()}>
      <header>
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
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
        return (
          <article
            className={`finops-metric ${card.tone}`}
            key={card.id}
            tabIndex={0}
            aria-label={`${card.label} ${card.value}`}
          >
            <div>
              <span>{card.label}</span>
              <CircleHelp className="finops-help-icon" size={13} aria-hidden="true" />
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
            <div className="finops-metric-tooltip" role="tooltip">
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
      {Array.from({ length: 6 }, (_, index) => (
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
  const maximum = Math.max(...rows.map((row) => Number(row[valueKey] || 0)), 1);
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
            <i style={{ width: `${Math.max(2, (Number(row[valueKey] || 0) / maximum) * 100)}%` }} />
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
  const maximum = Math.max(...values, 1);
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
        <span>{formatValue(maximum)}</span>
        <span>{formatValue(maximum / 2)}</span>
        <span>{formatValue(0)}</span>
      </div>
      <div className="finops-trend-columns">
        {rows.slice(-14).map((row, visibleIndex, visibleRows) => {
          const rawValue = metricValue(row);
          const value = Number(rawValue || 0);
          const height = value > 0 ? Math.max(2, (value / maximum) * 100) : 0;
          const comparisonOffset = Math.max(0, comparisonRows.length - visibleRows.length);
          const comparisonRow = comparisonRows[comparisonOffset + visibleIndex];
          const comparisonValue = Number(metricValue(comparisonRow) || 0);
          const comparisonHeight = comparisonRow
            ? Math.max(2, (comparisonValue / maximum) * 100)
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
              {rowEvents.length ? (
                <i className="finops-trend-event" title={`${rowEvents.length} 条运营事件`} />
              ) : null}
              <b className="finops-trend-value">{formatValue(rawValue)}</b>
              <div className="finops-trend-stack" style={{ height: `${height}%` }}>
                {metric !== "total"
                  ? <i className="input" style={{ height: "100%" }} />
                  : parts.map((part) => part.value
                    ? <i key={part.key} className={part.key} style={{ height: `${(part.value / partTotal) * 100}%` }} />
                    : null)}
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
            <button type="button" onClick={() => onEvidence(item.title || "风险项")}>
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
      label: "APIM 对账",
      value: trust.apim?.coverage_pct,
      meta: `${formatFinOpsNumber(trust.apim?.apim_governed_requests, "0")} 已关联 · ${formatFinOpsNumber(trust.apim?.app_observed_requests, "0")} 应用观测`,
      state: trust.apim?.state,
    },
  ];
  return (
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
        <Panel title="数据可信度" subtitle="明确哪些数字已记录、已计价并完成网关对账">
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
        <Panel title="成本趋势" subtitle="请求级价目表估算，不代表 Azure 实际账单" className="span-2">
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


function ValueCard({ label, value, meta, status }) {
  return (
    <article className="finops-value-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{meta}</small>
      <EvidenceBadge status={status} />
    </article>
  );
}


function RoiEconomics({ payload }) {
  const view = finopsRoiEconomicsView(payload);
  return (
    <>
      <Panel title="ROI 证据漏斗" subtitle="从投入、使用、产出到已验证业务结果" className="span-2">
        {view.funnel.length ? (
          <div className="finops-roi-funnel">
            {view.funnel.map((stage, index) => (
              <article key={stage.id}>
                <span>{index + 1}</span>
                <small>{stage.label}</small>
                <b>{stage.value == null ? "未记录" : `${formatFinOpsNumber(stage.value)} ${stage.unit || ""}`.trim()}</b>
                <EvidenceBadge status={stage.status} />
              </article>
            ))}
          </div>
        ) : <EmptyState>当前范围没有可形成漏斗的证据。</EmptyState>}
      </Panel>
      <section className="finops-value-grid span-2">
        {view.unitEconomics.map((item) => (
          <ValueCard
            key={item.label}
            label={item.label}
            value={item.valueLabel}
            meta="仅基于完整成本与已观测分母"
            status={item.status}
          />
        ))}
        <ValueCard
          label="可复核 ROI"
          value={view.verifiedRoiLabel}
          meta="不包含估算情景"
          status={view.verifiedRoiStatus}
        />
      </section>
      <Panel title="情景测算" subtitle="估算情景与已验证 ROI 严格分开" className="span-2">
        {view.scenarios.length ? (
          <div className="finops-scenarios">
            {view.scenarios.map((item) => (
              <article key={item.scenario_id}>
                <span><b>{item.title || "ROI 情景"}</b><small>版本 {item.revision || 1}</small></span>
                <EvidenceBadge status="estimated" />
              </article>
            ))}
          </div>
        ) : <EmptyState>当前没有已保存的 ROI 估算情景。</EmptyState>}
      </Panel>
    </>
  );
}


function RoiPage({
  detail,
}) {
  const roi = detail.roi || {};
  const costValue = detail.costValue || {};
  const realized = costValue.realized_roi || {};
  const outcomes = roi.outcome_evidence || {};
  const business = roi.business_value || {};
  const cost = roi.cost_evidence || {};
  const verifiedCount = Array.isArray(outcomes.verified_outcome_event_ids)
    ? outcomes.verified_outcome_event_ids.length
    : 0;
  const businessValue = business.total == null
    ? "未记录"
    : `${business.currency || ""} ${formatFinOpsNumber(business.total)}`.trim();
  const roiValue = realized.roi_ratio == null
    ? "证据不足"
    : `${formatFinOpsNumber(realized.roi_ratio * 100)}%`;
  return (
    <div className="finops-grid">
      <RoiEconomics payload={detail.economics} />
      <section className="finops-value-grid span-2">
        <ValueCard label="已记录业务价值" value={businessValue} meta={`${verifiedCount} 个已验证结果`} status={business.status || "not_recorded"} />
        <ValueCard label="可复核 ROI" value={roiValue} meta="仅使用完整成本与已验证结果" status={realized.status || "not_recorded"} />
        <ValueCard label="已观测成本" value={formatFinOpsCost(cost.total, cost.status === "complete" ? "estimated" : "unavailable")} meta={cost.currency || "未形成单一币种"} status={cost.status} />
        <ValueCard label="结果证据覆盖" value={outcomes.status === "verified" ? "已验证" : outcomes.status === "observed" ? "待验证" : "未记录"} meta={`${verifiedCount} 个结果通过验证`} status={outcomes.status} />
      </section>
      <Panel title="成本与价值证据" subtitle="估算、已记录与已验证状态严格分开">
        <div className="finops-evidence-summary">
          <div><span>成本证据</span><EvidenceBadge status={cost.status} /></div>
          <div><span>结果证据</span><EvidenceBadge status={outcomes.status} /></div>
          <div><span>业务价值</span><EvidenceBadge status={business.status || "not_recorded"} /></div>
          <div><span>链路完整性</span><EvidenceBadge status={roi.lineage_complete ? "complete" : "partial"} /></div>
        </div>
      </Panel>
      <Panel title="ROI 口径" subtitle="投入、使用、产出与业务结果必须逐层具备证据">
        <div className="finops-gap-list">
          <span>估算成本与实际账单保持分离。</span>
          <span>只有已验证业务结果才进入可复核 ROI。</span>
        </div>
      </Panel>
      <Panel title="证据缺口" subtitle="补齐后才能形成可复核 ROI" className="span-2">
        {outcomes.status === "verified" && cost.status === "complete" ? (
          <EmptyState>当前 ROI 证据链已完整。</EmptyState>
        ) : (
          <div className="finops-gap-list">
            {outcomes.status !== "verified" ? <span>需要已验证的业务结果事件。</span> : null}
            {cost.status !== "complete" ? <span>需要完整的模型价目表与成本覆盖。</span> : null}
            {!roi.lineage_complete ? <span>部分运行缺少完整证据关联。</span> : null}
          </div>
        )}
      </Panel>
    </div>
  );
}


function RiskPage({
  data,
  busyId,
  actionError,
  onAnomalyAction,
  onActionTransition,
  onEvidence,
}) {
  const anomalies = Array.isArray(data.anomalies?.items) ? data.anomalies.items : [];
  const recommendations = Array.isArray(data.recommendations?.items) ? data.recommendations.items : [];
  const actions = Array.isArray(data.actions?.items) ? data.actions.items : [];
  const opportunities = finopsOpportunityRows(data.opportunities);
  return (
    <div className="finops-grid">
      <Panel title="优化机会队列" subtitle="按影响、证据置信度与实施难度排序；不自动执行" className="span-2">
        {opportunities.length ? (
          <div className="finops-opportunity-list">
            {opportunities.map((item) => (
              <article key={item.opportunity_id} className={item.queue_state}>
                <div>
                  <span><b>{item.title}</b><small>{item.recommendation}</small></span>
                  <EvidenceBadge status={item.evidence_state} />
                </div>
                <dl>
                  <div><dt>影响</dt><dd>{item.impactLabel}</dd></div>
                  <div><dt>置信度</dt><dd>{item.confidenceLabel}</dd></div>
                  <div><dt>实施难度</dt><dd>{item.effortLabel}</dd></div>
                  <div><dt>潜在节省</dt><dd>{item.savingsLabel}</dd></div>
                </dl>
                <footer>
                  <span>{item.stateLabel}</span>
                  <b>{item.actionLabel}</b>
                  {onEvidence ? <button type="button" onClick={() => onEvidence(item.title)}>查看证据</button> : null}
                </footer>
              </article>
            ))}
          </div>
        ) : <EmptyState>当前没有达到证据门槛的优化机会。</EmptyState>}
      </Panel>
      <Panel title="开放异常" subtitle="只显示达到样本门槛的规则">
        {anomalies.length ? (
          <div className="finops-anomaly-list">
            {anomalies.map((item) => (
              <article key={item.anomaly_id} className={item.severity}>
                <div><AlertTriangle size={16} /><b>{item.policy_type}</b><EvidenceBadge status={item.status} /></div>
                <p>{item.recommendation}</p>
                <small>观测 {item.observed_value} · 阈值 {item.threshold_value} · 样本 {item.sample_count}</small>
                {["open", "acknowledged"].includes(item.status) ? (
                  <footer>
                    {item.status === "open" ? <button type="button" disabled={busyId === item.anomaly_id} onClick={() => onAnomalyAction(item, "acknowledge")}>确认</button> : null}
                    <button type="button" disabled={busyId === item.anomaly_id} onClick={() => onAnomalyAction(item, "suppress")}>抑制</button>
                    {onEvidence ? <button type="button" onClick={() => onEvidence(item.policy_type)}>查看证据</button> : null}
                  </footer>
                ) : onEvidence ? <footer><button type="button" onClick={() => onEvidence(item.policy_type)}>查看证据</button></footer> : null}
              </article>
            ))}
          </div>
        ) : <EmptyState>没有达到样本门槛的异常。</EmptyState>}
      </Panel>
      <Panel title="优化建议" subtitle="每条建议都需要证据与人工判断">
        {recommendations.length ? (
          <div className="finops-recommendations">
            {recommendations.map((item) => (
              <div key={item.recommendation_id}>
                <ShieldCheck size={16} />
                <span><b>{item.policy_type}</b><small>{item.recommendation}</small></span>
              </div>
            ))}
          </div>
        ) : <EmptyState>当前没有可复核的优化建议。</EmptyState>}
      </Panel>
      <Panel title="治理动作" subtitle="生产执行默认关闭，仍需异人审批">
        {actionError ? <div className="finops-inline-error">{actionError}</div> : null}
        {actions.length ? (
          <div className="finops-table-scroll">
            <table className="finops-table">
              <thead><tr><th>类型</th><th>状态</th><th>提出人</th><th>批准人</th><th>操作</th></tr></thead>
              <tbody>
                {actions.map((item) => {
                  const transition = {
                    draft: "submit",
                    pending_approval: "approve",
                    approved: "execute",
                    verifying: "verify",
                    succeeded: "rollback",
                    failed: "rollback",
                  }[item.status];
                  const label = {
                    submit: "提交审批",
                    approve: "批准",
                    execute: "候选执行",
                    verify: "验证",
                    rollback: "回滚",
                  }[transition];
                  return (
                    <tr key={item.action_id}>
                      <td><b>{item.action_type}</b></td>
                      <td><EvidenceBadge status={item.status} /></td>
                      <td>{item.proposed_by}</td>
                      <td>{item.approved_by || "待批准"}</td>
                      <td>{transition ? <button type="button" className="finops-table-action" disabled={busyId === item.action_id} onClick={() => onActionTransition(item, transition)}>{label}</button> : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : <EmptyState>尚未创建治理草案。</EmptyState>}
      </Panel>
    </div>
  );
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
                    在 Foundry Trace 中查看 <ExternalLink size={14} />
                  </a>
                ) : null}
                {detail.links.azureMonitor ? (
                  <a className="finops-monitor-link" href={detail.links.azureMonitor} target="_blank" rel="noreferrer">
                    在 Azure Monitor 中查看 <ExternalLink size={14} />
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
    loading: !initialCacheRef.current.value,
    updating: Boolean(initialCacheRef.current.value),
    error: "",
    cacheStatus: initialCacheRef.current.status,
    generatedAt: initialCacheRef.current.value?.freshness?.generated_at || "",
    data: initialView,
  });
  const [detailState, setDetailState] = useState({ loading: false, error: "", data: {} });
  const [filterOptions, setFilterOptions] = useState(initialView.filterOptions);
  const [comparisonEnabled, setComparisonEnabled] = useState(false);
  const [comparisonState, setComparisonState] = useState({ loading: false, data: null });
  const [refreshKey, setRefreshKey] = useState(0);
  const [governance, setGovernance] = useState({ busyId: "", error: "" });
  const [assistantState, setAssistantState] = useState({
    context: null,
    openRequest: 0,
  });
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
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

  const query = useMemo(() => ({
    from: toIso(windowValue.from),
    to: toIso(windowValue.to, true),
    workspaceId,
    departmentId: filters.departmentId,
    agentId: filters.agentId,
    model: filters.model,
  }), [filters, windowValue, workspaceId]);
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
  const selectDimension = useCallback((selection) => {
    setFilters((value) => applyDimensionFilter(value, selection));
  }, []);
  const activeFilterChips = useMemo(() => filterChips({
    departmentId: filters.departmentId,
    agentId: filters.agentId,
    model: filters.model,
  }), [filters]);
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

  const openEvidence = useCallback(async (reason) => {
    if (permissions["finops.request_detail.read"] === false) return;
    evidenceController.current?.abort();
    const controller = new AbortController();
    evidenceController.current = controller;
    evidenceTrigger.current = document.activeElement;
    setEvidenceState({
      open: true,
      reason,
      loading: true,
      error: "",
      detail: null,
    });
    try {
      const list = await loadFinOpsRequests(
        { ...query, limit: 20 },
        { signal: controller.signal },
      );
      const items = Array.isArray(list?.items) ? list.items : [];
      const selected = items[items.length - 1];
      const requestRef = String(selected?.request_ref || "").trim();
      if (!requestRef) {
        setEvidenceState({
          open: true,
          reason,
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
        reason,
        loading: false,
        error: "",
        detail,
      });
    } catch (error) {
      if (error?.name === "AbortError") return;
      setEvidenceState({
        open: true,
        reason,
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
        loading: !state.data?.overview?.metrics,
        updating: Boolean(state.data?.overview?.metrics),
        error: "",
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
        loading: !state.data?.overview?.metrics,
        updating: false,
        error: error instanceof Error ? error.message : "运营数据更新失败",
      }));
    });
    return () => controller.abort();
  }, [defaultScope, preloadScopeKey, query, refreshKey, workspaceId]);

  useEffect(() => {
    if (!workspaceId || tab === "overview") {
      setDetailState({ loading: false, error: "", data: {} });
      return undefined;
    }
    const current = ++detailSequence.current;
    const controller = new AbortController();
    setDetailState((state) => ({ ...state, loading: true, error: "" }));
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
      roi: () => Promise.all([
        loadWorkspaceRoi(workspaceId, { from: query.from, to: query.to }),
        loadWorkspaceCostValue(workspaceId, { from: query.from, to: query.to }),
        loadFinOpsRoiEconomics(query, { signal: controller.signal }),
      ]).then(([roi, costValue, economics]) => ({ roi, costValue, economics })),
      risk: () => Promise.all([
        loadFinOpsAnomalies(query, { signal: controller.signal }),
        loadFinOpsRecommendations(query, { signal: controller.signal }),
        loadFinOpsActions(query, { signal: controller.signal }),
        loadFinOpsOpportunities(query, { signal: controller.signal }),
      ]).then(([anomalies, recommendations, actions, opportunities]) => ({
        anomalies,
        recommendations,
        actions,
        opportunities,
      })),
    };
    requests[tab]().then((data) => {
      if (current === detailSequence.current) setDetailState({ loading: false, error: "", data });
    }).catch((error) => {
      if (error?.name !== "AbortError" && current === detailSequence.current) {
        setDetailState({
          loading: false,
          error: error instanceof Error ? error.message : "页面数据读取失败",
          data: {},
        });
      }
    });
    return () => controller.abort();
  }, [query, refreshKey, tab, workspaceId]);

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
    const timer = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => () => evidenceController.current?.abort(), []);

  const manageAnomaly = async (item, operation) => {
    let reason = "";
    if (operation === "suppress") {
      reason = window.prompt("请输入抑制原因（将写入治理审计）", "")?.trim() || "";
      if (!reason) return;
    }
    setGovernance({ busyId: item.anomaly_id, error: "" });
    try {
      if (operation === "acknowledge") await acknowledgeFinOpsAnomaly(item.anomaly_id);
      else await suppressFinOpsAnomaly(item.anomaly_id, reason);
      refresh();
      setGovernance({ busyId: "", error: "" });
    } catch (error) {
      setGovernance({ busyId: "", error: error instanceof Error ? error.message : "异常治理操作失败" });
    }
  };

  const transitionAction = async (item, transition) => {
    let payload = null;
    if (transition === "rollback") {
      const reason = window.prompt("请输入紧急回滚原因（仅 Owner 可执行）", "")?.trim() || "";
      if (!reason) return;
      payload = { reason };
    }
    setGovernance({ busyId: item.action_id, error: "" });
    try {
      await transitionFinOpsAction(item.action_id, transition, payload);
      refresh();
      setGovernance({ busyId: "", error: "" });
    } catch (error) {
      setGovernance({ busyId: "", error: error instanceof Error ? error.message : "审批动作失败" });
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
  const showDetailLoading = tab !== "overview" && detailState.loading;
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
            {formatRelativeUpdateTime(generatedAt)}
            {overviewState.updating ? " · 正在更新" : ""}
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

      <section className="finops-content" aria-busy={overviewState.loading || showDetailLoading ? "true" : "false"}>
        {overviewState.loading ? <MetricSkeleton /> : null}
        {overviewState.error && overviewState.data?.overview?.metrics
          ? <div className="finops-inline-error">更新失败，已保留上次数据：{overviewState.error}</div>
          : null}
        {overviewState.error && !overviewState.data?.overview?.metrics
          ? <div className="finops-state finops-state-error"><AlertTriangle size={18} /><span>{overviewState.error}</span><button type="button" onClick={refresh}>重试</button></div>
          : null}
        {!overviewState.loading && overviewState.data?.overview?.metrics && tab === "overview"
          ? <OverviewPage data={overviewState.data} scope={assistantScope} comparison={comparisonState.data} onEvidence={canOpenEvidence ? openEvidence : null} onAsk={openAssistant} onDimensionSelect={selectDimension} onConfigurePricing={() => setModelSettingsOpen(true)} />
          : null}
        {showDetailLoading ? <div className="finops-section-loading"><Loader2 className="spin" size={18} />正在读取当前页面</div> : null}
        {!showDetailLoading && detailState.error ? <div className="finops-state finops-state-error"><AlertTriangle size={18} /><span>{detailState.error}</span><button type="button" onClick={refresh}>重试</button></div> : null}
        {!showDetailLoading && !detailState.error && tab === "cost"
          ? <CostPage overviewData={overviewState.data} detail={detailState.data} scope={assistantScope} comparison={comparisonState.data} onEvidence={canOpenEvidence ? openEvidence : null} onAsk={openAssistant} onDimensionSelect={selectDimension} onSaveView={governance.busyId === "save-view" ? null : saveCurrentView} exportUrl={finOpsExportUrl("workspace", query)} onConfigurePricing={() => setModelSettingsOpen(true)} />
          : null}
        {!showDetailLoading && !detailState.error && tab === "roi"
          ? <RoiPage detail={detailState.data} />
          : null}
        {!showDetailLoading && !detailState.error && tab === "risk"
          ? <RiskPage data={detailState.data} busyId={governance.busyId} actionError={governance.error} onAnomalyAction={manageAnomaly} onActionTransition={transitionAction} onEvidence={canOpenEvidence ? openEvidence : null} />
          : null}
      </section>

      <footer className="finops-footnote">
        <WalletCards size={14} />
        <span>成本为 DataForge 价目表估算，不代表 Azure 实际账单；缺失证据不会补造数据。</span>
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
