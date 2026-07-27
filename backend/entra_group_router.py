from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .audit_store import record_audit_event
from .entra_group_mapping import (
    EntraGroupMapping,
    GroupMappingConflict,
    GroupMappingError,
    GroupMappingNotFound,
    get_entra_group_mapping_repository,
    group_ref_for,
)
from .finops.normalization import opaque_ref
from .graph_client import graph_token_context, search_entra_groups
from .identity import actor_from_request, is_trusted_tenant_identity
from .workspace_authz import active_workspace_role
from .workspace_store import list_workspaces


router = APIRouter(
    prefix="/api/identity-governance",
    tags=["identity-governance"],
)


class GroupMappingCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(
        min_length=1,
        max_length=160,
        exclude=True,
        repr=False,
    )
    display_name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "editor", "viewer"]
    workspace_ids: list[str] = Field(min_length=1, max_length=128)
    priority: int = Field(default=100, ge=0, le=1000)


class GroupMappingPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Literal["admin", "editor", "viewer"] | None = None
    workspace_ids: list[str] | None = Field(default=None, min_length=1, max_length=128)
    priority: int | None = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _has_change(self) -> "GroupMappingPatchBody":
        if not self.model_dump(exclude={"base_revision"}, exclude_none=True):
            raise ValueError("group mapping patch has no changes")
        return self


class GroupMappingDisableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1)


@router.get("")
async def identity_governance(request: Request) -> dict[str, Any]:
    tenant_ref, _actor_ref, _roles, _audit_workspace = _context(request)
    try:
        items = get_entra_group_mapping_repository().list(tenant_ref)
    except Exception as exc:
        raise _mapping_error(exc)
    token_context = graph_token_context(request)
    permission_state = (
        "configured"
        if token_context.get("source") in {"delegated", "app_only"}
        else "unavailable"
    )
    return {
        "mappings": [item.public_payload() for item in items],
        "mapping_count": len(items),
        "permissions": {
            "User.ReadBasic.All": permission_state,
            "GroupMember.Read.All": permission_state,
        },
        "membership_resolution": {
            "claims": "enabled",
            "overage_fallback": "enabled",
            "failure_mode": "explicit_membership_only",
        },
    }


@router.get("/groups")
async def identity_governance_groups(
    request: Request,
    query: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    _context(request)
    return search_entra_groups(query, request, limit=limit)


@router.post(
    "/group-mappings",
    status_code=status.HTTP_201_CREATED,
)
async def create_group_mapping(
    body: GroupMappingCreateBody,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, roles, audit_workspace = _context(request)
    _require_workspace_scope(body.workspace_ids, roles)
    mapping_id = f"mapping_{uuid.uuid4().hex[:24]}"
    _audit_required(request, audit_workspace, mapping_id)
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    actor = actor_from_request(request, fallback=False)
    tenant_id = str(actor.get("tenant_id") or "").strip()
    now = datetime.now(timezone.utc)
    value = EntraGroupMapping(
        mapping_id=mapping_id,
        tenant_ref=tenant_ref,
        group_ref=group_ref_for(tenant_id, body.group_id, secret=secret),
        display_name=body.display_name,
        role=body.role,
        workspace_ids=sorted(set(body.workspace_ids)),
        priority=body.priority,
        enabled=True,
        revision=1,
        created_by_ref=actor_ref,
        updated_by_ref=actor_ref,
        created_at=now,
        updated_at=now,
    )
    try:
        saved = get_entra_group_mapping_repository().create(value)
    except Exception as exc:
        raise _mapping_error(exc)
    return saved.public_payload()


@router.patch("/group-mappings/{mapping_id}")
async def update_group_mapping(
    mapping_id: str,
    body: GroupMappingPatchBody,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, roles, audit_workspace = _context(request)
    if body.workspace_ids is not None:
        _require_workspace_scope(body.workspace_ids, roles)
    _audit_required(request, audit_workspace, mapping_id)
    changes = body.model_dump(
        exclude={"base_revision"},
        exclude_none=True,
    )
    try:
        saved = get_entra_group_mapping_repository().update(
            tenant_ref,
            mapping_id,
            base_revision=body.base_revision,
            changes=changes,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _mapping_error(exc)
    return saved.public_payload()


@router.post("/group-mappings/{mapping_id}/disable")
async def disable_group_mapping(
    mapping_id: str,
    body: GroupMappingDisableBody,
    request: Request,
) -> dict[str, Any]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    _audit_required(request, audit_workspace, mapping_id)
    try:
        saved = get_entra_group_mapping_repository().update(
            tenant_ref,
            mapping_id,
            base_revision=body.base_revision,
            changes={"enabled": False},
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _mapping_error(exc)
    return saved.public_payload()


def _context(
    request: Request,
) -> tuple[str, str, dict[str, str], str]:
    if not _enabled("DF_ENTRA_GROUP_GOVERNANCE_ENABLED"):
        raise HTTPException(
            status_code=404,
            detail="Identity governance capability is disabled",
        )
    actor = actor_from_request(request, fallback=False)
    if not is_trusted_tenant_identity(actor):
        raise HTTPException(
            status_code=401,
            detail="trusted tenant identity is required",
        )
    roles = _authorized_workspace_roles(actor)
    if not roles or not all(
        role in {"owner", "admin"} for role in roles.values()
    ):
        raise HTTPException(
            status_code=403,
            detail="Identity governance requires admin or owner",
        )
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    tenant_id = str(actor.get("tenant_id") or "").strip()
    actor_id = str(actor.get("actor_id") or "").strip()
    if not secret or not tenant_id or not actor_id:
        raise HTTPException(
            status_code=503,
            detail="Identity governance scope is unavailable",
        )
    return (
        opaque_ref("tenant", tenant_id, secret=secret),
        opaque_ref("actor", tenant_id, actor_id, secret=secret),
        roles,
        sorted(roles)[0],
    )


def _authorized_workspace_roles(actor: Mapping[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for item in list_workspaces():
        workspace_id = str(item.get("workspace_id") or "").strip()
        if not workspace_id:
            continue
        try:
            role = active_workspace_role(workspace_id, actor)
        except FileNotFoundError:
            continue
        if role:
            roles[workspace_id] = role
    return roles


def _require_workspace_scope(
    workspace_ids: list[str],
    roles: Mapping[str, str],
) -> None:
    requested = {str(item).strip() for item in workspace_ids if str(item).strip()}
    if not requested or not requested.issubset(set(roles)):
        raise HTTPException(
            status_code=403,
            detail="Group mapping workspace scope is not authorized",
        )


def _audit_required(
    request: Request,
    workspace_id: str,
    mapping_id: str,
) -> None:
    try:
        record_audit_event(
            actor_from_request(request, fallback=False),
            "entra_group_mapping.manage",
            {
                "workspace_id": workspace_id,
                "resource_type": "entra_group_mapping",
                "resource_id": mapping_id,
            },
            result="allowed",
            reason_code="authorized",
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Audit persistence is required",
        ) from None


def _mapping_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, GroupMappingConflict):
        return HTTPException(status_code=409, detail=exc.code)
    if isinstance(exc, GroupMappingNotFound):
        return HTTPException(status_code=404, detail=exc.code)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail="invalid_group_mapping")
    if isinstance(exc, GroupMappingError):
        return HTTPException(status_code=503, detail=exc.code)
    return HTTPException(status_code=503, detail="entra_group_mapping_failed")


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


__all__ = [
    "get_entra_group_mapping_repository",
    "router",
]
