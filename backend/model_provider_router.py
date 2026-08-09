from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, Any, Literal, Mapping

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from .audit_store import record_audit_event
from .aws_bedrock_provider import AwsBedrockCredential, bedrock_control_endpoint
from .finops.normalization import canonical_actor_ref, canonical_tenant_ref
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
from .model_provider_service import (
    ModelProviderService,
    ProviderConfigurationError,
)
from .model_providers import ProviderPatch, deepseek_api_endpoint
from .provider_client import RequestsProviderTransport
from .workspace_authz import active_workspace_role
from .workspace_store import list_workspaces


router = APIRouter(prefix="/api/model-providers", tags=["model-providers"])
_IN_MEMORY_REPOSITORY = InMemoryModelProviderRepository()


class DeepSeekProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: Literal["deepseek"]
    display_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=320)
    api_key: str = Field(min_length=8, max_length=512, exclude=True, repr=False)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        try:
            return deepseek_api_endpoint(value)
        except ValueError:
            raise ValueError("invalid provider endpoint")


class BedrockProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: Literal["aws_bedrock"]
    display_name: str = Field(min_length=1, max_length=120)
    region: str = Field(min_length=1, max_length=32)
    access_key_id: str = Field(min_length=8, max_length=128, exclude=True, repr=False)
    secret_access_key: str = Field(
        min_length=16,
        max_length=256,
        exclude=True,
        repr=False,
    )
    session_token: str | None = Field(
        default=None,
        max_length=4096,
        exclude=True,
        repr=False,
    )

    @field_validator("region")
    @classmethod
    def _region(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        bedrock_control_endpoint(normalized)
        return normalized


ProviderCreateBody = Annotated[
    DeepSeekProviderCreate | BedrockProviderCreate,
    Field(discriminator="provider_type"),
]


class DeepSeekProviderRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: Literal["deepseek"]
    api_key: str = Field(min_length=8, max_length=512, exclude=True, repr=False)
    base_revision: int = Field(ge=1)


class BedrockProviderRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: Literal["aws_bedrock"]
    access_key_id: str = Field(min_length=8, max_length=128, exclude=True, repr=False)
    secret_access_key: str = Field(
        min_length=16,
        max_length=256,
        exclude=True,
        repr=False,
    )
    session_token: str | None = Field(
        default=None,
        max_length=4096,
        exclude=True,
        repr=False,
    )
    base_revision: int = Field(ge=1)


ProviderRotateBody = Annotated[
    DeepSeekProviderRotate | BedrockProviderRotate,
    Field(discriminator="provider_type"),
]


class ProviderRotateRequest(RootModel[ProviderRotateBody]):
    @model_validator(mode="before")
    @classmethod
    def _preserve_deepseek_contract(cls, value: object) -> object:
        if isinstance(value, dict) and "provider_type" not in value:
            return {"provider_type": "deepseek", **value}
        return value


class ProviderDisableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1)


class ProviderGovernanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1)


