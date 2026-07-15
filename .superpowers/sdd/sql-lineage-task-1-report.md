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
  - Adds unique canonical ordinals, composite attachment foreign keys, lifecycle checks, and rowversion storage.
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
- Redaction: unexpected connection or database errors become the fixed `LineageUnavailable("lineage database operation failed")` message with exception chaining suppressed. The module does not log operation parameters or exceptions. Rowversion is neither selected nor returned. Actor metadata is flat, bounded, and rejects claim-, token-, credential-, cookie-, rowversion-, and secret-shaped keys before opening a connection.
- Foreign keys: attachments have an application-level locked membership check and a database-level composite foreign key to the exact version/workspace/generation tuple.

## Commit

- `feat: add transactional lineage sql repository` (the single commit containing this report; its resulting hash is reported in the task status because a commit cannot contain its own final hash).

## Concerns

- This Task 1 test run uses the required explicitly injected in-memory DB-API test double; it does not execute against a live SQL Server engine.
- Microsoft ODBC Driver 18 system-package installation, managed-identity token construction, connection timeouts, and Azure smoke verification remain intentionally deferred to Tasks 2 and 5.
