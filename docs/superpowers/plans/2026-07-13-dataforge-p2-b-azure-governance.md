# DataForge P2-B Azure Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove Azure trace delivery, correlate verified business outcomes with observed agent cost, add a truthful Foundry ROI adapter, and complete Entra invitation and immutable audit lifecycle.

**Architecture:** Extend existing outcome, identity, workspace-role, and observability modules instead of replacing them. Add focused Azure Monitor query and Foundry ROI adapters behind explicit configuration. Keep DataForge's append-only outcome ledger authoritative and make provider data a reconciled projection.

**Tech Stack:** Python 3.12, FastAPI, Azure Monitor Query, Application Insights/OpenTelemetry, Microsoft Graph, Azure Identity, React/Vite, pytest.

## Global Constraints

- Do not change Easy Auth configuration from application code.
- Never log or return access tokens, raw claims, prompts, evidence content, connector credentials, or actor email in telemetry.
- Configuration is not proof of trace delivery or native ROI availability.
- `estimated`, `measured`, and `verified` ROI states remain distinct.
- Client payloads cannot directly create verified outcomes or active memberships.
- All object routes resolve stored workspace ownership before authorization.
- Unknown token, price, export, and outcome values remain unknown, not zero.

---

### Task 1: Azure Monitor Delivery-Aware Status

**Files:**
- Create: `backend/azure_monitor_client.py`
- Modify: `backend/tracing.py`
- Modify: `backend/observability.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/requirements.txt`
- Create: `tests/test_azure_monitor_status.py`
- Modify: `tests/test_tracing_telemetry.py`

**Interfaces:**
- Produces: `TraceDeliveryStatus`, `query_trace_delivery(run_id)`, `build_transaction_link(correlation)`, and `GET /api/workspaces/{id}/governance/trace-status`.
- Consumes: local span-export counters, App Insights resource/application IDs, Azure Monitor Logs query results, managed identity.

- [ ] **Step 1: Write failing delivery-state tests**

```python
def test_configured_exporter_without_observed_trace_is_partial():
    status = build_trace_status(configured=True, local_emit_at=now(), remote_trace=None)
    assert status.state == "partial"
    assert status.last_export_confirmed_at is None


def test_remote_trace_proves_connected_state_without_exposing_actor():
    status = build_trace_status(configured=True, local_emit_at=now(), remote_trace=remote_trace())
    assert status.state == "connected"
    assert "@" not in json.dumps(status.model_dump())
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_azure_monitor_status.py tests/test_tracing_telemetry.py -q`

Expected: FAIL because remote delivery is not queried.

- [ ] **Step 3: Implement bounded Azure Monitor query adapter**

```python
class TraceDeliveryStatus(BaseModel):
    state: Literal["connected", "partial", "not_configured", "unavailable"]
    local_emit_at: datetime | None
    last_export_confirmed_at: datetime | None
    correlation_id: str | None
    transaction_url: str | None
    error_type: str | None
```

Query only hashed correlation/run fields in a bounded time range. Cache successful status briefly to avoid loading Application Insights on every settings render. Redact query exceptions to type/status only.

- [ ] **Step 4: Add live export counters and API**

Record last local span emission and exporter success/failure callback when supported. API authorization requires `workspace.read`; run-specific queries verify run ownership.

- [ ] **Step 5: Verify tests and import smoke**

Run: `python -m pytest tests/test_azure_monitor_status.py tests/test_tracing_telemetry.py -q`

Run: `python -m backend.import_smoke`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/azure_monitor_client.py backend/tracing.py backend/observability.py backend/control_plane.py backend/requirements.txt tests/test_azure_monitor_status.py tests/test_tracing_telemetry.py
git commit -m "feat: prove Azure Monitor trace delivery"
```

---

### Task 2: ROI Snapshot And Per-Member Chargeback

**Files:**
- Create: `backend/roi_service.py`
- Modify: `backend/outcome_store.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/run_store.py`
- Create: `tests/test_roi_service.py`
- Modify: `tests/test_outcome_roi.py`
- Modify: `tests/test_actor_audit_usage.py`

**Interfaces:**
- Produces: `build_roi_snapshot(workspace_id, window, prices)`, `member_chargeback(workspace_id, window)`, and source-linked ROI states.
- Consumes: observed run usage, model price configuration with effective time, outcome events, experiment lineage, verification events.

- [ ] **Step 1: Write failing ROI-state and unknown-cost tests**

```python
def test_usage_without_outcome_is_estimated_not_measured():
    snapshot = build_roi_snapshot("ws", runs=[observed_usage_run()], outcomes=[])
    assert snapshot.status == "estimated"
    assert snapshot.business_value is None


