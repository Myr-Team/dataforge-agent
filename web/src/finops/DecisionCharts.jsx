import React, { useEffect, useId, useMemo, useState } from "react";
import { CircleHelp, Database } from "lucide-react";

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


export function decisionHelpInteraction(current = {}, action = "reset") {
  const state = {
    open: current?.open === true,
    dismissed: current?.dismissed === true,
  };
  if (action === "toggle") {
    return state.open
      ? { open: false, dismissed: true }
      : { open: true, dismissed: false };
  }
  if (action === "leave") {
    return state.dismissed ? { open: false, dismissed: false } : state;
  }
  if (action === "reset") return { open: false, dismissed: false };
  return state;
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
  const [interaction, setInteraction] = useState(() => decisionHelpInteraction());
  if (!children) return null;
  return (
    <span
      className={`finops-decision-help ${interaction.open ? "finops-decision-help-open" : ""} ${interaction.dismissed ? "finops-decision-help-dismissed" : ""}`.trim()}
      onMouseLeave={() => setInteraction((current) => decisionHelpInteraction(current, "leave"))}
    >
      <button
        type="button"
        aria-label={`${label}说明`}
        aria-describedby={tooltipId}
        aria-expanded={interaction.open}
        onClick={() => setInteraction((current) => decisionHelpInteraction(current, "toggle"))}
        onBlur={() => setInteraction((current) => decisionHelpInteraction(current, "reset"))}
      >
        <CircleHelp size={13} aria-hidden="true" />
      </button>
      <span className="finops-decision-tooltip" id={tooltipId} role="tooltip">
        {children}
      </span>
    </span>
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
          <div className="finops-decision-value-row finops-decision-tooltip-boundary" key={item.id}>
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
      <div className="finops-decision-table-wrap">
        <table className="finops-decision-table-fallback">
          <caption>价值构成明细</caption>
          <thead>
            <tr><th scope="col">项目</th><th scope="col">数值</th><th scope="col">单位</th><th scope="col">方向</th><th scope="col">证据状态</th></tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.id}>
                <th scope="row">{item.label}</th>
                <td>{item.valueLabel}</td>
                <td>{item.unitLabel || "未记录"}</td>
                <td>{item.directionLabel || "方向不可用"}</td>
                <td><EvidenceBadge status={item.status}>{item.badge}</EvidenceBadge></td>
              </tr>
            ))}
          </tbody>
        </table>
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


