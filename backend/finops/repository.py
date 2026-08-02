from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Iterable

from .models import FinOpsRequestEvent
from .normalization import normalize_run_event


class FinOpsEventRepairConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class FinOpsEventKeyRepair:
    legacy_tenant_ref: str
    legacy_request_ref: str
    canonical_event: FinOpsRequestEvent

    def __post_init__(self) -> None:
        if not self.legacy_tenant_ref or not self.legacy_request_ref:
            raise ValueError("legacy event key is required")


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

    def delete_events(
        self,
        *,
        tenant_ref: str,
        workspace_id: str,
        request_refs: Iterable[str],
    ) -> None:
        targets = {
            str(value or "").strip()
            for value in request_refs
            if str(value or "").strip()
        }
        with self._lock:
            for request_ref in targets:
                key = (tenant_ref, request_ref)
                event = self._events.get(key)
                if event is not None and event.workspace_id == workspace_id:
                    del self._events[key]

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

    def repair_event_keys(
        self,
        plans: Iterable[FinOpsEventKeyRepair],
    ) -> int:
        with self._lock:
            repaired = dict(self._events)
            changed = 0
            for plan in plans:
                legacy_key = (
                    plan.legacy_tenant_ref,
                    plan.legacy_request_ref,
                )
                canonical_key = (
                    plan.canonical_event.tenant_ref,
                    plan.canonical_event.request_ref,
                )
                legacy = repaired.get(legacy_key)
                canonical = repaired.get(canonical_key)
                resolved = resolve_event_key_repair(
                    plan,
                    legacy=legacy,
                    canonical=canonical,
                )
                if canonical != resolved:
                    repaired[canonical_key] = resolved
                    changed += 1
                if legacy_key != canonical_key and legacy_key in repaired:
                    del repaired[legacy_key]
                    if canonical == resolved:
                        changed += 1
            self._events = repaired
            return changed


def resolve_event_key_repair(
    plan: FinOpsEventKeyRepair,
    *,
    legacy: FinOpsRequestEvent | None,
    canonical: FinOpsRequestEvent | None,
) -> FinOpsRequestEvent:
    expected = plan.canonical_event
    for value in (legacy, canonical):
        if value is not None and _logical_event(value) != _logical_event(expected):
            raise FinOpsEventRepairConflict("event_identity_conflict")
    cost = _preserved_cost(
        legacy.estimated_cost if legacy is not None else None,
        canonical.estimated_cost if canonical is not None else None,
        expected.estimated_cost,
    )
    routing_policy_revision = _preserved_routing_policy_revision(
        legacy,
        canonical,
        expected,
    )
    source = canonical or legacy or expected
    return source.model_copy(
        update={
            "tenant_ref": expected.tenant_ref,
            "request_ref": expected.request_ref,
            "actor_ref": expected.actor_ref,
            "correlation_ref": expected.correlation_ref,
            "apim_correlation_id": expected.apim_correlation_id,
            "internal_correlation_key": expected.internal_correlation_key,
            "estimated_cost": cost,
            "routing_policy_revision": routing_policy_revision,
        }
    )


def _preserved_cost(*values: Any) -> Any:
    existing = [value for value in values[:2] if value is not None]
    priced = [
        value
        for value in existing
        if value.status != "unavailable"
        or value.amount is not None
        or value.price_card_revision is not None
        or value.official_price_key is not None
        or value.mapping_revision is not None
    ]
    if len(priced) > 1 and priced[0] != priced[1]:
        raise FinOpsEventRepairConflict("price_evidence_conflict")
    if priced:
        return priced[0]
    if existing:
        return existing[0]
    return values[2]


def _preserved_routing_policy_revision(
    legacy: FinOpsRequestEvent | None,
    canonical: FinOpsRequestEvent | None,
    expected: FinOpsRequestEvent,
) -> int | None:
    existing = [event for event in (legacy, canonical) if event is not None]
    observed = {
        event.routing_policy_revision
        for event in existing
        if event.routing_policy_revision is not None
    }
    if len(observed) > 1:
        raise FinOpsEventRepairConflict("routing_policy_evidence_conflict")
    if observed:
        return next(iter(observed))
    if existing:
        return None
    return expected.routing_policy_revision


def _logical_event(event: FinOpsRequestEvent) -> dict[str, object]:
    value = event.model_dump(mode="json", exclude_none=False)
    for key in (
        "tenant_ref",
        "request_ref",
        "actor_ref",
        "correlation_ref",
        "estimated_cost",
        "internal_correlation_key",
        "routing_policy_revision",
    ):
        value.pop(key, None)
    return value


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
                actor = run.get("actor") if isinstance(run.get("actor"), dict) else {}
                raw_tenant_id = str(actor.get("tenant_id") or "").strip()
                models = run.get("models") if isinstance(run.get("models"), list) else []
                for index in range(len(models)):
                    try:
                        event = normalize_run_event(
                            run,
                            model_index=index,
                            tenant_id=tenant_ref,
                            hmac_secret=self._hmac_secret,
                            department_id=self._department_resolver(tenant_ref, workspace_id),
                            raw_tenant_id=raw_tenant_id or None,
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
