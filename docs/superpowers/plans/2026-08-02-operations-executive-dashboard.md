# Operations Management Executive Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the crowded Operations Management overview with an honest four-KPI executive briefing, a real department-cost donut, a bounded attention list, and clear drill-downs while preserving the existing FinOps, ROI, risk, evidence, filters, and AI flows.

**Architecture:** Add one pure presentation projection that converts the existing bootstrap response into bounded executive cards, department cost slices, and attention items. Keep all business truth in existing APIs, render the new interactive overview inside the existing portal, and preserve the cache-first tab lifecycle. Remove repeated summaries from Cost Analysis and normalize the ROI investment label without changing server values or schemas.

**Tech Stack:** React 18, JavaScript ES modules, Vite, CSS, Node test runner, React server rendering tests, Playwright.

## Global Constraints

- No backend, SQL, Easy Auth, gateway, or price-card schema change.
- Overview displays exactly four primary KPI cards.
- Call quality uses observed success rate plus total calls; it never infers a successful-call count.
- Value maturity appears only when returned by the API; status text must not become an invented percentage.
- Department cost composition uses estimated cost, top three departments plus `Other`, and preserves `Unassigned`.
- Missing or incomparable cost renders an honest empty state; zero remains zero.
- Customer-facing copy must not expose infrastructure product names.
- Existing date, department, Agent, and model filters remain active and survive tab drill-down.
- Keep the ten-minute visible-page refresh and existing cache-first workspace-scoped store.
- Keep one floating Operations AI entry; do not add another chat surface.
- All new interactive chart targets must be keyboard reachable and use viewport-level unclipped tooltips.
- Preserve untracked `.superpowers/brainstorm/`, `test-results/`, `web/test-results/`, and `workspaces/ws-*` files.

---

## File structure

- Create `web/src/finopsExecutiveOverview.js`: pure, bounded projection for four KPI cards, department cost segments, attention items, and compact cost summary.
- Create `web/src/finopsExecutiveOverview.test.mjs`: truthfulness, aggregation, ordering, and empty-state unit tests.
- Modify `web/src/FinOpsPortal.jsx`: render the executive overview, interactive SVG donut, three drill-down cards, and slim Cost Analysis header.
- Modify `web/src/finopsDecisionViewModel.js`: normalize the customer-facing `monthly_total_cost` label while preserving source numbers and evidence state.
- Modify `web/src/finopsDecisionViewModel.test.mjs`: prove ROI label and help text separation.
- Modify `web/src/finopsLayout.test.mjs`: protect the new page hierarchy and absence of the old overview panels.
- Modify `web/src/styles.css`: desktop, intermediate, mobile, donut, attention, and navigation styling.
- Modify `web/tests/finopsMockApi.mjs`: add a deterministic multi-department composition option only for browser acceptance.
- Modify `web/tests/finops-operations-management.spec.mjs`: executive layout and cost/ROI separation acceptance.
- Modify `web/tests/finops-portal-acceptance.spec.mjs`: filter-preserving drill-down, donut tooltip, responsive overflow, and cache-first navigation acceptance.

### Task 1: Executive overview projection

**Files:**
- Create: `web/src/finopsExecutiveOverview.js`
- Create: `web/src/finopsExecutiveOverview.test.mjs`

**Interfaces:**
- Consumes: `finopsBootstrapViewData(payload)` output with `overview`, `department`, `anomalies`, and `insights`.
- Produces: `executiveOverviewView(data): { cards, costComposition, attention }` and `executiveCostSummary(overview): { value, meta, status }`.
- `cards` is exactly four `{ id, label, value, meta, tone, metric }` objects compatible with `MetricCards`.
- `costComposition` is `{ total, currency, status, segments }`; each segment is `{ id, label, value, sharePct, offsetPct, colorIndex, evidenceState }`.
- `attention` is at most three `{ id, title, detail, tone, status, reason, evidenceRefs }` objects.

- [ ] **Step 1: Write failing truthfulness and card tests**

