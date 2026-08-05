from __future__ import annotations

import os
import threading
from typing import Any, Callable, Mapping

from . import cache_store
from .entra_group_mapping import group_ref_for
from .finops.normalization import canonical_tenant_id, opaque_ref
from .graph_client import list_signed_in_transitive_groups


_RESOLUTION_LOCKS = tuple(threading.Lock() for _ in range(64))


def resolve_actor_group_membership(
    actor: Mapping[str, Any],
    *,
    request: Any | None,
    graph_loader: Callable[[Any | None], list[dict[str, str]]] = list_signed_in_transitive_groups,
    cache: Any = cache_store,
) -> dict[str, Any]:
    tenant_id = str(actor.get("tenant_id") or "").strip()
    actor_id = str(actor.get("actor_id") or "").strip()
    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not tenant_id or not actor_id or not secret:
        return _unavailable("claims")

    if not bool(actor.get("group_overage")):
        refs = sorted(
            {
                group_ref_for(tenant_id, str(group_id), secret=secret)
                for group_id in actor.get("groups") or []
                if str(group_id).strip()
            }
        )
        return {
            "state": "observed",
            "group_refs": refs,
            "source": "easy_auth_claims",
            "permission_state": "not_required",
        }

    cache_key = (
        "dataforge:entra-membership:v1:"
        + opaque_ref(
            "membership",
            canonical_tenant_id(tenant_id),
            actor_id.lower(),
            secret=secret,
        )
    )
    cached_result = _cached_membership(cache, cache_key)
    if cached_result is not None:
        return cached_result

    with _resolution_lock(cache_key):
        cached_result = _cached_membership(cache, cache_key)
        if cached_result is not None:
            return cached_result
        try:
            groups = graph_loader(request)
        except Exception:
            unavailable = _unavailable("microsoft_graph")
            _store_membership(cache, cache_key, unavailable, ttl_seconds=30)
            return unavailable
        refs = sorted(
            {
                group_ref_for(tenant_id, str(item.get("id") or ""), secret=secret)
                for item in groups
                if isinstance(item, Mapping) and str(item.get("id") or "").strip()
            }
        )
        result = {
            "state": "observed",
            "group_refs": refs,
            "source": "microsoft_graph",
            "permission_state": "granted",
        }
        _store_membership(cache, cache_key, result, ttl_seconds=120)
        return result


def _resolution_lock(cache_key: str) -> threading.Lock:
    # The stripe only coordinates loaders inside this process; it is never a
    # persisted or cross-process key, so Python's per-process hash is sufficient.
    return _RESOLUTION_LOCKS[hash(cache_key) % len(_RESOLUTION_LOCKS)]


def _cached_membership(cache: Any, cache_key: str) -> dict[str, Any] | None:
    try:
        cached, meta = cache.get_json(cache_key)
    except Exception:
        return None
    if not isinstance(cached, Mapping) or meta.get("status") != "hit":
        return None
    state = str(cached.get("state") or "observed").strip().lower()
    if state == "unavailable":
        return {
            "state": "unavailable",
            "group_refs": [],
            "source": "microsoft_graph_cache",
            "permission_state": "unavailable",
        }
    if state != "observed" or not isinstance(cached.get("group_refs"), list):
        return None
    refs = [
        str(item)
        for item in cached["group_refs"]
        if str(item).startswith("group_")
    ]
    return {
        "state": "observed",
        "group_refs": sorted(set(refs)),
        "source": "microsoft_graph_cache",
        "permission_state": "granted",
    }


def _store_membership(
    cache: Any,
    cache_key: str,
    result: Mapping[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    try:
        cache.set_json(
            cache_key,
            {
                "state": result.get("state"),
                "group_refs": list(result.get("group_refs") or []),
            },
            ttl_seconds=ttl_seconds,
        )
    except Exception:
        pass


def _unavailable(source: str) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "group_refs": [],
        "source": source,
        "permission_state": "unavailable",
    }


__all__ = ["resolve_actor_group_membership"]
