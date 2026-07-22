from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re
from dataclasses import dataclass
from typing import Iterator


_ROUTE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DEPLOYMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXECUTION_KIND_CAPABILITY = {
    "full_analysis": "analysis",
    "audit_repair": "analysis",
    "follow_up": "followup",
    "direct_reply": "chat",
}
_SAFE_FALLBACK_REASONS = frozenset(
    {
        "candidate_not_eligible",
        "capability_missing",
        "provider_usage_missing",
        "provider_error",
    }
)


class ModelPolicyError(ValueError):
    """Raised when server-owned model routing configuration is invalid."""


@dataclass(frozen=True)
class ModelRoute:
    route_id: str
    deployment: str
    label: str
    capabilities: frozenset[str]


@dataclass(frozen=True)
class SelectedTextRoute:
    route: ModelRoute
    execution_kind: str
    selection: str = "policy"
    fallback_reason: str | None = None


_ROUTE_SCOPE: ContextVar[SelectedTextRoute | None] = ContextVar("dataforge_model_route_scope", default=None)


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


def _routes_for_capability(capability: str) -> list[ModelRoute]:
    required = str(capability or "chat").strip().lower()
    return [route for route in list_allowed_model_routes() if required in route.capabilities]


def _pick_route(
    *,
    capability: str,
    prefer_non_analysis_for_chat: bool = False,
) -> ModelRoute:
    routes = _routes_for_capability(capability)
    if not routes:
        raise ModelPolicyError(f"No allowlisted route supports {capability}")
    configured = str(os.environ.get("DF_DEFAULT_MODEL_ROUTE") or "").strip().lower()
    if prefer_non_analysis_for_chat:
        lean_routes = [route for route in routes if "analysis" not in route.capabilities]
        if lean_routes:
            if configured:
                for route in lean_routes:
                    if route.route_id == configured:
                        return route
            return lean_routes[0]
    if configured:
        for route in routes:
            if route.route_id == configured:
                return route
    return routes[0]


def select_text_route_record(
    execution_kind: str,
    *,
    candidate_enabled: bool = False,
) -> SelectedTextRoute:
    normalized_kind = str(execution_kind or "direct_reply").strip().lower()
    desired = _EXECUTION_KIND_CAPABILITY.get(normalized_kind, "chat")
    fallback_reason: str | None = None
    capability = desired
    prefer_non_analysis_for_chat = normalized_kind in {"follow_up", "direct_reply"}
    if desired == "followup" and not candidate_enabled:
        capability = "chat"
        fallback_reason = "candidate_not_eligible"
    try:
        route = _pick_route(
            capability=capability,
            prefer_non_analysis_for_chat=prefer_non_analysis_for_chat and capability == "chat",
        )
    except ModelPolicyError:
        fallback_capability = "analysis" if normalized_kind in {"full_analysis", "audit_repair"} else "chat"
        route = _pick_route(
            capability=fallback_capability,
            prefer_non_analysis_for_chat=prefer_non_analysis_for_chat and fallback_capability == "chat",
        )
        if fallback_reason is None:
            fallback_reason = "capability_missing"
    return SelectedTextRoute(
        route=route,
        execution_kind=normalized_kind,
        selection="policy",
        fallback_reason=fallback_reason,
    )


def select_text_route(execution_kind: str, *, candidate_enabled: bool = False) -> ModelRoute:
    return select_text_route_record(execution_kind, candidate_enabled=candidate_enabled).route


def current_text_route() -> SelectedTextRoute:
    scoped = _ROUTE_SCOPE.get()
    if scoped is not None:
        return scoped
    return SelectedTextRoute(
        route=_pick_route(capability="chat", prefer_non_analysis_for_chat=True),
        execution_kind="direct_reply",
    )


def safe_fallback_reason(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return normalized if normalized in _SAFE_FALLBACK_REASONS else "provider_error"


@contextmanager
def model_route_scope(
    *,
    route: ModelRoute | SelectedTextRoute,
    execution_kind: str | None = None,
    selection: str = "policy",
    fallback_reason: str | None = None,
) -> Iterator[SelectedTextRoute]:
    scoped = route if isinstance(route, SelectedTextRoute) else SelectedTextRoute(
        route=route,
        execution_kind=str(execution_kind or "direct_reply").strip().lower() or "direct_reply",
        selection=str(selection or "policy").strip().lower() or "policy",
        fallback_reason=safe_fallback_reason(fallback_reason),
    )
    token = _ROUTE_SCOPE.set(
        SelectedTextRoute(
            route=scoped.route,
            execution_kind=scoped.execution_kind,
            selection=str(scoped.selection or "policy").strip().lower() or "policy",
            fallback_reason=safe_fallback_reason(scoped.fallback_reason),
        )
    )
    try:
        yield _ROUTE_SCOPE.get() or scoped
    finally:
        _ROUTE_SCOPE.reset(token)


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
