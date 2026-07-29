from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .finops.normalization import (
    canonical_tenant_id,
    canonical_tenant_ref,
    opaque_ref,
)
from .lineage_sql import build_lineage_sql_connection_factory


GroupRole = Literal["admin", "editor", "viewer"]


class GroupMappingError(RuntimeError):
    code = "entra_group_mapping_failed"


class GroupMappingConflict(GroupMappingError):
    code = "entra_group_mapping_conflict"


class GroupMappingNotFound(GroupMappingError):
    code = "entra_group_mapping_not_found"


class EntraGroupMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    tenant_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    group_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    display_name: str = Field(min_length=1, max_length=200)
    role: GroupRole
    workspace_ids: list[str] = Field(min_length=1, max_length=128)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True
    revision: int = Field(ge=1)
    created_by_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    updated_by_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    created_at: datetime
    updated_at: datetime

    def public_payload(self, *, include_technical: bool = False) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            exclude={
                "tenant_ref",
                "group_ref",
                "created_by_ref",
                "updated_by_ref",
            },
        )
        payload["state"] = "active" if self.enabled else "disabled"
        if include_technical:
            payload["technical_ref"] = self.group_ref
        return payload


class EntraGroupMappingRepository(Protocol):
    def list(self, tenant_ref: str) -> list[EntraGroupMapping]: ...
    def get(self, tenant_ref: str, mapping_id: str) -> EntraGroupMapping: ...
    def create(self, value: EntraGroupMapping) -> EntraGroupMapping: ...
    def update(
        self,
        tenant_ref: str,
        mapping_id: str,
        *,
        base_revision: int,
        changes: Mapping[str, Any],
        actor_ref: str,
    ) -> EntraGroupMapping: ...


class InMemoryEntraGroupMappingRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[tuple[str, str], EntraGroupMapping] = {}

    def list(self, tenant_ref: str) -> list[EntraGroupMapping]:
        with self._lock:
            values = [
                value.model_copy(deep=True)
                for (tenant, _), value in self._values.items()
                if tenant == tenant_ref
            ]
        return sorted(values, key=lambda item: (-item.priority, item.display_name.lower()))

    def get(self, tenant_ref: str, mapping_id: str) -> EntraGroupMapping:
        with self._lock:
            value = self._values.get((tenant_ref, mapping_id))
        if value is None:
            raise GroupMappingNotFound()
        return value.model_copy(deep=True)

    def create(self, value: EntraGroupMapping) -> EntraGroupMapping:
        key = (value.tenant_ref, value.mapping_id)
        with self._lock:
            if key in self._values:
                raise GroupMappingConflict()
            self._values[key] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def update(
        self,
        tenant_ref: str,
        mapping_id: str,
        *,
        base_revision: int,
        changes: Mapping[str, Any],
        actor_ref: str,
    ) -> EntraGroupMapping:
        allowed = {
            "display_name",
            "role",
            "workspace_ids",
            "priority",
            "enabled",
        }
        if not changes or set(changes) - allowed:
            raise ValueError("invalid group mapping changes")
        key = (tenant_ref, mapping_id)
        with self._lock:
            current = self._values.get(key)
            if current is None:
                raise GroupMappingNotFound()
            if current.revision != base_revision:
                raise GroupMappingConflict()
            payload = _mapping_payload(current)
            payload.update(dict(changes))
            payload.update(
                {
                    "revision": current.revision + 1,
                    "updated_by_ref": actor_ref,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            updated = EntraGroupMapping.model_validate(payload)
            self._values[key] = updated
        return updated.model_copy(deep=True)


class SqlEntraGroupMappingRepository:
    def __init__(self, *, connection_factory) -> None:
        self._connection_factory = connection_factory

    def list(self, tenant_ref: str) -> list[EntraGroupMapping]:
        with self._connection_factory() as connection:
            rows = connection.cursor().execute(
                """/* entra:list-group-mappings */
                SELECT tenant_ref, mapping_id, group_ref, display_name,
                       role_name, workspace_scope_json, mapping_priority,
                       enabled, revision, created_by_ref, updated_by_ref,
                       created_at, updated_at
                FROM df_finops.entra_group_mapping
                WHERE tenant_ref = ?
                ORDER BY mapping_priority DESC, display_name ASC""",
                tenant_ref,
            ).fetchall()
        return [_mapping_from_row(row) for row in rows]

    def get(self, tenant_ref: str, mapping_id: str) -> EntraGroupMapping:
        with self._connection_factory() as connection:
            row = connection.cursor().execute(
                """/* entra:get-group-mapping */
                SELECT tenant_ref, mapping_id, group_ref, display_name,
                       role_name, workspace_scope_json, mapping_priority,
                       enabled, revision, created_by_ref, updated_by_ref,
                       created_at, updated_at
                FROM df_finops.entra_group_mapping
                WHERE tenant_ref = ? AND mapping_id = ?""",
                tenant_ref,
                mapping_id,
            ).fetchone()
        if row is None:
            raise GroupMappingNotFound()
        return _mapping_from_row(row)

    def create(self, value: EntraGroupMapping) -> EntraGroupMapping:
        try:
            with self._connection_factory() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """/* entra:create-group-mapping */
                    INSERT INTO df_finops.entra_group_mapping (
                        tenant_ref, mapping_id, group_ref, display_name,
                        role_name, workspace_scope_json, mapping_priority,
                        enabled, revision, created_by_ref, updated_by_ref,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    value.tenant_ref,
                    value.mapping_id,
                    value.group_ref,
                    value.display_name,
                    value.role,
                    json.dumps(value.workspace_ids, separators=(",", ":")),
                    value.priority,
                    value.enabled,
                    value.revision,
                    value.created_by_ref,
                    value.updated_by_ref,
                    value.created_at,
                    value.updated_at,
                )
                connection.commit()
        except Exception as exc:
            raise GroupMappingError() from exc
        return value

    def update(
        self,
        tenant_ref: str,
        mapping_id: str,
        *,
        base_revision: int,
        changes: Mapping[str, Any],
        actor_ref: str,
    ) -> EntraGroupMapping:
        current = self.get(tenant_ref, mapping_id)
        if current.revision != base_revision:
            raise GroupMappingConflict()
        allowed = {
            "display_name",
            "role",
            "workspace_ids",
            "priority",
            "enabled",
        }
        if not changes or set(changes) - allowed:
            raise ValueError("invalid group mapping changes")
        payload = _mapping_payload(current)
        payload.update(dict(changes))
        payload.update(
            {
                "revision": current.revision + 1,
                "updated_by_ref": actor_ref,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        updated = EntraGroupMapping.model_validate(payload)
        try:
            with self._connection_factory() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """/* entra:update-group-mapping */
                    UPDATE df_finops.entra_group_mapping SET
                        display_name = ?, role_name = ?,
                        workspace_scope_json = ?, mapping_priority = ?,
                        enabled = ?, revision = ?, updated_by_ref = ?,
                        updated_at = ?
                    WHERE tenant_ref = ? AND mapping_id = ? AND revision = ?""",
                    updated.display_name,
                    updated.role,
                    json.dumps(updated.workspace_ids, separators=(",", ":")),
                    updated.priority,
                    updated.enabled,
                    updated.revision,
                    updated.updated_by_ref,
                    updated.updated_at,
                    tenant_ref,
                    mapping_id,
                    base_revision,
                )
                if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                    connection.rollback()
                    raise GroupMappingConflict()
                connection.commit()
        except GroupMappingConflict:
            raise
        except Exception as exc:
            raise GroupMappingError() from exc
        return updated


@dataclass(frozen=True, slots=True)
class GroupAccessResolution:
    role: GroupRole | None
    mapping_ids: tuple[str, ...] = ()


def resolve_group_access(
    mappings: Iterable[EntraGroupMapping],
    *,
    group_refs: set[str],
    workspace_id: str,
) -> GroupAccessResolution:
    matches = [
        item
        for item in mappings
        if item.enabled
        and item.group_ref in group_refs
        and workspace_id in item.workspace_ids
    ]
    if not matches:
        return GroupAccessResolution(None)
    highest_priority = max(item.priority for item in matches)
    highest = [item for item in matches if item.priority == highest_priority]
    roles = {item.role for item in highest}
    if len(roles) != 1:
        raise GroupMappingConflict()
    return GroupAccessResolution(
        role=next(iter(roles)),
        mapping_ids=tuple(sorted(item.mapping_id for item in highest)),
    )


_IN_MEMORY_REPOSITORY = InMemoryEntraGroupMappingRepository()


def get_entra_group_mapping_repository() -> EntraGroupMappingRepository:
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        return SqlEntraGroupMappingRepository(
            connection_factory=build_lineage_sql_connection_factory()
        )
    return _IN_MEMORY_REPOSITORY


def group_ref_for(tenant_id: str, group_id: str, *, secret: str) -> str:
    return opaque_ref(
        "group",
        canonical_tenant_id(tenant_id),
        str(group_id or "").strip().lower(),
        secret=secret,
    )


def resolve_actor_group_role(
    workspace_id: str,
    actor: Mapping[str, Any],
) -> tuple[str | None, str]:
    if not _enabled("DF_ENTRA_GROUP_GOVERNANCE_ENABLED"):
        return None, "membership_missing"
    state = str(actor.get("group_resolution_state") or "").strip().lower()
    if state == "unavailable":
        return None, "group_resolution_unavailable"
    tenant_id = str(actor.get("tenant_id") or "").strip()
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not tenant_id or not secret:
        return None, "group_resolution_unavailable"
    refs = {
        str(item).strip()
        for item in actor.get("group_refs") or []
        if str(item).strip()
    }
    if not refs and not bool(actor.get("group_overage")):
        refs = {
            group_ref_for(tenant_id, str(item), secret=secret)
            for item in actor.get("groups") or []
            if str(item).strip()
        }
    if not refs:
        return None, "membership_missing"
    tenant_ref = canonical_tenant_ref(tenant_id, secret=secret)
    try:
        resolution = resolve_group_access(
            get_entra_group_mapping_repository().list(tenant_ref),
            group_refs=refs,
            workspace_id=workspace_id,
        )
    except Exception:
        return None, "group_resolution_unavailable"
    return (
        (resolution.role, "group_match")
        if resolution.role
        else (None, "membership_missing")
    )


def _mapping_from_row(row: Sequence[Any]) -> EntraGroupMapping:
    return EntraGroupMapping(
        tenant_ref=str(row[0]),
        mapping_id=str(row[1]),
        group_ref=str(row[2]),
        display_name=str(row[3]),
        role=str(row[4]),
        workspace_ids=json.loads(str(row[5] or "[]")),
        priority=int(row[6]),
        enabled=bool(row[7]),
        revision=int(row[8]),
        created_by_ref=str(row[9]),
        updated_by_ref=str(row[10]),
        created_at=row[11],
        updated_at=row[12],
    )


def _mapping_payload(value: EntraGroupMapping) -> dict[str, Any]:
    return {
        **value.model_dump(),
        "tenant_ref": value.tenant_ref,
        "group_ref": value.group_ref,
        "created_by_ref": value.created_by_ref,
        "updated_by_ref": value.updated_by_ref,
    }


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


__all__ = [
    "EntraGroupMapping",
    "EntraGroupMappingRepository",
    "GroupAccessResolution",
    "GroupMappingConflict",
    "GroupMappingError",
    "GroupMappingNotFound",
    "InMemoryEntraGroupMappingRepository",
    "SqlEntraGroupMappingRepository",
    "get_entra_group_mapping_repository",
    "group_ref_for",
    "resolve_actor_group_role",
    "resolve_group_access",
]