function pointAlignment(point) {
  const horizontal = point.visualX > 65
    ? "finops-decision-align-end"
    : point.visualX < 35
      ? "finops-decision-align-start"
      : "finops-decision-align-center";
  const vertical = point.visualY > 62
    ? "finops-decision-place-below"
    : "finops-decision-place-above";
  return `${horizontal} ${vertical}`;
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
    <div className="finops-decision-matrix" aria-label="风险矩阵：横轴为证据置信度，纵轴为业务影响">
      <span className="finops-decision-axis-y" aria-hidden="true">业务影响</span>
      <div className="finops-decision-matrix-plot">
        <span className="finops-decision-quadrant finops-decision-quadrant-top-left">重点验证</span>
        <span className="finops-decision-quadrant finops-decision-quadrant-top-right">优先处置</span>
        <span className="finops-decision-quadrant finops-decision-quadrant-bottom-left">持续观察</span>
        <span className="finops-decision-quadrant finops-decision-quadrant-bottom-right">计划改善</span>
        {rows.map((point) => {
          const tooltipId = `finops-decision-risk-${point.id}`;
          return (
            <span
              className={`finops-decision-matrix-point finops-decision-domain-${point.domain} ${pointAlignment(point)} ${activeId === point.id ? "finops-decision-selected" : ""}`}
              key={point.id}
              style={{
                "--finops-decision-point-x": `${point.visualX}%`,
                "--finops-decision-point-y": `${100 - point.visualY}%`,
                "--finops-decision-point-size": `${point.radius * 2}px`,
                "--finops-decision-point-radius": `${point.radius}px`,
                "--finops-decision-point-offset": `${-point.radius}px`,
              }}
            >
              <button
                type="button"
                aria-label={point.accessibleLabel}
                aria-describedby={tooltipId}
                aria-pressed={activeId === point.id}
                onClick={() => {
                  const nextId = toggleRiskPointSelection(activeId, point.id, validIds);
                  setTappedId(nextId);
                  onSelect?.(nextId || null);
                }}
              />
              <span className="finops-decision-point-tooltip" id={tooltipId} role="tooltip">
                <b>{point.label}</b>
                <span>风险域 <strong>{point.domainLabel}</strong></span>
                <span>证据置信度 <strong>{point.xConfidence}</strong></span>
                <span>业务影响 <strong>{point.yImpact}</strong></span>
                <span>影响范围 <strong>{point.bubbleSize} 次请求</strong></span>
              </span>
            </span>
          );
        })}
      </div>
      <span className="finops-decision-axis-x" aria-hidden="true">证据置信度</span>
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
  const xAxisLabel = data?.metadata?.xAxis || "横轴";
  const yAxisLabel = data?.metadata?.yAxis || "纵轴";
  const sizeLabel = data?.metadata?.size || "点大小";
  const choose = (id) => {
    setTappedId(id);
    onSelect?.(id);
  };
  if (!items.length && !points.length) {
    return <DecisionEmpty>当前没有可排序的优化机会。</DecisionEmpty>;
  }
  return (
    <div className="finops-decision-portfolio">
      <section className="finops-decision-portfolio-chart" aria-label="优化组合散点图">
        {points.length ? (
          <>
            <span className="finops-decision-axis-y" aria-hidden="true">{yAxisLabel}</span>
            <div className="finops-decision-portfolio-plot">
              {points.map((point) => {
                const tooltipId = `finops-decision-portfolio-${point.id}`;
                return (
                  <span
                    className={`finops-decision-portfolio-point finops-decision-domain-${point.domain} ${pointAlignment(point)} ${activeId === point.id ? "finops-decision-selected" : ""}`}
                    key={point.id}
                    style={{
                      "--finops-decision-point-x": `${point.visualX}%`,
                      "--finops-decision-point-y": `${100 - point.visualY}%`,
                      "--finops-decision-point-size": `${point.radius * 2}px`,
                      "--finops-decision-point-radius": `${point.radius}px`,
                      "--finops-decision-point-offset": `${-point.radius}px`,
                    }}
                  >
                    <button
                      type="button"
                      aria-label={point.accessibleLabel}
                      aria-describedby={tooltipId}
                      aria-pressed={activeId === point.id}
                      onClick={() => choose(point.id)}
                    />
                    <span className="finops-decision-point-tooltip" id={tooltipId} role="tooltip">
                      <b>{point.label}</b>
                      <span>{xAxisLabel} <strong>{point.x}</strong></span>
                      <span>{yAxisLabel} <strong>{point.y}</strong></span>
                      <span>{sizeLabel} <strong>{point.bubbleSize}</strong></span>
                    </span>
                  </span>
                );
              })}
            </div>
            <span className="finops-decision-axis-x" aria-hidden="true">{xAxisLabel}</span>
          </>
        ) : (
          <DecisionEmpty>当前缺少服务端坐标，优化机会仍可在列表中复核。</DecisionEmpty>
        )}
      </section>
      <ol className="finops-decision-portfolio-list" aria-label="优化机会优先列表">
        {items.map((item, index) => (
          <li className={activeId === item.id ? "finops-decision-selected" : ""} key={item.id}>
            <button type="button" onClick={() => choose(item.id)} aria-pressed={activeId === item.id}>
              <span className="finops-decision-order">{index + 1}</span>
              <span className="finops-decision-portfolio-copy">
                <b>{item.label}</b>
                <small>{item.domainLabel} · 实施难度 {item.effortLabel} · 影响 {item.impactLevelLabel}</small>
              </span>
              <strong>{item.impactLabel}</strong>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
