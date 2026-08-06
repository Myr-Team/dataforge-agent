import React from "react";
import {
  AlertTriangle,
  ArrowRight,
  Database,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";

import { roiDecisionView } from "../finopsDecisionViewModel.js";
import { EvidenceMaturity, ValueBridge } from "./DecisionCharts.jsx";
import { FinOpsCapabilityNote } from "./FinOpsCapabilityNote.jsx";


function DecisionBadge({ status = "unavailable", children = "状态待确认" }) {
  return (
    <span className={`finops-decision-status finops-decision-status-${status}`}>
      {children}
    </span>
  );
}


function LocalEmpty({ children }) {
  return (
    <div className="finops-decision-page-empty" role="status">
      <Database size={17} aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}


function RoiLoadingShell() {
  return (
    <div className="finops-decision-roi-shell finops-decision-roi-loading" aria-label="正在读取 ROI 运营判断">
      <section className="finops-decision-roi-banner"><i /><span><b /><i /></span></section>
      <section className="finops-decision-roi-metrics">
        {Array.from({ length: 4 }, (_, index) => <article key={index}><i /><b /><i /></article>)}
      </section>
      <section className="finops-decision-roi-columns">
        <article><i /><b /><span /></article>
        <article><i /><b /><span /></article>
      </section>
      <section className="finops-decision-roi-wide"><i /><b /><span /></section>
    </div>
  );
}


function RoiMetricCard({ metric, onAsk }) {
  return (
    <article className="finops-decision-roi-metric">
      <header>
        <span>{metric.label}</span>
        <DecisionBadge status={metric.status}>{metric.badge}</DecisionBadge>
      </header>
      <strong>{metric.valueLabel}</strong>
      <p>{metric.explanation || "当前指标由服务端决策口径返回。"}</p>
      {onAsk ? (
        <button type="button" onClick={() => onAsk(metric)}>
          问 AI <ArrowRight size={12} aria-hidden="true" />
        </button>
      ) : null}
    </article>
  );
}


function EvidenceActions({ stages, onEvidence }) {
  if (!onEvidence) return null;
  const available = stages
    .map((stage) => ({
      ...stage,
      requestEvidenceRefs: stage.evidenceRefs.filter((reference) => /^req_[A-Za-z0-9_-]{1,124}$/.test(reference)),
    }))
    .filter((stage) => stage.requestEvidenceRefs.length);
  if (!available.length) return null;
  return (
    <div className="finops-decision-roi-evidence-actions" aria-label="按阶段查看证据">
      <span>可打开的请求证据</span>
      {available.map((stage) => (
        <button
          type="button"
          key={stage.id}
          onClick={() => onEvidence({
            reason: `${stage.label}证据`,
            evidenceRefs: stage.requestEvidenceRefs,
          })}
        >
          {stage.label} · {stage.requestEvidenceRefs.length} 条
        </button>
      ))}
    </div>
  );
}


function UnitEconomicsTrend({ items }) {
  if (!items.length) {
    return <LocalEmpty>当前范围没有可展示的单位效能趋势。</LocalEmpty>;
  }
  return (
    <div className="finops-decision-roi-trend-wrap">
      <table className="finops-decision-roi-trend">
        <caption>单位效能趋势明细</caption>
        <thead><tr><th scope="col">周期</th><th scope="col">指标</th><th scope="col">数值</th><th scope="col">证据状态</th></tr></thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <th scope="row">{item.period || item.label}</th>
              <td>{item.label}</td>
              <td>{item.valueLabel}</td>
              <td><DecisionBadge status={item.status}>{item.badge}</DecisionBadge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


export function RoiDecisionPage({
  payload = null,
  loading = false,
  updating = false,
  error = "",
  onRetry = null,
  onAdjustScenario = null,
  onEvidence = null,
  onAsk = null,
}) {
  if (loading && !payload) return <RoiLoadingShell />;
  if (error && !payload) {
    return (
      <section className="finops-decision-page-error" role="alert">
        <AlertTriangle size={18} aria-hidden="true" />
        <span><b>ROI 运营判断暂时无法读取</b><small>{error}</small></span>
        {onRetry ? <button type="button" onClick={onRetry}><RefreshCw size={13} />重试</button> : null}
      </section>
    );
  }

  const view = roiDecisionView(payload);
  const hasVerifiedRoi = view.verifiedRoiStatus === "verified"
    && view.verifiedRoiValue !== null;

  return (
    <div className="finops-decision-roi-shell">
      {error && payload ? (
        <div className="finops-decision-page-warning" role="status">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>更新失败，当前继续展示最近一次成功结果。</span>
          {onRetry ? <button type="button" onClick={onRetry}>重新更新</button> : null}
        </div>
      ) : null}

      <section className="finops-decision-roi-banner" aria-labelledby="finops-roi-decision-title">
        <div className="finops-decision-roi-banner-copy">
          <span className="finops-decision-eyebrow">本期运营判断</span>
          <div className="finops-decision-roi-title-row">
            <h2 id="finops-roi-decision-title">{view.decision.title || "当前证据不足以形成判断"}</h2>
            <DecisionBadge status={view.decision.status}>{view.decision.badge}</DecisionBadge>
            <span className="finops-decision-roi-updating" aria-live="polite">
              {updating ? <><RefreshCw className="spin" size={12} />正在更新</> : null}
            </span>
          </div>
          <p>{view.decision.description}</p>
          <small>情景测算与已验证业务结果分开展示；估算值不代表已实现回报。</small>
        </div>
        <div className="finops-decision-roi-banner-side">
          <aside className="finops-decision-roi-verified" aria-label="已验证 ROI">
            <span>已验证 ROI</span>
            <div>
              <strong>{view.verifiedRoiLabel}</strong>
              <DecisionBadge status={view.verifiedRoiStatus}>{view.verifiedRoiBadge}</DecisionBadge>
            </div>
            <small>{hasVerifiedRoi ? "仅计入已验证业务结果" : "估算情景不计入此结果"}</small>
          </aside>
          <div className="finops-decision-roi-banner-actions">
            {onAdjustScenario ? (
              <button type="button" onClick={onAdjustScenario}>
                <SlidersHorizontal size={14} aria-hidden="true" />调整测算参数
              </button>
            ) : null}
          </div>
        </div>
      </section>

      <section className="finops-decision-roi-section" aria-labelledby="finops-roi-metrics-title">
        <header className="finops-decision-roi-section-head">
          <div><span>同一情景 · 同一统计窗口</span><h2 id="finops-roi-metrics-title">本期月度测算</h2></div>
          <small>每项数值保留服务端返回的估算、已验证或不可用状态</small>
        </header>
        {view.metrics.length ? (
          <div className="finops-decision-roi-metrics">
            {view.metrics.slice(0, 4).map((metric) => <RoiMetricCard key={metric.id} metric={metric} onAsk={onAsk} />)}
          </div>
        ) : <LocalEmpty>当前还没有可展示的月度测算。</LocalEmpty>}
      </section>

      <section className="finops-decision-roi-columns">
        <article className="finops-decision-roi-panel">
          <header className="finops-decision-roi-panel-head"><div><span>价值构成</span><h2>价值桥</h2></div><small>同一测算口径下，收益减去投入得到净收益</small></header>
          <ValueBridge
            items={view.valueBridge.items}
            formulaRevision={view.valueBridge.formulaRevision}
            paybackLabel={view.valueBridge.paybackLabel}
            roiLabel={view.metrics.find((metric) => metric.id === "roi_ratio")?.valueLabel || ""}
            description={view.valueBridge.description}
          />
        </article>
        <article className="finops-decision-roi-panel">
          <header className="finops-decision-roi-panel-head"><div><span>从投入到结果</span><h2>证据成熟度</h2></div><small>只有业务结果完成验证后才进入可复核 ROI</small></header>
          <EvidenceMaturity
            stages={view.evidenceMaturity.stages}
            scoreLabel={view.evidenceMaturity.scoreLabel}
            formulaRevision={view.evidenceMaturity.formulaRevision}
            description={view.evidenceMaturity.description}
          />
          <EvidenceActions stages={view.evidenceMaturity.stages} onEvidence={onEvidence} />
        </article>
      </section>

      <section className="finops-decision-roi-wide">
        <header className="finops-decision-roi-panel-head"><div><span>成本、使用与产出的共同变化</span><h2>单位效能趋势</h2></div><small>按周期展示服务端返回的单位效能与证据状态</small></header>
        <UnitEconomicsTrend items={view.unitEconomicsTrend} />
      </section>

      <section className="finops-decision-roi-capability" aria-label="ROI 能力与口径">
        <header><span>能力说明</span><h2>平台自动确认与业务侧补充验证</h2></header>
        <FinOpsCapabilityNote capability={view.capability} />
      </section>

    </div>
  );
}
