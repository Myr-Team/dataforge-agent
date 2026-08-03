# DataForge FinOps Governance Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved cost-management and risk-optimization split, deterministic read-only risk scans, subject-specific evidence sets, and context-triggered structured Operations AI.

**Architecture:** Keep request facts, anomaly evaluation, authorization, and governance actions in their existing services. Add a focused evidence selector and a persisted risk-scan service, expose bounded API projections, and split the frontend into a shared shell with separate cost and risk entry modes. AI remains an explanation layer and receives only allowlisted evidence.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, Azure SQL additive schema, Redis-backed existing FinOps cache, React 19, Vite 8, Node test runner, Playwright.

## Global Constraints

- Preserve MAF and the existing request ledger, pricing, ROI, anomaly, authorization, and approval boundaries.
- Keep `DF_FINOPS_ACTIONS_ENABLED=0`; a scan never creates or executes a governance action.
- Derive tenant and workspace authorization from trusted server claims; never accept tenant from the client.
- Customer UI says “统一入口”, not the underlying gateway product name.
- Do not collect or expose full prompts, provider responses, secrets, raw identities, cache keys, or internal error bodies.
- Seed rich scenarios only for `demo-corpus`; other workspaces retain honest unavailable and insufficient states.
- Use additive SQL only and do not switch production traffic without a new explicit approval.
- Follow test-first RED → GREEN → REFACTOR for every behavior change.

---

## File Structure

- Create `backend/finops/evidence_selection.py`: deterministic subject-to-request evidence selection and bounded public summaries.
- Create `backend/finops/risk_scans.py`: scan models, in-memory repository contract, scan execution service, and public projections.
- Create `backend/finops/sql_risk_scans.py`: Azure SQL persistence for scans and findings.
- Modify `backend/sql/finops_schema.sql`: additive scan tables and indexes.
- Modify `backend/finops/router.py`: evidence-set projection, scan service wiring and endpoints, latest-scan risk decision integration.
- Modify `backend/finops/assistant.py`: structured answer schema and safe projection.
- Modify `backend/finops/demo_initialize.py` and `backend/finops/demo_workspace_seed.py`: initial demo scan and internally consistent evidence scenarios.
- Modify `backend/finops/candidate_acceptance.py`: scan, evidence distinction, and structured AI gates.
- Create `tests/test_finops_evidence_selection.py` and `tests/test_finops_risk_scans.py`.
- Modify existing FinOps API, assistant, demo, migration, decision, and candidate tests.
- Create `web/src/finopsEvidence.js`: evidence selection normalization and drawer state helpers.
- Create `web/src/finopsAskIntent.js`: deterministic default questions and one-shot interaction ids.
- Create `web/src/finops/StructuredAssistantAnswer.jsx`: semantic structured reply renderer.
- Create `web/src/finops/RiskScanStatus.jsx`: scan CTA, status, and rule-basis disclosure.
- Modify `web/src/constants.js`, `web/src/components.jsx`, `web/src/FinOpsPortal.jsx`, `web/src/FinOpsAssistant.jsx`, `web/src/api.js`, `web/src/finopsViewModel.js`, `web/src/finopsDecisionViewModel.js`, `web/src/finops/RiskDecisionPage.jsx`, and `web/src/styles.css`.
- Add or modify Node tests and Playwright acceptance for navigation, evidence, scan, AI and responsive layout.

---

### Task 1: Subject-specific evidence selection

**Files:**
- Create: `backend/finops/evidence_selection.py`
- Create: `tests/test_finops_evidence_selection.py`
- Modify: `backend/finops/router.py`
- Modify: `tests/test_finops_decision_api.py`

**Interfaces:**
- Produces: `EvidenceSubject`, `EvidenceItem`, `EvidenceSet`, `select_metric_evidence(events, metric_id, limit=3)`, `select_policy_evidence(events, policy_type, limit=3)`, and `public_evidence_summary(event, *, signal)`.
- Consumes: `FinOpsRequestEvent` and existing evidence alias naming.

- [ ] **Step 1: Write failing selector tests**

```python
def test_policy_selectors_return_semantic_and_distinct_first_refs(events):
    refs = {
        policy: select_policy_evidence(events, policy).items[0].request_ref
        for policy in ("p95_latency", "error_rate", "unpriced_requests", "cache_hit_rate", "token_spike", "apim_coverage")
    }
    assert len(set(refs.values())) == 6
    assert select_policy_evidence(events, "cache_hit_rate").items[0].cache_state in {"miss", "bypassed"}
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_finops_evidence_selection.py -q`

