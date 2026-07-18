# Conversation Analysis Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workspace-initiated analysis create a durable run and latest-analysis record without creating, selecting, or persisting a human conversation.

**Architecture:** Keep one intelligence pipeline, but make its execution identity explicit. `ChatRequest` carries an additive execution contract (`run_id`, `origin`, `persist_messages`); the existing orchestrator uses the run ID for traces and run storage, while it only loads or persists conversation state for human conversation origins. The frontend consumes `run_id` for autonomous analysis and only selects a conversation when the backend declares the origin as `conversation`.

**Tech Stack:** FastAPI, Pydantic, Python async generators and SSE, Blob-backed JSON stores, React/Vite, pytest, Node test runner.

## Global Constraints

- Do not change Easy Auth, identity configuration, or authorization semantics.
- Do not infer behavior from workspace names, data names, or keywords.
- Preserve existing conversations, run blobs, and artifact compatibility; no destructive migration.
- Every automatic-analysis execution must retain task/run/audit evidence but must not emit or persist a fabricated `message.create` action.
- Roll out with `DF_SEPARATE_ANALYSIS_CONVERSATIONS`; candidate verification precedes production traffic changes.

---

### Task 1: Establish Execution Identity Contracts

**Files:**
- Modify: `backend/schemas.py:519-526`
- Modify: `backend/run_store.py:92-132`
- Test: `tests/test_execution_context_contract.py`

**Interfaces:**
- Produces `ChatRequest.run_id: str | None`, `ChatRequest.origin: Literal["conversation", "workspace_auto_analysis", "data_send_analysis"]`, and `ChatRequest.persist_messages: bool | None`.
- Produces `start_run(..., conversation_id: str | None, origin: str)` with a run document that keeps both IDs distinct.

- [ ] **Step 1: Write failing contract tests**

```python
def test_start_run_keeps_auto_analysis_run_separate_from_conversation() -> None:
    start_run("run_auto", "ws", "goal", conversation_id=None, origin="workspace_auto_analysis")
    assert _ACTIVE["run_auto"]["run_id"] == "run_auto"
    assert _ACTIVE["run_auto"]["conversation_id"] is None
    assert _ACTIVE["run_auto"]["origin"] == "workspace_auto_analysis"


def test_chat_request_accepts_additive_execution_contract() -> None:
    request = ChatRequest(workspace_id="ws", message="analyze", run_id="run_1", origin="data_send_analysis", persist_messages=False)
    assert request.run_id == "run_1"
    assert request.persist_messages is False
```

- [ ] **Step 2: Run the focused tests and verify red**

Run: `python -m pytest tests/test_execution_context_contract.py -q`

Expected: failures because the request fields and run-store arguments do not exist.

- [ ] **Step 3: Implement the minimal typed execution fields and run metadata**

```python
class ChatRequest(BaseModel):
    ...
    run_id: str | None = None
    origin: Literal["conversation", "workspace_auto_analysis", "data_send_analysis"] = "conversation"
    persist_messages: bool | None = None
```

```python
def start_run(..., conversation_id: str | None = None, origin: str = "conversation") -> None:
    _ACTIVE[run_id]["conversation_id"] = conversation_id
    _ACTIVE[run_id]["origin"] = origin
```

- [ ] **Step 4: Run the focused tests and verify green**

Run: `python -m pytest tests/test_execution_context_contract.py -q`

Expected: `2 passed`.

### Task 2: Separate Automatic Analysis From Conversation Persistence

**Files:**
- Modify: `backend/orchestrator.py:558-575, 961-970, 5433-5638, 6128-6231, 6234-6970`
- Modify: `backend/app.py:313-392`
- Modify: `backend/workspace_store.py:912-950`
- Test: `tests/test_auto_analysis_separation.py`

**Interfaces:**
- Automatic request emits a `ready` event with `run_id`, `origin`, and `conversation_id: null`.
- `workspace_auto_analyze` returns `run_id`, `conversation_id: null`, and the completed latest-analysis payload.
- `save_workspace_last_analysis` persists `run_id` and optional `conversation_id` separately.

- [ ] **Step 1: Write failing integration-style orchestration tests**

