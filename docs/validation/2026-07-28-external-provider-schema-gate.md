# External Provider Schema Gate Evidence

**Date:** 2026-07-28

**Resource group:** `rg-dataforge-dev`

**Database:** `df_lineage`

**Source commit containing the migration:** `54f8289`

## Outcome

- The controlled Azure CLI service-principal identity matched the configured
  Azure SQL Microsoft Entra administrator.
- The initial connection failed with Azure SQL native error `40615`.
- The public address returned by a generic IP discovery service differed from
  the client egress address observed by Azure SQL.
- A temporary firewall rule was created for only the Azure SQL-observed IPv4.
- `backend/sql/finops_schema.sql` executed twice successfully.
- Both executions were additive and idempotent.
- The administrator session reported schema `ALTER` and database
  `CREATE TABLE` permission.
- The temporary firewall rule was deleted in the same operation.
- An independent firewall-rule list confirmed zero rules matching
  `df-codex-*`.

## Verified tables

- `df_finops.model_provider`
- `df_finops.model_provider_model`
- `df_finops.provider_route_revision`
- `df_finops.entra_group_mapping`

## Local contract verification

```text
python -m pytest -q tests/test_finops_sql_migration.py tests/test_finops_sql.py
7 passed

git diff --check
clean
```

## Operational note

Do not use a generic public-IP discovery result for this workstation's future
Azure SQL rules. The release operator must take the rejected client IPv4 from
Azure SQL error `40615`, validate it as a single public IPv4, create an exact
temporary rule, and remove that rule after migration verification.
