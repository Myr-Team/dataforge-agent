# DataForge P2-A Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve market-grounding quality, reduce full-analysis latency and token use, unify long-running work as durable tasks, and make connector lifecycle survive navigation and replica replacement.

**Architecture:** Add focused market-relevance, evidence-bundle, and generic task-store modules around the existing orchestrator and MAF runtime. Reuse the local-plus-Blob persistence and atomic claim patterns already used by artifact jobs. Keep existing API responses backward compatible while creating durable task records internally.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Microsoft Agent Framework, Azure AI Search, Azure Blob Storage, Azure Key Vault, React/Vite, pytest.

## Global Constraints

- Do not change Easy Auth.
- Do not store or return connector credentials outside Azure Key Vault; encrypted session-only fallback remains explicitly labelled.
- Do not use business, dataset, file, or industry allowlists for relevance, routing, scoring, or conclusions.
- Rejected market sources cannot support a score, verdict, competitor claim, or customer citation.
- Missing external evidence is a visible gap, not a reason to substitute adjacent sources.
- Existing SSE and endpoint fields remain backward compatible.
- Every task and object endpoint resolves workspace ownership before authorization.
- Latency/token targets are measured rollout gates and are not presented as achieved before evaluation.

---

### Task 1: Frozen P2 Baseline Contract

**Files:**
- Create: `eval/p2_reference_cases.json`
- Create: `eval/run_p2_baseline.py`
- Create: `tests/test_p2_baseline_contract.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: saved SSE/run JSON or authenticated production URL plus bearer token supplied at runtime.
- Produces: `build_report(cases: list[dict], runs: list[dict]) -> dict` and `generated-outputs/p2-baseline.json`.

- [ ] **Step 1: Write the failing contract tests**

```python
def test_baseline_report_separates_observed_and_fixture_metrics():
    report = build_report(reference_cases(), observed_runs=[])
    assert report["evidence_kind"] == "fixture"
    assert report["production_claim_allowed"] is False
    assert report["metrics"]["market_relevance"] is None


def test_observed_report_requires_build_and_run_lineage():
    with pytest.raises(ValueError, match="build_id"):
        build_report(reference_cases(), observed_runs=[{"run_id": "r1"}])
```

- [ ] **Step 2: Run the red test**

Run: `python -m pytest tests/test_p2_baseline_contract.py -q`

Expected: FAIL because `eval.run_p2_baseline` does not exist.

- [ ] **Step 3: Implement the report builder and reference cases**

The case file contains at least six domain-neutral shapes: site/channel selection, growth/retention, pricing/productization, operations, campaign/service, and risk/data readiness. Each case includes goal, schema roles, evidence strength, expected required agents, and a list of known-unrelated source topics used only to test rejection.

```python
def build_report(cases: list[dict], observed_runs: list[dict]) -> dict:
    observed = bool(observed_runs)
    if observed and any(not run.get("build_id") or not run.get("run_id") for run in observed_runs):
        raise ValueError("observed runs require build_id and run_id")
    return {
        "version": "p2-baseline.v1",
        "evidence_kind": "observed" if observed else "fixture",
        "production_claim_allowed": observed,
        "sample_count": len(observed_runs) if observed else len(cases),
        "metrics": aggregate_metrics(cases, observed_runs),
    }
```

- [ ] **Step 4: Verify deterministic and observed-input modes**

Run: `python -m pytest tests/test_p2_baseline_contract.py -q`

Expected: PASS.

Run: `python eval/run_p2_baseline.py --cases eval/p2_reference_cases.json --output generated-outputs/p2-baseline.json`

Expected: output is labelled `fixture` and does not claim production performance.

- [ ] **Step 5: Commit**

```powershell
git add eval/p2_reference_cases.json eval/run_p2_baseline.py tests/test_p2_baseline_contract.py .gitignore
git commit -m "test: capture P2 product baseline"
```

---

### Task 2: Market Relevance Gate

**Files:**
- Create: `backend/market_relevance.py`
- Modify: `backend/schemas.py`
- Modify: `backend/maf_team_runtime.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/foundry_client.py`
- Modify: `agents/prompts/market_researcher.md`
- Create: `tests/test_market_relevance.py`
- Modify: `tests/test_maf_team_runtime.py`

**Interfaces:**
- Produces: `MarketQueryPlan`, `MarketSourceAssessment`, `assess_market_comparison(opportunity, evidence_digest, comparison) -> dict`, and `accepted_market_sources(comparison) -> list[dict]`.
- Consumes: normalized opportunity text, bounded corpus evidence digest, provider competitors, URLs, titles/snippets when available.

- [ ] **Step 1: Write failing tests for relevant, adjacent, and unrelated sources**

```python
def test_unrelated_fitness_products_cannot_enter_location_intelligence_competitors():
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=market_comparison("Strava", "athlete route and workout analytics", "https://strava.com"),
    )
    assert result["competitors"] == []
    assert result["rejected_sources"][0]["reasons"]
    assert result["market_evidence_status"] == "unavailable"


