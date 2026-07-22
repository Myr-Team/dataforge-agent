# Monitor BI Candidate Validation Record

Date: `2026-07-22`
Scope: Monitor BI, truthful model-route telemetry, bounded follow-up Context Pack gating
Validation status: `automated verification in progress; candidate deployment and browser smoke pending`

## Boundaries

- This record must not invent candidate URLs, revision names, screenshots, trace IDs, or runtime timestamps.
- This task did not deploy to Azure, change Container Apps traffic, or modify authentication.
- Foundry ROI Preview is not treated as integrated evidence here. Any later runtime note must keep local verified ROI separate from Foundry ROI availability.

## Automated verification

### Focused monitoring API reconciliation

- Command: `python -m pytest tests/test_monitoring_dashboard_api.py -q`
- Status: `passed`
- Result summary: `3 passed in 6.02s`

### Full backend pytest

- Command: `python -m pytest -q`
- Status: `failed`
- Result summary: `1030 passed, 1 skipped, 1 failed, 1 warning in 141.01s`
- Failure detail: `tests/test_model_route_telemetry.py::test_followup_run_persists_selected_route_model_usage_and_latency` still expects `x-dataforge-model-route == "followup"` even when the offline candidate gate leaves follow-up ineligible and the truthful selected route falls back to `analysis`.

### Frontend node tests

- Command: `node --test <expanded list of web/src/*.test.mjs>`
- Status: `failed`
- Result summary: `74 passed, 1 failed in 1078.8152ms`
- Failure detail: `web/src/costValuePanel.test.mjs` failed SSR loading `/src/components.jsx` (`ERR_LOAD_URL`). This is outside the Task 6 monitor scope.

### Task 6 direct frontend coverage

- Command: `node --test web/src/monitorDashboardViewModel.test.mjs`
- Status: `passed`
- Result summary: `4 passed in 174.3078ms`

### Frontend production build

- Command: `npm --prefix web run build`
- Status: `passed`
- Result summary: `vite build passed; 1761 modules transformed, built in 1.24s`

## Candidate deployment evidence

Status: `pending - not executed in Task 6`

- Candidate backend revision: `PENDING_AFTER_DEPLOY`
- Candidate web revision: `PENDING_AFTER_DEPLOY`
- Candidate URL: `PENDING_AFTER_DEPLOY`
- Validation timestamp (UTC): `PENDING_AFTER_DEPLOY`

## Signed-in browser smoke

Status: `pending - not executed in Task 6`

Record later:

- Desktop viewport:
  - Width x height: `PENDING_AFTER_SMOKE`
  - Screenshot paths: `PENDING_AFTER_SMOKE`
- Mobile viewport:
  - Width x height: `PENDING_AFTER_SMOKE`
  - Screenshot paths: `PENDING_AFTER_SMOKE`

Checklist to fill later:

1. Owner can open `Monitor`; non-owner is denied in both navigation and direct API access.
2. Current and portfolio scopes load without geometry shift.
3. Loading, empty, denied, and partial-source states keep chart-frame height stable.
4. A new governed text call appears in model/route summaries and links back to a real run.
5. Missing pricing and outcome evidence remains `unavailable` or `pending_verification`.
6. Workspace, Data, Runs, Conversation, Artifacts, and Settings have no blank-page regressions.

## Trace and APIM correlation

Status: `pending - not executed in Task 6`

- APIM evidence window: `PENDING_AFTER_SMOKE`
- Foundry/OTel trace correlation ID(s): `PENDING_AFTER_SMOKE`
- Redacted run ID(s): `PENDING_AFTER_SMOKE`

## Promotion decision

- Current recommendation: `do not promote from this document alone`
- Reason: `candidate deployment and signed-in browser evidence are still pending`
