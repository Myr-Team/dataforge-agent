# Task 1 Report

Date: 2026-07-22
Base branch commit: `749346fbbfdf837069de94127770c849e2c03a93`
Task 1 commit: `02a3d891fb6e0a8e086e08020d7c4c93e4fa4661`

## Changed files

- `backend/control_plane.py`
- `backend/monitoring_dashboard.py`
- `tests/test_monitoring_dashboard.py`
- `tests/test_monitoring_dashboard_api.py`

`backend/schemas.py` was not needed.

## Red/green test log

1. Aggregation red
   - Command: `python -m pytest tests/test_monitoring_dashboard.py::test_monitor_dashboard_preserves_unknown_usage_and_groups_observed_model_routes -q`
   - Result: error during collection
   - Failure:
     - `ModuleNotFoundError: No module named 'backend.monitoring_dashboard'`

2. Aggregation green
   - Command: `python -m pytest tests/test_monitoring_dashboard.py::test_monitor_dashboard_preserves_unknown_usage_and_groups_observed_model_routes -q`
   - Result: `1 passed in 0.14s`

3. API red attempt 1
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_api_rejects_non_owner_and_limits_portfolio_to_owned_workspaces -q`
   - Result: error during collection
   - Failure:
     - `ImportError: attempted relative import with no known parent package`
   - Action: fixed the test import so the test could fail for the intended backend reason.

4. API red attempt 2
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_api_rejects_non_owner_and_limits_portfolio_to_owned_workspaces -q`
   - Result: `1 failed`
   - Failure:
     - `AttributeError: backend.control_plane has no attribute '_owned_workspace_ids'`
   - Action: adjusted the test monkeypatch to allow the helper to be absent before implementation.

5. API red attempt 3
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_api_rejects_non_owner_and_limits_portfolio_to_owned_workspaces -q`
   - Result: `1 failed`
   - Failure:
     - `AttributeError: backend.control_plane has no attribute 'build_monitor_dashboard'`
   - Action: adjusted the test monkeypatch to allow the builder import to be absent before implementation.

6. API red attempt 4
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_api_rejects_non_owner_and_limits_portfolio_to_owned_workspaces -q`
   - Result: `1 failed`
   - Failure:
     - `assert 404 == 403`
   - Meaning:
     - `/api/monitoring` did not exist yet.

7. API green
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_api_rejects_non_owner_and_limits_portfolio_to_owned_workspaces -q`
   - Result: `1 passed in 6.60s`

8. Focused Task 1 verification before first commit
   - Command: `python -m pytest tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py tests/test_monitoring_service.py -q`
   - Result: `4 passed in 7.45s`

9. Fresh focused Task 1 verification after user follow-up
   - Command: `python -m pytest tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py tests/test_monitoring_service.py -q`
   - Result: `4 passed in 6.08s`

## Design decisions

1. Added a new pure read-model module
   - `backend/monitoring_dashboard.py` owns projection and aggregation logic.
   - The builder accepts loader callables so the aggregation stays testable and does not modify authentication or persistence behavior.

2. Kept missing data conservative
   - Token summaries remain `None` when a run has no observed usage.
   - Cost remains `status: "unavailable"` unless cost evidence is complete.
   - ROI remains `status: "pending_verification"` unless verified ROI evidence is available.
   - Opportunity remains `status: "unavailable"` or `pending`; it never invents optimization claims.

3. Kept the API owner-scoped from persisted metadata
   - `backend/control_plane.py` now resolves owned workspaces by comparing the current trusted actor identity to persisted `workspace_owner` records loaded from workspace metadata.
   - The endpoint does not infer ownership from the request’s `workspace_id`.

4. Preserved public-only monitor output
   - The new payload exposes aggregated counts, model/route rows, public member labels, and coverage totals only.
   - It does not emit raw prompts, raw claims, credentials, audit payloads, or telemetry rows.

5. Folded APIM evidence in conservatively
   - The route keeps the pure builder independent from Azure Monitor.
   - `backend/control_plane.py` optionally raises `coverage.governed_text_calls` from verified APIM gateway evidence and marks APIM as a freshness source only when such evidence is available.

## Concerns

1. Test-generated untracked workspace directories remain in `workspaces/`
   - Current paths observed after verification:
     - `workspaces/ws-audit/`
     - `workspaces/ws-history/`
     - `workspaces/ws-locked/`
     - `workspaces/ws-private/`
     - `workspaces/ws-roi-api/`
     - `workspaces/ws-roles/`
     - `workspaces/ws-sensitive/`
   - They were not staged or committed.
   - A direct cleanup attempt was blocked by policy, so they were left untouched for review.

2. Task scope was kept intentionally narrow
   - No frontend files were touched.
   - No authentication behavior was modified.
   - `backend/schemas.py` was left unchanged.

## Task 1 correction pass

### Review findings addressed

1. Removed false `audited_runs` fallback from mixed audit activity counts.
2. Preserved zero token totals and kept missing prompt/completion splits as unknown instead of `0`.
3. Removed fabricated `Current owner` member attribution when no persisted chargeback evidence exists.
4. Threaded the monitor API `from` and `to` window into APIM gateway evidence so custom ranges no longer reuse a fixed 24-hour metric.

### Red/green correction log

1. Correction red: dashboard truthfulness regressions
   - Command: `python -m pytest tests/test_monitoring_dashboard.py::test_monitor_dashboard_keeps_audited_runs_unknown_when_only_activity_feed_exists tests/test_monitoring_dashboard.py::test_usage_from_dict_preserves_zero_totals_and_missing_splits tests/test_monitoring_dashboard.py::test_monitor_dashboard_keeps_split_tokens_unknown_when_only_total_is_observed tests/test_monitoring_dashboard.py::test_monitor_dashboard_does_not_fabricate_member_rows_without_chargeback_evidence -q`
   - Result: `4 failed in 0.98s`
   - Failures proved:
     - `audited_runs` incorrectly returned `5` from activity feed data.
     - `_usage_from_dict({"total": 0})` returned `None`.
     - `{total: 50}` incorrectly surfaced `input: 0`, `output: 0`, and reduced `known_runs`.
     - Members incorrectly included `Current owner` without chargeback evidence.

2. Correction red: monitor API window threading
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_api_uses_requested_window_for_gateway_evidence -q`
   - Result: `1 failed in 11.44s`
   - Failure proved:
     - `get_gateway_metric_evidence(...)` was called with `from_value=None, to_value=None`.

