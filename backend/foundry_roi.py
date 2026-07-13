from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

try:
    from .roi_service import parse_time_window
except ImportError:
    from roi_service import parse_time_window


class FoundryRoiTarget(BaseModel):
    """Canonical configuration required by a provider instance; never returned to clients."""

    model_config = ConfigDict(extra="forbid")
    project_endpoint: str
    agent_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")

    @field_validator("project_endpoint")
    @classmethod
    def canonical_project_endpoint(cls, value: str) -> str:
        parsed = urlparse(str(value).strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.lower().endswith(".services.ai.azure.com")
            or not parsed.path.rstrip("/").startswith("/api/projects/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("project endpoint must be a canonical Foundry project endpoint")
        return parsed.geturl().rstrip("/")


class FoundryRoiStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["connected", "not_configured", "unavailable"]
    configured: bool = False
    source: Literal["foundry_roi_provider"] = "foundry_roi_provider"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider_version: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,120}$")
    reason: str = Field(min_length=1, max_length=240)

    @field_validator("observed_at")
    @classmethod
    def utc_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include UTC offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def consistent_state(self) -> "FoundryRoiStatus":
        self.configured = self.state == "connected"
        return self


class ProviderAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    unit: Literal["currency"]

    @field_validator("amount")
    @classmethod
    def finite_nonnegative_amount(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("amount must be finite and non-negative")
        return value


class ProviderRoiSnapshot(BaseModel):
    """Sanitized provider value. Raw Foundry responses are intentionally not retained."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["foundry_roi_provider"] = "foundry_roi_provider"
    window: dict[str, str]
    observed_at: datetime
    provider_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,120}$")
    status: Literal["estimated", "measured", "verified"]
    business_value: ProviderAmount
    mapped_run_ids: list[str]
    mapped_outcome_event_ids: list[str]

    @field_validator("window")
    @classmethod
    def valid_window(cls, value: Mapping[str, Any]) -> dict[str, str]:
        return parse_time_window(value.get("from"), value.get("to"))

    @field_validator("observed_at")
    @classmethod
    def utc_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include UTC offset")
        return value.astimezone(timezone.utc)

    @field_validator("mapped_run_ids", "mapped_outcome_event_ids")
    @classmethod
    def valid_lineage_ids(cls, value: list[str]) -> list[str]:
        identifiers = sorted({str(item).strip() for item in value if str(item).strip()})
        if not identifiers:
            raise ValueError("provider values require mapped run and outcome lineage")
        if any(len(item) > 200 for item in identifiers):
            raise ValueError("lineage identifier is too long")
        return identifiers


class FoundryRoiProvider(Protocol):
    """An injected provider must be configured for one validated Foundry target."""

    def discover(self) -> FoundryRoiStatus: ...

    def read(self, window: Mapping[str, str]) -> ProviderRoiSnapshot: ...


def discover_foundry_roi(provider: FoundryRoiProvider | None = None) -> FoundryRoiStatus:
    """Discover an injected provider without treating environment flags as proof."""

    if _target_from_environment() is None:
        return _status("not_configured", "Canonical Foundry project endpoint and agent ID are not configured")
    if provider is None:
        return _status("not_configured", "No Foundry ROI provider is installed")
    try:
        discovered = FoundryRoiStatus.model_validate(provider.discover())
    except Exception:
        return _status("unavailable", "Foundry ROI provider discovery failed")
    if discovered.state == "connected":
        return _status(
            "connected",
            "Provider discovered the configured target agent and ROI surface",
            provider_version=discovered.provider_version,
        )
    if discovered.state == "unavailable":
        return _status("unavailable", "Foundry ROI provider is unavailable", provider_version=discovered.provider_version)
    return _status("not_configured", "Foundry ROI surface is not configured", provider_version=discovered.provider_version)


def read_foundry_roi(window: Mapping[str, Any], provider: FoundryRoiProvider | None = None) -> dict[str, Any]:
    """Read one sanitized provider snapshot, or expose a truthful unavailable state."""

    normalized_window = parse_time_window(window.get("from"), window.get("to"))
    status = discover_foundry_roi(provider)
    if status.state != "connected" or provider is None:
        return {"status": status.model_dump(mode="json"), "snapshot": None}
    try:
        snapshot = ProviderRoiSnapshot.model_validate(provider.read(normalized_window))
        if snapshot.window != normalized_window:
            raise ValueError("provider window does not match request")
    except (ValidationError, ValueError, TypeError):
        unavailable = _status("unavailable", "Foundry ROI provider returned an invalid snapshot", provider_version=status.provider_version)
        return {"status": unavailable.model_dump(mode="json"), "snapshot": None}
    except Exception:
        unavailable = _status("unavailable", "Foundry ROI provider read failed", provider_version=status.provider_version)
        return {"status": unavailable.model_dump(mode="json"), "snapshot": None}
    connected = _status("connected", status.reason, provider_version=snapshot.provider_version)
    return {"status": connected.model_dump(mode="json"), "snapshot": snapshot.model_dump(mode="json")}


def reconcile_roi(local: Mapping[str, Any], provider: ProviderRoiSnapshot | Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep local ROI authoritative and compare a provider value only when evidence aligns."""

    local_copy = _json_copy(local)
    snapshot, provider_status = _provider_snapshot(provider)
    metadata: dict[str, Any] = {
        "status": "not_reconciled",
        "reason": "Foundry ROI provider is not connected",
        "local_generated_at": local_copy.get("generated_at"),
        "provider_observed_at": snapshot.observed_at.isoformat() if snapshot else None,
        "provider_source": snapshot.source if snapshot else "foundry_roi_provider",
        "provider_version": snapshot.provider_version if snapshot else None,
    }
    if provider_status is not None and provider_status.state != "connected":
        metadata["reason"] = provider_status.reason
    if snapshot is None:
        return {"local": local_copy, "provider": None, "difference": None, "reconciliation": metadata}

    local_value, local_reason = _local_business_value(local_copy)
    if local_reason:
        metadata["reason"] = local_reason
        return {"local": local_copy, "provider": snapshot.model_dump(mode="json"), "difference": None, "reconciliation": metadata}
    assert local_value is not None
    if snapshot.window != local_value["window"]:
        metadata["reason"] = "window does not match local ROI"
    elif snapshot.business_value.currency != local_value["currency"]:
        metadata["reason"] = "currency does not match local ROI"
    elif snapshot.business_value.unit != local_value["unit"]:
        metadata["reason"] = "unit does not match local ROI"
    elif not set(snapshot.mapped_outcome_event_ids).intersection(local_value["outcome_ids"]):
        metadata["reason"] = "mapped outcome lineage does not match local ROI"
    else:
        metadata["status"] = "reconciled"
        metadata["reason"] = "mapped run and outcome lineage, window, currency, and unit match"
        difference = {
            "amount": round(snapshot.business_value.amount - local_value["amount"], 6),
            "currency": local_value["currency"],
            "unit": local_value["unit"],
        }
        return {
            "local": local_copy,
            "provider": snapshot.model_dump(mode="json"),
            "difference": difference,
            "reconciliation": metadata,
        }
    return {"local": local_copy, "provider": snapshot.model_dump(mode="json"), "difference": None, "reconciliation": metadata}


def _target_from_environment() -> FoundryRoiTarget | None:
    endpoint = str(os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").strip()
    agent_id = str(os.environ.get("FOUNDRY_AGENT_ID") or "").strip()
    if not endpoint or not agent_id:
        return None
    try:
        return FoundryRoiTarget(project_endpoint=endpoint, agent_id=agent_id)
    except ValidationError:
        return None


def _status(state: Literal["connected", "not_configured", "unavailable"], reason: str, *, provider_version: str | None = None) -> FoundryRoiStatus:
    return FoundryRoiStatus(state=state, configured=state == "connected", reason=reason, provider_version=provider_version)


def _provider_snapshot(provider: ProviderRoiSnapshot | Mapping[str, Any] | None) -> tuple[ProviderRoiSnapshot | None, FoundryRoiStatus | None]:
    if provider is None:
        return None, None
    if isinstance(provider, Mapping) and "status" in provider and "snapshot" in provider:
        try:
            status = FoundryRoiStatus.model_validate(provider["status"])
        except ValidationError:
            status = _status("unavailable", "Foundry ROI provider returned an invalid status")
        raw_snapshot = provider.get("snapshot")
    else:
        status, raw_snapshot = None, provider
    if raw_snapshot is None:
        return None, status
    try:
        payload = raw_snapshot.model_dump(mode="json") if isinstance(raw_snapshot, ProviderRoiSnapshot) else raw_snapshot
        return ProviderRoiSnapshot.model_validate(payload), status
    except ValidationError:
        return None, _status("unavailable", "Foundry ROI provider returned invalid lineage or snapshot data")


def _local_business_value(local: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        window = parse_time_window(local.get("window", {}).get("from"), local.get("window", {}).get("to"))
    except (AttributeError, TypeError, ValueError):
        return None, "local ROI window is unavailable"
    value = local.get("business_value")
    if not isinstance(value, Mapping) or value.get("total") is None:
        return None, "local ROI business value is unavailable"
    try:
        amount = float(value["total"])
    except (TypeError, ValueError):
        return None, "local ROI business value is invalid"
    currency = str(value.get("currency") or "")
    if not math.isfinite(amount) or amount < 0 or not re.fullmatch(r"[A-Z]{3}", currency):
        return None, "local ROI business value is invalid"
    outcome_ids = {str(item).strip() for item in local.get("outcome_event_ids", []) if str(item).strip()}
    if not outcome_ids:
        return None, "local ROI has no outcome lineage"
    return {"window": window, "amount": amount, "currency": currency, "unit": "currency", "outcome_ids": outcome_ids}, None


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    import json

    return json.loads(json.dumps(dict(value), default=str))


__all__ = [
    "FoundryRoiProvider",
    "FoundryRoiStatus",
    "ProviderRoiSnapshot",
    "discover_foundry_roi",
    "read_foundry_roi",
    "reconcile_roi",
]
