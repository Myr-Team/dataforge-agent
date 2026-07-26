from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from .apim_collector import apim_usage_query, collect_apim_usage
from .sql_repository import SqlFinOpsRepository


def run_apim_backfill(
    *,
    repository: Any,
    scopes: Mapping[str, tuple[str, ...]],
    query_rows: Callable[[str], Iterable[dict[str, Any]]],
    from_value: str,
    to_value: str,
    hmac_secret: str,
    price_mapping_repository: Any | None = None,
) -> dict[str, Any]:
    """Reconcile one APIM evidence window across all opaque SQL ledger scopes."""

    observations = list(query_rows(apim_usage_query(from_value, to_value)))
    totals = {
        "scope_count": 0,
        "application_events": 0,
        "apim_observations": len(observations),
        "rejected_observations": 0,
        "reconciled_events": 0,
    }
    for tenant_ref, workspace_ids in scopes.items():
        if not tenant_ref or not workspace_ids:
            continue
        result = collect_apim_usage(
            repository=repository,
            query_rows=lambda _query, rows=observations: rows,
            tenant_ref=tenant_ref,
            workspace_ids=tuple(workspace_ids),
            from_value=from_value,
            to_value=to_value,
            hmac_secret=hmac_secret,
            price_mapping_repository=price_mapping_repository,
        )
        totals["scope_count"] += 1
        totals["application_events"] += int(result["application_events"])
        totals["rejected_observations"] = max(
            totals["rejected_observations"],
            int(result["rejected_observations"]),
        )
        totals["reconciled_events"] += int(result["reconciled_events"])
    totals["unmatched_observations"] = max(
        0,
        totals["apim_observations"]
        - totals["reconciled_events"]
        - totals["rejected_observations"],
    )
    totals["window"] = {"from": from_value, "to": to_value}
    return totals


def main() -> int:
    try:
        secret = _required("DF_FINOPS_HMAC_SECRET")
        logs_workspace_id = _required("DF_AZURE_MONITOR_LOGS_WORKSPACE_ID")
        repository = _sql_repository()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        overlap_minutes = _bounded_int(
            os.environ.get("DF_FINOPS_APIM_BACKFILL_MINUTES"),
            default=10,
            minimum=5,
            maximum=90,
        )
        start = now - timedelta(minutes=overlap_minutes)
        from_value = _iso(start)
        to_value = _iso(now)
        scopes = repository.list_scopes(from_value=from_value, to_value=to_value)
        result = run_apim_backfill(
            repository=repository,
            scopes=scopes,
            query_rows=lambda query: _logs_query_rows(logs_workspace_id, query, start, now),
            from_value=from_value,
            to_value=to_value,
            hmac_secret=secret,
            price_mapping_repository=_price_mapping_repository(),
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "category": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps({"status": "completed", **result}, separators=(",", ":")))
    return 0


def _sql_repository() -> SqlFinOpsRepository:
    try:
        from ..lineage_sql import build_lineage_sql_connection_factory
    except ImportError:
        from lineage_sql import build_lineage_sql_connection_factory
    return SqlFinOpsRepository(connection_factory=build_lineage_sql_connection_factory())


def _price_mapping_repository() -> Any:
    try:
        from ..lineage_sql import build_lineage_sql_connection_factory
    except ImportError:
        from lineage_sql import build_lineage_sql_connection_factory
    from .sql_pricing import SqlPriceMappingRepository

    return SqlPriceMappingRepository(
        connection_factory=build_lineage_sql_connection_factory()
    )


def _logs_query_rows(
    workspace_id: str,
    query: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    from azure.identity import ManagedIdentityCredential
    from azure.monitor.query import LogsQueryClient

    client_id = str(os.environ.get("DF_AZURE_MONITOR_CLIENT_ID") or "").strip()
    credential = (
        ManagedIdentityCredential(client_id=client_id)
        if client_id
        else ManagedIdentityCredential()
    )
    result = LogsQueryClient(credential).query_workspace(
        workspace_id,
        query,
        timespan=(start, end),
    )
    if getattr(result, "partial_error", None) is not None:
        raise RuntimeError("Azure Monitor returned a partial APIM query result")
    rows: list[dict[str, Any]] = []
    for table in list(getattr(result, "tables", None) or []):
        names = [str(getattr(column, "name", column)) for column in table.columns]
        rows.extend(dict(zip(names, values, strict=False)) for values in table.rows)
    return rows


def _required(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _bounded_int(value: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
