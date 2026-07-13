# P2-B Task 1: Azure Monitor Delivery-Aware Status Report

## RED / GREEN Evidence

- RED 1: `test_azure_monitor_delivery_adapter_is_declared` failed because `backend.azure_monitor_client` did not exist.
- GREEN 1: the adapter module was added and the declaration test passed.
- RED 2: delivery behavior tests failed for missing status construction, bounded query adapter, confirmation cache, safe transaction link, and trace-status route.
- GREEN 2: `python -m pytest tests/test_azure_monitor_status.py tests/test_tracing_telemetry.py -q` passed with 16 tests after the implementation.
- RED 3: `test_agent_trace_emits_hashed_delivery_correlation_keys` failed because spans had no delivery hash attributes.
- GREEN 3: root and MAF participant spans now emit workspace/run/correlation hashes; the focused verification passed with 17 tests including the governance truthfulness regression.

## Delivery States

| State | Example | Meaning |
| --- | --- | --- |
| `not_configured` | No Application Insights connection string or required Logs workspace/application/resource identifiers. | The deployment cannot perform remote delivery proof. |
| `partial` | Exporter is configured and a local span was emitted, but there is no matching remote confirmation. | Local emission or exporter availability is not evidence of delivery. |
| `connected` | A bounded Azure Monitor query returns one trace whose workspace/run/correlation hashes, resource, application, and trace ID all match. | Remote delivery is confirmed for that exact scoped trace. |
| `unavailable` | Azure Monitor Logs query fails due to identity, permission, or network conditions. | Delivery cannot be confirmed; only a sanitized exception type is returned. |

## Safety Checks

- The Logs query uses only SHA-256 hashes for workspace, run, and correlation filters, a 15-minute time window, `take 1`, and a 10-second server timeout.
- The success-only 30-second cache key contains hashes of workspace, run, and correlation; misses and query failures are never cached.
- Status output has no actor data, Kusto rows, tokens, connection strings, credentials, or query text. Query exceptions retain only a bounded exception type.
- Portal URLs are generated only from canonical Azure resource IDs, GUID App Insights application IDs, and 32-hex trace IDs. Invalid values return no URL.
- Local span emission and an optional exporter callback are recorded separately. The Azure Monitor SDK integration currently exposes no exporter callback, so the status explicitly reports `exporter_state: unknown` until a callback is supplied.
- `GET /api/workspaces/{workspace_id}/governance/trace-status` requires `workspace.read`; a requested run is checked to belong to the path workspace before delivery status is queried.

## Verification

- Focused: `python -m pytest tests/test_governance_roi_summary.py::test_governance_reports_foundry_compatible_observability_truthfully tests/test_azure_monitor_status.py tests/test_tracing_telemetry.py -q` -> 17 passed.
- Full: `python -m pytest -q` -> 428 passed, 1 pre-existing experimental workflow warning.
- Compile: `python -m compileall -q backend` -> passed.
- Import smoke: `python -m backend.import_smoke` -> passed.
- Diff check: `git diff --check` -> passed; Git emitted a CRLF normalization notice for `backend/observability.py`, with no whitespace errors.
