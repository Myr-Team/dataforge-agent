# DataForge Service Readiness, Pricing, and Risk Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task by task and verify each checkpoint before continuing.

**Goal:** Close the production gaps around model price mapping, durable risk scans, ROI evidence integrity, background-job visibility, and demo-ready service status without weakening tenant/workspace authorization or enabling automatic governance actions.

**Architecture:** Keep official model pricing tenant-wide, but authorize writes through a trusted Entra tenant-pricing capability and bind every mutation to an authorized workspace audit context. Persist risk-scan lifecycle and background-job heartbeat records in Azure SQL, expose scoped read APIs, and render compact operational status inside Settings. Preserve the existing FinOps ledger, request-level evidence model, immutable images, and approval-disabled governance boundary.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, Azure SQL, React, Vite, Node test runner, Playwright, Azure Container Apps, Azure Container Apps Jobs.

## Global Constraints

- Preserve Easy Auth and the existing tenant/workspace authorization boundary.
- Keep `DF_FINOPS_ACTIONS_ENABLED=0`; scanning, observation, and price mapping must not execute production remediation.
- Never persist or return provider keys, raw prompts, response bodies, internal error text, or raw identity claims.
- Treat unknown price matches as `unpriced`; do not fabricate cost values.
- Every risk and ROI evidence link exposed by the UI must resolve through the authorized request-detail API.
- Apply only additive SQL migrations and keep rollback targets for backend, web, and scheduled jobs.
- Do not stage or modify unrelated `.superpowers/sdd`, `output`, or generated workspace files already present in the worktree.
- Use test-first changes for each behavior and run focused tests before the full gate.

---

## Task 1: Make tenant price mapping governable and auditable

**Files:**

- Modify: `backend/audit_store.py`
- Modify: `backend/finops/router.py`
- Modify: `tests/test_finops_pricing_api.py`
- Modify: `web/src/api.js`
- Modify: `web/src/ModelRoutingPage.jsx`
- Modify: `web/src/finops/modelRoutingViewModel.js`
- Test: `web/tests/finopsApi.test.mjs`
- Test: `web/tests/finopsInteraction.test.mjs`
- Test: `web/tests/model-routing-settings.spec.mjs`

### Step 1: Write failing backend authorization and audit tests

Add cases proving:

1. `GET /api/finops/pricing/mappings` returns `scope: "tenant"`, `can_manage`, and `authorization_source`.
2. An actor listed in `DF_FINOPS_TENANT_OWNER_OIDS` may create/remove a tenant mapping even when not owner of every visible workspace.
3. A normal workspace administrator remains read-only for tenant pricing.
4. PUT/DELETE require an authorized `workspace_id` audit context, preserve optimistic concurrency, and fail closed if audit persistence fails.
5. A stale `base_revision` returns `409` without writing.

Run: `python -m pytest -q tests/test_finops_pricing_api.py`

Expected: new cases fail before implementation.

### Step 2: Add explicit tenant-pricing capability

In `backend/finops/router.py`, add a focused helper equivalent to:

```python
def _tenant_pricing_capability(*, actor_ref: str, roles: dict[str, str]) -> tuple[bool, str]:
    configured_oids = _configured_oid_set("DF_FINOPS_TENANT_OWNER_OIDS")
    if actor_ref in configured_oids:
        return True, "entra_tenant_pricing_admin"
    if roles and all(role == "owner" for role in roles.values()):
        return True, "all_workspaces_owner"
    return False, "read_only"
```

Use only the trusted actor identifier derived from Easy Auth. Do not accept an actor or tenant identifier from the client.

### Step 3: Bind mutations to durable audit records

Extend `backend/audit_store.py` allowlists for the narrowly typed price-mapping mutation. Require `workspace_id` as an authorized audit context on PUT/DELETE, record deployment, official price key, previous revision, new revision, and outcome, but never credentials or provider configuration values.

### Step 4: Return a stable capability contract

Extend the mappings response with:

```json
{
  "scope": "tenant",
  "can_manage": true,
  "authorization_source": "entra_tenant_pricing_admin"
}
```

Keep mapping rows and revision behavior backward compatible.

### Step 5: Make the UI recover from 403/409 and explain scope

Send the current workspace as the mutation audit context. Disable edit actions when `can_manage` is false, display “组织级价目关联”, and translate:

