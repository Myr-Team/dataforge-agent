from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Iterable

from .models import FinOpsRequestEvent
from .normalization import normalize_run_event


class InMemoryFinOpsRepository:
    """Deterministic repository used by tests and local preview.

    The production SQL repository implements the same narrow methods.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._events: dict[tuple[str, str], FinOpsRequestEvent] = {}

    def upsert_events(self, events: Iterable[FinOpsRequestEvent]) -> None:
        with self._lock:
            for event in events:
                self._events[(event.tenant_ref, event.request_ref)] = event

    def list_events(
        self,
        *,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        from_value: str,
        to_value: str,
    ) -> list[FinOpsRequestEvent]:
        allowed = set(workspace_ids)
        with self._lock:
            rows = [
                event
                for (tenant, _), event in self._events.items()
                if tenant == tenant_ref
                and event.workspace_id in allowed
                and from_value <= event.occurred_at.isoformat().replace("+00:00", "Z") <= to_value
            ]
        return sorted(rows, key=lambda event: (event.occurred_at, event.request_ref))

    def get_event(
        self,
        *,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        request_ref: str,
    ) -> FinOpsRequestEvent | None:
        with self._lock:
            event = self._events.get((tenant_ref, request_ref))
        return event if event and event.workspace_id in set(workspace_ids) else None


class RunStoreFinOpsRepository:
    """Read-through adapter for the currently persisted DataForge run ledger.

    This keeps the portal useful before the additive Azure SQL migration is
    activated. It does not mutate run records and never copies prompt, answer,
    provider response, identity, secret, or internal error text into FinOps.
    """

    def __init__(
        self,
        *,
        run_loader: Callable[[str], list[dict[str, Any]]],
        hmac_secret: str,
        department_resolver: Callable[[str, str], str | None] | None = None,
    ) -> None:
        self._run_loader = run_loader
        self._hmac_secret = hmac_secret
        self._department_resolver = department_resolver or (lambda _tenant_ref, _workspace_id: None)

    def list_events(
        self,
        *,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        from_value: str,
        to_value: str,
    ) -> list[FinOpsRequestEvent]:
        events: dict[tuple[str, str], FinOpsRequestEvent] = {}
        for workspace_id in workspace_ids:
            try:
                runs = self._run_loader(workspace_id)
            except Exception:
                runs = []
            for run in runs or []:
                if not isinstance(run, dict) or str(run.get("workspace_id") or workspace_id) != workspace_id:
                    continue
                models = run.get("models") if isinstance(run.get("models"), list) else []
                for index in range(len(models)):
                    try:
                        event = normalize_run_event(
                            run,
                            model_index=index,
                            tenant_id=tenant_ref,
                            hmac_secret=self._hmac_secret,
                            department_id=self._department_resolver(tenant_ref, workspace_id),
                        )
                    except (TypeError, ValueError):
                        continue
                    timestamp = event.occurred_at.isoformat().replace("+00:00", "Z")
                    if from_value <= timestamp <= to_value:
                        events[(event.tenant_ref, event.request_ref)] = event
        return sorted(events.values(), key=lambda event: (event.occurred_at, event.request_ref))

    def get_event(
        self,
        *,
        tenant_ref: str,
        workspace_ids: tuple[str, ...],
        request_ref: str,
    ) -> FinOpsRequestEvent | None:
        rows = self.list_events(
            tenant_ref=tenant_ref,
            workspace_ids=workspace_ids,
            from_value="1970-01-01T00:00:00Z",
            to_value="9999-12-31T23:59:59Z",
        )
        return next((event for event in rows if event.request_ref == request_ref), None)
