from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Iterable, Mapping


UNATTRIBUTED_SCOPE = "unattributed"
STATUS_CLASSES = ("client_error_4xx", "server_error_5xx")
DEFAULT_DATA_SOURCE = "apim_gateway_logs"
SCOPE_NOTE = (
    "网关侧未关联到任何应用运行的 4xx/5xx 聚合证据；无法可靠归属租户或工作区，"
    "按 unattributed/system 范围统计，不计入请求账本、错误率或成本。"
)


def gateway_status_class(status_code: Any) -> str | None:
    """Map an HTTP status code to a 4xx/5xx aggregate class, else None."""
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return None
    if 400 <= code < 500:
        return "client_error_4xx"
    if 500 <= code <= 599:
        return "server_error_5xx"
    return None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hour_bucket(value: datetime) -> datetime:
    aware = value.astimezone(timezone.utc)
    return aware.replace(minute=0, second=0, microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bucket_counts(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[datetime, str], int]:
    """Bucket gateway rows by UTC hour and status class.

    Only the timestamp and status code are read; correlation ids, bodies and
    identities present on the row are deliberately ignored.
    """
    counts: dict[tuple[datetime, str], int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        occurred = _parse_time(row.get("occurred_at"))
        status_class = gateway_status_class(row.get("status_code"))
        if occurred is None or status_class is None:
            continue
        key = (_hour_bucket(occurred), status_class)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _empty_summary(from_value: str, to_value: str, data_source: str) -> dict[str, Any]:
    return {
        "scope": UNATTRIBUTED_SCOPE,
        "window": {"from": from_value, "to": to_value},
        "unmatched_gateway_errors": {
            "total": 0,
            "client_error_4xx": 0,
            "server_error_5xx": 0,
        },
        "data_source": data_source,
        "updated_at": None,
        "note": SCOPE_NOTE,
    }


class InMemoryGatewayUnmatchedRepository:
    """Aggregate-only, unattributed gateway 4xx/5xx evidence store.

    Rows are stored per (hour bucket, status class) and replaced on each
    collection so repeated ingestion of the same window is idempotent and never
    double counts. No tenant scope is stored: the evidence is system scoped.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}

    def record_gateway_errors(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        data_source: str = DEFAULT_DATA_SOURCE,
        now: datetime | None = None,
    ) -> int:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        counts = _bucket_counts(rows)
        with self._lock:
            for (bucket_at, status_class), count in counts.items():
                self._rows[(_iso(bucket_at), status_class)] = {
                    "bucket_at": bucket_at,
                    "request_count": count,
                    "data_source": data_source,
                    "updated_at": moment,
                }
        return len(counts)

    def summarize(self, from_value: str, to_value: str) -> dict[str, Any]:
        start = _parse_time(from_value)
        end = _parse_time(to_value)
        summary = _empty_summary(from_value, to_value, DEFAULT_DATA_SOURCE)
        if start is None or end is None:
            return summary
        totals = {"client_error_4xx": 0, "server_error_5xx": 0}
        latest: datetime | None = None
        data_source = DEFAULT_DATA_SOURCE
        with self._lock:
            rows = list(self._rows.items())
        for (_, status_class), entry in rows:
            bucket_at = entry["bucket_at"]
            if bucket_at < start or bucket_at >= end:
                continue
            totals[status_class] += int(entry["request_count"])
            data_source = str(entry.get("data_source") or data_source)
            updated = entry.get("updated_at")
            if isinstance(updated, datetime) and (latest is None or updated > latest):
                latest = updated
        summary["unmatched_gateway_errors"] = {
            "total": totals["client_error_4xx"] + totals["server_error_5xx"],
            "client_error_4xx": totals["client_error_4xx"],
            "server_error_5xx": totals["server_error_5xx"],
        }
        summary["data_source"] = data_source
        summary["updated_at"] = _iso(latest) if latest is not None else None
        return summary


class SqlGatewayUnmatchedRepository:
    """SQL-backed unattributed gateway 4xx/5xx aggregate store."""

    def __init__(self, *, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def record_gateway_errors(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        data_source: str = DEFAULT_DATA_SOURCE,
        now: datetime | None = None,
    ) -> int:
        counts = _bucket_counts(rows)
        if not counts:
            return 0
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            for (bucket_at, status_class), count in counts.items():
                cursor.execute(
                    """/* finops:upsert-gateway-unmatched */
                    MERGE df_finops.gateway_unmatched_rollup WITH (HOLDLOCK) AS target
                    USING (SELECT ? AS scope, ? AS bucket_at, ? AS status_class)
                        AS source
                    ON target.scope = source.scope
                       AND target.bucket_at = source.bucket_at
                       AND target.status_class = source.status_class
                    WHEN MATCHED THEN UPDATE SET
                        request_count = ?, data_source = ?,
                        updated_at = SYSUTCDATETIME()
                    WHEN NOT MATCHED THEN INSERT (
                        scope, bucket_at, status_class, request_count, data_source
                    ) VALUES (?, ?, ?, ?, ?);""",
                    UNATTRIBUTED_SCOPE,
                    bucket_at,
                    status_class,
                    count,
                    data_source,
                    UNATTRIBUTED_SCOPE,
                    bucket_at,
                    status_class,
                    count,
                    data_source,
                )
            connection.commit()
            return len(counts)
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            connection.close()

    def summarize(self, from_value: str, to_value: str) -> dict[str, Any]:
        start = _parse_time(from_value)
        end = _parse_time(to_value)
        summary = _empty_summary(from_value, to_value, DEFAULT_DATA_SOURCE)
        if start is None or end is None:
            return summary
        connection = self._connection_factory()
        try:
            rows = connection.cursor().execute(
                """/* finops:summarize-gateway-unmatched */
                SELECT status_class, SUM(request_count) AS request_count,
                       MAX(data_source) AS data_source, MAX(updated_at) AS updated_at
                FROM df_finops.gateway_unmatched_rollup
                WHERE scope = ? AND bucket_at >= ? AND bucket_at < ?
                GROUP BY status_class""",
                UNATTRIBUTED_SCOPE,
                start.replace(tzinfo=None),
                end.replace(tzinfo=None),
            ).fetchall()
        finally:
            connection.close()
        totals = {"client_error_4xx": 0, "server_error_5xx": 0}
        latest: datetime | None = None
        data_source = DEFAULT_DATA_SOURCE
        for row in rows:
            values = list(row)
            status_class = str(values[0])
            if status_class in totals:
                totals[status_class] += int(values[1] or 0)
            if values[2]:
                data_source = str(values[2])
            updated = _parse_time(values[3]) if values[3] is not None else None
            if updated is not None and (latest is None or updated > latest):
                latest = updated
        summary["unmatched_gateway_errors"] = {
            "total": totals["client_error_4xx"] + totals["server_error_5xx"],
            "client_error_4xx": totals["client_error_4xx"],
            "server_error_5xx": totals["server_error_5xx"],
        }
        summary["data_source"] = data_source
        summary["updated_at"] = _iso(latest) if latest is not None else None
        return summary


__all__ = [
    "DEFAULT_DATA_SOURCE",
    "InMemoryGatewayUnmatchedRepository",
    "SCOPE_NOTE",
    "STATUS_CLASSES",
    "SqlGatewayUnmatchedRepository",
    "UNATTRIBUTED_SCOPE",
    "gateway_status_class",
]
