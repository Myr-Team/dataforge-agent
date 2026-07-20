# Truthful ROI Evidence and Scenario Measurement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a production-safe Cost and Value surface that separates observed agent cost, verified business outcomes, and user-authored ROI scenario estimates.

**Architecture:** Existing run telemetry and `outcome_store` remain authoritative for measured evidence. A new workspace-scoped scenario ledger stores bounded assumptions and computed estimates independently. A typed public-event projector becomes the only way persisted artifacts and SSE events cross the backend boundary; the UI reads only these public projections.

**Tech Stack:** FastAPI, Pydantic, Azure Blob-backed workspace state, React/Vite, Node test runner, pytest, Azure Container Apps.

## Global Constraints

- Do not change Easy Auth, Entra tenant configuration, Key Vault permissions, or authentication routes.
- Do not claim that Azure AI Foundry provides a connected native ROI source unless a real supported provider is installed and verified.
- Never create a monetary result from absent price or outcome evidence; use explicit `not_configured`, `incomplete`, or `not_recorded` states.
- Scenario records must remain `estimated` and cannot change outcome verification or realized ROI.
- Persisted records, artifacts, API payloads, trace payloads, and every SSE event must use typed allowlisted projections; arbitrary nested metadata must be dropped.
- Preserve existing user changes and untracked runtime workspace folders. Never use `git reset`, `git checkout --`, or `git add -A`.
- Candidate Container Apps revisions receive zero traffic until signed-in browser validation passes. Production traffic changes require explicit approval.

---

### Task 1: Replace generic metadata filtering with typed public event projections

**Files:**
- Modify: `backend/evidence_bundle.py:566-717`
- Modify: `backend/orchestrator.py:980-1051`
- Modify: `tests/test_evidence_bundle.py`
- Modify: `tests/test_conversation_routes.py`

**Interfaces:**
- Produces `public_conversation_event(event: str, data: Any, conversation_id: str | None) -> dict[str, Any]`.
- `orchestrator._frame()` sends only the returned projection to `trace_event`, `record_event`, and `sse`.
- Permitted non-final content is limited to user-facing answer text, bounded identifiers, route enums, bounded progress/tool status, structured clarification question/options, and generic error codes.

- [ ] **Step 1: Write failing metadata and SSE tests**

```python
def test_public_event_projection_drops_unknown_nested_conversation_payloads() -> None:
    payload = {
        "opaque": {"anything": {"prompt": "secret", "token": "Bearer hidden"}},
        "conversation_route": {"mode": "followup", "reason": "raw rationale"},
    }
    assert public_conversation_event("followup", payload, "conv-1") == {
        "conversation_route": {"mode": "followup", "reason": "Follow-up", "evidence_required": False}
    }

def test_frame_projects_clarify_and_error_payloads_before_persistence(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(orchestrator, "record_event", lambda *_args: captured.append(_args[2]))
    orchestrator._frame("error", {"message": "provider secret", "error": "raw", "retryable": True}, "conv-1")
    assert captured == [{"code": "request_failed", "retryable": True}]
```

- [ ] **Step 2: Run focused tests to verify the current implementation fails**

Run: `python -m pytest tests/test_evidence_bundle.py tests/test_conversation_routes.py -q`

Expected: failure because unknown metadata survives and non-final `_frame()` events bypass the public projection.

- [ ] **Step 3: Implement allowlisted event schemas**

```python
_PUBLIC_EVENT_PROJECTORS = {
    "answer_delta": _project_answer_delta,
    "final": _project_final,
    "model_response": _project_model_response,
    "followup": _project_followup,
    "clarify": _project_clarify,
    "route": _project_route,
    "plan": _project_route,
    "progress": _project_progress,
    "role_change": _project_role_change,
    "tool_call": _project_tool_call,
    "tool_result": _project_tool_result,
    "audit": _project_audit,
    "revised_verdict": _project_revised_verdict,
    "error": _project_error,
}

def public_conversation_event(event: str, data: Any, conversation_id: str | None) -> dict[str, Any]:
    projector = _PUBLIC_EVENT_PROJECTORS.get(str(event))
    return projector(data, conversation_id) if projector else {}
```

