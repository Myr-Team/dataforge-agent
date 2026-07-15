# SQL Lineage Migration Task 2 Report

## Status

Task 2 is implemented and verified. The production connection boundary is managed-identity-only, lazy at app startup, and fail-closed for every configuration, token, driver, and connection failure covered by the brief. No Task 3 promotion or attachment call sites were changed.

## Changed Files

- `backend/lineage_sql.py`
  - Added strict validation for `LINEAGE_SQL_SERVER` and `LINEAGE_SQL_DATABASE` only.
  - Added a lazy `DefaultAzureCredential` connection factory for `https://database.windows.net/.default`.
  - Added the ODBC `ACCESSTOKEN` layout: 4-byte little-endian byte length followed by UTF-16LE token bytes.
  - Added ODBC Driver 18 options with encryption, certificate verification, and a 5-second connection timeout.
  - Redacts all boundary failures to `lineage database is unavailable` without exception chaining.
- `backend/app.py`
  - Registered one lazy `LineageRepository` dependency without opening SQL or validating SQL configuration during startup.
  - Did not change Easy Auth, public authentication, routes, or lineage write call sites.
- `tests/test_lineage_sql_config.py`
  - Added focused tests for invalid/missing configuration, lazy startup, default/injected credentials, token packing, parameterized ODBC options, timeout, and redacted token/driver/connect failures.
- `backend/Dockerfile`
  - Added the Microsoft Debian package repository and EULA-accepted `msodbcsql18` installation.
  - Added the Debian slim Kerberos runtime library recommended by Microsoft and removed temporary `curl`/repository setup packages.
- `.superpowers/sdd/sql-lineage-task-2-report.md`
  - Added this implementation and verification record.

## TDD Evidence

### RED

Command:

```text
python -m pytest tests/test_lineage_sql_config.py -q
```

Result before production changes:

```text
16 failed in 6.66s
```

All failures were expected missing-boundary failures: `backend.lineage_sql` lacked `build_lineage_sql_connection_factory` and `_pack_access_token`, and `backend.app` lacked `get_lineage_repository`.

### GREEN

Initial minimal implementation result:

```text
16 passed in 6.19s
```

After expanding the required ODBC options into parameterized cases and strengthening opaque token-layout verification, the first complete GREEN result was:

```text
20 passed in 5.04s
```

## Verification

```text
python -m pytest tests/test_lineage_sql_config.py -q
20 passed in 6.71s

python -m pytest tests/test_lineage_sql.py -q
14 passed, 1 skipped in 0.16s

python -m backend.import_smoke
exit 0

python -m compileall -q backend/lineage_sql.py backend/app.py
exit 0

git diff --check
exit 0
```

The skipped Task 1 test is the existing opt-in real Azure SQL integration test because `LINEAGE_SQL_TEST_CONNECTION_FACTORY` is not configured in this environment.

## Self-Review

- The connection string contains no `UID`, `PWD`, `Authentication`, `Trusted_Connection`, password, or token value.
- Only the two approved production environment variables are read; no password-bearing environment variable or full connection string is accepted.
- Missing configuration is rejected before credential construction or connector invocation.
- Credential, missing-driver, and timeout exceptions all become the same generic `LineageUnavailable` message with no cause or context retained.
- No logging was added. Tokens, SQL credentials, rowversions, raw claims, internal paths, and original connector diagnostics are not returned.
- The factory is created during app import but performs no environment validation, credential construction, token request, driver import, or network connection until a repository operation requests a connection.
- Docker installation follows Microsoft's current Debian `packages-microsoft-prod.deb` flow, accepts the Driver 18 EULA, avoids optional SQL command-line tools/development headers, includes the Debian slim Kerberos runtime dependency, purges setup-only packages, and clears apt lists.

## Commit

Commit message: `feat: add managed identity lineage sql boundary`

This report is included in that same commit; the final commit hash is returned after the commit is created.

## Concerns

- Docker is not installed on this machine, so the image could not be built locally. The Dockerfile was reviewed against current Microsoft Debian Driver 18 installation guidance and passed repository diff/syntax inspection only.
- The real managed-identity Azure SQL path was not exercised because no provisioned test database/factory was configured. The existing Task 1 integration test remains opt-in and was skipped; Task 5 owns Azure provisioning and deployment smoke.