3. Correction red: Azure Monitor gateway query window
   - Command: `python -m pytest tests/test_azure_monitor_status.py::test_gateway_metric_evidence_uses_exact_requested_window -q`
   - Result: `1 failed in 9.77s`
   - Failure proved:
     - `get_gateway_metric_evidence()` did not accept `from_value` / `to_value`.

4. Correction green: dashboard truthfulness regressions
   - Command: `python -m pytest tests/test_monitoring_dashboard.py::test_monitor_dashboard_keeps_audited_runs_unknown_when_only_activity_feed_exists tests/test_monitoring_dashboard.py::test_usage_from_dict_preserves_zero_totals_and_missing_splits tests/test_monitoring_dashboard.py::test_monitor_dashboard_keeps_split_tokens_unknown_when_only_total_is_observed tests/test_monitoring_dashboard.py::test_monitor_dashboard_does_not_fabricate_member_rows_without_chargeback_evidence -q`
   - Result: `4 passed in 0.20s`

5. Correction green: monitor API window threading
   - Command: `python -m pytest tests/test_monitoring_dashboard_api.py::test_monitor_api_uses_requested_window_for_gateway_evidence -q`
   - Result: `1 passed in 9.41s`

6. Correction green: Azure Monitor gateway query window
   - Command: `python -m pytest tests/test_azure_monitor_status.py::test_gateway_metric_evidence_uses_exact_requested_window -q`
   - Result: `1 passed in 9.51s`

7. Focused correction verification
   - Command: `python -m pytest tests/test_monitoring_dashboard.py tests/test_monitoring_dashboard_api.py tests/test_monitoring_service.py tests/test_azure_monitor_status.py -q`
   - Result: `30 passed in 12.57s`

---

## Cache persistence pass

### Status and scope

- Commit message: `feat: persist safe Redis cache metering`
- Files: `backend/orchestrator.py`, `backend/run_store.py`,
  `tests/test_run_store_dynamic.py`, and `tests/test_orchestrator_smoke.py`.

### Delivered behavior

- Redis feasibility entries now wrap the feasibility result with a bounded
  source meter.
- Cache hits restore only `state`, `provider`, optional `elapsed_ms`, optional
  `source_usage`, and optional `source_cost_estimate` into `_llm.cache`.
- The run store normalizes those values into `models[].cache` and compact
  model-response step data. Unrecognized data is discarded.
- Cache hits emit a model-response event for persistence without adding a
  model response ID.
- Cache-key samples and cache lookup metadata are not attached to feasibility
  telemetry or artifacts.

### Constraints checked

- Easy Auth and workspace authorization were not modified.
- The cache meter does not persist prompts, raw Entra IDs, headers, tokens,
  connection strings, or cache keys.
- Redis reuse remains application-level telemetry; no APIM data model or
  aggregation code changed.

### TDD evidence

1. Red
   - Command: `python -m pytest tests/test_run_store_dynamic.py tests/test_orchestrator_smoke.py -q`
   - Result: `2 failed, 1 passed in 10.27s`.
   - Failures: `model_record` dropped `cache`; the cache-hit metadata retained
     cache-key-derived fields and an error rather than the bounded contract.

2. Green
   - Command: `python -m pytest tests/test_run_store_dynamic.py tests/test_orchestrator_smoke.py -q`
   - Result: `3 passed in 4.25s`.

3. Final focused verification
   - Command: `python -m pytest tests/test_run_store_dynamic.py tests/test_orchestrator_smoke.py tests/test_model_route_telemetry.py -q`
   - Result: `8 passed in 4.32s`.
   - `python -m compileall -q backend/orchestrator.py backend/run_store.py`
     and `git diff --check` both exited `0`.

### Concern

The brief referenced `tests/test_orchestrator_smoke.py`, but that file was
absent in this fork. It was added as the focused cache-hit safety test. Full
suite, deployment, and dashboard aggregation verification remain later tasks.
