from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Mapping
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit_store import record_audit_event
from .finops.normalization import opaque_ref
from .identity import actor_from_request, is_trusted_tenant_identity
from .lineage_sql import build_lineage_sql_connection_factory
from .model_provider_repository import (
    InMemoryModelProviderRepository,
    ModelProviderConflictError,
    ModelProviderNotFoundError,
    ModelProviderRepository,
    ModelProviderRepositoryError,
    SqlModelProviderRepository,
)
from .model_provider_secrets import (
    ModelProviderSecretError,
    ModelProviderSecretStore,
    model_provider_secret_store_from_environment,
)
from .model_provider_service import ModelProviderService
from .model_providers import ProviderPatch
from .provider_client import RequestsProviderTransport
from .workspace_authz import active_workspace_role
from .workspace_store import list_workspaces


router = APIRouter(prefix="/api/model-providers", tags=["model-providers"])
_IN_MEMORY_REPOSITORY = InMemoryModelProviderRepository()


class ProviderCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: str = Field(pattern="^deepseek$")
    display_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=320)
    api_key: str = Field(min_length=8, max_length=512, exclude=True, repr=False)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        parsed = urlparse(str(value or "").strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.deepseek.com"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid provider endpoint")
        return "https://api.deepseek.com"


class ProviderRotateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=8, max_length=512, exclude=True, repr=False)
    base_revision: int = Field(ge=1)


class ProviderDisableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1)


def get_model_provider_repository() -> ModelProviderRepository:
    if _enabled("DF_FINOPS_SQL_ENABLED"):
        return SqlModelProviderRepository(
            connection_factory=build_lineage_sql_connection_factory()
        )
    return _IN_MEMORY_REPOSITORY


@lru_cache(maxsize=1)
def get_model_provider_secret_store() -> ModelProviderSecretStore:
    return model_provider_secret_store_from_environment()


@lru_cache(maxsize=1)
def get_provider_transport() -> RequestsProviderTransport:
    return RequestsProviderTransport()


@router.get("")
async def list_model_providers(request: Request) -> dict[str, Any]:
    tenant_ref, _actor_ref, _roles, _audit_workspace = _context(request)
    try:
        items = _service().list(tenant_ref)
    except Exception as exc:
        raise _provider_error(exc)
    return {"items": items, "count": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_model_provider(
    body: ProviderCreateBody,
    request: Request,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    provider_id = _new_provider_id()
    _audit_required(
        request,
        audit_workspace,
        provider_id,
        reason_code="authorized",
    )
    try:
        return _service().create(
            tenant_ref=tenant_ref,
            actor_ref=actor_ref,
            provider_type=body.provider_type,
            display_name=body.display_name,
            base_url=body.base_url,
            api_key=body.api_key,
            provider_id=provider_id,
        )
    except Exception as exc:
        raise _provider_error(exc)


@router.post("/{provider_id}/test")
async def test_model_provider(
    provider_id: str,
    request: Request,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    _audit_required(request, audit_workspace, provider_id, reason_code="authorized")
    try:
        return _service().test(
            tenant_ref=tenant_ref,
            provider_id=provider_id,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _provider_error(exc)


@router.post("/{provider_id}/rotate-secret")
async def rotate_model_provider_secret(
    provider_id: str,
    body: ProviderRotateBody,
    request: Request,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    _audit_required(request, audit_workspace, provider_id, reason_code="authorized")
    try:
        return _service().rotate(
            tenant_ref=tenant_ref,
            provider_id=provider_id,
            api_key=body.api_key,
            base_revision=body.base_revision,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _provider_error(exc)


@router.patch("/{provider_id}")
async def update_model_provider(
    provider_id: str,
    body: ProviderPatch,
    request: Request,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    _audit_required(request, audit_workspace, provider_id, reason_code="authorized")
    try:
        return _service().update(
            tenant_ref=tenant_ref,
            provider_id=provider_id,
            patch=body,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _provider_error(exc)


@router.post("/{provider_id}/disable")
async def disable_model_provider(
    provider_id: str,
    body: ProviderDisableBody,
    request: Request,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    _audit_required(request, audit_workspace, provider_id, reason_code="authorized")
    try:
        return _service().disable(
            tenant_ref=tenant_ref,
            provider_id=provider_id,
            base_revision=body.base_revision,
            actor_ref=actor_ref,
        )
    except Exception as exc:
        raise _provider_error(exc)


def _service() -> ModelProviderService:
    return ModelProviderService(
        repository=get_model_provider_repository(),
        secret_store=get_model_provider_secret_store(),
        transport=get_provider_transport(),
    )


def _context(
    request: Request,
) -> tuple[str, str, dict[str, str], str]:
    if not _enabled("DF_PROVIDER_CONNECTORS_ENABLED"):
        raise HTTPException(
            status_code=404,
            detail="Model provider capability is disabled",
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
            detail="Model provider management requires admin or owner",
        )
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    tenant_id = str(actor.get("tenant_id") or "").strip()
    actor_id = str(actor.get("actor_id") or "").strip()
    if not secret or not tenant_id or not actor_id:
        raise HTTPException(
            status_code=503,
            detail="Model provider scope is unavailable",
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


def _audit_required(
    request: Request,
    workspace_id: str,
    provider_id: str,
    *,
    reason_code: str,
) -> None:
    try:
        record_audit_event(
            actor_from_request(request, fallback=False),
            "model_provider.manage",
            {
                "workspace_id": workspace_id,
                "resource_type": "model_provider",
                "resource_id": provider_id,
            },
            result="allowed",
            reason_code=reason_code,
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Audit persistence is required",
        ) from None


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ModelProviderConflictError):
        return HTTPException(status_code=409, detail=exc.code)
    if isinstance(exc, ModelProviderNotFoundError):
        return HTTPException(status_code=404, detail=exc.code)
    if isinstance(exc, ModelProviderSecretError):
        return HTTPException(status_code=503, detail=exc.code)
    if isinstance(exc, ModelProviderRepositoryError):
        return HTTPException(status_code=503, detail=exc.code)
    return HTTPException(status_code=503, detail="model_provider_operation_failed")


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _new_provider_id() -> str:
    import uuid

    return f"provider_{uuid.uuid4().hex[:24]}"


__all__ = [
    "get_model_provider_repository",
    "get_model_provider_secret_store",
    "get_provider_transport",
    "router",
]
