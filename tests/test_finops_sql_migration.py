from __future__ import annotations

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
