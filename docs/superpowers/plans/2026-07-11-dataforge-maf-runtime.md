# DataForge MAF Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a progressively enabled first-class Microsoft Agent Framework team runtime with direct, concurrent, handoff, and bounded-review collaboration, truthful telemetry, UI visibility, and a single legacy fallback.

**Architecture:** Keep the current preflight routing, evidence controls, persistence, and artifact contracts. Add typed MAF contracts, a prompt-backed Foundry agent registry, and a `MafTeamRuntime` selected behind `DF_MAF_RUNTIME`; normalize its events into existing SSE/run-store contracts and preserve the current orchestrator as fallback.

**Tech Stack:** Python 3.12, FastAPI, `agent-framework-core==1.8.1`, `agent-framework-foundry==1.8.1`, Azure Foundry Responses API, OpenTelemetry/Application Insights, React/Vite.

## Global Constraints

- `DF_MAF_RUNTIME` values are exactly `off`, `audit`, and `full`.
- `DF_USE_MAF=1` maps to `audit` only when `DF_MAF_RUNTIME` is absent.
- `DF_MAF_TRAFFIC_PERCENT` is clamped to the inclusive range `0..100` and uses a stable workspace/conversation hash.
- Routing must not use business names, file names, dataset names, industry names, or demo scenario allowlists.
- Audit/revision is capped at two rounds.
- Existing SSE events and API response fields remain backward compatible.
- New telemetry must not record raw prompts, user messages, evidence rows, connector credentials, or actor email.
- MAF runtime failure falls back to the legacy path at most once.
- Optional market failure degrades; required corpus failure cannot support a stronger verdict.
- Do not change authentication or workspace authorization.

---

### Task 1: Runtime Configuration, Contracts, and UTF-8 Metadata

**Files:**
- Create: `backend/maf_contracts.py`
- Modify: `backend/maf_orchestrator.py`
- Test: `tests/test_maf_contracts.py`

**Interfaces:**
- Produces: `MafRuntimeMode`, `CollaborationPattern`, `CollaborationPlan`, `MafAgentRecord`, `MafRunSummary`, `runtime_mode()`, `traffic_percent()`, and `canary_selected(workspace_id, conversation_id)`.
- Preserves: `graph_description(max_revisions)` with UTF-8-clean labels.

- [ ] **Step 1: Write failing contract and configuration tests**

```python
def test_legacy_flag_maps_only_to_audit(monkeypatch):
    monkeypatch.delenv("DF_MAF_RUNTIME", raising=False)
    monkeypatch.setenv("DF_USE_MAF", "1")
    assert runtime_mode() is MafRuntimeMode.AUDIT

def test_explicit_runtime_overrides_legacy_flag(monkeypatch):
    monkeypatch.setenv("DF_MAF_RUNTIME", "full")
    monkeypatch.setenv("DF_USE_MAF", "0")
    assert runtime_mode() is MafRuntimeMode.FULL

def test_canary_selection_is_stable(monkeypatch):
    monkeypatch.setenv("DF_MAF_TRAFFIC_PERCENT", "37")
    first = canary_selected("workspace-a", "conversation-a")
    assert first == canary_selected("workspace-a", "conversation-a")

def test_graph_description_is_utf8_clean():
    raw = json.dumps(graph_description(2), ensure_ascii=False)
    assert "审计" in raw
    assert "复修" in raw
    assert "Ã" not in raw and "å®" not in raw and "?" not in raw
```

- [ ] **Step 2: Run tests and verify the expected import/assertion failures**

Run: `python -m pytest tests/test_maf_contracts.py -q`

Expected: FAIL because `backend.maf_contracts` does not exist and the current graph metadata is not clean.

- [ ] **Step 3: Implement typed contracts and deterministic configuration**

```python
class MafRuntimeMode(str, Enum):
    OFF = "off"
    AUDIT = "audit"
    FULL = "full"

class CollaborationPattern(str, Enum):
    DIRECT = "direct"
    CONCURRENT_RESEARCH = "concurrent_research"
    SPECIALIST_HANDOFF = "specialist_handoff"
    BOUNDED_REVIEW = "bounded_review"

def canary_selected(workspace_id: str, conversation_id: str) -> bool:
    digest = hashlib.sha256(f"{workspace_id}:{conversation_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return bucket < traffic_percent()
```

Use Pydantic models for collaboration plans, agent records, and run summaries. Replace corrupted MAF description literals with valid UTF-8 Chinese text.

- [ ] **Step 4: Run focused and full backend tests**

Run: `python -m pytest tests/test_maf_contracts.py -q`

