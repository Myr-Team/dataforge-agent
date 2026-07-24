from __future__ import annotations

from datetime import datetime, timezone

from backend.finops.evidence import build_evidence_alias
from backend.finops.evidence_repository import InMemoryEvidenceAliasRepository


def test_evidence_alias_uses_controlled_workspace_operation_and_local_time() -> None:
    alias = build_evidence_alias(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        workspace_name="Commerce",
        object_kind="request",
        object_ref="req_aaaaaaaaaaaa",
        operation_code="analysis_run",
        occurred_at=datetime(2026, 7, 24, 2, 42, tzinfo=timezone.utc),
    )

    assert alias.display_name == "Commerce · 分析运行 · 7月24日 10:42"
    assert alias.workspace_name_snapshot == "Commerce"
    assert alias.object_ref == "req_aaaaaaaaaaaa"


def test_evidence_alias_falls_back_without_free_form_model_naming() -> None:
    alias = build_evidence_alias(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        workspace_name="",
        object_kind="request",
        object_ref="req_aaaaaaaaaaaa",
        operation_code="unknown-operation",
        occurred_at=datetime(2026, 7, 24, 2, 42, tzinfo=timezone.utc),
    )

    assert alias.display_name == "工作区 · 操作记录 · 7月24日 10:42"
    assert alias.operation_code == "unknown"


def test_evidence_alias_repository_is_idempotent_and_tenant_scoped() -> None:
    repository = InMemoryEvidenceAliasRepository()
    original = build_evidence_alias(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        workspace_name="Commerce",
        object_kind="request",
        object_ref="req_aaaaaaaaaaaa",
        operation_code="analysis_run",
        occurred_at=datetime(2026, 7, 24, 2, 42, tzinfo=timezone.utc),
    )
    renamed = build_evidence_alias(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        workspace_name="Commerce Renamed",
        object_kind="request",
        object_ref="req_aaaaaaaaaaaa",
        operation_code="analysis_run",
        occurred_at=datetime(2026, 7, 24, 2, 42, tzinfo=timezone.utc),
    )
    other_tenant = renamed.model_copy(update={"tenant_ref": "tenant-b"})

    first = repository.get_or_create(original)
    second = repository.get_or_create(renamed)
    third = repository.get_or_create(other_tenant)

    assert first == second
    assert second.workspace_name_snapshot == "Commerce"
    assert third.workspace_name_snapshot == "Commerce Renamed"
    assert repository.get(
        tenant_ref="tenant-a",
        workspace_id="ws-a",
        object_kind="request",
        object_ref="req_aaaaaaaaaaaa",
    ) == first
    assert repository.get(
        tenant_ref="tenant-b",
        workspace_id="ws-a",
        object_kind="request",
        object_ref="req_aaaaaaaaaaaa",
    ) == third