def test_missing_model_price_does_not_become_zero_cost():
    snapshot = build_roi_snapshot("ws", runs=[unknown_model_run()], outcomes=[])
    assert snapshot.cost.total is None
    assert snapshot.cost.status == "partial"


def test_verified_state_requires_source_and_reviewer_event():
    snapshot = build_roi_snapshot("ws", runs=[run()], outcomes=[verified_source_linked_outcome()])
    assert snapshot.status == "verified"
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_roi_service.py tests/test_outcome_roi.py tests/test_actor_audit_usage.py -q`

Expected: FAIL because the normalized snapshot service does not exist.

- [ ] **Step 3: Implement normalized ROI contracts**

```python
class RoiSnapshot(BaseModel):
    status: Literal["estimated", "measured", "verified"]
    window: TimeWindow
    usage: UsageSummary
    cost: CostSummary
    time_value: TimeValueSummary
    business_value: BusinessValueSummary | None
    outcome_event_ids: list[str]
    assumptions: list[Assumption]
    generated_at: datetime
```

Price configuration is versioned and records currency, unit, effective dates, and source. Do not scrape or invent live prices inside request handling.

- [ ] **Step 4: Implement per-member chargeback**

Aggregate only trusted run/message/task actor records. Group by actor ID, model, task kind, and time window. Display email/name only from current trusted workspace membership data, not telemetry attributes.

- [ ] **Step 5: Add API and verify**

Add `GET /api/workspaces/{id}/governance/roi?from=&to=` and `GET /api/workspaces/{id}/governance/chargeback?from=&to=`. Require owner/admin for member comparison and workspace.read for aggregate ROI.

Run: `python -m pytest tests/test_roi_service.py tests/test_outcome_roi.py tests/test_actor_audit_usage.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/roi_service.py backend/outcome_store.py backend/control_plane.py backend/run_store.py tests/test_roi_service.py tests/test_outcome_roi.py tests/test_actor_audit_usage.py
git commit -m "feat: calculate source-linked workspace ROI"
```

---

### Task 3: Foundry Native ROI Adapter

**Files:**
- Create: `backend/foundry_roi.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/dependency_health.py`
- Create: `tests/test_foundry_roi.py`
- Modify: `tests/test_governance_roi_summary.py`

**Interfaces:**
- Produces: `FoundryRoiStatus`, `discover_foundry_roi()`, `read_foundry_roi(window)`, and reconciliation fields in governance ROI.
- Consumes: configured Foundry project/agent identifiers, provider API response, local ROI snapshot.

- [ ] **Step 1: Write failing truthfulness tests**

```python
def test_environment_flag_alone_does_not_mean_configured():
    status = discover_foundry_roi(env_enabled=True, provider=None)
    assert status.state != "connected"


def test_provider_values_are_reconciled_not_substituted():
    result = reconcile_roi(local=local_snapshot(100), provider=provider_snapshot(90))
    assert result.local.business_value.amount == 100
    assert result.provider.business_value.amount == 90
    assert result.difference.amount == -10
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_foundry_roi.py tests/test_governance_roi_summary.py -q`

Expected: FAIL because provider discovery and reconciliation do not exist.

- [ ] **Step 3: Implement optional provider boundary**

```python
class FoundryRoiProvider(Protocol):
    def discover(self) -> FoundryRoiStatus: ...
    def read(self, window: TimeWindow) -> ProviderRoiSnapshot: ...
