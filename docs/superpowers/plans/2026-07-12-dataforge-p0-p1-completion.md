# DataForge P0/P1 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the remaining P0 runtime-quality work and the P1 governance, ROI, evidence-based iteration, and durable artifact work before one production acceptance pass.

**Architecture:** Keep the current MAF runtime, run store, workspace store, and API contracts. Add focused modules for outcome evidence, artifact jobs, and runtime evaluation; make the governance API aggregate observed telemetry and explicit business assumptions without presenting estimates as measured values. Preserve the current UI routes while replacing snapshot-only iteration and request-bound artifact generation with durable records.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Azure Blob Storage, Azure Application Insights/OpenTelemetry, Microsoft Agent Framework, React/Vite, pytest.

## Global Constraints

- Do not change Easy Auth or weaken existing authentication.
- Do not persist, log, trace, or return connector credentials or raw access tokens.
- Scores, verdicts, opportunity selection, and version promotion remain evidence-derived; no dataset, business, file, industry, or demo-name routing rules.
- Missing usage, cost, outcome, and health values remain `null`/unknown instead of optimistic defaults.
- `estimated`, `measured`, and `verified` ROI are distinct states; only source-linked reviewer-approved outcomes may be `verified`.
- Generated or simulated feedback has `synthetic` provenance and cannot strengthen an evidence tier.
- Existing SSE and API fields remain backward compatible.
- Production rollout remains canary-based and is promoted only after real runtime evidence.

---

### Task 1: P0 Runtime Budget And Quality Gate

**Files:**
- Modify: `backend/maf_team_runtime.py`
- Modify: `backend/maf_agents.py`
- Modify: `backend/orchestrator.py`
- Modify: `eval/run_maf_runtime_eval.py`
- Modify: `eval/maf_runtime_cases.json`
- Test: `tests/test_maf_team_runtime.py`
- Test: `tests/test_maf_evaluation_contract.py`

**Interfaces:**
- Consumes: `MafTeamRequest`, selected collaboration pattern, authoritative corpus, rubric, and per-agent model usage.
- Produces: `execution_budget`, per-agent latency/token usage, skipped-agent reasons, and a production evaluation report with real sample metadata.

- [ ] **Step 1: Write failing tests for bounded agent selection and execution budgets**

```python
def test_direct_followup_does_not_run_unneeded_specialists():
    plan = select_collaboration_plan(
        intent="followup_edit", output_mode="chat",
        needs_workspace=True, needs_external=False, high_impact=False,
    )
    assert plan.selected_agents == ("df-coordinator",)

def test_runtime_summary_exposes_observed_budget_usage():
    result = asyncio.run(MafTeamRuntime(fake_registry()).run(sample_request()))
    assert result.summary.execution_budget["max_agent_calls"] >= result.summary.execution_budget["agent_calls"]
    assert result.summary.execution_budget["total_tokens"] is None or result.summary.execution_budget["total_tokens"] >= 0
```

- [ ] **Step 2: Run the focused tests and verify they fail on missing budget fields or excess agent selection**

Run: `python -m pytest tests/test_maf_team_runtime.py tests/test_maf_evaluation_contract.py -q`

Expected: FAIL because execution budgets and production sample metadata are not yet persisted.

- [ ] **Step 3: Implement semantic agent-call budgets and truthful per-agent usage aggregation**

Add `max_agent_calls`, `agent_calls`, `max_revision_rounds`, `workflow_duration_ms`, `participant_duration_ms`, and nullable token totals to the MAF summary. Skip market, audit, and producer agents unless the normalized route requires them. Reuse authoritative corpus across participants and correction retries.

- [ ] **Step 4: Extend the evaluation runner with three domain-neutral evidence shapes and weak-evidence cases**

The report records sample count, completion, groundedness, unsupported-claim rate, verdict calibration, P50/P95 latency, token totals, fallback rate, and the exact deployed runtime/image metadata when supplied. Deterministic fixtures remain labelled non-production.

- [ ] **Step 5: Verify focused tests, full tests, and deterministic evaluation**

