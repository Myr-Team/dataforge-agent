# SQL Lineage Task 1 Report

## Status

Complete. Task 1 adds the SQL lineage repository and schema only. No application call sites, Blob fallback, Easy Auth behavior, managed-identity connection setup, or runtime deployment configuration were changed.

## Changed Files

- `backend/lineage_sql.py`
  - Adds `LineageRepository`, `LineageUnavailable`, `VersionCommit`, and `AttachmentCommit`.
  - Requires an explicit pyodbc-style connection factory; there is no default or silent fallback.
  - Adds transactional version commit, attachment commit, purge, recreation, schema initialization, and version listing APIs.
- `backend/sql/lineage_schema.sql`
  - Adds idempotent Azure SQL DDL for workspace lineage, experiment versions, attachments, and generation events.
  - Adds unique canonical ordinals, composite attachment foreign keys, lifecycle checks, and no rowversion column.
- `tests/test_lineage_sql.py`
  - Adds an explicitly injected in-memory DB-API test double.
  - Covers duplicate and distinct parallel commits, purge/recreate fencing, attachment validation, schema idempotency, parameter binding, required lock hints, and sensitive metadata rejection.
- `backend/requirements.txt`
  - Adds `pyodbc==5.2.0`.
  - Retains the pre-existing `pymssql` dependency because `backend/data_workbench.py` still consumes it outside this task; the lineage repository does not use it.
- `.superpowers/sdd/sql-lineage-task-1-report.md`
  - Records Task 1 implementation and verification evidence.

## TDD Evidence

### Initial RED

Command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: expected failure, exit code 1. `6 failed in 0.57s`; every failure was `ModuleNotFoundError: No module named 'backend.lineage_sql'` before production implementation existed.

### Initial GREEN

Command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: exit code 0. `6 passed in 0.13s`.

### Security Hardening RED

Command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: expected failure, exit code 1. `1 failed, 6 passed in 0.41s`; token-shaped actor metadata such as `id_token` was not yet rejected.

### Final GREEN

Command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: exit code 0. The final pre-commit run completed with `7 passed in 0.11s` (the first hardening GREEN run was `7 passed in 0.17s`).

## Self-Review

- Transaction correctness: each repository operation obtains a fresh explicit connection, disables autocommit, commits only after the operation returns through the context manager, rolls back on all failures, and closes without allowing cleanup errors to replace the bounded result. Purge state transition, attachment deletion, version deletion, tombstone transition, and event insertion are atomic.
- Locking: workspace creation and lifecycle transitions serialize on `workspace_lineage WITH (UPDLOCK, HOLDLOCK)`. Latest-version comparison, version membership validation, and attachment idempotency checks also use `UPDLOCK, HOLDLOCK`. The workspace lock is held until commit, so canonical ordinal allocation and purge/recreation fencing share one transaction boundary.
- Injection risk: all dynamic values use `?` parameters. SQL identifiers and statements are static. The only formatted execution is the trusted repository-owned schema file, which contains no runtime values and no `GO` separators.
- Redaction: unexpected connection or database errors become the fixed `LineageUnavailable("lineage database operation failed")` message with exception chaining suppressed. The module does not log operation parameters or exceptions. The schema and repository contain no rowversion storage, selection, or return values. Actor metadata uses a typed allowlist: UUID `actor_id`/`request_id`, enumerated `actor_type`, and bounded numeric `actor_sequence`; unknown or nested fields are rejected before opening a connection.
- Foreign keys: attachments have an application-level locked membership check and a database-level composite foreign key to the exact version/workspace/generation tuple.

## Commit

- `feat: add transactional lineage sql repository` (the single commit containing this report; its resulting hash is reported in the task status because a commit cannot contain its own final hash).

## Concerns

- This Task 1 test run uses the required explicitly injected in-memory DB-API test double; it does not execute against a live SQL Server engine.
- Microsoft ODBC Driver 18 system-package installation, managed-identity token construction, connection timeouts, and Azure smoke verification remain intentionally deferred to Tasks 2 and 5.

## Review R1 Follow-Up

### Fixes

- Removed the `ROWVERSION` column from the SQL schema. The repository neither stores, selects, nor returns rowversion values.
- Removed caller-controlled `verdict` and `confidence` from `commit_analysis`, `VersionCommit`, SQL selection, and persistence. This repository accepts only normalized decision and evidence fingerprints; evidence-strength interpretation remains an upstream responsibility.
- Replaced metadata key matching with a narrow typed allowlist:
  - `actor_id` and `request_id` must be UUID strings.
  - `actor_type` must be `member`, `service`, or `system`.
  - `actor_sequence` must be a bounded non-negative integer.
  - Unknown fields, nested values, booleans, and values that do not meet their field type are rejected before a connection is opened.