```

Return `not_configured` unless the target agent/project and ROI surface are discoverable. Return `unavailable` on provider failure. Local ROI remains usable in both cases.

- [ ] **Step 4: Add reconciliation and health status**

Expose local/provider observation timestamps, currencies/units, mapped run/outcome identifiers, and differences. Do not combine values when units or windows differ.

- [ ] **Step 5: Verify tests**

Run: `python -m pytest tests/test_foundry_roi.py tests/test_governance_roi_summary.py -q`

Expected: PASS with fake provider; default environment remains `not_configured`.

- [ ] **Step 6: Commit**

```powershell
git add backend/foundry_roi.py backend/control_plane.py backend/dependency_health.py tests/test_foundry_roi.py tests/test_governance_roi_summary.py
git commit -m "feat: add truthful Foundry ROI adapter"
```

---

### Task 4: Entra Invitation Lifecycle

**Files:**
- Modify: `backend/graph_client.py`
- Modify: `backend/identity.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/workspace_authz.py`
- Create: `backend/invitation_store.py`
- Modify: `tests/test_entra_member_invites.py`
- Modify: `tests/test_workspace_roles.py`

**Interfaces:**
- Produces: append-only invitation records and trusted activation by object ID plus tenant ID.
- Consumes: Graph search/invitation responses and Easy Auth actor claims.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_pending_invite_does_not_grant_access():
    invite = create_invitation("ws", external_user(), owner())
    assert authorize_invitation(invite, action="workspace.read") is False


def test_acceptance_requires_matching_oid_and_tenant():
    invite = accepted_invitation(oid="oid-1", tenant="tenant-1")
    assert activate(invite, actor(oid="oid-2", tenant="tenant-1")) is False
    assert activate(invite, actor(oid="oid-1", tenant="tenant-1")) is True
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_entra_member_invites.py tests/test_workspace_roles.py -q`

Expected: FAIL on missing invitation store/lifecycle states.

- [ ] **Step 3: Implement invitation records and transitions**

States are `pending`, `accepted`, `expired`, `failed`, and `revoked`. Transitions are append-only events. Activation creates or updates membership only from trusted Easy Auth OID/tenant claims matching the accepted invitation.

- [ ] **Step 4: Add Graph operations without broad directory assumptions**

Directory search reports permission errors clearly and supports exact email invite when search permission is unavailable. Invitation responses store provider IDs/status only; access tokens and raw Graph payloads are not persisted.

- [ ] **Step 5: Verify tests**

Run: `python -m pytest tests/test_entra_member_invites.py tests/test_workspace_roles.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/graph_client.py backend/identity.py backend/control_plane.py backend/workspace_authz.py backend/invitation_store.py tests/test_entra_member_invites.py tests/test_workspace_roles.py
git commit -m "feat: complete Entra invitation lifecycle"
```

---

### Task 5: Immutable Audit Ledger

**Files:**
- Create: `backend/audit_store.py`
- Modify: `backend/app.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/data_workbench.py`
- Modify: `backend/task_store.py`
- Create: `tests/test_audit_store.py`
- Modify: `tests/test_actor_audit_usage.py`

**Interfaces:**
- Produces: `record_audit_event`, `list_audit_events`, and workspace audit API.
- Consumes: trusted actor, authorized action, resource identity, result, task/run/request correlation.

- [ ] **Step 1: Write failing redaction and append-only tests**

```python
def test_audit_event_redacts_content_credentials_and_email():
    event = record_audit_event(actor(), "connector.sync", resource(), metadata_with_secrets())
    text = json.dumps(event)
    assert "Password=" not in text
    assert "@contoso" not in text
    assert "raw_prompt" not in text


def test_audit_event_cannot_be_updated_or_deleted():
    event = record_audit_event(actor(), "file.edit", resource(), {})
    assert not hasattr(audit_store, "update_audit_event")
    assert not hasattr(audit_store, "delete_audit_event")
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_audit_store.py tests/test_actor_audit_usage.py -q`

Expected: FAIL because the append-only store does not exist.

- [ ] **Step 3: Implement redacted audit schema**