Expected: import failure because `evidence_selection` does not exist.

- [ ] **Step 3: Implement minimal selectors and bounded models**

Use explicit selector keys: descending latency, failed-first, missing-price-first, cache miss/bypass-first with optional hit comparison, descending Token, and non-governed-first. Preserve occurrence ordering as a deterministic tiebreaker.

- [ ] **Step 4: Replace generic risk summaries**

Build each priority’s evidence set from its policy rather than globally projecting `request=1`. Add the relevant signal metric, value and unit. Keep request IDs only in bounded evidence references.

- [ ] **Step 5: Verify GREEN and commit**

Run: `python -m pytest tests/test_finops_evidence_selection.py tests/test_finops_decision_api.py -q`

Commit: `feat(finops): bind evidence to each operating signal`

---

### Task 2: Persisted read-only risk scans

**Files:**
- Create: `backend/finops/risk_scans.py`
- Create: `backend/finops/sql_risk_scans.py`
- Create: `tests/test_finops_risk_scans.py`
- Modify: `backend/sql/finops_schema.sql`
- Modify: `tests/test_finops_sql_migration.py`

**Interfaces:**
- Produces: `RiskScanScope`, `RiskScanFinding`, `FinOpsRiskScan`, `InMemoryRiskScanRepository`, `RiskScanService.run(...)`, `RiskScanService.latest(...)`, and `SqlRiskScanRepository`.
- Consumes: existing `evaluate_default_anomalies`, policy configuration, query events, and Task 1 selectors.

- [ ] **Step 1: Write failing domain tests**

```python
def test_scan_records_rule_basis_without_creating_actions(service, scope):
    scan = service.run(scope=scope, events=_events(), policy_revision="policy-v3", ledger_revision="ledger-v7")
    assert scan.status == "completed"
    assert scan.rules_evaluated == 7
    assert all(item.rule_revision == "policy-v3" for item in scan.findings)
    assert service.action_calls == []
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_finops_risk_scans.py -q`

Expected: missing module or class failure.

- [ ] **Step 3: Implement scan domain and in-memory repository**

Validate opaque `rscan_<32 hex>` references, bounded filters, `running|completed|failed` status, public error category, counts, timestamps, findings, and 90-day retention boundary.

- [ ] **Step 4: Add additive SQL tables**

Create `df_finops.risk_scans` and `df_finops.risk_scan_findings` with tenant/workspace/scope indexes, JSON only for bounded filters and evidence references, and no prompt/response/identity/error-body columns.

- [ ] **Step 5: Implement SQL repository and migration tests**

Test create, replace findings, latest-by-exact-scope, get-by-ref, cross-tenant miss, and idempotent schema execution.

- [ ] **Step 6: Verify GREEN and commit**

Run: `python -m pytest tests/test_finops_risk_scans.py tests/test_finops_sql_migration.py -q`

Commit: `feat(finops): persist read-only risk scans`

---

### Task 3: Risk-scan APIs and decision integration

**Files:**
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/query_cache.py`
- Modify: `tests/test_finops_decision_api.py`
- Create or modify: `tests/test_finops_api.py`

**Interfaces:**
- Produces: `POST /api/finops/risk/scans`, `GET /api/finops/risk/scans/latest`, `GET /api/finops/risk/scans/{scan_ref}`.
- Extends: `GET /api/finops/risk/decision` with `scan` and per-priority evidence sets.

- [ ] **Step 1: Write failing API authorization and contract tests**

Cover owner/admin success, member summary-only access, cross-workspace 403/404, forbidden client rule fields, scan counts, policy revision, and no action creation.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_finops_decision_api.py tests/test_finops_api.py -q`

Expected: 404 for scan routes.

- [ ] **Step 3: Wire repository and service getters**

Use SQL when configured and in-memory fallback in tests. Resolve policy revision from enabled policies, ledger revision from request freshness, and current events from the existing scoped query service.

- [ ] **Step 4: Implement endpoints and cache invalidation**

Accept only typed scope fields. A successful scan invalidates the risk domain and returns the current decision payload. An automatic GET reads the latest saved scan but never creates one.

- [ ] **Step 5: Verify GREEN and commit**

Run: `python -m pytest tests/test_finops_decision_api.py tests/test_finops_api.py tests/test_finops_query_cache.py -q`

Commit: `feat(finops): expose governed risk scan APIs`

---

### Task 4: Structured Operations AI response

