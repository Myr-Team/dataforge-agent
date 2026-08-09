from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .aws_bedrock_provider import bedrock_control_endpoint
from .model_provider_secrets import SecretStatus


ProviderType = Literal["deepseek", "aws_bedrock"]
ConnectionState = Literal[
    "testing",
    "connected",
    "degraded",
    "invalid",
    "disabled",
]
GovernanceState = Literal["pending", "governed", "degraded", "unmanaged"]
ProviderSupportState = Literal["supported", "unsupported", "unpriced"]
ConnectionStage = Literal[
    "secret_read",
    "endpoint_resolution",
    "tls_connect",
    "provider_auth",
    "minimal_inference",
    "model_discovery",
    "completed",
]
PROVIDER_CONNECTION_STAGES = frozenset(ConnectionStage.__args__)
DEEPSEEK_API_ENDPOINT = "https://api.deepseek.com"


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=200)
    capabilities: list[str] = Field(default_factory=list, max_length=24)
    support_state: ProviderSupportState
    price_key: str | None = Field(default=None, max_length=240)

    @field_validator("capabilities")
    @classmethod
    def _capabilities(cls, value: list[str]) -> list[str]:
        clean: list[str] = []
        for item in value:
            normalized = str(item or "").strip().lower()
            if not normalized or len(normalized) > 48:
                raise ValueError("invalid provider capability")
            if normalized not in clean:
                clean.append(normalized)
        return clean


class ModelProviderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=80)
    tenant_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    provider_type: ProviderType
    display_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=320)
    region: str | None = Field(default=None, max_length=32)
    secret_ref: str = Field(min_length=1, max_length=240, exclude=True, repr=False)
    connection_state: ConnectionState
    governance_state: GovernanceState
    available_models: list[ProviderModel] = Field(default_factory=list, max_length=64)
    last_tested_at: datetime | None = None
    last_success_at: datetime | None = None
    safe_error_category: str | None = Field(default=None, max_length=64)
    connection_stage: ConnectionStage | None = None
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    revision: int = Field(ge=1)
    created_by_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    updated_by_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    created_at: datetime
    updated_at: datetime

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return _https_endpoint(value)

    @field_validator("stage_durations_ms")
    @classmethod
    def _stage_durations(cls, value: dict[str, int]) -> dict[str, int]:
        if any(key not in PROVIDER_CONNECTION_STAGES for key in value):
            raise ValueError("unknown provider connection stage")
        if any(not isinstance(duration, int) or duration < 0 for duration in value.values()):
            raise ValueError("invalid provider stage duration")
        return value

    @model_validator(mode="after")
    def _bedrock_control_endpoint(self) -> "ModelProviderRecord":
        if self.provider_type != "aws_bedrock":
            return self
        try:
            endpoint = bedrock_control_endpoint(self.region or "")
        except ValueError:
            raise ValueError("bedrock_region_unsupported") from None
        if self.base_url != endpoint:
            raise ValueError("bedrock_control_endpoint_mismatch")
        self.region = str(self.region or "").strip().lower()
        return self

    def public_payload(
        self,
        *,
        secret_status: SecretStatus = "unavailable",
    ) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            exclude={
                "tenant_ref",
                "secret_ref",
                "created_by_ref",
                "updated_by_ref",
            },
        )
        payload["secret_status"] = secret_status
        payload["route_eligibility"] = provider_route_eligibility(
            self,
            secret_status=secret_status,
        )
        return payload


class ProviderPatch(BaseModel):
    """Internal provider state/configuration transition.

    API handlers must project their dedicated external DTO into this model
    rather than exposing server-owned observation fields directly.
    """

    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=320)
    region: str | None = Field(default=None, max_length=32)
    connection_state: ConnectionState | None = None
    governance_state: GovernanceState | None = None
    available_models: list[ProviderModel] | None = Field(default=None, max_length=64)
    last_tested_at: datetime | None = None
    last_success_at: datetime | None = None
    safe_error_category: str | None = Field(default=None, max_length=64)
    connection_stage: ConnectionStage | None = None
    stage_durations_ms: dict[str, int] | None = None

    @field_validator("stage_durations_ms")
    @classmethod
    def _stage_durations(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is None:
            return None
        if any(key not in PROVIDER_CONNECTION_STAGES for key in value):
            raise ValueError("unknown provider connection stage")
        if any(not isinstance(duration, int) or duration < 0 for duration in value.values()):
            raise ValueError("invalid provider stage duration")
        return value

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        return _https_endpoint(value) if value is not None else None

    @model_validator(mode="after")
    def _has_update(self) -> "ProviderPatch":
        values = self.model_dump(exclude={"base_revision"}, exclude_none=True)
        if not values:
            raise ValueError("provider patch has no changes")
        if self.region is not None:
            try:
                endpoint = bedrock_control_endpoint(self.region)
            except ValueError:
                raise ValueError("bedrock_region_unsupported") from None
            if self.base_url is not None and self.base_url != endpoint:
                raise ValueError("bedrock_control_endpoint_mismatch")
        return self


def deepseek_api_endpoint(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("deepseek_endpoint_unsupported") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.deepseek.com"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("deepseek_endpoint_unsupported")
    return DEEPSEEK_API_ENDPOINT


def provider_route_eligibility(
    value: ModelProviderRecord,
    *,
    secret_status: SecretStatus,
) -> dict[str, Any]:
    supported_models = [
        model
        for model in value.available_models
        if model.support_state == "supported"
    ]
    eligible_model_count = sum(bool(model.price_key) for model in supported_models)
    reason: str | None = None
    can_govern = False
    selectable = False
    state = "unavailable"
    if secret_status != "stored":
        reason = "provider_secret_unavailable"
    elif value.provider_type != "deepseek":
        reason = "provider_type_not_routable"
    elif value.last_success_at is None:
        reason = "connection_verification_required"
    elif value.connection_state != "connected":
        reason = "provider_connection_unavailable"
    elif not supported_models:
        reason = "supported_model_required"
    elif eligible_model_count != len(supported_models):
        reason = "official_pricing_required"
    elif value.governance_state != "governed":
        state = "governance_required"
        reason = "governance_required"
        can_govern = True
    else:
        state = "selectable"
        selectable = True
    return {
        "state": state,
        "selectable": selectable,
        "can_govern": can_govern,
        "reason": reason,
        "eligible_model_count": eligible_model_count,
    }


def _https_endpoint(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("provider endpoint must be https")
    path = parsed.path.rstrip("/")
    return f"https://{parsed.hostname}{path}"


__all__ = [
    "ConnectionState",
    "ConnectionStage",
    "DEEPSEEK_API_ENDPOINT",
    "GovernanceState",
    "ModelProviderRecord",
    "ProviderModel",
    "ProviderPatch",
    "ProviderSupportState",
    "ProviderType",
    "PROVIDER_CONNECTION_STAGES",
    "deepseek_api_endpoint",
    "provider_route_eligibility",
]
