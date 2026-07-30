from __future__ import annotations

from threading import RLock
from typing import Protocol


class DemoSeedRepository(Protocol):
    def replace_batch(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
        request_refs: tuple[str, ...],
    ) -> tuple[int, int]: ...

    def list_request_refs(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
    ) -> tuple[str, ...]: ...


class InMemoryDemoSeedRepository:
    """Tracks demo-owned request refs without adding provenance to public facts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._batches: dict[tuple[str, str, str], tuple[str, ...]] = {}

    def replace_batch(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
        request_refs: tuple[str, ...],
    ) -> tuple[int, int]:
        key = _key(tenant_ref, workspace_id, batch)
        normalized = _request_refs(request_refs)
        with self._lock:
            previous = set(self._batches.get(key, ()))
            current = set(normalized)
            self._batches[key] = normalized
        return len(current - previous), len(current & previous)

    def list_request_refs(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
    ) -> tuple[str, ...]:
        key = _key(tenant_ref, workspace_id, batch)
        with self._lock:
            return self._batches.get(key, ())


def _key(tenant_ref: str, workspace_id: str, batch: str) -> tuple[str, str, str]:
    values = tuple(str(value or "").strip() for value in (tenant_ref, workspace_id, batch))
    if not all(values):
        raise ValueError("demo seed scope is required")
    if len(values[0]) > 128 or len(values[1]) > 160 or len(values[2]) > 64:
        raise ValueError("demo seed scope is too long")
    return values


def _request_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(value or "").strip() for value in values))
    if any(not value or len(value) > 128 for value in normalized):
        raise ValueError("invalid demo seed request ref")
    return normalized
