# SQL Lineage Task 5 Report

## Status

Task 5 implementation is complete within the requested file allowlist. The local release-gate behavior, documentation, focused lineage suites, and Vite build have executable evidence.

Final commit message: `docs: add lineage sql deployment verification`.

The Azure preview release gate is **not passed**. No Azure resource, SQL administrator, contained database user, network path, Container App revision, managed-identity connection, schema marker, real preview analysis, attachment, purge/recreate, or public ledger was observed or changed in this task. Production rollout remains prohibited.

## Scope

Changed only:

- `infra/lineage-sql.md`
- `scripts/verify_lineage_sql.py`
- `.superpowers/sdd/sql-lineage-task-5-report.md`

`.github/workflows/ci.yml` does not exist, so no workflow was created or modified. Untracked `output/` was present before Task 5 and was left untouched.

No Azure resource or deployment command was run. No auth, Easy Auth, backend runtime, schema DDL source, Dockerfile, frontend source, credentials, or production configuration was changed.

## Verifier Contract

`scripts/verify_lineage_sql.py` has two safe local aliases:

```text
python scripts/verify_lineage_sql.py --check-prerequisites
python scripts/verify_lineage_sql.py --dry-run
```

Both contact no Azure endpoint and write no SQL. They verify the checked-in schema contract and prove that intentionally absent server/database configuration reaches the existing repository's bounded `configuration` fail-closed outcome. ODBC Driver 18 registration is reported as an observation but does not make safe mode fail on a non-Azure CI host.

Full verify mode accepts only server/database identifiers and an explicit UUID:

```text
python scripts/verify_lineage_sql.py \
  --server <server>.database.windows.net \
  --database <database> \
  --ephemeral-workspace <uuid>
```

It uses the existing `build_lineage_sql_connection_factory` and `LineageRepository`. It has no arguments for connection strings, SQL logins, passwords, client secrets, user-assigned identity IDs, or access tokens. Runtime logging is suppressed during verification so Azure SDK diagnostics cannot bypass the bounded JSON output. The caller's prior logging state is restored afterward.

The live release gate verifies:

- system-assigned managed-identity connection and registered ODBC Driver 18 through the existing factory;
- all four `df_lineage` tables;
- every named primary, unique, foreign-key, and check constraint;
- `IX_experiment_version_latest`;
- schema-scoped marker `DataForgeLineageSchemaVersion=2026-07-15.v1`;
- intentional missing-configuration fail-closed behavior;
- ordinal 1 allocation and duplicate-result deduplication;
- rollback of a forced failure between version insertion and ordinal advancement, followed by ordinal 2 rather than 3;
- purge of generation 1, recreation to generation 2, and ordinal reset to 1.

The destructive path is disabled unless `--ephemeral-workspace` is a valid UUID. The verifier locks and atomically refuses any pre-existing workspace row. Cleanup runs only after it successfully inserts its own marker row, binds every delete by that exact UUID, and deletes attachments, versions, generation events, then the workspace row.

## Runbook

`infra/lineage-sql.md` documents:

- exact SQL logical-server Microsoft Entra administrator prerequisites;
- system-assigned Container App identity and same-tenant requirements;
- Azure RBAC and Microsoft Entra privilege caveats;
- a contained Entra user created from the observed principal/object ID using `SID` and `TYPE=E`, avoiding a Directory Readers grant solely for user creation;
- the alternate external-provider lookup requirements and lower-level Microsoft Graph permissions;
- an explicit custom `df_lineage_runtime` role with schema-scoped DML and metadata visibility only;
- idempotent schema deployment by an Entra administrator, separate from runtime identity permissions;
- schema marker deployment;
- Driver 18 image-build evidence;
- preview-only immutable revision flow;
- real analysis, SQL public ledger, artifact attachment, purge/recreate, fail-closed, and redaction smoke requirements;
- revision-first rollback and evidence-preserving database rollback;
- a hard prohibition on production rollout before observed preview evidence passes.

## TDD Evidence

The explicit Task 5 allowlist did not permit a committed `tests/` file. A temporary pytest harness was therefore created under existing `tmp/`, used for RED/GREEN evidence, and removed before the final diff.

Initial RED, before the verifier existed:

```text
python -m pytest tmp/test_verify_lineage_sql_task5.py -q
8 failed
```

All eight tests failed on the expected assertion: `Task 5 verifier is missing`.

Initial GREEN covered CLI secret exclusion, no-Azure prerequisite mode, UUID enforcement, schema marker/object contract, ordinal rollback assertions, and redacted missing-runtime failure:

```text
python -m pytest tmp/test_verify_lineage_sql_task5.py -q
8 passed
```

The first live negative check then exposed Azure Identity warning text on stderr. A new regression was added first:

```text
python -m pytest tmp/test_verify_lineage_sql_task5.py::test_runtime_logging_is_suppressed_and_restored -q
1 failed
```

It failed because `suppress_runtime_logging` did not exist. After the bounded logging context was implemented:

```text
python -m pytest tmp/test_verify_lineage_sql_task5.py -q
9 passed
```

The temporary test file was then deleted to preserve the user-owned allowlist.

After resuming the interrupted Task 5 session, a second temporary regression proved that the rollback probe records reaching its injected ordinal-advance failure before accepting the rollback result:

