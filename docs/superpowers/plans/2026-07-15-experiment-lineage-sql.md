# Experiment Lineage SQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move authoritative experiment lineage and attachments from multi-Blob coordination to transactional Azure SQL storage.

**Architecture:** A focused SQL repository owns workspace generations, canonical ordinals, and attachment membership. Existing Blob run documents remain display payloads only. `run_store` delegates promotion, purge, recreation, and attachment decisions to the repository, and fails closed when SQL is unavailable.

**Tech Stack:** FastAPI, Python, `pyodbc` with Microsoft ODBC Driver 18, Azure managed identity token authentication, Azure SQL Database, pytest.

## Global Constraints

- Do not change Easy Auth or public authentication behavior.
- Do not store, log, or return SQL credentials, access tokens, rowversions, or raw identity claims.
- Evidence and verdict strength remain evidence-driven; synthetic or unverified outcomes cannot strengthen a version.
- Blob data is non-authoritative for experiment membership and ordinals.
- Azure Container Apps Linux deployment uses managed identity and parameterized SQL only.

---

### Task 1: SQL Repository and Schema

**Files:**
- Create: `backend/lineage_sql.py`
- Create: `backend/sql/lineage_schema.sql`
- Create: `tests/test_lineage_sql.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: `LineageRepository`, `LineageUnavailable`, `VersionCommit`, `AttachmentCommit`.
- Consumes: workspace ID, generation, normalized decision/evidence fingerprints, actor-safe metadata.

- [ ] Write failing tests for one canonical ordinal under parallel commits, terminal purge, and attachment foreign-key validation.
- [ ] Run `python -m pytest tests/test_lineage_sql.py -q` and confirm RED.
- [ ] Implement idempotent schema creation and parameterized repository transactions using `UPDLOCK, HOLDLOCK`.
- [ ] Re-run the test file and confirm GREEN.
- [ ] Commit `feat: add transactional lineage sql repository`.

### Task 2: Managed Identity Configuration and Fail-Closed Boundary

**Files:**
- Modify: `backend/lineage_sql.py`
- Modify: `backend/app.py`
- Create: `tests/test_lineage_sql_config.py`
- Modify: `backend/Dockerfile`

**Interfaces:**
- Produces: a `pyodbc` connection factory using `DefaultAzureCredential` access tokens for `https://database.windows.net/.default` and ODBC Driver 18's `SQL_COPT_SS_ACCESS_TOKEN` attribute.
- Consumes: `LINEAGE_SQL_SERVER`, `LINEAGE_SQL_DATABASE`, optional explicit local test connection.

- [ ] Write failing tests proving missing configuration and token/connection failure yield `LineageUnavailable` with no leaked details.
- [ ] Run `python -m pytest tests/test_lineage_sql_config.py -q` and confirm RED.
- [ ] Implement configuration validation, token acquisition, connection timeout, and redacted diagnostics.
- [ ] Re-run configuration tests and confirm GREEN.
- [ ] Commit `feat: add managed identity lineage sql boundary`.

### Task 3: Replace Experiment Promotion and Attachments

**Files:**
- Modify: `backend/experiment_store.py`
- Modify: `backend/run_store.py`
- Modify: `backend/orchestrator.py`
- Modify: `tests/test_experiment_versions.py`
- Modify: `tests/test_artifact_version_snapshot.py`
- Modify: `tests/test_followup_plan_version.py`

**Interfaces:**
- Consumes: `LineageRepository.commit_analysis`, `attach_snapshot`, `purge_workspace`, `recreate_workspace`, `list_versions`.
- Produces: SQL-backed canonical version IDs and bounded unavailable states.

- [ ] Write failing regressions for concurrent analysis, old-generation writer rejection after recreate, and Blob publication failure after a committed SQL version.
- [ ] Run the three Task 3 test files and confirm RED.
- [ ] Route all authoritative version, attachment, purge, and recreation decisions through the repository; retain Blob only as optional payload hydration.
- [ ] Re-run the three test files plus SQL repository tests and confirm GREEN.
- [ ] Commit `refactor: make sql authoritative for experiment lineage`.

### Task 4: Legacy Read Migration and Public Contracts

**Files:**
- Modify: `backend/control_plane.py`
- Modify: `backend/experiment_store.py`
- Create: `backend/migrate_lineage_sql.py`
- Modify: `tests/test_experiment_versions.py`
- Modify: `tests/test_control_plane_runs.py`

**Interfaces:**
- Produces: legacy read-only/unavailable state and an explicit migration command.
- Consumes: complete Blob histories only.

- [ ] Write failing tests for incomplete legacy history, Blob payload mismatch, and stable SQL ordinal display.
- [ ] Run relevant migration/control-plane tests and confirm RED.
- [ ] Implement complete-history-only import and SQL-led public ledger projection.
- [ ] Re-run tests and confirm GREEN.
- [ ] Commit `feat: migrate experiment lineage reads to sql`.

### Task 5: Azure Provisioning, Verification, and Release Gate

**Files:**
- Create: `infra/lineage-sql.md`
- Modify: `scripts/verify_lineage_sql.py`
- Modify: `.github/workflows/ci.yml` if present

- [ ] Document Entra SQL administrator, contained user for backend managed identity, least-privilege grants, schema deployment, and rollback.
- [ ] Add a non-secret deployment verifier for managed identity connection, schema version, transaction behavior, and fail-closed response.
- [ ] Run full backend tests, Vite build, and SQL verifier against a disposable/local fixture.
- [ ] Deploy preview only after Azure prerequisites are observed; run a real analysis, artifact attachment, purge/recreate, and public ledger smoke.
- [ ] Commit `docs: add lineage sql deployment verification`.
