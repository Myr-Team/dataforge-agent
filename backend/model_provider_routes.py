from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .model_policy import ModelRoute
from .model_providers import ModelProviderRecord, provider_route_eligibility


@dataclass(frozen=True)
class ProviderRouteCandidate:
    route: ModelRoute
    public: dict[str, Any]


def provider_route_candidates(
    providers: Iterable[ModelProviderRecord],
    *,
    secret_status: Callable[[ModelProviderRecord], str],
) -> list[ProviderRouteCandidate]:
    candidates: list[ProviderRouteCandidate] = []
    used_route_ids: set[str] = set()
    for provider in sorted(
        providers,
        key=lambda item: (item.provider_type, item.provider_id),
    ):
        if provider.provider_type != "deepseek":
            continue
        status = str(secret_status(provider) or "unavailable")
        eligibility = provider_route_eligibility(
            provider,
            secret_status=status,  # type: ignore[arg-type]
        )
        for model in sorted(provider.available_models, key=lambda item: item.model_id):
            if model.support_state not in {"supported", "unpriced"}:
                continue
            route_id = _route_id(provider.provider_id, model.model_id)
            if route_id in used_route_ids:
                continue
            used_route_ids.add(route_id)
            route = ModelRoute(
                route_id=route_id,
                deployment=model.model_id,
                label=model.display_name,
                capabilities=frozenset(model.capabilities),
                provider_id=provider.provider_id,
                provider_type=provider.provider_type,
                model_id=model.model_id,
            )
            priced = bool(model.price_key)
            selectable = bool(eligibility["selectable"] and priced)
            unavailable_reason = eligibility.get("reason")
            if not priced:
                unavailable_reason = "official_pricing_required"
            candidates.append(
                ProviderRouteCandidate(
                    route=route,
                    public={
                        "id": route.route_id,
                        "deployment": route.deployment,
                        "model_id": route.model_id,
                        "provider_id": route.provider_id,
                        "provider_type": route.provider_type,
                        "provider_label": provider.display_name,
                        "label": route.label,
                        "capabilities": sorted(route.capabilities),
                        "official_price_key": model.price_key,
                        "pricing_state": "priced" if priced else "unpriced",
                        "health_state": provider.connection_state,
                        "governance_state": provider.governance_state,
                        "selectable": selectable,
                        "unavailable_reason": unavailable_reason,
                    },
                )
            )
    return candidates


def _route_id(provider_id: str, model_id: str) -> str:
    digest = hashlib.sha256(
        f"{provider_id}\0{model_id}".encode("utf-8")
    ).hexdigest()[:12]
    suffix = re.sub(r"[^a-z0-9]+", "_", model_id.lower()).strip("_")
    return f"ds_{digest}_{suffix[:40]}"


__all__ = ["ProviderRouteCandidate", "provider_route_candidates"]
