from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


EXECUTION_KINDS = (
    "direct_reply",
    "follow_up",
    "full_analysis",
    "audit_repair",
)
EXECUTION_KIND_CAPABILITIES = {
    "direct_reply": "chat",
    "follow_up": "chat",
    "full_analysis": "analysis",
    "audit_repair": "analysis",
}
AGENT_IDS = (
    "df-coordinator",
    "df-corpus-analyst",
    "df-market-researcher",
    "df-feasibility-analyst",
    "df-auditor",
    "df-producer",
)

_ROUTE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_ISO_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def validate_workspace_routing_policy(
    raw: Mapping[str, Any] | None,
    routes: Iterable[Any],
    *,
    revision: int | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Validate route IDs against the runtime allowlist without exposing deployments."""
    payload = dict(raw or {})
    source_assignments = payload.get("assignments")
    if source_assignments is None:
        source_assignments = {}
    if not isinstance(source_assignments, Mapping):
        raise ValueError("Model routing assignments must be an object")
    unknown_kinds = set(source_assignments) - set(EXECUTION_KINDS)
    if unknown_kinds:
        raise ValueError("Model routing execution kind is invalid")

    route_map = _routes_by_id(routes)
    default_route_id = _route_id(
        payload.get("default_route_id"),
        required=False,
    )
    if default_route_id:
        _require_capability(route_map, default_route_id, "chat")
    raw_agent_assignments = payload.get("agent_assignments") or {}
    if not isinstance(raw_agent_assignments, Mapping):
        raise ValueError("Model routing Agent assignments must be an object")
    unknown_agents = set(raw_agent_assignments) - set(AGENT_IDS)
    if unknown_agents:
        raise ValueError("Model routing Agent is invalid")
    agent_assignments: dict[str, dict[str, str | None]] = {}
    for agent_id in AGENT_IDS:
        source = raw_agent_assignments.get(agent_id)
        if source is None:
            continue
        if not isinstance(source, Mapping):
            raise ValueError("Model routing Agent assignment must be an object")
        primary = _route_id(source.get("primary_route_id"), required=True)
        fallback = _route_id(source.get("fallback_route_id"), required=False)
        _require_capability(route_map, primary, "analysis")
        if fallback:
            _require_capability(route_map, fallback, "analysis")
        agent_assignments[agent_id] = {
            "primary_route_id": primary,
            "fallback_route_id": fallback,
        }
    assignments: dict[str, dict[str, str | None]] = {}
    for kind in EXECUTION_KINDS:
        source = source_assignments.get(kind)
        if source is None:
            continue
        if not isinstance(source, Mapping):
            raise ValueError("Model routing assignment must be an object")
        primary = _route_id(source.get("primary_route_id"), required=True)
        fallback = _route_id(source.get("fallback_route_id"), required=False)
        _require_capability(route_map, primary, EXECUTION_KIND_CAPABILITIES[kind])
        if fallback:
            _require_capability(route_map, fallback, EXECUTION_KIND_CAPABILITIES[kind])
        assignments[kind] = {
            "primary_route_id": primary,
            "fallback_route_id": fallback,
        }

    result: dict[str, Any] = {
        "assignments": assignments,
        "agent_assignments": agent_assignments,
    }
    if default_route_id:
        result["default_route_id"] = default_route_id
    if revision is not None:
        result["revision"] = _revision(revision)
    if updated_at is not None:
        result["updated_at"] = _updated_at(updated_at)
    return result


def normalize_workspace_price_card(
    raw: Mapping[str, Any] | None,
    routes: Iterable[Any],
    *,
    revision: int,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Normalize an Owner-maintained pricing card; empty entries are valid."""
    payload = dict(raw or {})
    currency = str(payload.get("currency") or "USD").strip().upper()
    if not _CURRENCY.fullmatch(currency):
        raise ValueError("Price card currency is invalid")
    items = payload.get("entries")
    if items is None:
        items = []
    if not isinstance(items, list) or len(items) > 64:
        raise ValueError("Price card entries are invalid")
    route_map = _routes_by_id(routes)
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Price card entry must be an object")
        route_id = _route_id(item.get("route_id"), required=True)
        if route_id not in route_map or route_id in seen:
            raise ValueError("Price card route is invalid or duplicated")
        seen.add(route_id)
        source_label = _source_label(item.get("source_label"))
        entries.append(
            {
                "route_id": route_id,
                "input_per_million": _nonnegative_money(item.get("input_per_million")),
                "output_per_million": _nonnegative_money(item.get("output_per_million")),
                "source_label": source_label,
                "updated_at": _updated_at(updated_at or item.get("updated_at") or _utc_now()),
            }
        )
    return {
        "revision": _revision(revision),
        "currency": currency,
        "entries": entries,
        "updated_at": _updated_at(updated_at or _utc_now()),
    }


def estimate_model_cost(
    usage: Mapping[str, Any] | None,
    selected_route: Mapping[str, Any] | Any | None,
    price_card: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Calculate a labelled estimate only when usage and the pinned card are complete."""
    input_tokens = _nonnegative_int((usage or {}).get("input_tokens"))
    output_tokens = _nonnegative_int((usage or {}).get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return {"status": "unavailable", "reason": "usage_not_recorded"}
    route_id = _selected_route_id(selected_route)
    card = dict(price_card or {})
    entry = next(
        (
            item
            for item in (card.get("entries") or [])
            if isinstance(item, Mapping) and str(item.get("route_id") or "") == route_id
        ),
        None,
    )
    if entry is None:
        return {"status": "unavailable", "reason": "price_not_configured"}
    input_rate = _nonnegative_money(entry.get("input_per_million"))
    output_rate = _nonnegative_money(entry.get("output_per_million"))
    revision = _revision(card.get("revision"))
    currency = str(card.get("currency") or "").strip().upper()
    if not route_id or not _CURRENCY.fullmatch(currency):
        return {"status": "unavailable", "reason": "price_not_configured"}
    amount = round(
        input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate,
        6,
    )
    return {
        "status": "estimated",
        "currency": currency,
        "amount": amount,
        "price_card_revision": revision,
        "route_id": route_id,
        "formula": "input_tokens/1_000_000*input_per_million + output_tokens/1_000_000*output_per_million",
    }


def public_workspace_model_config(meta: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    source = dict(meta or {})
    policy = source.get("model_routing_policy")
    price_card = source.get("model_price_card")
    return (
        dict(policy) if isinstance(policy, Mapping) else {"revision": 0, "assignments": {}},
        dict(price_card) if isinstance(price_card, Mapping) else {"revision": 0, "currency": "USD", "entries": []},
    )


def _routes_by_id(routes: Iterable[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for route in routes:
        route_id = str(getattr(route, "route_id", "") or "").strip().lower()
        if route_id and route_id not in result:
            result[route_id] = route
    return result


def _route_id(value: Any, *, required: bool) -> str | None:
    route_id = str(value or "").strip().lower()
    if not route_id and not required:
        return None
    if not _ROUTE_ID.fullmatch(route_id):
        raise ValueError("Model routing route id is invalid")
    return route_id


def _require_capability(route_map: Mapping[str, Any], route_id: str | None, capability: str) -> None:
    route = route_map.get(str(route_id or ""))
    capabilities = getattr(route, "capabilities", frozenset()) if route is not None else frozenset()
    if route is None:
        raise ValueError("Model routing route is not allowlisted")
    if str(capability) not in capabilities:
        raise ValueError("Model routing route capability is invalid")


def _selected_route_id(selected_route: Mapping[str, Any] | Any | None) -> str:
    if isinstance(selected_route, Mapping):
        value = selected_route.get("route_id") or selected_route.get("route")
    else:
        value = getattr(selected_route, "route_id", None)
        if value is None:
            route = getattr(selected_route, "route", None)
            value = getattr(route, "route_id", None)
    route_id = str(value or "").strip().lower()
    return route_id if _ROUTE_ID.fullmatch(route_id) else ""


def _nonnegative_int(value: Any) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return None
    return int(value)


def _nonnegative_money(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError("Price card amount must be a finite non-negative number")
    return round(float(value), 9)


def _revision(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Model configuration revision is invalid")
    return value


def _source_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 160 or "\n" in text or "\r" in text:
        raise ValueError("Price card source label is invalid")
    return text


def _updated_at(value: Any) -> str:
    text = str(value or "").strip()
    if not _ISO_INSTANT.fullmatch(text):
        raise ValueError("Model configuration timestamp is invalid")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Model configuration timestamp is invalid") from exc
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "EXECUTION_KINDS",
    "EXECUTION_KIND_CAPABILITIES",
    "AGENT_IDS",
    "estimate_model_cost",
    "normalize_workspace_price_card",
    "public_workspace_model_config",
    "validate_workspace_routing_policy",
]
