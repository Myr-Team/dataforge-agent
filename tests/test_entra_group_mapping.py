from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.entra_group_mapping import (
    EntraGroupMapping,
    GroupMappingConflict,
    InMemoryEntraGroupMappingRepository,
    resolve_group_access,
)


def _mapping(
    mapping_id: str,
    *,
    tenant_ref: str = "tenant-a",
    group_ref: str = "group-a",
    role: str = "viewer",
    priority: int = 100,
    workspace_ids: list[str] | None = None,
    enabled: bool = True,
) -> EntraGroupMapping:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    return EntraGroupMapping(
        mapping_id=mapping_id,
        tenant_ref=tenant_ref,
        group_ref=group_ref,
        display_name=f"Group {mapping_id}",
        role=role,
        workspace_ids=workspace_ids or ["ws-a"],
        priority=priority,
        enabled=enabled,
        revision=1,
        created_by_ref="actor-owner",
        updated_by_ref="actor-owner",
        created_at=now,
        updated_at=now,
    )


def test_group_mapping_is_tenant_scoped_and_revisioned() -> None:
    repository = InMemoryEntraGroupMappingRepository()
    repository.create(_mapping("mapping-a"))
    repository.create(
        _mapping(
            "mapping-b",
            tenant_ref="tenant-b",
            group_ref="group-b",
        )
    )

    assert [item.mapping_id for item in repository.list("tenant-a")] == [
        "mapping-a"
    ]
    updated = repository.update(
        "tenant-a",
        "mapping-a",
        base_revision=1,
        changes={"role": "editor"},
        actor_ref="actor-owner",
    )
    assert updated.revision == 2
    assert updated.role == "editor"

    with pytest.raises(GroupMappingConflict):
        repository.update(
            "tenant-a",
            "mapping-a",
            base_revision=1,
            changes={"role": "admin"},
            actor_ref="actor-owner",
        )


def test_group_resolution_honors_workspace_scope_disabled_and_priority() -> None:
    mappings = [
        _mapping("viewer", role="viewer", priority=100),
        _mapping("editor", role="editor", priority=200),
        _mapping(
            "other-workspace",
            role="admin",
            priority=300,
            workspace_ids=["ws-b"],
        ),
        _mapping("disabled", role="admin", priority=400, enabled=False),
    ]

    result = resolve_group_access(
        mappings,
        group_refs={"group-a"},
        workspace_id="ws-a",
    )

    assert result.role == "editor"
    assert result.mapping_ids == ("editor",)


def test_equal_priority_different_roles_fails_closed() -> None:
    mappings = [
        _mapping("editor", role="editor", priority=200),
        _mapping("admin", role="admin", priority=200),
    ]

    with pytest.raises(GroupMappingConflict):
        resolve_group_access(
            mappings,
            group_refs={"group-a"},
            workspace_id="ws-a",
        )


def test_group_mapping_cannot_grant_owner() -> None:
    with pytest.raises(ValueError):
        _mapping("owner", role="owner")
