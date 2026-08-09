from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .model_providers import deepseek_api_endpoint


_OPAQUE_ID = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_DEEPSEEK_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})


class ProviderApimError(ValueError):
    """Raised when a candidate cannot be represented by the owned template."""


class ApimProviderCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=80)
    provider_type: Literal["deepseek"]
    base_url: str = Field(min_length=1, max_length=320)
    model_ids: list[str] = Field(min_length=1, max_length=16)
    revision: int = Field(ge=1)

    @field_validator("provider_id")
    @classmethod
    def _provider_id(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _OPAQUE_ID.fullmatch(normalized):
            raise ValueError("provider id must be opaque")
        return normalized

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        try:
            return deepseek_api_endpoint(value)
        except ValueError as exc:
            raise ValueError("provider endpoint is not allowed") from exc

    @field_validator("model_ids")
    @classmethod
    def _model_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = str(value or "").strip()
            if not _MODEL_ID.fullmatch(item) or item not in _DEEPSEEK_MODELS:
                raise ValueError("provider model is not supported")
            if item not in normalized:
                normalized.append(item)
        return normalized


class ApimCandidateVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_created: bool
    managed_identity_status: int
    anonymous_status: int
    expected_policy_hash: str = Field(min_length=64, max_length=64)
    observed_policy_hash: str = Field(min_length=64, max_length=64)
    expected_etag: str = Field(min_length=1, max_length=160)
    observed_etag: str = Field(min_length=1, max_length=160)
    correlation_preserved: bool
    usage_preserved: bool


def validate_deepseek_candidate(
    *,
    connection_state: str,
    governance_state: str,
    support_state: str,
    price_key: str | None,
) -> None:
    """Reject any external candidate that is not ready for zero-traffic validation."""
    if (
        str(connection_state or "").strip() != "connected"
        or str(governance_state or "").strip() != "governed"
        or str(support_state or "").strip() != "supported"
        or not str(price_key or "").strip()
    ):
        raise ProviderApimError("provider_candidate_ineligible")


def build_candidate_contract(candidate: ApimProviderCandidate) -> dict[str, Any]:
    """Render typed values consumed by a server-owned APIM policy template."""
    payload = {
        "contract_version": 1,
        "provider_id": candidate.provider_id,
        "provider_type": candidate.provider_type,
        "backend_origin": candidate.base_url,
        "model_ids": list(candidate.model_ids),
        "revision": candidate.revision,
        "auth_mode": "managed_identity",
        "correlation_header": "x-dataforge-correlation",
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {**payload, "policy_hash": hashlib.sha256(encoded).hexdigest()}


def candidate_is_activatable(
    verification: ApimCandidateVerification,
    *,
    environment: dict[str, str] | None = None,
) -> bool:
    source = os.environ if environment is None else environment
    enabled = str(
        source.get("DF_EXTERNAL_PROVIDER_APIM_PROVISIONING_ENABLED") or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    return bool(
        enabled
        and verification.revision_created
        and verification.managed_identity_status == 200
        and verification.anonymous_status == 401
        and verification.expected_policy_hash == verification.observed_policy_hash
        and verification.expected_etag == verification.observed_etag
        and verification.correlation_preserved
        and verification.usage_preserved
    )


__all__ = [
    "ApimCandidateVerification",
    "ApimProviderCandidate",
    "ProviderApimError",
    "build_candidate_contract",
    "candidate_is_activatable",
    "validate_deepseek_candidate",
]
