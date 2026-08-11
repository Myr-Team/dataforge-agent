# Shenzhen Site-Selection Demo Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate one deterministic Shenzhen site-selection dataset that reconciles DataForge runs, FinOps, Trace, evidence, operations signals, and ROI screens.

**Architecture:** A pure generator builds a typed synthetic bundle from a fixed manifest; the existing demo initializer persists it through current repositories. Existing query, pricing, anomaly, evidence, and ROI services calculate user-visible results.

**Tech Stack:** Python dataclasses/Pydantic, existing FinOps repositories/services, existing run store, pytest, React/Playwright acceptance.

## Global Constraints

- Only the allowlisted `demo-corpus` workspace may receive generated records.
- Every generated fact must carry `provenance=synthetic_demo`, scenario ID, batch ID, and stable safe references.
- Do not generate secrets, real PII, raw provider bodies, complete prompts, or production identity.
- Risk findings must be produced by existing rules, not written as final findings by the generator.
- Scenario, measured, and demo-verified evidence must remain distinct; synthetic verification must be labeled and cannot become a production-quality customer claim.
- Do not modify APIM, Terraform, deployment configuration, production traffic, or external cloud resources.
- Use UTF-8 Python and `ensure_ascii=False` for Chinese generated metadata.
- Use TDD for every behavior.

---

### Task 1: Typed deterministic bundle and reconciliation

**Files:**
- Create: `backend/finops/synthetic_demo.py`
- Create: `backend/finops/synthetic_demo_manifest.json`
- Create: `tests/test_finops_synthetic_demo.py`

**Interfaces:**
- Produces: `build_synthetic_demo_bundle(*, workspace_id, batch_id, anchor_at, seed) -> SyntheticDemoBundle` and `reconcile_synthetic_demo(bundle) -> ReconciliationReport`.

- [ ] Add failing tests for the exact 96 tasks, 2,480 request facts, 78 reports, 18 review tasks, fixed digest, unique references, token identities, USD cost total, and rejection of non-demo workspace IDs.
- [ ] Verify RED.
- [ ] Implement immutable typed records and canonical JSON digest excluding volatile generation timestamps.
- [ ] Generate only Shenzhen site-selection titles, actions, departments, Agents, and artifacts.
- [ ] Implement fail-closed reconciliation for request/status totals, department/Agent/model sums, `input + output = total`, reasoning/output and cached/input bounds, costs, evidence existence, distinct verification actors, and ROI currency/window consistency.
- [ ] Run focused tests and commit as `feat(finops): generate reconciled Shenzhen demo facts`.

### Task 2: Safe run, model, cache, pricing, and Trace projection

**Files:**
- Modify: `backend/finops/synthetic_demo.py`
- Modify: `backend/finops/demo_workspace_seed.py`
- Test: `tests/test_finops_demo_workspace_seed.py`
- Test: `tests/test_local_agent_observation.py`

**Interfaces:**
- Consumes: accepted L0 safe model-observation contract.
- Produces: safe run steps/model attempts and request ledger facts with shared `request_ref`, `run_id`, `correlation_ref`, and `attempt_ref`.

- [ ] Add failing tests requiring each request to resolve to a run, each run to contain a safe step/model attempt, and Trace usage/route/cache to reconcile with the ledger.
- [ ] Verify RED.
- [ ] Project Foundry/DeepSeek synthetic model attempts without pretending route evidence is observed.
- [ ] Map every priced attempt to an exact repository catalog key/revision; leave unmatched attempts unpriced.
- [ ] Generate separate provider-cache and result-cache evidence, including a repeatable miss-to-hit pair with a source result/version.
- [ ] Persist through existing repositories only from the allowlisted demo initializer.
- [ ] Run focused tests and commit as `feat(finops): bind demo ledger to run traces`.

### Task 3: ROI evidence states and operations signals

**Files:**
- Modify: `backend/finops/synthetic_demo.py`
- Modify: `backend/finops/demo_workspace_seed.py`
- Test: `tests/test_finops_roi.py`
- Test: `tests/test_finops_anomaly_rules.py`
- Test: `tests/test_finops_demo_workspace_seed.py`

**Interfaces:**
- Consumes: generated ledger/run bundle.
- Produces: scenario, measured, and synthetic independent-review inputs plus expected scanner reconciliation.

- [ ] Add failing tests for the specified scenario amounts/293.3% result, 18 paired tasks, 17.8h/8.1h process time, 174.6h reviewed savings, and distinct outcome/reviewer actors.
- [ ] Require synthetic verified presentation to carry `production_quality_claim=false` and the user-visible synthetic label.
- [ ] Generate request distributions that cause all seven existing operations rules to trigger in their expected category and sample range.
- [ ] Run the existing scanner; compare its findings to expected rule IDs, sample counts, thresholds, and real evidence refs. Do not insert final findings directly.
- [ ] Add failure tests for missing evidence, price mapping, cache source, currency/window mismatch, and actor collision.
- [ ] Commit as `feat(finops): add demo ROI and operations evidence`.

### Task 4: Demo initialization, AI knowledge, and end-to-end acceptance

**Files:**
- Modify: `backend/finops/demo_initialize.py`
- Modify: relevant allowlisted `data/knowledge/*.md` file or create one scoped Shenzhen operations guide
- Modify: `tests/test_finops_demo_initialize.py`
- Modify: `web/tests/finops-operations-management.spec.mjs`
- Modify: `web/tests/operations-governance-closure.spec.mjs`

**Interfaces:**
- Consumes: complete synthetic bundle and existing APIs.
- Produces: one demo initialization command plus browser/API acceptance evidence.

- [ ] Add failing tests proving idempotent batch replacement, no writes outside `demo-corpus`, and identical reconciliation after two initializations.
- [ ] Add a short internal knowledge document explaining cost, cache, evidence, ROI boundaries, and site-selection operations signals; it may explain evidence but never substitute for current metric facts.
- [ ] Extend browser fixtures/acceptance to require populated overview, cost, ROI, risk, request evidence, run Trace, pricing, provider/result cache, and context-bound operations AI responses.
- [ ] Verify every visible demo metric has a value or an intentional evidence-state explanation; no production JSX may hardcode the final aggregate.
- [ ] Run focused Python/Node/Playwright tests, full Python and Node suites, Vite build, deterministic generation twice, and `git diff --check`.
- [ ] Commit as `test(finops): verify Shenzhen demo evidence closure`.

