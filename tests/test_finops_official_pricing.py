from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.finops.models import TokenUsage
from backend.finops.official_pricing import (
    AttemptPriceInput,
    OfficialPrice,
    OfficialPriceCatalog,
    estimate_attempt_costs,
    load_official_price_catalog,
    official_price_supports_call_classes,
)


def _price(**overrides: object) -> OfficialPrice:
    payload = {
        "price_key": "azure-openai:gpt-5.1:global-standard:global",
        "provider": "azure-openai",
        "official_model": "gpt-5.1",
        "display_name": "GPT-5.1 Global Standard",
        "deployment_type": "GlobalStandard",
        "region_class": "global",
        "currency": "USD",
        "input_per_million": "1.25",
        "output_per_million": "10.00",
        "cached_input_per_million": "0.125",
        "source_url": "https://prices.azure.com/api/retail/prices",
        "effective_at": "2025-11-01T00:00:00Z",
        "reviewed_at": "2026-07-26T09:00:00Z",
        "revision": "azure-retail-2026-07-26",
    }
    payload.update(overrides)
    return OfficialPrice.model_validate(payload)


def test_official_price_requires_usd_and_official_https_source() -> None:
    with pytest.raises(ValidationError):
        _price(currency="CNY")
    with pytest.raises(ValidationError):
        _price(source_url="https://example.com/pricing")
    with pytest.raises(ValidationError):
        _price(source_url="http://prices.azure.com/api/retail/prices")


def test_catalog_looks_up_only_official_price_keys() -> None:
    catalog = OfficialPriceCatalog(revision="catalog-1", entries=(_price(),))

    assert catalog.get("azure-openai:gpt-5.1:global-standard:global") is not None
    assert catalog.get("gpt-5.6-terra") is None


def test_catalog_estimates_known_token_categories() -> None:
    catalog = OfficialPriceCatalog(revision="catalog-1", entries=(_price(),))

    estimate = catalog.estimate(
        "azure-openai:gpt-5.1:global-standard:global",
        TokenUsage(input=1_000_000, output=500_000, cached_input=200_000, total=1_500_000),
    )

    assert estimate.status == "estimated"
    assert Decimal(str(estimate.amount)) == Decimal("6.025")
    assert estimate.price_card_revision == "azure-retail-2026-07-26"


def test_unknown_price_key_is_unpriced_not_zero() -> None:
    catalog = OfficialPriceCatalog(revision="catalog-1", entries=(_price(),))

    estimate = catalog.estimate("gpt-5.6-terra", TokenUsage(input=100, output=10))

    assert estimate.status == "unavailable"
    assert estimate.amount is None


def test_incomplete_token_evidence_is_partial() -> None:
    catalog = OfficialPriceCatalog(revision="catalog-1", entries=(_price(),))

    estimate = catalog.estimate(
        "azure-openai:gpt-5.1:global-standard:global",
        TokenUsage(total=100),
    )

    assert estimate.status == "partial"
    assert estimate.amount is None


def test_official_price_compatibility_is_based_on_observed_modality() -> None:
    price = _price()

    # A deployment with no observed usage cannot be disproven; allow mapping.
    assert official_price_supports_call_classes(price, set()) is True
    # A deployment observed making model (LLM) calls is compatible.
    assert official_price_supports_call_classes(price, {"model"}) is True
    assert official_price_supports_call_classes(price, {"model", "tool"}) is True
    # A deployment observed only as image/speech/embedding is incompatible with
    # a text-model token price entry.
    assert official_price_supports_call_classes(price, {"image"}) is False
    assert official_price_supports_call_classes(price, {"speech", "embedding"}) is False


def test_bundled_catalog_contains_verified_gpt_5_1_global_standard() -> None:
    catalog = load_official_price_catalog()
    entry = catalog.get("azure-openai:gpt-5.1:global-standard:global")

    assert entry is not None
    assert entry.input_per_million == Decimal("1.25")
    assert entry.output_per_million == Decimal("10.0")
    assert entry.cached_input_per_million == Decimal("0.125")
    assert str(entry.source_url).startswith("https://prices.azure.com/")