def test_direct_site_selection_vendor_is_accepted_with_lineage():
    result = assess_market_comparison(
        opportunity="retail location intelligence using footfall and dwell time",
        evidence_digest="site candidates, rent, transit, footfall, dwell time",
        comparison=market_comparison("Vendor", "retail site selection and footfall analytics", "https://vendor.example"),
    )
    assert result["competitors"][0]["relevance"]["verdict"] == "accepted"
    assert result["competitors"][0]["relevance"]["query_purpose"] == "direct_competitor"
```

- [ ] **Step 2: Run the red tests**

Run: `python -m pytest tests/test_market_relevance.py -q`

Expected: FAIL because the relevance module and fields do not exist.

- [ ] **Step 3: Add typed source and query contracts**

```python
class MarketSourceAssessment(BaseModel):
    verdict: Literal["accepted", "adjacent", "rejected"]
    query_purpose: Literal["direct_competitor", "pricing", "demand", "regulation", "adjacent_pattern"]
    opportunity_terms: list[str]
    matched_terms: list[str]
    deterministic_score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(min_length=1)
    assessed_at: datetime


class MarketCompetitor(BaseModel):
    name: str
    positioning: str
    url: str
    title: str | None = None
    snippet: str | None = None
    retrieval_query: str | None = None
    relevance: MarketSourceAssessment | None = None
```

Term extraction uses Unicode word tokens and CJK character n-grams derived from the current opportunity/evidence. It removes only a short language stopword list; it does not contain industries, products, vendors, or datasets.

- [ ] **Step 4: Implement deterministic gate and fail-closed merge**

```python
def assess_market_comparison(opportunity: str, evidence_digest: str, comparison: Mapping[str, Any]) -> dict[str, Any]:
    signature = semantic_signature(f"{opportunity}\n{evidence_digest}")
    accepted, adjacent, rejected = [], [], []
    for raw in comparison.get("competitors") or []:
        source = dict(raw)
        features = relevance_features(signature, semantic_signature(source_text(source)))
        assessment = classify_relevance(features, source)
        source["relevance"] = assessment.model_dump(mode="json")
        {"accepted": accepted, "adjacent": adjacent, "rejected": rejected}[assessment.verdict].append(source)
    return {
        **dict(comparison),
        "competitors": accepted,
        "adjacent_sources": adjacent,
        "rejected_sources": rejected,
        "market_evidence_status": "available" if accepted else "unavailable",
    }
```

The gate requires both meaningful overlap and directness evidence. A source whose own positioning says it is orthogonal, consumer-only, or not a competitor is never `accepted`, even if the generated paragraph repeats target terms.

- [ ] **Step 5: Integrate both MAF and legacy paths**

Call the gate after provider output validation and before score/answer/artifact merge. Preserve rejected source metadata in run trace only. When no source is accepted, set `gaps += ["external_market_evidence_unavailable"]`, omit competitor claims, and keep market evidence from raising a score.

- [ ] **Step 6: Verify regression and production-shaped fixture**

Run: `python -m pytest tests/test_market_relevance.py tests/test_maf_team_runtime.py -q`

Expected: PASS, including the Strava/TrainingPeaks/Garmin/Nix rejection fixture and a direct location-intelligence acceptance fixture.

- [ ] **Step 7: Commit**

```powershell
git add backend/market_relevance.py backend/schemas.py backend/maf_team_runtime.py backend/orchestrator.py backend/foundry_client.py agents/prompts/market_researcher.md tests/test_market_relevance.py tests/test_maf_team_runtime.py
git commit -m "feat: gate external market evidence by relevance"
```

---

### Task 3: Shared Evidence Bundle And Budget Enforcement

**Files:**
- Create: `backend/evidence_bundle.py`
- Modify: `backend/maf_team_runtime.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/run_store.py`
- Create: `tests/test_evidence_bundle.py`
- Modify: `tests/test_maf_team_runtime.py`
- Modify: `eval/run_maf_runtime_eval.py`

**Interfaces:**
- Produces: `EvidenceBundle`, `build_evidence_bundle(corpus, route, packs, limits)`, `bundle_for_agent(bundle, agent_id)`, and observed `execution_budget` termination reasons.
- Consumes: authoritative corpus hits/profile, routing decision, selected capability packs, and configured limits.

- [ ] **Step 1: Write failing bundle and budget tests**

```python
def test_bundle_deduplicates_refs_and_bounds_quote_bytes():
    bundle = build_evidence_bundle(corpus_with_duplicates(), route(), [], BundleLimits(max_items=8, max_quote_chars=240))
    assert len({item.ref for item in bundle.evidence}) == len(bundle.evidence)
    assert all(len(item.quote) <= 240 for item in bundle.evidence)