- Strengthened the explicitly injected in-memory DB-API test double. It now asserts the repository-issued DDL excludes prohibited columns and includes idempotent ordinal/FK constraints, requires `UPDLOCK, HOLDLOCK` on every repository lock query, and checks attachment membership as the fake equivalent of the composite foreign-key boundary.

### Review R1 RED

Command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: expected failure, exit code 1. `5 failed, 5 passed in 0.49s`. The failures demonstrated the old prohibited DDL, accepted caller-selected strength, and accepted token/claim/credential values under benign metadata keys.

### Review R1 GREEN

Command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: exit code 0. `11 passed in 0.18s`.

### Live SQL Limitation and Release Prerequisite

The in-memory double is an explicit dependency-injection test seam, not a runtime fallback and not an Azure SQL compatibility claim. It verifies the SQL contract issued by the repository, but cannot validate SQL Server range locks, `HOLDLOCK` behavior, repeated DDL execution, or foreign-key enforcement by the Azure SQL engine. A disposable Azure SQL or SQL Server engine run covering concurrent ordinal allocation, repeat schema application, and attachment FK rejection is a Task 5 release prerequisite.

## Review R2 Follow-Up

### Corrections and Opt-In Engine Coverage

- Corrected the stale initial report text that said the schema stored rowversion. The current DDL has no rowversion column, and the repository neither stores, selects, nor returns one.
- Corrected the stale metadata description. The current boundary is a typed explicit allowlist, not a denylist.
- Added `test_real_sql_server_schema_concurrency_and_attachment_foreign_key`. It is skipped unless `LINEAGE_SQL_TEST_CONNECTION_FACTORY` contains a non-secret `module:function` reference to an already-provisioned external connection factory. The test source contains no connection string, credential, or fallback connection behavior.
- When explicitly enabled by the Task 5 release environment, the test uses a unique workspace ID, applies schema initialization twice, commits two distinct analyses concurrently and requires ordinals `1` and `2`, then attempts an attachment tuple with the committed version ID but a non-existent generation. It treats that check as validated only when the driver raises an integrity error whose diagnostic identifies the expected composite foreign key. It deletes the unique workspace rows in a `finally` block.

### Review R2 Test-First Evidence

The integration test was added before any repository refactor. No production change was necessary because `LineageRepository` already accepts only explicit injected connection factories.

Command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: initial gating run, exit code 0: `11 passed, 1 skipped in 0.14s`. Final focused verification, exit code 0: `11 passed, 1 skipped in 0.10s`. The skip is intentional and explicit when `LINEAGE_SQL_TEST_CONNECTION_FACTORY` is absent, so normal test runs never attempt a live SQL connection.

### Remaining Release Prerequisite

Live engine execution is deliberately limited to Task 5. That release environment must supply the external non-secret factory reference and run the opt-in test against a disposable or otherwise dedicated SQL Server/Azure SQL database before release.

## Review R3 Follow-Up

### Foreign-Key Failure Classification

- The opt-in engine test no longer accepts a broad `Exception` for the invalid attachment insert.
- It requires `pyodbc.IntegrityError`, then requires SQLSTATE `23000` and the exact `FK_experiment_attachment_version` constraint name in the driver diagnostic. Connectivity, authentication, permissions, SQL syntax, and transient failures therefore fail the test instead of passing the foreign-key check.
- Added focused classifier regressions covering the expected SQL Server foreign-key diagnostic, an authentication diagnostic that mentions the FK name, and a different foreign-key diagnostic.

### Review R3 TDD Evidence

RED command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: expected failure, exit code 1. `3 failed, 11 passed, 1 skipped in 0.38s`; each failure was the missing foreign-key diagnostic classifier.

GREEN command:

```text
python -m pytest tests/test_lineage_sql.py -q
```

Result: initial GREEN, exit code 0: `14 passed, 1 skipped in 0.17s`. Final pre-commit verification, exit code 0: `14 passed, 1 skipped in 0.15s`.

### Coverage Boundary

The current run did not set the external factory reference, so the opt-in engine test was skipped and provides no live SQL proof yet. Task 5 remains responsible for supplying the factory reference and executing this stricter check against a dedicated SQL Server/Azure SQL database.
