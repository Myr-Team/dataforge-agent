from __future__ import annotations

from datetime import datetime, timezone

from backend.model_provider_routes import provider_route_candidates
from backend.model_providers import ModelProviderRecord, ProviderModel


def _provider(
    *,
    tenant_ref: str = "tenant-a",
    provider_id: str = "provider_primary",
    connection_state: str = "connected",
    governance_state: str = "governed",
    price_key: str | None = "deepseek:deepseek-v4-flash:official",
) -> ModelProviderRecord:
    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    return ModelProviderRecord(
        provider_id=provider_id,
        tenant_ref=tenant_ref,
        provider_type="deepseek",
        display_name="DeepSeek 原厂",
        base_url="https://api.deepseek.com",
        secret_ref=f"kv:{provider_id}",
        connection_state=connection_state,
        governance_state=governance_state,
        available_models=[
            ProviderModel(
                model_id="deepseek-v4-flash",
                display_name="DeepSeek V4 Flash",
                capabilities=["chat", "analysis", "tools"],
                support_state="supported",
                price_key=price_key,
            )
        ],
        last_tested_at=now,
        last_success_at=now,
        revision=3,
        created_by_ref="actor-a",
        updated_by_ref="actor-a",
        created_at=now,
        updated_at=now,
    )


def test_provider_routes_are_deterministic_safe_and_selectable() -> None:
    first = provider_route_candidates([_provider()], secret_status=lambda _item: "stored")
    second = provider_route_candidates([_provider()], secret_status=lambda _item: "stored")

    assert len(first) == 1
    assert first[0].route.route_id == second[0].route.route_id
    assert first[0].route.route_id.startswith("ds_")
    assert first[0].public == {
        "id": first[0].route.route_id,
        "deployment": "deepseek-v4-flash",
        "model_id": "deepseek-v4-flash",
        "provider_id": "provider_primary",
        "provider_type": "deepseek",
        "provider_label": "DeepSeek 原厂",
        "label": "DeepSeek V4 Flash",
        "capabilities": ["analysis", "chat", "tools"],
        "official_price_key": "deepseek:deepseek-v4-flash:official",
        "pricing_state": "priced",
        "health_state": "connected",
        "governance_state": "governed",
        "selectable": True,
        "unavailable_reason": None,
    }


def test_provider_routes_keep_pending_or_unpriced_models_visible_but_disabled() -> None:
    pending = provider_route_candidates(
        [_provider(governance_state="pending")],
        secret_status=lambda _item: "stored",
    )[0]
    unpriced = provider_route_candidates(
        [_provider(provider_id="provider_unpriced", price_key=None)],
        secret_status=lambda _item: "stored",
    )[0]

    assert pending.public["selectable"] is False
    assert pending.public["unavailable_reason"] == "governance_required"
    assert unpriced.public["selectable"] is False
    assert unpriced.public["pricing_state"] == "unpriced"
    assert unpriced.public["unavailable_reason"] == "official_pricing_required"

