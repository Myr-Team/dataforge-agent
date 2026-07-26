# DataForge Operations Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 FinOps Portal 收敛为稳定、可解释、可下钻的“运营管理”，并补齐预算、分摊、ROI、指标感知 AI 与治理机会队列。

**Architecture:** 保留 `backend/finops/` 的请求账本、查询、异常和治理边界；新增的预算、保存视图、ROI 价值事件与 AI 问答通过 typed service 和 additive SQL 表实现。前端将导航可靠性、页面状态和指标交互拆成纯函数 view model，再由 `FinOpsPortal` 组合，所有高级能力均使用现有授权范围。

**Tech Stack:** FastAPI、Pydantic、Azure SQL/内存 repository、React、原生 Node test runner、Playwright、Vite。

## Global Constraints

- 不改变 MAF、分析流程和业务内核。
- 估算成本、预算预测和情景测算不得描述为 Azure 实际账单。
- 不生成示例数据；缺失值显示“未记录”“证据不足”或“暂不可用”。
- Agent 只能解释和生成 typed 草案，不能批准或执行。
- 所有新写接口从可信身份推导 tenant，并在服务端收窄 workspace。
- 每项行为改动必须先出现对应失败测试，再写生产代码。

---

### Task 1: 应用外壳、鉴权恢复与运行入口

**Files:**
- Modify: `web/src/constants.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/App.jsx`
- Modify: `web/src/api.js`
- Modify: `web/src/executionIdentity.js`
- Test: `web/src/constants.test.mjs`
- Test: `web/src/navigationContract.test.mjs`
- Test: `web/src/executionIdentity.test.mjs`
- Test: `web/src/finopsApi.test.mjs`
- Test: `web/src/shellNavigation.test.mjs`

**Interfaces:**
- Produces: `navigationAccessState(view, capabilities) -> "loading"|"allowed"|"denied"`.
- Produces: `executionMessageVisibility(options) -> { appendUser, appendAssistant }`.
- Produces: `toUserFacingRequestError(error, authProbe) -> Promise<Error>`.

- [x] **Step 1: Write failing shell and navigation tests**

```js
assert.deepEqual(
  visibleNavItems(null).map((item) => item.id),
  ["workspaces", "data", "conversations", "runs", "artifacts", "finops", "settings"],
);
assert.equal(resolvePrimaryView("finops", null), "finops");
assert.equal(navigationAccessState("finops", null), "loading");
assert.equal(navigationAccessState("finops", { sections: { finops: { visible: false } } }), "denied");
```

- [x] **Step 2: Run navigation tests and verify RED**

Run: `node --test src/constants.test.mjs src/navigationContract.test.mjs src/shellNavigation.test.mjs`

Expected: FAIL because runs is absent, FinOps is filtered before capability resolution, and the footer still exists.

- [x] **Step 3: Implement stable shell**

```js
export function visibleNavGroups() {
  return NAV_GROUPS;
}

export function navigationAccessState(view, capabilities) {
  if (view !== "finops") return "allowed";
  if (!capabilities) return "loading";
  return capabilities?.sections?.finops?.visible === true ? "allowed" : "denied";
}
```

Add the dedicated `runs` item, rename labels to “会话”“运营管理”, remove `.ws-foot`, and render the FinOps local loading/denied state without hiding the navigation.

- [x] **Step 4: Write failing automatic-analysis visibility test**

```js
assert.deepEqual(executionMessageVisibility({ stayOnDashboard: true }), {
  appendUser: false,
  appendAssistant: false,
});
assert.equal(executionMessageVisibility({}).appendUser, true);
```

- [x] **Step 5: Run execution test and verify RED**

Run: `node --test src/executionIdentity.test.mjs`

Expected: FAIL because `executionMessageVisibility` does not exist.

- [x] **Step 6: Implement hidden automatic-analysis messages**

```js
export function executionMessageVisibility(options = {}) {
  const hidden = options.stayOnDashboard === true;
  return { appendUser: !hidden, appendAssistant: !hidden };
}
```

Use it before `setMessages` in `App.run`; keep payload `origin` and `persist_messages=false` unchanged.

- [x] **Step 7: Write failing localized network/auth test**