```js
import assert from "node:assert/strict";
import test from "node:test";

import {
  executiveCostSummary,
  executiveOverviewView,
} from "./finopsExecutiveOverview.js";

const data = {
  overview: {
    data_status: "partial",
    metrics: {
      requests: 60,
      success_rate_pct: 93.3,
      estimated_cost: { amount: 0.0269, priced_requests: 58, unpriced_requests: 2, status: "partial" },
      cache_hit_rate_pct: 42,
      cache: { eligible_requests: 50, estimated_savings: 0.0118, data_status: "observed" },
    },
    trust: { pricing: { coverage_pct: 96.67, unpriced_requests: 2, state: "partial" } },
  },
  department: { items: [
    { key: "Commerce", estimated_cost: 0.014, data_status: "available" },
    { key: "Finance", estimated_cost: 0.0129, data_status: "partial" },
  ] },
  anomalies: { items: [{ anomaly_id: "slow", title: "响应时延需要关注", severity: "warning", evidence_state: "observed" }] },
  insights: { roi: { status: "insufficient_data", title: "业务结果仍需验证", evidence_state: "unavailable" } },
};

test("executive cards expose four truthful decisions without inferred successful calls", () => {
  const view = executiveOverviewView(data);
  assert.deepEqual(view.cards.map((item) => item.id), ["cost", "quality", "cache_value", "value_assessment"]);
  assert.equal(view.cards[1].value, "93.3%");
  assert.equal(view.cards[1].meta, "共 60 次调用");
  assert.doesNotMatch(JSON.stringify(view.cards[1]), /56/);
  assert.equal(view.cards[3].value, "需验证");
  assert.doesNotMatch(view.cards[3].value, /%/);
});

test("cost summary preserves price coverage and unpriced requests", () => {
  assert.deepEqual(executiveCostSummary(data.overview), {
    value: "$0.0269",
    meta: "计价覆盖 96.7% · 2 次未计价",
    status: "partial",
  });
});
```

- [ ] **Step 2: Run the focused test and verify the module is missing**

Run: `cd web && node --test src/finopsExecutiveOverview.test.mjs`

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `finopsExecutiveOverview.js`.

- [ ] **Step 3: Add failing composition and attention tests**

```js
test("department composition uses real proportions and aggregates beyond top three", () => {
  const view = executiveOverviewView({
    ...data,
    department: { items: [
      { key: "A", estimated_cost: 40, data_status: "available" },
      { key: "B", estimated_cost: 30, data_status: "available" },
      { key: "C", estimated_cost: 20, data_status: "available" },
      { key: "Unassigned", estimated_cost: 7, data_status: "partial" },
      { key: "E", estimated_cost: 3, data_status: "available" },
    ] },
  });
  assert.deepEqual(view.costComposition.segments.map((item) => [item.label, item.value, item.sharePct]), [
    ["A", 40, 40], ["B", 30, 30], ["Unassigned", 7, 7], ["Other", 23, 23],
  ]);
  assert.equal(view.costComposition.segments[2].evidenceState, "partial");
});

test("no comparable positive cost produces no fabricated slices", () => {
  const view = executiveOverviewView({ ...data, department: { items: [
    { key: "Zero", estimated_cost: 0, data_status: "available" },
    { key: "Missing", estimated_cost: null, data_status: "unavailable" },
  ] } });
  assert.deepEqual(view.costComposition.segments, []);
});

test("attention is bounded and combines observed risk, pricing gap, and ROI evidence", () => {
  const view = executiveOverviewView(data);
  assert.equal(view.attention.length, 3);
  assert.deepEqual(view.attention.map((item) => item.id), ["anomaly-slow", "pricing-gap", "roi-evidence"]);
});
```

- [ ] **Step 4: Implement the bounded projection**

