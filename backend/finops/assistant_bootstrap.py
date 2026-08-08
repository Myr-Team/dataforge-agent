from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .. import cache_store
from .assistant_store import AssistantBootstrap, AssistantScope


class BootstrapCacheBackend(Protocol):
    def get_json(self, key: str) -> tuple[dict[str, Any] | None, dict[str, Any]]: ...

    def set_json(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> dict[str, Any]: ...

    def delete(self, key: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CachedAssistantBootstrap:
    value: AssistantBootstrap
    cache_status: str
    cache_key: str


class AssistantBootstrapCache:
    def __init__(
        self,
        *,
        backend: BootstrapCacheBackend = cache_store,
        ttl_seconds: int = 300,
    ) -> None:
        self._backend = backend
        self._ttl_seconds = max(30, min(int(ttl_seconds), 1800))

    def load(
        self,
        scope: AssistantScope,
        loader: Callable[[], AssistantBootstrap],
    ) -> CachedAssistantBootstrap:
        key = self.key(scope)
        try:
            payload, metadata = self._backend.get_json(key)
        except Exception:
            payload, metadata = None, {"status": "unavailable"}
        if payload is not None:
            try:
                return CachedAssistantBootstrap(
                    value=AssistantBootstrap.model_validate(payload),
                    cache_status="hit",
                    cache_key=key,
                )
            except Exception:
                pass
        value = loader()
        if str(metadata.get("status") or "") not in {"unavailable", "unconfigured"}:
            try:
                self._backend.set_json(
                    key,
                    value.model_dump(mode="json"),
                    ttl_seconds=self._ttl_seconds,
                )
            except Exception:
                pass
        return CachedAssistantBootstrap(
            value=value,
            cache_status="miss",
            cache_key=key,
        )

    def invalidate(self, scope: AssistantScope) -> None:
        try:
            self._backend.delete(self.key(scope))
        except Exception:
            return

    @staticmethod
    def key(scope: AssistantScope) -> str:
        digest = hashlib.sha256(
            f"{scope.tenant_ref}\0{scope.actor_ref}\0{scope.workspace_id}".encode(
                "utf-8"
            )
        ).hexdigest()
        return f"df:finops:assistant-bootstrap:{digest}"


__all__ = [
    "AssistantBootstrapCache",
    "CachedAssistantBootstrap",
]
