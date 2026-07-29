from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .aws_bedrock_provider import bedrock_control_endpoint


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
    revision: int = Field(ge=1)
    created_by_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    updated_by_ref: str = Field(min_length=1, max_length=160, exclude=True, repr=False)
    created_at: datetime
    updated_at: datetime

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return _https_endpoint(value)

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

    def public_payload(self) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            exclude={
                "tenant_ref",
                "secret_ref",
                "created_by_ref",
                "updated_by_ref",
            },
        )
        payload["secret_status"] = "stored"
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
    "DEEPSEEK_API_ENDPOINT",
    "GovernanceState",
    "ModelProviderRecord",
    "ProviderModel",
    "ProviderPatch",
    "ProviderSupportState",
    "ProviderType",
    "deepseek_api_endpoint",
]
