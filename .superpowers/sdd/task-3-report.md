# Task 3 Report: Bounded Context Packs with Safe Legacy Fallback

## Scope

Implemented only the Task 3 backend surfaces:

- `backend/context_pack.py`
- `backend/conversation_store.py`
- `backend/orchestrator.py`
- `backend/run_store.py`
- `tests/test_context_pack.py`
- `tests/test_context_pack_integration.py`

No auth, frontend, deployment, or generated workspace data changes were made.
`progress.md` and the untracked `workspaces/ws-*` directories were left alone.

## Implementation

- Added `backend/context_pack.py` with a pure `build_context_pack(...)` builder.
  - Scope is always `workspace_id + conversation_id`.
  - Only allowlisted durable fact kinds are admitted:
    - `verified_constraint`
    - `selected_metric`
    - `accepted_scope`
    - `evidence_revision`
  - Pack content is bounded:
    - max 6 durable facts
    - max 8 evidence refs
    - max 6 workspace facts
    - max 6 audit constraints
  - Fingerprints are derived only from:
    - workspace/conversation scope
    - profile revision
    - analysis revision
    - evidence refs
    - durable fact IDs and kinds
  - Raw user message and raw durable-fact text are excluded from telemetry.
- Extended `backend/conversation_store.py` with:
  - `record_durable_fact(...)`
  - `conversation_durable_facts(...)`
  - durable facts persisted under the conversation document, filtered by exact
    workspace/conversation scope, and validated against the allowlist.
- Extended `backend/orchestrator.py` so only lightweight follow-up execution
  attempts Context Pack optimization.
  - Full analysis, audit repair, market research, and other evidence-heavy
    routes still use the existing context path.
  - When pack build succeeds:
    - `run_followup_assessment(...)` receives `context_pack`
    - `conversation_history` is emptied
  - When pack build or durable-fact lookup fails:
    - the reply still proceeds
    - legacy compact history is used
    - only safe fallback metadata is recorded
    - no raw exception details are exposed through `context_pack`
- Extended `backend/run_store.py` with `record_context_pack(...)`.
  - Persists only safe/public metadata:
    - `status`
    - `version`
    - `scope`
    - `fingerprint`
    - `durable_fact_ids`
    - `durable_fact_kinds`
    - counts
    - allowlisted fallback reasons
  - Drops arbitrary debug text or raw fact bodies.
  - Mirrors the metadata into one `context_pack` step event for traceability.

## TDD Evidence

### Red

1. `python -m pytest tests/test_context_pack.py::test_context_pack_is_scoped_bounded_and_invalidated_by_evidence_revision -q`
   - failed with `ModuleNotFoundError: No module named 'backend.context_pack'`
2. `python -m pytest tests/test_context_pack_integration.py::test_followup_uses_context_pack_and_falls_back_to_legacy_history_when_pack_build_fails -q`
   - after fixing one bad test import, failed because `backend.orchestrator`
     had no `build_context_pack` integration point

### Green

Focused Task 3 suites:

- `python -m pytest tests/test_context_pack.py -q`
  - `4 passed`
- `python -m pytest tests/test_context_pack_integration.py -q`
  - `3 passed`
- `python -m pytest tests/test_conversation_execution_linkage.py -q`
  - `3 passed`
- `python -m pytest tests/test_context_pack.py tests/test_context_pack_integration.py tests/test_conversation_execution_linkage.py -q`
  - `10 passed`

Follow-up regression coverage:

- `python -m pytest tests/test_followup_provisional_choice.py -q`
  - `23 passed`
- `python -m pytest tests/test_followup_plan_version.py -q`
  - `8 passed`

## Behavior verified

- Context Packs are scoped strictly to one workspace and one conversation.
- Cross-scope durable facts are excluded.
- Disallowed fact kinds are rejected.
- Pack fingerprint changes when evidence/analysis revision changes.
- Pack fingerprint does not change when only durable-fact wording changes.
- Lightweight follow-up falls back cleanly to legacy compact history when pack
  construction fails.
- The fallback still returns a usable reply.
- Persisted `context_pack` run metadata excludes raw fact text and arbitrary
  debug payloads.

## Residual risks

- Task 3 does not yet decide whether Context Pack routing is eligible by offline
  evaluation evidence. That gate remains Task 4.
- Generic lightweight follow-up currently sends both `context_pack` and the
  structured `last_analysis` summary. This is intentional for continuity and
  safety, but it means some legacy structured context still coexists with the
  new bounded pack.
- Durable facts are only stored when an explicit structured caller records
  them. This task intentionally does not infer facts from raw user text.

## Commit

- `718a22d` - `feat: add bounded followup context packs`

## Correction pass

Review feedback identified one real persistence leak and one missing regression.
This correction pass changed only:

- `backend/run_store.py`
- `tests/test_context_pack_integration.py`

### Root cause

- `record_context_pack(...)` already sanitized top-level run metadata, but
  `complete_run(..., artifact=...)` persisted `artifact["context_pack"]`
  through `_sanitize_artifact(...)` without applying the explicit Context Pack
  allowlist.
- Lightweight follow-up already had a durable-fact lookup fallback path, but no
  integration test locked that behavior in.

### Red

Focused red run:

- `python -m pytest tests/test_context_pack_integration.py -k "nested_artifact_context_pack_metadata or durable_fact_lookup_fails" -q`
  - failed first on
    `test_run_store_sanitizes_nested_artifact_context_pack_metadata`
  - persisted artifact still contained:
    - `profile_revision`
    - `analysis_revision`
    - `evidence_refs`
    - `debug_text`

### Green

After fixing the run-store boundary to re-sanitize nested
`artifact["context_pack"]` with `_sanitize_context_pack_metadata(...)`:

- `python -m pytest tests/test_context_pack_integration.py -k "nested_artifact_context_pack_metadata or durable_fact_lookup_fails" -q`
  - `2 passed`

Full focused Context Pack suite:

- `python -m pytest tests/test_context_pack.py tests/test_context_pack_integration.py tests/test_conversation_execution_linkage.py tests/test_followup_provisional_choice.py tests/test_followup_plan_version.py -q`
  - `43 passed in 6.62s`

### Behavior now locked

- Nested `artifact.context_pack` persists only the public allowlisted metadata:
  - `status`
  - `version`
  - `scope`
  - `fingerprint`
  - `durable_fact_ids`
  - `durable_fact_kinds`
  - `fact_count`
  - `workspace_fact_count`
  - `audit_constraint_count`
  - allowlisted fallback reasons when present
- Follow-up still succeeds when durable fact lookup raises.
- That fallback uses legacy compact history and records only
  `conversation_fact_lookup_failed`.
- Raw exception text does not leak into the returned follow-up payload.
