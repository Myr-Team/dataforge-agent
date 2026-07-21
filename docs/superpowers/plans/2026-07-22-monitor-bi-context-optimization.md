# Monitor BI and Context Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an owner-only Monitor BI page backed by one truthful telemetry read model, then add recorded model-route decisions, bounded Context Packs, and evaluation-gated optimization.

**Architecture:** A new backend monitoring dashboard service aggregates allowlisted run, route, APIM, audit, member, and outcome evidence into one API contract. The React Monitor page consumes that contract through a focused view model and fixed-height SVG chart primitives. Context optimization remains in the orchestration boundary; it is opt-in by policy, versioned, measurable, and falls back to the existing conversation history path whenever required evidence is unavailable.

**Tech Stack:** FastAPI, existing Blob-backed run/audit/outcome stores, Azure Monitor/APIM evidence, Azure AI Foundry text deployments, React, Vite, SVG/CSS charts, Node test runner, pytest, Playwright.

## Global Constraints

- Do not modify Easy Auth, tenant configuration, or Entra token handling.
- Owner authorization is enforced server-side for every monitor endpoint and portfolio aggregation.
- All values must originate from persisted run records, APIM/Azure Monitor evidence, or verified outcome records; missing data remains `unknown`, `unavailable`, or `pending_verification`.
- Never return raw prompts, message history, credentials, Entra claims, model reasoning, or unredacted telemetry attributes.
- Keep image-generation calls out of governed text-model cost and route claims until a separate gateway implementation exists.
- Use the current blue/white DataForge design language, fixed layout geometry, and opacity/transform-only data transitions.
- New behavior is test-first. Run focused tests after every task, then full backend tests and frontend build before promotion.

---

### Task 1: Define the Monitor Read Model and Owner-Scoped API

**Files:**
- Create: `backend/monitoring_dashboard.py`
- Modify: `backend/control_plane.py: import block, route declarations, authorization helpers`
- Modify: `backend/schemas.py: monitor response models if the module centralizes public API schemas`
- Test: `tests/test_monitoring_dashboard.py`
- Test: `tests/test_monitoring_dashboard_api.py`

**Interfaces:**
- Consumes: `run_store.list_runs`, `control_plane.workspace_cost_value_snapshot`, `control_plane.workspace_member_chargeback`, `control_plane.workspace_audit_events`, `azure_monitor_client.get_gateway_metric_evidence`, `workspace_store.list_workspaces`, and the current actor/role helpers.
- Produces: `build_monitor_dashboard(workspace_ids: list[str], *, scope: str, from_value: str, to_value: str, actor: dict[str, Any]) -> dict[str, Any]` and `GET /api/monitoring?scope=current|portfolio&workspace_id={id}&from={ISO}&to={ISO}`.

- [ ] **Step 1: Write failing aggregation tests**

```python
def test_monitor_dashboard_preserves_unknown_usage_and_groups_observed_model_routes() -> None:
    payload = build_monitor_dashboard(
        ["ws-a"],
        scope="current",
        from_value="2026-07-01T00:00:00Z",
        to_value="2026-07-08T00:00:00Z",
        actor={"actor_id": "owner-a"},
        run_loader=lambda _workspace_id: [
            {
                "run_id": "run-1", "workspace_id": "ws-a", "status": "completed",
                "completed_at": "2026-07-02T10:00:00Z", "duration_ms": 1200,
                "actor": {"actor_id": "owner-a"},
                "tokens": {"prompt": 80, "completion": 20, "total": 100},
                "models": [{"route": "follow_up", "deployment": "gpt-mini", "usage": {"total": 100}}],
            },
            {"run_id": "run-2", "workspace_id": "ws-a", "status": "completed", "completed_at": "2026-07-02T11:00:00Z"},
        ],
    )

    assert payload["summary"]["tokens"] == {"input": 80, "output": 20, "total": 100, "known_runs": 1, "unknown_runs": 1}
    assert payload["models"] == [{"deployment": "gpt-mini", "route": "follow_up", "calls": 1, "total_tokens": 100}]
    assert payload["summary"]["calls"]["observed"] == 2
```

- [ ] **Step 2: Run the failing aggregation test**

Run: `python -m pytest tests/test_monitoring_dashboard.py::test_monitor_dashboard_preserves_unknown_usage_and_groups_observed_model_routes -q`

Expected: FAIL because `backend.monitoring_dashboard` and `build_monitor_dashboard` do not exist.

