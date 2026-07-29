from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

try:
    from .context_evaluation import DEFAULT_CONTEXT_EVALUATION_SUMMARY_PATH, load_evaluation_gate
    from .workspace_model_config import EXECUTION_KIND_CAPABILITIES
except ImportError:
    from context_evaluation import DEFAULT_CONTEXT_EVALUATION_SUMMARY_PATH, load_evaluation_gate
    from workspace_model_config import EXECUTION_KIND_CAPABILITIES


_ROUTE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
_DEPLOYMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PROVIDER_TYPES = frozenset({"azure_foundry", "deepseek"})
_DEEPSEEK_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})
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
    provider_id: str | None = None
    provider_type: str = "azure_foundry"
    model_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", self.model_id or self.deployment)


@dataclass(frozen=True)
class SelectedTextRoute:
    route: ModelRoute
    execution_kind: str
    selection: str = "policy"
    fallback_reason: str | None = None
    policy_revision: int | None = None
    price_card_revision: int | None = None


_ROUTE_SCOPE: ContextVar[SelectedTextRoute | None] = ContextVar("dataforge_model_route_scope", default=None)
_PRICE_CARD_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar("dataforge_model_price_card_scope", default=None)
_WORKSPACE_POLICY_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar("dataforge_workspace_model_policy_scope", default=None)
_WORKSPACE_PRICE_CARD_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar("dataforge_workspace_price_card_scope", default=None)
_MANUAL_ROUTE_SCOPE: ContextVar[str | None] = ContextVar("dataforge_manual_model_route_scope", default=None)


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
        provider_type = str(item.get("provider_type") or "azure_foundry").strip().lower()
        provider_id = str(item.get("provider_id") or "").strip().lower() or None
        model_id = str(item.get("model_id") or item.get("deployment") or "").strip()
        deployment = str(item.get("deployment") or model_id).strip()
        label = str(item.get("label") or deployment).strip()
        capabilities = frozenset(str(value).strip().lower() for value in item.get("capabilities", []) if str(value).strip())
        if not _ROUTE_ID.fullmatch(route_id) or route_id in seen:
            raise ModelPolicyError("Model route id is invalid or duplicated")
        if not _DEPLOYMENT.fullmatch(deployment):
            raise ModelPolicyError("Model route deployment is invalid")
        if not label or len(label) > 100 or not capabilities:
            raise ModelPolicyError("Model route label or capabilities are invalid")
        if provider_type not in _PROVIDER_TYPES:
            raise ModelPolicyError("Model route provider type is invalid")
        if not isinstance(item.get("enabled", True), bool) or item.get("enabled", True) is not True:
            raise ModelPolicyError("Model route provider is disabled")
        if provider_type == "azure_foundry":
            if provider_id is not None:
                raise ModelPolicyError("Azure Foundry route must not declare provider id")
        else:
            if provider_id is None or not _PROVIDER_ID.fullmatch(provider_id):
                raise ModelPolicyError("External model route provider id is invalid")
            if model_id not in _DEEPSEEK_MODELS:
                raise ModelPolicyError("External model route model is unsupported")
            if str(item.get("connection_state") or "").strip().lower() != "connected":
                raise ModelPolicyError("External model route provider is not connected")
            if str(item.get("governance_state") or "").strip().lower() != "governed":
                raise ModelPolicyError("External model route provider is not governed")
        seen.add(route_id)
        routes.append(
            ModelRoute(
                route_id,
                deployment,
                label,
                capabilities,
                provider_id=provider_id,
                provider_type=provider_type,
                model_id=model_id,
            )
        )
    return routes


def resolve_text_route(*, capability: str = "chat") -> ModelRoute:
    required = str(capability or "chat").strip().lower()
    routes = [
        route
        for route in list_allowed_model_routes()
        if required in route.capabilities and _runtime_route_enabled(route)
    ]
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
    return [
        route
        for route in list_allowed_model_routes()
        if required in route.capabilities and _runtime_route_enabled(route)
    ]


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


