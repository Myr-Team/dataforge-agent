import React, { useEffect, useId, useMemo, useState } from "react";
import { CircleHelp, Database } from "lucide-react";
import { ViewportTooltip, useViewportTooltipAnchor } from "./ViewportTooltip.jsx";

const SAFE_EVIDENCE_STATUSES = new Set([
  "observed",
  "estimated",
  "verified",
  "partial",
  "unavailable",
  "not_recorded",
  "complete",
  "available",
]);


function safeEvidenceStatus(value) {
  return SAFE_EVIDENCE_STATUSES.has(value) ? value : "unavailable";
}


function safeValueDirection(value) {
  return ["negative", "positive", "zero", "unavailable"].includes(value)
    ? value
    : "unavailable";
}


function safeBarWidth(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(100, Math.max(0, value))
    : 0;
}


export function resolveRiskPointSelection(selectedId, validIds) {
  return typeof selectedId === "string" && Array.isArray(validIds) && validIds.includes(selectedId)
    ? selectedId
    : "";
}


export function toggleRiskPointSelection(currentId, pointId, validIds) {
  const nextId = resolveRiskPointSelection(pointId, validIds);
  if (!nextId) return "";
  return resolveRiskPointSelection(currentId, validIds) === nextId ? "" : nextId;
}


export function resolvePortfolioSelection(
  selectedId,
  tappedId,
  validIds,
  selectionProvided,
) {
  const ids = Array.isArray(validIds) ? validIds : [];
  if (selectionProvided) {
    return typeof selectedId === "string" && ids.includes(selectedId)
      ? selectedId
      : "";
  }
  if (typeof tappedId === "string" && ids.includes(tappedId)) return tappedId;
  return ids[0] || "";
}


