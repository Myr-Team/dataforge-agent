## Task 4 - Offline evaluation and candidate eligibility gates

### Scope delivered

- Added `backend/context_evaluation.py` for deterministic offline evaluation
  summaries, stale/malformed gating, and safe public projections.
- Added sanitized fixture cases in `eval/context_optimization_cases.json`.
- Wired `backend/model_policy.py` so follow-up candidate routing requires an
  eligible offline summary instead of enabling on config or intent alone.
- Wired `backend/monitoring_dashboard.py` so monitor quality exposes only safe
  context-optimization state: `status`, `sample_count`,
  `evaluator_version`, and `eligible`.
- Added regression coverage in `tests/test_context_evaluation.py`,
  `tests/test_model_policy.py`, and `tests/test_monitoring_dashboard.py`.

### Red phase evidence

1. Initial focused red:

   - `python -m pytest tests/test_context_evaluation.py tests/test_model_policy.py tests/test_monitoring_dashboard.py -q`
   - Result:
     - `ModuleNotFoundError: No module named 'backend.context_evaluation'`
     - `AttributeError: module 'backend.model_policy' has no attribute 'context_optimization_gate'`
     - `TypeError: build_monitor_dashboard() got an unexpected keyword argument 'evaluation_loader'`

2. Expanded red after adding stricter tests:

   - `python -m pytest tests/test_context_evaluation.py -q`
   - Result: import error until `backend.context_evaluation` existed.

   - `python -m pytest tests/test_model_policy.py::test_followup_candidate_does_not_enable_when_gate_is_unavailable -q`
   - Result: missing `context_optimization_gate`.

   - `python -m pytest tests/test_monitoring_dashboard.py::test_monitor_dashboard_reports_context_optimization_gate_without_claiming_success -q`
   - Result: missing `evaluation_loader` hook on dashboard builder.

### Green phase evidence

1. Focused task suite:

   - `python -m pytest tests/test_context_evaluation.py tests/test_model_policy.py tests/test_monitoring_dashboard.py -q`
   - Result: `29 passed in 9.68s`

2. Targeted gate verification:

   - `python -m pytest tests/test_context_evaluation.py::test_evaluate_context_candidate_returns_safe_aggregate_summary tests/test_model_policy.py::test_followup_candidate_requires_eligible_offline_evaluation tests/test_monitoring_dashboard.py::test_monitor_dashboard_reports_context_optimization_gate_without_claiming_success -q`
   - Result: `3 passed in 7.08s`

3. Related monitor API regression:

   - `python -m pytest tests/test_monitoring_dashboard_api.py -q`
   - Result: `2 passed in 5.84s`

### Review-fix cycle evidence

1. Reviewer regression red:

   - `python -m pytest tests/test_context_evaluation.py tests/test_monitoring_dashboard.py -q`
   - Result:
     - `TypeError: ... runner() got an unexpected keyword argument 'variant'`
     - `AssertionError: {'status': 'passed'} != {'status': 'malformed'}`
     - `AssertionError: {'eligible': True, 'status': 'passed'} != {'eligible': False, 'status': 'malformed'}`

2. Fix verification:

   - `python -m pytest tests/test_context_evaluation.py tests/test_monitoring_dashboard.py -q`
   - Result: `15 passed in 0.24s`

3. Final focused suite after the fix:

   - `python -m pytest tests/test_context_evaluation.py tests/test_model_policy.py tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py -q`
   - Result: `33 passed in 5.35s`

### Review-fix scope

- Updated `backend/context_evaluation.py` so `evaluate_context_candidate()` honors the public one-argument runner contract by passing a copied mapping with safe `variant: "paired"` metadata instead of undocumented kwargs.
- Added centralized gate-status sanitization with a strict allowlist. Unknown statuses such as `passed` now fail closed to `malformed`, keep `eligible: false`, and flow consistently through both `load_evaluation_gate()` and monitor projections.
- Updated `backend/monitoring_dashboard.py` to reuse the same sanitization boundary when a custom evaluation loader bypasses the default gate loader.
- Added regression coverage in `tests/test_context_evaluation.py` and `tests/test_monitoring_dashboard.py` for the normal one-argument runner and unknown gate statuses.

### Residual risks

- The gate reads a local summary file and safely disables the candidate route
  when that summary is missing, stale, or malformed. This task does not yet add
  the separate offline job that writes a fresh summary artifact.
- The bundled fixture cases are sanitized placeholders for deterministic offline
  evaluation coverage. They are intentionally not wired to a live Foundry
  evaluator in the unit-test path.

---

## Bedrock provider UI delivery

### Scope delivered

- Added an isolated, ephemeral AWS Bedrock credential form with a server-supported region selector and save-and-test action.
- Added typed Bedrock view-model fields and revisioned credential rotation while retaining the DeepSeek path.
- Bedrock cards disclose only region, secure-storage status, configuration-test state, and that they are outside Agent routing.
- Sanitized provider load/action errors, including 409 conflicts; no backend error body is rendered.

### Verification evidence

- Node red: `node --test src/providerConnectionsViewModel.test.mjs src/finopsApi.test.mjs` failed as expected because `aws_bedrock` lacked the friendly label.
- Node green: `node --test` — `140` passed.
- Build: `npm run build` — Vite completed successfully (the existing >500 kB chunk advisory remains).
- Browser: `npx playwright test tests/finops-pricing-routing-remediation.spec.mjs` — `4` passed, covering desktop create/clear/reload/routing exclusion and mobile 409 sanitization.

### Visual review

- `output/playwright/bedrock-provider-desktop-connected.png`: reviewed; all Bedrock fields fit the existing wide settings panel.
- `output/playwright/bedrock-provider-mobile-conflict.png`: reviewed; fields stack without overflow, inputs are blank, and the safe conflict message is visible.

### Residual risks

- Browser acceptance uses inert markers only; it exercises the mock contract, not live AWS connectivity.

---

## Bedrock provider UI review remediation

### RED evidence

- `node --test src/providerConnectionsViewModel.test.mjs src/finopsApi.test.mjs` failed as intended: the rotate helper sent `display_name` and `region`, and a hostile connected/governed/supported Bedrock record was assignable.

### GREEN evidence

- `node --test` — `142` passed.
- `npm run build` — Vite completed successfully; the existing chunk-size advisory remains.
- `npx playwright test tests/finops-pricing-routing-remediation.spec.mjs` — `5` passed.
- `git diff --check aff87ef5e713f63914a007c26a534bd1bda99efc..HEAD` is rerun after the remediation commit.

### Exact-payload and lifecycle checks

- Node and Playwright assert Bedrock rotation emits exactly `provider_type`, `access_key_id`, `secret_access_key`, `session_token`, and `base_revision`; the mock rejects extra rotate fields.
- Playwright asserts the typed create body, create/rotate clearing only after success, retained ephemeral values after a sanitized conflict, blank inputs and no markers after reload, empty browser storage, and marker-free provider mock records.
- Success copy is derived from the refreshed connection record; a degraded record receives only the neutral “测试结果已刷新” notice.

### Visual and self-review

- Reviewed `output/playwright/bedrock-provider-desktop-connected.png` and `output/playwright/bedrock-provider-mobile-conflict.png`; both are blank credential captures, fit their layouts, and use the existing settings language.
- Bedrock is non-assignable even with hostile backend states; error categories use an allowlist with a generic fallback; Access Key ID and Secret Access Key enforce backend-aligned lengths 8 and 16.
