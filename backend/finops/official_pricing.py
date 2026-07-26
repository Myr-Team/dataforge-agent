from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import EstimatedCost, TokenUsage


_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "official_model_prices.json"
_OFFICIAL_HOSTS = {
    "azure.microsoft.com",
    "learn.microsoft.com",
    "prices.azure.com",
}


class OfficialPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    price_key: str = Field(min_length=3, max_length=240)
    provider: Literal["azure-openai", "openai"]
    official_model: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=200)
    deployment_type: str = Field(min_length=1, max_length=80)
    region_class: str = Field(min_length=1, max_length=80)
    currency: Literal["USD"]
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    cached_input_per_million: Decimal | None = Field(default=None, ge=0)
    reasoning_per_million: Decimal | None = Field(default=None, ge=0)
    source_url: str
    effective_at: str = Field(min_length=10, max_length=40)
    reviewed_at: str = Field(min_length=10, max_length=40)
    revision: str = Field(min_length=1, max_length=128)

    @field_validator("source_url")
    @classmethod
    def validate_official_source(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _OFFICIAL_HOSTS:
            raise ValueError("price source must be an official Microsoft HTTPS URL")
        return value


class OfficialPriceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: str = Field(min_length=1, max_length=128)
    entries: tuple[OfficialPrice, ...]

    @model_validator(mode="after")
    def validate_unique_keys(self) -> "OfficialPriceCatalog":
        keys = [entry.price_key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("official price keys must be unique")
        return self

    def get(self, price_key: str) -> OfficialPrice | None:
        key = str(price_key or "").strip()
        return next((entry for entry in self.entries if entry.price_key == key), None)

    def estimate(self, price_key: str, tokens: TokenUsage) -> EstimatedCost:
        price = self.get(price_key)
        if price is None:
            return EstimatedCost()
        if tokens.input is None or tokens.output is None:
            return EstimatedCost(
                status="partial",
                price_card_revision=price.revision,
            )
        cached = min(tokens.cached_input or 0, tokens.input)
        regular_input = tokens.input - cached
        amount = (
            Decimal(regular_input) * price.input_per_million
            + Decimal(tokens.output) * price.output_per_million
        ) / Decimal(1_000_000)
        if cached:
            cached_rate = price.cached_input_per_million
            if cached_rate is None:
                return EstimatedCost(
                    status="partial",
                    price_card_revision=price.revision,
                )
            amount += Decimal(cached) * cached_rate / Decimal(1_000_000)
        return EstimatedCost(
            amount=float(amount),
            currency=price.currency,
            status="estimated",
            price_card_revision=price.revision,
        )


@lru_cache(maxsize=1)
def load_official_price_catalog() -> OfficialPriceCatalog:
    payload = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return OfficialPriceCatalog.model_validate(payload)


# The official catalog only publishes text-model token prices. A deployment
# alias may only be mapped to such an entry when it is actually observed making
# model (LLM) calls; image, speech, and embedding deployments are incompatible.
_MODEL_CALL_CLASSES = frozenset({"model"})


def official_price_supports_call_classes(
    price: "OfficialPrice",
    call_classes: "set[str] | frozenset[str] | tuple[str, ...]",
) -> bool:
    """Return True when a deployment's observed usage is compatible with a price.

    An unobserved deployment (empty ``call_classes``) is allowed because the
    owner is pre-mapping the unpriced queue. A deployment that has been observed
    but never as a model call is incompatible with a text-model price entry.
    """
    observed = {str(value) for value in call_classes if value}
    if not observed:
        return True
    return bool(observed & _MODEL_CALL_CLASSES)


def estimate_official_cost(
    official_price_key: str,
    mapping_revision: int | None,
    tokens: TokenUsage,
) -> EstimatedCost:
    """Estimate a request cost from the official catalog for a mapped key.

    The returned estimate always records the official price key and mapping
    revision so historical requests remain tied to the revision that priced
    them. When the catalog cannot price the usage, the amount stays ``None``.
    """
    estimate = load_official_price_catalog().estimate(official_price_key, tokens)
    return estimate.model_copy(
        update={
            "official_price_key": official_price_key,
            "mapping_revision": mapping_revision,
        }
    )


__all__ = [
    "OfficialPrice",
    "OfficialPriceCatalog",
    "estimate_official_cost",
    "load_official_price_catalog",
    "official_price_supports_call_classes",
]