Run: `python -m pytest tests/test_maf_team_runtime.py tests/test_maf_evaluation_contract.py -q`

Expected: PASS.

Run: `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json`

Expected: report contains all collaboration patterns and no production-quality claim.

---

### Task 2: P1 Outcome Ledger, Azure Telemetry, And ROI States

**Files:**
- Create: `backend/outcome_store.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/tracing.py`
- Modify: `backend/app.py`
- Modify: `web/src/api.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Test: `tests/test_outcome_roi.py`
- Test: `tests/test_tracing_telemetry.py`
- Test: `tests/test_governance_roi_summary.py`

**Interfaces:**
- Produces: `record_outcome_event(workspace_id, payload, actor)`, `list_outcome_events(workspace_id)`, `GET/POST /api/workspaces/{id}/outcomes`, and an expanded `governance-summary.roi` contract.
- Consumes: run-store usage, actor identity, App Insights configuration state, outcome events, and explicit cost assumptions.

- [ ] **Step 1: Write failing tests for outcome provenance and ROI state transitions**

```python
def test_assumption_only_roi_is_estimated():
    summary = build_roi(usage=observed_usage(), outcomes=[])
    assert summary["status"] == "estimated"

def test_source_linked_observation_is_measured_but_not_verified(tmp_path):
    event = record_outcome_event("ws", observed_payload(source_file_id="file-v2"), actor())
    assert event["provenance"] == "observed"
    assert event["verification"]["status"] == "unverified"

def test_only_reviewer_approval_promotes_roi_to_verified():
    summary = build_roi(usage=observed_usage(), outcomes=[verified_outcome()])
    assert summary["status"] == "verified"
```

- [ ] **Step 2: Run tests and verify missing store/API/state failures**

Run: `python -m pytest tests/test_outcome_roi.py tests/test_governance_roi_summary.py tests/test_tracing_telemetry.py -q`

Expected: FAIL because the outcome ledger and ROI state model do not exist.

- [ ] **Step 3: Implement the append-only outcome ledger and validation**

Require metric name, unit, baseline/target/observed values as applicable, observation time, attribution window, provenance, source file/connector/run lineage, and actor. Reject `verified` input from clients; verification is a separate reviewer action. Store local JSON atomically and mirror to Blob using the existing store pattern.

- [ ] **Step 4: Emit Foundry-compatible OpenTelemetry attributes without raw content**

Emit `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, run/workspace correlation hashes, cache/retry/tool status, and outcome-event IDs. Expose telemetry status as `connected`, `partial`, or `not_configured`; keep experimental SDK instrumentation disabled while it breaks streamed MAF responses.

- [ ] **Step 5: Extend governance UI with three separate blocks**

Render observed model usage/cost, estimated time-value assumptions, and measured business outcomes separately. Show Foundry/App Insights connection truthfully and provide outcome-record/verify actions only to the roles already represented by the workspace member model.

- [ ] **Step 6: Verify focused tests and frontend build**

Run: `python -m pytest tests/test_outcome_roi.py tests/test_governance_roi_summary.py tests/test_tracing_telemetry.py -q`

Expected: PASS.

Run: `npm run build` from `web`.

Expected: Vite build succeeds.

---

### Task 3: P1 Multi-User Audit And Chargeback

**Files:**
- Modify: `backend/identity.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/conversation_store.py`
- Modify: `backend/data_workbench.py`
- Modify: `web/src/components.jsx`
- Test: `tests/test_actor_audit_usage.py`
- Test: `tests/test_entra_member_invites.py`
- Test: `tests/test_workspace_roles.py`

**Interfaces:**
- Consumes: Easy Auth actor claims, workspace member records, run/message/data mutation events.
- Produces: normalized Owner/Editor/Viewer permissions, per-actor actions and usage, and explicit permission-denied responses for workspace mutations.

- [ ] **Step 1: Write failing tests for role enforcement and actor attribution**