- [ ] **Step 3: Implement the pure aggregation service**

```python
# backend/monitoring_dashboard.py
def build_monitor_dashboard(
    workspace_ids: list[str], *, scope: str, from_value: str, to_value: str,
    actor: dict[str, Any], run_loader: Callable[[str], list[dict[str, Any]]],
    cost_loader: Callable[[str, str, str], dict[str, Any]],
    audit_loader: Callable[[str], dict[str, Any]],
    outcome_loader: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any]:
    rows = [run for workspace_id in workspace_ids for run in run_loader(workspace_id) if _in_window(run, from_value, to_value)]
    usage = _usage_summary(rows)
    costs = _cost_summary(workspace_ids, from_value, to_value, cost_loader)
    quality = _quality_summary(rows, workspace_ids, audit_loader)
    roi = _roi_summary(costs, workspace_ids, outcome_loader)
    return {
        "scope": _scope_projection(scope, workspace_ids),
        "window": {"from": from_value, "to": to_value, "timezone": "UTC"},
        "freshness": _freshness_projection(),
        "summary": {"calls": _call_summary(rows), "tokens": usage, "cost": costs, "quality": quality, "roi": roi},
        "series": {"daily": _daily_series(rows)},
        "models": _model_rows(rows),
        "routes": _route_rows(rows),
        "members": _member_rows(rows, actor),
        "opportunity": _opportunity(rows, usage),
        "coverage": _coverage(rows),
    }
```

Implement `_usage_summary`, `_model_rows`, `_route_rows`, `_daily_series`, and every projection as pure helpers. Treat absent token/model/route fields as unknown. Use only public actor labels in `members` and only include full member rows when the caller is permitted.

- [ ] **Step 4: Run aggregation tests**

Run: `python -m pytest tests/test_monitoring_dashboard.py -q`

Expected: PASS, including zero-run, failed-run, unknown-model, and missing-cost cases.

- [ ] **Step 5: Write failing authorization/API tests**

```python
def test_monitor_api_rejects_non_owner_and_limits_portfolio_to_owned_workspaces(client, monkeypatch) -> None:
    monkeypatch.setattr(control_plane, "_owned_workspace_ids", lambda _request: ["ws-owned"])
    denied = client.get("/api/monitoring?scope=portfolio&workspace_id=ws-other&from=2026-07-01T00:00:00Z&to=2026-07-08T00:00:00Z")
    assert denied.status_code == 403

    allowed = client.get("/api/monitoring?scope=portfolio&workspace_id=ws-owned&from=2026-07-01T00:00:00Z&to=2026-07-08T00:00:00Z")
    assert allowed.status_code == 200
    assert allowed.json()["scope"]["workspace_ids"] == ["ws-owned"]
```

- [ ] **Step 6: Run the failing API test**

Run: `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_api_rejects_non_owner_and_limits_portfolio_to_owned_workspaces -q`

Expected: FAIL because `/api/monitoring` does not exist.

- [ ] **Step 7: Add the endpoint and owner-workspace resolver**

```python
# backend/control_plane.py
@router.get("/api/monitoring")
async def monitoring_dashboard(
    request: Request,
    scope: Literal["current", "portfolio"] = Query("current"),
    workspace_id: str = Query(min_length=1, max_length=160),
    from_value: str = Query(alias="from", max_length=64),
    to_value: str = Query(alias="to", max_length=64),
) -> dict[str, Any]:
    owned_ids = _owned_workspace_ids(request)
    if workspace_id not in owned_ids:
        raise HTTPException(status_code=403, detail="workspace access denied for monitor.read")
    selected_ids = owned_ids if scope == "portfolio" else [workspace_id]
    return await _call(build_monitor_dashboard, selected_ids, scope=scope, from_value=from_value, to_value=to_value, actor=actor_from_request(request, fallback=False))
```

`_owned_workspace_ids` must use persisted workspace ownership and the current trusted actor identity. Do not infer ownership from a client-supplied workspace ID.

- [ ] **Step 8: Run API tests**

Run: `python -m pytest tests/test_monitoring_dashboard_api.py tests/test_monitoring_service.py -q`

Expected: PASS; requests from a non-owner return 403 and portfolio responses omit unowned workspaces.

- [ ] **Step 9: Commit**

```powershell
git add backend/monitoring_dashboard.py backend/control_plane.py backend/schemas.py tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py
git commit -m "feat: add owner-scoped monitor dashboard API"
```