function DecisionEmpty({ children = "当前范围没有可展示的数据。" }) {
  return (
    <div className="finops-decision-empty" role="status">
      <Database size={18} aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}


function DecisionTooltip({ label, children }) {
  const tooltipId = useId();
  const { anchorRef, open, toggle, anchorProps } = useViewportTooltipAnchor();
  if (!children) return null;
  return (
    <>
      <span className="finops-decision-help">
        <button
          {...anchorProps}
          ref={anchorRef}
          type="button"
          aria-label={`${label}说明`}
          aria-describedby={tooltipId}
          aria-expanded={open}
          onClick={toggle}
        >
          <CircleHelp size={13} aria-hidden="true" />
        </button>
      </span>
      <ViewportTooltip anchorRef={anchorRef} open={open} id={tooltipId} variant="finops-decision-tooltip-content">
        {children}
      </ViewportTooltip>
    </>
  );
}


function EvidenceBadge({ status = "unavailable", children }) {
  const safeStatus = safeEvidenceStatus(status);
  return <span className={`finops-decision-badge finops-decision-badge-${safeStatus}`}>{children || "状态待确认"}</span>;
}


export function ValueBridge({
  items = [],
  formulaRevision = "",
  paybackLabel = "",
  description = "",
}) {
  const rows = Array.isArray(items) ? items.filter((item) => item?.id) : [];
  if (!rows.length) return <DecisionEmpty>当前没有可展示的价值构成。</DecisionEmpty>;
  return (
    <div className="finops-decision-value-bridge" aria-label={description || "价值构成"}>
      <div className="finops-decision-value-bars">
        {rows.map((item) => (
          <div
            className="finops-decision-value-row finops-decision-tooltip-boundary"
            key={item.id}
            aria-label={`${item.label}：${item.valueLabel}；${item.directionLabel || "方向不可用"}；${item.badge || "状态待确认"}`}
          >
            <span className={`finops-decision-value-label ${item.explanation ? "finops-decision-value-label-help" : ""}`}>
              <b>{item.label}</b>
              {item.explanation ? (
                <DecisionTooltip label={item.label}>{item.explanation}</DecisionTooltip>
              ) : null}
            </span>
            <div className="finops-decision-value-track">
              <span className="finops-decision-zero-axis" aria-hidden="true" />
              <i
                className={`finops-decision-value-bar finops-decision-value-${safeEvidenceStatus(item.status)} finops-decision-direction-${safeValueDirection(item.direction)}`}
                aria-hidden="true"
                style={{
                  "--finops-decision-bar-width": `${safeBarWidth(item.barPct)}%`,
                  "--finops-decision-bar-half-width": `${safeBarWidth(item.barPct) / 2}%`,
                }}
              />
            </div>
            <span className="finops-decision-value-result">
              <strong>{item.valueLabel}</strong>
              <small>{item.directionLabel || "方向不可用"}{item.unitLabel ? ` · ${item.unitLabel}` : ""}</small>
            </span>
          </div>
        ))}
      </div>
      {formulaRevision || paybackLabel ? (
        <footer className="finops-decision-chart-meta">
          {formulaRevision ? <span>公式版本：<code>{formulaRevision}</code></span> : null}
          {paybackLabel ? <span>预计回收周期：<b>{paybackLabel}</b></span> : null}
        </footer>
      ) : null}
    </div>
  );
}


export function EvidenceMaturity({
  stages = [],
  scoreLabel = "",
  formulaRevision = "",
  description = "",
}) {
  const rows = Array.isArray(stages) ? stages.filter((item) => item?.id) : [];
  if (!rows.length) return <DecisionEmpty>当前没有可展示的证据阶段。</DecisionEmpty>;
  return (
    <div className="finops-decision-maturity" aria-label={description || "证据成熟度"}>
      {scoreLabel ? (
        <header className="finops-decision-maturity-head">
          <span>当前成熟度</span>
          <strong>{scoreLabel}</strong>
          {formulaRevision ? <small>公式版本 {formulaRevision}</small> : null}
        </header>
      ) : null}
      <ol className="finops-decision-maturity-stages">
        {rows.map((stage) => (
          <li
            className={`finops-decision-tooltip-boundary ${stage.complete ? "finops-decision-maturity-complete" : ""}`}
            key={stage.id}
            aria-label={stage.description || `${stage.label}：${stage.valueLabel}；${stage.badge}`}
          >
            <i aria-hidden="true" />
            <div>
              <header>
                <span>{stage.label}</span>
                {stage.evidenceGap ? (
                  <DecisionTooltip label={stage.label}>{stage.evidenceGap}</DecisionTooltip>
                ) : null}
              </header>
              <strong>{stage.valueLabel}</strong>
              <EvidenceBadge status={stage.status}>{stage.badge}</EvidenceBadge>
              {stage.evidenceCount !== null && stage.evidenceCount !== undefined ? (
                <small>{stage.evidenceCount} 条证据</small>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}


function isHighSignal(point, visualKey, sourceKey) {
  const visual = Number(point?.[visualKey]);
  if (Number.isFinite(visual)) return visual > 50;
  const source = Number(point?.[sourceKey]);
  return Number.isFinite(source) && source >= 3;
}


export function riskQuadrants(points = []) {
  const quadrants = [
    { id: "priority", label: "优先处置", hint: "影响高 · 证据强", items: [] },
    { id: "validate", label: "重点验证", hint: "影响高 · 证据待补", items: [] },
    { id: "improve", label: "计划改善", hint: "影响可控 · 证据强", items: [] },
    { id: "observe", label: "持续观察", hint: "影响可控 · 证据待补", items: [] },
  ];
  for (const point of Array.isArray(points) ? points : []) {
    if (!point?.id) continue;
    const highConfidence = isHighSignal(point, "visualX", "xConfidence");
    const highImpact = isHighSignal(point, "visualY", "yImpact");
    const target = highImpact
      ? (highConfidence ? quadrants[0] : quadrants[1])
      : (highConfidence ? quadrants[2] : quadrants[3]);
    target.items.push(point);
  }
  return quadrants;
}


export function RiskMatrix({ points = [], selectedId = "", onSelect = null }) {
  const rows = Array.isArray(points) ? points.filter((point) => point?.id) : [];
  const [tappedId, setTappedId] = useState(null);
  const validIds = useMemo(() => rows.map((point) => point.id), [rows]);
  const validIdsKey = validIds.join("|");
  useEffect(() => setTappedId(null), [selectedId, validIdsKey]);
  if (!rows.length) return <DecisionEmpty>当前没有可定位到矩阵的风险证据。</DecisionEmpty>;
  const activeId = tappedId === null
    ? resolveRiskPointSelection(selectedId, validIds)
    : resolveRiskPointSelection(tappedId, validIds);
  return (
    <div className="finops-decision-matrix" aria-label="风险矩阵：按证据置信度与业务影响分组">
      <div className="finops-decision-risk-quadrants">
        {riskQuadrants(rows).map((quadrant) => (
          <section className={`finops-decision-risk-quadrant finops-decision-risk-quadrant-${quadrant.id}`} key={quadrant.id}>
            <header>
              <span><b>{quadrant.label}</b><small>{quadrant.hint}</small></span>
              <strong>{quadrant.items.length}</strong>
            </header>
            <div className="finops-decision-risk-rows">
              {quadrant.items.length ? quadrant.items.map((point) => (
                <button
                  type="button"
                  className={`finops-decision-risk-row finops-decision-domain-${point.domain} ${activeId === point.id ? "finops-decision-selected" : ""}`}
                  key={point.id}
                  aria-label={point.accessibleLabel}
                  aria-pressed={activeId === point.id}
                  onClick={() => {
                    const nextId = toggleRiskPointSelection(activeId, point.id, validIds);
                    setTappedId(nextId);
                    onSelect?.(nextId || null);
                  }}
                >
                  <i className={`finops-decision-domain-${point.domain}`} aria-hidden="true" />
                  <span>
                    <b>{point.label}</b>
                    <small>{point.domainLabel} · 置信度 {point.xConfidence} · 影响 {point.yImpact}</small>
                  </span>
                  <strong>{point.bubbleSize} 次请求</strong>
                </button>
              )) : <small className="finops-decision-risk-empty">当前无风险项</small>}
            </div>
          </section>
        ))}
      </div>
      <div className="finops-decision-domain-legend" aria-label="风险域图例">
        {["cost", "experience", "efficiency", "governance"].map((domain) => {
          const point = rows.find((item) => item.domain === domain);
          return point ? <span key={domain}><i className={`finops-decision-domain-${domain}`} />{point.domainLabel}</span> : null;
        })}
      </div>
    </div>
  );
}


export function OpportunityPortfolio(props = {}) {
  const { data = {}, onSelect = null } = props;
  const selectionProvided = Object.hasOwn(props, "selectedId");
  const selectedId = props.selectedId;
  const items = Array.isArray(data?.items) ? data.items.filter((item) => item?.id) : [];
  const points = Array.isArray(data?.points) ? data.points.filter((point) => point?.id) : [];
  const [tappedId, setTappedId] = useState("");
  const itemIds = useMemo(() => new Set(items.map((item) => item.id)), [items]);
  const activeId = resolvePortfolioSelection(
    selectedId,
    tappedId,
    [...itemIds],
    selectionProvided,
  );
  const pointsById = useMemo(() => new Map(points.map((point) => [point.id, point])), [points]);
  const yAxisLabel = data?.metadata?.yAxis || "预期影响";
  const choose = (id) => {
    setTappedId(id);
    onSelect?.(id);
  };
  if (!items.length && !points.length) {
    return <DecisionEmpty>当前没有可排序的优化机会。</DecisionEmpty>;
  }
  return (
    <div className="finops-decision-portfolio">
      {!points.length ? <p className="finops-decision-portfolio-note">当前缺少影响坐标，仍按服务端优先级展示。</p> : null}
      <ol className="finops-decision-opportunity-bars" aria-label="优化机会优先列表">
        {items.map((item, index) => {
          const point = pointsById.get(item.id);
          const width = point ? Math.max(10, safeBarWidth(point.visualY)) : 0;
          return (
          <li className={activeId === item.id ? "finops-decision-selected" : ""} key={item.id}>
            <button type="button" onClick={() => choose(item.id)} aria-pressed={activeId === item.id}>
              <span className="finops-decision-order">{index + 1}</span>
              <span className="finops-decision-portfolio-copy">
                <b>{item.label}</b>
                <small>{item.domainLabel} · 实施难度 {item.effortLabel} · 影响 {item.impactLevelLabel}</small>
                <span className="finops-decision-opportunity-track" aria-label={`${yAxisLabel} ${point?.y ?? "未记录"}`}>
                  <i className={`finops-decision-domain-${item.domain}`} style={{ width: `${width}%` }} />
                </span>
              </span>
              <strong>{item.impactLabel}</strong>
            </button>
          </li>
          );
        })}
      </ol>
    </div>
  );
}