```js
import {
  formatFinOpsCost,
  formatFinOpsNumber,
  formatFinOpsPercent,
} from "./finopsViewModel.js";

const number = (value) => typeof value === "number" && Number.isFinite(value) ? value : null;
const text = (value, fallback = "") => String(value || fallback).trim().slice(0, 120);
const evidence = (value) => ["available", "complete", "observed", "estimated", "partial", "unavailable"].includes(value)
  ? value : "unavailable";

function valueAssessment(roi = {}) {
  const state = String(roi.evidence_state || roi.status || "unavailable").toLowerCase();
  if (["verified", "complete"].includes(state)) return { value: "已验证", tone: "positive", status: "verified" };
  if (["estimated", "partial", "ready"].includes(state)) return { value: "需验证", tone: "warning", status: state };
  if (["insufficient_data", "unavailable", "not_recorded"].includes(state)) return { value: "证据不足", tone: "warning", status: state };
  return { value: "待评估", tone: "neutral", status: "unavailable" };
}

export function executiveCostSummary(overview = {}) {
  const cost = overview.metrics?.estimated_cost || {};
  const pricing = overview.trust?.pricing || {};
  const coverage = number(pricing.coverage_pct);
  const unpriced = number(cost.unpriced_requests ?? pricing.unpriced_requests);
  return {
    value: formatFinOpsCost(number(cost.amount), cost.status),
    meta: coverage === null
      ? "计价覆盖待补齐"
      : `计价覆盖 ${formatFinOpsPercent(coverage)}${unpriced ? ` · ${formatFinOpsNumber(unpriced, "0")} 次未计价` : ""}`,
    status: evidence(cost.status),
  };
}

function costComposition(department = {}) {
  const rows = (Array.isArray(department.items) ? department.items : [])
    .map((item) => ({
      label: text(item?.key, "未记录"),
      value: number(item?.estimated_cost),
      evidenceState: evidence(item?.data_status),
    }))
    .filter((item) => item.value !== null && item.value > 0)
    .sort((left, right) => right.value - left.value);
  const unassigned = rows.find((item) => ["unassigned", "未归属"].includes(item.label.toLowerCase()));
  const named = rows.filter((item) => item !== unassigned);
  const visible = unassigned ? [...named.slice(0, 2), unassigned] : rows.slice(0, 3);
  const visibleSet = new Set(visible);
  const remainder = rows.filter((item) => !visibleSet.has(item));
  if (remainder.length) visible.push({
    label: "Other",
    value: remainder.reduce((sum, item) => sum + item.value, 0),
    evidenceState: remainder.some((item) => item.evidenceState !== "available") ? "partial" : "available",
  });
  const total = rows.reduce((sum, item) => sum + item.value, 0);
  let offsetPct = 0;
  return {
    total,
    currency: "USD",
    status: rows.some((item) => item.evidenceState !== "available") ? "partial" : rows.length ? "available" : "unavailable",
    segments: visible.map((item, index) => {
      const sharePct = total ? Number(((item.value / total) * 100).toFixed(1)) : 0;
      const segment = { id: `department-${index}`, ...item, sharePct, offsetPct, colorIndex: index % 6 };
      offsetPct += sharePct;
      return segment;
    }),
  };
}

export function executiveOverviewView(data = {}) {
  const overview = data.overview || {};
  const metrics = overview.metrics || {};
  const cost = executiveCostSummary(overview);
  const value = valueAssessment(data.insights?.roi || {});
  const anomalyItems = (Array.isArray(data.anomalies?.items) ? data.anomalies.items : []).slice(0, 1).map((item, index) => ({
    id: `anomaly-${text(item.anomaly_id, index)}`,
    title: text(item.title, "运营异常待处理"),
    detail: "来自当前筛选范围的已记录异常",
    tone: item.severity === "critical" ? "critical" : "warning",
    status: evidence(item.evidence_state),
    reason: text(item.title, "运营异常"),
    evidenceRefs: Array.isArray(item.evidence_refs) ? item.evidence_refs.slice(0, 10) : [],
  }));
  const pricing = overview.trust?.pricing || {};
  const attention = [...anomalyItems];
  if (number(pricing.unpriced_requests) > 0) attention.push({ id: "pricing-gap", title: "计价覆盖需要补齐", detail: `${formatFinOpsNumber(pricing.unpriced_requests, "0")} 次调用未计价`, tone: "warning", status: evidence(pricing.state), reason: "计价覆盖", evidenceRefs: [] });
  if (value.status !== "verified") attention.push({ id: "roi-evidence", title: text(data.insights?.roi?.title, "业务价值仍需验证"), detail: "价值测算与已验证业务结果保持分离", tone: "neutral", status: value.status, reason: "价值证据", evidenceRefs: [] });
  return {
    cards: [
      { id: "cost", label: "AI 使用成本", value: cost.value, meta: cost.meta, tone: cost.status === "partial" ? "warning" : "neutral", metric: { id: "estimated_cost", label: "AI 使用成本", kind: "cost", amount: number(metrics.estimated_cost?.amount), value: number(metrics.estimated_cost?.amount), unit: "USD", dataStatus: overview.data_status || "unavailable", evidenceState: cost.status } },
      { id: "quality", label: "调用质量", value: formatFinOpsPercent(number(metrics.success_rate_pct)), meta: `共 ${formatFinOpsNumber(number(metrics.requests), "0")} 次调用`, tone: number(metrics.success_rate_pct) !== null && metrics.success_rate_pct < 95 ? "warning" : "neutral", metric: { id: "success_rate", label: "调用质量", kind: "quality", value: number(metrics.success_rate_pct), unit: "%", requests: number(metrics.requests), successRatePct: number(metrics.success_rate_pct), dataStatus: overview.data_status || "unavailable", evidenceState: overview.data_status === "complete" ? "observed" : overview.data_status || "unavailable" } },
      { id: "cache_value", label: "缓存收益", value: formatFinOpsCost(number(metrics.cache?.estimated_savings), metrics.cache?.data_status), meta: `命中率 ${formatFinOpsPercent(number(metrics.cache_hit_rate_pct))}`, tone: number(metrics.cache_hit_rate_pct) !== null && metrics.cache_hit_rate_pct < 60 ? "warning" : "positive", metric: { id: "cache_savings", label: "缓存收益", kind: "cache", value: number(metrics.cache?.estimated_savings), unit: "USD", cache: metrics.cache || {}, dataStatus: metrics.cache?.data_status || "unavailable", evidenceState: metrics.cache?.data_status || "unavailable" } },
      { id: "value_assessment", label: "价值判断", value: value.value, meta: text(data.insights?.roi?.summary, "等待业务结果验证"), tone: value.tone, metric: { id: "roi_assessment", label: "价值判断", kind: "overview", value: null, unit: "", dataStatus: value.status, evidenceState: value.status } },
    ],
    costComposition: costComposition(data.department),
    attention: attention.slice(0, 3),
  };
}
```

