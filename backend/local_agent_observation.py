from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_references import safe_configured_provider_ref
from .provider_usage import ProviderCacheEvidence
from .token_integrity import finite_nonnegative_integral_token_count


_SCHEMA_VERSION = "dataforge.local-model-observation.v1"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_PROVIDER_TYPES = frozenset({"azure_foundry", "deepseek"})
_ROUTE_EVIDENCE = frozenset({"observed", "selected", "inferred", "unavailable"})


class LocalTokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> "LocalTokenUsage":
        if self.total_tokens is None:
            return self
        known_total = sum(
            value
            for value in (self.input_tokens, self.output_tokens)
            if value is not None
        )
        if known_total and self.total_tokens < known_total:
            raise ValueError("total tokens cannot be lower than input plus output")
        return self


class LocalCostEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    status: Literal["estimated", "partial", "unavailable"] = "unavailable"
    price_card_revision: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_evidence(self) -> "LocalCostEvidence":
        if self.status == "estimated" and (self.amount is None or self.currency is None):
            raise ValueError("estimated cost requires amount and currency")
        if self.status == "unavailable" and self.amount is not None:
            raise ValueError("unavailable cost cannot include an amount")
        return self


class LocalModelObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["dataforge.local-model-observation.v1"] = _SCHEMA_VERSION
    run_ref: str = Field(pattern=r"^run_[0-9a-f]{24}$")
    request_ref: str = Field(pattern=r"^request_[0-9a-f]{24}$")
    workspace_ref: str = Field(pattern=r"^workspace_[0-9a-f]{24}$")
    agent: str | None = Field(default=None, max_length=160)
    capability: str | None = Field(default=None, max_length=80)
    provider_type: Literal["azure_foundry", "deepseek"] | None = None
    provider_id: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, max_length=160)
    route: str | None = Field(default=None, max_length=160)
    deployment: str | None = Field(default=None, max_length=160)
    route_evidence: Literal["observed", "selected", "inferred", "unavailable"] = "unavailable"
    usage: LocalTokenUsage = Field(default_factory=LocalTokenUsage)
    provider_cache: ProviderCacheEvidence = Field(
        default_factory=lambda: ProviderCacheEvidence(
            state="unavailable",
            hit_tokens=None,
            miss_tokens=None,
            hit_rate_pct=None,
            evidence_state="unavailable",
        )
    )
    latency_ms: int | None = Field(default=None, ge=0)
    status: Literal["completed", "failed", "cancelled", "unknown"] = "unknown"
    cost: LocalCostEvidence = Field(default_factory=LocalCostEvidence)


def build_local_model_observation(
    run: dict[str, Any],
    *,
    model_index: int = 0,
) -> LocalModelObservation:
    """Build a privacy-bounded local observation from one persisted run model."""

    if not isinstance(run, dict):
        raise TypeError("run must be an object")
    models = run.get("models")
    if not isinstance(models, list) or model_index < 0 or model_index >= len(models):
        raise ValueError("model_index does not identify a persisted model record")
    model = models[model_index]
    if not isinstance(model, dict):
        raise ValueError("persisted model record must be an object")

    run_id = str(run.get("run_id") or "").strip()
    workspace_id = str(run.get("workspace_id") or "").strip()
    if not run_id or not workspace_id:
        raise ValueError("run_id and workspace_id are required")
    request_source = str(model.get("response_id") or f"{run_id}:{model_index}").strip()

    usage = model.get("usage") if isinstance(model.get("usage"), dict) else {}
    provider_cache = _provider_cache(model.get("provider_cache"))
    cost = _cost_evidence(model.get("cost_estimate"))

    return LocalModelObservation(
        run_ref=_opaque_ref("run", run_id),
        request_ref=_opaque_ref("request", request_source),
        workspace_ref=_opaque_ref("workspace", workspace_id),
        agent=_safe_identifier(model.get("agent")),
        capability=_safe_identifier(model.get("execution_kind"), max_length=80),
        provider_type=_provider_type(model.get("provider_type")),
        provider_id=_provider_id(model.get("provider_id")),
        model_id=_safe_identifier(model.get("model_id")),
        route=_safe_identifier(model.get("route") or model.get("model_route")),
        deployment=_safe_identifier(
            model.get("deployment") or model.get("model_deployment") or model.get("model")
        ),
        route_evidence=_route_evidence(model.get("route_evidence")),
        usage=LocalTokenUsage(
            input_tokens=_nonnegative_int(usage.get("prompt")),
            output_tokens=_nonnegative_int(usage.get("completion")),
            reasoning_tokens=_nonnegative_int(usage.get("reasoning")),
            cached_input_tokens=_nonnegative_int(usage.get("cached_input")),
            total_tokens=_nonnegative_int(usage.get("total")),
        ),
        provider_cache=provider_cache,
        latency_ms=_nonnegative_int(model.get("latency_ms")),
        status=_run_status(run.get("status")),
        cost=cost,
    )


def _opaque_ref(kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{kind}:{value}".encode("utf-8")).hexdigest()[:24]
    return f"{kind}_{digest}"


def _safe_identifier(value: Any, *, max_length: int = 160) -> str | None:
    text = str(value or "").strip()
    if not text or len(text) > max_length or not _SAFE_IDENTIFIER.fullmatch(text):
        return None
    return text


def _provider_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in _PROVIDER_TYPES else None


def _provider_id(value: Any) -> str | None:
    return safe_configured_provider_ref(value)


def _route_evidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _ROUTE_EVIDENCE else "unavailable"


def _nonnegative_int(value: Any) -> int | None:
    return finite_nonnegative_integral_token_count(value)


def _provider_cache(value: Any) -> ProviderCacheEvidence:
    data = value if isinstance(value, dict) else {}
    hit = _nonnegative_int(data.get("hit_tokens"))
    miss = _nonnegative_int(data.get("miss_tokens"))
    if hit is None or miss is None:
        return ProviderCacheEvidence(
            state="unavailable",
            hit_tokens=hit,
            miss_tokens=miss,
            hit_rate_pct=None,
            evidence_state="partial" if hit is not None or miss is not None else "unavailable",
        )
    denominator = hit + miss
    return ProviderCacheEvidence(
        state="partial_hit" if hit and miss else "hit" if hit else "miss",
        hit_tokens=hit,
        miss_tokens=miss,
        hit_rate_pct=round(hit / denominator * 100, 2) if denominator else None,
        evidence_state="observed",
    )


def _cost_evidence(value: Any) -> LocalCostEvidence:
    data = value if isinstance(value, dict) else {}
    status = str(data.get("status") or "unavailable").strip().lower()
    if status not in {"estimated", "partial", "unavailable"}:
        status = "unavailable"
    amount = data.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
        amount = None
    currency = str(data.get("currency") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        currency = None
    revision = data.get("price_card_revision")
    revision_text = str(revision).strip() if revision is not None else None
    if revision_text and len(revision_text) > 128:
        revision_text = None
    if status == "estimated" and (amount is None or currency is None):
        status = "unavailable"
        amount = None
    if status == "unavailable":
        amount = None
    return LocalCostEvidence(
        amount=float(amount) if amount is not None else None,
        currency=currency,
        status=status,
        price_card_revision=revision_text,
    )


def _run_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"completed", "failed", "cancelled"}:
        return text
    if text in {"succeeded", "success"}:
        return "completed"
    return "unknown"


__all__ = [
    "LocalCostEvidence",
    "LocalModelObservation",
    "LocalTokenUsage",
    "build_local_model_observation",
]
