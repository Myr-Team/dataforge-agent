from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Protocol

try:
    from ..model_policy import list_allowed_model_routes
    from ..workspace_model_config import validate_workspace_routing_policy
except ImportError:
    from model_policy import list_allowed_model_routes
    from workspace_model_config import validate_workspace_routing_policy

from .governance import CachePolicyPayload, ModelRoutePayload


class WorkspaceConfigStore(Protocol):
    def load_model_policy(self, workspace_id: str) -> dict[str, Any]: ...
    def save_model_policy(self, workspace_id: str, policy: dict[str, Any]) -> None: ...
    def load_cache_policy(self, workspace_id: str) -> dict[str, Any]: ...
    def save_cache_policy(self, workspace_id: str, policy: dict[str, Any]) -> None: ...


class DataForgeWorkspaceConfigStore:
    def load_model_policy(self, workspace_id: str) -> dict[str, Any]:
        try:
            from ..workspace_store import load_workspace_model_configuration
        except ImportError:
            from workspace_store import load_workspace_model_configuration
        value = load_workspace_model_configuration(workspace_id).get("policy")
        return dict(value) if isinstance(value, dict) else {"revision": 0, "assignments": {}}

    def save_model_policy(self, workspace_id: str, policy: dict[str, Any]) -> None:
        try:
            from ..workspace_store import save_workspace_finops_model_policy
        except ImportError:
            from workspace_store import save_workspace_finops_model_policy
        save_workspace_finops_model_policy(workspace_id, policy)

    def load_cache_policy(self, workspace_id: str) -> dict[str, Any]:
        try:
            from ..workspace_store import load_workspace_finops_cache_policy
        except ImportError:
            from workspace_store import load_workspace_finops_cache_policy
        return load_workspace_finops_cache_policy(workspace_id)

    def save_cache_policy(self, workspace_id: str, policy: dict[str, Any]) -> None:
        try:
            from ..workspace_store import save_workspace_finops_cache_policy
        except ImportError:
            from workspace_store import save_workspace_finops_cache_policy
        save_workspace_finops_cache_policy(workspace_id, policy)


class DataForgeModelRouteClient:
    def __init__(
        self,
        *,
        store: WorkspaceConfigStore,
        route_loader: Callable[[], list[Any]] = list_allowed_model_routes,
    ) -> None:
        self._store = store
        self._route_loader = route_loader

    def current_version(self, workspace_id: str) -> str:
        return str(_revision(self._store.load_model_policy(workspace_id), key="revision"))

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = ModelRoutePayload.model_validate(payload)
        previous = self._store.load_model_policy(clean.workspace_id)
        routes = list(self._route_loader())
        selected = next(
            (
                route
                for route in routes
                if str(getattr(route, "route_id", "") or "") == clean.route_id
            ),
            None,
        )
        if selected is None or str(getattr(selected, "deployment", "") or "") != clean.deployment:
            raise ValueError("model route and deployment are not an allowlisted pair")
        assignments = dict(previous.get("assignments") or {})
        current_assignment = assignments.get(clean.execution_kind)
        fallback = (
            str(current_assignment.get("fallback_route_id") or "").strip() or None
            if isinstance(current_assignment, dict)
            else None
        )
        assignments[clean.execution_kind] = {
            "primary_route_id": clean.route_id,
            "fallback_route_id": fallback,
        }
        applied = validate_workspace_routing_policy(
            {"assignments": assignments},
            routes,
            revision=_revision(previous, key="revision") + 1,
            updated_at=_now(),
        )
        self._store.save_model_policy(clean.workspace_id, applied)
        return {
            "previous": previous,
            "applied_version": str(applied["revision"]),
            "execution_kind": clean.execution_kind,
            "route_id": clean.route_id,
        }

    def verify(self, payload: dict[str, Any], result: dict[str, Any]) -> bool:
        clean = ModelRoutePayload.model_validate(payload)
        current = self._store.load_model_policy(clean.workspace_id)
        assignment = (current.get("assignments") or {}).get(clean.execution_kind)
        return (
            isinstance(assignment, dict)
            and assignment.get("primary_route_id") == clean.route_id
            and str(current.get("revision")) == str(result.get("applied_version"))
        )

    def restore(self, payload: dict[str, Any], result: dict[str, Any]) -> bool:
        clean = ModelRoutePayload.model_validate(payload)
        previous = result.get("previous")
        if not isinstance(previous, dict):
            return False
        self._store.save_model_policy(clean.workspace_id, previous)
        return self._store.load_model_policy(clean.workspace_id) == previous


class DataForgeCachePolicyClient:
    def __init__(self, *, store: WorkspaceConfigStore) -> None:
        self._store = store

    def current_version(self, workspace_id: str) -> str:
        return str(_revision(self._store.load_cache_policy(workspace_id), key="version"))

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean = CachePolicyPayload.model_validate(payload)
        previous = self._store.load_cache_policy(clean.workspace_id)
        applied = {
            "version": _revision(previous, key="version") + 1,
            "enabled": clean.enabled,
            "ttl_seconds": clean.ttl_seconds,
            "updated_at": _now(),
        }
        self._store.save_cache_policy(clean.workspace_id, applied)
        return {"previous": previous, "applied_version": str(applied["version"])}

    def verify(self, payload: dict[str, Any], result: dict[str, Any]) -> bool:
        clean = CachePolicyPayload.model_validate(payload)
        current = self._store.load_cache_policy(clean.workspace_id)
        return (
            current.get("enabled") is clean.enabled
            and int(current.get("ttl_seconds") or 0) == clean.ttl_seconds
            and str(current.get("version")) == str(result.get("applied_version"))
        )

    def restore(self, payload: dict[str, Any], result: dict[str, Any]) -> bool:
        clean = CachePolicyPayload.model_validate(payload)
        previous = result.get("previous")
        if not isinstance(previous, dict):
            return False
        self._store.save_cache_policy(clean.workspace_id, previous)
        return self._store.load_cache_policy(clean.workspace_id) == previous


def _revision(value: dict[str, Any], *, key: str) -> int:
    revision = value.get(key)
    return revision if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0 else 0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