- [ ] **Step 5: Run focused tests and commit**

Run: `cd web && node --test src/finopsExecutiveOverview.test.mjs`

Expected: all tests PASS.

```powershell
git add web/src/finopsExecutiveOverview.js web/src/finopsExecutiveOverview.test.mjs
git commit -m "feat(finops): project executive overview decisions"
```

### Task 2: Executive dashboard rendering and interaction

**Files:**
- Modify: `web/src/FinOpsPortal.jsx:228-278, 658-739, 1912-1930`
- Modify: `web/src/finopsLayout.test.mjs:25-38`
- Modify: `web/src/styles.css:5239-5900` and existing FinOps responsive blocks

**Interfaces:**
- Consumes: `executiveOverviewView(data)` from Task 1 and existing `activateTab(targetTab)`.
- Produces: `MetricCards({ cards, payload, ... })`, `ExecutiveCostDonut({ composition, onOpenCost })`, `ExecutiveAttention({ items, onEvidence })`, and `OverviewDrilldowns({ onNavigate })`.

- [ ] **Step 1: Replace the old static layout assertion with a failing hierarchy assertion**

```js
test("overview renders one executive decision hierarchy", async () => {
  const component = await readFile(new URL("./FinOpsPortal.jsx", import.meta.url), "utf8");
  assert.match(component, /executiveOverviewView\(data\)/);
  assert.match(component, /aria-label="运营决策概览"/);
  assert.match(component, /title="部门成本构成"/);
  assert.match(component, /查看成本分析/);
  assert.match(component, /成本分析.*成本来自哪里/s);
  assert.match(component, /效能与 ROI.*投入是否产生价值/s);
  assert.match(component, /风险与优化.*现在应优先处理什么/s);
  assert.doesNotMatch(component, /title="数据可信度"/);
  assert.doesNotMatch(component, /title="部门成本与运行质量"/);
});
```