```js
const error = await toUserFacingRequestError(
  new TypeError("Failed to fetch"),
  async () => ({ authenticated: false }),
);
assert.equal(error.message, "登录已失效，请刷新后重新登录");
```

- [x] **Step 8: Run API test and verify RED**

Run: `node --test src/finopsApi.test.mjs`

Expected: FAIL because the error classifier does not exist.

- [x] **Step 9: Implement auth-aware error translation and verify GREEN**

Catch only transient fetch failures, probe `/.auth/me`, and return either the login-expired message or “暂时无法连接服务，请稍后重试”; never return raw `Failed to fetch`.

Run: `node --test src/constants.test.mjs src/navigationContract.test.mjs src/shellNavigation.test.mjs src/executionIdentity.test.mjs src/finopsApi.test.mjs`

Expected: PASS.

- [x] **Step 10: Commit Task 1**

```powershell
git add -- web/src/constants.js web/src/components.jsx web/src/App.jsx web/src/api.js web/src/executionIdentity.js web/src/constants.test.mjs web/src/navigationContract.test.mjs web/src/shellNavigation.test.mjs web/src/executionIdentity.test.mjs web/src/finopsApi.test.mjs
git commit -m "fix: stabilize operations shell and analysis visibility"
```

### Task 2: 指标交互、跨图联动与轻量 AI 浮框

**Files:**
- Create: `web/src/finopsInteraction.js`
- Create: `web/src/finopsInteraction.test.mjs`
- Create: `web/src/FinOpsAssistant.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/finopsViewModel.test.mjs`
- Modify: `web/src/api.js`
- Modify: `web/src/styles.css`
- Create: `backend/finops/assistant.py`
- Modify: `backend/finops/router.py`
- Test: `tests/test_finops_assistant.py`

**Interfaces:**
- Produces: `metricContext(metric, scope) -> MetricContext`.
- Produces: `metricTooltip(metric) -> { rows, evidenceState, actions }`.
- Produces: `POST /api/finops/assistant/query`.

- [x] **Step 1: Write failing interaction view-model tests**

```js
assert.equal(metricTooltip({ kind: "cache", cache: { hit: 8, miss: 2 } }).rows[0].label, "缓存命中");
assert.equal(metricTooltip({ kind: "latency", p95_ms: 2100 }).rows.some((row) => row.label.includes("缓存")), false);
assert.deepEqual(applyDimensionFilter({}, { dimension: "model", value: "gpt-5" }), { model: "gpt-5" });
```

- [x] **Step 2: Verify RED**

Run: `node --test src/finopsInteraction.test.mjs src/finopsViewModel.test.mjs`

Expected: FAIL because the interaction module is absent.

- [x] **Step 3: Implement pure interaction models**

Implement metric-specific rows, safe `MetricContext`, equal-period comparison metadata, filter chips, reset, reduced-motion state and URL-safe non-sensitive filter serialization.

- [x] **Step 4: Write failing assistant service tests**

```python
def test_assistant_rejects_unknown_context_fields_and_cites_allowed_evidence():
    body = AssistantRequest.model_validate({
        "question": "为什么缓存命中率下降？",
        "metric_context": {
            "metric_id": "cache_hit_rate",
            "label": "缓存命中率",
            "value": 62.5,
            "unit": "%",
            "window": {"from": "2026-07-01T00:00:00Z", "to": "2026-07-26T00:00:00Z"},
            "filters": {"workspace_id": "ws-a"},
            "data_status": "partial",
            "evidence_state": "observed",
        },
    })
    assert body.metric_context.metric_id == "cache_hit_rate"
```

- [x] **Step 5: Verify backend RED**

Run: `python -m pytest tests/test_finops_assistant.py -q`

Expected: FAIL because `backend.finops.assistant` is absent.

- [x] **Step 6: Implement typed assistant**

Add strict Pydantic request/response types, reuse the Foundry analysis runner with only bounded aggregates and allowlisted evidence refs, and return answer, evidence state, evidence links and suggested follow-ups. Do not expose approval or execution operations.

- [x] **Step 7: Replace Agent cards with compact popover**

