from __future__ import annotations

"""Bounded repair of historical FinOps actor attribution from retained runs.

The command is dry-run by default. Public output is aggregate-only: raw run,
tenant, actor, request, email, and provider identifiers are never returned.
"""

import argparse
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any


_CURSOR = re.compile(r"^offset_(\d{8})$")
_APPLY_CONFIRMATION = "APPLY_CANONICAL_ACTOR_REF_REPAIR"
_MAX_WINDOW = timedelta(days=90)
_MAX_PAGE_SIZE = 200
_MAX_PAGES = 20
_MAX_SOURCE_SUMMARIES = 100_000
_MAX_MODELS_PER_RUN = 64
_TERMINAL_STATUSES = {
    "cancelled",
    "canceled",
    "completed",
    "error",
    "failed",
    "success",
    "succeeded",
}


class ActorRefRepairInputError(ValueError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def repair_completed_run_actor_refs(
    *,
    run_summaries: Sequence[Mapping[str, Any]],
    run_loader: Callable[[str], Mapping[str, Any]],
    event_writer: Callable[[dict[str, object]], int],
    from_value: str,
    to_value: str,
    cursor: str | None = None,
    page_size: int = 100,
    max_pages: int = 1,
    apply: bool = False,
) -> dict[str, object]:
    start = _utc(from_value, "invalid_window")
    end = _utc(to_value, "invalid_window")
    if start >= end:
        raise ActorRefRepairInputError("invalid_window")
    if end - start > _MAX_WINDOW:
        raise ActorRefRepairInputError("window_exceeds_limit")
    if isinstance(page_size, bool) or not 1 <= int(page_size) <= _MAX_PAGE_SIZE:
        raise ActorRefRepairInputError("page_size_out_of_range")
    if isinstance(max_pages, bool) or not 1 <= int(max_pages) <= _MAX_PAGES:
        raise ActorRefRepairInputError("max_pages_out_of_range")
    offset = _cursor_offset(cursor)
    if len(run_summaries) > _MAX_SOURCE_SUMMARIES:
        raise ActorRefRepairInputError("source_limit_exceeded")

    candidates = _candidate_summaries(run_summaries, start=start, end=end)
    batch_limit = int(page_size) * int(max_pages)
    selected = candidates[offset : offset + batch_limit]
    next_offset = offset + len(selected)
    has_more = next_offset < len(candidates)
    skipped: Counter[str] = Counter()
    runs_eligible = 0
    events_planned = 0
    events_applied = 0

    for summary in selected:
        run_id = summary["run_id"]
        try:
            loaded = run_loader(run_id)
        except Exception:
            skipped["detail_unavailable"] += 1
            continue
        if not isinstance(loaded, Mapping):
            skipped["invalid_record"] += 1
            continue
        run = dict(loaded)
        if not _matches_summary(run, summary, start=start, end=end):
            skipped["invalid_record"] += 1
            continue
        actor = run.get("actor") if isinstance(run.get("actor"), Mapping) else {}
        if not str(actor.get("tenant_id") or "").strip():
            skipped["tenant_unavailable"] += 1
            continue
        models = run.get("models") if isinstance(run.get("models"), list) else []
        if len(models) > _MAX_MODELS_PER_RUN:
            skipped["model_limit"] += 1
            continue
        if not models:
            skipped["no_model_events"] += 1
            continue
        runs_eligible += 1
        events_planned += len(models)
        if not apply:
            continue
        try:
            applied = event_writer(run)
            if (
                isinstance(applied, bool)
                or not isinstance(applied, int)
                or applied < 0
                or applied > len(models)
            ):
                raise ValueError("unsafe writer result")
        except Exception:
            skipped["write_failed"] += 1
            continue
        events_applied += applied

    pages_scanned = (
        (len(selected) + int(page_size) - 1) // int(page_size)
        if selected
        else 0
    )
    status = "partial" if skipped.get("write_failed") else "completed"
    return {
        "status": status,
        "mode": "apply" if apply else "dry_run",
        "window": {"from": _iso(start), "to": _iso(end)},
        "page_size": int(page_size),
        "max_pages": int(max_pages),
        "pages_scanned": pages_scanned,
        "runs_scanned": len(selected),
        "runs_eligible": runs_eligible,
        "events_planned": events_planned,
        "events_applied": events_applied,
        "skipped": dict(sorted(skipped.items())),
        "next_cursor": _encode_cursor(next_offset) if has_more else None,
        "has_more": has_more,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    summary_loader: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
    run_loader: Callable[[str], Mapping[str, Any]] | None = None,
    event_writer: Callable[[dict[str, object]], int] | None = None,
    output: Callable[[str], None] = print,
) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.apply and args.confirm != _APPLY_CONFIRMATION:
        output(
            json.dumps(
                {"status": "failed", "category": "confirmation_required"},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    try:
        if summary_loader is None or run_loader is None:
            from ..run_store import get_run, list_runs

            summary_loader = summary_loader or list_runs
            run_loader = run_loader or get_run
        writer = event_writer
        if writer is None:
            writer = _production_writer() if args.apply else lambda _run: 0
        result = repair_completed_run_actor_refs(
            run_summaries=summary_loader(),
            run_loader=run_loader,
            event_writer=writer,
            from_value=args.from_value,
            to_value=args.to_value,
            cursor=args.cursor,
            page_size=args.page_size,
            max_pages=args.max_pages,
            apply=args.apply,
        )
    except ActorRefRepairInputError as exc:
        output(
            json.dumps(
                {"status": "failed", "category": exc.category},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    except Exception:
        output(
            json.dumps(
                {"status": "failed", "category": "repair_unavailable"},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 1
    output(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["status"] == "completed" else 1


def _candidate_summaries(
    values: Sequence[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, object]]:
    candidates: dict[str, dict[str, object]] = {}
    for raw in values:
        if not isinstance(raw, Mapping) or not _terminal(raw.get("status")):
            continue
        run_id = str(raw.get("run_id") or "").strip()
        workspace_id = str(raw.get("workspace_id") or "").strip()
        completed_at = _optional_utc(
            raw.get("completed_at") or raw.get("time") or raw.get("updated_at")
        )
        if (
            not run_id
            or not workspace_id
            or completed_at is None
            or completed_at < start
            or completed_at >= end
        ):
            continue
        candidates.setdefault(
            run_id,
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "completed_at": completed_at,
            },
        )
    return sorted(
        candidates.values(),
        key=lambda item: (item["completed_at"], item["run_id"]),
    )


def _matches_summary(
    run: Mapping[str, Any],
    summary: Mapping[str, Any],
    *,
    start: datetime,
    end: datetime,
) -> bool:
    completed_at = _optional_utc(
        run.get("completed_at") or run.get("time") or run.get("updated_at")
    )
    return bool(
        str(run.get("run_id") or "").strip() == summary["run_id"]
        and str(run.get("workspace_id") or "").strip() == summary["workspace_id"]
        and _terminal(run.get("status"))
        and completed_at is not None
        and completed_at == summary["completed_at"]
        and start <= completed_at < end
    )


def _production_writer() -> Callable[[dict[str, object]], int]:
    from ..lineage_sql import build_lineage_sql_connection_factory
    from .ingestion import ingest_completed_run
    from .sql_repository import SqlFinOpsRepository

    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not secret or not _enabled("DF_FINOPS_SQL_ENABLED"):
        raise RuntimeError("repair unavailable")
    repository = SqlFinOpsRepository(
        connection_factory=build_lineage_sql_connection_factory()
    )

    def write(run: dict[str, object]) -> int:
        result = ingest_completed_run(
            run,
            repository=repository,
            hmac_secret=secret,
        )
        if result.get("status") != "ingested":
            raise RuntimeError("repair unavailable")
        return int(result.get("events") or 0)

    return write


def _cursor_offset(value: str | None) -> int:
    if value in (None, ""):
        return 0
    matched = _CURSOR.fullmatch(str(value))
    if matched is None:
        raise ActorRefRepairInputError("invalid_cursor")
    return int(matched.group(1))


def _encode_cursor(offset: int) -> str:
    if offset < 0 or offset > 99_999_999:
        raise ActorRefRepairInputError("cursor_out_of_range")
    return f"offset_{offset:08d}"


def _terminal(value: object) -> bool:
    status = str(value or "").strip().lower()
    return status.startswith("completed") or status in _TERMINAL_STATUSES


def _utc(value: object, category: str) -> datetime:
    parsed = _optional_utc(value)
    if parsed is None:
        raise ActorRefRepairInputError(category)
    return parsed


def _optional_utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _enabled(name: str) -> bool:
    return str(os.environ.get(name) or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply a bounded canonical FinOps actor-ref repair."
    )
    parser.add_argument("--from", dest="from_value", required=True)
    parser.add_argument("--to", dest="to_value", required=True)
    parser.add_argument("--cursor")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ActorRefRepairInputError",
    "main",
    "repair_completed_run_actor_refs",
]