- [ ] **Step 2: Run the layout test and verify the old hierarchy fails**

Run: `cd web && node --test src/finopsLayout.test.mjs`

Expected: FAIL because the executive projection, donut title, and drill-down copy are absent.

- [ ] **Step 3: Generalize KPI rendering and build the overview components**

```jsx
import { executiveOverviewView } from "./finopsExecutiveOverview.js";

function MetricCards({ cards = null, payload, scope, onEvidence = null, onAsk = null, onConfigurePricing = null }) {
  const visibleCards = cards || finopsMetricCards(payload);
  return (
    <section className="finops-metrics" aria-label="运营核心指标">
      {visibleCards.map((card) => {
        // Keep the existing MetricHelp, evidence, pricing, and Ask AI body unchanged.
      })}
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
        cx="21" cy="21" r="15.9155" fill="none" strokeWidth="6"
        className={`segment segment-${segment.colorIndex}`}
        strokeDasharray={`${segment.sharePct} ${100 - segment.sharePct}`}
        strokeDashoffset={-segment.offsetPct + 25}
        tabIndex="0" role="button"
        aria-label={`${segment.label} ${segment.sharePct}%`}
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
  if (!composition.segments.length) return <EmptyState>当前范围没有可比较的部门成本。</EmptyState>;
  return (
    <div className="finops-executive-donut-layout">
      <div className="finops-executive-donut-wrap">
        <svg className="finops-executive-donut" viewBox="0 0 42 42" role="img" aria-label="部门估算成本占比">
          <circle className="track" cx="21" cy="21" r="15.9155" fill="none" strokeWidth="6" />
          {composition.segments.map((segment) => <ExecutiveDonutSlice key={segment.id} segment={segment} onOpenCost={onOpenCost} />)}
        </svg>
        <span><b>{formatFinOpsCost(composition.total, composition.status)}</b><small>部门估算成本</small></span>
      </div>
      <div className="finops-executive-donut-legend">
        {composition.segments.map((segment) => <button key={segment.id} type="button" onClick={onOpenCost}><i className={`segment-${segment.colorIndex}`} /><span>{segment.label}</span><b>{segment.sharePct}%</b></button>)}
      </div>
      <button type="button" className="finops-panel-link" onClick={onOpenCost}>查看成本分析</button>
    </div>
  );
}
```

- [ ] **Step 4: Replace `OverviewPage` with the approved one-screen hierarchy**

```jsx
function OverviewPage({ data, scope, comparison, onEvidence = null, onAsk = null, onConfigurePricing = null, onNavigateTab }) {
  const [trendMetric, setTrendMetric] = useState("cost");
  const view = executiveOverviewView(data);
  return (
    <section className="finops-executive-overview" aria-label="运营决策概览">
      <MetricCards cards={view.cards} payload={data.overview} scope={scope} onEvidence={onEvidence} onAsk={onAsk} onConfigurePricing={onConfigurePricing} />
      <div className="finops-executive-decision-grid">
        <Panel title="成本与调用趋势" subtitle="按真实数值比例呈现" className="finops-executive-trend">
          {/* retain existing Cost / Calls / Token / P95 switch and TrendBars */}
        </Panel>
        <Panel title="部门成本构成" subtitle="当前筛选范围的估算成本">
          <ExecutiveCostDonut composition={view.costComposition} onOpenCost={() => onNavigateTab("cost")} />
        </Panel>
        <Panel title="需要关注" subtitle="当前最值得处理的三项">
          <ExecutiveAttention items={view.attention} onEvidence={onEvidence} />
        </Panel>
      </div>
      <OverviewDrilldowns onNavigate={onNavigateTab} />
    </section>
  );
}
```

