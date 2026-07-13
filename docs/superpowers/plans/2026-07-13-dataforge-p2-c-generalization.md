# DataForge P2-C Generalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize DataForge across business contexts using evidence-driven capability packs, make iteration reflect real experiment/evidence changes, and deliver adaptive onboarding plus persistent branded artifacts.

**Architecture:** Add a data-driven capability-pack registry and selector before collaboration planning. Extend the existing experiment and artifact stores rather than creating parallel version systems. Keep industry/domain terms out of verdict logic; packs only supply questions, metric families, validation methods, and artifact sections.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Azure AI Search/Blob, React/Vite, pytest, Playwright.

## Global Constraints

- Capability packs cannot define conclusions, scores, named opportunities, winners, or preferred industries.
- Pack selection uses business goal, schema roles, metric types, time coverage, entity relationships, and data quality.
- Generated artifacts and synthetic feedback cannot create evidence progress or strengthen a verdict.
- A new experiment version requires new source-linked evidence or a changed decision.
- Brand assets remain workspace-scoped, versioned, and authorization-protected.
- Existing workspace, run, conversation, artifact, and experiment API fields remain backward compatible.
- No UI surface may fall back to `Demo User` in authenticated production.

---

### Task 1: Capability Pack Registry

**Files:**
- Create: `backend/capability_packs.py`
- Create: `backend/data/capability_packs.json`
- Modify: `backend/schemas.py`
- Create: `tests/test_capability_packs.py`

**Interfaces:**
- Produces: `CapabilityPack`, `CapabilitySelection`, `load_capability_packs()`, and `select_capability_packs(goal, schema_profile, quality) -> list[CapabilitySelection]`.
- Consumes: normalized business goal, semantic schema roles, metric types, temporal coverage, entity relationships, and quality summary.

- [ ] **Step 1: Write failing selection and anti-hardcoding tests**

```python
def test_different_schema_shapes_select_different_packs():
    retention = select_capability_packs("reduce churn", retention_schema(), quality())
    sites = select_capability_packs("choose channels", location_schema(), quality())
    assert retention[0].pack_id == "growth_retention"
    assert sites[0].pack_id == "site_channel_selection"


def test_pack_files_do_not_contain_scores_or_named_winners():
    for pack in load_capability_packs():
        text = json.dumps(pack.model_dump(), ensure_ascii=False)
        assert "weighted_score" not in text
        assert "recommended_winner" not in text
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_capability_packs.py -q`

Expected: FAIL because the registry and selector do not exist.

- [ ] **Step 3: Define data-only pack schema**

```python
class CapabilityPack(BaseModel):
    pack_id: str
    label: str
    goal_signals: list[str]
    schema_roles: list[str]
    metric_families: list[str]
    evidence_requirements: list[str]
    validation_methods: list[str]
    artifact_sections: list[str]


class CapabilitySelection(BaseModel):
    pack_id: str
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(min_length=1)
    matched_schema_roles: list[str]
    missing_evidence: list[str]
```

Create six packs from the design. Signals remain generic concepts and semantic roles; the selector does not inspect workspace/file/dataset names.

- [ ] **Step 4: Implement deterministic selector**

Score normalized goal concepts, semantic schema-role overlap, metric-family overlap, temporal suitability, and quality availability. Return up to three packs above a minimum confidence and return `data_readiness` when no opportunity pack has enough evidence.

- [ ] **Step 5: Verify tests**

Run: `python -m pytest tests/test_capability_packs.py -q`

Expected: PASS across at least six schema fixtures and renamed workspace/file fixtures.

- [ ] **Step 6: Commit**

```powershell
git add backend/capability_packs.py backend/data/capability_packs.json backend/schemas.py tests/test_capability_packs.py
git commit -m "feat: select evidence-driven capability packs"
```

---

### Task 2: Integrate Packs Into Routing, MAF, And Artifacts

**Files:**
- Modify: `backend/orchestrator.py`
- Modify: `backend/maf_team_runtime.py`
- Modify: `backend/evidence_bundle.py`
- Modify: `backend/control_plane.py`
- Modify: `backend/run_store.py`
- Modify: `tests/test_maf_team_runtime.py`
- Create: `tests/test_capability_pack_integration.py`