async def test_revision_receives_only_disputed_dimensions():
    registry = recording_registry(audit_issues=[{"dimension": "market_signal", "reason": "weak"}])
    await MafTeamRuntime(registry).run(review_request())
    revision_payload = registry.inputs_for("df-feasibility-analyst")[-1]
    assert revision_payload["revision_scope"] == ["market_signal"]
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_evidence_bundle.py tests/test_maf_team_runtime.py -q`

Expected: FAIL on missing bundle and revision scope.

- [ ] **Step 3: Implement bounded bundle views**

```python
class BundleLimits(BaseModel):
    max_items: int = Field(default=12, ge=1, le=40)
    max_quote_chars: int = Field(default=320, ge=80, le=1000)
    max_profile_facts: int = Field(default=20, ge=1, le=80)


class EvidenceBundle(BaseModel):
    workspace_id: str
    fingerprint: str
    evidence: list[Evidence]
    profile_facts: list[str]
    gaps: list[str]
    capability_pack_ids: list[str]
```

Persist only the fingerprint and bounded bundle metadata in run records; raw evidence remains in the existing authoritative corpus/artifact fields.

- [ ] **Step 4: Enforce request and revision budgets**

Add configuration with conservative defaults:

```python
MAX_MAF_AGENT_CALLS = 8
MAX_MAF_REVISIONS = 2
MAX_MARKET_SOURCES = 6
MAX_EVIDENCE_ITEMS = 12
MAX_EVIDENCE_QUOTE_CHARS = 320
```

Budget exhaustion records `budget_exhausted` and fails closed for required agents. Optional market exhaustion degrades with a gap. It never silently truncates required corpus evidence below one verified source.

- [ ] **Step 5: Extend evaluation output**

Report P50/P95 wall time, median/P95 tokens, cache-token ratio, per-pattern completion, unsupported-claim rate, groundedness, selected/skipped agent accuracy, and budget terminations. Fixture runs remain labelled non-production.

- [ ] **Step 6: Verify tests and deterministic evaluation**

Run: `python -m pytest tests/test_evidence_bundle.py tests/test_maf_team_runtime.py tests/test_maf_evaluation_contract.py -q`

Run: `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/p2-maf-eval.json`

Expected: tests pass; report includes budget and bundle fields with `production_claim_allowed=false`.

- [ ] **Step 7: Commit**

```powershell
git add backend/evidence_bundle.py backend/maf_team_runtime.py backend/orchestrator.py backend/run_store.py tests/test_evidence_bundle.py tests/test_maf_team_runtime.py eval/run_maf_runtime_eval.py
git commit -m "feat: share bounded evidence across MAF agents"
```

---

### Task 4: Generic Durable Task Store And API

**Files:**
- Create: `backend/task_store.py`
- Modify: `backend/artifact_jobs.py`
- Modify: `backend/data_workbench.py`
- Modify: `backend/app.py`
- Modify: `backend/control_plane.py`
- Create: `tests/test_task_store.py`
- Create: `tests/test_task_api.py`
- Create: `tests/test_data_workbench_task_bridge.py`

**Interfaces:**
- Produces: `create_task`, `get_task`, `list_tasks`, `claim_task`, `update_task`, `request_cancel`, and retry linkage.
- Consumes: workspace/action authorization, actor identity, existing Blob JSON helpers, existing artifact/ingest workers.

- [ ] **Step 1: Write failing persistence, claim, retry, and authorization tests**

```python
def test_task_survives_local_store_loss_via_blob(fake_blob, tmp_path):
    task = create_task(task_payload(), actor())
    shutil.rmtree(TASK_DIR)
    assert get_task(task["task_id"])["workspace_id"] == "ws-1"


def test_only_one_worker_claims_task():
    task = create_task(task_payload(), actor())
    claims = concurrent_calls(lambda: claim_task(task["task_id"], "worker"), 2)
    assert sum(item is not None for item in claims) == 1


