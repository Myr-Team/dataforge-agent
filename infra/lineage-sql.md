# Lineage SQL Preview Release Gate

This runbook provisions no Azure resources by itself. It defines the prerequisites and evidence required before the SQL lineage path can move beyond preview. Production rollout is prohibited until every preview gate below has observed evidence tied to one immutable backend revision.

## Security Boundary

The backend and `scripts/verify_lineage_sql.py` accept only these runtime identifiers:

- `LINEAGE_SQL_SERVER`: an Azure SQL `*.database.windows.net` FQDN.
- `LINEAGE_SQL_DATABASE`: the database name.
- `--ephemeral-workspace`: an explicit UUID that authorizes the verifier's destructive probe.

Do not provide a connection string, SQL login, password, client secret, or access token. The existing `build_lineage_sql_connection_factory` obtains an Azure SQL access token through the deployed Container App's system-assigned managed identity and passes it directly to ODBC Driver 18. The verifier never receives or prints that token. Its output contains only bounded status categories.

## Azure Preconditions

An Azure operator must observe all of the following before schema deployment:

1. The preview backend Container App has a system-assigned identity. Record its immutable Azure resource ID, principal/object ID, revision name, and image digest as release evidence. Do not use a developer identity or a user-assigned identity as a substitute.
2. The Container App identity and the Azure SQL logical server are in the same Microsoft Entra tenant.
3. The logical server has a Microsoft Entra administrator. The initial contained Microsoft Entra database user must be created by the Entra administrator, or by another Entra database principal already granted `ALTER ANY USER`. A SQL-authenticated server administrator cannot create the initial Entra user. See [Microsoft Entra service principals with Azure SQL](https://learn.microsoft.com/en-us/azure/azure-sql/database/authentication-aad-service-principal-tutorial?view=azuresql).
4. The operator can connect to the target database as that Entra administrator and can deploy DDL. This is separate from the backend runtime identity.
5. The preview Container App has an allowed network path to `<server>.database.windows.net:1433`, including firewall/private endpoint, DNS, and egress policy.
6. The immutable backend image contains and registers Microsoft ODBC Driver 18. `backend/Dockerfile` installs `msodbcsql18` and fails its image build unless `pyodbc.drivers()` contains `ODBC Driver 18 for SQL Server`.

Setting the SQL Entra administrator, reading or changing the Container App system identity, assigning SQL server identities, and executing commands in a deployed Container App can require Azure RBAC permissions that application developers do not have. A Microsoft Entra Privileged Role Administrator is also required if the organization chooses to grant the SQL logical server identity Microsoft Graph permissions or Directory Readers.

### Directory lookup choice

Use the contained-user `SID` method below. Microsoft documents this method for creating an Entra user without external-provider validation, so the SQL logical server identity does not need Directory Readers merely to create the backend user. See [managed identities in Microsoft Entra for Azure SQL](https://learn.microsoft.com/en-us/azure/azure-sql/database/authentication-azure-ad-user-assigned-managed-identity?view=azuresql) and [`CREATE USER`](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-user-transact-sql?view=sql-server-ver17).

If an administrator instead uses `CREATE USER ... FROM EXTERNAL PROVIDER`, the SQL logical server identity must be able to query Microsoft Graph when a service principal performs the operation. Microsoft lists `User.Read.All`, `GroupMember.Read.All`, and `Application.Read.All` as the lower-level permissions; Directory Readers is broader. Do not grant either set to the backend Container App identity.

## Schema Deployment

Connect to the target database as the SQL logical server's Microsoft Entra administrator. Deploy [backend/sql/lineage_schema.sql](../backend/sql/lineage_schema.sql) exactly once per database; the DDL is idempotent. Do not grant DDL permissions to the backend identity.

After the DDL succeeds, set the schema-scoped release marker in the same administrator session:

```sql
IF EXISTS (
    SELECT 1
    FROM sys.extended_properties
    WHERE class = 3
      AND major_id = SCHEMA_ID(N'df_lineage')
      AND minor_id = 0
      AND name = N'DataForgeLineageSchemaVersion'
)
BEGIN
    EXEC sys.sp_updateextendedproperty
        @name = N'DataForgeLineageSchemaVersion',
        @value = N'2026-07-15.v1',
        @level0type = N'SCHEMA',
        @level0name = N'df_lineage';
END;
ELSE
BEGIN
    EXEC sys.sp_addextendedproperty
        @name = N'DataForgeLineageSchemaVersion',
        @value = N'2026-07-15.v1',
        @level0type = N'SCHEMA',
        @level0name = N'df_lineage';
END;
```

The release verifier requires this marker, all four tables, every named primary/unique/foreign/check constraint, and `IX_experiment_version_latest`. Missing metadata fails the release gate.

## Backend Contained User

Use the preview Container App's system-assigned identity **application (client) ID**, not its principal/object ID. Azure SQL maps a managed identity or service principal `TYPE = E` user from that application ID. Run this in the target database as the Entra administrator. Replace only the UUID literal; keep the database alias stable.

```sql
DECLARE @client_id UNIQUEIDENTIFIER = '00000000-0000-0000-0000-000000000000';
DECLARE @sid VARCHAR(34) = CONVERT(
    VARCHAR(34), CONVERT(VARBINARY(16), @client_id),
    1
);

IF DATABASE_PRINCIPAL_ID(N'dataforge-backend-ca') IS NULL
BEGIN
    EXEC (
        N'CREATE USER [dataforge-backend-ca] WITH SID = '
        + @sid
        + N', TYPE = E'
    );
END;

IF DATABASE_PRINCIPAL_ID(N'df_lineage_runtime') IS NULL
BEGIN
    CREATE ROLE [df_lineage_runtime] AUTHORIZATION dbo;
END;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON SCHEMA::df_lineage TO [df_lineage_runtime];
GRANT VIEW DEFINITION
    ON SCHEMA::df_lineage TO [df_lineage_runtime];
ALTER ROLE [df_lineage_runtime] ADD MEMBER [dataforge-backend-ca];
```

Do not add this user to `db_owner`, `db_ddladmin`, `db_securityadmin`, `db_datareader`, or `db_datawriter`. The custom role limits DML and metadata visibility to `df_lineage`. Confirm the mapping and role membership:

```sql
SELECT name, type_desc
FROM sys.database_principals
WHERE name IN (N'dataforge-backend-ca', N'df_lineage_runtime');

SELECT role_principal.name AS role_name, member_principal.name AS member_name
FROM sys.database_role_members AS membership
JOIN sys.database_principals AS role_principal
    ON role_principal.principal_id = membership.role_principal_id
JOIN sys.database_principals AS member_principal
    ON member_principal.principal_id = membership.member_principal_id
WHERE role_principal.name = N'df_lineage_runtime';
```

## Verifier Modes

Local CI and developer machines must use the safe mode:

```powershell
python scripts/verify_lineage_sql.py --check-prerequisites
```

`--dry-run` is an exact safe-mode alias. Safe mode contacts no Azure endpoint and writes no SQL. It verifies the local schema contract and intentional missing-configuration fail-closed behavior. It reports ODBC Driver 18 registration as an observation; a local `false` value is not Azure or image evidence.

The full release gate must execute in an approved environment running as the exact preview backend Container App system-assigned identity:

```powershell
$workspace = [guid]::NewGuid().ToString()
python scripts/verify_lineage_sql.py `
  --server <server>.database.windows.net `
  --database <database> `
  --ephemeral-workspace $workspace
```

The full mode exits nonzero unless it can:

- connect through the existing system-assigned managed-identity factory and ODBC Driver 18;
- observe the exact schema objects, constraints, index, and `2026-07-15.v1` marker;
- prove absent server/database configuration fails closed;
- allocate ordinal 1, deduplicate an identical result, force and roll back a failed ordinal advance, then allocate ordinal 2;
- purge generation 1, recreate generation 2, and restart at ordinal 1; and
- delete attachments, versions, generation events, and the workspace row for only the UUID it atomically claimed.

The verifier refuses an existing workspace UUID and does not clean it. Omitting the UUID performs no destructive checks and exits nonzero because the full release gate is incomplete.

The backend image includes `scripts/verify_lineage_sql.py` and validates its safe mode while building. The full verifier must still run through an exec session in the exact immutable preview revision; a different Container Apps Job system identity is not equivalent evidence.

## Preview-Only Release Flow

1. Commit and build immutable backend and web images from the same source SHA. Record image digests.
2. Deploy the schema and version marker with the Entra administrator. Create the contained backend user and custom role.
3. Build the backend image and retain the Driver 18 registration assertion output.
4. Deploy only a preview backend revision with system-assigned identity enabled and only `LINEAGE_SQL_SERVER` and `LINEAGE_SQL_DATABASE` identifiers configured. Keep production traffic and production configuration unchanged.
5. Run the full verifier under that exact preview revision identity. Record timestamp, source SHA, image digest, revision name, server/database identifiers, ephemeral UUID, bounded JSON output, and exit code. Do not record identity tokens or command environment dumps.
6. Run the application smoke below through authenticated preview UI/API flows. Keep every workspace disposable and preview-only.
7. Review logs and traces for bounded `configuration`, `token`, `driver`, or `connection` categories only. Raw identity claims, token values, connection data, and database error text must be absent.
8. Do not shift any production traffic or configure production SQL until all evidence passes and is reviewed.

## Required Preview Smoke

Use one new disposable uploaded workspace and capture public API responses, SQL verifier output, run IDs, version IDs, artifact IDs, revision name, image digest, and timestamps.

1. **Real analysis:** run a normal preview analysis. It must finish with trusted SQL lineage, not `lineage_unavailable` or a synthetic/legacy version.
2. **Public ledger:** call `GET /api/workspaces/{workspace_id}/experiments`. The response must identify `source=sql_lineage`, preserve SQL ordinal 1, and expose no SQL metadata or credentials.
3. **Artifact attachment:** generate one normal PDF or other supported artifact from that analysis. The public ledger must hydrate it only from a SQL attachment row whose version ID, kind, source run ID, and payload SHA-256 match. Download the artifact through its authenticated public endpoint.
4. **Purge/recreate:** after saving the evidence, run the approved preview operator path for `backend.run_store.purge_workspace_runs` and `recreate_workspace_generation` on that disposable workspace. Purge must be SQL-confirmed before payload cleanup; recreation must advance the generation. A new real analysis must appear at ordinal 1 in the new generation, and the old generation must not accept attachments.
5. **Fail closed:** intentionally remove both lineage SQL identifiers from a separate no-traffic preview revision. Analysis promotion and attachment writes must return bounded unavailable behavior while legacy Blob reads remain read-only. Restore the previous preview configuration after evidence capture.

The repository currently has no public purge/recreate endpoint. Use only an approved operator execution path against a disposable preview workspace; do not expose a temporary unauthenticated route. This is also why the verifier independently covers the transactional purge/recreate path.

## Rollback

Application rollback is revision-first:

1. Route preview traffic back to the prior immutable backend revision.
2. Remove the no-traffic failed preview revision after evidence is retained.
3. Do not point the prior revision at a partially deployed lineage schema. With SQL identifiers absent, lineage promotion remains fail closed and complete legacy Blob data remains read-only.

Database rollback preserves evidence by default:

1. Remove the backend user from `df_lineage_runtime`, or revoke that role's schema DML grants, to stop new SQL writes.
2. Retain the `df_lineage` tables and marker for investigation. Do not renumber, rewrite, or migrate committed ordinals back into Blob.
3. Drop the contained user or role only after access is no longer required.
4. Drop the schema objects only for an abandoned preview database after proving there are no non-verifier rows and taking the required backup. Delete in dependency order: `experiment_attachment`, `experiment_version`, `workspace_generation_event`, `workspace_lineage`, then the schema marker and schema.

No production rollout is permitted until the managed-identity verifier, preview real-analysis/attachment/public-ledger smoke, purge/recreate smoke, fail-closed preview revision, redaction review, and rollback rehearsal all pass with observed evidence.