def test_bundled_catalog_contains_verified_gpt_5_6_global_standard_tiers() -> None:
    catalog = load_official_price_catalog()

    expected = {
        "azure-openai:gpt-5.6-sol:global-standard:global": ("5.0", "30.0", "0.5"),
        "azure-openai:gpt-5.6-terra:global-standard:global": ("2.5", "15.0", "0.25"),
        "azure-openai:gpt-5.6-luna:global-standard:global": ("1.0", "6.0", "0.1"),
    }
    for price_key, prices in expected.items():
        entry = catalog.get(price_key)
        assert entry is not None
        assert entry.input_per_million == Decimal(prices[0])
        assert entry.output_per_million == Decimal(prices[1])
        assert entry.cached_input_per_million == Decimal(prices[2])
        assert str(entry.source_url).startswith("https://prices.azure.com/")


def test_bundled_catalog_contains_verified_deepseek_v4_prices() -> None:
    catalog = load_official_price_catalog()
    expected = {
        "deepseek:deepseek-v4-flash:official": (
            "0.14",
            "0.0028",
            "0.28",
        ),
        "deepseek:deepseek-v4-pro:official": (
            "0.435",
            "0.003625",
            "0.87",
        ),
    }

    for price_key, prices in expected.items():
        entry = catalog.get(price_key)
        assert entry is not None
        assert entry.provider == "deepseek"
        assert entry.input_per_million == Decimal(prices[0])
        assert entry.cached_input_per_million == Decimal(prices[1])
        assert entry.output_per_million == Decimal(prices[2])
        assert (
            entry.source_url
            == "https://api-docs.deepseek.com/quick_start/pricing/"
        )
        assert entry.revision == "deepseek-2026-07-28-v1"


def test_deepseek_cost_uses_cache_hit_and_miss_input_populations() -> None:
    estimate = load_official_price_catalog().estimate(
        "deepseek:deepseek-v4-pro:official",
        TokenUsage(
            input=1_000_000,
            cached_input=800_000,
            output=100_000,
            reasoning=40_000,
            total=1_140_000,
        ),
    )

    # 200k miss * .435 + 800k hit * .003625 + 100k output * .87.
    # Reasoning is already part of provider completion usage and is not added.
    assert Decimal(str(estimate.amount)) == Decimal("0.1769")


def test_fallback_attempt_costs_are_priced_separately_and_summed() -> None:
    result = estimate_attempt_costs(
        [
            AttemptPriceInput(
                attempt=1,
                official_price_key="deepseek:deepseek-v4-flash:official",
                mapping_revision=2,
                tokens=TokenUsage(
                    input=1000,
                    cached_input=0,
                    output=0,
                    total=1000,
                ),
            ),
            AttemptPriceInput(
                attempt=2,
                official_price_key="azure-openai:gpt-5.1:global-standard:global",
                mapping_revision=4,
                tokens=TokenUsage(input=1000, output=100, total=1100),
            ),
        ]
    )

    assert result["status"] == "estimated"
    assert result["priced_attempts"] == 2
    assert result["unpriced_attempts"] == 0
    assert Decimal(str(result["amount"])) == Decimal("0.00239")
    assert [item["attempt"] for item in result["attempts"]] == [1, 2]


def test_fallback_cost_keeps_unsupported_attempt_unpriced() -> None:
    result = estimate_attempt_costs(
        [
            AttemptPriceInput(
                attempt=1,
                official_price_key="deepseek:unsupported:official",
                tokens=TokenUsage(input=100, output=10, total=110),
            )
        ]
    )

    assert result["status"] == "unavailable"
    assert result["amount"] is None
    assert result["unpriced_attempts"] == 1