### Task 2: Record Actual Model Route and Usage Metadata Per Run

**Files:**
- Modify: `backend/model_policy.py`
- Modify: `backend/foundry_client.py`
- Modify: `backend/maf_agents.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/run_store.py`
- Test: `tests/test_model_policy.py`
- Test: `tests/test_model_route_telemetry.py`

**Interfaces:**
- Consumes: existing `RoutingDecision`, model allowlist, Foundry response usage, and APIM request headers.
- Produces: `select_text_route(execution_kind: str, *, candidate_enabled: bool) -> ModelRoute` and a persisted run model record `{route, deployment, selection, fallback_reason, usage, latency_ms}`.

- [ ] **Step 1: Write a failing policy test**

```python
def test_full_analysis_never_selects_followup_candidate_route(monkeypatch) -> None:
    monkeypatch.setenv("DF_MODEL_ROUTE_ALLOWLIST", json.dumps([
        {"id": "analysis", "deployment": "gpt-5.1", "label": "Analysis", "capabilities": ["analysis", "chat"]},
        {"id": "followup", "deployment": "gpt-5-mini", "label": "Follow-up", "capabilities": ["followup"]},
    ]))
    monkeypatch.setenv("DF_DEFAULT_MODEL_ROUTE", "analysis")

    assert select_text_route("full_analysis").route_id == "analysis"
    assert select_text_route("follow_up", candidate_enabled=True).route_id == "followup"
```

- [ ] **Step 2: Run the failing policy test**

Run: `python -m pytest tests/test_model_policy.py::test_full_analysis_never_selects_followup_candidate_route -q`

Expected: FAIL because `select_text_route` does not exist.

- [ ] **Step 3: Implement execution-kind route selection**

```python
def select_text_route(execution_kind: str, *, candidate_enabled: bool = False) -> ModelRoute:
    capability = {"full_analysis": "analysis", "audit_repair": "analysis", "follow_up": "followup", "direct_reply": "chat"}.get(execution_kind, "chat")
    if capability == "followup" and not candidate_enabled:
        capability = "chat"
    try:
        return resolve_text_route(capability=capability)
    except ModelPolicyError:
        return resolve_text_route(capability="analysis" if execution_kind in {"full_analysis", "audit_repair"} else "chat")
```

Use route categories from the orchestrator decision, never prompt keywords. Preserve current safe default behavior when no new route is configured.

- [ ] **Step 4: Run policy tests**

Run: `python -m pytest tests/test_model_policy.py -q`

Expected: PASS, including invalid allowlists and fallback cases.

- [ ] **Step 5: Write a failing route-record persistence test**

```python
def test_followup_run_persists_selected_route_model_usage_and_allowlisted_fallback() -> None:
    result = run_with_fake_foundry_response(
        execution_kind="follow_up",
        response_usage={"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
    )

    model = result["run"]["models"][0]
    assert model == {
        "route": "followup", "deployment": "gpt-5-mini", "selection": "policy",
        "fallback_reason": None, "usage": {"prompt": 40, "completion": 10, "total": 50},
        "latency_ms": 120,
    }
```

- [ ] **Step 6: Run the failing route-record test**

Run: `python -m pytest tests/test_model_route_telemetry.py::test_followup_run_persists_selected_route_model_usage_and_allowlisted_fallback -q`

Expected: FAIL because the run lacks one normalized model selection record.

- [ ] **Step 7: Wire the selected route through Foundry/MAF and the run store**

```python
# backend/orchestrator.py
execution_kind = execution_kind_from_decision(decision)
route = select_text_route(execution_kind, candidate_enabled=candidate_enabled_for_route("followup"))
with model_route_scope(route=route, execution_kind=execution_kind):
    response = await invoke_followup_model(req, context=followup_input, route=route)
append_model_execution(run, route=route, execution_kind=execution_kind, response=response)
```

Make `foundry_client._response_meta` and `maf_agents` consume the scoped route rather than independently resolving `chat` or `analysis`. Store only allowlisted `fallback_reason` values such as `candidate_not_eligible`, `capability_missing`, `provider_usage_missing`, and `provider_error`.

- [ ] **Step 8: Run telemetry tests**

Run: `python -m pytest tests/test_model_route_telemetry.py tests/test_model_policy.py tests/test_tracing_telemetry.py -q`

