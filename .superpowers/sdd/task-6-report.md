# Task 6 Report: Monitor BI verification and documentation

## Scope completed

- Added monitor API reconciliation coverage in [tests/test_monitoring_dashboard_api.py](C:\Users\12140\Documents\Agent-Demo-project-worktrees\codex-monitor-bi-context\tests\test_monitoring_dashboard_api.py).
- Updated [README.md](C:\Users\12140\Documents\Agent-Demo-project-worktrees\codex-monitor-bi-context\README.md) with truthful monitoring, model-routing, APIM governance, Context Pack, and Foundry ROI boundaries.
- Updated [backend/.env.example](C:\Users\12140\Documents\Agent-Demo-project-worktrees\codex-monitor-bi-context\backend\.env.example) with safe placeholders for route allowlists and the offline evaluation gate.
- Created [docs/validation/2026-07-22-monitor-bi-evidence.md](C:\Users\12140\Documents\Agent-Demo-project-worktrees\codex-monitor-bi-context\docs\validation\2026-07-22-monitor-bi-evidence.md) as an honest candidate-verification record with runtime-only fields left pending.

## Reconciliation coverage

Added `test_monitor_dashboard_reconciles_model_and_route_totals_with_run_records`.

What it proves:

- `models` counts only normalized execution rows with route/model telemetry.
- `routes` may exceed `models` because route projection preserves `unknown` executions when a run has no model telemetry.
- `coverage.governed_text_calls` matches the counted governed text model rows.

Focused result:

- `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_dashboard_reconciles_model_and_route_totals_with_run_records -q`
- Result: `1 passed in 5.63s`

## Automated verification

1. Focused monitoring API suite
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py -q`
   - Result: `3 passed in 6.02s`

2. Full backend pytest
   - Command: `python -m pytest -q`
   - Result: `1030 passed, 1 skipped, 1 failed, 1 warning in 141.01s`
   - Residual: `tests/test_model_route_telemetry.py::test_followup_run_persists_selected_route_model_usage_and_latency`
   - Reason observed: the test still expects `followup`, while the current offline evaluation gate leaves the candidate route ineligible and the truthful selected route falls back to `analysis`.

3. Full frontend node tests
   - Command: `node --test <expanded list of web/src/*.test.mjs>`
   - Result: `74 passed, 1 failed in 1078.8152ms`
   - Residual: `web/src/costValuePanel.test.mjs`
   - Reason observed: SSR import load failure for `/src/components.jsx` (`ERR_LOAD_URL`), outside the Task 6 monitor scope.

4. Task 6 direct frontend test
   - Command: `node --test web/src/monitorDashboardViewModel.test.mjs`
   - Result: `4 passed in 174.3078ms`

5. Frontend production build
   - Command: `npm --prefix web run build`
   - Result: `passed; 1761 modules transformed, built in 1.24s`

## Non-live residuals

- Candidate Container Apps deployment was not performed in this task.
- Signed-in browser smoke was not performed in this task.
- The validation record keeps candidate URL, revision, screenshot, and trace fields pending on purpose.
- Full-repo automated verification is not yet clean because of the two pre-existing failures listed above.

## Commit scope

- No Azure deployment
- No auth changes
- No Container Apps traffic change
- No secret material added