def _allowlisted_route(route_id: str, *, capability: str) -> ModelRoute | None:
    normalized = str(route_id or "").strip().lower()
    for route in list_allowed_model_routes():
        if (
            route.route_id == normalized
            and str(capability or "").strip().lower() in route.capabilities
            and _runtime_route_enabled(route)
        ):
            return route
    return None


def _policy_revision(policy: Mapping[str, Any] | None) -> int | None:
    value = (policy or {}).get("revision") if isinstance(policy, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _price_card_revision(price_card: Mapping[str, Any] | None) -> int | None:
    value = (price_card or {}).get("revision") if isinstance(price_card, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _workspace_assignment(
    policy: Mapping[str, Any] | None,
    execution_kind: str,
) -> Mapping[str, Any] | None:
    assignments = (policy or {}).get("assignments") if isinstance(policy, Mapping) else None
    candidate = assignments.get(execution_kind) if isinstance(assignments, Mapping) else None
    return candidate if isinstance(candidate, Mapping) else None


def select_text_route_record(
    execution_kind: str,
    *,
    agent_id: str | None = None,
    candidate_enabled: bool = False,
    policy: Mapping[str, Any] | None = None,
    manual_route_id: str | None = None,
    price_card: Mapping[str, Any] | None = None,
) -> SelectedTextRoute:
    normalized_kind = str(execution_kind or "direct_reply").strip().lower()
    if policy is None:
        policy = _WORKSPACE_POLICY_SCOPE.get()
    if price_card is None:
        price_card = _WORKSPACE_PRICE_CARD_SCOPE.get()
    if manual_route_id is None:
        manual_route_id = _MANUAL_ROUTE_SCOPE.get()
    policy_revision = _policy_revision(policy)
    price_card_revision = _price_card_revision(price_card)
    workspace_capability = EXECUTION_KIND_CAPABILITIES.get(normalized_kind, "chat")
    if manual_route_id:
        manual = _allowlisted_route(str(manual_route_id), capability=workspace_capability)
        if manual is None:
            raise ModelPolicyError("Requested model route is not allowlisted or compatible")
        return SelectedTextRoute(
            route=manual,
            execution_kind=normalized_kind,
            selection="manual",
            policy_revision=policy_revision,
            price_card_revision=price_card_revision,
        )
    agent_assignments = (
        policy.get("agent_assignments")
        if isinstance(policy, Mapping)
        else None
    )
    agent_assignment = (
        agent_assignments.get(str(agent_id or ""))
        if isinstance(agent_assignments, Mapping)
        else None
    )
    assignment_candidates = [
        (agent_assignment, "agent_policy"),
        (_workspace_assignment(policy, normalized_kind), "workspace_policy"),
    ]
    default_route_id = (
        str(policy.get("default_route_id") or "").strip().lower()
        if isinstance(policy, Mapping)
        else ""
    )
    if default_route_id:
        assignment_candidates.append(
            ({"primary_route_id": default_route_id}, "workspace_default")
        )
    for assignment, primary_selection in assignment_candidates:
        if not isinstance(assignment, Mapping):
            continue
        for field_name, selection, fallback_reason in (
            ("primary_route_id", primary_selection, None),
            ("fallback_route_id", "fallback", "capability_missing"),
        ):
            route_id = str(assignment.get(field_name) or "").strip().lower()
            if not route_id:
                continue
            selected = _allowlisted_route(route_id, capability=workspace_capability)
            if selected is not None:
                return SelectedTextRoute(
                    route=selected,
                    execution_kind=normalized_kind,
                    selection=selection,
                    fallback_reason=fallback_reason,
                    policy_revision=policy_revision,
                    price_card_revision=price_card_revision,
                )
    desired = _EXECUTION_KIND_CAPABILITY.get(normalized_kind, "chat")
    fallback_reason: str | None = None
    capability = desired
    prefer_non_analysis_for_chat = normalized_kind in {"follow_up", "direct_reply"}
    if desired == "followup":
        if candidate_enabled:
            try:
                candidate_route = _pick_route(capability="followup")
            except ModelPolicyError:
                fallback_reason = "capability_missing"
            else:
                gate = context_optimization_gate(candidate_route.route_id)
                if gate.get("eligible") is True:
                    return SelectedTextRoute(
                        route=candidate_route,
                        execution_kind=normalized_kind,
                        selection="policy",
                        policy_revision=policy_revision,
                        price_card_revision=price_card_revision,
                    )
                fallback_reason = "candidate_not_eligible"
        else:
            fallback_reason = "candidate_not_eligible"
        capability = "chat"
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
        policy_revision=policy_revision,
        price_card_revision=price_card_revision,
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


def current_model_price_card() -> dict[str, Any]:
    scoped = _PRICE_CARD_SCOPE.get()
    if not isinstance(scoped, dict):
        scoped = _WORKSPACE_PRICE_CARD_SCOPE.get()
    if not isinstance(scoped, dict):
        return {"revision": 0, "currency": "USD", "entries": []}
    return {
        "revision": scoped.get("revision", 0),
        "currency": scoped.get("currency", "USD"),
        "entries": [dict(item) for item in scoped.get("entries") or [] if isinstance(item, Mapping)],
    }


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
    price_card: Mapping[str, Any] | None = None,
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
            policy_revision=scoped.policy_revision,
            price_card_revision=scoped.price_card_revision,
        )
    )
    inherited_price_card = _WORKSPACE_PRICE_CARD_SCOPE.get()
    effective_price_card = price_card if isinstance(price_card, Mapping) else inherited_price_card
    price_token = _PRICE_CARD_SCOPE.set(
        {
            "revision": (effective_price_card or {}).get("revision", 0),
            "currency": (effective_price_card or {}).get("currency", "USD"),
            "entries": [dict(item) for item in (effective_price_card or {}).get("entries") or [] if isinstance(item, Mapping)],
        }
        if isinstance(effective_price_card, Mapping)
        else None
    )
    try:
        yield _ROUTE_SCOPE.get() or scoped
    finally:
        _PRICE_CARD_SCOPE.reset(price_token)
        _ROUTE_SCOPE.reset(token)