Expected: PASS; headers, persisted model rows, and APIM route IDs agree for a text call.

- [ ] **Step 9: Commit**

```powershell
git add backend/model_policy.py backend/foundry_client.py backend/maf_agents.py backend/orchestrator.py backend/run_store.py tests/test_model_policy.py tests/test_model_route_telemetry.py
git commit -m "feat: persist model route telemetry"
```

### Task 3: Build Bounded Context Packs with Safe Legacy Fallback

**Files:**
- Create: `backend/context_pack.py`
- Modify: `backend/conversation_store.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/run_store.py`
- Test: `tests/test_context_pack.py`
- Test: `tests/test_context_pack_integration.py`

**Interfaces:**
- Consumes: workspace profile/detail, latest structured analysis, audit constraints, evidence references, and public conversation metadata.
- Produces: `build_context_pack(request: ChatRequest, *, profile: dict, analysis: dict, facts: list[dict]) -> ContextPack`, `record_durable_fact(conversation_id: str, workspace_id: str, fact: dict[str, str]) -> dict[str, Any]`, and persisted `context_pack` telemetry.

- [ ] **Step 1: Write a failing Context Pack unit test**

```python
def test_context_pack_is_scoped_bounded_and_invalidated_by_evidence_revision() -> None:
    pack = build_context_pack(
        request=ChatRequest(workspace_id="ws-a", conversation_id="conv-a", message="Compare the pilot options"),
        profile={"revision": "data-r2", "summary": "Two candidate areas"},
        analysis={"revision": "run-r4", "verdict": "conditional", "evidence_refs": ["doc-1", "doc-2"]},
        facts=[{"scope": "ws-a:conv-a", "kind": "verified_constraint", "text": "Budget is capped"}] * 10,
    )

    assert pack.scope == {"workspace_id": "ws-a", "conversation_id": "conv-a"}
    assert len(pack.durable_facts) <= 6
    assert pack.fingerprint != ""
    assert "Compare the pilot options" not in pack.serialized_for_telemetry
```

- [ ] **Step 2: Run the failing Context Pack test**

Run: `python -m pytest tests/test_context_pack.py::test_context_pack_is_scoped_bounded_and_invalidated_by_evidence_revision -q`

Expected: FAIL because `backend.context_pack` does not exist.

- [ ] **Step 3: Implement the Context Pack model and durable fact store**

```python
@dataclass(frozen=True)
class ContextPack:
    scope: dict[str, str]
    version: str
    fingerprint: str
    workspace_facts: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    durable_facts: tuple[str, ...]
    audit_constraints: tuple[str, ...]

def build_context_pack(request, *, profile, analysis, facts):
    selected = [fact for fact in facts if _same_scope(fact, request) and _allowed_fact(fact)][-6:]
    return ContextPack(
        scope={"workspace_id": request.workspace_id, "conversation_id": request.conversation_id or ""},
        version="context-pack-v1",
        fingerprint=_fingerprint(request, profile, analysis, selected),
        workspace_facts=tuple(_workspace_facts(profile)),
        evidence_refs=tuple(_evidence_refs(analysis)),
        durable_facts=tuple(str(item["text"]) for item in selected),
        audit_constraints=tuple(_audit_constraints(analysis)),
    )
```

Persist only typed, allowlisted durable facts: `verified_constraint`, `selected_metric`, `accepted_scope`, and `evidence_revision`. Do not infer a fact from arbitrary raw user text. Build fingerprints from workspace ID, conversation ID, profile revision, analysis revision, evidence references, and fact IDs; never include raw prompt text.

- [ ] **Step 4: Run Context Pack unit tests**

Run: `python -m pytest tests/test_context_pack.py -q`

Expected: PASS, including cross-workspace exclusion, invalid fact rejection, max-six bound, and fingerprint changes.

- [ ] **Step 5: Write a failing orchestration fallback test**

```python
def test_followup_uses_context_pack_and_falls_back_to_legacy_history_when_pack_build_fails(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator, "build_context_pack", lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad revision")))
    result = run_followup("ws-a", "conv-a", "Refine the pilot")

    assert result["run"]["context_pack"]["status"] == "fallback"
    assert result["run"]["context_pack"]["fallback_reason"] == "pack_build_failed"
    assert result["reply"]
```

- [ ] **Step 6: Run the failing integration test**

Run: `python -m pytest tests/test_context_pack_integration.py::test_followup_uses_context_pack_and_falls_back_to_legacy_history_when_pack_build_fails -q`