```python
async def test_auto_execution_never_persists_messages(monkeypatch) -> None:
    persisted = []
    monkeypatch.setattr(orchestrator, "_persist_user_message", lambda *args: persisted.append("user"))
    monkeypatch.setattr(orchestrator, "_persist_assistant_message", lambda *args: persisted.append("assistant"))
    frames = [decode(frame) async for frame in orchestrator.orchestrate_chat(
        ChatRequest(workspace_id="ws", message="analyze", run_id="run_auto", origin="workspace_auto_analysis", persist_messages=False)
    )]
    assert next(frame for frame in frames if frame["event"] == "ready")["data"]["conversation_id"] is None
    assert persisted == []


def test_auto_analyze_endpoint_audits_analysis_but_not_message(monkeypatch, client) -> None:
    ...
    response = client.post("/api/workspaces/ws/auto-analyze", json={})
    assert response.json()["conversation_id"] is None
    assert response.json()["run_id"]
    assert "message.create" not in audit_actions
```

- [ ] **Step 2: Run the focused tests and verify red**

Run: `python -m pytest tests/test_auto_analysis_separation.py -q`

Expected: failures because automatic execution still persists messages and reuses a conversation ID.

- [ ] **Step 3: Implement the shared execution adapter boundary**

```python
def _execution_contract(req: ChatRequest) -> tuple[str, str | None, bool]:
    run_id = req.run_id or str(uuid.uuid4())
    origin = req.origin or "conversation"
    persist_messages = origin == "conversation" if req.persist_messages is None else bool(req.persist_messages)
    conversation_id = req.conversation_id if persist_messages else None
    if origin == "conversation" and not conversation_id:
        conversation_id = str(uuid.uuid4())
    return run_id, conversation_id, persist_messages
```

Use `run_id` for `agent_trace`, `_frame`/`record_event`, `start_run`, `complete_run`, MAF telemetry, and artifact source provenance. Load history and call either message persistence helper only when `persist_messages` is true. Add `run_id`, `origin`, and optional `conversation_id` to every terminal/ready payload and artifact. Pass the persistence flag through lightweight, MAF, failure, and final-completion helpers.

`workspace_auto_analyze` must instantiate the request with `origin="workspace_auto_analysis"`, a fresh `run_id`, and `persist_messages=False`; it must remove the `message.create` audit call and return the event's run ID.

- [ ] **Step 4: Store canonical run lineage in latest analysis**

```python
analysis = {
    "run_id": artifact.get("run_id") or final_payload.get("run_id"),
    "conversation_id": artifact.get("conversation_id") or final_payload.get("conversation_id"),
    ...
}
```

- [ ] **Step 5: Run focused tests and established orchestrator suites**

Run: `python -m pytest tests/test_auto_analysis_separation.py tests/test_orchestrator*.py tests/test_run_store*.py -q`

Expected: all pass.

### Task 3: Preserve Artifact And Conversation Compatibility

**Files:**
- Modify: `backend/artifact_jobs.py:46-81, 355-374`
- Modify: `backend/conversation_store.py:35-95, 190-211`
- Test: `tests/test_artifact_source_run.py`
- Test: `tests/test_conversation_visibility.py`

**Interfaces:**
- Artifact jobs accept `source_run_id` first, then legacy `run_id`/`conversation_id` fallback.
- Conversation summaries expose `origin`, `visibility`, and `linked_run_ids` only when present.
- Default list hides only explicit `visibility="system_activity"`; all ambiguous legacy records remain visible.

- [ ] **Step 1: Write failing compatibility tests**

```python
def test_artifact_job_prefers_source_run_id_over_conversation_id() -> None:
    payload = {"workspace_id": "ws", "source_run_id": "run_auto", "conversation_id": "conversation_1", "kinds": ["pdf"]}
    assert create_artifact_job(payload)["source_run_id"] == "run_auto"


def test_default_conversation_list_keeps_human_records_and_hides_explicit_system_activity(monkeypatch) -> None:
    ...
    assert [item["conversation_id"] for item in list_conversations("ws")] == ["human"]
```

- [ ] **Step 2: Run focused tests and verify red**

Run: `python -m pytest tests/test_artifact_source_run.py tests/test_conversation_visibility.py -q`

Expected: failures because artifact jobs prefer the legacy conversation parameter and summaries have no visibility handling.

- [ ] **Step 3: Implement additive compatibility changes**

```python
source_run_id = _required_text(
    request.get("source_run_id") or request.get("run_id") or request.get("conversation_id"),
    "source_run_id", 180,
)
```

Add optional conversation metadata fields without mutating old records. Existing calls keep their behavior. Do not run a destructive migration; visibility changes require an explicit trusted classification marker.

- [ ] **Step 4: Run focused tests and the existing artifact/conversation suites**

Run: `python -m pytest tests/test_artifact_source_run.py tests/test_conversation_visibility.py tests/test_artifact_jobs*.py tests/test_conversation*.py -q`

