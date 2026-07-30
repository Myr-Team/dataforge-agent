from __future__ import annotations

import json
import math
import re
import threading
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

try:
    from .blob_store import download_blob_json, upload_blob_json
    from .artifact_jobs import get_artifact_job
    from .identity import canonical_actor_identity, is_trusted_tenant_identity, public_actor
    from .run_store import get_run
    from .workspace_store import get_workspace_detail
except ImportError:
    from blob_store import download_blob_json, upload_blob_json
    from artifact_jobs import get_artifact_job
    from identity import canonical_actor_identity, is_trusted_tenant_identity, public_actor
    from run_store import get_run
    from workspace_store import get_workspace_detail


ROOT = Path(__file__).resolve().parents[1]
OUTCOME_DIR = ROOT / "generated-outputs" / "outcomes"
OUTCOME_BLOB_PREFIX = "outcomes"

_LOCK = threading.RLock()
_PROVENANCE = {"assumption", "target", "observed", "synthetic"}
_VERIFIED_OUTCOME_TRIGGER: Callable[[dict[str, Any]], None] | None = None
_SOURCE_KEYS = (
    "file_id",
    "file_version",
    "connector_id",
    "connector_version",
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
    if provenance == "observed" and not source_is_valid(normalized_workspace, source):
        raise ValueError("observed outcomes require a real same-workspace source")

    baseline = _optional_number(data.get("baseline_value"), "baseline_value")
    target = _optional_number(data.get("target_value"), "target_value")
    observed = _optional_number(data.get("observed_value"), "observed_value")
    observed_at = _optional_text(data.get("observed_at"), 64)
    if provenance == "observed" and (observed is None or not observed_at):
        raise ValueError("observed outcomes require observed_value and observed_at")
    business_value = _normalize_business_value(data.get("business_value"))

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
        "trusted_identity": is_trusted_tenant_identity(actor),
        "verification": {"status": "unverified"},
        "business_value": business_value,
        "created_at": now,
        "updated_at": now,
    }
    event = {key: value for key, value in event.items() if value is not None}
    with _LOCK:
        events = list_outcome_events(normalized_workspace)
        events.append(event)
        _persist(normalized_workspace, events, list_verification_events(normalized_workspace))
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
    reviewer_identity = canonical_actor_identity(reviewer)
    if not reviewer_identity or not is_trusted_tenant_identity(reviewer):
        raise ValueError("reviewer must have a trusted tenant actor_id")

    with _LOCK:
        events = list_outcome_events(normalized_workspace)
        verification_events = list_verification_events(normalized_workspace)
        matched: dict[str, Any] | None = None
        now = _now()
        for item in events:
            if str(item.get("event_id") or "") != normalized_event_id:
                continue
            if item.get("provenance") != "observed":
                raise ValueError("only observed outcomes can be verified")
            if not _normalize_source(item.get("source")) or item.get("observed_value") is None:
                raise ValueError("only source-linked observed outcomes can be verified")
            actor = item.get("actor") if isinstance(item.get("actor"), Mapping) else {}
            outcome_identity = canonical_actor_identity(actor)
            if not item.get("trusted_identity") or not outcome_identity:
                raise ValueError("outcome actor must have a trusted tenant identity")
            if reviewer_identity == outcome_identity:
                raise ValueError("verification requires an independent reviewer")
            if str((item.get("verification") or {}).get("status") or "").lower() == "verified":
                raise ValueError("outcome is already verified")
            verification_event_id = f"verification_{uuid4().hex[:16]}"
            verification_event = {
                "event_id": verification_event_id,
                "workspace_id": normalized_workspace,
                "kind": "outcome_verification",
                "outcome_event_id": normalized_event_id,
                "actor": clean_reviewer,
                "trusted_identity": True,
                "created_at": now,
                **({"note": _optional_text(note, 500)} if _optional_text(note, 500) else {}),
            }
            item["verification"] = {
                "status": "verified",
                "verification_event_id": verification_event_id,
                "verified_at": now,
                "reviewer": clean_reviewer,
                "trusted_identity": True,
                "event": verification_event,
                **({"note": _optional_text(note, 500)} if _optional_text(note, 500) else {}),
            }
            item["updated_at"] = now
            verification_events.append(verification_event)
            matched = item
            break
        if matched is None:
            raise FileNotFoundError(normalized_event_id)
        _persist(normalized_workspace, events, verification_events)
        result = dict(matched)
    if _VERIFIED_OUTCOME_TRIGGER is not None:
        try:
            _VERIFIED_OUTCOME_TRIGGER(result)
        except Exception:
            pass
    return result