Expected: FAIL because the follow-up route does not yet record Context Pack state.

- [ ] **Step 7: Integrate only into eligible follow-up execution**

```python
pack_result = try_build_context_pack(
    request=req,
    profile=workspace_profile,
    analysis=last_analysis,
    facts=conversation_durable_facts(conversation_id),
)
if pack_result.available:
    followup_input = pack_result.prompt_projection
    context_pack_meta = pack_result.public_metadata
else:
    followup_input = _compact_history(conversation_context(conversation_id))
    context_pack_meta = {"status": "fallback", "fallback_reason": pack_result.reason}
record_run_context_pack(run, context_pack_meta)
```

Do not replace the full-analysis, audit-repair, or market-research paths. Those paths keep their evidence-rich existing context until an independent evaluation explicitly qualifies a candidate route.

- [ ] **Step 8: Run Context Pack integration tests**

Run: `python -m pytest tests/test_context_pack.py tests/test_context_pack_integration.py tests/test_conversation_execution_linkage.py -q`

Expected: PASS; a bad pack does not fail a customer conversation.

- [ ] **Step 9: Commit**

```powershell
git add backend/context_pack.py backend/conversation_store.py backend/orchestrator.py backend/run_store.py tests/test_context_pack.py tests/test_context_pack_integration.py
git commit -m "feat: add bounded followup context packs"
```

### Task 4: Add Offline Evaluation and Candidate Eligibility Gates

**Files:**
- Create: `backend/context_evaluation.py`
- Create: `eval/context_optimization_cases.json`
- Modify: `backend/model_policy.py`
- Modify: `backend/monitoring_dashboard.py`
- Test: `tests/test_context_evaluation.py`
- Test: `tests/test_model_policy.py`

**Interfaces:**
- Consumes: sanitized evaluation cases, baseline and candidate response projections, Context Pack metadata, and evaluator version.
- Produces: `evaluate_context_candidate(cases, runner) -> EvaluationSummary` and `candidate_route_eligible(summary) -> bool`.

- [ ] **Step 1: Write a failing eligibility test**

```python
def test_candidate_route_is_ineligible_when_evidence_coverage_regresses() -> None:
    summary = EvaluationSummary(
        sample_count=20,
        baseline={"evidence_coverage": 0.90, "completion": 0.85},
        candidate={"evidence_coverage": 0.75, "completion": 0.90},
        evaluator_version="context-v1",
    )
    assert candidate_route_eligible(summary) is False
```

- [ ] **Step 2: Run the failing eligibility test**

Run: `python -m pytest tests/test_context_evaluation.py::test_candidate_route_is_ineligible_when_evidence_coverage_regresses -q`

Expected: FAIL because evaluation types and eligibility function do not exist.

- [ ] **Step 3: Implement deterministic evaluation summaries**

```python
def candidate_route_eligible(summary: EvaluationSummary) -> bool:
    return (
        summary.sample_count >= 20
        and summary.candidate["evidence_coverage"] >= summary.baseline["evidence_coverage"]
        and summary.candidate["completion"] >= summary.baseline["completion"]
    )
```

The runner interface may invoke a Foundry evaluator later, but unit tests use deterministic fixture results. Persist evaluator version, sample count, and status. Do not store prompts or raw answers in the summary artifact.

- [ ] **Step 4: Run evaluation tests**

Run: `python -m pytest tests/test_context_evaluation.py -q`

Expected: PASS for insufficient sample, evidence regression, completion regression, and eligible candidate cases.

- [ ] **Step 5: Extend route selection and monitor projections**

```python
def candidate_enabled_for_route(route_id: str) -> bool:
    summary = load_latest_evaluation(route_id)
    return summary is not None and candidate_route_eligible(summary)
```

Expose only `{status, sample_count, evaluator_version, eligible}` in monitor quality data. Keep the candidate disabled when no summary exists, when a summary is stale, or when it is ineligible.

- [ ] **Step 6: Run policy and dashboard tests**

Run: `python -m pytest tests/test_context_evaluation.py tests/test_model_policy.py tests/test_monitoring_dashboard.py -q`

Expected: PASS; monitor quality reports evaluator coverage without claiming evaluation success for missing samples.

- [ ] **Step 7: Commit**

