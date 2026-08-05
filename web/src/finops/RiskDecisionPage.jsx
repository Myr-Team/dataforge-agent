import React from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Database,
  FileSearch,
  Loader2,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import { riskDecisionView, riskScanView } from "../finopsDecisionViewModel.js";
import { OpportunityPortfolio, RiskMatrix } from "./DecisionCharts.jsx";


const DOMAIN_ORDER = [
  ["cost", "成本"],
  ["experience", "体验"],
  ["efficiency", "效率"],
  ["governance", "治理"],
];
const REQUEST_REFERENCE = /^req_[A-Za-z0-9_-]{1,124}$/;


export function riskCacheNotice(payload, updating = false) {
  const status = String(payload?.freshness?.query_cache?.status || "");
  if (["hit_stale", "revalidating"].includes(status)) {
    return updating || status === "revalidating"
      ? "正在使用最近一次结果，后台更新中"
      : "正在使用最近一次结果";
  }
  return updating ? "后台更新中" : "";
}


function StatusBadge({ status = "unavailable", children = "状态待确认" }) {
  return <span className={`finops-decision-status finops-decision-status-${status}`}>{children}</span>;
}


function LocalEmpty({ children }) {
  return (
    <div className="finops-decision-page-empty" role="status">
      <Database size={17} aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}


function RiskLoadingShell() {
  return (
    <div className="finops-decision-risk-shell finops-decision-risk-loading" aria-label="正在读取风险运营判断">
      <section className="finops-decision-risk-banner"><i /><span><b /><i /></span></section>
      <section className="finops-decision-risk-domains">{DOMAIN_ORDER.map(([id]) => <article key={id}><i /><b /></article>)}</section>
      <section className="finops-decision-risk-columns"><article><i /><b /><span /></article><article><i /><b /><span /></article></section>
      <section className="finops-decision-risk-wide"><i /><b /><span /></section>
    </div>
  );
}


export function resolveSelectedRisk(selectedRiskId, priorities = []) {
  const rows = Array.isArray(priorities) ? priorities.filter((item) => item?.id) : [];
  if (selectedRiskId === undefined) return rows[0] || null;
  if (selectedRiskId === null) return null;
  return rows.find((item) => item.id === selectedRiskId) || null;
}


function requestRefsOf(priority) {
  return (Array.isArray(priority?.evidenceRefs) ? priority.evidenceRefs : [])
    .filter((reference) => REQUEST_REFERENCE.test(reference));
}


function cacheLabel(value) {
  return {
    hit: "缓存命中",
    miss: "缓存未命中",
    bypassed: "绕过缓存",
    unavailable: "缓存状态未记录",
  }[value] || "缓存状态未记录";
}


function resultLabel(value) {
  return {
    succeeded: "调用成功",
    failed: "调用失败",
    unavailable: "结果未记录",
  }[value] || "结果未记录";
}


function EvidenceSummary({ item }) {
  const technicalRows = [
    ["请求 ID", item.technical?.requestRef || item.requestRef],
    ["运行 ID", item.technical?.runId],
    ["Trace ID", item.technical?.traceId],
    ["关联 ID", item.technical?.correlationId],
  ].filter(([, value]) => value);
  return (
    <article className="finops-decision-risk-evidence-card">
      <header>
        <span><b>{item.requestName || "已记录请求"}</b><small>{item.operation || "工作区操作"}</small></span>
        <StatusBadge status={item.status === "succeeded" ? "observed" : item.status}>{resultLabel(item.status)}</StatusBadge>
      </header>
      <dl>
        <div><dt>信号</dt><dd>{item.signal.metric || "请求"} · {item.signal.valueLabel}</dd></div>
        <div><dt>缓存</dt><dd>{cacheLabel(item.cacheState)}</dd></div>
        <div><dt>时延</dt><dd>{item.latencyMs === null ? "未记录" : `${item.latencyMs} ms`}</dd></div>
        <div><dt>结果</dt><dd>{item.errorCategory || resultLabel(item.status)}</dd></div>
      </dl>
      {item.visibleAnswerSummary ? <p className="finops-decision-risk-visible-answer">{item.visibleAnswerSummary}</p> : null}
      {technicalRows.length ? (
        <details className="finops-decision-risk-technical">
          <summary>技术详情</summary>
          <dl>{technicalRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        </details>
      ) : null}
    </article>
  );
}


function PriorityList({ items, selectedId, onSelect }) {
  if (!items.length) return <LocalEmpty>当前没有达到证据门槛的优先事项。</LocalEmpty>;
  return (
    <ol className="finops-decision-risk-priorities" aria-label="风险优先事项">
      {items.map((item, index) => (
        <li key={item.id} className={selectedId === item.id ? "selected" : ""}>
          <button type="button" aria-pressed={selectedId === item.id} onClick={() => onSelect?.(item.id)}>
            <span className="finops-decision-order">{index + 1}</span>
            <span><b>{item.label}</b><small>{item.domainLabel} · 置信度 {item.confidenceLabel} · 实施难度 {item.effortLabel}</small></span>
            <strong>{item.impactLabel}</strong>
          </button>
        </li>
      ))}
    </ol>
  );
}


function scanTimeLabel(value) {
  if (!value) return "尚未执行";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间待确认";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}


function RiskScanWorkbench({ scan, loading, busy, error, onRun, onEvidence, onAsk }) {
  const view = riskScanView(scan);
  const summaryItems = [
    ["检查规则", view.summary.evaluated, "条"],
    ["需关注", view.summary.triggered, "项"],
    ["规则正常", view.summary.clear, "项"],
    ["待补证据", view.summary.insufficient + view.summary.unavailable, "项"],
    ["请求样本", view.summary.sampleCount, "次"],
  ];
  return (
    <section className="finops-risk-scan" aria-labelledby="finops-risk-scan-title">
      <header className="finops-risk-scan-head">
        <div>
          <span className="finops-decision-eyebrow">只读规则扫描</span>
          <h2 id="finops-risk-scan-title">检查当前筛选范围的运营风险</h2>
          <p>读取请求、成本、时延、缓存与统一入口证据；扫描不会修改模型、缓存或生产策略。</p>
        </div>
        <div className="finops-risk-scan-actions">
          <small>{loading ? "正在读取最近结果" : `上次扫描 ${scanTimeLabel(view.finishedAt || view.startedAt)}`}</small>
          <button type="button" onClick={onRun} disabled={busy || loading}>
            {busy ? <Loader2 className="spin" size={14} /> : <ScanSearch size={14} />}
            {busy ? "扫描中" : view.isAvailable ? "重新扫描" : "执行风险扫描"}
          </button>
        </div>
      </header>
      {error ? <div className="finops-risk-scan-error" role="alert"><AlertTriangle size={14} />{error}</div> : null}
      {view.isAvailable ? (
        <>
          <div className="finops-risk-scan-summary" aria-label="扫描摘要">
            {summaryItems.map(([label, value, unit]) => (
              <div key={label}><small>{label}</small><strong>{value}<i>{unit}</i></strong></div>
            ))}
          </div>
          <details className="finops-risk-scan-disclosure">
            <summary>
              <span><b>判定规则</b><small>展开查看七项运营检查的观测值、阈值与证据</small></span>
              <span>{view.summary.triggered} 项需关注 · {view.findings.length} 条规则</span>
            </summary>
            <div className="finops-risk-scan-rule-head">
              <div><span>规则依据</span><h3>七项运营检查</h3></div>
              <small>策略版本 {view.policyRevision || "待确认"} · 证据覆盖 {view.summary.evidenceCoveragePct === null ? "待确认" : `${view.summary.evidenceCoveragePct}%`}</small>
            </div>
            <ol className="finops-risk-scan-rules">
              {view.findings.map((finding) => (
                <li key={finding.policy} className={`status-${finding.status}`}>
                  <div className="finops-risk-scan-rule-title">
                    <span className={`finops-risk-scan-rule-dot severity-${finding.severity}`} />
                    <div><b>{finding.label}</b><small>{finding.reason}</small></div>
                    <StatusBadge status={finding.status}>{finding.statusLabel}</StatusBadge>
                  </div>
                  <dl>
                    <div><dt>观测值</dt><dd>{finding.observedLabel}</dd></div>
                    <div><dt>阈值</dt><dd>{finding.thresholdLabel}</dd></div>
                    <div><dt>样本</dt><dd>{finding.sampleCount} / {finding.minimumSamples}</dd></div>
                  </dl>
                  <p>{finding.recommendation}</p>
                  <div className="finops-risk-scan-rule-actions">
                    {onEvidence && finding.evidenceRefs.length ? (
                      <button type="button" onClick={() => onEvidence({
                        reason: `${finding.label}扫描证据`,
                        policyType: finding.policy,
                        evidenceRefs: finding.evidenceRefs,
                      })}><FileSearch size={12} />查看证据</button>
                    ) : <span>当前没有可下钻请求</span>}
                    {onAsk ? (
                      <button type="button" onClick={() => onAsk({
                        id: `risk_scan_${finding.policy}`,
                        label: finding.label,
                        value: finding.observedValue,
                        unit: finding.unit,
                        dataStatus: finding.status === "unavailable" ? "unavailable" : "complete",
                        evidenceState: finding.evidenceRefs.length ? "observed" : "partial",
                      })}>问 AI</button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
            <footer><ShieldCheck size={13} />扫描只生成可复核判断；未触发自动整改或生产动作。</footer>
          </details>
        </>
      ) : loading ? (
        <div className="finops-risk-scan-empty"><Loader2 className="spin" size={16} />正在读取最近一次扫描</div>
      ) : (
        <div className="finops-risk-scan-empty"><ScanSearch size={17} />执行一次扫描后，这里会显示七项规则的数值、阈值、样本与证据。</div>
      )}
    </section>
  );
}


function EvidenceChain({ priority, evidence, onEvidence, onAsk, onCreateDraft, onAcknowledge, onSuppress, draftEnabled, busyId }) {
  if (!priority) return <LocalEmpty>选择一个风险点后，这里会联动展示其证据与整改入口。</LocalEmpty>;
  const requestEvidenceRefs = requestRefsOf(priority);
  const evidenceByRef = new Set(requestEvidenceRefs);
  const selectedEvidence = evidence.filter((item) => evidenceByRef.has(item.requestRef));
  const canAcknowledge = priority.applicableActions.includes("acknowledge");
  const canSuppress = priority.applicableActions.includes("suppress");
  const stages = [
    ["信号", "已识别", `${priority.policyLabel} · ${priority.domainLabel}`],
    ["影响范围", priority.sampleCount === null ? "未记录" : `${priority.sampleCount} 次请求`, `业务影响 ${priority.impactLevelLabel}`],
    ["代表证据", selectedEvidence.length ? `${selectedEvidence.length} 条请求证据` : "暂无可下钻请求", "仅请求证据可打开详情"],
    ["改善验证", "待验证", "保存整改草案后复核"],
  ];
  return (
    <section className="finops-decision-risk-chain" aria-labelledby="finops-risk-chain-title">
      <header className="finops-decision-risk-section-head">
        <div><span>当前选择 · {priority.domainLabel}</span><h2 id="finops-risk-chain-title">{priority.label}</h2></div>
        <div className="finops-decision-risk-section-actions">
          {onAsk ? (
            <button type="button" className="quiet" onClick={() => onAsk({
              id: `risk_${priority.policy}`,
              label: priority.label,
              value: priority.sampleCount,
              unit: " 次请求",
              dataStatus: priority.evidenceRefs.length ? "complete" : "partial",
              evidenceState: priority.evidenceRefs.length ? "observed" : "partial",
            })}>问 AI</button>
          ) : null}
          <button type="button" className="quiet" data-finops-remediation-trigger onClick={() => onCreateDraft?.(priority)} disabled={!draftEnabled}>查看整改方案</button>
        </div>
      </header>
      <ol className="finops-decision-risk-chain-stages">
        {stages.map(([label, value, note], index) => (
          <li key={label}><span>{index + 1}</span><div><small>{label}</small><b>{value}</b><p>{note}</p></div></li>
        ))}
      </ol>
      <div className="finops-decision-risk-assessment">
        <article className="finops-decision-risk-recommendation">
          <small>处置建议</small>
          <p>{priority.summary || "当前尚未形成具体处置建议，请先复核代表证据后再创建整改草案。"}</p>
          <span>建议只作为候选判断；保存草案并完成验证前，不视为已完成改善。</span>
        </article>
        <dl className="finops-decision-risk-facts" aria-label="判定依据">
          <div><dt>判定依据</dt><dd>{priority.policyLabel}</dd></div>
          <div><dt>证据置信度</dt><dd>{priority.confidenceLabel}</dd></div>
          <div><dt>实施难度</dt><dd>{priority.effortLabel}</dd></div>
          <div><dt>预计影响</dt><dd>{priority.impactLabel}</dd></div>
        </dl>
      </div>
      <div className="finops-decision-risk-evidence-head">
        <div><span>可复核请求</span><h3>代表证据</h3></div>
        <div>
          {onEvidence && requestEvidenceRefs.length ? (
            <button type="button" onClick={() => onEvidence({ reason: `${priority.label}证据`, evidenceRefs: requestEvidenceRefs })}>
              <FileSearch size={13} />查看证据
            </button>
          ) : null}
          {canAcknowledge ? <button type="button" disabled={busyId === priority.anomalyId} onClick={() => onAcknowledge?.(priority)}>确认异常</button> : null}
          {canSuppress ? <button type="button" disabled={busyId === priority.anomalyId} onClick={() => onSuppress?.(priority)}>抑制异常</button> : null}
        </div>
      </div>
      {selectedEvidence.length
        ? <div className="finops-decision-risk-evidence-list">{selectedEvidence.map((item) => <EvidenceSummary key={item.requestRef} item={item} />)}</div>
        : <LocalEmpty>此风险当前没有服务端关联的请求级证据，不会跳转到无关请求。</LocalEmpty>}
    </section>
  );
}


export function RiskDecisionPage({
  payload = null,
  loading = false,
  updating = false,
  error = "",
  mutationError = "",
  busyId = "",
  selectedRiskId = undefined,
  onSelectRisk = null,
  onRetry = null,
  onEvidence = null,
  onAsk = null,
  onCreateDraft = null,
  onAcknowledge = null,
  onSuppress = null,
  scan = null,
  scanLoading = false,
  scanBusy = false,
  scanError = "",
  onRunScan = null,
}) {
  if (loading && !payload) return <RiskLoadingShell />;
  if (error && !payload) {
    return (
      <section className="finops-decision-page-error" role="alert">
        <AlertTriangle size={18} aria-hidden="true" />
        <span><b>风险运营判断暂时无法读取</b><small>{error}</small></span>
        {onRetry ? <button type="button" onClick={onRetry}><RefreshCw size={13} />重试</button> : null}
      </section>
    );
  }

  const view = riskDecisionView(payload);
  const cacheNotice = riskCacheNotice(payload, updating);
  const selected = resolveSelectedRisk(selectedRiskId, view.priorities);
  const selectedId = selected?.id || "";
  return (
    <div className="finops-decision-risk-shell">
      {error && payload ? (
        <div className="finops-decision-page-warning" role="status">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>更新失败，当前继续展示最近一次成功结果。</span>
          {onRetry ? <button type="button" onClick={onRetry}>重新更新</button> : null}
        </div>
      ) : null}
      {cacheNotice ? (
        <div className="finops-decision-page-warning" role="status">
          <RefreshCw className={updating ? "spin" : ""} size={14} aria-hidden="true" />
          <span>{cacheNotice}</span>
        </div>
      ) : null}
      {mutationError ? (
        <div className="finops-decision-page-warning" role="alert">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>{mutationError}</span>
        </div>
      ) : null}

      <section className="finops-decision-risk-banner" aria-labelledby="finops-risk-decision-title">
        <div>
          <span className="finops-decision-eyebrow">本期风险判断</span>
          <div className="finops-decision-risk-title-row">
            <h2 id="finops-risk-decision-title">{view.decision.title || "当前没有可排序的风险证据"}</h2>
            <StatusBadge status={view.decision.status}>{view.decision.badge}</StatusBadge>
            <span className="finops-decision-risk-updating" aria-live="polite">{updating ? <><RefreshCw className="spin" size={12} />后台更新中</> : null}</span>
          </div>
          <p>{view.decision.description}</p>
          <small>风险按证据置信度、业务影响与真实影响范围展示，不生成无法解释的复合分数。</small>
        </div>
      </section>

      <RiskScanWorkbench
        scan={scan}
        loading={scanLoading}
        busy={scanBusy}
        error={scanError}
        onRun={onRunScan}
        onEvidence={onEvidence}
        onAsk={onAsk}
      />

      <section className="finops-decision-risk-domains" aria-label="四个风险治理域">
        {DOMAIN_ORDER.map(([id, label]) => {
          const domain = view.riskDomains.find((item) => item.id === id);
          return <article key={id} className={`domain-${id}`}><span>{label}</span><strong>{domain?.count ?? "—"}</strong><small>{domain?.count === null || domain === undefined ? "未记录" : "当前风险项"}</small></article>;
        })}
      </section>

      <section className="finops-decision-risk-columns">
        <article className="finops-decision-risk-panel">
          <header className="finops-decision-risk-section-head"><div><span>业务影响 × 证据置信度</span><h2>风险矩阵</h2></div><small>气泡大小代表服务端返回的影响范围</small></header>
          <RiskMatrix points={view.matrix} selectedId={selectedId} onSelect={onSelectRisk} />
        </article>
        <article className="finops-decision-risk-panel">
          <header className="finops-decision-risk-section-head"><div><span>与矩阵联动</span><h2>优先事项</h2></div><small>选择后仅在本页切换证据</small></header>
          <PriorityList items={view.priorities} selectedId={selectedId} onSelect={onSelectRisk} />
        </article>
      </section>

      <EvidenceChain
        priority={selected}
        evidence={view.evidence}
        onEvidence={onEvidence}
        onAsk={onAsk}
        onCreateDraft={onCreateDraft}
        onAcknowledge={onAcknowledge}
        onSuppress={onSuppress}
        draftEnabled={view.governance.draftEnabled}
        busyId={busyId}
      />

      <section className="finops-decision-risk-wide">
        <header className="finops-decision-risk-section-head"><div><span>价值、难度与影响范围</span><h2>优化组合</h2></div><small>用于排序，不代表自动执行顺序</small></header>
        <OpportunityPortfolio data={view.portfolio} selectedId={selectedId} onSelect={onSelectRisk} />
      </section>

      {view.insight ? (
        <section className="finops-decision-risk-insight" aria-labelledby="finops-risk-insight-title">
          <span><Bot size={17} aria-hidden="true" /></span>
          <div><small>最新 AI 解读 · 已保存证据</small><h2 id="finops-risk-insight-title">{view.insight.title || "运营分析说明"}</h2><p>{view.insight.summary || "当前没有可展示的分析说明。"}</p></div>
          <StatusBadge status={view.insight.status}>{view.insight.badge}</StatusBadge>
        </section>
      ) : null}

      <section className="finops-decision-risk-governance" aria-label="治理边界">
        <span><ShieldCheck size={17} aria-hidden="true" /></span>
        <div><small>治理边界</small><h2>建议、草案与生产变更保持分离</h2><p>AI 只解释现有证据；整改入口保存结构化草案，不批准、不执行生产变更。</p></div>
        <div className="finops-decision-risk-governance-state">
          <span><CheckCircle2 size={13} />只读判断</span>
          <span><Wrench size={13} />{view.governance.draftEnabled ? "草案可保存" : "草案未开放"}</span>
        </div>
      </section>
    </div>
  );
}