- `403` to “当前账号可查看价目，但没有组织级管理权限”.
- `409` to “价目已被其他管理员更新，已重新加载最新版本”.
- audit persistence failures to a human-readable retry message.

Do not show raw backend JSON in the user interface.

### Step 6: Verify and commit

Run:

```powershell
python -m pytest -q tests/test_finops_pricing_api.py
Set-Location web
node --test tests/finopsApi.test.mjs tests/finopsInteraction.test.mjs
npx playwright test tests/model-routing-settings.spec.mjs
```

Commit: `fix(finops): govern tenant price mappings`

---

## Task 2: Guarantee request-level ROI evidence integrity

**Files:**

- Modify: `backend/finops/decision_service.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/finops/candidate_acceptance.py`
- Modify: `backend/demo_corpus.py` or the existing demo seed module selected by repository inspection
- Test: `tests/test_finops_decisions.py`
- Test: `tests/test_finops_candidate_acceptance.py`

### Step 1: Reproduce the production acceptance failure

Add a test matching the observed condition: an ROI stage contains a stored ref that is not a request-level `request_ref` visible in the selected workspace.

Expected before fix: candidate acceptance raises `ROI stage evidence is not request-level`.

### Step 2: Resolve and filter ROI evidence at assembly time

For each ROI stage:

1. Resolve source refs through the tenant/workspace-scoped request index.
2. Keep only visible request-level refs.
3. De-duplicate while preserving order.
4. If none remain, set an evidence gap and an honest `unavailable` or `partial` status; never expose a broken link.

Do not synthesize request identifiers.

### Step 3: Repair the demo lineage, not the evidence contract

Update the demo corpus seed so each advertised ROI stage has representative request facts whose `request_ref` resolves through `/api/finops/requests/{request_ref}`. Keep dates relative to the current demo window and costs internally consistent with the active price-card revision.

### Step 4: Strengthen candidate acceptance

For every returned stage ref, call the request-detail route using the same trusted principal and selected workspace. Fail if it is unauthorized, missing, cross-workspace, or not request-level.

### Step 5: Verify and commit

Run:

```powershell
python -m pytest -q tests/test_finops_decisions.py tests/test_finops_candidate_acceptance.py
```

Commit: `fix(finops): bind ROI stages to request evidence`

---

## Task 3: Persist the full risk-scan lifecycle and evidence coverage

**Files:**

- Modify: `backend/finops/risk_scans.py`
- Modify: `backend/finops/sql_risk_scans.py`
- Modify: `backend/finops/router.py`
- Modify: `backend/sql/finops_schema.sql`
- Modify: `backend/audit_store.py`
- Test: `tests/test_finops_risk_scans.py`
- Test: `tests/test_finops_risk_api.py`

### Step 1: Write lifecycle and history tests

Cover:

- `running → completed` persistence.
- `running → failed` persistence with a safe public failure category.
- Seven rule results per completed scan.
- Evidence coverage based on actual bound request refs, not merely non-unavailable observations.
- History ordered newest first and isolated by tenant/workspace.
- Stored scan evidence remains stable when later request windows change.
- Risk-scan audit failure prevents a manual scan from being reported as accepted.

### Step 2: Apply additive SQL changes

Extend the risk-scan table only when columns are missing. Persist at minimum:

- `status`
- `started_at`
- `completed_at`
- `failure_category`
- `rule_count`
- `rules_evaluated`
- `rules_triggered`
- `evidence_bound_findings`
- `evidence_coverage_pct`

Keep finding evidence refs immutable for a completed scan.

### Step 3: Implement durable scan execution

Create the `running` record before evaluation, persist completed findings in one committed result, and convert exceptions to `failed` with a safe category. Never store exception text containing provider or request details.

### Step 4: Add scoped history APIs

Implement:

```text
GET /api/finops/risk/scans?workspace_id=...&limit=5
GET /api/finops/risk/scans/{scan_ref}?workspace_id=...
```

Both routes must derive tenant from trusted claims and authorize only the selected workspace.

### Step 5: Audit manual scans

Add typed `finops_risk_scan.run` / `finops_risk_scan` audit support. Record selected workspace, scan ref, policy revision, status, and counts. Scanning remains read-only with respect to production routing and gateway policies.

### Step 6: Verify and commit

Run:

```powershell
python -m pytest -q tests/test_finops_risk_scans.py tests/test_finops_risk_api.py
```