```python
def test_viewer_can_read_but_cannot_edit_file():
    assert authorize("viewer", "file.read") is True
    assert authorize("viewer", "file.edit") is False

def test_editor_cannot_change_member_roles():
    assert authorize("editor", "member.role.update") is False

def test_audit_event_links_actor_action_and_resource():
    event = record_audit_event(actor(), "file.edit", resource("file-v2"))
    assert event["actor"]["actor_id"]
    assert event["resource"]["id"] == "file-v2"
```

- [ ] **Step 2: Run tests and verify missing authorization/audit failures**

Run: `python -m pytest tests/test_workspace_roles.py tests/test_actor_audit_usage.py tests/test_entra_member_invites.py -q`

Expected: FAIL because current roles are display metadata and are not enforced.

- [ ] **Step 3: Implement a centralized workspace authorization helper**

Owner/admin manages members and all workspace content; editor analyzes and edits content but cannot manage members; viewer reads only. Preserve current owner access and workspace-local invite fallback. Do not alter Easy Auth configuration.

- [ ] **Step 4: Record all mutable actions with actor and resource lineage**

Cover uploads, cell/content edits, file deletes, connector imports, analyses, messages, outcome events, artifact generation, and member changes. Audit values exclude raw prompts, file contents, credentials, and tokens.

- [ ] **Step 5: Verify role, actor, and invite tests**

Run: `python -m pytest tests/test_workspace_roles.py tests/test_actor_audit_usage.py tests/test_entra_member_invites.py -q`

Expected: PASS.

---

### Task 4: P1 Evidence-Based Experiment And Version Ledger

**Files:**
- Create: `backend/experiment_store.py`
- Modify: `backend/run_store.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/control_plane.py`
- Modify: `web/src/api.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Test: `tests/test_experiment_versions.py`
- Test: `tests/test_followup_plan_version.py`
- Test: `tests/test_artifact_version_snapshot.py`

**Interfaces:**
- Produces: version records containing hypothesis, decision, evidence set, gaps, metric definitions, source lineage, evidence delta, decision delta, and artifact references.
- Consumes: current run artifact, iteration inputs, imported file versions, connector metadata, and outcome events.

- [ ] **Step 1: Write failing tests for assumption/target/observed separation and evidence deltas**

```python
def test_synthetic_feedback_cannot_promote_verdict():
    version = build_next_version(previous(), [synthetic_metric()])
    assert verdict_rank(version.verdict) <= verdict_rank(previous().verdict)

def test_observed_file_version_creates_evidence_and_decision_delta():
    version = build_next_version(previous(), [observed_metric(source_file_id="file-v2")])
    assert version.evidence_delta["added"]
    assert version.decision_delta["reasons"]

def test_duplicate_plan_snapshot_is_not_presented_as_new_experiment():
    assert summarize_version(plan_snapshot())["evidence_changed"] is False
```

- [ ] **Step 2: Run tests and verify missing experiment model failures**

Run: `python -m pytest tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py -q`

Expected: FAIL because current versions are run snapshots without evidence/decision deltas.

- [ ] **Step 3: Implement append-only experiment versions and deterministic deltas**

Normalize metric kinds to `assumption`, `target`, or `observed`; require source lineage for observed metrics. Compare evidence by stable reference and decision fields by normalized value. Never invent a difference when values are unchanged.

- [ ] **Step 4: Integrate analysis, plan, feedback, and artifact snapshots**

An analysis starts V1; a plan or artifact can attach to V1 without claiming evidence improvement; imported observed feedback starts the next experiment version; the next analysis records evidence and decision deltas and links generated artifacts.

- [ ] **Step 5: Replace the iteration surface with truthful version comparison**

Show changed/unchanged decisions, added/contradicted/strengthened evidence, metric status, source, observation time, and artifact links. Empty comparisons render “暂无可比较的新证据”.

- [ ] **Step 6: Verify tests and build**

Run: `python -m pytest tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py -q`

Expected: PASS.

Run: `npm run build` from `web`.

Expected: Vite build succeeds.

---

### Task 5: P1 Durable Artifact Jobs

**Files:**
- Create: `backend/artifact_jobs.py`
- Modify: `backend/app.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/run_store.py`
- Modify: `web/src/api.js`
- Modify: `web/src/App.jsx`
- Modify: `web/src/components.jsx`
- Test: `tests/test_artifact_jobs.py`
- Test: `tests/test_artifact_version_snapshot.py`

**Interfaces:**
- Produces: `POST /api/artifact-jobs`, `GET /api/artifact-jobs/{job_id}`, and workspace job/list fields with `queued|running|partial|completed|failed|cancelled` states.
- Consumes: an existing run artifact, requested kinds, workspace branding assets, and existing PDF/image/audio generators.

- [ ] **Step 1: Write failing tests for persistence, partial success, and idempotency**

```python
def test_job_survives_store_reload(tmp_path):
    job = create_job(store(tmp_path), request())
    assert store(tmp_path).get(job.id).status == "queued"