**Interfaces:**
- Consumes: selected capability packs from Task 1.
- Produces: run/artifact `capability_packs`, pack selection trace, and pack-aware evidence questions/sections.

- [ ] **Step 1: Write failing integration tests**

```python
def test_pack_changes_questions_not_verdict_authority():
    low = run_with_pack("site_channel_selection", weak_evidence())
    high = run_with_pack("site_channel_selection", strong_evidence())
    assert low.artifact["capability_packs"][0]["pack_id"] == "site_channel_selection"
    assert verdict_rank(low.artifact["verdict"]) <= verdict_rank(high.artifact["verdict"])
    assert low.artifact["verdict_source"] == "evidence_guard"


def test_renaming_workspace_does_not_change_pack_selection():
    assert select_for(workspace("alpha")) == select_for(workspace("totally-renamed"))
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py -q`

Expected: FAIL because packs are not in route/run/artifact contracts.

- [ ] **Step 3: Add pack selection before collaboration planning**

Use the current workspace data profile and normalized goal. Persist selection IDs, confidence, reasons, and missing evidence in run trace. Add pack IDs to the shared evidence bundle and pass only pack questions/validation methods relevant to each agent.

- [ ] **Step 4: Keep evidence/rubric authority explicit**

Pack content cannot write score fields. Feasibility remains recalculated by the rubric and guarded by verified evidence. Audit checks that pack suggestions are not presented as observed evidence.

- [ ] **Step 5: Verify tests**

Run: `python -m pytest tests/test_capability_pack_integration.py tests/test_maf_team_runtime.py tests/test_agent_generalization_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/orchestrator.py backend/maf_team_runtime.py backend/evidence_bundle.py backend/control_plane.py backend/run_store.py tests/test_maf_team_runtime.py tests/test_capability_pack_integration.py
git commit -m "feat: drive analysis with capability packs"
```

---

### Task 3: Experiment Version Promotion Rules

**Files:**
- Modify: `backend/experiment_store.py`
- Modify: `backend/outcome_store.py`
- Modify: `backend/run_store.py`
- Modify: `backend/orchestrator.py`
- Modify: `tests/test_experiment_versions.py`
- Modify: `tests/test_followup_plan_version.py`
- Modify: `tests/test_artifact_version_snapshot.py`

**Interfaces:**
- Produces: canonical experiment versions with evidence/decision deltas and attachment-only artifact snapshots.
- Consumes: observed/synthetic/target/assumption metrics, source file/connector versions, outcome verification, analysis decisions.

- [ ] **Step 1: Write failing version-promotion tests**

```python
def test_artifact_generation_attaches_without_new_experiment_version():
    before = versions("ws")
    attach_artifact("ws", before[-1].version_id, artifact())
    after = versions("ws")
    assert len(after) == len(before)
    assert after[-1].artifacts


def test_observed_feedback_creates_new_version_only_after_decision_reanalysis():
    record_outcome(observed_metric(source_file_id="f-v2"))
    assert len(versions("ws")) == 1
    persist_analysis_decision(next_analysis())
    assert len(versions("ws")) == 2


def test_synthetic_feedback_cannot_strengthen_verdict():
    v2 = build_next_version(v1(), metrics=[synthetic_metric()], decision=stronger_decision())
    assert verdict_rank(v2.verdict) <= verdict_rank(v1().verdict)
```

- [ ] **Step 2: Run red tests**

Run: `python -m pytest tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py -q`

Expected: at least one test fails on current attachment/version behavior.

- [ ] **Step 3: Implement explicit version triggers**

Version creation requires a completed analysis decision plus either changed source-linked evidence or a changed normalized decision. Artifact, roadmap, and validation-plan generation only append artifact references to the current version. Duplicate evidence and unchanged decisions record no new version.

- [ ] **Step 4: Extend deterministic deltas**

Compare evidence by stable source/file/connector/version reference and decision fields by normalized values. Deltas include added, removed, contradicted, strengthened, unchanged, and unverifiable. Store human-readable reasons generated from actual field/evidence differences, not model narrative alone.

- [ ] **Step 5: Verify tests**