def test_non_member_cannot_read_task(client):
    assert client.get("/api/tasks/task-private", headers=outsider()).status_code == 403
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_task_store.py tests/test_task_api.py -q`

Expected: FAIL because the generic task store does not exist.

- [ ] **Step 3: Implement append-only attempts and atomic claims**

```python
def create_task(payload: Mapping[str, Any], actor: Mapping[str, Any]) -> dict[str, Any]:
    now = utc_now()
    task = TaskRecord.model_validate({
        **payload,
        "task_id": f"task_{uuid4().hex[:16]}",
        "status": "queued",
        "attempt": 1,
        "actor": public_actor(actor),
        "created_at": now,
        "updated_at": now,
    })
    return persist_task(task.model_dump(mode="json"))
```

Claims use the existing Blob conditional-create helper. Progress updates are monotonic within an attempt. Retry creates a new task with `retry_of` and incremented attempt. Cancel requests do not rewrite completed tasks.

- [ ] **Step 4: Add workspace-scoped API routes**

Implement:

- `GET /api/workspaces/{id}/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/tasks/{task_id}/retry`

Resolve workspace from the stored task before authorization. Retry/cancel require the corresponding original action permission.

- [ ] **Step 5: Bridge artifact and ingest jobs**

Existing artifact job IDs and responses remain unchanged. Each new artifact/ingest operation creates a generic task whose result links the original job ID. Generic task failure never erases partial artifact or imported-file results.

- [ ] **Step 6: Verify tests**

Run: `python -m pytest tests/test_task_store.py tests/test_task_api.py tests/test_artifact_jobs.py tests/test_data_workbench_task_bridge.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/task_store.py backend/artifact_jobs.py backend/data_workbench.py backend/app.py backend/control_plane.py tests/test_task_store.py tests/test_task_api.py
git commit -m "feat: persist workspace tasks across operations"
```

---

### Task 5: Task Center Frontend

**Files:**
- Modify: `web/src/api.js`
- Create: `web/src/TaskCenter.jsx`
- Modify: `web/src/App.jsx`
- Modify: `web/src/components.jsx`
- Modify: `web/src/DataWorkbench.jsx`
- Modify: `web/src/styles.css`
- Create: `web/src/taskCenter.test.mjs`

**Interfaces:**
- Consumes: task list/detail/cancel/retry APIs.
- Produces: one global task drawer, completion notifications, and links to run/data/artifact destinations.

- [ ] **Step 1: Write failing view-model tests**

```javascript
it("keeps partial task results available after failure", () => {
  const model = taskViewModel({status: "partial", result: {file_ids: ["f1"]}, errors: [{message: "one table failed"}]});
  expect(model.canOpenResult).toBe(true);
  expect(model.severity).toBe("warning");
});

it("does not offer cancellation for terminal tasks", () => {
  expect(taskViewModel({status: "completed"}).canCancel).toBe(false);
});
```

- [ ] **Step 2: Run red tests**

Run: `node --test src/taskCenter.test.mjs` from `web`.

Expected: FAIL because `TaskCenter` and its view model do not exist.

- [ ] **Step 3: Implement polling and recovery**

Poll only while queued/running tasks exist, pause when the tab is hidden, refresh immediately when it becomes visible, and use server timestamps to avoid duplicate notifications. Do not store task truth in localStorage; localStorage may only remember dismissed notification IDs.

- [ ] **Step 4: Integrate page-local operations**

Analysis, upload/ingest, connector import, artifact generation, and iteration surfaces link to the global task record. Existing page-specific progress remains as a compact projection of the same task.

- [ ] **Step 5: Verify tests and build**

Run: `npm run build` from `web`.

Expected: Vite production build succeeds with no missing imports.

- [ ] **Step 6: Browser acceptance**

Use Playwright on desktop and mobile: start an analysis, navigate through all six pages, reload, reopen the task drawer, and verify the same task reaches a terminal state without console errors or horizontal overflow.

- [ ] **Step 7: Commit**

```powershell
git add web/src/api.js web/src/TaskCenter.jsx web/src/App.jsx web/src/components.jsx web/src/DataWorkbench.jsx web/src/styles.css web/src/taskCenter.test.mjs
git commit -m "feat: add durable task center"
```

---

### Task 6: Durable Connector Records And Key Vault Adapter

**Files:**
- Create: `backend/connector_store.py`
- Create: `backend/connector_secret_store.py`
- Modify: `backend/data_workbench.py`
- Modify: `backend/dependency_health.py`
- Modify: `backend/requirements.txt`
- Modify: `web/src/DataWorkbench.jsx`
- Modify: `web/src/api.js`
- Create: `tests/test_connector_store.py`
- Create: `tests/test_connector_secret_store.py`
- Create: `tests/test_data_workbench_connectors.py`

**Interfaces:**
- Produces: connector CRUD/reconnect/sync records and `SecretStore.put/get/delete` with Key Vault and encrypted-session implementations.
- Consumes: managed identity, `DF_KEY_VAULT_URL`, validated SQL/Blob connection payloads, task store.

- [ ] **Step 1: Write failing tests for secret isolation and reconnect**

```python
def test_connector_record_contains_secret_reference_not_credential(fake_secret_store):
    connector = create_connector("ws", sql_payload(password="secret"), actor(), fake_secret_store)
    serialized = json.dumps(connector)
    assert "secret" not in serialized
    assert connector["secret_ref"]


