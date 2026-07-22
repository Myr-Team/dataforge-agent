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

## Evaluation contract note

This task keeps the stable evaluation boundary explicit:

- `measurement_scope='deterministic_harness'`
- `production_quality_claim=false`
- groundedness and unsupported-claim rate remain fixture/reference-propagation contract checks
- the deterministic harness is for runtime contract verification, not production answer quality

## Automated verification

1. Focused monitoring API suite
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py -q`
   - Result: `3 passed in 6.02s`

2. Focused model route telemetry suite
   - Command: `python -m pytest tests/test_model_route_telemetry.py -q`
   - Result: `3 passed in 6.59s`
   - Coverage: explicit eligible follow-up route persists `followup`; default fail-closed gate fallback persists `analysis` with `fallback_reason="candidate_not_eligible"`.

3. Focused model policy suite
   - Command: `python -m pytest tests/test_model_policy.py -q`
   - Result: `16 passed in 6.02s`

4. Full frontend node tests
   - Command: `node --test web/src/*.test.mjs`
   - Result: `75 passed in 5042.916ms`
   - Note: `web/src/costValuePanel.test.mjs` now uses the actual `web` root for Vite SSR, so `server.ssrLoadModule("/src/components.jsx")` resolves cleanly under bare `node --test`.

5. Task 6 direct frontend test
   - Command: `node --test web/src/monitorDashboardViewModel.test.mjs`
   - Result: `4 passed in 174.3078ms`

6. Frontend production build
   - Command: `npm --prefix web run build`
   - Result: `passed; 1761 modules transformed, built in 1.69s`

7. Full backend pytest
   - Command: `python -m pytest -q`
   - Result: `1032 passed, 1 skipped, 1 warning in 152.74s`
   - Warning: `ExperimentalWarning: [FUNCTIONAL_WORKFLOWS] workflow is experimental and may change or be removed in future versions without notice.`

## Non-live residuals

- Candidate Container Apps deployment was not performed in this task.
- Signed-in browser smoke was not performed in this task.
- The validation record keeps candidate URL, revision, screenshot, and trace fields pending on purpose.
- Full-repo automated verification is now clean; live Azure/browser evidence remains pending by design.

## Commit scope

- No Azure deployment
- No auth changes
- No Container Apps traffic change
- No secret material added