Pass `onNavigateTab={activateTab}` at the existing `OverviewPage` call site. The existing `activateTab` reads any cached destination before switching, so filters and cache-first behavior remain intact.

- [ ] **Step 5: Add responsive styles with explicit ratios and stacking**

```css
.finops-executive-decision-grid {
  display: grid;
  grid-template-columns: minmax(0, 13fr) minmax(240px, 6fr) minmax(260px, 6fr);
  gap: 16px;
  align-items: stretch;
}
.finops-executive-decision-grid > * { min-width: 0; }
.finops-executive-drilldowns { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.finops-executive-donut { width: 176px; height: 176px; transform: rotate(-90deg); overflow: visible; }
.finops-executive-donut .segment { cursor: pointer; transition: opacity 160ms ease, stroke-width 160ms ease; }
.finops-executive-donut .segment:hover,
.finops-executive-donut .segment:focus-visible { opacity: .82; stroke-width: 7; outline: none; }
@media (max-width: 1100px) {
  .finops-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .finops-executive-decision-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .finops-executive-trend { grid-column: 1 / -1; }
}
@media (max-width: 720px) {
  .finops-metrics,
  .finops-executive-decision-grid,
  .finops-executive-drilldowns { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .finops-executive-donut .segment { transition: none; }
}
```

Update the first/last-child metric border-radius rules so four cards render correctly at desktop, two-by-two intermediate, and single-column mobile widths.

- [ ] **Step 6: Run unit/layout tests and commit**

Run: `cd web && node --test src/finopsExecutiveOverview.test.mjs src/finopsLayout.test.mjs`

Expected: all focused tests PASS.

```powershell
git add web/src/FinOpsPortal.jsx web/src/finopsLayout.test.mjs web/src/styles.css
git commit -m "feat(web): build executive operations overview"
```

### Task 3: Separate Cost Analysis and ROI responsibilities

**Files:**
- Modify: `web/src/FinOpsPortal.jsx:766-812`
- Modify: `web/src/finopsDecisionViewModel.js:240-257`
- Modify: `web/src/finopsDecisionViewModel.test.mjs:104-137`
- Modify: `web/src/finopsLayout.test.mjs`

**Interfaces:**
- Consumes: `executiveCostSummary(overviewData.overview)`.
- Produces: a compact Cost Analysis summary and normalized ROI metric labels.

- [ ] **Step 1: Add failing cost deduplication and ROI semantic tests**

```js
test("monthly total cost is presented as AI operating investment", () => {
  const view = roiDecisionView({
    metrics: [{ id: "monthly_total_cost", label: "月度总成本", value: 800, unit: "USD", status: "estimated", explanation: "场景成本" }],
  });
  assert.equal(view.metrics[0].label, "AI 运营总投入");
  assert.match(view.metrics[0].explanation, /实施摊销|固定运营成本|模型成本/);
});
```

Add this layout assertion:

```js
assert.match(component, /className="finops-cost-summary"/);
assert.doesNotMatch(component, /function CostPage[\s\S]*?<MetricCards payload=\{overviewData\.overview\}/);
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd web && node --test src/finopsDecisionViewModel.test.mjs src/finopsLayout.test.mjs`

Expected: FAIL because ROI keeps the server label and Cost Analysis still renders `MetricCards`.

- [ ] **Step 3: Normalize only the customer-facing ROI label and explanation**

```js
const ROI_METRIC_COPY = {
  monthly_total_cost: {
    label: "AI 运营总投入",
    explanation: "可包含实施摊销、固定运营成本与当前模型成本；不等同于请求级模型使用成本。",
  },
};

function safeMetric(raw) {
  // preserve the existing validation and numeric/status handling
  const copy = ROI_METRIC_COPY[id];
  return {
    id,
    label: copy?.label || boundedText(raw.label, 80) || "运营指标",
    value,
    unit,
    unitLabel: UNIT_LABELS[unit] || "",
    status: evidence.key,
    badge: evidence.label,
    valueLabel: formatValue(value, unit, evidence.key),
    explanation: copy?.explanation || boundedText(raw.explanation, 240),
  };
}
```