Use fixed key sets inside each projector. `answer_delta` allows only a bounded
`delta`; `clarify` allows `question` and at most five `{id, label}` options;
`error` maps all backend/provider detail to a bounded public code and optional
`retryable`; `final` delegates to `public_artifact_projection`. Replace
recursive pass-through in `sanitize_conversation_metadata()` with a top-level
allowlist for persisted artifact fields and its existing capability-pack projection.

- [ ] **Step 4: Route all SSE events through the projector**

```python
def _frame(event: str, data: Any, conversation_id: str | None = None) -> str:
    client_data = public_conversation_event(event, data, conversation_id)
    if event != "answer_delta" or os.environ.get("DF_TRACE_DELTAS") == "1":
        trace_event(event, client_data, conversation_id)
    record_event(conversation_id, event, client_data)
    return sse(event, client_data)
```

Keep the existing best-effort persistence failure handling around `record_event`.

- [ ] **Step 5: Run focused tests and commit**

Run: `python -m pytest tests/test_evidence_bundle.py tests/test_conversation_routes.py -q`

Expected: PASS, including arbitrary nested metadata and `followup`,
`model_response`, `clarify`, `error`, and `final` event coverage.

```bash
git add backend/evidence_bundle.py backend/orchestrator.py tests/test_evidence_bundle.py tests/test_conversation_routes.py
git commit -m "fix: allowlist persisted and streamed conversation metadata"
```

### Task 2: Make the Foundry integration state truthful and preserve measured-cost semantics

**Files:**
- Modify: `backend/foundry_roi.py:106-430`
- Modify: `backend/control_plane.py:1705-1724, 1896-2006`
- Modify: `backend/roi_service.py:89-293`
- Modify: `tests/test_foundry_roi.py`
- Modify: `tests/test_roi_service.py`
- Modify: `tests/test_governance_roi_summary.py`

**Interfaces:**
- `workspace_roi_snapshot()` returns `cost_evidence`, `outcome_evidence`, and `foundry_integration` rather than a misleading `foundry_roi` card.
- `foundry_integration.state` is `not_connected`, `available`, or `verified`; `not_connected` is a normal state, not an error.
- `RoiSnapshot.status` reflects only local evidence and cannot be promoted by a provider response.

- [ ] **Step 1: Write failing truthfulness tests**

```python
def test_missing_provider_is_a_normal_not_connected_integration_state(monkeypatch) -> None:
    monkeypatch.delenv("DF_FOUNDRY_ROI_PROVIDER", raising=False)
    snapshot = workspace_roi_snapshot("ws", WINDOW["from"], WINDOW["to"])
    assert snapshot["foundry_integration"]["state"] == "not_connected"
    assert snapshot["foundry_integration"]["official_source"] is False

def test_missing_price_never_returns_zero_monetary_cost() -> None:
    snapshot = build_roi_snapshot("ws", WINDOW, runs=[run_without_price()], outcomes=[])
    assert snapshot["cost"]["status"] == "incomplete"
    assert snapshot["cost"]["total"] is None
```

- [ ] **Step 2: Run focused tests to verify failure**

Run: `python -m pytest tests/test_foundry_roi.py tests/test_roi_service.py tests/test_governance_roi_summary.py -q`

Expected: failure because the current public contract exposes a provider-style
ROI object rather than the new integration state.

- [ ] **Step 3: Simplify public contracts without deleting the provider boundary**

```python
def public_foundry_integration() -> dict[str, Any]:
    status = discover_foundry_roi()
    return {
        "state": {"not_configured": "not_connected", "configured_unverified": "available"}.get(status.state, status.state),
        "official_source": status.state == "connected",
        "provider_version": status.provider_version,
        "observed_at": status.observed_at.isoformat(),
        "reason_code": _public_foundry_reason(status.state),
    }
```

