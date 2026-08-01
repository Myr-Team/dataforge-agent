import React, { useState } from "react";
import {
  AlertTriangle,
  Check,
  ClipboardCheck,
  FileCheck2,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  X,
} from "lucide-react";

import { remediationDraftView } from "../finopsDecisionViewModel.js";


const LIFECYCLE = [
  ["draft", "草案"],
  ["reviewed", "已复核"],
  ["pending_approval", "待审批"],
  ["promoted", "动作草案"],
  ["closed", "已关闭"],
];


function formatScalar(value) {
  if (value === null || value === undefined || value === "") return "未记录";
  if (typeof value === "boolean") return value ? "启用" : "停用";
  return String(value);
}


function ScopeSummary({ scope }) {
  const rows = [
    ["工作区", scope.workspaceId],
    ["Agent", scope.agentId],
    ["模型", scope.model],
    ["操作", scope.operation],
  ].filter(([, value]) => value);
  return rows.length ? <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl> : <p>当前仅限定到已授权工作区。</p>;
}


function ListSection({ title, icon: Icon, items, empty = "未记录" }) {
  return (
    <section className="finops-remediation-section">
      <header><Icon size={14} aria-hidden="true" /><h3>{title}</h3></header>
      {items.length ? <ul>{items.map((item, index) => <li key={`${title}:${index}`}>{item}</li>)}</ul> : <p>{empty}</p>}
    </section>
  );
}


export function RemediationDraftPanel({
  draft = null,
  busy = false,
  error = "",
  actionsEnabled = false,
  onClose = null,
  onCreate = null,
  onReload = null,
  onReview = null,
  onPromote = null,
}) {
  const view = remediationDraftView(draft);
  const [reason, setReason] = useState("");
  const canCreate = !view.isAvailable && Boolean(view.workspaceId && view.sourceOpportunityId && view.baseVersion && onCreate);
  const canReview = view.isAvailable && view.status === "draft" && Boolean(onReview);
  const canPromote = view.isAvailable
    && view.status === "reviewed"
    && view.executionCapability === "typed_action_available"
    && Boolean(onPromote);
  const activeIndex = Math.max(0, LIFECYCLE.findIndex(([status]) => status === view.status));

  return (
    <div className="finops-remediation-layer" role="presentation">
      <section className="finops-remediation-panel" role="dialog" aria-modal="true" aria-labelledby="finops-remediation-title">
        <header className="finops-remediation-head">
          <div><span>治理工作台</span><h2 id="finops-remediation-title">整改草案</h2><p>保存与复核候选方案，不会直接执行生产变更。</p></div>
          <button type="button" aria-label="关闭整改草案" onClick={onClose} disabled={busy}><X size={17} /></button>
        </header>

        {error ? <div className="finops-remediation-error" role="alert"><AlertTriangle size={14} /><span>{error}</span>{onReload ? <button type="button" onClick={onReload} disabled={busy}><RefreshCw size={12} />重新载入</button> : null}</div> : null}

        {!view.isAvailable ? (
          <div className="finops-remediation-create">
            <span><FileCheck2 size={20} aria-hidden="true" /></span>
            <div><h3>{view.title || "保存当前机会的整改草案"}</h3><p>服务端会根据最新授权证据生成固定字段；浏览器不会提交任意配置正文。</p></div>
            {!view.baseVersion ? <small>当前未返回可复核的配置基线，暂不能保存草案。</small> : null}
            <button type="button" onClick={() => onCreate?.()} disabled={busy || !canCreate}>{busy ? "正在保存" : "保存整改草案"}</button>
          </div>
        ) : (
          <>
            <section className="finops-remediation-summary">
              <div><span>{view.actionKindLabel}</span><h3>{view.title || "整改草案"}</h3><p>{view.summary || "当前草案没有补充说明。"}</p></div>
              <strong>{view.statusLabel}</strong>
            </section>

            <ol className="finops-remediation-lifecycle" aria-label="整改草案生命周期">
              {LIFECYCLE.map(([status, label], index) => (
                <li key={status} className={`${index <= activeIndex ? "reached" : ""} ${status === view.status ? "current" : ""}`}>
                  <span>{index < activeIndex ? <Check size={11} /> : index + 1}</span><small>{label}</small>
                </li>
              ))}
            </ol>

            <div className="finops-remediation-grid">
              <section className="finops-remediation-section">
                <header><ShieldCheck size={14} /><h3>来源证据</h3></header>
                <p>{view.evidenceRefs.length ? `${view.evidenceRefs.length} 条授权证据` : "当前没有请求级证据引用。"}</p>
                {view.evidenceRefs.length ? <details><summary>查看技术引用</summary><ul>{view.evidenceRefs.map((item) => <li key={item}>{item}</li>)}</ul></details> : null}
              </section>
              <section className="finops-remediation-section"><header><ClipboardCheck size={14} /><h3>适用范围</h3></header><ScopeSummary scope={view.scope} /></section>
              <section className="finops-remediation-section span-2">
                <header><FileCheck2 size={14} /><h3>候选修改</h3></header>
                {view.proposedChanges.length ? (
                  <div className="finops-remediation-changes">{view.proposedChanges.map((item) => <article key={item.field}><b>{item.label}</b><span>{formatScalar(item.currentValue)} <strong>→</strong> {formatScalar(item.candidateValue)}</span><p>{item.rationale}</p></article>)}</div>
                ) : <p>没有可展示的白名单修改项。</p>}
              </section>
              <section className="finops-remediation-section"><header><FileCheck2 size={14} /><h3>预期影响</h3></header><strong>{view.expectedImpact.label}</strong><p>{view.expectedImpact.calculationBasis || "当前影响仍需验证。"}</p></section>
              <ListSection title="前置条件" icon={ClipboardCheck} items={view.prerequisites} />
              <ListSection title="风险与护栏" icon={ShieldCheck} items={view.risksAndGuardrails} />
              <section className="finops-remediation-section">
                <header><ClipboardCheck size={14} /><h3>验证标准</h3></header>
                {view.verificationPlan.length ? <ul>{view.verificationPlan.map((item) => <li key={`${item.metric}:${item.operator}`}>{item.metricLabel} {item.operatorLabel} {formatScalar(item.target)} · 至少 {formatScalar(item.minimumSamples)} 个样本</li>)}</ul> : <p>验证标准未记录。</p>}
              </section>
              <ListSection title="回滚方案" icon={RotateCcw} items={view.rollbackPlan} />
            </div>

            <label className="finops-remediation-reason">
              <span>复核说明（可选）</span>
              <textarea value={reason} maxLength={300} onChange={(event) => setReason(event.target.value)} placeholder="仅记录有助于后续审批的简短说明" />
            </label>

            <footer className="finops-remediation-actions">
              <div>
                <b>{view.executionCapabilityLabel}</b>
                <small>{actionsEnabled ? "生产执行需进入独立审批与验证流程。" : "生产执行保持关闭；提升后最多生成审批动作草案。"}</small>
              </div>
              {canReview ? <button type="button" onClick={() => onReview?.({ baseRevision: view.revision, reason: reason.trim() })} disabled={busy}>复核草案</button> : null}
              {canPromote ? <button type="button" className="primary" onClick={() => onPromote?.({ baseRevision: view.revision, reason: reason.trim() })} disabled={busy}>提升为审批动作草案</button> : null}
              {view.executionCapability === "advisory_only" ? <span className="finops-remediation-advisory">仅供建议，不提供提升入口</span> : null}
            </footer>
          </>
        )}
      </section>
    </div>
  );
}