def test_reconnect_after_process_state_is_cleared(fake_secret_store):
    connector = create_connector("ws", blob_payload(), actor(), fake_secret_store)
    clear_connector_sessions()
    assert reconnect_connector("ws", connector["connector_id"], fake_secret_store)["status"] == "connected"
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_connector_store.py tests/test_connector_secret_store.py tests/test_data_workbench_connectors.py -q`

Expected: FAIL because durable connectors and secret-store interfaces do not exist.

- [ ] **Step 3: Implement secret-store interface and Key Vault adapter**

```python
class SecretStore(Protocol):
    def put(self, workspace_id: str, connector_id: str, secret: Mapping[str, str]) -> SecretReference: ...
    def get(self, reference: SecretReference) -> dict[str, str]: ...
    def delete(self, reference: SecretReference) -> None: ...
```

Use `azure-keyvault-secrets` with `DefaultAzureCredential` only when `DF_KEY_VAULT_URL` is configured. The fallback uses the existing encrypted in-process/session store and reports `persistence=session_only`.

- [ ] **Step 4: Implement connector API lifecycle**

Add list, reconnect, sync, disconnect, and delete endpoints. Reconnect reads the secret server-side and returns status/metadata only. Sync creates a durable task and imported file versions with connector/table/blob lineage and cursor/watermark metadata.

- [ ] **Step 5: Update frontend connector states**

Show durable, session-only, expired, disconnected, syncing, and error states. Provide reconnect, sync, disconnect, and delete commands. Never repopulate password/connection-string fields from API data.

- [ ] **Step 6: Verify tests, dependency import, and build**

Run: `python -m pytest tests/test_connector_store.py tests/test_connector_secret_store.py tests/test_data_workbench_connectors.py -q`

Run: `python -m backend.import_smoke`

Run: `npm run build` from `web`.

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/connector_store.py backend/connector_secret_store.py backend/data_workbench.py backend/dependency_health.py backend/requirements.txt web/src/DataWorkbench.jsx web/src/api.js tests/test_connector_store.py tests/test_connector_secret_store.py tests/test_data_workbench_connectors.py
git commit -m "feat: persist connector lifecycle securely"
```

---

### Task 7: P2-A Integration Gate

**Files:**
- Create: `eval/run_p2_a_acceptance.py`
- Create: `tests/test_p2_a_acceptance_contract.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- Consumes: baseline report, market relevance fixtures, task/connector APIs, MAF evaluation output.
- Produces: one machine-readable acceptance report with observed versus fixture labels.

- [ ] **Step 1: Write the acceptance contract test**

```python
def test_acceptance_requires_every_gate_and_evidence_label():
    report = build_acceptance_report(component_reports())
    assert set(report["gates"]) >= {"market_relevance", "maf_quality", "tasks", "connectors"}
    assert all(gate["evidence_kind"] in {"fixture", "observed"} for gate in report["gates"].values())
```

- [ ] **Step 2: Implement and run local acceptance**

Run: `python eval/run_p2_a_acceptance.py --output generated-outputs/p2-a-acceptance.json`

Expected: functional gates pass; latency/token targets remain `unmeasured` until production samples are supplied.

- [ ] **Step 3: Run full regression**

Run: `python -m pytest -q`

Run: `python -m compileall -q backend tests eval`

Run: `npm run build` from `web`.

Run: `git diff --check`

Expected: all commands exit 0.

- [ ] **Step 4: Commit**

```powershell
git add eval/run_p2_a_acceptance.py tests/test_p2_a_acceptance_contract.py README.md README.zh-CN.md
git commit -m "docs: publish P2-A productization gates"
```
