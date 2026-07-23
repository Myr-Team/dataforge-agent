# Model Routing and Cost Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Give workspace Owners a real Foundry model-routing control plane and a truthful, per-run token and estimated-cost monitoring experience.

**Architecture:** The server allowlist remains the only source of real model deployments. Workspace metadata stores a versioned execution policy and optional price card. The runtime pins both revisions to model-response metadata, and monitoring aggregates only persisted evidence.

**Tech Stack:** FastAPI, Pydantic, Python contextvars, React, Vite, Lucide React, pytest, Node test runner, Azure Container Apps.

## Global Constraints

- Do not change Easy Auth, Entra sign-in, token storage, Key Vault, or APIM authentication.
- Only server allowlisted routes may be selected; clients never submit raw deployments, endpoints, credentials, or provider URLs.
- Do not hard-code route assignments, price, cost, ROI, dataset conclusions, or industry outcomes.
- Price-card results are always estimated; Azure invoice cost and ROI remain unavailable until independently evidenced.
- Missing usage, price, or source evidence stays unavailable/partial, never zero or healthy by default.
- Policy, price-card, cost, and workspace monitoring reads/writes are Owner-only.
- Persist only route IDs, deployments, revisions, usage, bounded status/reason codes, and safe actor projections.
- Keep stable layouts at 1536 x 960 and 1024 x 800.
- Candidate revisions remain at zero traffic until user approval after evidence.

## File Structure