**Files:**
- Modify: `backend/finops/assistant.py`
- Modify: `backend/finops/router.py`
- Modify: `tests/test_finops_assistant.py`
- Modify: `tests/test_finops_api.py`
- Modify: `tests/test_finops_assistant_history_api.py`

**Interfaces:**
- Produces: `AssistantAnswerSection(kind, heading, points)`, additive response fields `headline`, `summary`, `sections`, and existing safe text `answer`.
- Consumes: `metric_context`, allowlisted `evidence_refs`, evidence catalog, bounded conversation history.

- [ ] **Step 1: Write failing structured-output tests**

```python
def test_assistant_returns_structured_sections_and_safe_text(service):
    result = service.answer(request=_request(), evidence_payload=_evidence())
    assert [section.kind for section in result.sections] == ["finding", "basis", "recommendation", "caveat"]
    assert "req_" not in result.answer
    assert result.evidence_refs == ["req_allowed"]
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_finops_assistant.py tests/test_finops_api.py -q`

Expected: missing `sections` fields.

- [ ] **Step 3: Implement additive structured schema and prompt contract**

Allow only `finding|basis|impact|recommendation|caveat`, 1–5 bounded points, and allowlisted evidence. Generate a safe flattened `answer` for history compatibility without removing the structured response.

- [ ] **Step 4: Preserve history compatibility**

Keep persisted message content text-only; API responses carry structure for the current answer. Old histories render as one conclusion section.

- [ ] **Step 5: Verify GREEN and commit**

Run: `python -m pytest tests/test_finops_assistant.py tests/test_finops_api.py tests/test_finops_assistant_history_api.py -q`

Commit: `feat(finops): structure contextual AI explanations`

---

### Task 5: Navigation split and evidence-set drawer

**Files:**
- Create: `web/src/finopsEvidence.js`
- Modify: `web/src/constants.js`
- Modify: `web/src/components.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finopsViewModel.js`
- Modify: `web/src/finopsDecisionViewModel.js`
- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/src/styles.css`
- Modify: `web/src/navigationContract.test.mjs`
- Modify: `web/src/evidenceDrawer.test.mjs`
- Modify: `web/src/finopsLayout.test.mjs`

**Interfaces:**
- Produces: primary views `finops` and `finops-risk`, `normalizeEvidenceSelection`, `evidenceCursor`, and multi-item drawer state.
- Consumes: backend `evidence_sets` and request detail API.

- [ ] **Step 1: Write failing navigation and evidence tests**

Assert operations navigation labels are “成本管理” and “风险与优化”, old risk tab normalizes to the new view, subject evidence selections require references, and drawer next/previous keeps the requested order.

- [ ] **Step 2: Verify RED**

Run: `node --test navigationContract.test.mjs evidenceDrawer.test.mjs finopsLayout.test.mjs`

Expected: one-item navigation and string evidence behavior fail assertions.

- [ ] **Step 3: Implement navigation split**

Render the same shared Portal shell in cost or risk mode. Cost mode exposes overview/cost/ROI tabs; risk mode exposes the standalone risk page. Preserve permissions, filters and preload behavior.

- [ ] **Step 4: Implement evidence selection object and multi-item drawer**

Remove subject-specific string calls. Show an evidence list/stepper, load the selected request detail, retain reason and signal, and pass AI answer references unchanged.

- [ ] **Step 5: Apply calm enterprise UI refinement**

Use existing typography, borders, spacing, focus rings and Lucide icons. Keep the drawer within viewport bounds at 1366px and mobile widths; do not add gradients, decorative bubbles or a permanent panel.

- [ ] **Step 6: Verify GREEN and commit**

Run: `node --test navigationContract.test.mjs evidenceDrawer.test.mjs finopsLayout.test.mjs finopsDecisionViewModel.test.mjs`

Commit: `feat(web): split risk operations and bind evidence sets`

---

### Task 6: Context-triggered AI and structured renderer

**Files:**
- Create: `web/src/finopsAskIntent.js`
- Create: `web/src/finops/StructuredAssistantAnswer.jsx`
- Modify: `web/src/FinOpsAssistant.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/src/finopsAssistantHistory.js`
- Modify: `web/src/styles.css`
- Modify: `web/src/finopsAssistant.test.mjs`
- Modify: `web/src/finopsAssistantHistory.test.mjs`
- Modify: `web/src/finopsInteraction.test.mjs`

**Interfaces:**
- Produces: `askIntent(context, evidenceRefs)`, `{interactionRef, question, context, evidenceRefs}`, and `StructuredAssistantAnswer`.
- Consumes: existing assistant request API and Task 4 structured response.

- [ ] **Step 1: Write failing intent, one-shot and rendering tests**

Assert cost/cache/risk contexts generate different Chinese questions; the same `interactionRef` auto-submits once; structure renders headings and lists; old text renders a conclusion block; evidence button sends the answer’s references.

- [ ] **Step 2: Verify RED**

Run: `node --test finopsAssistant.test.mjs finopsAssistantHistory.test.mjs finopsInteraction.test.mjs`

Expected: missing intent and structured renderer failures.

- [ ] **Step 3: Implement deterministic intents and one-shot submission**

Metric-level buttons open the existing popover and immediately submit the generated question. The floating launcher remains manual. History preload must not overwrite messages added after the request started.

- [ ] **Step 4: Implement semantic structured reply UI**

Render headline, summary, section headings and point lists with bounded text. Use safe text fallback for historical answers. Do not render HTML or arbitrary Markdown.

- [ ] **Step 5: Verify GREEN and commit**

Run: `node --test finopsAssistant.test.mjs finopsAssistantHistory.test.mjs finopsInteraction.test.mjs`

Commit: `feat(web): auto-ask from operating context`

---

### Task 7: Risk scan UI and demo readiness

**Files:**
- Create: `web/src/finops/RiskScanStatus.jsx`
- Modify: `web/src/api.js`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/src/styles.css`
- Modify: `backend/finops/demo_initialize.py`
- Modify: `backend/finops/demo_workspace_seed.py`
- Modify: `backend/finops/candidate_acceptance.py`
- Modify: `tests/test_finops_demo_initialize.py`
- Modify: `tests/test_finops_candidate_acceptance.py`
- Modify: `web/src/finopsApi.test.mjs`
- Modify: `web/src/finopsInteraction.test.mjs`

