# P2-B Task 5 Implementation Report

Status: `DONE_WITH_CONCERNS`

Base: `c3f9bedfc56e0703d6c3e0f5f924ccf78609ac0c`

## Scope

Implemented an append-only workspace audit ledger in the owned backend surface. The implementation adds:

- Blob-backed per-workspace journals with ETag compare-and-swap retries.
- Monotonic revisions and an HMAC-authenticated hash chain.
- A fail-closed local journal with an atomic per-store HMAC key, exclusive cross-process append locks, atomic replacement, and full-chain validation.
- HMAC-pseudonymous actors with no raw email, OID, tenant identity, token, prompt, content, provider body, credential, or connection string in the persisted/API schema.
- Strict action, resource type, result, reason code, and correlation field allowlists.
- Deterministic event IDs when safe correlation is present, so retries are idempotent across CAS conflicts.
- Owner/admin-only bounded pagination at `GET /api/workspaces/{workspace_id}/governance/audit-events` without changing the legacy `/api/workspaces/{workspace_id}/audit-events` route.
- Read-only permission disclosure: `role`, `can_read=true`, `can_update=false`, and `can_delete=false`.
- No update or delete audit-store interface.

## Event Contract

Persisted events contain only:

`event_id`, `workspace_id`, `actor_hash`, `action`, `resource_type`, `resource_id`, `result`, `reason_code`, `correlation`, `at`, `revision`, `previous_hash`, and `event_hash`.

The timestamp must parse as UTC. Every loaded event is revalidated against all policy allowlists before its HMAC chain is accepted. Arbitrary metadata keys are discarded rather than serialized.

## Coverage Matrix

| Required surface | Instrumentation | Failure/denial behavior | Correlation/resource |
| --- | --- | --- | --- |
| Existing workspace upload | `backend/app.py` pre-write `file.create` | Audit failure returns 503 before upload mutation; authorization denial remains 403 | request ID; workspace/upload |
| New workspace upload | `backend/app.py` after actual workspace allocation and before ingest scheduling | Audit failure returns 503 and prevents ingest scheduling | request ID; actual workspace/upload |
| File create/edit/delete/mapping | `backend/data_workbench.py` audited call wrapper | Required `allowed` before mutation; best-effort `failed` after operation failure; denied action audited without changing 403 | request ID; file ID or pending |
| Connector connect/reconnect/sync/import/disconnect/delete | `backend/data_workbench.py` audited call wrapper | Same required allowed/failed/denied semantics | request ID; connector ID |
| Analysis and user-message attempts | app chat/auto-analysis and workbench selected-file analysis | Both `analysis.run` and `message.create` are required before work starts; task failures are separately terminal-audited | request ID; analysis/message |
| Durable task create/start/cancel/complete/fail | `backend/task_store.py` | Audit failure raises `TaskPersistenceError` before state mutation; terminal failure emits `task.fail` with result `failed` | task ID |
| Artifact generation | synchronous produce and durable artifact-job entrypoints in `backend/app.py` | Required audit before generation/job creation; durable task lifecycle records completion/failure | request ID and task ID |
| Outcome record/verify | `backend/control_plane.py` | Required audit before mutation; best-effort failed event on operation failure; denied actions remain denied | request ID and outcome event ID |
| Invitation create/send/revoke | `backend/control_plane.py` | Required audit before invitation/provider/member mutation; denied member management remains denied | request ID; invitation/pending |
| Member role/remove | `backend/control_plane.py` | Required audit before member mutation | request ID; pseudonymous member resource |
| Experiment promotion extension | `audit_experiment_promotion(...)` | Safe required pre-mutation hook for a later endpoint | request ID and experiment version ID |
| Authorization denial | audited helpers in app, control plane, and workbench | Audit persistence is best-effort only for denial logging; it can never turn denial into allow | original allowlisted action; workspace |
| Governance read API | control-plane route | Trusted persisted owner/admin only; invalid cursor/limit rejected; store/read failure maps to 503 | opaque revision cursor |

## Durability and Integrity