Expected: all pass.

### Task 4: Consume Run Identity In The Frontend

**Files:**
- Modify: `web/src/App.jsx:385-451, 610-730, 885-985`
- Modify: `web/src/api.js:555-630` only if event parsing needs additive fields
- Test: `web/src/conversationExecution.test.mjs`

**Interfaces:**
- Automatic analysis sends `origin: "workspace_auto_analysis"`, no conversation ID, and records the returned `run_id` as trace/artifact provenance.
- Manual conversation sends `origin: "conversation"` and may select the returned conversation ID.
- Artifact generation sends `source_run_id` when a latest analysis exists.

- [ ] **Step 1: Write failing UI contract tests**

```javascript
test("automatic analysis does not select a conversation", () => {
  const result = readyState({ origin: "workspace_auto_analysis", run_id: "run_1", conversation_id: null });
  assert.equal(result.activeConversationId, null);
  assert.equal(result.runId, "run_1");
});

test("conversation ready event retains conversation selection", () => {
  const result = readyState({ origin: "conversation", run_id: "run_2", conversation_id: "conv_2" });
  assert.equal(result.activeConversationId, "conv_2");
});
```

- [ ] **Step 2: Run the focused UI test and verify red**

Run: `node --test web/src/conversationExecution.test.mjs`

Expected: failure because the helper/event contract does not exist.

- [ ] **Step 3: Implement a small pure ready-event helper and wire it into App**

```javascript
export function readyExecutionState(data, previousConversationId = null) {
  const origin = data?.origin || "conversation";
  return {
    runId: data?.run_id || null,
    activeConversationId: origin === "conversation" ? data?.conversation_id || previousConversationId : previousConversationId,
  };
}
```

Use it in both primary analysis execution and `ensureAnalysisArtifact`. Do not set `df-conv:*` or `activeConversationId` for automatic analysis. Read latest analysis `run_id` into the final artifact and pass it as `source_run_id` to `createArtifactJob`.

- [ ] **Step 4: Run UI contracts and build**

Run: `node --test web/src/conversationExecution.test.mjs web/src/governanceViewModel.test.mjs`

Expected: all pass.

Run: `npm run build`

Expected: Vite build succeeds.

### Task 5: Verify Feature Flag, Full Regression, And Candidate Runtime

**Files:**
- Modify: `backend/.env.example`
- Modify: `README.md:207-214`
- Test: `tests/test_execution_context_contract.py`
- Test: `tests/test_auto_analysis_separation.py`

**Interfaces:**
- `DF_SEPARATE_ANALYSIS_CONVERSATIONS=0` keeps legacy behavior for rollback.
- `DF_SEPARATE_ANALYSIS_CONVERSATIONS=1` enables explicit run/conversation separation.

- [ ] **Step 1: Write a failing flag-gate test**

```python
def test_separation_feature_flag_defaults_to_legacy(monkeypatch) -> None:
    monkeypatch.delenv("DF_SEPARATE_ANALYSIS_CONVERSATIONS", raising=False)
    assert separation_enabled() is False
```

- [ ] **Step 2: Run it and verify red**

Run: `python -m pytest tests/test_execution_context_contract.py::test_separation_feature_flag_defaults_to_legacy -q`

Expected: failure because no explicit gate exists.

- [ ] **Step 3: Implement the flag and documentation**

Gate the new adapter semantics in one backend helper. Candidate deployment uses `1`; production remains at `0` until signed-in validation completes. Document the flag and the rollback behavior.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest -q`

Expected: full suite passes.

Run: `npm run build`

Expected: production frontend bundle succeeds.

- [ ] **Step 5: Candidate runtime acceptance**

Deploy zero-traffic backend and frontend candidates with the flag enabled. In a signed-in candidate workspace: trigger automatic analysis, verify there is a run and latest analysis but no new conversation; then send a real message and verify one conversation is created. Generate one artifact from the latest analysis and verify the job resolves by `source_run_id`. Record the API responses and browser screenshots before any production traffic change.

## Coverage Review

- Automatic analysis has a distinct run, no synthetic conversation, and no `message.create`: Tasks 1-2.
- Data/asset analysis can use the same origin contract: Task 2 supplies `data_send_analysis`; existing caller wiring follows the same request shape.
- Latest analysis and artifacts use canonical run lineage: Tasks 2-3.
- Existing conversations remain available and only explicitly classified system activity may be hidden: Task 3.
- The frontend no longer selects an automatic run as a conversation: Task 4.
- Candidate-first feature-flag rollout and rollback path: Task 5.