Run: `python -m pytest tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/experiment_store.py backend/outcome_store.py backend/run_store.py backend/orchestrator.py tests/test_experiment_versions.py tests/test_followup_plan_version.py tests/test_artifact_version_snapshot.py
git commit -m "feat: promote only evidence-backed experiments"
```

---

### Task 4: Experiment-Centered Iteration UI

**Files:**
- Modify: `web/src/api.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Create: `web/src/experimentViewModel.js`
- Create: `web/src/experimentViewModel.test.mjs`

**Interfaces:**
- Consumes: canonical experiment versions, evidence/decision deltas, outcomes, artifacts.
- Produces: truthful comparison, feedback intake, source links, and experiment/artifact navigation.

- [ ] **Step 1: Write failing view-model tests**

```javascript
it("does not describe attachment-only change as evidence progress", () => {
  const model = experimentViewModel({evidence_delta: {changed: false}, decision_delta: {changed: false}, artifacts: [{kind: "pdf"}]});
  expect(model.progressLabel).toBe("产物已更新，证据与决策未变化");
});

it("shows observed and synthetic metrics separately", () => {
  const model = experimentViewModel(versionWithMixedMetrics());
  expect(model.observedMetrics).toHaveLength(1);
  expect(model.syntheticMetrics).toHaveLength(1);
});
```

- [ ] **Step 2: Implement the iteration surface**

Show hypothesis, decision, verdict, metric kind, source, observation time, verification, evidence changes, decision changes, unchanged fields, and linked artifacts. Empty comparisons state that no new source-linked evidence exists.

- [ ] **Step 3: Verify build and browser behavior**

Run: `node --test src/experimentViewModel.test.mjs` from `web`.

Run: `npm run build` from `web`.

Use Playwright for V1 only, artifact attachment, synthetic feedback, observed feedback pending reanalysis, and V2 decision-delta states on desktop/mobile.

- [ ] **Step 4: Commit**

```powershell
git add web/src/api.js web/src/components.jsx web/src/styles.css web/src/experimentViewModel.js web/src/experimentViewModel.test.mjs
git commit -m "feat: show evidence-centered plan iteration"
```

---

### Task 5: Adaptive Onboarding Contract

**Files:**
- Create: `backend/onboarding.py`
- Modify: `backend/workspace_store.py`
- Modify: `backend/app.py`
- Modify: `backend/control_plane.py`
- Create: `tests/test_onboarding.py`
- Modify: `web/src/components.jsx`
- Modify: `web/src/api.js`

**Interfaces:**
- Produces: `OnboardingProfile`, profile API, and capability-pack recommendations without industry templates.
- Consumes: user-provided business goal, decision, audience, available data, sensitivity, horizon, and validation outcome.

- [ ] **Step 1: Write failing profile tests**

```python
def test_onboarding_profile_has_decision_and_validation_goal():
    profile = OnboardingProfile.model_validate(valid_payload())
    assert profile.decision_to_support
    assert profile.validation_outcome


def test_onboarding_does_not_require_industry():
    payload = valid_payload()
    payload.pop("industry", None)
    assert OnboardingProfile.model_validate(payload)
```

- [ ] **Step 2: Implement profile and API**

Store the profile in workspace metadata with actor/time/version. Sensitive-field descriptions are labels only; users are warned not to paste secrets or personal records. Profile updates do not change verdicts until a new analysis runs.

- [ ] **Step 3: Update onboarding UI**

Collect goal, decision, audience, data availability, sensitive-field categories, horizon, and validation outcome. Do not present industry presets. Show capability-pack suggestions after files are profiled.

- [ ] **Step 4: Verify tests and build**

Run: `python -m pytest tests/test_onboarding.py -q`

Run: `npm run build` from `web`.

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/onboarding.py backend/workspace_store.py backend/app.py backend/control_plane.py tests/test_onboarding.py web/src/components.jsx web/src/api.js
git commit -m "feat: add decision-focused workspace onboarding"
```

---

### Task 6: Artifact Registry And Brand Profiles