@contextmanager
def workspace_model_policy_scope(
    *,
    policy: Mapping[str, Any] | None = None,
    price_card: Mapping[str, Any] | None = None,
    manual_route_id: str | None = None,
) -> Iterator[None]:
    policy_token = _WORKSPACE_POLICY_SCOPE.set(dict(policy) if isinstance(policy, Mapping) else None)
    price_token = _WORKSPACE_PRICE_CARD_SCOPE.set(dict(price_card) if isinstance(price_card, Mapping) else None)
    manual_token = _MANUAL_ROUTE_SCOPE.set(str(manual_route_id).strip().lower() if manual_route_id else None)
    try:
        yield None
    finally:
        _MANUAL_ROUTE_SCOPE.reset(manual_token)
        _WORKSPACE_PRICE_CARD_SCOPE.reset(price_token)
        _WORKSPACE_POLICY_SCOPE.reset(policy_token)


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
                "model_id": route.model_id,
                "provider_id": route.provider_id,
                "provider_type": route.provider_type,
                "label": route.label,
                "capabilities": sorted(route.capabilities),
            }
            for route in routes
        ],
    }


def _environment_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _runtime_route_enabled(route: ModelRoute) -> bool:
    return (
        route.provider_type == "azure_foundry"
        or _environment_flag("DF_EXTERNAL_PROVIDER_ROUTING_ENABLED")
    )


def context_optimization_gate(route_id: str = "followup") -> dict[str, object]:
    configured = str(os.environ.get("DF_CONTEXT_EVALUATION_SUMMARY_PATH") or "").strip()
    summary_path = Path(configured) if configured else DEFAULT_CONTEXT_EVALUATION_SUMMARY_PATH
    return load_evaluation_gate(summary_path, route_id=str(route_id or "followup").strip().lower() or "followup")