**Interfaces:**
- Produces: client `runFinOpsRiskScan`, scan status bar, rule-basis disclosure, initial `demo-corpus` scan, and candidate acceptance gates.

- [ ] **Step 1: Write failing client, UI and demo acceptance tests**

Assert typed scan body, running lock, last-success fallback, no automatic scan on refresh, demo scan counts, six differentiated scenarios, cache miss/hit evidence, and structured AI readiness.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_finops_demo_initialize.py tests/test_finops_candidate_acceptance.py -q` and `node --test finopsApi.test.mjs finopsInteraction.test.mjs`

- [ ] **Step 3: Implement scan client and status UI**

Show last scan time, scope, rules, samples, findings, coverage and policy revision. The primary button says “运行风险扫描” and its help copy states that it is read-only.

- [ ] **Step 4: Seed initial demo scan and validate scenario consistency**

Use the existing idempotent demo seed batch. Preserve any human-edited budget or configuration revision. Do not enable the global demo seed flag in production.

- [ ] **Step 5: Extend candidate acceptance**

Require a completed scan, per-rule basis, six semantically distinct first evidence references, cache contrast, structured AI contract, and `actions_enabled=false`.

- [ ] **Step 6: Verify GREEN and commit**

Run the focused Python and Node commands from Step 2.

Commit: `feat(finops): complete risk scan demo workflow`

---

### Task 8: Browser acceptance, full regression and release evidence

**Files:**
- Modify: `web/e2e/finops*.spec.*` or the existing FinOps Playwright acceptance files.
- Create: `docs/validation/2026-08-03-finops-governance-loop-candidate.md`

**Interfaces:**
- Validates the complete product flow; produces no new business behavior.

- [ ] **Step 1: Add Playwright acceptance before final UI adjustments**

Cover two navigation entries, distinct evidence for cost/cache/latency/error/unpriced/Token, evidence switching, auto-question text, structured answer headings, scan running/completed states, no scan on refresh, and 1366/1440/mobile clipping.

- [ ] **Step 2: Run Playwright and fix only test-proven UI issues**

Run: `npx playwright test`

Expected: all tests pass with desktop and mobile screenshots saved outside git-tracked product paths.

- [ ] **Step 3: Run complete regression**

Run:

```text
python -m pytest -q
cd web && node --test
cd web && npm run build
cd web && npx playwright test
git diff --check
```

Expected: all suites pass; Vite may retain the existing chunk-size warning but no build failure.

- [ ] **Step 4: Record candidate evidence**

Document commits, SQL additions, feature flags, test totals, screenshots, expected API responses, image build commands, zero-traffic candidate checks, rollback targets, and the explicit production approval gate.

- [ ] **Step 5: Commit**

Commit: `test(finops): verify governance loop acceptance`
