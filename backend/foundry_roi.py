from __future__ import annotations

import math
import os
import re
from hashlib import sha256
from hmac import compare_digest
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

try:
    from .roi_service import parse_time_window
except ImportError:
    from roi_service import parse_time_window


class FoundryRoiTarget(BaseModel):
    """Canonical configuration required by a provider instance; never returned to clients."""

    model_config = ConfigDict(extra="forbid")
    project_endpoint: str
    agent_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    target_fingerprint: str = Field(default="", pattern=r"^[a-f0-9]{64}$")

    @field_validator("project_endpoint")
    @classmethod
    def canonical_project_endpoint(cls, value: str) -> str:
        try:
            parsed = urlparse(str(value).strip())
            port = parsed.port
        except ValueError as exc:
            raise ValueError("project endpoint must be a canonical Foundry project endpoint") from exc
        host = (parsed.hostname or "").lower()
        account_host = re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.services\.ai\.azure\.com", host)
        project_path = re.fullmatch(r"/api/projects/(?:_project|[A-Za-z0-9][A-Za-z0-9._-]{0,127})", parsed.path)
        if (
            parsed.scheme != "https"
            or not account_host
            or port is not None
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or not project_path
        ):
            raise ValueError("project endpoint must be a canonical Foundry project endpoint")
        return f"https://{host}{parsed.path}"

    @model_validator(mode="after")
    def derived_fingerprint(self) -> "FoundryRoiTarget":
        self.target_fingerprint = sha256(f"{self.project_endpoint}\n{self.agent_id}".encode("utf-8")).hexdigest()
        return self


class DiscoveryProof(BaseModel):
    """Provider evidence that it discovered the exact configured target and ROI surface."""

    model_config = ConfigDict(extra="forbid")
    state: Literal["connected", "not_configured", "unavailable"]
    target_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    surface_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    surface_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def utc_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include UTC offset")
        return value.astimezone(timezone.utc)


class VerifiedDiscoveryAttestation(BaseModel):
    """Independent verifier evidence; provider proof alone can never establish connection."""

    model_config = ConfigDict(extra="forbid")
    target_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    surface_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    surface_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    observed_at: datetime
    observed_source: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")

    @field_validator("observed_at")
    @classmethod
    def utc_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include UTC offset")
        return value.astimezone(timezone.utc)


class FoundryRoiStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["connected", "configured_unverified", "not_configured", "unavailable"]
    configured: bool = False
    source: Literal["foundry_roi_provider", "foundry_roi_verifier"] = "foundry_roi_provider"
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
        self.configured = self.state in {"connected", "configured_unverified"}
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
    target_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
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


class VerifiedProviderRead(BaseModel):
    """A connected provider read bound to an independent discovery attestation."""

    model_config = ConfigDict(extra="forbid")
    target_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    attestation: VerifiedDiscoveryAttestation
    snapshot: ProviderRoiSnapshot
    _issued_by_adapter: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def matching_target_and_version(self) -> "VerifiedProviderRead":
        if not (
            compare_digest(self.target_fingerprint, self.attestation.target_fingerprint)
            and compare_digest(self.target_fingerprint, self.snapshot.target_fingerprint)
            and self.attestation.surface_version == self.snapshot.provider_version
        ):
            raise ValueError("verified provider read must match attested target and surface version")
        return self

    @classmethod
    def _issue(cls, *, target: FoundryRoiTarget, attestation: VerifiedDiscoveryAttestation, snapshot: ProviderRoiSnapshot) -> "VerifiedProviderRead":
        result = cls(target_fingerprint=target.target_fingerprint, attestation=attestation, snapshot=snapshot)
        result._issued_by_adapter = True
        return result


class FoundryRoiReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: FoundryRoiStatus
    verified_read: VerifiedProviderRead | None = None

    @model_validator(mode="after")
    def connected_requires_attested_read(self) -> "FoundryRoiReadResult":
        if (self.status.state == "connected") != (self.verified_read is not None):
            raise ValueError("only connected reads may contain a verified provider read")
        return self


class FoundryRoiProvider(Protocol):
    """An injected provider must be configured for one validated Foundry target."""

    def discover(self, target: FoundryRoiTarget) -> DiscoveryProof: ...

    def read(self, target: FoundryRoiTarget, window: Mapping[str, str]) -> ProviderRoiSnapshot: ...


class FoundryRoiDiscoveryVerifier(Protocol):
    """Independent attestation boundary. Production has no default verifier until an official API exists."""

    def verify(self, target: FoundryRoiTarget, proof: DiscoveryProof) -> VerifiedDiscoveryAttestation: ...


def discover_foundry_roi(
    provider: FoundryRoiProvider | None = None,
    verifier: FoundryRoiDiscoveryVerifier | None = None,
) -> FoundryRoiStatus:
    """Discover an injected provider without treating environment flags as proof."""

    target = _target_from_environment()
    if target is None:
        return _status("not_configured", "Canonical Foundry project endpoint and agent ID are not configured")
    return _discover_target(target, provider, verifier)[0]


