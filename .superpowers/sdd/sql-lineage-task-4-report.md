# SQL Lineage Task 4 Report

## Status

GREEN for the Task 4 contract and the required Task 3/SQL regression suites.

Public experiment ledgers now resolve SQL workspace existence, generation, canonical versions, attachment rows, and display ordinals before optional Blob hydration. Blob-only workspaces can be shown only as a validated read-only legacy ledger; incomplete or mismatched histories return a bounded `legacy_unavailable` response with no partial versions.

An explicit one-workspace migration command validates the full legacy registry/payload history, rejects existing SQL lineage, defaults to dry-run, and imports all canonical versions in one SQL transaction. Blob attachment declarations are never imported as SQL attachment membership.

## Scope

Changed only the Task 4 allowlist:

- `backend/control_plane.py`
- `backend/experiment_store.py`
- `backend/migrate_lineage_sql.py`
- `tests/test_experiment_versions.py`
- `tests/test_control_plane_runs.py`
- `.superpowers/sdd/sql-lineage-task-4-report.md`

Easy Auth, public authorization, frontend code, SQL identity configuration, schema files, tokens, claims, and credentials were not changed.

## RED Evidence

The first Task 4 regressions were added before production changes:

```text
python -m pytest tests/test_experiment_versions.py::test_legacy_migration_rejects_incomplete_history_without_partial_sql_write tests/test_experiment_versions.py::test_legacy_migration_rejects_registry_payload_mismatch_without_partial_sql_write tests/test_control_plane_runs.py::test_public_ledger_reads_sql_ordinals_before_blob_run_listing -q

3 failed
```

Expected failures:

1. `backend.migrate_lineage_sql` did not exist for incomplete-history and payload-mismatch tests.
2. `workspace_experiment_ledger` listed Blob runs before reaching SQL, so SQL ordinal display was unavailable when Blob listing failed.

Additional test-first review corrections:

- Existing SQL lineage was initially checked after Blob validation: `1 failed`; the SQL existence rejection now short-circuits Blob reads.
- Optional outcome hydration initially ran before SQL: `1 failed`; a failed outcome read now degrades to an empty display set without suppressing SQL versions.
- A migration/read race initially returned a stale legacy ledger after SQL appeared: `1 failed`; the read now re-checks SQL after legacy validation.

## Public Read Contract

- The control plane no longer enumerates up to 300 Blob runs before calling the experiment ledger.
- SQL workspace existence is checked directly; empty existing SQL lineage remains an authoritative empty SQL generation and is not mistaken for legacy data.
- SQL versions are sorted and labeled from SQL `ordinal` values. Blob ordering or ordinal-like fields cannot renumber them.
- Canonical payloads are optional hydration. Missing payloads preserve the SQL version and ordinal with `payload.status=unavailable`.
- SQL attachment rows are loaded before scanning Blob candidates. A Blob snapshot hydrates only when version, kind, source run, and payload SHA-256 match a SQL attachment row.
- A SQL attachment row with no matching payload makes hydration unavailable; a Blob-only attachment declaration has no membership effect.
- Complete Blob-only histories return `lineage_resolution.status=read_only` and `reason=legacy_read_only`.
- Truncated, missing, non-contiguous, or payload-mismatched legacy histories return no versions with `reason=legacy_unavailable`.
- SQL is re-checked after legacy validation so a concurrent completed migration wins in the same read request.

## Migration Contract

Commands:

```text
python -m backend.migrate_lineage_sql --workspace-id <workspace-id> --dry-run
python -m backend.migrate_lineage_sql --workspace-id <workspace-id> --apply
```

The command requires exactly one workspace ID. Omitting `--apply` remains dry-run; `--dry-run` and `--apply` are mutually exclusive.

Validation requires:

- a readable registry with `history_truncated=false`;
- unique workspace run IDs;
- full payload availability for lineage-relevant rows;
- exact registry/payload lineage envelopes;
- recomputed analysis content hashes and legacy commit IDs;
- contiguous analysis sequence numbers starting at one;
- canonical targets present in the validated history with matching target commit and content hashes;
- valid attachment payload hashes for attachment-like legacy rows.

Migration behavior:

- Any existing SQL workspace row returns `rejected/sql_lineage_exists` before Blob history is read.
- Apply repeats the existence check under `UPDLOCK, HOLDLOCK` to close the validation/insert race.
- The workspace row and every canonical version are inserted in one repository transaction. Any insert failure rolls back the entire batch.
- SQL UUID version IDs and contiguous ordinals are generated for validated canonical runs only.
- Legacy aliases are validation evidence but do not become duplicate SQL versions.
- Legacy attachment rows are counted for operator visibility but `attachment_imported_count` is always zero. Blob data cannot create SQL attachment membership.
- Returned errors are bounded categories only; exception text, tokens, connection data, credentials, rowversions, and raw claims are not logged or returned.

## GREEN Evidence

Focused Task 4 files:

```text
python -m pytest tests/test_control_plane_runs.py tests/test_experiment_versions.py -q
63 passed
```

Task 4 plus required Task 3, repository, and configuration suites:

```text
python -m pytest tests/test_control_plane_runs.py tests/test_experiment_versions.py tests/test_artifact_version_snapshot.py tests/test_followup_plan_version.py tests/test_lineage_sql.py tests/test_lineage_sql_config.py -q
138 passed, 1 skipped
```

The skip is the existing opt-in real SQL Server integration test requiring `LINEAGE_SQL_TEST_CONNECTION_FACTORY`.

Static and command checks:

```text
python -m py_compile backend/control_plane.py backend/experiment_store.py backend/migrate_lineage_sql.py tests/test_experiment_versions.py tests/test_control_plane_runs.py
git diff --check
python -m backend.migrate_lineage_sql --help
```

All completed successfully. The CLI help showed required `--workspace-id` and mutually exclusive `--dry-run | --apply` modes.

## Full-Suite Concerns

`python -m pytest -q` completed with `866 passed, 8 failed, 1 skipped`. The failures are outside the Task 4 allowlist and have no Task 4 stack frames:

- Four `tests/test_actor_audit_usage.py` cases fail because generic runs are not persisted by current `run_store` behavior. All four reproduce when run alone.
- Two `tests/test_capability_pack_integration.py` cases fail for the same unchanged generic run persistence behavior. Both reproduce when run alone.
- `tests/test_backend_image_import_smoke.py` expects `RUN python -m backend.import_smoke` in the existing Dockerfile. It reproduces alone.
- `tests/test_lineage_sql_config.py::test_app_lineage_repository_registration_is_lazy_without_configuration` observed a prior connection outcome only in the full run and passes alone, so it is order-sensitive shared state.

Those files were not changed because the user explicitly limited Task 4 writes.

No live Azure SQL migration was applied in this task. The real managed-identity connection and schema/deployment smoke remain Task 5 release-gate work.

## Review

Manual review covered the six allowlisted paths because no subagent review tool was available. The main maintainability constraint is that the migration module uses the repository's transaction context directly for an atomic bulk import; a future public repository bulk-import API would be preferable, but adding it would require changing `backend/lineage_sql.py` outside the Task 4 write allowlist.