| File | Responsibility |
| --- | --- |
| backend/workspace_model_config.py | Validate/version workspace routing policy and price cards; calculate safe estimates. |
| backend/model_policy.py | Select allowlisted policy/manual/fallback routes and scope request metadata. |
| backend/orchestrator.py | Resolve effective policy once per request and scope all model call paths. |
| backend/foundry_client.py | Attach selection and estimate evidence to actual provider usage. |
| backend/run_store.py | Persist safe route/cost response metadata. |
| backend/control_plane.py | Owner APIs for model routing/price card and dashboard projections. |
| backend/monitoring_dashboard.py | Aggregate model, execution kind, member, token, and estimated-cost evidence. |
| backend/schemas.py | Validate optional route-ID override on chat requests. |
| web/src/ModelRoutingPage.jsx | Stable model matrix and policy/price-card modals. |
| web/src/modelRoutingViewModel.js | Safe routing display model. |
| web/src/MonitorPage.jsx | Clear observed consumption and estimated-cost dashboard. |
| web/src/constants.js, App.jsx, components.jsx, styles.css | Nav migration, avatar preferences, and stable UI. |
| tests/test_*.py, web/src/*.test.mjs | Backend authorization/estimate and client contract tests. |

---

### Task 1: Add a versioned workspace model configuration domain

**Files:**
- Create: backend/workspace_model_config.py
- Modify: backend/model_policy.py
- Test: tests/test_workspace_model_config.py
- Test: tests/test_model_policy.py

**Interfaces:**
- validate_workspace_routing_policy(raw, routes) -> dict[str, Any]
- normalize_workspace_price_card(raw, routes) -> dict[str, Any]
- estimate_model_cost(usage, selected_route, price_card) -> dict[str, Any]
- select_text_route_record(..., policy=None, manual_route_id=None, price_card=None)

- [ ] **Step 1: Write failing tests**

~~~python
def test_workspace_policy_rejects_incompatible_route() -> None:
    routes = [
        ModelRoute("sol", "gpt-5.6-sol", "Sol", frozenset({"chat", "analysis"})),
        ModelRoute("luna", "gpt-5.6-luna", "Luna", frozenset({"chat"})),
    ]
    assert validate_workspace_routing_policy(
        {"assignments": {"full_analysis": {"primary_route_id": "sol"}}}, routes
    )["assignments"]["full_analysis"]["primary_route_id"] == "sol"
    with pytest.raises(ValueError, match="capability"):
        validate_workspace_routing_policy(
            {"assignments": {"full_analysis": {"primary_route_id": "luna"}}}, routes
        )

def test_estimate_requires_usage_and_matching_price() -> None:
    estimate = estimate_model_cost(
        {"input_tokens": 1000, "output_tokens": 500},
        {"route_id": "sol", "price_card_revision": 2},
        {"revision": 2, "currency": "USD", "entries": [{
            "route_id": "sol", "input_per_million": 2,
            "output_per_million": 8, "source_label": "unit",
            "updated_at": "2026-07-23T00:00:00Z"}]},
    )
    assert estimate["status"] == "estimated"
    assert estimate["amount"] == 0.006
    assert estimate_model_cost({"input_tokens": None, "output_tokens": 1}, {"route_id": "sol"}, {"revision": 2, "entries": []})["status"] == "unavailable"
~~~

- [ ] **Step 2: Confirm red**

Run: python -m pytest -q tests/test_workspace_model_config.py tests/test_model_policy.py -k "workspace_policy or estimate"

Expected: FAIL because the module and extended selector do not exist.

- [ ] **Step 3: Implement the minimal strict domain**

Use fixed execution kinds direct_reply, follow_up, full_analysis, and audit_repair. Route references must be allowlisted and capability-compatible. Price entries must have finite, non-negative rates, a currency, source label, and bounded timestamp. The estimator returns only estimated with amount/currency/revision/formula or unavailable with usage_not_recorded or price_not_configured.

Selector order: valid Owner manual override, valid workspace-policy primary, valid workspace-policy fallback, existing server default. Extend SelectedTextRoute with policy_revision and price_card_revision.

- [ ] **Step 4: Confirm green**

Run: python -m pytest -q tests/test_workspace_model_config.py tests/test_model_policy.py

Expected: PASS; existing follow-up evaluation gate tests still pass.

- [ ] **Step 5: Commit**

~~~powershell
git add backend/workspace_model_config.py backend/model_policy.py tests/test_workspace_model_config.py tests/test_model_policy.py
git commit -m "feat: add workspace model routing policy"
~~~

### Task 2: Pin route and estimate evidence to actual model responses

**Files:**
- Modify: backend/schemas.py
- Modify: backend/orchestrator.py
- Modify: backend/foundry_client.py
- Modify: backend/run_store.py
- Test: tests/test_model_policy.py
- Test: tests/test_model_route_telemetry.py

**Interfaces:**
- ChatRequest.model_route_id: str | None validates only a route ID.
- SelectedTextRoute is scoped throughout direct reply, follow-up, analysis, audit, and MAF calls.
- model_response records selection, revisions, and safe cost_estimate.

- [ ] **Step 1: Write failing telemetry tests**

~~~python
def test_response_metadata_pins_selected_estimate(monkeypatch) -> None:
    monkeypatch.setattr(foundry_client, "current_text_route", lambda: SELECTED_TERRA)
    monkeypatch.setattr(foundry_client, "current_price_card", lambda: PRICE_CARD)
    metadata = foundry_client._response_meta(Response(
        "resp-1", {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
    ), "unit")
    assert metadata["selection"] == "manual"
    assert metadata["policy_revision"] == 4
    assert metadata["cost_estimate"]["status"] == "estimated"

def test_run_store_keeps_estimate_without_unit_rates() -> None:
    run_store.start_run("run-cost", "ws-cost", "hello")
    run_store.record_event("run-cost", "model_response", MODEL_RESPONSE_WITH_ESTIMATE)
    record = run_store.complete_run("run-cost")
    assert record["models"][0]["cost_estimate"]["amount"] == 0.001
    assert "input_per_million" not in str(record)
~~~

- [ ] **Step 2: Confirm red**

Run: python -m pytest -q tests/test_model_route_telemetry.py tests/test_model_policy.py -k "price_card or estimate or policy_revision"

Expected: FAIL because response/run metadata lacks the fields.

- [ ] **Step 3: Implement request-scoped execution**

Add model_route_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$") in ChatRequest. Create an orchestrator helper that loads workspace policy/price card once, honors a manual override only after Owner authorization, and scopes every existing select_text_route_record call. Extend _response_meta with selection revisions and estimate_model_cost. In run_store.record_event, persist only safe estimate output and strip unit-rate fields.

- [ ] **Step 4: Confirm green**

Run: python -m pytest -q tests/test_model_policy.py tests/test_model_route_telemetry.py tests/test_orchestrator_chat.py

Expected: PASS; missing price becomes unavailable, never numeric.

- [ ] **Step 5: Commit**

~~~powershell
git add backend/schemas.py backend/orchestrator.py backend/foundry_client.py backend/run_store.py tests/test_model_policy.py tests/test_model_route_telemetry.py
git commit -m "feat: persist model route cost evidence"
~~~

### Task 3: Add Owner routing/price-card API and dashboard aggregation

**Files:**
- Modify: backend/control_plane.py
- Modify: backend/workspace_authz.py
- Modify: backend/monitoring_dashboard.py
- Test: tests/test_workspace_roles.py
- Test: tests/test_monitoring_dashboard.py
- Test: tests/test_monitoring_dashboard_api.py

**Interfaces:**
- GET/PUT /api/workspaces/{workspace_id}/governance/model-routing
- GET/PUT /api/workspaces/{workspace_id}/governance/model-price-card
- Monitor snapshot adds execution_kinds, selection counts, estimated cost, and unpriced-call count.

- [ ] **Step 1: Write failing API and aggregation tests**

~~~python
def test_editor_cannot_read_or_update_model_routing(client, editor_headers) -> None:
    assert client.get("/api/workspaces/ws-model/governance/model-routing", headers=editor_headers).status_code == 403
    assert client.put("/api/workspaces/ws-model/governance/model-routing", headers=editor_headers, json={"assignments": {}}).status_code == 403

def test_dashboard_aggregates_persisted_estimate() -> None:
    snapshot = build_monitor_dashboard(["ws-model"], scope="current",
        from_value="2026-07-23T00:00:00Z", to_value="2026-07-24T00:00:00Z",
        actor={}, run_loader=lambda _: [RUN_WITH_ESTIMATE])
    assert snapshot["summary"]["cost"]["amount"] == 0.012
    assert snapshot["summary"]["cost"]["status"] == "estimated"
    assert snapshot["execution_kinds"][0]["execution_kind"] == "full_analysis"
~~~

- [ ] **Step 2: Confirm red**

Run: python -m pytest -q tests/test_workspace_roles.py tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py -k "model_routing or price_card or estimated_cost or execution_kind"

Expected: FAIL because the endpoints and aggregation do not exist.

- [ ] **Step 3: Implement bounded owner contracts**

Use _require_workspace_owner for each new endpoint. Policy writes call the domain validator, increment metadata revision, attach a safe actor projection, save, and audit action/revision/route IDs only. Price-card writes normalize entries and increment revision. GET routing returns only allowlisted public routes, effective policy, and safe allocation summary. GET price-card is Owner-only.

Aggregate models[].cost_estimate by response ID, deployment, route, execution kind, selection, and existing safe member projection. A total exists only when all relevant records are estimated in one currency; otherwise report partial/unavailable plus unpriced_calls.

- [ ] **Step 4: Confirm green**

Run: python -m pytest -q tests/test_workspace_roles.py tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py tests/test_roi_service.py tests/test_roi_security.py

Expected: PASS. Existing verified ROI rules remain untouched.

- [ ] **Step 5: Commit**

~~~powershell
git add backend/control_plane.py backend/workspace_authz.py backend/monitoring_dashboard.py tests/test_workspace_roles.py tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py
git commit -m "feat: add owner model routing control plane"
~~~

### Task 4: Implement Model Routing UI and move preferences to avatar menu

**Files:**
- Modify: web/src/constants.js
- Modify: web/src/api.js
- Create: web/src/modelRoutingViewModel.js
- Create: web/src/ModelRoutingPage.jsx
- Modify: web/src/GovernanceCenter.jsx
- Modify: web/src/components.jsx
- Modify: web/src/App.jsx
- Modify: web/src/styles.css
- Test: web/src/constants.test.mjs
- Test: web/src/modelRoutingViewModel.test.mjs
- Test: web/src/modelRoutingPage.test.mjs

**Interfaces:**
- loadModelRouting, updateModelRouting, loadModelPriceCard, updateModelPriceCard.
- ModelRoutingPage({ workspaceId, workspaceAccess }).
- Local preference dataforge-theme: system|light|dark.

- [ ] **Step 1: Write failing nav/view-model tests**

~~~javascript
test("governance rail shows monitor and model routing but not settings", () => {
  const ids = visibleNavItems(ownerCapabilities).map((item) => item.id);
  assert.deepEqual(ids.slice(-4), ["members", "lineage", "cost-value", "model-routing"]);
  assert.equal(ids.includes("settings"), false);
});

test("route display uses server label and unavailable price state", () => {
  const [route] = modelRoutingViewModel({
    routes: [{ id: "luna", label: "GPT-5.6 Luna", deployment: "gpt-5.6-luna" }],
    policy: { assignments: {} }, recent_allocation: [],
  }).routes;
  assert.equal(route.label, "GPT-5.6 Luna");
  assert.equal(route.costLabel, "未配置价格卡");
});
~~~

- [ ] **Step 2: Confirm red**

Run: node --test web/src/constants.test.mjs web/src/modelRoutingViewModel.test.mjs web/src/modelRoutingPage.test.mjs

Expected: FAIL because Model Routing does not exist.

- [ ] **Step 3: Implement stable Owner UX**

Replace passive models/connectivity with model-routing; relabel cost/value as Monitor; remove Settings from nav/capability handling. ModelRoutingPage uses a stable header, fixed model matrix, safe allocation rows, a Configure Routing modal, and a Price Card modal. Both modals use server data, submit only route IDs, and disclose estimates.

Move account identity and a system/light/dark segmented theme control into the existing avatar menu. Store only dataforge-theme locally and set document.documentElement.dataset.theme. Do not create another Settings page.

- [ ] **Step 4: Confirm green**

Run:
~~~powershell
node --test web/src/constants.test.mjs web/src/modelRoutingViewModel.test.mjs web/src/modelRoutingPage.test.mjs web/src/governanceViewModel.test.mjs
npm --prefix web run build
~~~

Expected: PASS with no settings nav item and no hard-coded route/price data.

- [ ] **Step 5: Commit**

~~~powershell
git add web/src/constants.js web/src/api.js web/src/modelRoutingViewModel.js web/src/ModelRoutingPage.jsx web/src/GovernanceCenter.jsx web/src/components.jsx web/src/App.jsx web/src/styles.css web/src/constants.test.mjs web/src/modelRoutingViewModel.test.mjs web/src/modelRoutingPage.test.mjs
git commit -m "feat: add model routing control surface"
~~~

### Task 5: Make monitoring visibly show consumption and estimates

**Files:**
- Modify: web/src/monitorDashboardViewModel.js
- Modify: web/src/MonitorPage.jsx
- Modify: web/src/styles.css
- Test: web/src/monitorDashboardViewModel.test.mjs
- Test: web/src/MonitorPage.test.mjs

**Interfaces:** Cost displays use estimated vs unavailable states; the dashboard additionally renders execution-kind/selection rows.

- [ ] **Step 1: Write a failing truthfulness test**

~~~javascript
test("estimated spend and ROI remain distinct", () => {
  const view = monitorDashboardViewModel({
    summary: { cost: { status: "estimated", amount: 0.012, currency: "USD", unpriced_calls: 0 },
      roi: { status: "unavailable" } },
    execution_kinds: [{ execution_kind: "direct_reply", calls: 2,
      selection_counts: { manual: 1, workspace_policy: 1 } }],
  });
  assert.equal(view.cards.cost.badge, "估算");
  assert.equal(view.cards.roi.value, "未验证");
  assert.equal(view.executionRows[0].selectionLabel, "手动 1 / 策略 1");
});
~~~

- [ ] **Step 2: Confirm red**

Run: node --test web/src/monitorDashboardViewModel.test.mjs web/src/MonitorPage.test.mjs

Expected: FAIL because estimated cost and execution rows are absent.

- [ ] **Step 3: Implement compact source-backed regions**

Rename the KPI to Estimated Cost, visibly show unpriced/unknown calls, and keep verified ROI separate. Add one compact execution allocation frame with calls, observed tokens, selection counts, and estimated-cost state. Use fixed chart/frame CSS grid dimensions; no page-level resize or button shift.

- [ ] **Step 4: Confirm green**

Run:
~~~powershell
node --test web/src/monitorDashboardViewModel.test.mjs web/src/MonitorPage.test.mjs
npm --prefix web run build
~~~

Expected: PASS; unavailable cost is never numeric and estimated cost is never labelled verified ROI.

- [ ] **Step 5: Commit**

~~~powershell
git add web/src/monitorDashboardViewModel.js web/src/MonitorPage.jsx web/src/styles.css web/src/monitorDashboardViewModel.test.mjs web/src/MonitorPage.test.mjs
git commit -m "feat: visualize model consumption estimates"
~~~

### Task 6: Verify candidate routes without production traffic

**Files:**
- Create: docs/verification/2026-07-23-model-routing-cost-control-candidate.md
- Modify: design doc only if implementation alters its public contract.

- [ ] **Step 1: Run full code verification**

~~~powershell
python -m pytest -q -x
node --test web/src/*.test.mjs
npm --prefix web run build
~~~

Expected: all pass. Capture exact counts/output in the evidence note.

- [ ] **Step 2: Build and deploy zero-traffic candidate revisions**

Build backend/web images from the tracked source archive. Apply a candidate-only allowlist with the verified Foundry deployment inventory. Keep candidate revisions at 0% traffic and point only candidate web to candidate backend. Do not alter production weights.

- [ ] **Step 3: Run candidate evidence checks**

Check health and Owner endpoints, then use signed-in candidate UI to save a route policy and a clearly labelled temporary price card. Initiate one Owner manual-route follow-up and verify its completed run has matching model, token, selection, and cost state. Validate all governance pages at 1536 x 960 and 1024 x 800. If APIM rejects a route, record its bounded error and leave production untouched.

- [ ] **Step 4: Record evidence and request promotion approval**

Document test results, candidate revisions, 0% traffic, safe route IDs, sanitized run/response IDs, screenshots, and any APIM limitation. Ask for explicit production promotion approval; do not promote automatically.

- [ ] **Step 5: Commit evidence**

~~~powershell
git add docs/verification/2026-07-23-model-routing-cost-control-candidate.md
git commit -m "docs: record model routing candidate verification"
~~~

## Plan Self-Review

- **Spec coverage:** Tasks 1-3 cover validated routing, manual selection, policy/price revision pinning, safe cost evidence, Owner APIs, and aggregate observability. Tasks 4-5 cover the split product surface and clear estimated-versus-ROI visual treatment. Task 6 validates actual candidate behavior before traffic changes.
- **Placeholder scan:** Each task names files, APIs, tests, commands, expected outcomes, and safety conditions. No price or ROI value is invented.
- **Type consistency:** Client sends only model_route_id; backend selects deployment. SelectedTextRoute carries policy/price revisions into response metadata; cost_estimate is persisted then aggregated.