Add a fixed AI button, anchored non-modal chat popover, context chip, four suggested questions, message history limited to the current page session, clear-context action and “查看证据”. Remove full-width FinOps/ROI Agent panels.

- [x] **Step 8: Add accessible chart interaction**

Add hover/focus/tap tooltips, 160–220ms opacity/transform transitions, cross-chart selection, filter chips, comparison labels and event annotations. Under `prefers-reduced-motion: reduce`, remove transitions.

- [x] **Step 9: Verify Task 2**

Run: `node --test src/finopsInteraction.test.mjs src/finopsViewModel.test.mjs src/finopsApi.test.mjs`

Run: `python -m pytest tests/test_finops_assistant.py tests/test_finops_api.py -q`

Expected: PASS.

- [x] **Step 10: Commit Task 2**

```powershell
git add -- web/src/finopsInteraction.js web/src/finopsInteraction.test.mjs web/src/FinOpsAssistant.jsx web/src/FinOpsPortal.jsx web/src/finopsViewModel.js web/src/finopsViewModel.test.mjs web/src/api.js web/src/styles.css backend/finops/assistant.py backend/finops/router.py tests/test_finops_assistant.py
git commit -m "feat: add metric-aware operations assistant"
```

### Task 3: 预算、预测、分摊、保存视图与导出

**Files:**
- Create: `backend/finops/planning.py`
- Create: `backend/finops/saved_views.py`
- Create: `backend/finops/sql_planning.py`
- Modify: `backend/sql/finops_schema.sql`
- Modify: `backend/finops/router.py`
- Modify: `web/src/api.js`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Test: `tests/test_finops_planning.py`
- Test: `tests/test_finops_saved_views.py`
- Test: `tests/test_finops_api.py`
- Test: `web/src/finopsViewModel.test.mjs`

**Interfaces:**
- Produces: `GET/POST/PATCH /api/finops/budgets`.
- Produces: `GET/POST/DELETE /api/finops/views`.
- Produces: `GET /api/finops/export.csv`.

- [x] **Step 1: Write failing budget and allocation tests**

Test an organization/department/workspace budget, unique workspace allocation, “未归属”, 80/100 thresholds, elapsed-period burn rate and end-of-period forecast with explicit `estimated` status.

- [x] **Step 2: Verify RED**

Run: `python -m pytest tests/test_finops_planning.py -q`

Expected: FAIL because planning service is absent.

- [x] **Step 3: Implement planning domain and additive SQL**

Create strict `BudgetDefinition`, `BudgetProgress`, `SavedView` models; add tenant-scoped tables with optimistic versions; compute forecast only from priced evidence and return confidence/data status.

- [x] **Step 4: Write failing API authorization and export tests**

Verify tenant isolation, workspace narrowing, safe saved-view fields, CSV formula neutralization, UTF-8 BOM and no raw actor/provider identifiers.

- [x] **Step 5: Verify RED and implement routes**

Run: `python -m pytest tests/test_finops_saved_views.py tests/test_finops_api.py -q`

Expected before implementation: FAIL; after bounded routes and repositories: PASS.

- [x] **Step 6: Implement Cost & Budget UI**

Add budget burn, forecast band, department/workspace allocation, model/Agent doughnuts, unpriced coverage, IT/Finance saved views and authorized CSV export. Zero/single-category doughnuts use an empty or single-total state.

- [x] **Step 7: Verify Task 3**

Run: `python -m pytest tests/test_finops_planning.py tests/test_finops_saved_views.py tests/test_finops_api.py tests/test_finops_sql.py -q`

Run: `node --test src/finopsViewModel.test.mjs src/finopsApi.test.mjs`

Expected: PASS.

- [x] **Step 8: Commit Task 3**

```powershell
git add -- backend/finops/planning.py backend/finops/saved_views.py backend/finops/sql_planning.py backend/sql/finops_schema.sql backend/finops/router.py web/src/api.js web/src/FinOpsPortal.jsx web/src/finopsViewModel.js tests/test_finops_planning.py tests/test_finops_saved_views.py tests/test_finops_api.py web/src/finopsViewModel.test.mjs
git commit -m "feat: add finops planning and allocation"
```