```powershell
git add backend/context_evaluation.py backend/model_policy.py backend/monitoring_dashboard.py eval/context_optimization_cases.json tests/test_context_evaluation.py tests/test_model_policy.py
git commit -m "feat: gate context optimization on offline evaluation"
```

### Task 5: Build the Monitor BI View and Navigation

**Files:**
- Create: `web/src/MonitorPage.jsx`
- Create: `web/src/monitorDashboardViewModel.js`
- Create: `web/src/monitorDashboardViewModel.test.mjs`
- Create: `web/src/constants.test.mjs`
- Modify: `web/src/api.js`
- Modify: `web/src/constants.js`
- Modify: `web/src/App.jsx`
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/monitorDashboardViewModel.test.mjs`

**Interfaces:**
- Consumes: `GET /api/monitoring` and the existing active workspace/access state.
- Produces: `loadMonitoringDashboard({scope, workspaceId, from, to})`, `monitorDashboardViewModel(payload)`, and a lazy-loaded `MonitorPage`.

- [ ] **Step 1: Write failing frontend view-model tests**

```javascript
test("monitor view model keeps unavailable cost and pending ROI distinct", () => {
  const view = monitorDashboardViewModel({
    summary: {
      calls: { observed: 3, succeeded: 3, failed: 0, unknown: 0 },
      tokens: { input: 80, output: 20, total: 100, known_runs: 1, unknown_runs: 2 },
      cost: { status: "unavailable", amount: null, currency: "USD" },
      quality: { evidence_coverage_pct: null, audited_runs: 1, rework_runs: 0, evaluator_coverage_pct: null },
      roi: { status: "pending_verification", verified_value: null, model_cost: null, evaluator_cost: null, roi_pct: null },
    },
    models: [], routes: [], series: { daily: [] }, members: [],
  });

  assert.equal(view.cards.cost.value, "未记录");
  assert.equal(view.cards.roi.badge, "待验证");
  assert.equal(view.modelRows.length, 0);
});
```

- [ ] **Step 2: Run the failing frontend test**

Run: `node --test web/src/monitorDashboardViewModel.test.mjs`

Expected: FAIL because `monitorDashboardViewModel.js` does not exist.

- [ ] **Step 3: Implement API client and view model**

```javascript
export async function loadMonitoringDashboard({ scope = "current", workspaceId, from, to }) {
  const params = new URLSearchParams({ scope, workspace_id: workspaceId, from, to });
  return request(`/api/monitoring?${params.toString()}`);
}
```

Build safe display values from the API response only. Represent all unknown/unavailable/pending values as explicit UI states. Do not calculate monetary costs or ROI in the browser.

- [ ] **Step 4: Run frontend view-model tests**

Run: `node --test web/src/monitorDashboardViewModel.test.mjs web/src/monitoringViewModel.test.mjs`

Expected: PASS.

- [ ] **Step 5: Add a failing navigation visibility test**

```javascript
test("monitor navigation is visible only to a workspace owner", () => {
  assert.ok(visibleNavItems({ allowed: true, role: "owner" }).some((item) => item.id === "monitor"));
  assert.ok(!visibleNavItems({ allowed: true, role: "editor" }).some((item) => item.id === "monitor"));
});
```

- [ ] **Step 6: Run the failing navigation test**

Run: `node --test web/src/constants.test.mjs`

Expected: FAIL because `monitor` is not a navigation item.

- [ ] **Step 7: Implement the fixed-layout Monitor page**

```jsx
<section className="monitor-page" data-testid="monitor-page">
  <header className="monitor-toolbar">
    <MonitorScopeControls scope={scope} window={windowValue} onChange={reload} />
  </header>
  <div className="monitor-kpis">
    <MetricCard metric={view.cards.calls} />
    <MetricCard metric={view.cards.tokens} />
    <MetricCard metric={view.cards.cost} />
    <MetricCard metric={view.cards.quality} />
    <MetricCard metric={view.cards.roi} />
  </div>
  <div className="monitor-grid">
    <TrendChart series={view.dailySeries} />
    <ModelConsumptionChart rows={view.modelRows} />
    <RouteDistribution rows={view.routeRows} />
    <OptimizationOpportunity value={view.opportunity} />
  </div>
