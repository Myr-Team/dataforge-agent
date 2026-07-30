from __future__ import annotations

from threading import RLock
from typing import Any, Protocol

from .models import FinOpsRequestEvent


class DemoSeedRepository(Protocol):
    def replace_batch_events(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
        events: tuple[FinOpsRequestEvent, ...],
        event_repository: Any,
    ) -> tuple[int, int]: ...

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

    def replace_batch_events(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        batch: str,
        events: tuple[FinOpsRequestEvent, ...],
        event_repository: Any,
    ) -> tuple[int, int]:
        key = _key(tenant_ref, workspace_id, batch)
        normalized = _request_refs(tuple(event.request_ref for event in events))
        if any(
            event.tenant_ref != key[0] or event.workspace_id != key[1]
            for event in events
        ):
            raise ValueError("demo seed event scope mismatch")
        with self._lock:
            owned_keys = [
                owned_key
                for owned_key in self._batches
                if owned_key[:2] == key[:2]
            ]
            previous = {
                request_ref
                for owned_key in owned_keys
                for request_ref in self._batches[owned_key]
            }
            current = set(normalized)
            event_repository.upsert_events(events)
            event_repository.delete_events(
                tenant_ref=key[0],
                workspace_id=key[1],
                request_refs=previous - current,
            )
            for owned_key in owned_keys:
                del self._batches[owned_key]
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
