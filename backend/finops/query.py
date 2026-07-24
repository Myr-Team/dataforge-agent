from __future__ import annotations

import base64
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import FinOpsRequestEvent


class FinOpsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_ref: str = Field(min_length=1, max_length=128)
    authorized_workspace_ids: tuple[str, ...]
    from_value: str
    to_value: str
    department_id: str | None = None
    workspace_id: str | None = None
    agent_id: str | None = None
    actor_ref: str | None = None
    model: str | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_window(self) -> "FinOpsQuery":
        start = _parse_time(self.from_value)
        end = _parse_time(self.to_value)
        if start > end:
            raise ValueError("from must be before to")
        if (end - start).days > 90:
            raise ValueError("query window cannot exceed 90 days")
        if self.workspace_id and self.workspace_id not in self.authorized_workspace_ids:
            raise ValueError("workspace is outside the authorized scope")
        return self


class FinOpsQueryService:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def requests(self, query: FinOpsQuery) -> dict[str, Any]:
        rows = self._rows(query)
        offset = _decode_cursor(query.cursor)
        page = rows[offset : offset + query.limit]
        next_offset = offset + len(page)
        payload = self._envelope(query, rows)
        payload.update(
            {
                "items": [self._public_event(row) for row in page],
                "next_cursor": _encode_cursor(next_offset) if next_offset < len(rows) else None,
                "count": len(page),
            }
        )
        return payload

    def events(self, query: FinOpsQuery) -> list[FinOpsRequestEvent]:
        """Return already tenant/workspace-scoped events for internal evaluators."""
        return self._rows(query)

    def request_detail(self, query: FinOpsQuery, request_ref: str) -> dict[str, Any] | None:
        event = self._repository.get_event(
            tenant_ref=query.tenant_ref,
            workspace_ids=self._selected_workspace_ids(query),
            request_ref=request_ref,
        )
        if event is None or not self._matches(event, query):
            return None
        payload = self._envelope(query, [event])
        payload["request"] = self._public_event(event)
        return payload

    def overview(self, query: FinOpsQuery) -> dict[str, Any]:
        rows = self._rows(query)
        return self._overview_from_rows(query, rows)

    def bootstrap(self, query: FinOpsQuery) -> dict[str, Any]:
        rows = self._rows(query)
        departments = self._breakdowns_from_rows(query, rows, "department")
        filters = self._filters_from_rows(query, rows)
        departments["items"] = departments["items"][:5]
        departments["count"] = len(departments["items"])
        payload = self._envelope(query, rows)
        payload.update(
            {
                "overview": self._overview_from_rows(query, rows),
                "trend": self._trends_from_rows(query, rows, "day"),
                "departments": departments,
                "filters": filters["filters"],
            }
        )
        return payload

    def _overview_from_rows(
        self,
        query: FinOpsQuery,
        rows: list[FinOpsRequestEvent],
    ) -> dict[str, Any]:
        token_values = [row.tokens.total for row in rows if row.tokens.total is not None]
        cost_values = [row.estimated_cost.amount for row in rows if row.estimated_cost.amount is not None]
        latencies = sorted(row.latency_ms for row in rows if row.latency_ms is not None)
        failures = sum(row.status == "failed" for row in rows)
        cache_eligible = [row for row in rows if row.cache.eligible is True]
        cache_hits = sum(row.cache.state == "hit" for row in cache_eligible)
        governed = sum(row.gateway_coverage == "apim_governed" for row in rows)
        priced = len(cost_values)

        payload = self._envelope(query, rows)
        payload["metrics"] = {
            "requests": len(rows),
            "tokens": {
                "total": sum(token_values) if token_values else None,
                "known_requests": len(token_values),
                "unknown_requests": len(rows) - len(token_values),
            },
            "estimated_cost": {
                "amount": round(sum(cost_values), 8) if cost_values else None,
                "priced_requests": priced,
                "unpriced_requests": len(rows) - priced,
                "status": "unavailable" if not rows or not priced else ("partial" if priced < len(rows) else "estimated"),
            },
            "latency": {
                "p50_ms": _percentile(latencies, 0.50),
                "p95_ms": _percentile(latencies, 0.95),
                "known_requests": len(latencies),
            },
            "error_rate_pct": round((failures / len(rows)) * 100, 2) if rows else None,
            "cache_hit_rate_pct": round((cache_hits / len(cache_eligible)) * 100, 2) if cache_eligible else None,
            "apim_coverage_pct": round((governed / len(rows)) * 100, 2) if rows else None,
        }
        payload["insights"] = []
        return payload

    def breakdowns(self, query: FinOpsQuery, group_by: str) -> dict[str, Any]:
        if group_by not in {"department", "workspace", "actor", "agent", "model"}:
            raise ValueError("unsupported group_by")
        rows = self._rows(query)
        return self._breakdowns_from_rows(query, rows, group_by)

    def _breakdowns_from_rows(
        self,
        query: FinOpsQuery,
        rows: list[FinOpsRequestEvent],
        group_by: str,
    ) -> dict[str, Any]:
        field = {
            "department": "department_id",
            "workspace": "workspace_id",
            "actor": "actor_ref",
            "agent": "agent_id",
            "model": "deployment",
        }[group_by]
        grouped: dict[str, list[FinOpsRequestEvent]] = {}
        for event in rows:
            key = str(getattr(event, field) or ("unassigned" if group_by == "department" else "unrecorded"))
            grouped.setdefault(key, []).append(event)
        items = []
        for key, rows in grouped.items():
            costs = [row.estimated_cost.amount for row in rows if row.estimated_cost.amount is not None]
            tokens = [row.tokens.total for row in rows if row.tokens.total is not None]
            latencies = sorted(row.latency_ms for row in rows if row.latency_ms is not None)
            failures = sum(row.status == "failed" for row in rows)
            items.append(
                {
                    "key": key,
                    "requests": len(rows),
                    "tokens": sum(tokens) if tokens else None,
                    "estimated_cost": round(sum(costs), 8) if costs else None,
                    "error_rate_pct": round((failures / len(rows)) * 100, 2),
                    "p95_latency_ms": _percentile(latencies, 0.95),
                    "data_status": _data_status(rows),
                }
            )
        items.sort(key=lambda row: (-row["requests"], row["key"]))
        payload = self._envelope(query, rows)
        payload.update({"group_by": group_by, "items": items, "count": len(items)})
        return payload

    def trends(self, query: FinOpsQuery, bucket: str) -> dict[str, Any]:
        if bucket not in {"hour", "day"}:
            raise ValueError("unsupported bucket")
        rows = self._rows(query)
        return self._trends_from_rows(query, rows, bucket)

    def _trends_from_rows(
        self,
        query: FinOpsQuery,
        rows: list[FinOpsRequestEvent],
        bucket: str,
    ) -> dict[str, Any]:
        grouped: dict[str, list[FinOpsRequestEvent]] = {}
        for event in rows:
            value = event.occurred_at.astimezone(timezone.utc)
            key = value.strftime("%Y-%m-%dT%H:00:00Z") if bucket == "hour" else value.strftime("%Y-%m-%dT00:00:00Z")
            grouped.setdefault(key, []).append(event)
        items = []
        for key, rows in sorted(grouped.items()):
            totals = Counter()
            for row in rows:
                for name in ("input", "output", "cached_input", "reasoning", "total"):
                    value = getattr(row.tokens, name)
                    if value is not None:
                        totals[name] += value
            costs = [row.estimated_cost.amount for row in rows if row.estimated_cost.amount is not None]
            items.append(
                {
                    "bucket": key,
                    "requests": len(rows),
                    "tokens": {name: totals[name] if totals[name] else None for name in ("input", "output", "cached_input", "reasoning", "total")},
                    "estimated_cost": round(sum(costs), 8) if costs else None,
                    "data_status": _data_status(rows),
                }
            )
        payload = self._envelope(query, rows)
        payload.update({"bucket": bucket, "items": items, "count": len(items)})
        return payload

    def filters(self, query: FinOpsQuery) -> dict[str, Any]:
        rows = self._rows(query)
        return self._filters_from_rows(query, rows)

    def _filters_from_rows(
        self,
        query: FinOpsQuery,
        rows: list[FinOpsRequestEvent],
    ) -> dict[str, Any]:
        payload = self._envelope(query, rows)
        payload["filters"] = {
            "departments": sorted({row.department_id or "unassigned" for row in rows}),
            "workspaces": sorted({row.workspace_id for row in rows}),
            "actors": sorted({row.actor_ref for row in rows if row.actor_ref}),
            "agents": sorted({row.agent_id for row in rows if row.agent_id}),
            "models": sorted({row.deployment or row.model for row in rows if row.deployment or row.model}),
        }
        return payload

    def agents(self, query: FinOpsQuery) -> dict[str, Any]:
        rows = self._rows(query)
        agent_groups: dict[str, list[FinOpsRequestEvent]] = {}
        model_groups: dict[str, list[FinOpsRequestEvent]] = {}
        matrix_groups: dict[tuple[str, str], list[FinOpsRequestEvent]] = {}
        for event in rows:
            agent = event.agent_id or "unrecorded"
            model = event.deployment or event.model or "unrecorded"
            agent_groups.setdefault(agent, []).append(event)
            model_groups.setdefault(model, []).append(event)
            matrix_groups.setdefault((agent, model), []).append(event)

        def aggregate(key: str, grouped_rows: list[FinOpsRequestEvent]) -> dict[str, Any]:
            tokens = [row.tokens.total for row in grouped_rows if row.tokens.total is not None]
            costs = [row.estimated_cost.amount for row in grouped_rows if row.estimated_cost.amount is not None]
            latencies = sorted(row.latency_ms for row in grouped_rows if row.latency_ms is not None)
            succeeded = sum(row.status == "succeeded" for row in grouped_rows)
            return {
                "key": key,
                "requests": len(grouped_rows),
                "tokens": sum(tokens) if tokens else None,
                "estimated_cost": round(sum(costs), 8) if costs else None,
                "success_rate_pct": round(succeeded / len(grouped_rows) * 100, 2),
                "p95_latency_ms": _percentile(latencies, 0.95),
                "data_status": _data_status(grouped_rows),
            }

        agents = [aggregate(key, grouped) for key, grouped in agent_groups.items()]
        models = [aggregate(key, grouped) for key, grouped in model_groups.items()]
        matrix = [
            {
                "agent_id": agent,
                "model": model,
                **{key: value for key, value in aggregate(f"{agent}|{model}", grouped).items() if key != "key"},
            }
            for (agent, model), grouped in matrix_groups.items()
        ]
        agents.sort(key=lambda item: (-item["requests"], item["key"]))
        models.sort(key=lambda item: (-item["requests"], item["key"]))
        matrix.sort(key=lambda item: (-item["requests"], item["agent_id"], item["model"]))
        payload = self._envelope(query, rows)
        payload.update(
            {
                "items": agents,
                "agents": agents,
                "models": models,
                "matrix": matrix,
                "count": len(agents),
            }
        )
        return payload

    def _rows(self, query: FinOpsQuery) -> list[FinOpsRequestEvent]:
        rows = self._repository.list_events(
            tenant_ref=query.tenant_ref,
            workspace_ids=self._selected_workspace_ids(query),
            from_value=query.from_value,
            to_value=query.to_value,
        )
        return [row for row in rows if self._matches(row, query)]

    def _matches(self, event: FinOpsRequestEvent, query: FinOpsQuery) -> bool:
        return all(
            (
                not query.department_id or event.department_id == query.department_id,
                not query.workspace_id or event.workspace_id == query.workspace_id,
                not query.agent_id or event.agent_id == query.agent_id,
                not query.actor_ref or event.actor_ref == query.actor_ref,
                not query.model or query.model in {event.model, event.deployment},
            )
        )

    def _selected_workspace_ids(self, query: FinOpsQuery) -> tuple[str, ...]:
        return (query.workspace_id,) if query.workspace_id else query.authorized_workspace_ids

    def _envelope(self, query: FinOpsQuery, rows: list[FinOpsRequestEvent]) -> dict[str, Any]:
        selected = list(self._selected_workspace_ids(query))
        governed = sum(row.gateway_coverage == "apim_governed" for row in rows)
        return {
            "scope": {
                "tenant_ref": query.tenant_ref,
                "workspace_ids": selected,
                "workspace_count": len(selected),
            },
            "window": {"from": query.from_value, "to": query.to_value, "timezone": "UTC"},
            "freshness": {
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "sources": ["dataforge_application"],
                "refresh_after_seconds": 60,
            },
            "coverage": {
                "observed_requests": len(rows),
                "apim_governed_requests": governed,
                "apim_coverage_pct": round((governed / len(rows)) * 100, 2) if rows else None,
            },
            "currency": "USD",
            "data_status": _data_status(rows),
        }

    @staticmethod
    def _public_event(event: FinOpsRequestEvent) -> dict[str, Any]:
        payload = event.model_dump(mode="json", exclude_none=False)
        payload.pop("correlation_ref", None)
        return payload


def _data_status(rows: list[FinOpsRequestEvent]) -> str:
    if not rows:
        return "unavailable"
    if any(
        row.evidence_state in {"partial", "unavailable"}
        or row.tokens.total is None
        or row.estimated_cost.amount is None
        for row in rows
    ):
        return "partial"
    return "available"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int((len(values) - 1) * fraction + 0.999999)))
    return values[index]


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        offset = int(payload.get("offset") or 0)
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0
    return max(0, offset)
