import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Clock3,
  Database,
  Gauge,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  WalletCards,
} from "lucide-react";

import {
  acknowledgeFinOpsAnomaly,
  loadFinOpsActions,
  loadFinOpsAgents,
  loadFinOpsAnomalies,
  loadFinOpsBootstrap,
  loadFinOpsBreakdowns,
  loadFinOpsRecommendations,
  loadWorkspaceCostValue,
  loadWorkspaceRoi,
  suppressFinOpsAnomaly,
  transitionFinOpsAction,
} from "./api.js";
import {
  prefetchFinOpsBootstrap,
  readFinOpsBootstrap,
} from "./finopsPreload.js";
import {
  FINOPS_TABS,
  finopsBootstrapViewData,
  finopsBreakdownRows,
  finopsMetricCards,
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
    unavailable: "不可用",
    open: "待处理",
    acknowledged: "已确认",
    suppressed: "已抑制",
    resolved: "已解决",
    stale: "已过期",
    failed: "失败",
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


function MetricCards({ payload }) {
  return (
    <section className="finops-metrics" aria-label="运营核心指标">
      {finopsMetricCards(payload).map((card) => (
        <article className={`finops-metric ${card.tone}`} key={card.id}>
          <div>
            <span>{card.label}</span>
            <EvidenceBadge status={payload?.data_status} />
          </div>
          <strong>{card.value}</strong>
          <small>{card.meta}</small>
        </article>
      ))}
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


function HorizontalBars({ rows, valueKey = "requests", valueFormatter = formatFinOpsNumber }) {
  const maximum = Math.max(...rows.map((row) => Number(row[valueKey] || 0)), 1);
  if (!rows.length) return <EmptyState />;
  return (
    <div className="finops-bars">
      {rows.slice(0, 10).map((row) => (
        <div className="finops-bar-row" key={row.key}>
          <span title={row.key}>{row.key}</span>
          <div>
            <i style={{ width: `${Math.max(2, (Number(row[valueKey] || 0) / maximum) * 100)}%` }} />
          </div>
          <b>{valueFormatter(row[valueKey])}</b>
        </div>
      ))}
    </div>
  );
}


function TrendBars({ payload, metric = "total" }) {
  const rows = finopsTrendViewModel(payload);
  const values = rows.map((row) => Number(metric === "cost" ? row.cost : row.total || 0));
  const maximum = Math.max(...values, 1);
  if (!rows.length) return <EmptyState />;
  return (
    <div className="finops-trend-chart">
      <div className="finops-trend-legend">
        {metric === "cost" ? (
          <span><i className="input" />估算成本</span>
        ) : (
          <>
            <span><i className="input" />输入</span>
            <span><i className="output" />输出</span>
            <span><i className="cached" />缓存</span>
            <span><i className="reasoning" />推理</span>
          </>
        )}
      </div>
      <div className="finops-trend-columns">
        {rows.slice(-14).map((row) => {
          const value = Number(metric === "cost" ? row.cost : row.total || 0);
          const height = Math.max(4, (value / maximum) * 100);
          const parts = ["input", "output", "cached", "reasoning"].map((key) => ({
            key,
            value: Number(row.series[key] || 0),
          }));
          const partTotal = parts.reduce((sum, item) => sum + item.value, 0) || 1;
          return (
            <div
              className="finops-trend-column"
              key={row.bucket}
              title={`${row.label} · ${metric === "cost" ? formatFinOpsCost(row.cost, row.cost == null ? "unavailable" : "estimated") : `${value} Token`}`}
            >
              <div className="finops-trend-stack" style={{ height: `${height}%` }}>
                {metric === "cost"
                  ? <i className="input" style={{ height: "100%" }} />
                  : parts.map((part) => part.value
                    ? <i key={part.key} className={part.key} style={{ height: `${(part.value / partTotal) * 100}%` }} />
                    : null)}
              </div>
              <span>{row.label.slice(5)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}


function BreakdownTable({ rows }) {
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
            <tr key={row.key}>
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


function AttentionList({ items }) {
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
        </div>
      ))}
    </div>
  );
}


function AgentInsightCard({ kind, insight }) {
  const title = kind === "roi" ? "ROI Agent" : "FinOps Agent";
  const icon = kind === "roi" ? <Sparkles size={17} /> : <Bot size={17} />;
  if (!insight) {
    return (
      <article className="finops-agent-card empty">
        <header>{icon}<div><b>{title}</b><span>尚无分析结论</span></div></header>
        <p>有符合条件的异常或已验证结果后，分析结论会出现在这里。</p>
      </article>
    );
  }
  return (
    <article className={`finops-agent-card ${insight.status || ""}`}>
      <header>
        {icon}
        <div><b>{title}</b><span>{insight.title || "运营分析"}</span></div>
        <EvidenceBadge status={insight.status} />
      </header>
      <p>{insight.summary || "证据不足，暂不生成推测性结论。"}</p>
      <small>{insight.generated_at ? `分析于 ${new Date(insight.generated_at).toLocaleString("zh-CN")}` : "尚未分析"}</small>
    </article>
  );
}


function OverviewPage({ data }) {
  const departmentRows = finopsBreakdownRows(data.department);
  const anomalies = Array.isArray(data.anomalies?.items) ? data.anomalies.items : [];
  return (
    <>
      <MetricCards payload={data.overview} />
      <div className="finops-grid finops-grid-wide">
        <Panel title="成本与调用趋势" subtitle="最近 30 天的 Token 结构与调用变化" className="span-2">
          <TrendBars payload={data.trends} />
        </Panel>
        <Panel title="需要关注" subtitle="预算、延迟、计价和网关治理">
          <AttentionList items={anomalies} />
        </Panel>
        <Panel title="部门成本与运行质量" subtitle="未映射 workspace 统一进入“未归属”">
          <BreakdownTable rows={departmentRows} />
        </Panel>
        <Panel title="价值与优化摘要" subtitle="分析结论与运营数据分开更新时间">
          <div className="finops-agent-stack">
            <AgentInsightCard kind="finops" insight={data.insights?.finops} />
            <AgentInsightCard kind="roi" insight={data.insights?.roi} />
          </div>
        </Panel>
      </div>
    </>
  );
}


function CostPage({ overviewData, detail }) {
  const agents = finopsBreakdownRows({ items: detail.agents?.agents || [] });
  const models = finopsBreakdownRows({ items: detail.agents?.models || [] });
  return (
    <>
      <MetricCards payload={overviewData.overview} />
      <div className="finops-grid">
        <Panel title="成本趋势" subtitle="请求级价目表估算，不代表 Azure 实际账单" className="span-2">
          <TrendBars payload={overviewData.trends} metric="cost" />
        </Panel>
        <Panel title="部门成本归因" subtitle="部门与专案按同一账本口径聚合">
          <BreakdownTable rows={finopsBreakdownRows(overviewData.department)} />
        </Panel>
        <Panel title="专案成本归因" subtitle="每个 workspace 最多归属一个部门">
          <BreakdownTable rows={finopsBreakdownRows(detail.workspace)} />
        </Panel>
        <Panel title="Agent 成本归因" subtitle="Agent 只作为下钻维度">
          <HorizontalBars rows={agents} valueKey="cost" valueFormatter={(value) => formatFinOpsCost(value, value == null ? "unavailable" : "estimated")} />
        </Panel>
        <Panel title="模型成本归因" subtitle="按 deployment 聚合">
          <HorizontalBars rows={models} valueKey="cost" valueFormatter={(value) => formatFinOpsCost(value, value == null ? "unavailable" : "estimated")} />
        </Panel>
        <Panel title="FinOps Agent" subtitle="解释成本变化与优化机会" className="span-2">
          <AgentInsightCard kind="finops" insight={overviewData.insights?.finops} />
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


function RoiPage({ detail, insight }) {
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
      <Panel title="ROI Agent" subtitle="只分析已验证结果，不补造价值">
        <AgentInsightCard kind="roi" insight={insight} />
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
  insights,
  busyId,
  actionError,
  onAnomalyAction,
  onActionTransition,
}) {
  const anomalies = Array.isArray(data.anomalies?.items) ? data.anomalies.items : [];
  const recommendations = Array.isArray(data.recommendations?.items) ? data.recommendations.items : [];
  const actions = Array.isArray(data.actions?.items) ? data.actions.items : [];
  return (
    <div className="finops-grid">
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
                  </footer>
                ) : null}
              </article>
            ))}
          </div>
        ) : <EmptyState>没有达到样本门槛的异常。</EmptyState>}
      </Panel>
      <Panel title="风险分析 Agent" subtitle="Agent 只能解释和生成草案">
        <div className="finops-agent-stack">
          <AgentInsightCard kind="finops" insight={insights?.finops} />
          <AgentInsightCard kind="roi" insight={insights?.roi} />
        </div>
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
  const [refreshKey, setRefreshKey] = useState(0);
  const [governance, setGovernance] = useState({ busyId: "", error: "" });
  const overviewSequence = useRef(0);
  const detailSequence = useRef(0);

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

  const refresh = useCallback(() => setRefreshKey((value) => value + 1), []);

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
      ]).then(([workspace, agents]) => ({ workspace, agents })),
      roi: () => Promise.all([
        loadWorkspaceRoi(workspaceId, { from: query.from, to: query.to }),
        loadWorkspaceCostValue(workspaceId, { from: query.from, to: query.to }),
      ]).then(([roi, costValue]) => ({ roi, costValue })),
      risk: () => Promise.all([
        loadFinOpsAnomalies(query, { signal: controller.signal }),
        loadFinOpsRecommendations(query, { signal: controller.signal }),
        loadFinOpsActions(query, { signal: controller.signal }),
      ]).then(([anomalies, recommendations, actions]) => ({ anomalies, recommendations, actions })),
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
    const timer = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

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

  const generatedAt = overviewState.generatedAt || overviewState.data?.overview?.freshness?.generated_at;
  const visibleTabs = FINOPS_TABS.filter((item) => {
    if (item.id === "cost") return permissions["finops.cost.read"] !== false;
    if (item.id === "roi") return permissions["finops.roi.read"] !== false;
    return true;
  });
  const showDetailLoading = tab !== "overview" && detailState.loading;

  return (
    <main className="finops-page">
      <header className="finops-head">
        <div>
          <p>AI OPERATIONS</p>
          <h1>运营驾驶舱</h1>
          <span>让 IT 与财务在同一视图理解成本、预算、效能、价值与风险。</span>
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
      </section>

      <nav className="finops-tabs" aria-label="运营驾驶舱页面">
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
          ? <OverviewPage data={overviewState.data} />
          : null}
        {showDetailLoading ? <div className="finops-section-loading"><Loader2 className="spin" size={18} />正在读取当前页面</div> : null}
        {!showDetailLoading && detailState.error ? <div className="finops-state finops-state-error"><AlertTriangle size={18} /><span>{detailState.error}</span><button type="button" onClick={refresh}>重试</button></div> : null}
        {!showDetailLoading && !detailState.error && tab === "cost"
          ? <CostPage overviewData={overviewState.data} detail={detailState.data} />
          : null}
        {!showDetailLoading && !detailState.error && tab === "roi"
          ? <RoiPage detail={detailState.data} insight={overviewState.data.insights?.roi} />
          : null}
        {!showDetailLoading && !detailState.error && tab === "risk"
          ? <RiskPage data={detailState.data} insights={overviewState.data.insights} busyId={governance.busyId} actionError={governance.error} onAnomalyAction={manageAnomaly} onActionTransition={transitionAction} />
          : null}
      </section>

      <footer className="finops-footnote">
        <WalletCards size={14} />
        <span>成本为 DataForge 价目表估算，不代表 Azure 实际账单；缺失证据不会补造数据。</span>
      </footer>
    </main>
  );
}
