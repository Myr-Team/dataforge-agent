# SQL Lineage Final Fix Report

Date: 2026-07-16

## Result

1. Purge now requires an existing SQL workspace row. An unseen workspace raises bounded lineage unavailable before local or Blob payload cleanup, and purge no longer creates an empty SQL lineage row.
2. Failure to establish SQL now validates legacy history through the existing read-only path. Complete history returns `source=legacy_blob` with `legacy_read_only`; incomplete or mismatched history remains unavailable. Once a SQL row is observed, SQL remains authoritative.
3. The backend image copies only `scripts/verify_lineage_sql.py` and executes its no-Azure `--check-prerequisites` mode during the image build before the backend import smoke.

No Azure deployment, Azure resource write, Easy Auth change, secret handling change, or Blob lineage write fallback was performed.

## Changed Files

- `backend/lineage_sql.py`
- `backend/experiment_store.py`
- `backend/Dockerfile`
- `tests/test_lineage_sql.py`
- `tests/test_control_plane_runs.py`
- `tests/test_backend_image_import_smoke.py`
- `.superpowers/sdd/sql-lineage-final-fix-report.md`

`backend/run_store.py`, `tests/test_experiment_versions.py`, and unrelated untracked `output/` content were not changed.

## TDD Evidence

### Finding 1: absent SQL workspace purge

RED:

```powershell
python -m pytest -q tests/test_lineage_sql.py::test_purge_without_sql_workspace_preserves_legacy_payload
```

Observed: `1 failed`; purge returned `purged`, deleted one local payload, and created/updated SQL lineage.

GREEN:

```powershell
python -m pytest -q tests/test_lineage_sql.py::test_purge_without_sql_workspace_preserves_legacy_payload
```

Observed: `1 passed`.

Related lifecycle GREEN:

```powershell
python -m pytest -q tests/test_lineage_sql.py::test_purge_is_terminal_until_explicit_recreation tests/test_lineage_sql.py::test_repository_reads_current_generation_and_sql_attachment_rows tests/test_lineage_sql.py::test_purge_without_sql_workspace_preserves_legacy_payload
```

Observed: `3 passed`.

### Finding 2: SQL-unavailable legacy rollback read

RED:

```powershell
python -m pytest -q tests/test_control_plane_runs.py::test_complete_legacy_history_is_read_only_when_sql_configuration_is_unavailable
```

Observed: `1 failed`; the ledger returned `source=sql_lineage` instead of validated `legacy_blob` read-only history.

GREEN:

```powershell
python -m pytest -q tests/test_control_plane_runs.py::test_complete_legacy_history_is_read_only_when_sql_configuration_is_unavailable
```

Observed: `1 passed`.

Legacy authority and validation GREEN:

```powershell
python -m pytest -q tests/test_control_plane_runs.py::test_public_ledger_rechecks_sql_after_legacy_validation tests/test_control_plane_runs.py::test_complete_legacy_history_is_read_only_and_blob_attachments_are_not_membership tests/test_control_plane_runs.py::test_complete_legacy_history_is_read_only_when_sql_configuration_is_unavailable tests/test_control_plane_runs.py::test_mismatched_legacy_attachment_payload_exposes_no_partial_versions tests/test_control_plane_runs.py::test_incomplete_legacy_public_history_exposes_no_partial_versions
```

Observed: `5 passed`.

### Finding 3: verifier image packaging

RED:

```powershell
python -m pytest -q tests/test_backend_image_import_smoke.py
```

Observed: `1 failed, 2 passed`; the exact verifier copy instruction was absent.

GREEN:

```powershell
python -m pytest -q tests/test_backend_image_import_smoke.py
```

Observed: `3 passed`.

## Verification

Owned test set:

```powershell
python -m pytest -q tests/test_experiment_versions.py tests/test_control_plane_runs.py tests/test_lineage_sql.py tests/test_backend_image_import_smoke.py
```

Observed: `83 passed, 1 skipped`.

Related SQL configuration and purge/recreate coverage:

```powershell
python -m pytest -q tests/test_lineage_sql_config.py tests/test_artifact_version_snapshot.py
```

Observed: `52 passed`.

Final combined relevant suite:

```powershell
python -m pytest -q tests/test_experiment_versions.py tests/test_control_plane_runs.py tests/test_lineage_sql.py tests/test_backend_image_import_smoke.py tests/test_lineage_sql_config.py tests/test_artifact_version_snapshot.py
```

Observed: `135 passed, 1 skipped`.

Local no-Azure verifier:

```powershell
python scripts/verify_lineage_sql.py --check-prerequisites
```

Observed: exit `0`, `status=ok`, `azure_checked=false`, fail-closed and schema source verified. Local ODBC Driver 18 registration was `false`; this is an allowed observation in prerequisite mode.

Full repository suite:

```powershell
python -m pytest -q
```

Observed: `871 passed, 1 skipped, 7 failed, 1 warning`.

The seven failures are outside the owned files: four generic run persistence expectations in `tests/test_actor_audit_usage.py`, two in `tests/test_capability_pack_integration.py`, and one order-dependent lazy connection outcome assertion in `tests/test_lineage_sql_config.py`. The first six reproduce in their files without this task's test set; the lazy outcome assertion passes when run alone. No out-of-scope production or test files were changed.

Docker image build was not run because the machine has no `docker` executable. The source-level image contract and in-process verifier execution are covered by the passing Docker smoke tests; this is not claimed as a completed image build.

`git diff --check` completed with exit `0`.

## Commit

Commit message: `fix: close sql lineage release blockers`