```python
class AuditEvent(BaseModel):
    event_id: str
    workspace_id: str
    actor_hash: str
    action: str
    resource_type: str
    resource_id: str
    result: Literal["allowed", "denied", "failed"]
    reason_code: str | None
    correlation: dict[str, str]
    at: datetime
```

Allowlisted correlation fields are request ID, run ID, task ID, invitation ID, connector ID, outcome event ID, and experiment version ID.

- [ ] **Step 4: Instrument mutable and denied actions**

Cover uploads, edits, deletes, connector lifecycle, analyses, messages, tasks, artifacts, outcomes, experiment promotion, invitation/member changes, and authorization denials. Audit write failure must not grant an otherwise denied action; for allowed mutations it becomes an operational error unless explicitly configured as best-effort for low-risk reads.

- [ ] **Step 5: Add read API and verify**

Add `GET /api/workspaces/{id}/governance/audit-events`; require owner/admin and bounded pagination.

Run: `python -m pytest tests/test_audit_store.py tests/test_actor_audit_usage.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/audit_store.py backend/app.py backend/control_plane.py backend/data_workbench.py backend/task_store.py tests/test_audit_store.py tests/test_actor_audit_usage.py
git commit -m "feat: persist immutable workspace audit events"
```

---

### Task 6: Governance Frontend

**Files:**
- Modify: `web/src/api.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Create: `web/src/governanceViewModel.js`
- Create: `web/src/governanceViewModel.test.mjs`

**Interfaces:**
- Consumes: trace status, local/provider ROI, chargeback, invitations, and audit events.
- Produces: truthful settings/governance surfaces and role-gated commands.

- [ ] **Step 1: Write failing view-model tests**

```javascript
it("does not label configured monitoring as connected without delivery", () => {
  expect(traceStatusLabel({state: "partial"})).toBe("已配置，尚未确认遥测到达");
});

it("keeps local and Foundry ROI separate", () => {
  const model = roiViewModel({local: {status: "measured"}, provider: {state: "not_configured"}});
  expect(model.localStatus).toBe("measured");
  expect(model.providerStatus).toBe("not_configured");
});
```

- [ ] **Step 2: Implement Settings sections**

Render Azure trace delivery, local ROI, Foundry ROI, per-member usage/cost, invitation lifecycle, and paged audit events as separate sections. Role-restricted actions are hidden or disabled with a reason based on server-provided permission data.

- [ ] **Step 3: Verify build and browser behavior**

Run: `node --test src/governanceViewModel.test.mjs` from `web`.

Run: `npm run build` from `web`.

Use Playwright on desktop/mobile with connected, partial, not-configured, measured, and verified fixtures. Verify no fake success, no overflow, and no raw actor/email in trace detail.

- [ ] **Step 4: Commit**

```powershell
git add web/src/api.js web/src/components.jsx web/src/styles.css web/src/governanceViewModel.js web/src/governanceViewModel.test.mjs
git commit -m "feat: expose Azure governance and verified ROI"
```

---

### Task 7: P2-B Integration Gate

**Files:**
- Create: `eval/run_p2_b_acceptance.py`
- Create: `tests/test_p2_b_acceptance_contract.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- Produces: machine-readable Azure governance acceptance report.

- [ ] **Step 1: Implement contract and local report**

Required gates: trace configuration, trace delivery, local ROI state, Foundry ROI state, chargeback lineage, invitation claim match, audit redaction, and authorization.

Run: `python -m pytest tests/test_p2_b_acceptance_contract.py -q`

Run: `python eval/run_p2_b_acceptance.py --output generated-outputs/p2-b-acceptance.json`

Expected: local gates pass; Azure delivery/native ROI remain explicitly unmeasured or not configured until production evidence is supplied.

- [ ] **Step 2: Run full regression**

Run: `python -m pytest -q`

Run: `python -m compileall -q backend tests eval`

Run: `npm run build` from `web`.

Expected: all exit 0.

- [ ] **Step 3: Commit**

```powershell
git add eval/run_p2_b_acceptance.py tests/test_p2_b_acceptance_contract.py README.md README.zh-CN.md
git commit -m "docs: publish P2-B Azure governance gates"
```