Because `safeBridge()` calls `safeMetric()` for explicit items and otherwise filters the normalized metrics, the same label is used consistently in both the KPI row and value bridge.

- [ ] **Step 4: Replace the repeated Cost Analysis KPI grid with one compact summary**

```jsx
const summary = executiveCostSummary(overviewData.overview);
return (
  <>
    <section className="finops-cost-summary" aria-label="成本分析口径">
      <div><small>当前估算成本</small><b>{summary.value}</b><span>{summary.meta}</span></div>
      <p>以下按部门、工作区、Agent 与模型解释成本来源；估算不代表云平台实际账单。</p>
      {onConfigurePricing ? <button type="button" onClick={onConfigurePricing}><Pencil size={14} />维护计价映射</button> : null}
    </section>
    {/* preserve saved view, CSV, trend, attribution, and detailed donuts */}
  </>
);
```

- [ ] **Step 5: Run tests and commit**

Run: `cd web && node --test src/finopsDecisionViewModel.test.mjs src/finopsLayout.test.mjs`

Expected: all focused tests PASS.

```powershell
git add web/src/FinOpsPortal.jsx web/src/finopsDecisionViewModel.js web/src/finopsDecisionViewModel.test.mjs web/src/finopsLayout.test.mjs web/src/styles.css
git commit -m "fix(finops): separate cost and ROI page responsibilities"
```

### Task 4: Real-browser executive dashboard acceptance