Retain `FoundryRoiProvider` internally for a future supported adapter. Remove
provider amount/difference fields from the normal client payload unless the
provider reaches the verified state. Keep existing cost rules: no matching
price means `total: None`; saved hours remain non-monetized until a recorded
conversion method exists.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m pytest tests/test_foundry_roi.py tests/test_roi_service.py tests/test_governance_roi_summary.py -q`

Expected: PASS; no feature flag or discovery-only proof can claim a connected
official source or upgrade local evidence.

```bash
git add backend/foundry_roi.py backend/control_plane.py backend/roi_service.py tests/test_foundry_roi.py tests/test_roi_service.py tests/test_governance_roi_summary.py
git commit -m "fix: separate local ROI evidence from Foundry integration state"
```

### Task 3: Add a versioned ROI scenario ledger

**Files:**
- Create: `backend/roi_scenario_store.py`
- Modify: `backend/workspace_authz.py:28-52`
- Modify: `backend/control_plane.py:243-301, 1705-1724`
- Create: `tests/test_roi_scenarios.py`
- Modify: `tests/test_workspace_roles.py`

**Interfaces:**
- `create_roi_scenario(workspace_id, payload, actor, previous_id=None) -> dict[str, Any]`
- `list_roi_scenarios(workspace_id) -> list[dict[str, Any]]`
- `scenario_projection(workspace_id, scenario) -> dict[str, Any]`
- `GET /api/workspaces/{id}/governance/scenarios`
- `POST /api/workspaces/{id}/governance/scenarios`
- Authorization actions: `roi.scenario.read` for workspace readers and `roi.scenario.write` for owners/admins/editors.

- [ ] **Step 1: Write failing store and API tests**

```python
def test_scenario_is_persisted_as_estimated_and_versioned(tmp_path, monkeypatch) -> None:
    configure_scenario_store(tmp_path, monkeypatch)
    first = create_roi_scenario("ws", scenario_payload(title="Pilot A"), actor())
    second = create_roi_scenario("ws", scenario_payload(title="Pilot A"), actor(), previous_id=first["scenario_id"])
    assert first["status"] == second["status"] == "estimated"
    assert second["revision"] == 2
    assert list_roi_scenarios("ws")[-1]["scenario_id"] == second["scenario_id"]

def test_viewer_cannot_write_but_can_read_scenarios(client) -> None:
    assert client.get("/api/workspaces/ws/governance/scenarios", headers=viewer_headers()).status_code == 200
    assert client.post("/api/workspaces/ws/governance/scenarios", json=scenario_payload(), headers=viewer_headers()).status_code == 403
```

- [ ] **Step 2: Run the scenario tests to verify failure**

Run: `python -m pytest tests/test_roi_scenarios.py tests/test_workspace_roles.py -q`

Expected: FAIL because the store, actions, and routes do not exist.

- [ ] **Step 3: Implement bounded scenario persistence and formula**

```python
def scenario_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    revenue = money(payload.get("expected_revenue"))
    avoided_cost = money(payload.get("expected_avoided_cost"))
    pilot_cost = money(payload.get("pilot_cost"))
    value = revenue + avoided_cost
    return {
        "status": "estimated",
        "currency": payload["currency"],
        "estimated_business_value": value,
        "pilot_cost": pilot_cost,
        "net_value": value - pilot_cost,
        "roi_ratio": None if pilot_cost == 0 else round((value - pilot_cost) / pilot_cost, 6),
        "saved_hours": optional_nonnegative_number(payload.get("expected_saved_hours")),
        "formula_version": "roi-scenario-v1",
    }
