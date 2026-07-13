# P2-B Task 2 ROI Snapshot And Member Chargeback Report

## Scope

- Added `backend/roi_service.py` for source-linked workspace ROI snapshots and trusted-actor chargeback aggregation.
- Added `GET /api/workspaces/{workspace_id}/governance/roi?from=&to=` and `GET /api/workspaces/{workspace_id}/governance/chargeback?from=&to=`.
- Replaced legacy governance-summary monetary defaults with null cost/value fields so it cannot guess model price or convert saved time to cash.

## ROI Evidence States

| State | Evidence required | Example |
| --- | --- | --- |
| `estimated` | Observed usage, with no source-linked observed outcome | A dated model run has token usage but no outcome event; `business_value` is `null`. |
| `measured` | Source-linked observed outcome with observed value and observation time | `outcome-1` references `run-1` and has an observed value, but has no independent verification event. |
| `verified` | Measured outcome plus `verification_event_id` and a reviewer `actor_id` different from the outcome actor | `outcome-1` references `run-1`; `verification-1` is recorded by `actor-reviewer`, not `actor-owner`. |

## Unknown Cost Evidence

- Prices are read only from `DF_ROI_PRICE_CONFIG_JSON` or `DF_ROI_PRICE_CONFIG_FILE`; request handling does not call an external price source.
- `run_store` now persists the observed `model`/`model_name` identifier beside each model usage record, so versioned price matching has an authoritative model key.
- Each price row carries `version`, `currency`, `unit=per_1m_tokens`, `effective_from`, optional `effective_to`, and `source`.
- A run with an unknown model returns `cost.total: null`, `cost.status: partial`, and identifies the unpriced model. It never becomes zero.
- A member with only message/task attribution has `cost.total: null`, `cost.status: unknown`; no usage event is treated as a free model call.

## Member Attribution Evidence

- Aggregation accepts only `actor_id` from run, message, and task records.
- Email and display name are resolved only when the `actor_id` matches an active current workspace membership.
- Telemetry actor text is never surfaced by chargeback. Unmatched or departed members use a stable `actor_<hash>` identifier with `status: unknown_or_departed` and null email/name.
- ROI records and outcome events are filtered to the requested workspace before output.

## Permission Evidence

- ROI endpoint calls `workspace.read` authorization before calculation.
- Chargeback endpoint calls `chargeback.read` authorization before calculation. RBAC maps that action to owner/admin; compatibility mode remains explicitly labeled for existing non-RBAC deployments.
- `tests/test_actor_audit_usage.py::test_roi_and_chargeback_api_enforce_window_scope_and_member_comparison_role` proves ROI access, malformed-window rejection, editor denial for member comparison, owner success, and telemetry-email exclusion.

## Verification

- Focused: `python -m pytest tests/test_roi_service.py tests/test_outcome_roi.py tests/test_actor_audit_usage.py -q`
- Full: `python -m pytest -q`
- Compile/import: `python -m compileall backend -q` and direct imports of `backend.roi_service`, `backend.control_plane`, and `backend.app`.
- Diff review: inspected the staged task paths and confirmed no unrelated `output/` files are included.

## Review Remediation

- Chargeback now fails closed: only records with persisted `trusted_identity=true` are eligible. The endpoint requires proxy-verified Easy Auth, an `actor_id`, and an active owner/admin workspace membership even when compatibility RBAC is enabled.
- Price configuration is a strict Pydantic `PriceCatalog`; required metadata, finite non-negative rates, UTC half-open intervals, and no same-model overlap are enforced. Recalculation assumptions include the effective price fields.
- Costs are grouped by currency. Mixed currency makes aggregate `total` null with `partial` status; no USD fallback exists. Total-only usage is partial, and invalid numeric values are rejected.
- Chargeback groups include actor, currency, model, task kind, and window. Usage-event IDs are deduplicated; message/task records contribute only activity.
- Outcome sources are checked at write and read against same-workspace run/file/artifact references. Verification embeds a separate trusted verification event, and a snapshot is `verified` only when every business-value outcome is independently verified; otherwise it is `measured` with unverified IDs.
- Departed identities use workspace-scoped HMAC pseudonyms derived from a configured secret. Window filtering occurs before the 300-record bound and responses expose `truncated` when the bound is hit.

## Remediation Tests

- `tests/test_roi_security.py` covers strict price validation, non-finite rates, overlapping windows, total-only token usage, mixed verified/unverified outcomes, untrusted actor exclusion, currency breakdown, HMAC workspace isolation, duplicate usage IDs, and forged-source exclusion.
- `tests/test_actor_audit_usage.py` covers trusted Easy Auth identity requirements and persisted run trust metadata.
