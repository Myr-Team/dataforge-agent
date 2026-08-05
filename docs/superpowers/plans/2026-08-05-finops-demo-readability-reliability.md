# FinOps Demo Readability and Load Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reliable Dashboard bootstrap plus demonstrably readable Operations AI, ROI, and risk-governance interfaces without changing DataForge authorization or governance execution boundaries.

**Architecture:** Add a bounded, per-workspace, in-process read cache around the existing Dashboard aggregate and expose explicit loading/error/retry states to the shell. Keep FinOps API contracts stable by normalizing assistant context at the frontend boundary, then refactor only the ROI and selected-risk presentation components while preserving their service-derived values and actions.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, React 18, Vite, Node test runner, Playwright, CSS.

## Global Constraints

- Preserve MAF behavior, Easy Auth/Entra authorization, model routing, price cards, and `DF_FINOPS_ACTIONS_ENABLED`.
- Do not add static fallback metrics or present estimated ROI as verified.
- Dashboard cache defaults to 2 seconds, keys by workspace, never caches failures, and never serializes unrelated workspace builds behind one global build lock.
- Desktop acceptance width is 1440px and mobile acceptance width is 390px.
- Do not deploy a revision or switch production traffic without explicit user approval after candidate acceptance.

---

### Task 1: Reliable per-workspace Dashboard cache

**Files:**
- Modify: `backend/control_plane.py:90-110,748-821`
- Create: `tests/test_control_plane_dashboard_cache.py`

**Interfaces:**
- Consumes: existing `_build_workspace_dashboard_uncached(workspace_id: str) -> dict[str, Any]` aggregate.
- Produces: `build_workspace_dashboard(workspace_id: str) -> dict[str, Any]` with bounded TTL caching and no response-shape change.

- [ ] **Step 1: Write failing cache boundary tests**

```python
def test_slow_build_gets_full_ttl_after_completion(monkeypatch):
    clock = iter([10.0, 10.0, 13.0, 13.5])
    monkeypatch.setattr(control_plane.time, "monotonic", lambda: next(clock))
    calls = []
    monkeypatch.setattr(control_plane, "_build_workspace_dashboard_uncached", lambda workspace_id: calls.append(workspace_id) or _payload(workspace_id))
    control_plane.build_workspace_dashboard("ws-a")
    control_plane.build_workspace_dashboard("ws-a")
    assert calls == ["ws-a"]

def test_different_workspaces_build_concurrently(monkeypatch):
    barrier = threading.Barrier(2)
    monkeypatch.setattr(control_plane, "_build_workspace_dashboard_uncached", lambda workspace_id: barrier.wait(timeout=1) or _payload(workspace_id))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert {item["workspace_id"] for item in pool.map(control_plane.build_workspace_dashboard, ["ws-a", "ws-b"])} == {"ws-a", "ws-b"}

def test_nested_mutation_does_not_change_cached_payload(monkeypatch):
    monkeypatch.setattr(control_plane, "_build_workspace_dashboard_uncached", lambda workspace_id: {"workspace_id": workspace_id, "runs": []})
    first = control_plane.build_workspace_dashboard("ws-a")
    first["runs"].append({"run_id": "caller-change"})
    assert control_plane.build_workspace_dashboard("ws-a")["runs"] == []
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `python -m pytest -q tests/test_control_plane_dashboard_cache.py`

Expected: failures proving the current branch has no cache, no per-key single-flight, and no mutation isolation.

- [ ] **Step 3: Implement bounded cache and per-workspace single-flight**

```python
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy

DASHBOARD_CACHE_SECONDS = max(0.0, float(os.environ.get("DF_DASHBOARD_CACHE_SECONDS", "2")))
DASHBOARD_CACHE_MAX_ENTRIES = max(1, int(os.environ.get("DF_DASHBOARD_CACHE_MAX_ENTRIES", "128")))
_DASHBOARD_CACHE_GUARD = threading.Lock()
_DASHBOARD_CACHE = OrderedDict()
_DASHBOARD_BUILD_STATES = {}

def build_workspace_dashboard(workspace_id: str) -> dict[str, Any]:
    cached = _dashboard_cache_get(workspace_id)
    if cached is not None:
        return cached
    with _dashboard_build_lock(workspace_id):
        cached = _dashboard_cache_get(workspace_id)
        if cached is not None:
            return cached
        payload = _build_workspace_dashboard_uncached(workspace_id)
        _dashboard_cache_put(workspace_id, payload, time.monotonic())
        return deepcopy(payload)