### Task 4: 单位经济、ROI 证据漏斗与情景测算

**Files:**
- Create: `backend/finops/roi_economics.py`
- Modify: `backend/finops/router.py`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Test: `tests/test_finops_roi_economics.py`
- Test: `web/src/finopsViewModel.test.mjs`

**Interfaces:**
- Produces: `GET /api/finops/roi/economics`.
- Consumes: existing workspace ROI snapshot, verified outcome events and immutable ROI scenarios.

- [x] **Step 1: Write failing unit-economics tests**

Cover cost per successful request/analysis/artifact, zero denominators, partial costs, verified business values, evidence gaps and estimated scenarios.

- [x] **Step 2: Verify RED**

Run: `python -m pytest tests/test_finops_roi_economics.py -q`

Expected: FAIL because the aggregator is absent.

- [x] **Step 3: Implement evidence-safe ROI economics**

Return the four-stage funnel `investment → usage → output → outcome`; emit verified ROI only when both cost and value evidence are complete; keep scenarios labelled `estimated`.

- [x] **Step 4: Implement ROI UI**

Render the funnel, unit-economics cards, evidence gaps, verified value sources and scenario comparison without showing a single unsupported ROI percentage.

- [x] **Step 5: Verify and commit Task 4**

Run: `python -m pytest tests/test_finops_roi_economics.py tests/test_roi_service.py tests/test_roi_scenarios.py -q`

Run: `node --test src/finopsViewModel.test.mjs`

Expected: PASS.

```powershell
git add -- backend/finops/roi_economics.py backend/finops/router.py web/src/FinOpsPortal.jsx web/src/finopsViewModel.js tests/test_finops_roi_economics.py web/src/finopsViewModel.test.mjs
git commit -m "feat: add evidence-based roi economics"
```

### Task 5: 风险机会队列、全量验证与候选部署

**Files:**
- Create: `backend/finops/opportunities.py`
- Modify: `backend/finops/router.py`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/styles.css`
- Test: `tests/test_finops_opportunities.py`
- Test: `web/src/finopsViewModel.test.mjs`
- Test: `web/tests/finops-operations-management.spec.mjs`

**Interfaces:**
- Produces: `GET /api/finops/opportunities`.
- Consumes: anomalies, recommendations, estimated impact, evidence coverage and governance actions.

- [ ] **Step 1: Write failing opportunity-ranking tests**

Verify impact/confidence/effort ordering, insufficient samples entering “观察中”, no savings estimate without priced evidence, and no automatic action transitions.

- [ ] **Step 2: Verify RED and implement queue**

Run: `python -m pytest tests/test_finops_opportunities.py -q`

Expected before implementation: FAIL; after typed aggregator and route: PASS.

- [ ] **Step 3: Implement risk workbench**

Unify trend annotations, anomaly rows and recommendations around one opportunity ID; show impact, confidence, difficulty, evidence and typed draft action.

- [ ] **Step 4: Add Playwright acceptance**

Cover immediate navigation, desktop/mobile layout, stable skeleton, localized auth failure, component partial failure, hover/focus/tap metric tooltip, cross-filtering, AI popover, run navigation, hidden auto-analysis message, budget/ROI/risk states and reduced motion.

- [ ] **Step 5: Run full verification**

Run: `python -m pytest -q`

Run: `node --test src/*.test.mjs`

Run: `npm run build`

Run: `npx playwright test`

Expected: all commands exit 0; no failed tests; browser screenshots show no clipping, late navigation or layout shift.

- [ ] **Step 6: Deploy zero-traffic candidates and verify**

Deploy backend and web candidates with zero traffic, then verify health, authenticated APIs, desktop/mobile hard refresh and rollback target. Do not enable governance actions or switch production traffic before candidate evidence is recorded.

- [ ] **Step 7: Commit Task 5**

```powershell
git add -- backend/finops/opportunities.py backend/finops/router.py web/src/FinOpsPortal.jsx web/src/finopsViewModel.js web/src/styles.css tests/test_finops_opportunities.py web/src/finopsViewModel.test.mjs web/tests/finops-operations-management.spec.mjs
git commit -m "feat: complete operations management workbench"
```
