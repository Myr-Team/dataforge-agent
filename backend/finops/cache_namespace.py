from __future__ import annotations

import hashlib
from typing import Any, Protocol


SUPPORTED_FINOPS_CACHE_DOMAINS = frozenset(
    {"overview", "cost", "roi", "risk", "requests", "settings"}
)
_REVISION_TTL_SECONDS = 86400 * 30


class AtomicRevisionCache(Protocol):
    def get_int(self, key: str) -> tuple[int | None, dict[str, Any]]: ...

    def increment(
        self,
        key: str,
        *,
        ttl_seconds: int = _REVISION_TTL_SECONDS,
    ) -> tuple[int | None, dict[str, Any]]: ...


class FinOpsCacheNamespace:
    """Workspace/domain revisions used for scoped cache invalidation."""

    def __init__(self, cache: AtomicRevisionCache) -> None:
        self._cache = cache

    def current(
        self,
        tenant_ref: str,
        workspace_ids: tuple[str, ...] | list[str],
        domains: tuple[str, ...] | list[str],
    ) -> str:
        tenant = _required(tenant_ref, "tenant")
        workspaces = _workspaces(workspace_ids)
        selected_domains = _domains(domains)
        revisions: list[str] = []
        for domain in selected_domains:
            for workspace_id in workspaces:
                revision, _ = self._cache.get_int(
                    _revision_key(tenant, workspace_id, domain)
                )
                revisions.append(f"{domain}:{workspace_id}:{int(revision or 0)}")
        return "|".join(revisions)

    def bump(
        self,
        tenant_ref: str,
        workspace_id: str,
        domains: tuple[str, ...] | list[str],
    ) -> None:
        tenant = _required(tenant_ref, "tenant")
        workspace = _required(workspace_id, "workspace")
        for domain in _domains(domains):
            self._cache.increment(
                _revision_key(tenant, workspace, domain),
                ttl_seconds=_REVISION_TTL_SECONDS,
            )


def _required(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"FinOps cache {label} is required")
    return normalized


def _workspaces(workspace_ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    values = tuple(sorted({_required(item, "workspace") for item in workspace_ids}))
    if not values:
        raise ValueError("FinOps cache workspace is required")
    return values


def _domains(domains: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    values = tuple(sorted({str(item or "").strip().lower() for item in domains}))
    if not values or any(item not in SUPPORTED_FINOPS_CACHE_DOMAINS for item in values):
        raise ValueError("unsupported FinOps cache domain")
    return values


def _revision_key(tenant_ref: str, workspace_id: str, domain: str) -> str:
    material = "\x00".join((tenant_ref, workspace_id, domain)).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return f"finops:namespace:v1:{digest}"
