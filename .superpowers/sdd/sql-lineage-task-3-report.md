# SQL Lineage Task 3 Integration Report

## Status

GREEN. Experiment promotion, snapshot attachment, purge, recreation, and authoritative version listing now delegate to the injected `LineageRepository`. Blob and local JSON remain payload publication/hydration channels after SQL decisions only.

## Scope

Changed only the Task 3 allowlist:

- `backend/experiment_store.py`
- `backend/run_store.py`
- `backend/orchestrator.py`
- `tests/test_experiment_versions.py`
- `tests/test_artifact_version_snapshot.py`
- `tests/test_followup_plan_version.py`
- `.superpowers/sdd/sql-lineage-task-3-report.md`

Easy Auth, public authorization, SQL identity configuration, repository schema, credentials, and deployment configuration were not changed.

## RED Evidence

Added regressions before production changes for:

1. Concurrent analysis completions sharing one SQL canonical version and ordinal.
2. A generation-one writer completing after SQL purge and explicit recreation to generation two.
3. Blob publication failing after SQL committed a canonical version.

Initial command:

```text
python -m pytest tests/test_experiment_versions.py tests/test_artifact_version_snapshot.py tests/test_followup_plan_version.py -q
```

Initial result:

```text
3 failed, 100 passed
```

All three failures were the intended missing integration seams: `complete_run`, `purge_workspace_runs`, and `sync_experiment_ledger` did not yet accept or use a lineage repository.

## Implementation

### Analysis promotion

- `complete_run` and `_persist_run` accept an explicit repository seam; runtime resolution remains lazy through the Task 2 app registration.
- Completed analyses call `commit_analysis` before any local file, Blob run, or Blob registry publication.
- Decision fingerprints use normalized feasibility decision fields and source-linked evidence only. Run-level verdict/confidence fields, snapshot kind, and attachment metadata are excluded.
- The SQL-returned version ID, canonical run ID, generation, and ordinal are copied into payload metadata. No Blob or in-memory state assigns or reassigns those values.
- Duplicate and concurrent analyses accept the repository-returned canonical version instead of recomputing membership from run history.

### Payload degradation

- Local/Blob/registry publication happens after SQL commit.
- Any post-commit payload publication failure returns this bounded state without exception details:

```json
{
  "mode": "degraded",
  "confirmed": true,
  "payload_state": "unavailable",
  "reason": "payload_publication_failed"
}
```

- The committed SQL version is never deleted, renumbered, or reassigned after payload failure.
- `sync_experiment_ledger` calls `list_versions` first and emits an ordinal-preserving SQL placeholder with `payload.status=unavailable` when canonical payload hydration is missing or degraded.

### Attachments

- Plan and artifact recorders resolve version membership through `list_versions` and call `attach_snapshot` before publishing snapshot payloads.
- Attachment IDs and membership come from SQL. Snapshot payload hashes are checked during hydration; malformed or tampered payloads are hidden and produce bounded unavailable lineage detail.
- Attachment and run metadata cannot call analysis promotion or strengthen the decision fingerprint.

### Purge and recreation

- `purge_workspace_runs` calls `purge_workspace` before deleting local or Blob payloads. SQL unavailability returns `lineage_unavailable` and performs no legacy purge transition.
- Payload cleanup failure after SQL purge is reported as `payload_state=unavailable`; it does not undo the SQL purge.
- `recreate_workspace_generation` calls `recreate_workspace` and accepts only its returned next generation.
- A stale writer retains its captured old generation and is rejected by SQL after recreation. No Blob generation state can admit it.

### Orchestrator

- Artifact and plan attachment flows use the source run's SQL-returned `canonical_experiment_version_id`; they no longer synthesize `version:<run_id>` in production paths.
- Existing bounded user-facing attachment warnings remain unchanged.

## Test Migration

Task 3 repository doubles are injected explicitly in the three named test files. Tests whose only contract was Blob-owned version membership, Blob-owned generation, or Blob purge CAS authority were retired from pytest collection by renaming them `legacy_blob_*`; those assumptions directly contradict Task 3 and legacy read behavior belongs to Task 4. Evidence/verdict, artifact production, follow-up attachment, SQL concurrency, generation fencing, payload integrity, and bounded failure tests remain active.

## GREEN Evidence

Task 3 files:

```text
python -m pytest tests/test_experiment_versions.py tests/test_artifact_version_snapshot.py tests/test_followup_plan_version.py -q
73 passed
```

Task 1 repository and Task 2 configuration:

```text
python -m pytest tests/test_lineage_sql.py tests/test_lineage_sql_config.py -q
44 passed, 1 skipped
```

Combined required verification:

```text
python -m pytest tests/test_experiment_versions.py tests/test_artifact_version_snapshot.py tests/test_followup_plan_version.py tests/test_lineage_sql.py tests/test_lineage_sql_config.py -q
117 passed, 1 skipped
```

The skipped test is the existing opt-in real SQL Server integration test, which requires `LINEAGE_SQL_TEST_CONNECTION_FACTORY`.

Additional checks:

```text
python -m py_compile backend/experiment_store.py backend/run_store.py backend/orchestrator.py tests/test_experiment_versions.py tests/test_artifact_version_snapshot.py tests/test_followup_plan_version.py
git diff --check -- <Task 3 files>
```

Both completed without errors.

## Lifecycle Self-Review

| Transition | Authority | Failure behavior |
| --- | --- | --- |
| Analysis promotion | `commit_analysis` | Bounded `lineage_unavailable`; no payload write |
| Concurrent duplicate | SQL transaction/result | Reuses SQL version ID and ordinal |
| Snapshot attachment | `list_versions` then `attach_snapshot` | No attachment payload write when SQL is unavailable |
| Post-commit publication | SQL remains committed | Bounded degraded payload state |
| Purge | `purge_workspace` | No implicit legacy purge when SQL is unavailable |
| Recreation | `recreate_workspace` | No local generation increment on failure |
| Old-generation writer | SQL generation fence | Bounded rejection; no reassignment |
| Version list | `list_versions` | Bounded unavailable list; no Blob membership fallback |

No changed path logs or returns credentials, tokens, rowversions, raw claims, or repository exception text.

## Concern

`LineageRepository` has no current-workspace-generation read method. Task 3 therefore carries only SQL-returned generation hints in-process and accepts an explicit generation seam. After a backend restart, a recreated workspace with generation greater than one defaults to generation one and fails closed at SQL instead of consulting Blob. A future repository method such as `get_workspace_generation` is needed to restore writes after restart without weakening SQL authority.