def _discover_target(
    target: FoundryRoiTarget,
    provider: FoundryRoiProvider | None,
    verifier: FoundryRoiDiscoveryVerifier | None,
) -> tuple[FoundryRoiStatus, VerifiedDiscoveryAttestation | None]:
    if provider is None:
        return _status("not_configured", "No Foundry ROI provider is installed"), None
    try:
        proof = DiscoveryProof.model_validate(provider.discover(target))
    except Exception:
        return _status("unavailable", "Foundry ROI provider discovery failed"), None
    if not compare_digest(proof.target_fingerprint, target.target_fingerprint):
        return _status("unavailable", "Foundry ROI provider discovery target does not match configuration"), None
    if proof.state == "unavailable":
        return _status("unavailable", "Foundry ROI provider is unavailable", provider_version=proof.surface_version, observed_at=proof.observed_at), None
    if proof.state == "not_configured":
        return _status("not_configured", "Foundry ROI surface is not configured", provider_version=proof.surface_version, observed_at=proof.observed_at), None
    if verifier is None:
        return _status("configured_unverified", "Provider proof awaits independent Foundry ROI verification", provider_version=proof.surface_version, observed_at=proof.observed_at), None
    try:
        attestation = VerifiedDiscoveryAttestation.model_validate(verifier.verify(target, proof))
    except Exception:
        return _status("unavailable", "Independent Foundry ROI verification failed", provider_version=proof.surface_version), None
    if not _attestation_matches(target, proof, attestation):
        return _status("unavailable", "Independent Foundry ROI attestation does not match provider discovery", provider_version=proof.surface_version), None
    return (
        _status(
            "connected",
            "Independent verifier confirmed the configured target agent and ROI surface",
            provider_version=proof.surface_version,
            observed_at=attestation.observed_at,
            source="foundry_roi_verifier",
        ),
        attestation,
    )


def read_foundry_roi(
    window: Mapping[str, Any],
    provider: FoundryRoiProvider | None = None,
    verifier: FoundryRoiDiscoveryVerifier | None = None,
) -> FoundryRoiReadResult:
    """Read one sanitized provider snapshot, or expose a truthful unavailable state."""

    normalized_window = parse_time_window(window.get("from"), window.get("to"))
    target = _target_from_environment()
    if target is None:
        status = _status("not_configured", "Canonical Foundry project endpoint and agent ID are not configured")
        return FoundryRoiReadResult(status=status)
    status, attestation = _discover_target(target, provider, verifier)
    if status.state != "connected" or provider is None:
        return FoundryRoiReadResult(status=status)
    try:
        snapshot = ProviderRoiSnapshot.model_validate(provider.read(target, normalized_window))
    except (ValidationError, ValueError, TypeError):
        unavailable = _status("unavailable", "Foundry ROI provider returned an invalid snapshot", provider_version=status.provider_version)
        return FoundryRoiReadResult(status=unavailable)
    except Exception:
        unavailable = _status("unavailable", "Foundry ROI provider read failed", provider_version=status.provider_version)
        return FoundryRoiReadResult(status=unavailable)
    if not compare_digest(snapshot.target_fingerprint, target.target_fingerprint):
        unavailable = _status("unavailable", "Foundry ROI provider snapshot target does not match configuration", provider_version=status.provider_version)
        return FoundryRoiReadResult(status=unavailable)
    if snapshot.window != normalized_window:
        unavailable = _status("unavailable", "Foundry ROI provider snapshot window does not match request", provider_version=status.provider_version)
        return FoundryRoiReadResult(status=unavailable)
    assert attestation is not None
    try:
        verified_read = VerifiedProviderRead._issue(target=target, attestation=attestation, snapshot=snapshot)
    except ValidationError:
        unavailable = _status("unavailable", "Foundry ROI provider snapshot does not match independent attestation", provider_version=status.provider_version)
        return FoundryRoiReadResult(status=unavailable)
    return FoundryRoiReadResult(status=status, verified_read=verified_read)


def reconcile_roi(local: Mapping[str, Any], provider: VerifiedProviderRead) -> dict[str, Any]:
    """Public reconciliation boundary: only adapter-issued, independently attested reads are accepted."""

    if not isinstance(provider, VerifiedProviderRead) or not provider._issued_by_adapter:
        raise TypeError("reconcile_roi requires a VerifiedProviderRead issued by read_foundry_roi")
    return _reconcile_snapshot(local, provider.snapshot, provider.attestation)


def reconcile_foundry_read(local: Mapping[str, Any], provider: FoundryRoiReadResult) -> dict[str, Any]:
    if not isinstance(provider, FoundryRoiReadResult):
        raise TypeError("reconcile_foundry_read requires FoundryRoiReadResult")
    if provider.verified_read is None:
        return _unreconciled(local, provider.status)
    return reconcile_roi(local, provider.verified_read)


