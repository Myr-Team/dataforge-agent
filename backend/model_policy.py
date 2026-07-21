from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass


_ROUTE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DEPLOYMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ModelPolicyError(ValueError):
    """Raised when server-owned model routing configuration is invalid."""


@dataclass(frozen=True)
class ModelRoute:
    route_id: str
    deployment: str
    label: str
    capabilities: frozenset[str]


def list_allowed_model_routes() -> list[ModelRoute]:
    raw = str(os.environ.get("DF_MODEL_ROUTE_ALLOWLIST") or "").strip()
    if not raw:
        deployment = str(os.environ.get("DF_CHAT_DEPLOYMENT") or "gpt-5.1").strip()
        if not _DEPLOYMENT.fullmatch(deployment):
            raise ModelPolicyError("DF_CHAT_DEPLOYMENT is invalid")
        return [ModelRoute("default", deployment, deployment, frozenset({"chat", "analysis", "research"}))]
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelPolicyError("DF_MODEL_ROUTE_ALLOWLIST must be valid JSON") from exc
    if not isinstance(items, list) or not items:
        raise ModelPolicyError("DF_MODEL_ROUTE_ALLOWLIST must contain at least one route")
    routes: list[ModelRoute] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ModelPolicyError("Model route entries must be objects")
        route_id = str(item.get("id") or "").strip().lower()
        deployment = str(item.get("deployment") or "").strip()
        label = str(item.get("label") or deployment).strip()
        capabilities = frozenset(str(value).strip().lower() for value in item.get("capabilities", []) if str(value).strip())
        if not _ROUTE_ID.fullmatch(route_id) or route_id in seen:
            raise ModelPolicyError("Model route id is invalid or duplicated")
        if not _DEPLOYMENT.fullmatch(deployment):
            raise ModelPolicyError("Model route deployment is invalid")
        if not label or len(label) > 100 or not capabilities:
            raise ModelPolicyError("Model route label or capabilities are invalid")
        seen.add(route_id)
        routes.append(ModelRoute(route_id, deployment, label, capabilities))
    return routes


def resolve_text_route(*, capability: str = "chat") -> ModelRoute:
    required = str(capability or "chat").strip().lower()
    routes = [route for route in list_allowed_model_routes() if required in route.capabilities]
    if not routes:
        raise ModelPolicyError(f"No allowlisted route supports {required}")
    configured = str(os.environ.get("DF_DEFAULT_MODEL_ROUTE") or "").strip().lower()
    if not configured:
        return routes[0]
    for route in routes:
        if route.route_id == configured:
            return route
    raise ModelPolicyError("DF_DEFAULT_MODEL_ROUTE is not an allowlisted route")


def resolve_text_deployment(*, capability: str = "chat") -> str:
    return resolve_text_route(capability=capability).deployment


def public_model_route_snapshot() -> dict[str, object]:
    """Return the server-owned model routes safe to expose in owner monitoring."""
    try:
        routes = list_allowed_model_routes()
        default_route = resolve_text_route(capability="chat")
    except ModelPolicyError as exc:
        return {"state": "misconfigured", "default_route": None, "routes": [], "reason": str(exc)}
    return {
        "state": "available",
        "default_route": default_route.route_id,
        "routes": [
            {
                "id": route.route_id,
                "deployment": route.deployment,
                "label": route.label,
                "capabilities": sorted(route.capabilities),
            }
            for route in routes
        ],
    }
