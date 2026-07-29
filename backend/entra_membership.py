from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from . import cache_store
from .entra_group_mapping import group_ref_for
from .finops.normalization import canonical_tenant_id, opaque_ref
from .graph_client import list_signed_in_transitive_groups


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
    try:
        cached, meta = cache.get_json(cache_key)
    except Exception:
        cached, meta = None, {"status": "unavailable"}
    if (
        isinstance(cached, Mapping)
        and meta.get("status") == "hit"
        and isinstance(cached.get("group_refs"), list)
    ):
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

    try:
        groups = graph_loader(request)
    except Exception:
        return _unavailable("microsoft_graph")
    refs = sorted(
        {
            group_ref_for(tenant_id, str(item.get("id") or ""), secret=secret)
            for item in groups
            if isinstance(item, Mapping) and str(item.get("id") or "").strip()
        }
    )
    try:
        cache.set_json(
            cache_key,
            {"group_refs": refs},
            ttl_seconds=120,
        )
    except Exception:
        pass
    return {
        "state": "observed",
        "group_refs": refs,
        "source": "microsoft_graph",
        "permission_state": "granted",
    }


def _unavailable(source: str) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "group_refs": [],
        "source": source,
        "permission_state": "unavailable",
    }


__all__ = ["resolve_actor_group_membership"]
