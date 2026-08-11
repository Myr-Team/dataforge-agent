from __future__ import annotations

import base64
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import FinOpsRequestEvent
from .rollups import FinOpsRollup, aggregate_rollups


class FinOpsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_ref: str = Field(min_length=1, max_length=128)
    authorized_workspace_ids: tuple[str, ...]
    permission_scope: str = Field(default="", max_length=64)
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
    def __init__(
        self,
        repository: Any,
        *,
        gateway_unmatched_repository: Any | None = None,
        rollup_repository: Any | None = None,
    ) -> None:
        self._repository = repository
        self._gateway_unmatched_repository = gateway_unmatched_repository
        self._rollup_repository = rollup_repository

    def _gateway_unmatched(
        self,
        query: FinOpsQuery,
        rows: list[FinOpsRequestEvent],
    ) -> dict[str, Any] | None:
        """Read the unattributed gateway 4xx/5xx aggregate for the window.

        The evidence is system scoped, never tenant attributed. It is exposed as
        a clearly labelled block and only enriches ``unmatched_metric_records``;
        it is never folded into request counts, error rate or cost.
        """
        repo = self._gateway_unmatched_repository
        if repo is None:
            return None
        summary = repo.summarize(query.from_value, query.to_value)
        if not isinstance(summary, dict):
            return None
        summary = dict(summary)
        summary["linked_requests"] = sum(
            row.gateway_coverage == "apim_governed" for row in rows
        )
        return summary

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

    def unit_economics_trend(
        self,
        query: FinOpsQuery,
        bucket: Literal["hour", "day"] = "day",
    ) -> dict[str, Any]:
        rollups = self._unit_economics_rollups(query, bucket)
        grouped: dict[str, list[FinOpsRollup]] = defaultdict(list)
        for item in rollups:
            grouped[item.bucket_at].append(item)
        items = []
        for bucket_at, rows in sorted(grouped.items()):
            requests = sum(item.request_count for item in rows)
            failures = min(requests, sum(item.failure_count for item in rows))
            successful = requests - failures
            known_costs = [item.estimated_cost for item in rows if item.estimated_cost is not None]
            estimated_cost = round(sum(known_costs), 8) if known_costs else None
            items.append({
                "bucket_at": bucket_at,
                "successful_requests": successful,
                "estimated_cost": estimated_cost,
                "cost_per_successful_request": (
                    round(estimated_cost / successful, 8)
                    if estimated_cost is not None and successful > 0
                    else None
                ),
                "data_status": (
                    "available"
                    if estimated_cost is not None and successful > 0
                    else "unavailable"
                ),
            })
        return {"items": items, "count": len(items)}

    def _unit_economics_rollups(
        self,
        query: FinOpsQuery,
        bucket: Literal["hour", "day"],
    ) -> list[FinOpsRollup]:
        if self._rollup_repository is None:
            hourly, daily = aggregate_rollups(self._rows(query))
            return hourly if bucket == "hour" else daily
        # Rollups have no actor dimension.  Actor-scoped views must derive from
        # the already-authorized request facts for their complete query window.
        if query.actor_ref:
            hourly, daily = aggregate_rollups(self._rows(query))
            return hourly if bucket == "hour" else daily

        now = datetime.now(timezone.utc)
        start = _parse_time(query.from_value)
        end = _parse_time(query.to_value)
        current_start = (
            now.replace(minute=0, second=0, microsecond=0)
            if bucket == "hour"
            else now.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        # A historical/closed query never reaches into request facts.  When
        # the current bucket is included, use persisted rollups only through
        # the previous closed boundary, then add facts for that one bucket.
        if end <= current_start:
            return self._closed_rollups(
                self._rollup_repository.read(query, bucket),
                query,
                bucket,
                start,
                end,
            )

        rollups: list[FinOpsRollup] = []
        closed_end = min(end, current_start)
        if start < closed_end:
            closed_query = query.model_copy(update={"to_value": _time_string(closed_end)})
            rollups.extend(
                self._closed_rollups(
                    self._rollup_repository.read(closed_query, bucket),
                    query,
                    bucket,
                    start,
                    closed_end,
                )
            )
        current_end = min(end, now)
        current_begin = max(start, current_start)
        if current_begin < current_end:
            current_query = query.model_copy(
                update={"from_value": _time_string(current_begin), "to_value": _time_string(current_end)}
            )
            hourly, daily = aggregate_rollups(self._rows(current_query))
            rollups.extend(hourly if bucket == "hour" else daily)
        return rollups

    @staticmethod
    def _closed_rollups(
        rows: list[FinOpsRollup],
        query: FinOpsQuery,
        bucket: Literal["hour", "day"],
        start: datetime,
        end: datetime,
    ) -> list[FinOpsRollup]:
        selected = set(query.authorized_workspace_ids)
        result = []
        for item in rows:
            try:
                bucket_at = _parse_time(item.bucket_at)
            except ValueError:
                continue
            if (
                item.tenant_ref != query.tenant_ref
                or item.workspace_id not in selected
                or not start <= bucket_at < end
                or (query.department_id and item.department_id != query.department_id)
                or (query.agent_id and item.agent_id != query.agent_id)
                or (query.model and item.model_deployment != query.model)
            ):
                continue
            result.append(item)
        return result

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
                "trend": self._trends_from_rows(
                    query, rows, "day", "tokens"
                ),
                "departments": departments,
                "filters": filters["filters"],
                "trust": _trust(rows, self._gateway_unmatched(query, rows)),
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
        succeeded = sum(row.status == "succeeded" for row in rows)
        cache_eligible = [row for row in rows if row.result_cache.eligible is True]
        cache_hits = sum(row.result_cache.state == "hit" for row in cache_eligible)
        cache_counts = {
            state: sum(row.result_cache.state == state for row in rows)
            for state in ("hit", "miss", "bypassed", "unavailable")
        }
        cache_economics = _cache_economics(rows)
        provider_cache_rows = [
            row
            for row in rows
            if row.provider_cache.hit_tokens is not None
            and row.provider_cache.miss_tokens is not None
        ]
        provider_hit_tokens = sum(
            row.provider_cache.hit_tokens or 0 for row in provider_cache_rows
        )
        provider_miss_tokens = sum(
            row.provider_cache.miss_tokens or 0 for row in provider_cache_rows
        )
        provider_denominator = provider_hit_tokens + provider_miss_tokens
        governed = sum(row.gateway_coverage == "apim_governed" for row in rows)
        priced = len(cost_values)

        def token_total(field: str) -> int | None:
            values = [
                getattr(row.tokens, field)
                for row in rows
                if getattr(row.tokens, field) is not None
            ]
            return sum(values) if values else None

        payload = self._envelope(query, rows)
        payload["metrics"] = {
            "requests": len(rows),
            "tokens": {
                "input": token_total("input"),
                "output": token_total("output"),
                "cached_input": token_total("cached_input"),
                "reasoning": token_total("reasoning"),
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
            "success_rate_pct": round((succeeded / len(rows)) * 100, 2) if rows else None,
            "cache": {
                "eligible_requests": len(cache_eligible),
                **cache_counts,
                **cache_economics,
            },
            "cache_hit_rate_pct": round((cache_hits / len(cache_eligible)) * 100, 2) if cache_eligible else None,
            "result_cache": {
                "eligible_requests": len(cache_eligible),
                **cache_counts,
                "hit_rate_pct": (
                    round((cache_hits / len(cache_eligible)) * 100, 2)
                    if cache_eligible
                    else None
                ),
            },
            "provider_cache": {
                "known_requests": len(provider_cache_rows),
                "hit_tokens": provider_hit_tokens if provider_cache_rows else None,
                "miss_tokens": provider_miss_tokens if provider_cache_rows else None,
                "hit_rate_pct": (
                    round(provider_hit_tokens / provider_denominator * 100, 2)
                    if provider_denominator
                    else None
                ),
                "data_status": (
                    "available" if provider_cache_rows else "unavailable"
                ),
            },
            "apim_coverage_pct": round((governed / len(rows)) * 100, 2) if rows else None,
        }
        payload["insights"] = []
        payload["trust"] = _trust(rows, self._gateway_unmatched(query, rows))
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
            cache_eligible = [row for row in rows if row.result_cache.eligible is True]
            cache_counts = {
                state: sum(row.result_cache.state == state for row in rows)
                for state in ("hit", "miss", "bypassed", "unavailable")
            }
            cache_hits = cache_counts["hit"]
            token_known = {
                name: [getattr(row.tokens, name) for row in rows if getattr(row.tokens, name) is not None]
                for name in ("input", "cached_input", "output", "reasoning")
            }
            uncached_known = [
                max(0, int(row.tokens.input) - int(row.tokens.cached_input))
                for row in rows
                if row.tokens.input is not None and row.tokens.cached_input is not None
            ]
            composition_known_rows = sum(
                row.tokens.input is not None
                and row.tokens.cached_input is not None
                and row.tokens.output is not None
                for row in rows
            )
            items.append(
                {
                    "key": key,
                    "requests": len(rows),
                    "tokens": sum(tokens) if tokens else None,
                    "estimated_cost": round(sum(costs), 8) if costs else None,
                    "error_rate_pct": round((failures / len(rows)) * 100, 2),
                    "p95_latency_ms": _percentile(latencies, 0.95),
                    "cache_hit_rate_pct": (
                        round(cache_hits / len(cache_eligible) * 100, 2)
                        if cache_eligible
                        else None
                    ),
                    "cache": {
                        "eligible_requests": len(cache_eligible),
                        **cache_counts,
                        **_cache_economics(rows),
                    },
                    "token_composition": {
                        "input": sum(token_known["input"]) if token_known["input"] else None,
                        "cached_input": (
                            sum(token_known["cached_input"])
                            if token_known["cached_input"]
                            else None
                        ),
                        "uncached_input": sum(uncached_known) if uncached_known else None,
                        "output": sum(token_known["output"]) if token_known["output"] else None,
                        "reasoning": (
                            sum(token_known["reasoning"])
                            if token_known["reasoning"]
                            else None
                        ),
                        "known_requests": composition_known_rows,
                        "data_status": (
                            "available"
                            if composition_known_rows == len(rows) and rows
                            else "partial"
                            if any(token_known.values())
                            else "unavailable"
                        ),
                    },
                    "data_status": _data_status(rows),
                }
            )
        items.sort(key=lambda row: (-row["requests"], row["key"]))
        payload = self._envelope(query, rows)
        payload.update({"group_by": group_by, "items": items, "count": len(items)})
        return payload

    def trends(
        self,
        query: FinOpsQuery,
        bucket: str,
        *,
        metric: str = "tokens",
    ) -> dict[str, Any]:
        if bucket not in {"hour", "day"}:
            raise ValueError("unsupported bucket")
        if metric not in {
            "tokens",
            "requests",
            "estimated_cost",
            "p95_latency_ms",
        }:
            raise ValueError("unsupported metric")
        rows = self._rows(query)
        return self._trends_from_rows(query, rows, bucket, metric)

    def _trends_from_rows(
        self,
        query: FinOpsQuery,
        rows: list[FinOpsRequestEvent],
        bucket: str,
        metric: str = "tokens",
    ) -> dict[str, Any]:
        grouped: dict[str, list[FinOpsRequestEvent]] = {}
        for event in rows:
            value = event.occurred_at.astimezone(timezone.utc)
            key = value.strftime("%Y-%m-%dT%H:00:00Z") if bucket == "hour" else value.strftime("%Y-%m-%dT00:00:00Z")
            grouped.setdefault(key, []).append(event)
        items = []
        observed_now = datetime.now(timezone.utc)
        for key, rows in sorted(grouped.items()):
            totals = Counter()
            known: Counter = Counter()
            for row in rows:
                for name in ("input", "output", "cached_input", "reasoning", "total"):
                    value = getattr(row.tokens, name)
                    if value is not None:
                        totals[name] += value
                        known[name] += 1
            costs = [row.estimated_cost.amount for row in rows if row.estimated_cost.amount is not None]
            latencies = sorted(
                row.latency_ms
                for row in rows
                if row.latency_ms is not None
            )
            # Distinguish an observed zero from a missing observation: only
            # collapse to None when no row contributed a known value.
            token_totals = {
                name: (totals[name] if known[name] else None)
                for name in ("input", "output", "cached_input", "reasoning", "total")
            }
            metric_values = {
                "tokens": token_totals["total"],
                "requests": len(rows),
                "estimated_cost": (
                    round(sum(costs), 8) if costs else None
                ),
                "p95_latency_ms": _percentile(latencies, 0.95),
            }
            cache_eligible = [row for row in rows if row.result_cache.eligible is True]
            cache_counts = {
                state: sum(row.result_cache.state == state for row in rows)
                for state in ("hit", "miss", "bypassed", "unavailable")
            }
            items.append(
                {
                    "bucket": key,
                    "bucket_status": (
                        "complete"
                        if datetime.strptime(key, "%Y-%m-%dT%H:00:00Z" if bucket == "hour" else "%Y-%m-%dT00:00:00Z")
                        .replace(tzinfo=timezone.utc)
                        + (timedelta(hours=1) if bucket == "hour" else timedelta(days=1))
                        <= observed_now
                        else "in_progress"
                    ),
                    "requests": len(rows),
                    "tokens": token_totals,
                    "estimated_cost": round(sum(costs), 8) if costs else None,
                    "p95_latency_ms": _percentile(latencies, 0.95),
                    "cache": {
                        "eligible_requests": len(cache_eligible),
                        **cache_counts,
                        **_cache_economics(rows),
                    },
                    "value": metric_values[metric],
                    "data_status": _metric_data_status(rows, metric),
                }
            )
        payload = self._envelope(query, rows)
        units = {
            "tokens": "Token",
            "requests": "次",
            "estimated_cost": "USD",
            "p95_latency_ms": "ms",
        }
        payload.update(
            {
                "bucket": bucket,
                "metric": metric,
                "unit": units[metric],
                "items": items,
                "count": len(items),
            }
        )
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
                "refresh_after_seconds": 300,
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


def _cache_economics(rows: list[FinOpsRequestEvent]) -> dict[str, Any]:
    """Summarize only explicit result-cache evidence.

    Avoided cost is deliberately conservative: it is calculated only for a
    cache hit with observed avoided tokens, total tokens, an estimated amount,
    an official price key, and a price-card revision. Missing price evidence
    leaves the total unreported rather than inventing a value.
    """
    hits = [row for row in rows if row.result_cache.state == "hit"]
    avoided_rows = [
        row for row in hits
        if row.cache.avoided_tokens is not None
    ]
    avoided_tokens = (
        sum(row.cache.avoided_tokens or 0 for row in avoided_rows)
        if avoided_rows
        else None
    )
    savings: list[float] = []
    incomplete = len(avoided_rows) < len(hits)
    for row in avoided_rows:
        avoided = row.cache.avoided_tokens or 0
        total = row.tokens.total
        cost = row.estimated_cost
        reliable = (
            avoided > 0
            and total is not None
            and total > avoided
            and cost.amount is not None
            and bool(cost.official_price_key)
            and bool(cost.price_card_revision)
        )
        if not reliable:
            incomplete = True
            continue
        charged_equivalent = total - avoided
        savings.append(cost.amount * avoided / charged_equivalent)
    return {
        "avoided_tokens": avoided_tokens,
        "estimated_savings": round(sum(savings), 8) if savings else None,
        "data_status": (
            "unavailable"
            if not avoided_rows
            else ("partial" if incomplete else "available")
        ),
    }


def _metric_data_status(rows: list[FinOpsRequestEvent], metric: str) -> str:
    """Report completeness for the selected metric only.

    Request counts are always exact, so token/cost/latency gaps must not
    degrade a request-count trend. Other metrics reflect how many rows carry
    the observation backing that specific metric.
    """
    if not rows:
        return "unavailable"
    if metric == "requests":
        return "available"
    if metric == "tokens":
        known = sum(row.tokens.total is not None for row in rows)
    elif metric == "estimated_cost":
        known = sum(row.estimated_cost.amount is not None for row in rows)
    elif metric == "p95_latency_ms":
        known = sum(row.latency_ms is not None for row in rows)
    else:
        return _data_status(rows)
    if known == 0:
        return "unavailable"
    return "available" if known == len(rows) else "partial"


def _coverage_state(
    total: int,
    known: int,
    *,
    empty_state: str = "no_samples",
    none_state: str = "partial",
) -> str:
    if total == 0:
        return empty_state
    if known == 0:
        return none_state
    return "complete" if known == total else "partial"


def _trust(
    rows: list[FinOpsRequestEvent],
    gateway_unmatched: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total = len(rows)
    priced = sum(
        row.estimated_cost.amount is not None
        for row in rows
    )
    token_known = sum(row.tokens.total is not None for row in rows)
    governed = sum(
        row.gateway_coverage == "apim_governed"
        for row in rows
    )

    def coverage(known: int) -> float | None:
        return round(known / total * 100, 4) if total else None

    unmatched_metric_records: int | None = None
    if isinstance(gateway_unmatched, dict):
        errors = gateway_unmatched.get("unmatched_gateway_errors")
        if isinstance(errors, dict) and errors.get("total") is not None:
            unmatched_metric_records = int(errors["total"])

    return {
        "pricing": {
            "priced_requests": priced,
            "unpriced_requests": total - priced,
            "coverage_pct": coverage(priced),
            "state": _coverage_state(
                total,
                priced,
                none_state="unpriced",
            ),
        },
        "tokens": {
            "known_requests": token_known,
            "unknown_requests": total - token_known,
            "coverage_pct": coverage(token_known),
            "state": _coverage_state(total, token_known),
        },
        "apim": {
            "app_observed_requests": total,
            "apim_governed_requests": governed,
            "unmatched_metric_records": unmatched_metric_records,
            "coverage_pct": coverage(governed),
            "state": (
                "no_samples"
                if not total
                else (
                    "complete"
                    if governed == total
                    else "reconciliation_pending"
                )
            ),
            "gateway_unmatched": gateway_unmatched,
        },
    }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _time_string(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
