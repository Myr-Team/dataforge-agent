from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from .anomalies import DetectedAnomaly


class AnomalyNotFound(KeyError):
    pass


class AnomalyConflict(RuntimeError):
    pass


class ManagedAnomaly(DetectedAnomaly):
    model_config = ConfigDict(extra="forbid")

    tenant_ref: str
    first_detected_at: str
    updated_at: str
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    suppressed_by: str | None = None
    suppressed_at: str | None = None
    suppressed_until: str | None = None
    suppression_reason: str | None = None
    resolved_at: str | None = None


class AnomalyRepository(Protocol):
    def get(self, tenant_ref: str, anomaly_id: str) -> ManagedAnomaly | None: ...
    def save(self, value: ManagedAnomaly) -> ManagedAnomaly: ...
    def list(self, tenant_ref: str) -> list[ManagedAnomaly]: ...


class InMemoryAnomalyRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[tuple[str, str], ManagedAnomaly] = {}

    def get(self, tenant_ref: str, anomaly_id: str) -> ManagedAnomaly | None:
        with self._lock:
            value = self._items.get((tenant_ref, anomaly_id))
        return value.model_copy(deep=True) if value else None

    def save(self, value: ManagedAnomaly) -> ManagedAnomaly:
        with self._lock:
            self._items[(value.tenant_ref, value.anomaly_id)] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def list(self, tenant_ref: str) -> list[ManagedAnomaly]:
        with self._lock:
            rows = [
                value.model_copy(deep=True)
                for (tenant, _), value in self._items.items()
                if tenant == tenant_ref
            ]
        return sorted(rows, key=lambda item: (item.updated_at, item.anomaly_id), reverse=True)


class FinOpsAnomalyService:
    def __init__(
        self,
        repository: AnomalyRepository,
        *,
        trigger: Callable[[str, ManagedAnomaly], None] | None = None,
    ) -> None:
        self._repository = repository
        self._trigger = trigger

    def reconcile(
        self,
        *,
        tenant_ref: str,
        findings: list[DetectedAnomaly],
        scope_workspace_ids: tuple[str, ...] | None = None,
    ) -> list[ManagedAnomaly]:
        now = _now()
        current_ids = {finding.anomaly_id for finding in findings}
        existing = {item.anomaly_id: item for item in self._repository.list(tenant_ref)}

        for finding in findings:
            current = existing.get(finding.anomaly_id)
            trigger_event: str | None = None
            if current is None:
                value = ManagedAnomaly(
                    **finding.model_dump(),
                    tenant_ref=tenant_ref,
                    first_detected_at=now,
                    updated_at=now,
                )
                trigger_event = "anomaly_created"
            else:
                status = current.status
                if status == "resolved" or (
                    status == "suppressed"
                    and current.suppressed_until
                    and _parse_time(current.suppressed_until) <= datetime.now(timezone.utc)
                ):
                    status = "open"
                value = current.model_copy(
                    update={
                        **finding.model_dump(exclude={"status"}),
                        "status": status,
                        "updated_at": now,
                        "resolved_at": None,
                    }
                )
                if (
                    current.severity != value.severity
                    or current.status != value.status
                    or current.observed_value != value.observed_value
                    or current.threshold_value != value.threshold_value
                ):
                    trigger_event = "anomaly_changed"
            saved = self._repository.save(value)
            if trigger_event and self._trigger is not None:
                try:
                    self._trigger(trigger_event, saved)
                except Exception:
                    pass

        scoped = set(scope_workspace_ids or ())
        for anomaly_id, current in existing.items():
            current_workspace_ids = set(current.workspace_ids)
            is_in_scope = not scoped or (
                bool(current_workspace_ids)
                and current_workspace_ids.issubset(scoped)
            )
            if is_in_scope and anomaly_id not in current_ids and current.status != "resolved":
                self._repository.save(
                    current.model_copy(
                        update={
                            "status": "resolved",
                            "resolved_at": now,
                            "updated_at": now,
                        }
                    )
                )
        return self._repository.list(tenant_ref)

    def acknowledge(
        self,
        *,
        tenant_ref: str,
        anomaly_id: str,
        actor_ref: str,
    ) -> ManagedAnomaly:
        value = self._require(tenant_ref, anomaly_id)
        if value.status != "open":
            raise AnomalyConflict("only open anomalies may be acknowledged")
        now = _now()
        return self._repository.save(
            value.model_copy(
                update={
                    "status": "acknowledged",
                    "acknowledged_by": actor_ref,
                    "acknowledged_at": now,
                    "updated_at": now,
                }
            )
        )

    def suppress(
        self,
        *,
        tenant_ref: str,
        anomaly_id: str,
        actor_ref: str,
        reason: str,
        until: str | None = None,
    ) -> ManagedAnomaly:
        value = self._require(tenant_ref, anomaly_id)
        if value.status not in {"open", "acknowledged"}:
            raise AnomalyConflict("only open or acknowledged anomalies may be suppressed")
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise ValueError("suppression reason is required")
        suppressed_until = None
        if until:
            parsed = _parse_time(until)
            if parsed <= datetime.now(timezone.utc):
                raise ValueError("suppression expiry must be in the future")
            suppressed_until = parsed.isoformat().replace("+00:00", "Z")
        now = _now()
        return self._repository.save(
            value.model_copy(
                update={
                    "status": "suppressed",
                    "suppressed_by": actor_ref,
                    "suppressed_at": now,
                    "suppressed_until": suppressed_until,
                    "suppression_reason": clean_reason[:512],
                    "updated_at": now,
                }
            )
        )

    def list(
        self,
        *,
        tenant_ref: str,
        workspace_ids: tuple[str, ...] | None = None,
    ) -> list[ManagedAnomaly]:
        allowed = set(workspace_ids or ())
        return [
            item
            for item in self._repository.list(tenant_ref)
            if not allowed or bool(allowed.intersection(item.workspace_ids))
        ]

    def get(self, *, tenant_ref: str, anomaly_id: str) -> ManagedAnomaly:
        return self._require(tenant_ref, anomaly_id)

    def _require(self, tenant_ref: str, anomaly_id: str) -> ManagedAnomaly:
        value = self._repository.get(tenant_ref, anomaly_id)
        if value is None:
            raise AnomalyNotFound(anomaly_id)
        return value


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO-8601 timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