def _reconcile_snapshot(
    local: Mapping[str, Any],
    snapshot: ProviderRoiSnapshot,
    attestation: VerifiedDiscoveryAttestation,
) -> dict[str, Any]:
    """Private snapshot comparison reached only through an adapter-issued wrapper."""

    local_copy = _json_copy(local)
    metadata: dict[str, Any] = {
        "status": "not_reconciled",
        "reconciled": False,
        "reason": "Foundry ROI provider is not connected",
        "local_generated_at": local_copy.get("generated_at"),
        "provider_observed_at": snapshot.observed_at.isoformat(),
        "provider_source": attestation.observed_source,
        "provider_version": snapshot.provider_version,
        "provider_attested_at": attestation.observed_at.isoformat(),
    }
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
    elif set(snapshot.mapped_run_ids) != local_value["run_ids"]:
        metadata["reason"] = "mapped run lineage does not exactly match local ROI"
    elif set(snapshot.mapped_outcome_event_ids) != local_value["outcome_ids"]:
        metadata["reason"] = "mapped outcome lineage does not exactly match local ROI"
    else:
        metadata["status"] = "reconciled"
        metadata["reconciled"] = True
        metadata["reason"] = "mapped run and outcome lineage exactly match window, currency, and unit"
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


def _unreconciled(local: Mapping[str, Any], status: FoundryRoiStatus) -> dict[str, Any]:
    local_copy = _json_copy(local)
    return {
        "local": local_copy,
        "provider": None,
        "difference": None,
        "reconciliation": {
            "status": "not_reconciled",
            "reconciled": False,
            "reason": status.reason,
            "local_generated_at": local_copy.get("generated_at"),
            "provider_observed_at": None,
            "provider_source": status.source,
            "provider_version": status.provider_version,
            "provider_attested_at": None,
        },
    }


def _target_from_environment() -> FoundryRoiTarget | None:
    endpoint = str(os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or "").strip()
    agent_id = str(os.environ.get("FOUNDRY_AGENT_ID") or "").strip()
    if not endpoint or not agent_id:
        return None
    try:
        return FoundryRoiTarget(project_endpoint=endpoint, agent_id=agent_id)
    except ValidationError:
        return None


def _status(
    state: Literal["connected", "configured_unverified", "not_configured", "unavailable"],
    reason: str,
    *,
    provider_version: str | None = None,
    observed_at: datetime | None = None,
    source: Literal["foundry_roi_provider", "foundry_roi_verifier"] = "foundry_roi_provider",
) -> FoundryRoiStatus:
    return FoundryRoiStatus(
        state=state,
        configured=state == "connected",
        reason=reason,
        provider_version=provider_version,
        observed_at=observed_at or datetime.now(timezone.utc),
        source=source,
    )


def _attestation_matches(
    target: FoundryRoiTarget,
    proof: DiscoveryProof,
    attestation: VerifiedDiscoveryAttestation,
) -> bool:
    return bool(
        compare_digest(attestation.target_fingerprint, target.target_fingerprint)
        and compare_digest(attestation.target_fingerprint, proof.target_fingerprint)
        and attestation.surface_id == proof.surface_id
        and attestation.surface_version == proof.surface_version
        and attestation.observed_at == proof.observed_at
        and attestation.observed_source
    )


def _local_business_value(local: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if local.get("lineage_complete") is not True or bool(local.get("truncated")) or bool(local.get("invalid_run_ids")):
        return None, "local_lineage_incomplete"
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
    run_ids = _safe_lineage_set(local.get("observed_run_ids"))
    if not run_ids:
        return None, "local_lineage_incomplete"
    outcome_ids = _safe_lineage_set(local.get("outcome_event_ids"))
    if not outcome_ids:
        return None, "local_lineage_incomplete"
    return {
        "window": window,
        "amount": amount,
        "currency": currency,
        "unit": "currency",
        "run_ids": run_ids,
        "outcome_ids": outcome_ids,
    }, None


def _safe_lineage_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    identifiers = {str(item).strip() for item in value if str(item).strip()}
    if not identifiers or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", item) for item in identifiers):
        return set()
    return identifiers


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    import json

    return json.loads(json.dumps(dict(value), default=str))


__all__ = [
    "DiscoveryProof",
    "FoundryRoiDiscoveryVerifier",
    "FoundryRoiProvider",
    "FoundryRoiReadResult",
    "FoundryRoiStatus",
    "FoundryRoiTarget",
    "ProviderRoiSnapshot",
    "VerifiedDiscoveryAttestation",
    "VerifiedProviderRead",
    "discover_foundry_roi",
    "read_foundry_roi",
    "reconcile_foundry_read",
    "reconcile_roi",
]
