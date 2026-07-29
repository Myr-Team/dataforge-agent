from __future__ import annotations

"""Bounded repair of historical FinOps tenant/actor attribution from retained runs.

The command is dry-run by default. Public output is aggregate-only: raw run,
tenant, actor, request, email, and provider identifiers are never returned.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any


_CURSOR = re.compile(r"^cur_v1_([A-Za-z0-9_-]+)\.([0-9a-f]{64})$")
_SNAPSHOT = re.compile(r"^snap_v1_([0-9a-f]{64})$")
_APPLY_CONFIRMATION = "APPLY_CANONICAL_ACTOR_REF_REPAIR"
_MAX_WINDOW = timedelta(days=90)
_MAX_PAGE_SIZE = 200
_MAX_PAGES = 20
_MAX_SOURCE_SUMMARIES = 100_000
_MAX_MODELS_PER_RUN = 64
_MAX_MODEL_CONTENT_BYTES_PER_RUN = 512_000
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


def build_event_key_repairs(
    run: Mapping[str, Any],
    *,
    hmac_secret: str,
) -> list[Any]:
    from .normalization import (
        canonical_tenant_ref,
        normalize_run_event,
        opaque_ref,
    )
    from .repository import FinOpsEventKeyRepair

    secret = str(hmac_secret or "").strip()
    actor = run.get("actor") if isinstance(run.get("actor"), Mapping) else {}
    legacy_tenant_id = str(actor.get("tenant_id") or "").strip()
    run_id = str(run.get("run_id") or "").strip()
    workspace_id = str(run.get("workspace_id") or "").strip()
    models = run.get("models") if isinstance(run.get("models"), list) else []
    if (
        not secret
        or not legacy_tenant_id
        or not run_id
        or not workspace_id
        or len(models) > _MAX_MODELS_PER_RUN
    ):
        raise ActorRefRepairInputError("invalid_record")
    legacy_tenant_ref = opaque_ref(
        "tenant",
        legacy_tenant_id,
        secret=secret,
    )
    tenant_ref = canonical_tenant_ref(
        legacy_tenant_id,
        secret=secret,
    )
    plans: list[FinOpsEventKeyRepair] = []
    for index in range(len(models)):
        canonical_event = normalize_run_event(
            run,
            model_index=index,
            tenant_id=tenant_ref,
            raw_tenant_id=legacy_tenant_id,
            hmac_secret=secret,
        )
        plans.append(
            FinOpsEventKeyRepair(
                legacy_tenant_ref=legacy_tenant_ref,
                legacy_request_ref=opaque_ref(
                    "req",
                    legacy_tenant_ref,
                    workspace_id,
                    run_id,
                    index,
                    secret=secret,
                ),
                canonical_event=canonical_event,
            )
        )
    return plans


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
    snapshot_secret: str,
    snapshot_token: str | None = None,
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
    secret = str(snapshot_secret or "").strip()
    if not secret:
        raise ActorRefRepairInputError("snapshot_secret_required")
    if apply and not snapshot_token:
        raise ActorRefRepairInputError("snapshot_required")
    if len(run_summaries) > _MAX_SOURCE_SUMMARIES:
        raise ActorRefRepairInputError("source_limit_exceeded")

    candidates = _candidate_summaries(
        run_summaries,
        start=start,
        end=end,
        secret=secret,
    )
    catalog_digest = _catalog_digest(
        candidates,
        start=start,
        end=end,
        secret=secret,
    )
    boundary = _decode_cursor(
        cursor,
        start=start,
        end=end,
        catalog_digest=catalog_digest,
        secret=secret,
    )
    remaining = [
        item
        for item in candidates
        if boundary is None or _candidate_key(item) > boundary
    ]
    batch_limit = int(page_size) * int(max_pages)
    selected = remaining[:batch_limit]
    has_more = len(remaining) > len(selected)
    skipped: Counter[str] = Counter()
    runs_eligible = 0
    events_planned = 0
    events_applied = 0
    eligible_runs: list[dict[str, object]] = []
    snapshot_records: list[dict[str, object]] = []

    for summary in selected:
        run_id = summary["run_id"]
        try:
            loaded = run_loader(run_id)
        except Exception:
            skipped["detail_unavailable"] += 1
            snapshot_records.append(
                _snapshot_skip_record(summary, "detail_unavailable")
            )
            continue
        if not isinstance(loaded, Mapping):
            skipped["invalid_record"] += 1
            snapshot_records.append(_snapshot_skip_record(summary, "invalid_record"))
            continue
        run = dict(loaded)
        if not _matches_summary(run, summary, start=start, end=end):
            skipped["invalid_record"] += 1
            snapshot_records.append(_snapshot_skip_record(summary, "invalid_record"))
            continue
        actor = run.get("actor") if isinstance(run.get("actor"), Mapping) else {}
        if not str(actor.get("tenant_id") or "").strip():
            skipped["tenant_unavailable"] += 1
            snapshot_records.append(
                _snapshot_skip_record(summary, "tenant_unavailable")
            )
            continue
        models = run.get("models") if isinstance(run.get("models"), list) else []
        if len(models) > _MAX_MODELS_PER_RUN:
            skipped["model_limit"] += 1
            snapshot_records.append(_snapshot_skip_record(summary, "model_limit"))
            continue
        if not models:
            skipped["no_model_events"] += 1
            snapshot_records.append(
                _snapshot_skip_record(summary, "no_model_events")
            )
            continue
        record = _snapshot_record(run, summary, state="eligible")
        if len(_canonical_json(record)) > _MAX_MODEL_CONTENT_BYTES_PER_RUN:
            skipped["model_content_limit"] += 1
            snapshot_records.append(
                _snapshot_skip_record(summary, "model_content_limit")
            )
            continue
        snapshot_records.append(record)
        eligible_runs.append(run)
        runs_eligible += 1
        events_planned += len(models)

    computed_snapshot = _snapshot_token(
        records=snapshot_records,
        start=start,
        end=end,
        catalog_digest=catalog_digest,
        boundary=boundary,
        page_size=int(page_size),
        max_pages=int(max_pages),
        secret=secret,
    )
    if apply and not _snapshot_matches(snapshot_token, computed_snapshot):
        raise ActorRefRepairInputError("snapshot_mismatch")

    if apply:
        for run in eligible_runs:
            models = run.get("models") if isinstance(run.get("models"), list) else []
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

    next_cursor = (
        _encode_cursor(
            _candidate_key(selected[-1]),
            start=start,
            end=end,
            catalog_digest=catalog_digest,
            secret=secret,
        )
        if has_more and selected
        else None
    )

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
        "snapshot_token": computed_snapshot,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    summary_loader: Callable[[], Sequence[Mapping[str, Any]]] | None = None,
    run_loader: Callable[[str], Mapping[str, Any]] | None = None,
    event_writer: Callable[[dict[str, object]], int] | None = None,
    snapshot_secret: str | None = None,
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
        secret = str(
            snapshot_secret
            or os.environ.get("DF_FINOPS_HMAC_SECRET")
            or ""
        ).strip()
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
            snapshot_secret=secret,
            snapshot_token=args.snapshot_token,
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
    secret: str,
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
        candidate = {
            "run_id": run_id,
            "run_key": _digest(secret, "run-key", run_id),
            "workspace_id": workspace_id,
            "completed_at": completed_at,
            "status": str(raw.get("status") or "").strip().lower(),
        }
        existing = candidates.get(run_id)
        if existing is not None and existing != candidate:
            raise ActorRefRepairInputError("source_conflict")
        candidates[run_id] = candidate
    return sorted(
        candidates.values(),
        key=_candidate_key,
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
        and str(run.get("status") or "").strip().lower() == summary["status"]
        and _terminal(run.get("status"))
        and completed_at is not None
        and completed_at == summary["completed_at"]
        and start <= completed_at < end
    )


def _production_writer() -> Callable[[dict[str, object]], int]:
    from ..lineage_sql import build_lineage_sql_connection_factory
    from .sql_repository import SqlFinOpsRepository

    secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not secret or not _enabled("DF_FINOPS_SQL_ENABLED"):
        raise RuntimeError("repair unavailable")
    repository = SqlFinOpsRepository(
        connection_factory=build_lineage_sql_connection_factory()
    )

    def write(run: dict[str, object]) -> int:
        return repository.repair_event_keys(
            build_event_key_repairs(
                run,
                hmac_secret=secret,
            )
        )

    return write


def _candidate_key(value: Mapping[str, object]) -> tuple[str, str]:
    completed_at = value.get("completed_at")
    if not isinstance(completed_at, datetime):
        raise ActorRefRepairInputError("invalid_record")
    return (_iso(completed_at), str(value.get("run_key") or ""))


def _catalog_digest(
    candidates: Sequence[Mapping[str, object]],
    *,
    start: datetime,
    end: datetime,
    secret: str,
) -> str:
    material = {
        "version": 1,
        "window": {"from": _iso(start), "to": _iso(end)},
        "candidates": [
            {
                "run_id": item["run_id"],
                "workspace_id": item["workspace_id"],
                "completed_at": _iso(item["completed_at"]),  # type: ignore[arg-type]
                "status": item["status"],
            }
            for item in candidates
        ],
    }
    return _digest_bytes(secret, "catalog", _canonical_json(material))


def _snapshot_skip_record(
    summary: Mapping[str, object],
    state: str,
) -> dict[str, object]:
    return {
        "summary": {
            "run_id": summary["run_id"],
            "workspace_id": summary["workspace_id"],
            "completed_at": _iso(summary["completed_at"]),  # type: ignore[arg-type]
            "status": summary["status"],
        },
        "state": state,
    }


def _snapshot_record(
    run: Mapping[str, Any],
    summary: Mapping[str, object],
    *,
    state: str,
) -> dict[str, object]:
    actor = run.get("actor") if isinstance(run.get("actor"), Mapping) else {}
    models = run.get("models") if isinstance(run.get("models"), list) else []
    return {
        "summary": {
            "run_id": summary["run_id"],
            "workspace_id": summary["workspace_id"],
            "completed_at": _iso(summary["completed_at"]),  # type: ignore[arg-type]
            "status": summary["status"],
        },
        "state": state,
        "repair_input": {
            "run_id": run.get("run_id"),
            "workspace_id": run.get("workspace_id"),
            "status": run.get("status"),
            "completed_at": run.get("completed_at"),
            "time": run.get("time"),
            "updated_at": run.get("updated_at"),
            "started_at": run.get("started_at"),
            "raw_tenant_id": actor.get("tenant_id"),
            "raw_actor_id": actor.get("actor_id"),
            "trace": run.get("trace"),
            "apim_correlation_id": run.get("apim_correlation_id"),
            "correlation_id": run.get("correlation_id"),
            "trace_id": run.get("trace_id"),
            "model_count": len(models),
            "models": models,
        },
    }


def _snapshot_token(
    *,
    records: Sequence[Mapping[str, object]],
    start: datetime,
    end: datetime,
    catalog_digest: str,
    boundary: tuple[str, str] | None,
    page_size: int,
    max_pages: int,
    secret: str,
) -> str:
    material = {
        "version": 1,
        "window": {"from": _iso(start), "to": _iso(end)},
        "catalog_digest": catalog_digest,
        "boundary": list(boundary) if boundary is not None else None,
        "page_size": page_size,
        "max_pages": max_pages,
        "records": list(records),
    }
    digest = _digest_bytes(secret, "snapshot", _canonical_json(material))
    return f"snap_v1_{digest}"


def _snapshot_matches(provided: str | None, computed: str) -> bool:
    value = str(provided or "")
    if _SNAPSHOT.fullmatch(value) is None:
        return False
    return hmac.compare_digest(value, computed)


def _encode_cursor(
    boundary: tuple[str, str],
    *,
    start: datetime,
    end: datetime,
    catalog_digest: str,
    secret: str,
) -> str:
    payload = {
        "version": 1,
        "window": {"from": _iso(start), "to": _iso(end)},
        "catalog_digest": catalog_digest,
        "after_completed_at": boundary[0],
        "after_run_key": boundary[1],
    }
    raw = _canonical_json(payload)
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = _digest_bytes(secret, "cursor", raw)
    return f"cur_v1_{encoded}.{signature}"


def _decode_cursor(
    value: str | None,
    *,
    start: datetime,
    end: datetime,
    catalog_digest: str,
    secret: str,
) -> tuple[str, str] | None:
    if value in (None, ""):
        return None
    matched = _CURSOR.fullmatch(str(value))
    if matched is None:
        raise ActorRefRepairInputError("invalid_cursor")
    encoded, signature = matched.groups()
    try:
        raw = base64.urlsafe_b64decode(
            encoded + ("=" * (-len(encoded) % 4))
        )
    except (ValueError, TypeError):
        raise ActorRefRepairInputError("invalid_cursor") from None
    expected = _digest_bytes(secret, "cursor", raw)
    if not hmac.compare_digest(signature, expected):
        raise ActorRefRepairInputError("invalid_cursor")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ActorRefRepairInputError("invalid_cursor") from None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ActorRefRepairInputError("invalid_cursor")
    window = payload.get("window")
    if not isinstance(window, dict) or window != {
        "from": _iso(start),
        "to": _iso(end),
    }:
        raise ActorRefRepairInputError("cursor_context_mismatch")
    if payload.get("catalog_digest") != catalog_digest:
        raise ActorRefRepairInputError("source_snapshot_changed")
    completed_at = str(payload.get("after_completed_at") or "")
    run_key = str(payload.get("after_run_key") or "")
    if _optional_utc(completed_at) is None or not re.fullmatch(
        r"[0-9a-f]{64}",
        run_key,
    ):
        raise ActorRefRepairInputError("invalid_cursor")
    return (completed_at, run_key)


def _digest(secret: str, label: str, *parts: object) -> str:
    return _digest_bytes(
        secret,
        label,
        "\x1f".join(str(part) for part in parts).encode("utf-8"),
    )


def _digest_bytes(secret: str, label: str, value: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        label.encode("ascii") + b"\0" + value,
        hashlib.sha256,
    ).hexdigest()


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
            default=_json_default,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ActorRefRepairInputError("invalid_record") from None


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise TypeError("naive datetime")
        return _iso(value)
    raise TypeError(type(value).__name__)


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
        description="Dry-run or apply a bounded canonical FinOps identity repair."
    )
    parser.add_argument("--from", dest="from_value", required=True)
    parser.add_argument("--to", dest="to_value", required=True)
    parser.add_argument("--cursor")
    parser.add_argument("--snapshot-token")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ActorRefRepairInputError",
    "build_event_key_repairs",
    "main",
    "repair_completed_run_actor_refs",
]