**Files:**
- Create: `backend/brand_store.py`
- Modify: `backend/artifact_jobs.py`
- Modify: `backend/tools/render_pdf.py`
- Modify: `backend/tools/generate_image.py`
- Modify: `backend/control_plane.py`
- Modify: `web/src/api.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/styles.css`
- Create: `tests/test_brand_store.py`
- Modify: `tests/test_artifact_jobs.py`
- Create: `tests/test_render_pdf.py`

**Interfaces:**
- Produces: versioned brand profiles and richer artifact metadata linked to experiment/run/task.
- Consumes: workspace-scoped authorized brand assets, artifact job, experiment version, capability packs.

- [ ] **Step 1: Write failing brand and metadata tests**

```python
def test_brand_asset_is_workspace_scoped_and_versioned():
    profile = create_brand_profile("ws-1", asset(), placement={"document": True, "image": False}, actor=owner())
    assert profile["version"] == 1
    with pytest.raises(PermissionError):
        read_brand_profile("ws-2", profile["profile_id"], outsider())


def test_artifact_name_and_metadata_follow_opportunity_and_version():
    artifact = completed_artifact_job(title="Location intelligence pilot", version="V2")
    assert "V2" in artifact["display_name"]
    assert artifact["experiment_version_id"]
    assert artifact["brand_profile_version"] == 1
```

- [ ] **Step 2: Implement brand profile store**

Profiles contain authorized asset references, document/image placement choices, display name, version, actor, and timestamps. Assets remain in the existing workspace reference-image storage and never become arbitrary filesystem paths.

- [ ] **Step 3: Extend artifact records and rendering**

Persist customer title, version, capability packs, experiment version, source run, task ID, brand profile version, summary, status, URL, and generated time. PDF/image tools receive only sanitized authorized asset URLs. Transparent logos are composited for the target background; logo placement is omitted when disabled.

- [ ] **Step 4: Update artifact center**

Show persistent artifact versions, summary, source analysis/experiment, brand profile, generation task, status, and downloads. Refresh/re-login reconstructs the same registry from backend data.

- [ ] **Step 5: Verify tests, PDF rendering, and build**

Run: `python -m pytest tests/test_brand_store.py tests/test_artifact_jobs.py tests/test_render_pdf.py -q`

Run: `npm run build` from `web`.

Render a PDF and concept image with and without a transparent logo and visually inspect desktop/mobile artifact cards plus PDF page images.

- [ ] **Step 6: Commit**

```powershell
git add backend/brand_store.py backend/artifact_jobs.py backend/tools/render_pdf.py backend/tools/generate_image.py backend/control_plane.py web/src/api.js web/src/components.jsx web/src/styles.css tests/test_brand_store.py tests/test_artifact_jobs.py tests/test_render_pdf.py
git commit -m "feat: version branded artifact delivery"
```

---

### Task 7: P2-C Multi-Domain Evaluation And Integration Gate

**Files:**
- Create: `eval/run_capability_pack_eval.py`
- Create: `eval/capability_pack_cases.json`
- Create: `tests/test_p2_c_acceptance_contract.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- Produces: pack-selection, groundedness, verdict calibration, experiment, onboarding, and artifact acceptance report.

- [ ] **Step 1: Build evaluation cases**

Use at least five domain-neutral data shapes and renamed workspace/file variants. Each case defines expected pack families, required evidence gaps, prohibited hardcoded outcomes, and weak/strong evidence variants.

- [ ] **Step 2: Run deterministic evaluation**

Run: `python eval/run_capability_pack_eval.py --cases eval/capability_pack_cases.json --output generated-outputs/p2-c-eval.json`

Expected: renamed fixtures select the same packs; weak evidence never produces a stronger verdict than corresponding strong evidence; no generated artifact creates evidence progress.

- [ ] **Step 3: Run full regression**

Run: `python -m pytest -q`

Run: `python -m compileall -q backend tests eval`

Run: `npm run build` from `web`.

Run: `git diff --check`

Expected: all exit 0.

- [ ] **Step 4: Commit**

```powershell
git add eval/run_capability_pack_eval.py eval/capability_pack_cases.json tests/test_p2_c_acceptance_contract.py README.md README.zh-CN.md
git commit -m "docs: publish P2-C generalization gates"
```