Expected: PASS.

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/maf_contracts.py backend/maf_orchestrator.py tests/test_maf_contracts.py
git commit -m "feat: add typed MAF runtime contracts"
```

### Task 2: First-Class Agent Registry and Tool Boundaries

**Files:**
- Create: `backend/maf_agents.py`
- Modify: `backend/requirements.txt`
- Test: `tests/test_maf_agents.py`

**Interfaces:**
- Consumes: the existing prompt files under `agents/prompts/` and `FOUNDRY_PROJECT_ENDPOINT`, `DF_CHAT_DEPLOYMENT`.
- Produces: `MafAgentRegistry`, `AgentSpec`, `create_agent_registry()`, and role-scoped agent instances keyed by the six existing agent IDs.

- [ ] **Step 1: Write failing registry tests**

```python
def test_registry_uses_existing_prompt_files(fake_foundry_client):
    registry = create_agent_registry(client_factory=lambda spec: fake_foundry_client(spec))
    assert set(registry.ids()) == {
        "df-coordinator", "df-corpus-analyst", "df-market-researcher",
        "df-feasibility-analyst", "df-auditor", "df-producer",
    }
    assert "evidence" in registry.spec("df-auditor").instructions.lower()

def test_each_agent_receives_only_scoped_tools(fake_foundry_client):
    registry = create_agent_registry(client_factory=lambda spec: fake_foundry_client(spec))
    assert registry.spec("df-coordinator").tool_names == ()
    assert "search_pack_context" in registry.spec("df-corpus-analyst").tool_names
    assert "market_lookup_mcp" in registry.spec("df-market-researcher").tool_names
    assert "generate_image" not in registry.spec("df-auditor").tool_names
```

- [ ] **Step 2: Run tests and verify missing-module failures**

Run: `python -m pytest tests/test_maf_agents.py -q`

Expected: FAIL because `backend.maf_agents` does not exist.

- [ ] **Step 3: Add the Foundry provider dependency and registry**

Add `agent-framework-foundry==1.8.1` beside `agent-framework-core==1.8.1`.

```python
@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    prompt_file: str
    tool_names: tuple[str, ...]
    description: str

class MafAgentRegistry:
    def spec(self, agent_id: str) -> AgentSpec: ...
    def agent(self, agent_id: str) -> Agent: ...
    def ids(self) -> tuple[str, ...]: ...
```

Construct first-class `Agent` instances with `FoundryChatClient`, `DefaultAzureCredential`, the project endpoint, deployed model, prompt text, name, description, and scoped local/MCP tools. Allow a client factory for deterministic tests without Azure calls.

- [ ] **Step 4: Verify dependency resolution and tests**

Run: `python -m pip install -r backend/requirements.txt`

Expected: compatible `agent-framework-core` and `agent-framework-foundry` 1.8.1 packages install.

Run: `python -m pytest tests/test_maf_agents.py -q`

Expected: PASS without network access.

- [ ] **Step 5: Commit**

```powershell
git add backend/maf_agents.py backend/requirements.txt tests/test_maf_agents.py
git commit -m "feat: add first-class MAF agent registry"
```

### Task 3: Collaboration Selector and Team Runtime

**Files:**
- Create: `backend/maf_team_runtime.py`
- Test: `tests/test_maf_team_runtime.py`

**Interfaces:**
- Consumes: `MafAgentRegistry`, `CollaborationPlan`, normalized intent, output mode, risk level, workspace-evidence requirement, and external-signal requirement.
- Produces: `select_collaboration_plan(...)`, `MafTeamRuntime.run(...)`, ordered runtime events, normalized branch results, and a `MafRunSummary`.

- [ ] **Step 1: Write failing selector and runtime tests**

```python
def test_simple_question_selects_direct_without_specialists():
    plan = select_collaboration_plan(intent="qa", output_mode="chat", needs_workspace=True, needs_external=False, high_impact=False)
    assert plan.pattern is CollaborationPattern.DIRECT
    assert plan.selected_agents == ("df-coordinator",)

@pytest.mark.asyncio
async def test_internal_and_external_research_run_concurrently(fake_registry):
    runtime = MafTeamRuntime(fake_registry)
    result = await runtime.run(concurrent_request())
    assert result.summary.mode == "concurrent_research"
    assert result.branch_overlap_ms > 0
    assert result.completed_agents == {"df-corpus-analyst", "df-market-researcher", "df-feasibility-analyst", "df-auditor"}

@pytest.mark.asyncio
async def test_optional_market_failure_degrades_without_losing_corpus(fake_registry):
    fake_registry.fail("df-market-researcher", TransientAgentError("timeout"))
    result = await MafTeamRuntime(fake_registry).run(concurrent_request())
    assert result.degraded is True
    assert result.artifact["hits"]
    assert "external_signal_unavailable" in result.gaps