def configure_verified_outcome_trigger(
    callback: Callable[[dict[str, Any]], None] | None,
) -> None:
    global _VERIFIED_OUTCOME_TRIGGER
    _VERIFIED_OUTCOME_TRIGGER = callback


def list_outcome_events(workspace_id: str) -> list[dict[str, Any]]:
    normalized_workspace = _required_text(workspace_id, "workspace_id", 160)
    return _state_items(normalized_workspace, "events")


def list_verification_events(workspace_id: str) -> list[dict[str, Any]]:
    normalized_workspace = _required_text(workspace_id, "workspace_id", 160)
    return _state_items(normalized_workspace, "verification_events")


def upsert_demo_outcome_events(
    workspace_id: str,
    values: tuple[Mapping[str, Any], ...],
    *,
    seed_key: str,
) -> dict[str, Any]:
    """Replace internally-owned demo outcomes without fabricating verification."""
    normalized_workspace = _required_text(
        workspace_id, "workspace_id", 160
    )
    normalized_seed = _required_text(seed_key, "seed_key", 64)
    now = _now()
    prepared: list[dict[str, Any]] = []
    for value in values:
        metric_name = _required_text(
            value.get("metric_name"), "metric_name", 120
        )
        unit = _required_text(value.get("unit"), "unit", 40)
        source = _normalize_source(value.get("source"))
        observed_value = _optional_number(
            value.get("observed_value"), "observed_value"
        )
        observed_at = _required_text(
            value.get("observed_at"), "observed_at", 64
        )
        if (
            str(value.get("provenance") or "").strip() != "observed"
            or observed_value is None
            or not source
            or not source_is_valid(normalized_workspace, source)
        ):
            raise ValueError(
                "demo outcomes require source-linked observed evidence"
            )
        digest = hashlib.sha256(
            (
                f"{normalized_workspace}\0operations_demo\0"
                f"{metric_name}\0{unit}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        prepared.append(
            {
                "event_id": f"outcome_demo_{digest}",
                "workspace_id": normalized_workspace,
                "metric_name": metric_name,
                "unit": unit,
                "baseline_value": _optional_number(
                    value.get("baseline_value"), "baseline_value"
                ),
                "target_value": _optional_number(
                    value.get("target_value"), "target_value"
                ),
                "observed_value": observed_value,
                "observed_at": observed_at,
                "attribution_window_days": _optional_nonnegative_int(
                    value.get("attribution_window_days"),
                    "attribution_window_days",
                    maximum=3650,
                ),
                "provenance": "observed",
                "source": source,
                "actor": {},
                "trusted_identity": False,
                "verification": {"status": "unverified"},
                "created_at": now,
                "updated_at": now,
                "demo_seed": {
                    "owner": "operations_demo",
                    "batch": normalized_seed,
                },
            }
        )
    prepared = [
        {key: item for key, item in value.items() if item is not None}
        for value in prepared
    ]
    with _LOCK:
        current = list_outcome_events(normalized_workspace)
        existing = {
            str(item.get("event_id") or ""): item
            for item in current
            if isinstance(item.get("demo_seed"), Mapping)
            and item["demo_seed"].get("owner") == "operations_demo"
        }
        preserved = [
            item
            for item in current
            if str(item.get("event_id") or "") not in existing
        ]
        for item in prepared:
            previous = existing.get(item["event_id"])
            if previous is not None:
                item["created_at"] = previous.get("created_at") or now
        _persist(
            normalized_workspace,
            [*preserved, *prepared],
            list_verification_events(normalized_workspace),
        )
    identifiers = {item["event_id"] for item in prepared}
    return {
        "created": len(identifiers - set(existing)),
        "updated": len(identifiers & set(existing)),
        "seed_batch": normalized_seed,
    }


def outcome_is_authoritative(workspace_id: str, candidate: Mapping[str, Any]) -> bool:
    """Return whether an outcome has persisted, independently verified server authority."""
    try:
        normalized_workspace = _required_text(workspace_id, "workspace_id", 160)
        event_id = _required_text(candidate.get("event_id"), "event_id", 80)
        persisted = next(
            (
                item
                for item in list_outcome_events(normalized_workspace)
                if str(item.get("event_id") or "") == event_id
            ),
            None,
        )
        if not persisted or persisted.get("provenance") != "observed":
            return False
        source = _normalize_source(persisted.get("source"))
        if not source or source != _normalize_source(candidate.get("source")):
            return False
        if not source_is_valid(normalized_workspace, source):
            return False
        for field in ("workspace_id", "metric_name", "unit", "observed_value", "observed_at", "provenance"):
            if persisted.get(field) != candidate.get(field):
                return False
        verification = persisted.get("verification") if isinstance(persisted.get("verification"), Mapping) else {}
        verification_event_id = str(verification.get("verification_event_id") or "").strip()
        if (
            str(verification.get("status") or "").strip().lower() not in {"verified", "passed"}
            or verification.get("trusted_identity") is not True
            or not verification_event_id
        ):
            return False
        return any(
            str(item.get("event_id") or "") == verification_event_id
            and str(item.get("outcome_event_id") or "") == event_id
            and item.get("kind") == "outcome_verification"
            and item.get("trusted_identity") is True
            for item in list_verification_events(normalized_workspace)
        )
    except Exception:
        return False


def _state_items(workspace_id: str, key: str) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    path = _local_path(workspace_id)
    if path.exists():
        try:
            local = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            local = {}
        for item in local.get(key) or []:
            if isinstance(item, dict) and item.get("event_id"):
                by_id[str(item["event_id"])] = item
    remote = download_blob_json(_blob_name(workspace_id)) or {}
    for item in remote.get(key) or []:
        if isinstance(item, dict) and item.get("event_id"):
            current = by_id.get(str(item["event_id"]))
            if current is None or str(item.get("updated_at") or "") > str(current.get("updated_at") or ""):
                by_id[str(item["event_id"])] = item
    return sorted(by_id.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _persist(workspace_id: str, events: list[dict[str, Any]], verification_events: list[dict[str, Any]]) -> None:
    value = {
        "version": 2,
        "workspace_id": workspace_id,
        "updated_at": _now(),
        "events": sorted(events, key=lambda item: str(item.get("created_at") or ""), reverse=True),
        "verification_events": sorted(verification_events, key=lambda item: str(item.get("created_at") or ""), reverse=True),
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


def source_is_valid(workspace_id: str, source: Mapping[str, Any]) -> bool:
    source = _normalize_source(source)
    anchors = [key for key in ("run_id", "file_id", "artifact_id") if source.get(key)]
    if not anchors:
        return False
    for key in anchors:
        if not _source_reference_exists(workspace_id, key, str(source[key])):
            return False
    return True


def _source_reference_exists(workspace_id: str, kind: str, reference: str) -> bool:
    if kind == "run_id":
        try:
            return str(get_run(reference).get("workspace_id") or "") == workspace_id
        except (FileNotFoundError, ValueError):
            return False
    if kind == "file_id":
        try:
            detail = get_workspace_detail(workspace_id)
        except (FileNotFoundError, ValueError):
            return False
        for item in detail.get("documents") or []:
            if not isinstance(item, Mapping):
                continue
            source_file = str(item.get("source_file") or item.get("name") or "")
            file_id = hashlib.sha256(source_file.encode("utf-8")).hexdigest()[:16]
            if reference == file_id:
                return True
        return False
    if kind == "artifact_id":
        try:
            job = get_artifact_job(reference)
        except (FileNotFoundError, ValueError):
            return False
        return str(job.get("job_id") or "") == reference and str(job.get("workspace_id") or "") == workspace_id
    return False


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


def _normalize_business_value(value: Any) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    if not isinstance(value, Mapping):
        raise ValueError("business_value must be an object")
    amount = _optional_number(value.get("value"), "business_value.value")
    currency = _optional_text(value.get("currency"), 3)
    source = _optional_text(value.get("source"), 240)
    formula = _optional_text(value.get("formula"), 500)
    status = _optional_text(value.get("status"), 32)
    if amount is None or amount < 0 or not currency or not re.fullmatch(r"[A-Z]{3}", currency) or not source or not formula or not status:
        raise ValueError("business_value requires value, currency, source, formula, and status")
    return {
        "value": amount,
        "currency": currency,
        "source": source,
        "formula": formula,
        "status": status,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "configure_verified_outcome_trigger",
    "list_outcome_events",
    "list_verification_events",
    "outcome_is_authoritative",
    "record_outcome_event",
    "source_is_valid",
    "upsert_demo_outcome_events",
    "verify_outcome_event",
]