- Blob mode reads the strict Blob JSON surface, validates the complete ledger, appends one event, and commits with revision plus ETag CAS. A conflict reloads and retries up to eight times. If the deterministic event ID already exists, the existing event is returned.
- Local mode creates a random 256-bit HMAC key with create-if-absent semantics, then serializes appenders through an exclusive lock file. An unavailable/stale lock, malformed key, malformed journal, revision gap, duplicate event, non-UTC timestamp, policy-invalid field, or HMAC mismatch fails closed.
- The API paginates newest-first with an opaque revision cursor and a hard maximum of 100 events per page.

## Privacy Controls

| Input class | Persistence behavior |
| --- | --- |
| Raw email/OID/tenant actor identity | Reduced to HMAC `actor_hash` |
| Prompt, message, file content, arbitrary metadata | Dropped because no schema field accepts it |
| Passwords, tokens, credentials, provider bodies, connection strings | Dropped because no schema field accepts them |
| Correlation | Only request, run, task, invitation, connector, outcome event, and experiment version fields are considered; unknown fields are dropped and values must be safe identifiers |
| Resource/action/result/reason | Strict allowlists; invalid values fail before persistence |

## TDD Evidence

### Red 1: Missing store

Command:

```powershell
python -m pytest tests/test_audit_store.py tests/test_actor_audit_usage.py -q
```

Observed:

```text
ERROR tests/test_audit_store.py
ModuleNotFoundError: No module named 'backend.audit_store'
1 error in 4.79s
```

### Red 2: Missing integration semantics

Command:

```powershell
python -m pytest tests/test_actor_audit_usage.py -q
```

Observed:

```text
5 failed, 15 passed in 5.70s
```

The failures covered mandatory pre-write gating, the missing governance route, missing task lifecycle events, task audit fail-closed behavior, and the missing experiment-promotion hook.

### Red 3: Rehashed policy-invalid history

Command:

```powershell
python -m pytest tests/test_audit_store.py::test_rehashed_policy_invalid_event_is_rejected -q
```

Observed:

```text
3 failed in 1.49s
```

This forced the reader to reapply action, actor-hash, and UTC policy validation independently of chain verification.

### Red 4: Predictable local HMAC key

Command:

```powershell
python -m pytest tests/test_audit_store.py::test_local_store_creates_and_reuses_private_hmac_key tests/test_audit_store.py::test_blob_store_requires_deployment_hmac_key -q
```

Observed:

```text
1 failed, 1 passed in 1.33s
```

This forced local mode to create and reuse a private random key rather than use a fixed development key.

### Green: Binding focused suite

Command:

```powershell
python -m pytest tests/test_audit_store.py tests/test_actor_audit_usage.py -q
```

Observed:

```text
37 passed in 5.90s
```

### Green: Impacted regression sweep

Command covered audit, actor, task, task API, artifact jobs, control-plane task persistence, workbench connectors/tasks, Entra invitations, outcomes, experiments, and workspace roles.

Observed:

```text
225 passed in 44.77s
```

### Green: Full repository

Command:

```powershell
python -m pytest -q
```

Observed:

```text
579 passed, 1 warning in 65.84s (0:01:05)
```

The single warning is the existing `ExperimentalWarning` from `backend/maf_team_runtime.py` exercised by `tests/test_maf_evaluation_contract.py`.

## Mechanical Verification

```text
python -m compileall -q backend tests
exit 0

import check
imports=ok routes=ok append_only_api=ok

git diff --check
exit 0
```

The import check also asserted that both legacy and governance audit routes exist and that no `update_audit_event` or `delete_audit_event` symbol exists.

## Concerns

1. Blob mode intentionally requires `DF_AUDIT_HMAC_KEY`; without it, audited mutations fail closed. No auth configuration was changed in this task, so deployment must provide and retain this secret before rollout.
2. For uploads that create a brand-new workspace, the actual workspace ID is allocated inside the out-of-scope workspace store. The ledger therefore records the required event immediately after the upload job allocates the real workspace and before ingest scheduling. If that audit write fails, the API returns 503 and ingest is not scheduled, but the newly allocated upload job may remain for recovery/cleanup. Existing-workspace uploads are audited before mutation and are covered by a test proving the upload function is not called on audit failure.

`output/` and auth configuration were not touched.