Commit: `feat(finops): persist governed risk scan history`

---

## Task 4: Record background-job heartbeats and expose service readiness

**Files:**

- Add: `backend/finops/job_status.py`
- Add: `backend/service_readiness_router.py`
- Modify: `backend/finops/apim_backfill.py`
- Modify: `backend/finops/rollup_refresh.py`
- Modify: `backend/finops/retention.py`
- Modify: `backend/sql/finops_schema.sql`
- Modify: `backend/app.py`
- Test: `tests/test_finops_job_status.py`
- Test: `tests/test_service_readiness_api.py`

### Step 1: Write heartbeat and readiness tests

Cover successful and failed executions, stale status, tenant-safe responses, unavailable optional dependencies, and secret-safe output.

### Step 2: Add the heartbeat ledger

Create additive table `df_finops.job_run_status` with job name, execution ref, started/completed timestamps, status, safe error category, rows observed/written, and source freshness. Do not store stack traces or connection details.

### Step 3: Wrap all three jobs

Each entrypoint records `running`, then `succeeded` or `failed`. The process must still exit non-zero on failure so Container Apps Jobs reports the execution truthfully.

### Step 4: Add a scoped service-readiness API

Expose `GET /api/service-readiness?workspace_id=...` for an authorized workspace. Return compact groups:

- Identity: authenticated principal, role source, selected-workspace role.
- Data: SQL, Blob, Redis, search/index availability.
- AI: Foundry, external-provider configuration state, selected routing readiness.
- FinOps: ledger, price revision, priced/unpriced request coverage, latest risk scan.
- Background jobs: last status, last completion, expected cadence, stale/not-run state.

Return capability and status only; never return resource IDs, endpoints, secrets, tenant IDs, subscription IDs, or exception messages.

### Step 5: Verify and commit

Run:

```powershell
python -m pytest -q tests/test_finops_job_status.py tests/test_service_readiness_api.py
```

Commit: `feat(ops): expose safe service readiness`

---

## Task 5: Refine the Settings and Risk user experience

**Files:**

- Add: `web/src/ServiceReadinessPage.jsx`
- Add: `web/src/finops/serviceReadinessViewModel.js`
- Modify: `web/src/api.js`
- Modify: `web/src/ModelGovernanceSettings.jsx`
- Modify: `web/src/finops/RiskDecisionPage.jsx`
- Modify: `web/src/FinOpsPortal.jsx`
- Modify: `web/src/styles.css`
- Test: `web/tests/serviceReadinessViewModel.test.mjs`
- Test: `web/tests/finopsRiskViewModel.test.mjs`
- Test: `web/tests/finopsInteraction.test.mjs`
- Test: `web/tests/model-routing-settings.spec.mjs`
- Test: `web/tests/finops-operations-management.spec.mjs`

### Step 1: Write failing view-model and browser tests

Assert:

- Settings contains a compact “服务状态” tab.
- The page renders real loading, ready, stale, failed, and not-run states without placeholders.
- Price mapping errors are actionable and raw JSON is never shown.
- A risk scan shows lifecycle, seven-rule coverage, representative evidence count, and recent history.
- Each rule’s “查看证据” opens only that rule’s stored evidence.
- “问 AI” is immediately contextualized to the selected service/rule/metric.
- Desktop and 390px layouts have no overlap, clipped tooltip, or horizontal page overflow.

### Step 2: Build the compact service-status page

Use the existing design system. Prefer grouped rows and small status chips over another large dashboard. Each item must answer: what is checked, current state, last confirmed time, and what the administrator can do next.

### Step 3: Add risk history and honest coverage

Show latest scan status, evaluated rules, triggered rules, evidence-bound findings, coverage percentage, and the last five scans. Keep the seven rules collapsible and decision-first. Failed or not-yet-run scans must remain visibly distinct.

### Step 4: Preserve performance

Fetch service readiness only when its settings tab opens. Reuse the existing FinOps preload/cache path for risk history and latest scan, apply a three-to-five-minute client freshness window, and invalidate only after a manual scan or price mutation.

### Step 5: Verify and commit

Run:

```powershell
Set-Location web
node --test tests/serviceReadinessViewModel.test.mjs tests/finopsRiskViewModel.test.mjs tests/finopsInteraction.test.mjs
npm run build
$env:DF_PLAYWRIGHT_PORT='5291'
npx playwright test tests/model-routing-settings.spec.mjs tests/finops-operations-management.spec.mjs
```