def test_image_failure_keeps_completed_pdf():
    result = run_job(request(kinds=["pdf", "concept_image"]), fail_image=True)
    assert result.status == "partial"
    assert result.artifacts["pdf"]["artifact_url"]
    assert result.errors["concept_image"]["message"]

def test_idempotency_key_reuses_non_terminal_job():
    first = create_job(store(), request(idempotency_key="same"))
    second = create_job(store(), request(idempotency_key="same"))
    assert first.id == second.id
```

- [ ] **Step 2: Run tests and verify missing job-store/API failures**

Run: `python -m pytest tests/test_artifact_jobs.py tests/test_artifact_version_snapshot.py -q`

Expected: FAIL because artifact generation is currently request-bound.

- [ ] **Step 3: Implement durable job state and per-kind isolation**

Persist job state atomically and mirror it to Blob. Run each requested artifact kind independently with existing generator retry/fallback behavior. Merge completed outputs immediately into the source run and workspace artifact list; retain friendly error details per failed kind.

- [ ] **Step 4: Integrate polling and refresh-safe UI state**

Starting a job returns immediately. The UI polls persisted status, restores active jobs after navigation or refresh, shows partial outputs as soon as available, and names outputs from the analysis title plus version instead of generic timestamps.

- [ ] **Step 5: Verify tests and build**

Run: `python -m pytest tests/test_artifact_jobs.py tests/test_artifact_version_snapshot.py -q`

Expected: PASS.

Run: `npm run build` from `web`.

Expected: Vite build succeeds.

---

### Task 6: Unified Verification, Production Canary, And Documentation

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Create: `docs/p0-p1-production-evidence.md`

**Interfaces:**
- Consumes: committed source SHA, immutable ACR image tags, evaluation reports, live API output, App Insights traces, and browser screenshots.
- Produces: one acceptance record for P0 and P1.

- [ ] **Step 1: Run full local verification**

Run: `python -m pytest -q`

Expected: all backend tests pass.

Run: `python -m compileall -q backend`

Expected: exit code 0.

Run: `npm run build` from `web`.

Expected: Vite build succeeds.

- [ ] **Step 2: Build immutable backend and frontend images from the same commit**

Use tags containing the short commit SHA and record both tags in the evidence document.

- [ ] **Step 3: Roll out backend at 10%, then 30%, then 100% only after live evidence**

At each stage run grounded analysis, follow-up, ambiguous clarification, weak-evidence downgrade, stop/cancel, artifact partial failure, outcome recording, multi-user attribution, and experiment-version comparison. Do not promote when any terminal error, duplicate final, credential leak, unknown-as-success value, or unsupported verdict appears.

- [ ] **Step 4: Verify all six production pages and persisted refresh behavior**

Capture screenshots for workspace, data, runs, conversation, artifacts, settings/governance, plus refreshed artifact job and experiment comparison.

- [ ] **Step 5: Update READMEs and record exact evidence**

Document what is observed, estimated, measured, verified, configured, and pending. Include file/line references, run IDs, image tags, revision names, test/build output, App Insights correlation IDs, API samples, and screenshot paths with secrets removed.