@pytest.mark.asyncio
async def test_review_loop_stops_at_two_revisions(fake_registry):
    fake_registry.audit_verdicts("revise", "revise", "revise")
    result = await MafTeamRuntime(fake_registry, max_revisions=2).run(review_request())
    assert result.summary.rounds == 2
    assert result.events[-1].event == "maf_review"
```

- [ ] **Step 2: Run tests and verify missing-runtime failures**

Run: `python -m pytest tests/test_maf_team_runtime.py -q`

Expected: FAIL because the selector and runtime do not exist.

- [ ] **Step 3: Implement selector and bounded collaboration patterns**

Implement direct, concurrent research, specialist handoff, and bounded review. Use MAF agents as participants. Use `asyncio.gather` through the MAF concurrent orchestration path for independent corpus and market branches. Emit typed runtime events before and after each participant, branch, handoff, and review decision.

Selection rules use semantic route fields only:

```python
if output_mode == "chat" and not high_impact and not needs_external:
    return direct_plan()
if needs_workspace and needs_external:
    return concurrent_research_plan(high_impact=high_impact)
if high_impact:
    return bounded_review_plan()
return specialist_handoff_plan(intent)
```

- [ ] **Step 4: Run focused runtime tests**

Run: `python -m pytest tests/test_maf_team_runtime.py -q`

Expected: PASS, including overlap, degradation, handoff, and revision-cap assertions.

- [ ] **Step 5: Commit**

```powershell
git add backend/maf_team_runtime.py tests/test_maf_team_runtime.py
git commit -m "feat: add dynamic MAF team runtime"
```

### Task 4: Orchestrator Integration, Fallback, Run Store, and Telemetry

**Files:**
- Modify: `backend/orchestrator.py`
- Modify: `backend/run_store.py`
- Modify: `backend/schemas.py`
- Modify: `backend/tracing.py`
- Test: `tests/test_maf_integration.py`
- Test: `tests/test_tracing_telemetry.py`

**Interfaces:**
- Consumes: `runtime_mode()`, `canary_selected()`, `MafTeamRuntime`, and existing routing/artifact contracts.
- Produces: backward-compatible SSE frames, persisted `maf` summaries, per-agent OpenTelemetry spans, and a one-shot legacy fallback.

- [ ] **Step 1: Write failing integration and fallback tests**

```python
@pytest.mark.asyncio
async def test_full_mode_emits_new_and_legacy_compatible_events(monkeypatch):
    events = await collect_frames(full_mode_request(), fake_team_runtime())
    assert "maf_plan" in event_names(events)
    assert "maf_agent_started" in event_names(events)
    assert "role_change" in event_names(events)
    assert event_names(events).count("final") == 1

@pytest.mark.asyncio
async def test_runtime_failure_falls_back_exactly_once(monkeypatch):
    team = failing_team_runtime(RuntimeError("workflow construction failed"))
    legacy = CountingLegacyRunner()
    events = await collect_frames(full_mode_request(), team, legacy)
    assert event_names(events).count("maf_fallback") == 1
    assert legacy.calls == 1
    assert event_names(events).count("final") == 1

def test_maf_summary_comes_from_real_events():
    summary = summarize_maf_run(run_with_maf_events())
    assert summary["mode"] == "specialist_handoff"
    assert summary["selected_agents"] == ["df-coordinator", "df-feasibility-analyst"]
    assert summary["fallback"] is False
```

Extend telemetry tests to assert hashed actor identity, no raw user text, and per-agent `gen_ai.agent.id`, collaboration mode, branch ID, and handoff attributes.

- [ ] **Step 2: Run focused tests and verify expected failures**

Run: `python -m pytest tests/test_maf_integration.py tests/test_tracing_telemetry.py -q`

Expected: FAIL because the new integration and telemetry attributes are absent.

- [ ] **Step 3: Integrate the new runtime behind flags**

Add a narrow integration function instead of expanding `_orchestrate_chat_impl` with another large block:

```python
async def _try_full_maf_runtime(req, decision, artifact, conversation_id):
    if runtime_mode() is not MafRuntimeMode.FULL:
        return None
    if not canary_selected(req.workspace_id, conversation_id):
        return None
    return await MafTeamRuntime(create_agent_registry()).run(...)
```

Translate typed events into SSE frames and existing compatibility events. Persist agent records and summaries. Wrap each participant in a redacted child span. On runtime failure emit one fallback event and invoke the legacy path once.

- [ ] **Step 4: Run focused and full backend tests**

Run: `python -m pytest tests/test_maf_integration.py tests/test_tracing_telemetry.py -q`

Expected: PASS.

Run: `python -m pytest -q`

Expected: all backend tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/orchestrator.py backend/run_store.py backend/schemas.py backend/tracing.py tests/test_maf_integration.py tests/test_tracing_telemetry.py
git commit -m "feat: integrate MAF runtime with fallback telemetry"
```

