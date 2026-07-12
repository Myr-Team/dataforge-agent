from __future__ import annotations

import json
import math
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

try:
    from .blob_store import download_blob_json, upload_blob_json
    from .identity import public_actor
except ImportError:
    from blob_store import download_blob_json, upload_blob_json
    from identity import public_actor


ROOT = Path(__file__).resolve().parents[1]
OUTCOME_DIR = ROOT / "generated-outputs" / "outcomes"
OUTCOME_BLOB_PREFIX = "outcomes"

_LOCK = threading.RLock()
_PROVENANCE = {"assumption", "target", "observed", "synthetic"}
_SOURCE_KEYS = (
    "file_id",
    "file_version",
    "connector_id",
    "run_id",
    "artifact_id",
    "query_hash",
    "table_name",
)


def record_outcome_event(
    workspace_id: str,
    payload: Mapping[str, Any],
    actor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_workspace = _required_text(workspace_id, "workspace_id", 160)
    data = dict(payload or {})
    verification = data.get("verification")
    if isinstance(verification, Mapping) and str(verification.get("status") or "").lower() == "verified":
        raise ValueError("verified outcomes require a separate reviewer action")

    provenance = _required_text(data.get("provenance"), "provenance", 24).lower()
    if provenance not in _PROVENANCE:
        raise ValueError(f"provenance must be one of {sorted(_PROVENANCE)}")
    source = _normalize_source(data.get("source"))
    if provenance == "observed" and not source:
        raise ValueError("observed outcomes require source lineage")

    baseline = _optional_number(data.get("baseline_value"), "baseline_value")
    target = _optional_number(data.get("target_value"), "target_value")
    observed = _optional_number(data.get("observed_value"), "observed_value")
    observed_at = _optional_text(data.get("observed_at"), 64)
    if provenance == "observed" and (observed is None or not observed_at):
        raise ValueError("observed outcomes require observed_value and observed_at")

    now = _now()
    event = {
        "event_id": f"outcome_{uuid4().hex[:16]}",
        "workspace_id": normalized_workspace,
        "metric_name": _required_text(data.get("metric_name"), "metric_name", 120),
        "unit": _required_text(data.get("unit"), "unit", 40),
        "baseline_value": baseline,
        "target_value": target,
        "observed_value": observed,
        "observed_at": observed_at,
        "attribution_window_days": _optional_nonnegative_int(
            data.get("attribution_window_days"),
            "attribution_window_days",
            maximum=3650,
        ),
        "provenance": provenance,
        "source": source,
        "actor": public_actor(dict(actor or {})),
        "verification": {"status": "unverified"},
        "created_at": now,
        "updated_at": now,
    }
    event = {key: value for key, value in event.items() if value is not None}
    with _LOCK:
        events = list_outcome_events(normalized_workspace)
        events.append(event)
        _persist(normalized_workspace, events)
    return event


def verify_outcome_event(
    workspace_id: str,
    event_id: str,
    reviewer: Mapping[str, Any] | None,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    normalized_workspace = _required_text(workspace_id, "workspace_id", 160)
    normalized_event_id = _required_text(event_id, "event_id", 80)
    clean_reviewer = public_actor(dict(reviewer or {}))
    if not clean_reviewer.get("actor_id") and not clean_reviewer.get("email"):
        raise ValueError("reviewer identity is required")

    with _LOCK:
        events = list_outcome_events(normalized_workspace)
        matched: dict[str, Any] | None = None
        now = _now()
        for item in events:
            if str(item.get("event_id") or "") != normalized_event_id:
                continue
            if item.get("provenance") != "observed":
                raise ValueError("only observed outcomes can be verified")
            item["verification"] = {
                "status": "verified",
                "verified_at": now,
                "reviewer": clean_reviewer,
                **({"note": _optional_text(note, 500)} if _optional_text(note, 500) else {}),
            }
            item["updated_at"] = now
            matched = item
            break
        if matched is None:
            raise FileNotFoundError(normalized_event_id)
        _persist(normalized_workspace, events)
        return matched


def list_outcome_events(workspace_id: str) -> list[dict[str, Any]]:
    normalized_workspace = _required_text(workspace_id, "workspace_id", 160)
    by_id: dict[str, dict[str, Any]] = {}
    path = _local_path(normalized_workspace)
    if path.exists():
        try:
            local = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            local = {}
        for item in local.get("events") or []:
            if isinstance(item, dict) and item.get("event_id"):
                by_id[str(item["event_id"])] = item
    remote = download_blob_json(_blob_name(normalized_workspace)) or {}
    for item in remote.get("events") or []:
        if isinstance(item, dict) and item.get("event_id"):
            current = by_id.get(str(item["event_id"]))
            if current is None or str(item.get("updated_at") or "") > str(current.get("updated_at") or ""):
                by_id[str(item["event_id"])] = item
    return sorted(by_id.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _persist(workspace_id: str, events: list[dict[str, Any]]) -> None:
    value = {
        "version": 1,
        "workspace_id": workspace_id,
        "updated_at": _now(),
        "events": sorted(events, key=lambda item: str(item.get("created_at") or ""), reverse=True),
    }
    path = _local_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    try:
        upload_blob_json(_blob_name(workspace_id), value)
    except Exception:
        pass


def _normalize_source(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    source: dict[str, str] = {}
    for key in _SOURCE_KEYS:
        text = _optional_text(value.get(key), 240)
        if text:
            source[key] = text
    return source


def _local_path(workspace_id: str) -> Path:
    return OUTCOME_DIR / f"{_safe_key(workspace_id)}.json"


def _blob_name(workspace_id: str) -> str:
    return f"{OUTCOME_BLOB_PREFIX}/{_safe_key(workspace_id)}.json"


def _safe_key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")[:160]
    if not clean:
        raise ValueError("workspace_id is invalid")
    return clean


def _required_text(value: Any, field: str, limit: int) -> str:
    text = _optional_text(value, limit)
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _optional_text(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _optional_number(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _optional_nonnegative_int(value: Any, field: str, *, maximum: int) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if number < 0 or number > maximum:
        raise ValueError(f"{field} must be between 0 and {maximum}")
    return number


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["list_outcome_events", "record_outcome_event", "verify_outcome_event"]