```text
python -m pytest tmp/test_verify_lineage_sql_rollback_marker.py -q
1 passed
```

That temporary file was also deleted before the final scope audit.

## Verification Evidence

Focused verifier plus existing SQL lineage/configuration suites, before temporary-test removal:

```text
python -m pytest tmp/test_verify_lineage_sql_task5.py tests/test_lineage_sql.py tests/test_lineage_sql_config.py -q
53 passed, 1 skipped
```

The skip is the existing opt-in real SQL integration test requiring an already-provisioned connection factory.

Safe local gate:

```json
{"azure_checked": false, "fail_closed": "verified", "mode": "check-prerequisites", "odbc_driver_registered": false, "schema_source": "verified", "schema_version": "2026-07-15.v1", "status": "ok"}
```

`--dry-run` returned the same safe result. `python -m py_compile scripts/verify_lineage_sql.py` and `git diff --check` exited zero.

Local full-mode negative check used syntactically valid non-secret identifiers and a fresh UUID. This host has no Azure managed identity, so the expected bounded failure was:

```json
{"failure_category": "token", "mode": "verify", "reason": "lineage_unavailable", "status": "failed"}
```

It exited nonzero and emitted no SDK diagnostics after the logging regression fix. This is fail-closed evidence only, not Azure connection evidence.

Vite production build:

```text
npm run build
vite v8.0.16
1756 modules transformed
built in 1.72s
```

The first complete backend run included the nine temporary verifier tests:

```text
python -m pytest -q
875 passed, 8 failed, 1 skipped, 1 warning
```

The eight failures match the pre-Task-5 baseline already recorded by Task 4:

- four `tests/test_actor_audit_usage.py` generic-run persistence failures;
- two `tests/test_capability_pack_integration.py` generic-run persistence failures;
- `tests/test_backend_image_import_smoke.py` expecting a standalone Dockerfile `RUN python -m backend.import_smoke` line;
- the full-suite-only shared-state failure in `tests/test_lineage_sql_config.py::test_app_lineage_repository_registration_is_lazy_without_configuration`.

None has a Task 5 file in its stack. The final post-removal run against only permanent repository files confirmed the baseline:

```text
python -m pytest -q
866 passed, 8 failed, 1 skipped, 1 warning
```

Final resumed verification against permanent Task 5 files:

```text
python -m pytest tests/test_lineage_sql.py tests/test_lineage_sql_config.py -q
45 passed, 1 skipped

python scripts/verify_lineage_sql.py --check-prerequisites
python scripts/verify_lineage_sql.py --dry-run
```

Both verifier commands exited zero with `azure_checked=false`, `fail_closed=verified`, and `schema_source=verified`. They observed `odbc_driver_registered=false` on this Windows host. `python -m py_compile scripts/verify_lineage_sql.py` and `git diff --check` also exited zero.

## Blockers

1. No live Azure or preview evidence was requested or available. Managed-identity connection, SQL schema/marker, Entra admin, contained user, permissions, network access, and application smoke remain unobserved.
2. This Windows host does not register ODBC Driver 18. The backend Dockerfile has a build-time registration assertion, but no image build was run in this task.
3. `backend/Dockerfile` copies `backend/` but not `scripts/`. The verifier cannot currently execute inside the immutable backend Container App revision through an exec session. A separately approved packaging change or another mechanism using the exact backend system-assigned identity is required; a different Container Apps Job identity is not equivalent.
4. There is no public purge/recreate endpoint. Preview application smoke needs an approved operator path against a disposable workspace; no temporary unauthenticated route should be introduced.
5. The eight pre-existing full-suite failures remain outside the Task 5 allowlist.

These blockers keep the release preview-only and prevent any truthful claim of live Azure or production success.

## R1 P1 Follow-up: Bounded Argument Errors

Review R1 found that `argparse.ArgumentParser.parse_args()` used its default error path before the verifier's bounded JSON handling. Unknown credential-like arguments therefore echoed raw values to stderr.

The verifier now uses a parser that raises a message-free internal parse failure. `main()` converts every parser error into this fixed nonzero response:

```json
{"mode":"verify","reason":"invalid_arguments","status":"failed"}
```

The parser error path does not render raw argv, option names, or values. Valid `--help` behavior remains argparse-owned and is not an invalid-argument path.

TDD evidence:

```text
python -m pytest tests/test_verify_lineage_sql.py::test_unknown_credential_like_arguments_are_bounded_and_redacted -q
1 failed
```

The expected RED failure occurred because default argparse produced no JSON on stdout and rendered its own error path. The permanent subprocess regression passes a credential-like unknown option and a sentinel, requires a nonzero exit and exactly the bounded JSON result, and asserts that neither the sentinel nor the option name appears in stdout or stderr.

```text
python -m pytest tests/test_verify_lineage_sql.py tests/test_lineage_sql.py tests/test_lineage_sql_config.py -q
46 passed, 1 skipped

python scripts/verify_lineage_sql.py --check-prerequisites
python scripts/verify_lineage_sql.py --dry-run
```

Both safe verifier commands exited zero with the existing no-Azure bounded output. `python -m py_compile scripts/verify_lineage_sql.py` and `git diff --check` also exited zero. No Azure resource or deployment operation was performed.

Follow-up commit message: `fix: bound lineage verifier argument errors`.