### Task 5: Agent Flow and Run Trace Visibility

**Files:**
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Test: `tests/test_ui_truthfulness_contract.py`

**Interfaces:**
- Consumes: `maf_plan`, `maf_agent_*`, `maf_branch_*`, `maf_handoff`, `maf_review`, `maf_fallback`, and persisted `maf` summary.
- Produces: actual collaboration-mode rendering in Agent Flow and grouped per-agent details in run trace.

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_agent_flow_reads_dynamic_maf_events():
    source = COMPONENTS.read_text(encoding="utf-8")
    for event in ("maf_plan", "maf_agent_started", "maf_branch_joined", "maf_handoff", "maf_review", "maf_fallback"):
        assert event in source

def test_maf_ui_does_not_render_fixed_participant_success():
    source = COMPONENTS.read_text(encoding="utf-8")
    assert "selected_agents" in source
    assert "skipped_agents" in source
```

- [ ] **Step 2: Run tests and verify missing-event failures**

Run: `python -m pytest tests/test_ui_truthfulness_contract.py -q`

Expected: FAIL because the new event names and dynamic participant rendering are absent.

- [ ] **Step 3: Implement backward-compatible dynamic rendering**

Add pure helper functions that derive a view model from trace events. Render segmented mode labels, parallel branch lanes, handoff arrows, review rounds, and a restrained fallback notice. Use existing Lucide icons and existing MAF styles; do not add decorative cards or fixed success labels. Older `maf_workflow` runs keep their current rendering.

- [ ] **Step 4: Run UI contract tests and build**

Run: `python -m pytest tests/test_ui_truthfulness_contract.py -q`

Expected: PASS.

Run: `npm run build`

Working directory: `web`

Expected: Vite build completes with exit code 0.

- [ ] **Step 5: Commit**

```powershell
git add web/src/components.jsx web/src/styles.css tests/test_ui_truthfulness_contract.py
git commit -m "feat: visualize dynamic MAF collaboration"
```

### Task 6: Evaluation Gate, Production Canary, and Documentation

**Files:**
- Create: `eval/run_maf_runtime_eval.py`
- Create: `eval/maf_runtime_cases.json`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Test: `tests/test_maf_evaluation_contract.py`

**Interfaces:**
- Consumes: legacy and full MAF execution entrypoints.
- Produces: JSON comparison containing selection accuracy, groundedness, unsupported-claim rate, latency, token cost, task completion, fallback rate, and per-case evidence.

- [ ] **Step 1: Write failing evaluation-contract tests**

```python
def test_eval_cases_cover_all_collaboration_patterns():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert {case["expected_pattern"] for case in cases} == {
        "direct", "concurrent_research", "specialist_handoff", "bounded_review"
    }

def test_eval_report_requires_truthful_metrics():
    required = {"selection_accuracy", "groundedness", "unsupported_claim_rate", "latency_ms", "tokens", "task_completion", "fallback_rate"}
    assert required <= set(empty_report_schema()["metrics"])
```

- [ ] **Step 2: Run tests and verify missing-evaluation failures**

Run: `python -m pytest tests/test_maf_evaluation_contract.py -q`

Expected: FAIL because the evaluation assets do not exist.

- [ ] **Step 3: Implement the deterministic evaluation runner and documentation**

Cases must be schema- and evidence-driven, contain no dataset-name routing triggers, and include weak evidence, missing optional market, ambiguous follow-up, high-impact conclusion, and forced runtime failure. Report measured values without replacing missing values with optimistic defaults.

Update both READMEs with the exact runtime modes, canary control, fallback behavior, supported collaboration patterns, and the statement that Magentic and Hosted Agents are not enabled.

- [ ] **Step 4: Run all verification**

Run: `python -m pytest -q`

Expected: all backend and contract tests pass.

Run: `python eval/run_maf_runtime_eval.py --mode deterministic --output generated-outputs/maf-runtime-eval.json`

Expected: report contains all required metrics and four collaboration patterns.

Run: `npm run build`

Working directory: `web`

Expected: exit code 0.

- [ ] **Step 5: Build and deploy a 10% canary after code review**

Build immutable backend and frontend image tags from the final commit. Deploy backend with `DF_MAF_RUNTIME=full` and `DF_MAF_TRAFFIC_PERCENT=10`. Verify system status, one run per collaboration pattern, no raw-text telemetry leakage, one forced fallback, and matching Agent Flow/run-record facts before increasing traffic.

- [ ] **Step 6: Commit**

```powershell
git add eval/run_maf_runtime_eval.py eval/maf_runtime_cases.json README.md README.zh-CN.md tests/test_maf_evaluation_contract.py
git commit -m "test: add MAF runtime evaluation gate"
```
