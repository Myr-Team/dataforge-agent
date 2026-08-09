from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Mapping

from .finops.normalization import canonical_tenant_ref
from .identity import is_trusted_tenant_identity
from .model_policy import ModelRoute
from .model_provider_router import (
    get_model_provider_repository,
    get_model_provider_secret_store,
)
from .model_provider_routes import provider_route_candidates


@dataclass(frozen=True)
class ActorProviderRuntime:
    routes: tuple[ModelRoute, ...] = ()
    connections: tuple[dict[str, str], ...] = ()


_PROVIDER_CONNECTION_SCOPE: ContextVar[dict[str, dict[str, str]] | None] = ContextVar(
    "dataforge_provider_connection_scope",
    default=None,
)


def load_actor_provider_runtime(actor: Mapping[str, Any] | None) -> ActorProviderRuntime:
    if not _enabled("DF_PROVIDER_CONNECTORS_ENABLED") or not _enabled(
        "DF_EXTERNAL_PROVIDER_ROUTING_ENABLED"
    ):
        return ActorProviderRuntime()
    if not is_trusted_tenant_identity(actor):
        return ActorProviderRuntime()
    tenant_id = str((actor or {}).get("tenant_id") or "").strip()
    hmac_secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not tenant_id or not hmac_secret:
        raise RuntimeError("model provider scope is unavailable")
    tenant_ref = canonical_tenant_ref(tenant_id, secret=hmac_secret)
    repository = get_model_provider_repository()
    secret_store = get_model_provider_secret_store()
    records = repository.list(tenant_ref)
    candidates = provider_route_candidates(
        records,
        secret_status=lambda item: secret_store.status(
            item.tenant_ref,
            item.provider_id,
            item.secret_ref,
        ),
    )
    selectable = [
        candidate
        for candidate in candidates
        if candidate.public.get("selectable") is True
    ]
    selected_provider_ids = {
        str(candidate.route.provider_id or "")
        for candidate in selectable
        if candidate.route.provider_id
    }
    connections = tuple(
        {
            "tenant_ref": record.tenant_ref,
            "provider_id": record.provider_id,
            "provider_type": record.provider_type,
            "base_url": record.base_url,
            "secret_ref": record.secret_ref,
        }
        for record in records
        if record.provider_id in selected_provider_ids
    )
    return ActorProviderRuntime(
        routes=tuple(candidate.route for candidate in selectable),
        connections=connections,
    )


@contextmanager
def provider_runtime_scope(
    connections: tuple[dict[str, str], ...] | list[dict[str, str]],
) -> Iterator[None]:
    token = _PROVIDER_CONNECTION_SCOPE.set(
        {
            str(item.get("provider_id") or ""): dict(item)
            for item in connections
            if str(item.get("provider_id") or "").strip()
        }
    )
    try:
        yield None
    finally:
        _PROVIDER_CONNECTION_SCOPE.reset(token)


def current_provider_connection(provider_id: str | None) -> dict[str, str] | None:
    identifier = str(provider_id or "").strip()
    scoped = _PROVIDER_CONNECTION_SCOPE.get()
    if not identifier or not isinstance(scoped, dict):
        return None
    value = scoped.get(identifier)
    return dict(value) if isinstance(value, dict) else None


def runtime_provider_secret(connection: Mapping[str, str]) -> str:
    tenant_ref = str(connection.get("tenant_ref") or "").strip()
    provider_id = str(connection.get("provider_id") or "").strip()
    secret_ref = str(connection.get("secret_ref") or "").strip()
    if not tenant_ref or not provider_id or not secret_ref:
        raise RuntimeError("external provider connection is unavailable")
    return get_model_provider_secret_store().get(
        tenant_ref,
        provider_id,
        secret_ref,
    )


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


__all__ = [
    "ActorProviderRuntime",
    "current_provider_connection",
    "load_actor_provider_runtime",
    "provider_runtime_scope",
    "runtime_provider_secret",
]
