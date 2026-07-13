from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

try:
    from .blob_store import blob_configured, compare_and_swap_blob_json, download_blob_json_strict
except ImportError:
    from blob_store import blob_configured, compare_and_swap_blob_json, download_blob_json_strict


ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "generated-outputs" / "audit"
AUDIT_BLOB_PREFIX = "audit/workspaces"
GENESIS_HASH = "0" * 64
MAX_PAGE_SIZE = 100
MAX_CAS_RETRIES = 8
LOCAL_LOCK_TIMEOUT_SECONDS = 5.0

ALLOWED_ACTIONS = frozenset(
    {
        "workspace.read",
        "workspace.delete",
        "file.read",
        "file.create",
        "file.edit",
        "file.delete",
        "run.read",
        "artifact.read",
        "member.read",
        "member.manage",
        "outcome.read",
        "chargeback.read",
        "connector.manage",
        "connector.connect",
        "connector.reconnect",
        "connector.sync",
        "connector.import",
        "connector.disconnect",
        "connector.delete",
        "analysis.run",
        "message.create",
        "task.create",
        "task.start",
        "task.cancel",
        "task.complete",
        "task.fail",
        "artifact.generate",
        "outcome.record",
        "outcome.verify",
        "invitation.create",
        "invitation.send",
        "invitation.revoke",
        "invitation.fail",
        "member.update",
        "member.remove",
        "experiment.promote",
    }
)
ALLOWED_RESOURCE_TYPES = frozenset(
    {"workspace", "file", "connector", "analysis", "message", "task", "artifact", "outcome", "invitation", "member", "experiment"}
)
ALLOWED_RESULTS = frozenset({"allowed", "denied", "failed"})
ALLOWED_REASON_CODES = frozenset(
    {
        "authorized",
        "permission_denied",
        "operation_failed",
        "persistence_failed",
        "task_completed",
        "task_cancelled",
        "task_failed",
        "connector_error",
        "invitation_failed",
        "invalid_request",
        "conflict",
        "not_found",
        "experiment_promoted",
    }
)
ALLOWED_CORRELATION_FIELDS = frozenset(
    {"request_id", "run_id", "task_id", "invitation_id", "connector_id", "outcome_event_id", "experiment_version_id"}
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_LOCK_SLEEP_SECONDS = 0.01


class AuditPersistenceError(RuntimeError):
    pass


class AuditIntegrityError(AuditPersistenceError):
    pass


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    workspace_id: str
    actor_hash: str
    action: str
    resource_type: str
    resource_id: str
    result: Literal["allowed", "denied", "failed"]
    reason_code: str | None
    correlation: dict[str, str]
    at: datetime
    revision: int
    previous_hash: str
    event_hash: str


def record_audit_event(
    actor: Mapping[str, Any] | None,
    action: str,
    resource: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
    *,
    result: str | None = None,
    reason_code: str | None = None,
    correlation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    clean_action = _allowlisted(action, ALLOWED_ACTIONS, "action")
    clean_resource = _clean_resource(resource)
    merged_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    if result is not None:
        merged_metadata["result"] = result
    if reason_code is not None:
        merged_metadata["reason_code"] = reason_code
    if correlation is not None:
        merged_metadata["correlation"] = correlation
    clean_metadata = _clean_metadata(merged_metadata)
    actor_hash = _actor_hash(actor)
    event_id = _event_id(
        clean_resource["workspace_id"],
        actor_hash,
        clean_action,
        clean_resource,
        clean_metadata,
    )
    at = _now()
    if blob_configured():
        return _append_blob(event_id, actor_hash, clean_action, clean_resource, clean_metadata, at)
    return _append_local(event_id, actor_hash, clean_action, clean_resource, clean_metadata, at)


def list_audit_events(workspace_id: str, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    workspace = _safe_id(workspace_id, "workspace_id")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    before_revision = _decode_cursor(cursor)
    if blob_configured():
        try:
            snapshot = download_blob_json(_blob_name(workspace))
        except Exception as exc:
            raise AuditPersistenceError("durable audit read failed") from exc
    else:
        snapshot = _read_local(workspace)
    revision, events = _validate_ledger(snapshot, workspace, missing_ok=True)
    descending = [event for event in reversed(events) if before_revision is None or int(event["revision"]) < before_revision]
    page = descending[:limit]
    has_more = len(descending) > limit
    next_cursor = _encode_cursor(int(page[-1]["revision"])) if page and has_more else None
    return {
        "workspace_id": workspace,
        "events": page,
        "count": len(page),
        "revision": revision,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "permissions": {"can_read": True, "can_update": False, "can_delete": False},
    }


def _append_blob(
    event_id: str,
    actor_hash: str,
    action: str,
    resource: dict[str, str],
    metadata: dict[str, Any],
    at: str,
) -> dict[str, Any]:
    blob_name = _blob_name(resource["workspace_id"])
    for _attempt in range(MAX_CAS_RETRIES):
        try:
            snapshot = download_blob_json(blob_name)
        except Exception as exc:
            raise AuditPersistenceError("durable audit read failed") from exc
        revision, events = _validate_ledger(snapshot, resource["workspace_id"], missing_ok=True)
        existing = _event_by_id(events, event_id)
        if existing is not None:
            return existing
        previous_hash = events[-1]["event_hash"] if events else GENESIS_HASH
        event = _build_event(
            {"actor_hash": actor_hash},
            action,
            resource,
            metadata,
            revision=revision + 1,
            previous_hash=previous_hash,
            event_id=event_id,
            at=at,
        )
        changes = {"revision": revision + 1, "events": [*events, event]}
        try:
            committed = compare_and_swap_blob_json(blob_name, expected_revision=revision, changes=changes)
        except Exception as exc:
            raise AuditPersistenceError("durable audit append failed") from exc
        if committed is not None:
            committed_revision, committed_events = _validate_ledger(committed, resource["workspace_id"])
            if committed_revision != revision + 1:
                raise AuditIntegrityError("durable audit revision mismatch")
            persisted = _event_by_id(committed_events, event_id)
            if persisted is None:
                raise AuditIntegrityError("durable audit append was not retained")
            return persisted
    raise AuditPersistenceError("durable audit append conflict")


def _append_local(
    event_id: str,
    actor_hash: str,
    action: str,
    resource: dict[str, str],
    metadata: dict[str, Any],
    at: str,
) -> dict[str, Any]:
    workspace_id = resource["workspace_id"]
    with _local_lock(workspace_id):
        snapshot = _read_local(workspace_id)
        revision, events = _validate_ledger(snapshot, workspace_id, missing_ok=True)
        existing = _event_by_id(events, event_id)
        if existing is not None:
            return existing
        event = _build_event(
            {"actor_hash": actor_hash},
            action,
            resource,
            metadata,
            revision=revision + 1,
            previous_hash=events[-1]["event_hash"] if events else GENESIS_HASH,
            event_id=event_id,
            at=at,
        )
        _write_local(workspace_id, {"revision": revision + 1, "events": [*events, event]})
        return event


def _build_event(
    actor: Mapping[str, Any] | None,
    action: str,
    resource: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    *,
    revision: int,
    previous_hash: str,
    event_id: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    clean_resource = _clean_resource(resource)
    clean_metadata = _clean_metadata(metadata)
    actor_hash = str((actor or {}).get("actor_hash") or "") or _actor_hash(actor)
    payload: dict[str, Any] = {
        "event_id": event_id
        or _event_id(clean_resource["workspace_id"], actor_hash, action, clean_resource, clean_metadata),
        "workspace_id": clean_resource["workspace_id"],
        "actor_hash": actor_hash,
        "action": _allowlisted(action, ALLOWED_ACTIONS, "action"),
        "resource_type": clean_resource["resource_type"],
        "resource_id": clean_resource["resource_id"],
        "result": clean_metadata["result"],
        "reason_code": clean_metadata["reason_code"],
        "correlation": clean_metadata["correlation"],
        "at": at or _now(),
        "revision": int(revision),
        "previous_hash": str(previous_hash),
        "event_hash": "",
    }
    normalized = AuditEvent.model_validate(payload).model_dump(mode="json")
    normalized["event_hash"] = _hash_event(normalized)
    return AuditEvent.model_validate(normalized).model_dump(mode="json")


def _validate_ledger(
    snapshot: Mapping[str, Any] | None,
    workspace_id: str,
    *,
    missing_ok: bool = False,
) -> tuple[int, list[dict[str, Any]]]:
    if snapshot is None:
        if missing_ok:
            return 0, []
        raise AuditIntegrityError("audit ledger is unavailable")
    if not isinstance(snapshot, Mapping) or set(snapshot) != {"revision", "events"}:
        raise AuditIntegrityError("audit ledger schema is invalid")
    revision = snapshot.get("revision")
    raw_events = snapshot.get("events")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0 or not isinstance(raw_events, list):
        raise AuditIntegrityError("audit ledger schema is invalid")
    if revision != len(raw_events):
        raise AuditIntegrityError("audit ledger revision is invalid")
    events: list[dict[str, Any]] = []
    previous_hash = GENESIS_HASH
    seen: set[str] = set()
    for index, raw_event in enumerate(raw_events, start=1):
        try:
            model = AuditEvent.model_validate(raw_event)
            event = model.model_dump(mode="json")
            _validate_event_policy(model, event)
        except Exception as exc:
            raise AuditIntegrityError("audit event schema is invalid") from exc
        if event["workspace_id"] != workspace_id or event["revision"] != index:
            raise AuditIntegrityError("audit event identity is invalid")
        if event["event_id"] in seen or event["previous_hash"] != previous_hash:
            raise AuditIntegrityError("audit event chain is invalid")
        if not hmac.compare_digest(event["event_hash"], _hash_event(event)):
            raise AuditIntegrityError("audit event hash is invalid")
        seen.add(event["event_id"])
        previous_hash = event["event_hash"]
        events.append(event)
    return revision, events


def _clean_resource(resource: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(resource, Mapping):
        raise ValueError("resource must be an object")
    return {
        "workspace_id": _safe_id(resource.get("workspace_id"), "workspace_id"),
        "resource_type": _allowlisted(resource.get("resource_type"), ALLOWED_RESOURCE_TYPES, "resource_type"),
        "resource_id": _safe_id(resource.get("resource_id"), "resource_id"),
    }


def _clean_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    source = metadata if isinstance(metadata, Mapping) else {}
    result = _allowlisted(source.get("result") or "allowed", ALLOWED_RESULTS, "result")
    reason_value = source.get("reason_code")
    reason_code = None if reason_value in {None, ""} else _allowlisted(reason_value, ALLOWED_REASON_CODES, "reason_code")
    correlation_source = source.get("correlation") if isinstance(source.get("correlation"), Mapping) else {}
    correlation: dict[str, str] = {}
    for key in ALLOWED_CORRELATION_FIELDS:
        value = correlation_source.get(key)
        if value in {None, ""}:
            continue
        correlation[key] = _safe_id(value, f"correlation.{key}")
    return {"result": result, "reason_code": reason_code, "correlation": correlation}


def _actor_hash(actor: Mapping[str, Any] | None) -> str:
    source = actor if isinstance(actor, Mapping) else {}
    tenant_id = str(source.get("tenant_id") or source.get("tid") or "").strip().lower()
    actor_id = str(source.get("actor_id") or source.get("oid") or "").strip().lower()
    email = str(source.get("email") or "").strip().lower()
    identity = f"tenant:{tenant_id}|actor:{actor_id}" if actor_id else f"tenant:{tenant_id}|email:{email}" if email else "anonymous"
    digest = hmac.new(_hmac_key(), identity.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"actor_{digest[:40]}"


def _hmac_key() -> bytes:
    configured = str(os.environ.get("DF_AUDIT_HMAC_KEY") or "").strip()
    if not configured and blob_configured():
        raise AuditPersistenceError("DF_AUDIT_HMAC_KEY is required for durable audit storage")
    return configured.encode("utf-8") if configured else _local_hmac_key()


def _local_hmac_key() -> bytes:
    key_path = AUDIT_DIR / ".hmac-key"
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AuditPersistenceError("local audit HMAC key is unavailable") from exc
    deadline = time.monotonic() + LOCAL_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                key = key_path.read_bytes()
            except OSError as exc:
                raise AuditPersistenceError("local audit HMAC key is unavailable") from exc
            if len(key) >= 32:
                return key
            if time.monotonic() >= deadline:
                raise AuditIntegrityError("local audit HMAC key is invalid")
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
        except OSError as exc:
            raise AuditPersistenceError("local audit HMAC key is unavailable") from exc
        key = secrets.token_bytes(32)
        try:
            view = memoryview(key)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        except OSError as exc:
            raise AuditPersistenceError("local audit HMAC key creation failed") from exc
        finally:
            os.close(descriptor)
        return key


def _event_id(
    workspace_id: str,
    actor_hash: str,
    action: str,
    resource: Mapping[str, str],
    metadata: Mapping[str, Any],
) -> str:
    correlation = metadata.get("correlation") if isinstance(metadata.get("correlation"), Mapping) else {}
    if not correlation:
        return f"event_{uuid4().hex}"
    source = {
        "workspace_id": workspace_id,
        "actor_hash": actor_hash,
        "action": action,
        "resource_type": resource.get("resource_type"),
        "resource_id": resource.get("resource_id"),
        "result": metadata.get("result"),
        "reason_code": metadata.get("reason_code"),
        "correlation": dict(sorted(correlation.items())),
    }
    digest = hmac.new(_hmac_key(), _canonical_json(source), hashlib.sha256).hexdigest()
    return f"event_{digest[:40]}"


def _hash_event(event: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return hmac.new(_hmac_key(), _canonical_json(payload), hashlib.sha256).hexdigest()


def _validate_event_policy(model: AuditEvent, event: Mapping[str, Any]) -> None:
    _safe_id(event.get("event_id"), "event_id")
    _safe_id(event.get("workspace_id"), "workspace_id")
    _safe_id(event.get("resource_id"), "resource_id")
    _allowlisted(event.get("action"), ALLOWED_ACTIONS, "action")
    _allowlisted(event.get("resource_type"), ALLOWED_RESOURCE_TYPES, "resource_type")
    _allowlisted(event.get("result"), ALLOWED_RESULTS, "result")
    if event.get("reason_code") is not None:
        _allowlisted(event.get("reason_code"), ALLOWED_REASON_CODES, "reason_code")
    actor_hash = str(event.get("actor_hash") or "")
    if not re.fullmatch(r"actor_[0-9a-f]{40}", actor_hash):
        raise ValueError("actor_hash is invalid")
    if not re.fullmatch(r"event_[0-9a-f]{32,40}", str(event.get("event_id") or "")):
        raise ValueError("event_id is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(event.get("previous_hash") or "")):
        raise ValueError("previous_hash is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(event.get("event_hash") or "")):
        raise ValueError("event_hash is invalid")
    if model.at.tzinfo is None or model.at.utcoffset() != timedelta(0):
        raise ValueError("at must be UTC")
    correlation = event.get("correlation")
    if not isinstance(correlation, Mapping) or not set(correlation).issubset(ALLOWED_CORRELATION_FIELDS):
        raise ValueError("correlation is invalid")
    for key, value in correlation.items():
        _safe_id(value, f"correlation.{key}")


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _event_by_id(events: list[dict[str, Any]], event_id: str) -> dict[str, Any] | None:
    return next((event for event in events if event.get("event_id") == event_id), None)


def _read_local(workspace_id: str) -> dict[str, Any] | None:
    path = _local_path(workspace_id)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditIntegrityError("local audit ledger is invalid") from exc
    if not isinstance(value, dict):
        raise AuditIntegrityError("local audit ledger is invalid")
    return value


def _write_local(workspace_id: str, ledger: Mapping[str, Any]) -> None:
    path = _local_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(ledger, ensure_ascii=True, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AuditPersistenceError("local audit append failed") from exc


@contextmanager
def _local_lock(workspace_id: str):
    lock_path = _local_path(workspace_id).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    deadline = time.monotonic() + LOCAL_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise AuditPersistenceError("local audit append lock is unavailable")
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
        except OSError as exc:
            raise AuditPersistenceError("local audit append lock is unavailable") from exc
        try:
            os.write(descriptor, token.encode("ascii"))
        finally:
            os.close(descriptor)
        break
    try:
        yield
    finally:
        try:
            if lock_path.read_text(encoding="ascii") == token:
                lock_path.unlink()
        except OSError:
            pass


def _local_path(workspace_id: str) -> Path:
    return AUDIT_DIR / f"{_safe_id(workspace_id, 'workspace_id')}.json"


def _blob_name(workspace_id: str) -> str:
    return f"{AUDIT_BLOB_PREFIX}/{_safe_id(workspace_id, 'workspace_id')}/ledger.json"


def _allowlisted(value: Any, allowed: frozenset[str], field: str) -> str:
    clean = str(value or "").strip().lower()
    if clean not in allowed:
        raise ValueError(f"{field} is not allowlisted")
    return clean


def _safe_id(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not _SAFE_ID.fullmatch(clean) or "@" in clean or "?" in clean or "=" in clean:
        raise ValueError(f"{field} is invalid")
    return clean


def _encode_cursor(revision: int) -> str:
    raw = json.dumps({"before": revision}, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int | None:
    if cursor in {None, ""}:
        return None
    try:
        raw = base64.urlsafe_b64decode(str(cursor) + "=" * (-len(str(cursor)) % 4))
        value = json.loads(raw.decode("ascii"))
    except Exception as exc:
        raise ValueError("cursor is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"before"} or not isinstance(value["before"], int) or value["before"] < 1:
        raise ValueError("cursor is invalid")
    return value["before"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# The strict Blob reader is intentionally exposed under the neutral name used
# internally so tests and alternate persistence adapters can replace it.
download_blob_json = download_blob_json_strict


__all__ = [
    "AuditEvent",
    "AuditIntegrityError",
    "AuditPersistenceError",
    "list_audit_events",
    "record_audit_event",
]