```

The helper functions must prune expired entries under `_DASHBOARD_CACHE_GUARD`, evict the oldest entry above `DASHBOARD_CACHE_MAX_ENTRIES`, return `deepcopy(payload)`, and remove unused per-workspace build states after waiters leave.

- [ ] **Step 4: Verify cache tests GREEN**

Run: `python -m pytest -q tests/test_control_plane_dashboard_cache.py tests/test_control_plane.py`

Expected: all selected tests pass with no warning or deadlock.

- [ ] **Step 5: Commit the cache checkpoint**

```powershell
git add backend/control_plane.py tests/test_control_plane_dashboard_cache.py
git commit -m "perf(control-plane): bound dashboard aggregate cache"
```

### Task 2: Explicit Dashboard loading, error, and retry states

**Files:**
- Modify: `web/src/App.jsx:285-345,1254-1290`
- Modify: `web/src/components.jsx:590-730`
- Modify: `web/src/styles.css`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: `dashboard`, `dashboardLoading`, `dashboardError`, and `refreshDashboard(workspaceId)` from `App`.
- Produces: `WorkbenchMain` props `dashboardLoading`, `dashboardError`, and `onRetryDashboard` plus `DashboardLoadingSkeleton` and `DashboardLoadError` views.

- [ ] **Step 1: Add failing Playwright scenarios**

```javascript
test("dashboard pending renders a skeleton then real workspace", async ({ page }) => {
  await installFinOpsMockApi(page, [], { dashboardDelayMs: 1200 });
  await page.goto("/");
  await expect(page.locator(".dashboard-stage-loading")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Commerce" })).toBeVisible();
});

test("dashboard failure becomes a retryable content state", async ({ page }) => {
  const control = await installFinOpsMockApi(page, [], { dashboardFailures: 1 });
  await page.goto("/");
  const state = page.getByRole("alert", { name: "工作区加载失败" });
  await expect(state).toBeVisible();
  await state.getByRole("button", { name: "重新加载" }).click();
  await expect(page.getByRole("heading", { name: "Commerce" })).toBeVisible();
  expect(control.calls.dashboard).toBe(2);
});
```

- [ ] **Step 2: Run the new browser tests and verify RED**

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5217'; npx playwright test tests/finops-operations-management.spec.mjs -g "dashboard"`

Expected: skeleton and retry assertions fail against the current implementation.

- [ ] **Step 3: Implement state-specific rendering**

```jsx
if (resolvedView === "workspaces" && dashboardLoading && !dashboard) {
  return <DashboardLoadingSkeleton />;
}
if (resolvedView === "workspaces" && dashboardError && !dashboard) {
  return <DashboardLoadError message={dashboardError} onRetry={onRetryDashboard} />;
}
```

Pass the loading, error, and retry props from `App`; keep `finopsPreloadScope?.workspaceId` as the first Operations Portal workspace source so FinOps can open after capability resolution without waiting for Dashboard.

- [ ] **Step 4: Verify the focused Playwright tests GREEN**

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5217'; npx playwright test tests/finops-operations-management.spec.mjs -g "dashboard"`

Expected: both delayed-success and retry-after-failure scenarios pass.

- [ ] **Step 5: Commit the shell-state checkpoint**

```powershell
git add web/src/App.jsx web/src/components.jsx web/src/styles.css web/tests/finopsMockApi.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "fix(web): make dashboard bootstrap states explicit"
```

### Task 3: Operations AI contract normalization and readable failures

**Files:**
- Modify: `web/src/finopsInteraction.js:1-140`
- Modify: `web/src/FinOpsAssistant.jsx:32-170,256-305`
- Modify: `web/src/styles.css:5667-5705`
- Modify: `web/src/finopsInteraction.test.mjs`
- Modify: `web/src/finopsApi.test.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: arbitrary view-model metric statuses.
- Produces: `metricContext()` that always satisfies `AssistantMetricContext`, and `assistantFailureMessage(error)` that never exposes validation JSON.

- [ ] **Step 1: Add failing contract and error-redaction tests**

```javascript
test("estimated evidence maps to partial assistant data status", () => {
  const context = metricContext({ id: "roi", label: "价值判断", dataStatus: "estimated", evidenceState: "estimated" }, scope);
  assert.equal(context.data_status, "partial");
  assert.equal(context.evidence_state, "estimated");
});

test("assistant validation details become a retryable public message", () => {
  const raw = new Error('[{"type":"string_pattern_mismatch","loc":["body","metric_context","data_status"]}]');
  assert.equal(assistantFailureMessage(raw), "当前分析未完成，请重试。");
});
```

- [ ] **Step 2: Run focused Node tests and verify RED**

Run: `cd web; node --test src/finopsInteraction.test.mjs src/finopsApi.test.mjs`

Expected: `estimated` is still sent as `data_status`, and raw validation JSON is still returned.

- [ ] **Step 3: Implement safe status mapping and transient retry UI**

```javascript
const DATA_STATUSES = new Set(["complete", "partial", "unavailable", "insufficient_data"]);
const EVIDENCE_STATES = new Set(["observed", "estimated", "partial", "unavailable"]);

function assistantDataStatus(value) {
  if (DATA_STATUSES.has(value)) return value;
  return value === "estimated" || value === "observed" || value === "verified" ? "partial" : "unavailable";
}
```

Add `assistantFailureMessage()` to recognize HTTP 4xx validation payloads, JSON-shaped strings, timeout, and connectivity errors. Failed assistant turns store only the public message in component state, carry `retryQuestion`, and are not written through `writeFinOpsAssistantHistory`. Render a “重试” button that calls `ask(retryQuestion)`. Structured successful replies continue to render conclusion, basis, impact, recommendation, and caveat.

- [ ] **Step 4: Verify Node and AI Playwright scenarios GREEN**

Run: `cd web; node --test src/finopsInteraction.test.mjs src/finopsApi.test.mjs`

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5218'; npx playwright test tests/finops-operations-management.spec.mjs -g "运营 AI|validation"`

Expected: no validation JSON is visible; an estimated ROI context produces a successful structured or friendly retryable response.

- [ ] **Step 5: Commit the assistant checkpoint**

```powershell
git add web/src/finopsInteraction.js web/src/FinOpsAssistant.jsx web/src/styles.css web/src/finopsInteraction.test.mjs web/src/finopsApi.test.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "fix(finops): normalize operations assistant context"
```

### Task 4: Replace the ambiguous ROI half-axis with a formula flow

**Files:**
- Modify: `web/src/finops/DecisionCharts.jsx:80-145`
- Modify: `web/src/finops/RoiDecisionPage.jsx:190-230`
- Modify: `web/src/styles.css:5820-5845,5960-5985,6590-6600`
- Modify: `web/src/finopsLayout.test.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: existing `view.bridge.items`, `formulaRevision`, and `paybackLabel` without changing API fields.
- Produces: `ValueBridge` formula operands for monthly revenue, AI investment, and monthly net benefit, plus separate ROI/payback result chips.

- [ ] **Step 1: Add failing markup and visual-contract tests**

```javascript
test("ROI value bridge renders a readable financial formula", () => {
  const markup = renderToStaticMarkup(<ValueBridge items={bridgeItems} paybackLabel="2.2 月" />);
  assert.match(markup, /月度收益/);
  assert.match(markup, /AI 运营总投入/);
  assert.match(markup, /月度净收益/);
  assert.match(markup, /ROI 比率/);
  assert.doesNotMatch(markup, /finops-decision-zero-axis/);
});
```

Playwright must assert the desktop formula fits in one row, the 390px layout is vertical, and no `.finops-decision-value-track` remains.

- [ ] **Step 2: Run ROI tests and verify RED**

Run: `cd web; node --test src/finopsLayout.test.mjs`

Expected: current `ValueBridge` still renders the zero axis and percentage bars.

- [ ] **Step 3: Implement the formula flow**

```jsx
<div className="finops-roi-formula-flow">
  <FormulaOperand item={revenue} role="positive" />
  <span aria-hidden="true">−</span>
  <FormulaOperand item={investment} role="negative" />
  <span aria-hidden="true">=</span>
  <FormulaOperand item={netBenefit} role="result" />
</div>
<div className="finops-roi-result-strip">
  <ResultMetric item={roiRatio} />
  <ResultMetric label="预计回收期" value={paybackLabel} />
</div>
```

Reuse each item’s `valueLabel`, `status`, `badge`, `unitLabel`, and explanation tooltip. Never calculate a replacement amount in the browser; only select and present service-returned items.

- [ ] **Step 4: Verify ROI unit and Playwright tests GREEN**

Run: `cd web; node --test src/finopsLayout.test.mjs`

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5219'; npx playwright test tests/finops-operations-management.spec.mjs -g "ROI|价值桥"`

Expected: exact service values render, the half-axis is absent, and desktop/mobile bounding boxes remain inside the viewport.

- [ ] **Step 5: Commit the ROI checkpoint**

```powershell
git add web/src/finops/DecisionCharts.jsx web/src/finops/RoiDecisionPage.jsx web/src/styles.css web/src/finopsLayout.test.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "feat(finops): clarify ROI value formula"
```

### Task 5: Recompose selected-risk governance evidence

**Files:**
- Modify: `web/src/finops/RiskDecisionPage.jsx:253-305`
- Modify: `web/src/styles.css:6235-6285,6380-6415`
- Modify: `web/src/finopsLayout.test.mjs`
- Modify: `web/tests/finops-portal-acceptance.spec.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Consumes: current `priority`, request evidence refs, anomaly actions, and remediation draft callback.
- Produces: selected-risk summary, full recommendation, compact semantic progress, and evidence cards with unchanged callbacks and permission checks.

- [ ] **Step 1: Add failing layout and content tests**

```javascript
test("selected risk keeps its full recommendation and evidence controls", () => {
  const markup = renderToStaticMarkup(<RiskDecisionPage payload={riskPayload} />);
  assert.match(markup, /判断摘要/);
  assert.match(markup, /完整建议/);
  assert.match(markup, /代表证据/);
  assert.match(markup, /定位 app_observed、unmanaged 或 unknown/);
});
```

Playwright must assert the recommendation element has `scrollWidth <= clientWidth` at 1440px and 390px, and “问 AI”, “查看整改方案”, “查看证据”, “确认异常”, and “抑制异常” remain operable when authorized.

- [ ] **Step 2: Run risk tests and verify RED**

Run: `cd web; node --test src/finopsLayout.test.mjs`

Expected: current five-column stages truncate the recommendation and do not expose the new three-layer labels.

- [ ] **Step 3: Implement the three-layer selected-risk composition**

```jsx
<div className="finops-risk-selection-summary">...</div>
<ol className="finops-risk-progress" aria-label="风险治理步骤">...</ol>
<section className="finops-risk-recommendation">
  <span>完整建议</span>
  <p>{priority.summary || "服务端尚未返回建议说明"}</p>
</section>
<section className="finops-risk-evidence-section">...</section>
```

Keep all long recommendation text wrapping naturally. Show technical signal fields only inside each evidence card’s existing disclosure. Preserve `requestRefsOf(priority)` filtering so evidence never drifts to unrelated requests.

- [ ] **Step 4: Verify focused Node and Playwright tests GREEN**

Run: `cd web; node --test src/finopsLayout.test.mjs`

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5220'; npx playwright test tests/finops-portal-acceptance.spec.mjs tests/finops-operations-management.spec.mjs -g "risk|风险|evidence"`

Expected: recommendation text is complete, evidence remains bound to the selected item, and all controls pass desktop/mobile viewport assertions.

- [ ] **Step 5: Commit the risk checkpoint**

```powershell
git add web/src/finops/RiskDecisionPage.jsx web/src/styles.css web/src/finopsLayout.test.mjs web/tests/finops-portal-acceptance.spec.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "feat(finops): clarify selected risk governance evidence"
```

### Task 6: Full regression and candidate evidence

**Files:**
- Modify only if a test exposes a defect in files already listed above.
- Produce local screenshots under `output/playwright/`; do not commit them.

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: reproducible test totals, desktop/mobile screenshots, clean diff, and a PR-ready branch without deployment.

- [ ] **Step 1: Run full backend regression**

Run: `python -m pytest -q`

Expected: exit 0 with all tests passing and only the repository’s existing intentional skip.

- [ ] **Step 2: Run full Node regression and Vite build**

Run: `cd web; node --test`

Run: `cd web; npm run build`

Expected: both exit 0; existing bundle-size warning may remain but no compilation error is accepted.

- [ ] **Step 3: Run full Playwright on a unique fresh port**

Run: `cd web; $env:DF_PLAYWRIGHT_PORT='5221'; npx playwright test`

Expected: exit 0 with no reuse of a stale preview server.

- [ ] **Step 4: Inspect screenshots and repository hygiene**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors; only intended tracked changes/commits and existing ignored or untracked local test workspaces. Inspect 1440px and 390px screenshots for text overlap, viewport overflow, tooltip clipping, and AI launcher obstruction.

- [ ] **Step 5: Prepare PR update without deployment**

```powershell
git log --oneline 941932d..HEAD
git diff --stat 941932d..HEAD
```

Expected: a reviewable sequence containing the design, implementation plan, and Tasks 1-5 checkpoints. Push and PR update only after verification; do not deploy or change production traffic in this task.