**Files:**
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`
- Modify: `web/tests/finops-portal-acceptance.spec.mjs`

**Interfaces:**
- Consumes: existing `installFinOpsMockApi(page, options)` and portal test helpers.
- Produces: deterministic browser evidence for proportions, filters, tooltips, responsive layout, and deduplication.

- [ ] **Step 1: Add failing Playwright acceptance**

```js
test("executive overview is bounded and cost drilldown preserves filters", async ({ page }) => {
  await installFinOpsMockApi(page, { executiveComposition: true });
  await openOperationsManagement(page);
  const overview = page.getByRole("region", { name: "运营决策概览" });
  await expect(overview.locator(".finops-metric")).toHaveCount(4);
  await expect(overview.getByText("AI 使用成本")).toBeVisible();
  await expect(overview.getByText("调用质量")).toBeVisible();
  await expect(overview.getByText("缓存收益")).toBeVisible();
  await expect(overview.getByText("价值判断")).toBeVisible();
  await expect(overview.locator(".finops-executive-attention-item")).toHaveCount(3);

  const segments = overview.locator(".finops-executive-donut .segment");
  await expect(segments).toHaveCount(4);
  const dashArrays = await segments.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("stroke-dasharray")));
  expect(new Set(dashArrays).size).toBeGreaterThan(1);

  await page.getByLabel("部门筛选").selectOption("Commerce");
  await overview.getByRole("button", { name: "查看成本分析" }).click();
  await expect(page.getByRole("button", { name: /成本分析/ })).toHaveClass(/active/);
  await expect(page.getByLabel("部门筛选")).toHaveValue("Commerce");
  await expect(page.locator(".finops-cost-summary")).toBeVisible();
  await expect(page.locator(".finops-metrics")).toHaveCount(0);
});
```

Add a tooltip boundary check using the last donut legend/segment: focus it, read `.finops-viewport-tooltip` bounding box, and assert `left >= 0`, `right <= viewport.width`, `top >= 0`, `bottom <= viewport.height`.

- [ ] **Step 2: Run targeted Playwright and verify failure**

Run: `cd web && npx playwright test tests/finops-operations-management.spec.mjs tests/finops-portal-acceptance.spec.mjs --project=chromium`

Expected: FAIL because the old eight-card layout and conic-gradient donut are still present.

- [ ] **Step 3: Extend the mock only through existing fixture options**

```js
function executiveCompositionDepartments() {
  return { items: [
    { key: "Commerce", estimated_cost: 0.014, data_status: "available" },
    { key: "Finance", estimated_cost: 0.008, data_status: "available" },
    { key: "IT", estimated_cost: 0.003, data_status: "available" },
    { key: "Unassigned", estimated_cost: 0.0012, data_status: "partial" },
    { key: "Support", estimated_cost: 0.0007, data_status: "available" },
  ] };
}
```

When `options.executiveComposition === true`, replace only the mock bootstrap's `departments` field. Do not change production fallback behavior or label this data inside the UI.

- [ ] **Step 4: Add responsive and screenshot assertions**

At 1440 x 900 and 1366 x 768 assert:

```js
await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
await expect(page.locator(".finops-executive-decision-grid")).toBeVisible();
await page.screenshot({ path: "output/playwright/operations-executive-overview-desktop.png", fullPage: true });
```

At 820 x 1180 assert trend spans the grid and donut/attention do not overlap. At 390 x 844 assert `document.documentElement.scrollWidth <= window.innerWidth + 1`, each executive panel is narrower than the viewport, and capture `operations-executive-overview-mobile.png`.

- [ ] **Step 5: Run targeted browser acceptance and commit**

Run: `cd web && npx playwright test tests/finops-operations-management.spec.mjs tests/finops-portal-acceptance.spec.mjs --project=chromium`

Expected: all targeted tests PASS and both screenshots exist.

```powershell
git add web/tests/finopsMockApi.mjs web/tests/finops-operations-management.spec.mjs web/tests/finops-portal-acceptance.spec.mjs
git commit -m "test(web): verify executive FinOps dashboard"
```

### Task 5: Full regression and visual gate

**Files:**
- Modify only if a verified failure is caused by Tasks 1-4.
- Do not commit `web/test-results/`, `test-results/`, Playwright traces, or `.superpowers/brainstorm/`.

**Interfaces:**
- Consumes: completed Tasks 1-4.
- Produces: reproducible release-candidate evidence; no deployment or traffic change.

- [ ] **Step 1: Run the full Node suite**

Run: `cd web && node --test`

Expected: all Node tests PASS with zero failures.

- [ ] **Step 2: Run the production build**

Run: `cd web && npm run build`

Expected: Vite exits 0 and emits the production bundle.

- [ ] **Step 3: Run the full Playwright suite**

Run: `cd web && npx playwright test`

Expected: all Playwright tests PASS with zero failures.

- [ ] **Step 4: Inspect the four required views in a real browser**

Open the local Vite app with the existing authenticated/mock acceptance setup and inspect:

- Overview at 1440 x 900, 1366 x 768, 820 x 1180, and 390 x 844.
- Cost Analysis after department, Agent, and model filtering.
- Efficiency and ROI with `AI 运营总投入` visible and distinct from `AI 使用成本`.
- Risk and Optimization with evidence drawer and floating Operations AI.

Reject the candidate if any chart tooltip is clipped, any card overlaps another, a panel shows an invented equal share, a filter resets on drill-down, or an old eight-card block remains on Cost Analysis.

- [ ] **Step 5: Check repository hygiene**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only intentionally changed tracked files plus the pre-existing untracked evidence/workspace directories.

- [ ] **Step 6: Record final evidence without deploying**

Update the PR description or handoff with exact Node, Vite, Playwright, screenshot, and commit evidence. Production deployment remains a separate explicit approval gate.

---

## Self-review result

- Spec coverage: all four page responsibilities, the four truthful KPIs, real department proportions, bounded attention, filter-preserving drill-down, ROI naming, responsive behavior, tooltip containment, cache-first navigation, and no duplicate AI surface are assigned to Tasks 1-5.
- Placeholder scan: no deferred implementation marker or undefined task dependency remains. The donut tooltip step includes the actual active segment values and evidence state.
- Type consistency: Task 1 produces `executiveOverviewView(data)` and `executiveCostSummary(overview)`; Tasks 2 and 3 consume those exact signatures. The `cards` shape remains compatible with the existing `MetricCards` body and the destination navigation uses the existing `activateTab(targetTab)` callback.