</section>
```

Add `monitor` to `NAV_ITEMS`, protect it with the same server-backed owner condition as governance, and render it through `WorkbenchMain`. Use a `MonitorPage` request guard/AbortController so stale workspace responses cannot replace a newer scope. Chart frames must reserve their height for loading, empty, error, and ready states.

- [ ] **Step 8: Add CSS geometry and interaction states**

```css
.monitor-grid { display:grid; grid-template-columns:minmax(0,1.6fr) minmax(280px,1fr); gap:16px; }
.monitor-chart-frame { min-height:248px; height:248px; overflow:hidden; }
.monitor-data-layer { transition:opacity 160ms ease, transform 160ms ease; }
.monitor-data-layer[data-loading="true"] { opacity:.58; transform:translateY(2px); }
```

Provide `@media` rules that collapse to one column without horizontal page overflow. Do not animate height, width, grid tracks, or font size.

- [ ] **Step 9: Run frontend tests and production build**

Run: `node --test web/src/monitorDashboardViewModel.test.mjs web/src/constants.test.mjs web/src/governanceViewModel.test.mjs; npm --prefix web run build`

Expected: all tests PASS and Vite exits 0.

- [ ] **Step 10: Commit**

```powershell
git add web/src/MonitorPage.jsx web/src/monitorDashboardViewModel.js web/src/monitorDashboardViewModel.test.mjs web/src/api.js web/src/constants.js web/src/constants.test.mjs web/src/App.jsx web/src/components.jsx web/src/styles.css
git commit -m "feat: add owner monitor BI page"
```

### Task 6: End-to-End Evidence, Browser QA, and Candidate Deployment

**Files:**
- Modify: `README.md: monitoring and model-routing documentation`
- Modify: `backend/.env.example: model route allowlist and evaluation gate examples`
- Test: `tests/test_monitoring_dashboard_api.py`
- Test: `web/src/monitorDashboardViewModel.test.mjs`
- Create: `docs/validation/2026-07-22-monitor-bi-evidence.md`

**Interfaces:**
- Consumes: deployed candidate backend/frontend, one owner account, and generated text-model runs.
- Produces: a redacted evidence record containing endpoint response checks, browser screenshots, APIM/Foundry trace references, and deployed revision identifiers.

- [ ] **Step 1: Write failing integration assertions for real route reconciliation**

```python
def test_monitor_dashboard_reconciles_model_and_route_totals_with_run_records(client, seeded_owner_runs) -> None:
    response = client.get("/api/monitoring?scope=current&workspace_id=ws-owner&from=2026-07-01T00:00:00Z&to=2026-08-01T00:00:00Z")
    assert response.status_code == 200
    body = response.json()
    assert sum(row["calls"] for row in body["models"]) <= body["summary"]["calls"]["observed"]
    assert body["coverage"]["governed_text_calls"] >= 0
```

- [ ] **Step 2: Run the integration assertion before final fixes**

Run: `python -m pytest tests/test_monitoring_dashboard_api.py -q`

Expected: PASS after Tasks 1-5; any mismatch is fixed before proceeding.

- [ ] **Step 3: Run full automated verification**

Run:

```powershell
python -m pytest -q
node --test web/src/*.test.mjs
npm --prefix web run build
```

Expected: all existing and new tests pass; build produces `web/dist`.

- [ ] **Step 4: Build zero-traffic candidate revisions**

Use the existing `az acr build` and Container Apps revision workflow. Deploy backend and web candidate revisions with zero traffic first. Do not change production traffic during this step.

- [ ] **Step 5: Run signed-in browser smoke**

Use Playwright or the signed-in app browser to verify, at desktop and mobile widths:

1. Owner sees `Monitor`; editor cannot access it through navigation or direct API.
2. Current workspace and portfolio scope load without layout shift.
3. Date range, refresh, loading, empty, denied, and partial-source states retain chart-frame geometry.
4. A new text call appears in model/route consumption and links to a real run.
5. Missing price/outcome evidence displays unavailable/pending, not a number.
6. Existing workspaces, data, runs, conversation, artifacts, and settings have no blank page regressions.

- [ ] **Step 6: Record redacted evidence and request promotion**

Document candidate URL, revision names, timestamp, endpoint response shapes, screenshot paths, APIM/Foundry trace correlation IDs, and test output in `docs/validation/2026-07-22-monitor-bi-evidence.md`. Exclude access tokens, connection strings, actor identifiers, prompts, and raw telemetry.

- [ ] **Step 7: Commit**

```powershell
git add README.md backend/.env.example docs/validation/2026-07-22-monitor-bi-evidence.md tests/test_monitoring_dashboard_api.py
git commit -m "docs: verify monitor BI candidate"
```
