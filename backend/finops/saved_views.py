from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SAFE_FILTERS = {"department_id", "workspace_id", "agent_id", "model"}


class SavedViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    audience: Literal["it", "finance", "shared"] = "shared"
    tab: Literal["overview", "cost", "roi", "risk"] = "overview"
    filters: dict[str, str] = Field(default_factory=dict)

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = set(value) - _SAFE_FILTERS
        if unknown:
            raise ValueError("saved view contains unsupported filters")
        return {
            key: str(raw).strip()[:160]
            for key, raw in value.items()
            if str(raw).strip()
        }


class SavedView(SavedViewCreate):
    view_id: str
    version: int = Field(default=1, ge=1)
    created_by: str
    updated_at: str


class SavedViewRepository(Protocol):
    def save(self, tenant_ref: str, value: SavedView) -> SavedView: ...
    def list(self, tenant_ref: str) -> list[SavedView]: ...
    def delete(self, tenant_ref: str, view_id: str) -> bool: ...


class InMemorySavedViewRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[tuple[str, str], SavedView] = {}

    def save(self, tenant_ref: str, value: SavedView) -> SavedView:
        with self._lock:
            self._values[(tenant_ref, value.view_id)] = value.model_copy(deep=True)
        return value.model_copy(deep=True)

    def list(self, tenant_ref: str) -> list[SavedView]:
        with self._lock:
            values = [
                value.model_copy(deep=True)
                for (tenant, _), value in self._values.items()
                if tenant == tenant_ref
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    def delete(self, tenant_ref: str, view_id: str) -> bool:
        with self._lock:
            return self._values.pop((tenant_ref, view_id), None) is not None


class FinOpsSavedViewService:
    def __init__(self, repository: SavedViewRepository) -> None:
        self._repository = repository

    def create(
        self,
        *,
        tenant_ref: str,
        actor_ref: str,
        value: SavedViewCreate,
    ) -> SavedView:
        saved = SavedView(
            **value.model_dump(),
            view_id=f"view_{uuid4().hex}",
            created_by=actor_ref,
            updated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        return self._repository.save(tenant_ref, saved)

    def list(
        self,
        *,
        tenant_ref: str,
        authorized_workspace_ids: tuple[str, ...],
    ) -> list[SavedView]:
        allowed = set(authorized_workspace_ids)
        return [
            value
            for value in self._repository.list(tenant_ref)
            if not value.filters.get("workspace_id")
            or value.filters["workspace_id"] in allowed
        ]

    def delete(self, *, tenant_ref: str, view_id: str) -> bool:
        return self._repository.delete(tenant_ref, view_id)


def csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def export_breakdown_csv(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["维度", "调用", "Token", "估算成本(USD)", "错误率(%)", "P95(ms)"])
    for row in rows:
        writer.writerow([
            csv_cell(row.get("key")),
            csv_cell(row.get("requests")),
            csv_cell(row.get("tokens")),
            csv_cell(row.get("estimated_cost")),
            csv_cell(row.get("error_rate_pct")),
            csv_cell(row.get("p95_latency_ms")),
        ])
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