```

Accept only title, currency, expected revenue, expected avoided cost, pilot
cost, optional saved hours, time horizon days, linked run ID, and evidence
revision. Validate finite nonnegative values, one ISO currency, title/input
limits, and same-workspace source references. Store a new immutable revision
for every recalculation. Use Blob and local fallback persistence patterns from
`outcome_store.py`; never persist raw request identity beyond `public_actor`.

- [ ] **Step 4: Add authenticated routes and audit events**

```python
@router.post("/api/workspaces/{workspace_id}/governance/scenarios")
async def workspace_scenario_create(workspace_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_sensitive_workspace_action(workspace_id, request, "roi.scenario.write")
    _audit_required(request, workspace_id, "roi.scenario.write", "roi_scenario", "pending")
    scenario = await _call(create_roi_scenario, workspace_id, body, actor_from_request(request))
    return {"workspace_id": workspace_id, "scenario": scenario_projection(workspace_id, scenario)}
```

The GET route requires `roi.scenario.read`. Add the new actions to the existing
read/editor sets, not to a new role system.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_roi_scenarios.py tests/test_workspace_roles.py tests/test_outcome_roi.py -q`

Expected: PASS, including persistence, version history, formula validation,
source ownership, public projection, and role enforcement.

```bash
git add backend/roi_scenario_store.py backend/workspace_authz.py backend/control_plane.py tests/test_roi_scenarios.py tests/test_workspace_roles.py tests/test_outcome_roi.py
git commit -m "feat: add versioned ROI scenario measurement"
```

### Task 4: Expose a single Cost and Value API contract

**Files:**
- Modify: `backend/control_plane.py:198-206, 1705-1724`
- Modify: `backend/roi_service.py:194-293`
- Modify: `tests/test_governance_roi_summary.py`
- Create: `tests/test_cost_value_api.py`

**Interfaces:**
- `GET /api/workspaces/{id}/governance/cost-value?from=<UTC>&to=<UTC>`
- Returns `{window, cost_evidence, outcome_evidence, realized_roi, scenarios, foundry_integration, generated_at}`.
- `realized_roi.status` is `verified`, `not_recorded`, `incomplete`, or `not_monetized`; it has no numeric value outside `verified`.

- [ ] **Step 1: Write API contract tests**

```python
def test_cost_value_keeps_outcome_and_scenario_evidence_separate(client) -> None:
    response = client.get(f"/api/workspaces/ws/governance/cost-value?from={FROM}&to={TO}", headers=owner_headers())
    body = response.json()
    assert body["cost_evidence"]["status"] in {"complete", "incomplete", "not_configured"}
    assert body["realized_roi"]["status"] == "not_recorded"
    assert body["scenarios"][0]["status"] == "estimated"
    assert body["foundry_integration"]["state"] == "not_connected"
```

- [ ] **Step 2: Run the API test to verify failure**

Run: `python -m pytest tests/test_cost_value_api.py -q`

Expected: FAIL because the endpoint and top-level contract do not exist.

- [ ] **Step 3: Implement the aggregate without client-side joins**

```python
def workspace_cost_value(workspace_id: str, from_value: str, to_value: str) -> dict[str, Any]:
    snapshot = workspace_roi_snapshot(workspace_id, from_value, to_value)
    return {
        "window": snapshot["window"],
        "cost_evidence": public_cost_evidence(snapshot),
        "outcome_evidence": public_outcome_evidence(workspace_id, snapshot),
        "realized_roi": public_realized_roi(snapshot),
        "scenarios": public_roi_scenarios(workspace_id),
        "foundry_integration": public_foundry_integration(),
        "generated_at": snapshot["generated_at"],
    }
```

Make `/governance/roi` either a backward-compatible alias that returns the new
shape or update all first-party callers in the same task. Do not return raw
outcome notes, full actor identities, provider responses, or internal
verification events.

- [ ] **Step 4: Run API and ROI tests, then commit**

Run: `python -m pytest tests/test_cost_value_api.py tests/test_governance_roi_summary.py tests/test_roi_service.py -q`

Expected: PASS; a missing outcome or incompatible currency has no realized
monetary ROI and provides a bounded explanation state.

```bash
git add backend/control_plane.py backend/roi_service.py tests/test_cost_value_api.py tests/test_governance_roi_summary.py tests/test_roi_service.py
git commit -m "feat: expose cost and value evidence contract"
```

### Task 5: Build the Cost and Value interface with fixed layout states

**Files:**
- Create: `web/src/costValueViewModel.js`
- Create: `web/src/costValueViewModel.test.mjs`
- Modify: `web/src/api.js:336-366`
- Modify: `web/src/components.jsx:2432-2555, 3026-3055`
- Modify: `web/src/styles.css:3757-3811`
- Modify: `web/src/components.test.mjs`

**Interfaces:**
- `loadWorkspaceCostValue(workspaceId, {from, to})` fetches the aggregate endpoint.
- `costValueViewModel(payload)` returns display-safe status, labels, source counts, and numeric formatting.
- `CostValuePanel` receives `{data, loading, error, onRetry, onCreateScenario}` and does not derive ROI client-side.

- [ ] **Step 1: Write frontend view-model and rendering tests**

```javascript
test("scenario estimates cannot render as verified ROI", () => {
  const view = costValueViewModel({
    realized_roi: { status: "not_recorded" },
    scenarios: [{ status: "estimated", result: { roi_ratio: 1.25, currency: "CNY" } }],
    foundry_integration: { state: "not_connected" },
  });
  assert.equal(view.realized.label, "未记录");
  assert.equal(view.scenarios[0].badge, "情景测算");
  assert.equal(view.foundry.label, "未接入官方 ROI 数据源");
});
```

- [ ] **Step 2: Run frontend tests to verify failure**

Run: `node --test web/src/costValueViewModel.test.mjs web/src/components.test.mjs`

Expected: FAIL because the Cost and Value view model, API call, and panel do
not exist.

- [ ] **Step 3: Implement data-backed UI states and scenario submission**

```javascript
export async function createWorkspaceRoiScenario(workspaceId, payload) {
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/governance/scenarios`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
```

Replace the two-card local/provider ROI band with an outcome-first Cost and
Value area: observed cost, outcome evidence, scenario list/form, and a small
trace-source status. Show `未记录`, `价格未配置`, `数据不完整`, and `估算` as
first-class states. Do not show a healthy aggregate when source evidence is
missing. Use fixed grid tracks with `minmax(0, ...)`, stable button/input
heights, wrapping labels, and a one-column breakpoint below 1180 CSS pixels;
the browser validation determines the actual desktop viewport before final CSS
tuning.

- [ ] **Step 4: Run frontend checks and commit**

Run: `node --test web/src/costValueViewModel.test.mjs web/src/components.test.mjs`

Expected: PASS.

Run: `npm --prefix web run build`

Expected: Vite production build succeeds with no unresolved imports.

```bash
git add web/src/costValueViewModel.js web/src/costValueViewModel.test.mjs web/src/api.js web/src/components.jsx web/src/styles.css web/src/components.test.mjs
git commit -m "feat: add cost and value governance surface"
```

### Task 6: Full verification, candidate deployment, and production gate

**Files:**
- Create: `docs/validation/2026-07-20-cost-value-candidate.md`
- Modify only if deployment configuration requires the new backend build; do not edit auth configuration.

**Interfaces:**
- The validation document records commands, candidate revision URL, measured CSS viewport, signed-in test account role, timestamp, API result identifiers, and pass/fail evidence. It contains no tokens, credentials, full prompts, or raw telemetry payloads.

- [ ] **Step 1: Run the complete automated suite**

Run: `python -m pytest -q`

Expected: PASS. Investigate every failure before a build; do not waive the
existing capability-pack, authorization, tracing, outcome, or conversation
tests.

Run: `node --test web/src/*.test.mjs`

Expected: PASS.

Run: `npm --prefix web run build`

Expected: PASS.

- [ ] **Step 2: Build and deploy zero-traffic candidates**

Build backend and frontend images through the existing Azure Container Registry
workflow, update each Container App with a labeled candidate revision, and set
candidate traffic to `0`. Record only image tags and revision names in the
validation document.

- [ ] **Step 3: Validate signed-in candidate flows in the browser**

Measure `window.innerWidth` and `window.innerHeight` on the user's browser;
do not assume 1440x900. Validate at that viewport, at 1280 CSS pixels, and at
the narrow breakpoint:

```text
1. Open Cost and Value as owner: cost is trace-linked and missing evidence is honest.
2. Create a scenario: it is estimated, versioned, and survives refresh.
3. Record an outcome and verify it with an independent authorized actor.
4. Confirm realized ROI remains absent until compatible verified evidence exists.
5. Open conversation and run an answer, follow-up, clarify, and controlled error path.
6. Confirm browser-visible SSE frames contain no arbitrary metadata, credentials, raw provider error, or hidden rationale.
```

- [ ] **Step 4: Commit validation evidence and request release approval**

```bash
git add docs/validation/2026-07-20-cost-value-candidate.md
git commit -m "docs: record cost and value candidate validation"
```

Report the candidate evidence and wait for explicit confirmation before
assigning production traffic. After approval, promote the already validated
revision, run one final signed-in smoke test, and record the production revision
name in the validation document.