Commit: `feat(web): surface service and risk readiness`

---

## Task 6: Expand the candidate acceptance gate

**Files:**

- Modify: `backend/finops/candidate_acceptance.py`
- Modify: `tests/test_finops_candidate_acceptance.py`
- Modify: `web/tests/finopsMockApi.mjs`
- Modify: `web/tests/finops-operations-management.spec.mjs`
- Modify: `web/tests/model-routing-settings.spec.mjs`
- Add: `docs/validation/2026-08-09-service-readiness-candidate-runbook.md`

### Step 1: Add acceptance checks

The backend candidate command must verify:

- An official price match and an intentionally unpriced model.
- A mapping write/read/remove cycle using `base_revision`, followed by restoration.
- Every ROI evidence ref opens successfully.
- A manual risk scan completes all seven rules and appears in history.
- Each finding evidence ref opens successfully.
- Service readiness reports all required groups without sensitive fields.
- Job heartbeat records can be read after one-shot executions.

### Step 2: Add browser acceptance

Capture authenticated desktop and 390px evidence for:

- Service status.
- Governed price mapping and the unpriced recovery path.
- Risk scan execution/history/evidence.
- Contextual AI question and structured response.

Use a unique Playwright port and never reuse a stale server.

### Step 3: Write the operator runbook

Document additive migration, immutable image digests, zero-traffic candidates, one-shot jobs, schedules, health/readiness checks, rollback revisions, and traffic-switch approval. Do not include credentials or raw resource identifiers.

### Step 4: Run the full local gate

Run:

```powershell
python -m pytest -q
Set-Location web
node --test
npm run build
$env:DF_PLAYWRIGHT_PORT='5291'
npx playwright test
Set-Location ..
git diff --check
```

Expected: all suites pass and no generated artifacts are staged.

Commit: `test(finops): enforce service readiness acceptance`

---

## Task 7: Deploy, validate, and release with rollback evidence

**Files:**

- Modify: `docs/validation/2026-08-09-service-readiness-candidate-runbook.md`
- Modify: the existing production rollout evidence document selected by repository inspection

### Step 1: Inspect current production state

Record current backend/web revisions, traffic weights, image digests, job configuration, and rollback targets. Confirm the production branch and commit before building.

### Step 2: Apply additive SQL migration

Run the migration against the production SQL database using the existing secret-safe deployment path. Verify the added columns/table without printing connection strings.

### Step 3: Build immutable images and deploy zero-traffic candidates

Build backend and web from the exact release commit. Deploy candidates at zero traffic and verify revision health before any switch.

### Step 4: Update and exercise jobs

Point APIM reconciliation, rollup, and retention jobs to the same immutable backend image. Run each once manually and require `succeeded` plus a persisted heartbeat before enabling schedules:

- Reconciliation: every 5 minutes.
- Rollup: every 15 minutes.
- Retention: daily at 02:00 UTC unless the production runbook specifies a different approved timezone.

If any one-shot fails, leave schedules unchanged and stop the release.

### Step 5: Run candidate acceptance

Run backend acceptance and authenticated desktop/mobile browser acceptance against the zero-traffic candidates. Confirm:

- Price association saves and restores.
- ROI evidence opens.
- Risk scan and history are real and complete.
- Service status reflects actual job runs.
- DeepSeek remains selectable and its price mapping remains independent from credentials.
- No request stalls behind bootstrap authorization.

### Step 6: Secret and artifact audit

Search tracked diff and candidate logs for credential patterns, PATs, provider keys, authorization headers, tenant/subscription IDs, and test artifacts. Stop if any secret-like value is found.

### Step 7: Switch traffic and observe

After candidate success, move backend traffic first, then web traffic. Observe health, 4xx/5xx, timeouts, risk-scan latency, assistant latency, and price-mapping writes. Keep `DF_FINOPS_ACTIONS_ENABLED=0`.

### Step 8: Push and update the PR

Push the release branch to `Myr-Team/dataforge-agent`, update the existing PR with exact commit SHA, test counts, candidate evidence, production revisions, and rollback targets. Do not merge until the final review gate is complete.

### Step 9: Final verification

Repeat production health/readiness requests and an authenticated smoke path after traffic switch. Record all results and only then state that production is ready.
