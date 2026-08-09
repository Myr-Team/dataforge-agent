from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.finops import migrate


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "sql"
    / "finops_schema.sql"
)


def test_provider_and_entra_schema_is_additive_tenant_scoped_and_revisioned() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    lowered = schema.lower()

    for table in (
        "model_provider",
        "model_provider_model",
        "provider_route_revision",
        "entra_group_mapping",
    ):
        assert f"if object_id(n'df_finops.{table}', n'u') is null" in lowered
        assert f"create table df_finops.{table}" in lowered

    assert "primary key (tenant_ref, provider_id)" in lowered
    assert "primary key (tenant_ref, provider_id, model_id)" in lowered
    assert "primary key (tenant_ref, revision_id)" in lowered
    assert "primary key (tenant_ref, mapping_id)" in lowered
    assert "base_revision" not in lowered
    assert "drop table" not in lowered
    assert "truncate table" not in lowered


def test_finops_schema_allows_bedrock_and_adds_region() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    assert "region NVARCHAR(32) NULL" in sql
    assert "N'aws_bedrock'" in sql
    assert "DROP CONSTRAINT CK_finops_model_provider_type" in sql
    assert "definition" in sql
    assert "IF NOT EXISTS" in sql
    assert "connection_stage NVARCHAR(64) NULL" in sql
    assert "stage_durations_json NVARCHAR(MAX)" in sql


@pytest.mark.parametrize(
    ("table", "upgrade"),
    (
        ("request_event", "ALTER TABLE df_finops.request_event"),
        ("department", "ALTER TABLE df_finops.department"),
        ("model_provider", "ALTER TABLE df_finops.model_provider"),
        ("budget_alert", "ALTER TABLE df_finops.budget_alert"),
    ),
)
def test_conditional_table_creation_precedes_upgrade_in_an_earlier_batch(
    table: str,
    upgrade: str,
) -> None:
    batches = re.split(
        r"(?im)^\s*GO\s*$",
        SCHEMA_PATH.read_text(encoding="utf-8"),
    )
    create_batch = next(
        index
        for index, batch in enumerate(batches)
        if f"CREATE TABLE df_finops.{table}" in batch
    )
    upgrade_batch = next(
        index
        for index, batch in enumerate(batches)
        if upgrade in batch
    )

    assert create_batch < upgrade_batch


@pytest.mark.parametrize(
    "table",
    (
        "request_event",
        "department",
        "model_provider",
        "notification_setting",
        "budget_alert",
    ),
)
def test_schema_upgrades_defer_alter_table_compilation(table: str) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    assert f"EXEC(N'ALTER TABLE df_finops.{table}" in schema


def test_member_budget_schema_is_additive_and_uses_no_destructive_rewrite() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    for table in ("member_budget", "notification_setting", "budget_alert"):
        assert f"if object_id(n'df_finops.{table}', n'u') is null" in schema
        assert f"create table df_finops.{table}" in schema

    assert "drop table df_finops.member_budget" not in schema
    assert "truncate table df_finops.member_budget" not in schema
    assert "ck_finops_budget_alert_period" in schema


def test_member_budget_period_check_has_a_guarded_existing_table_upgrade() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()
    table_end = schema.index("end;", schema.index("create table df_finops.budget_alert"))
    upgrade = schema[table_end + len("end;") :]

    assert "from sys.check_constraints" in upgrade
    assert "parent_object_id = object_id(n'df_finops.budget_alert')" in upgrade
    assert "alter table df_finops.budget_alert" in upgrade
    assert "add constraint ck_finops_budget_alert_period" in upgrade


def test_job_run_status_schema_is_additive() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    assert "if object_id(n'df_finops.job_run_status', n'u') is null" in schema
    assert "create table df_finops.job_run_status" in schema
    assert "drop table df_finops.job_run_status" not in schema


def test_finops_schema_contains_remediation_tables() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    lowered = sql.lower()

    for table in ("remediation_draft", "remediation_transition"):
        assert f"if object_id(n'df_finops.{table}', n'u') is null" in lowered
        assert f"create table df_finops.{table}" in lowered

    assert "CK_finops_remediation_scope_json" in sql
    assert "CK_finops_remediation_status" in sql
    assert "DROP TABLE df_finops.remediation_draft" not in sql
    assert "TRUNCATE TABLE df_finops.remediation_draft" not in sql


def test_finops_schema_contains_additive_risk_scan_history() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    lowered = sql.lower()

    for table in ("risk_scan", "risk_scan_finding"):
        assert f"if object_id(n'df_finops.{table}', n'u') is null" in lowered
        assert f"create table df_finops.{table}" in lowered

    assert "CK_finops_risk_scan_status" in sql
    assert "CK_finops_risk_scan_finding_status" in sql
    assert "DROP TABLE df_finops.risk_scan" not in sql
    assert "TRUNCATE TABLE df_finops.risk_scan" not in sql


def test_request_event_routing_policy_revision_is_additive_and_nullable() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    lowered = schema.lower()

    assert "routing_policy_revision int null" in lowered
    assert (
        "col_length(n'df_finops.request_event', "
        "n'routing_policy_revision') is null"
    ) in lowered
    assert "alter table df_finops.request_event" in lowered
    assert "add routing_policy_revision int null" in lowered


def test_provider_schema_constrains_public_states_roles_and_json() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    for state in (
        "testing",
        "connected",
        "degraded",
        "invalid",
        "disabled",
        "pending",
        "governed",
        "unmanaged",
    ):
        assert f"n'{state}'" in schema

    for role in ("admin", "editor", "viewer"):
        assert f"n'{role}'" in schema

    assert "n'owner'" not in _mapping_check(schema)
    assert "isjson(available_models_json) = 1" in schema
    assert "isjson(route_payload) = 1" in schema
    assert "isjson(workspace_scope_json) = 1" in schema


def _mapping_check(schema: str) -> str:
    start = schema.index("ck_finops_entra_mapping_role")
    return schema[start : start + 240]


class _DeniedCursor:
    def execute(self, operation: str, *parameters: object) -> "_DeniedCursor":
        raise RuntimeError(
            "permission denied for tenant-secret and Authorization bearer-marker"
        )


class _DeniedConnection:
    autocommit = False

    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _DeniedCursor:
        return _DeniedCursor()

    def commit(self) -> None:
        raise AssertionError("denied migration must not commit")

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_migration_failure_is_safe_and_fails_closed() -> None:
    connection = _DeniedConnection()

    with pytest.raises(migrate.FinOpsMigrationError) as captured:
        migrate.run_migration(lambda: connection)

    assert captured.value.code == "finops_schema_migration_failed"
    assert str(captured.value) == "finops_schema_migration_failed"
    assert "tenant-secret" not in repr(captured.value)
    assert "bearer-marker" not in repr(captured.value)
    assert connection.rolled_back is True
    assert connection.closed is True
