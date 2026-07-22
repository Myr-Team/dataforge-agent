# Monitor BI Candidate Validation Record

Date: `2026-07-22`
Scope: Monitor BI, truthful model-route telemetry, bounded follow-up Context Pack gating
Validation status: `candidate deployed at zero traffic; authenticated visual smoke pending`

## Boundaries

- This record must not invent candidate URLs, revision names, screenshots, trace IDs, or runtime timestamps.
- The candidate is deployed with zero traffic. Production revisions and authentication configuration were not changed.
- Foundry ROI Preview is not treated as integrated evidence here. Any later runtime note must keep local verified ROI separate from Foundry ROI availability.

## Automated verification

### Focused monitoring API reconciliation

- Command: `python -m pytest tests/test_monitoring_dashboard_api.py -q`
- Status: `passed`
- Result summary: `3 passed in 6.02s`

### Full backend pytest

- Command: `python -m pytest -q`
- Status: `passed`
- Result summary: `1032 passed, 1 skipped, 1 warning in 147.72s`
- Residual warning: `backend/maf_team_runtime.py:1065` emits an existing experimental API warning.

### Frontend node tests

- Command: `node --test <expanded list of web/src/*.test.mjs>`
- Status: `passed`
- Result summary: `75 passed`

### Task 6 direct frontend coverage

- Command: `node --test web/src/monitorDashboardViewModel.test.mjs`
- Status: `passed`
- Result summary: `4 passed in 174.3078ms`

### Frontend production build

- Command: `npm --prefix web run build`
- Status: `passed`
- Result summary: `vite build passed; 1761 modules transformed, built in 1.69s`

## Candidate deployment evidence

Status: `passed at zero traffic`

- Candidate backend revision: `ca-dataforge-backend--mbi722` (`Healthy`, one replica, `0%` traffic)
- Candidate web revision: `ca-dataforge-web--mbifx722` (`Healthy`, one replica, `0%` traffic)
- Candidate URL: `https://ca-dataforge-web---lineage.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/`
- Candidate backend URL: `https://ca-dataforge-backend---mbi.thankfultree-c0fc8321.eastus2.azurecontainerapps.io/`
- Candidate web upstream: the candidate backend URL above
- Validation timestamp (UTC): `2026-07-22T10:22:12Z`
- Backend health probe: `GET /api/health` returned `ok: true`.
- Access-control probe: unauthenticated `GET /api/monitoring` with a workspace and time window returned `403 workspace access denied for monitor.read`.
- Production web traffic remained on `ca-dataforge-web--monprod722` at `100%`; production backend traffic remained on `ca-dataforge-backend--apimprod723` at `100%`.

## Signed-in browser smoke

Status: `blocked on a user-authenticated browser session`

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

Status: `not claimed in this deployment pass`

- APIM evidence window: `PENDING_AFTER_SMOKE`
- Foundry/OTel trace correlation ID(s): `PENDING_AFTER_SMOKE`
- Redacted run ID(s): `PENDING_AFTER_SMOKE`

## Promotion decision

- Current recommendation: `do not promote from this document alone`
- Reason: `the candidate is healthy and zero-traffic, but its owner-only visual smoke and trace/APIM correlation still require a signed-in session and a real governed call`
