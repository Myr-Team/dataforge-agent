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
| `verified` | Measured outcome plus a persisted matching verification event; both actors have trusted non-empty tenant identities and are different after tenant/actor normalization | `outcome-1` references `run-1`; `verification-1` is recorded by `tenant-a/actor-reviewer`, not `tenant-a/actor-owner`. |

## Unknown Cost Evidence

- Prices are read only from `DF_ROI_PRICE_CONFIG_JSON` or `DF_ROI_PRICE_CONFIG_FILE`; request handling does not call an external price source.
- `run_store` now persists the observed `model`/`model_name` identifier beside each model usage record, so versioned price matching has an authoritative model key.
- Each price row carries `version`, `currency`, `unit=per_1m_tokens`, `effective_from`, optional `effective_to`, and `source`.
- A run with an unknown model returns `cost.total: null`, `cost.status: partial`, and identifies the unpriced model. It never becomes zero.
- A member with only message/task attribution has `cost.total: null`, `cost.status: unknown`; no usage event is treated as a free model call.

## Member Attribution Evidence

- Aggregation accepts only persisted Easy Auth actor metadata with a non-empty tenant ID from run, message, and task records.
- Email and display name are resolved only when the tenant-scoped actor identity matches an active current workspace membership.
- New conversation messages persist a generated `message_id`. Historical messages without one receive a workspace-scoped derived ID from conversation ID, index, and immutable message fields during chargeback reads.
- Telemetry actor text is never surfaced by chargeback. Unmatched or departed members use a stable `actor_<hash>` identifier with `status: unknown_or_departed` and null email/name.
- ROI records and outcome events are filtered to the requested workspace before output.

## Permission Evidence

- ROI endpoint fails closed before data reads, including RBAC compatibility mode: it requires proxy-verified Easy Auth, actor ID, tenant ID, and a current active workspace role (`viewer` or above).
- Chargeback uses the same trusted active-membership check and additionally requires `owner` or `admin`.
- `tests/test_actor_audit_usage.py::test_roi_and_chargeback_api_enforce_window_scope_and_member_comparison_role` proves ROI access, malformed-window rejection, editor denial for member comparison, owner success, and telemetry-email exclusion.

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

## Second Review Remediation

- Any unpriced or unknown-currency run usage now makes its group, owning member, and workspace aggregate `cost.total: null` with `cost.status: partial`. Priced currency breakdown remains visible for audit, but is never promoted to a misleading total.
- Snapshot verification reads persisted `verification_events`, not embedded outcome data. A verification event must have its own exact ID, matching workspace and `outcome_verification` kind, a trusted Easy Auth reviewer, a matching canonical reviewer identity, and a reviewer distinct from the outcome actor after lower-case tenant/actor normalization.
- `verified` now requires every in-window source-linked observed outcome, including outcomes with no `business_value`, to have that valid independent event. All missing or invalid evidence IDs appear in `unverified_outcome_event_ids`.
- Source checks use exact same-workspace lookups: run ID through `get_run`, canonical document hash for file ID, and artifact job/registry ID through `get_artifact_job`. Filename and path wildcard matching are not accepted.
- Chargeback authorization now directly resolves the canonical current workspace role after requiring trusted Easy Auth. A `workspace_owner` row remains owner even without a `role` field; editors are denied. Member attribution uses tenant-scoped canonical identity keys.
- `BusinessValueSummary`, `TimeValueSummary`, `ChargebackGroup`, `ChargebackMember`, and cost structures are Pydantic contracts. Business values, costs, rates, tokens, and currency breakdowns reject negative, NaN, or infinite input; currencies require `^[A-Z]{3}$`.
- Message and task activity are deduplicated by workspace, kind, and stable message/task ID. Responses expose `duplicate_event_ids` and `duplicate_event_count`; run usage event de-duplication remains separate.

## Third Review Remediation

- Aggregate ROI reads are fail-closed even when compatibility RBAC is enabled. The endpoint validates proxy-verified Easy Auth plus non-empty actor/tenant identity and resolves only a current persisted active workspace role before invoking any snapshot read.
- Tenant-scoped canonical identities require lower-case non-empty tenant and actor IDs. Missing tenant IDs cannot create a verified ROI state or pass outcome verification; case changes and empty/non-empty tenant combinations cannot bypass independent review.
- Conversation storage assigns and persists `message_<id>` for every new message with persisted trusted actor metadata. Historical messages derive deterministic `legacy_message_<hash>` IDs from workspace, conversation, index, and immutable fields; chargeback still excludes any non-Easy-Auth actor source.
- `CostSummary` now enforces status consistency: `complete` requires one matching total/currency breakdown, `partial` exposes no singular total/currency while retaining known currency amounts, and `unknown` exposes no priced breakdown.

## Fresh Verification

- Previous report counts, including `458` and `459`, are explicitly superseded and are not used for this acceptance record. This report does not infer why those historical counts differ.
- Focused command at the current implementation: `python -m pytest -q tests/test_actor_audit_usage.py tests/test_roi_security.py tests/test_roi_service.py tests/test_outcome_roi.py tests/test_workspace_roles.py` -> `84 passed`.
- Full command started `2026-07-13T22:37:21.7123096+08:00` and completed `2026-07-13T22:38:11.7620133+08:00`: `python -m pytest -q` -> `465 passed, 1 existing ExperimentalWarning` from `backend/maf_team_runtime.py:1060`.
- Compile/import: `python -m compileall -q backend`; direct imports of `backend.control_plane`, `backend.conversation_store`, `backend.outcome_store`, and `backend.roi_service`.
- Diff: `git diff --check` completed with no whitespace errors. Unrelated untracked `output/` remains excluded.