class ProviderConfigurationPatchBody(BaseModel):
    """Owner-editable provider configuration only.

    Connection observations, discovered models, governance evidence, error
    categories, and timestamps are intentionally absent and remain server-owned.
    """

    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=320)
    region: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _valid_configuration_patch(self) -> "ProviderConfigurationPatchBody":
        self.to_internal_patch()
        return self

    def to_internal_patch(self) -> ProviderPatch:
        return ProviderPatch.model_validate(
            self.model_dump(exclude_none=True)
        )


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
    repository = get_model_provider_repository()
    if isinstance(body, BedrockProviderCreate):
        secret_value = AwsBedrockCredential(
            access_key_id=body.access_key_id,
            secret_access_key=body.secret_access_key,
            session_token=body.session_token,
        ).to_secret_value()
        base_url = bedrock_control_endpoint(body.region)
        region = body.region
    else:
        secret_value = body.api_key
        base_url = body.base_url
        region = None
    try:
        with repository.mutation_guard(tenant_ref, provider_id):
            if isinstance(body, BedrockProviderCreate):
                _bedrock_enabled()
            _audit_required(
                request,
                audit_workspace,
                provider_id,
                reason_code="authorized",
                provider_type=body.provider_type,
                display_name=body.display_name,
                region=region,
            )
            return _service(repository).create(
                tenant_ref=tenant_ref,
                actor_ref=actor_ref,
                provider_type=body.provider_type,
                display_name=body.display_name,
                base_url=base_url,
                region=region,
                secret_value=secret_value,
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
    repository = get_model_provider_repository()
    try:
        with repository.mutation_guard(tenant_ref, provider_id):
            provider = _provider_for_mutation(
                repository,
                tenant_ref,
                provider_id,
            )
            if provider.provider_type == "aws_bedrock":
                _bedrock_enabled()
            _audit_required(
                request,
                audit_workspace,
                provider_id,
                reason_code="authorized",
                provider_type=provider.provider_type,
                display_name=provider.display_name,
                region=provider.region,
            )
            return _service(repository).test(
                tenant_ref=tenant_ref,
                provider_id=provider_id,
                actor_ref=actor_ref,
            )
    except Exception as exc:
        raise _provider_error(exc)


@router.post("/{provider_id}/rotate-secret")
async def rotate_model_provider_secret(
    provider_id: str,
    body: ProviderRotateRequest,
    request: Request,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    rotate_body = body.root
    repository = get_model_provider_repository()
    if isinstance(rotate_body, BedrockProviderRotate):
        secret_value = AwsBedrockCredential(
            access_key_id=rotate_body.access_key_id,
            secret_access_key=rotate_body.secret_access_key,
            session_token=rotate_body.session_token,
        ).to_secret_value()
    else:
        secret_value = rotate_body.api_key
    try:
        with repository.mutation_guard(tenant_ref, provider_id):
            provider = _provider_for_mutation(
                repository,
                tenant_ref,
                provider_id,
                base_revision=rotate_body.base_revision,
            )
            if rotate_body.provider_type != provider.provider_type:
                raise HTTPException(status_code=409, detail="provider_type_mismatch")
            if isinstance(rotate_body, BedrockProviderRotate):
                _bedrock_enabled()
            _audit_required(
                request,
                audit_workspace,
                provider_id,
                reason_code="authorized",
                provider_type=provider.provider_type,
                display_name=provider.display_name,
                region=provider.region,
            )
            return _service(repository).rotate(
                tenant_ref=tenant_ref,
                provider_id=provider_id,
                secret_value=secret_value,
                base_revision=rotate_body.base_revision,
                actor_ref=actor_ref,
            )
    except Exception as exc:
        raise _provider_error(exc)


@router.patch("/{provider_id}")
async def update_model_provider(
    provider_id: str,
    body: ProviderConfigurationPatchBody,
    request: Request,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    repository = get_model_provider_repository()
    try:
        with repository.mutation_guard(tenant_ref, provider_id):
            provider = _provider_for_mutation(
                repository,
                tenant_ref,
                provider_id,
                base_revision=body.base_revision,
            )
            service = _service(repository)
            patch = service.prepare_configuration_patch(
                provider,
                body.to_internal_patch(),
            )
            if provider.provider_type == "aws_bedrock":
                _bedrock_enabled()
            _audit_required(
                request,
                audit_workspace,
                provider_id,
                reason_code="authorized",
                provider_type=provider.provider_type,
                display_name=provider.display_name,
                region=provider.region,
            )
            return repository.update(
                tenant_ref,
                provider_id,
                patch,
                actor_ref=actor_ref,
            ).public_payload()
    except Exception as exc:
        raise _provider_error(exc)


@router.post("/{provider_id}/disable")
async def disable_model_provider(
    provider_id: str,
    body: ProviderDisableBody,
    request: Request,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    repository = get_model_provider_repository()
    try:
        with repository.mutation_guard(tenant_ref, provider_id):
            provider = _provider_for_mutation(
                repository,
                tenant_ref,
                provider_id,
                base_revision=body.base_revision,
            )
            if provider.provider_type == "aws_bedrock":
                _bedrock_enabled()
            _audit_required(
                request,
                audit_workspace,
                provider_id,
                reason_code="authorized",
                provider_type=provider.provider_type,
                display_name=provider.display_name,
                region=provider.region,
            )
            return _service(repository).disable(
                tenant_ref=tenant_ref,
                provider_id=provider_id,
                base_revision=body.base_revision,
                actor_ref=actor_ref,
            )
    except Exception as exc:
        raise _provider_error(exc)


@router.post("/{provider_id}/govern")
async def govern_model_provider(
    provider_id: str,
    body: ProviderGovernanceBody,
    request: Request,
) -> dict[str, object]:
    return _transition_provider_governance(
        provider_id,
        body,
        request,
        target_state="governed",
        reason_code="routing_governed",
    )


@router.post("/{provider_id}/suspend")
async def suspend_model_provider_routing(
    provider_id: str,
    body: ProviderGovernanceBody,
    request: Request,
) -> dict[str, object]:
    return _transition_provider_governance(
        provider_id,
        body,
        request,
        target_state="pending",
        reason_code="routing_suspended",
    )


def _transition_provider_governance(
    provider_id: str,
    body: ProviderGovernanceBody,
    request: Request,
    *,
    target_state: str,
    reason_code: str,
) -> dict[str, object]:
    tenant_ref, actor_ref, _roles, audit_workspace = _context(request)
    repository = get_model_provider_repository()
    try:
        with repository.mutation_guard(tenant_ref, provider_id):
            provider = _provider_for_mutation(
                repository,
                tenant_ref,
                provider_id,
                base_revision=body.base_revision,
            )
            service = _service(repository)
            patch = service.prepare_governance_patch(
                provider,
                target_state=target_state,
            )
            _audit_required(
                request,
                audit_workspace,
                provider_id,
                reason_code=reason_code,
                provider_type=provider.provider_type,
                display_name=provider.display_name,
                region=provider.region,
            )
            updated = repository.update(
                tenant_ref,
                provider_id,
                patch,
                actor_ref=actor_ref,
            )
            return service.public_payload(updated)
    except Exception as exc:
        raise _provider_error(exc)


def _service(
    repository: ModelProviderRepository | None = None,
) -> ModelProviderService:
    return ModelProviderService(
        repository=repository or get_model_provider_repository(),
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
        canonical_tenant_ref(tenant_id, secret=secret),
        canonical_actor_ref(tenant_id, actor_id, secret=secret),
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
    provider_type: str | None = None,
    display_name: str | None = None,
    region: str | None = None,
) -> None:
    try:
        resource: dict[str, str] = {
            "workspace_id": workspace_id,
            "resource_type": "model_provider",
            "resource_id": provider_id,
        }
        if provider_type:
            resource["provider_type"] = provider_type
        if display_name:
            resource["display_name"] = display_name
        if region:
            resource["region"] = region
        record_audit_event(
            actor_from_request(request, fallback=False),
            "model_provider.manage",
            resource,
            result="allowed",
            reason_code=reason_code,
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Audit persistence is required",
        ) from None


def _provider_for_mutation(
    repository: ModelProviderRepository,
    tenant_ref: str,
    provider_id: str,
    *,
    base_revision: int | None = None,
) -> Any:
    try:
        provider = repository.get(tenant_ref, provider_id)
    except Exception as exc:
        raise _provider_error(exc)
    if base_revision is not None and provider.revision != base_revision:
        raise HTTPException(status_code=409, detail="provider_revision_conflict")
    return provider


def _bedrock_enabled() -> None:
    if not _enabled("DF_AWS_BEDROCK_CONNECTOR_ENABLED"):
        raise HTTPException(
            status_code=404,
            detail="Model provider capability is disabled",
        )


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ModelProviderConflictError):
        return HTTPException(status_code=409, detail=exc.code)
    if isinstance(exc, ModelProviderNotFoundError):
        return HTTPException(status_code=404, detail=exc.code)
    if isinstance(exc, ModelProviderSecretError):
        return HTTPException(status_code=503, detail=exc.code)
    if isinstance(exc, ProviderConfigurationError):
        return HTTPException(status_code=422, detail=exc.code)
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
