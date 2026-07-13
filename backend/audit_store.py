from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol
from uuid import uuid4

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from pydantic import BaseModel, ConfigDict

try:
    from .blob_store import blob_configured
except ImportError:
    from blob_store import blob_configured


ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = Path(os.environ.get("DF_AUDIT_LOCAL_DIR") or (ROOT / "generated-outputs" / "audit"))
AUDIT_CONTAINER = "dataforge-audit"
GENESIS_HASH = "0" * 64
MAX_PAGE_SIZE = 100
MAX_APPEND_RETRIES = 12
STREAM_SEGMENT_EVENTS = 10_000
LOCAL_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_SLEEP_SECONDS = 0.01

ALLOWED_ACTIONS = frozenset(
    {
        "workspace.read", "workspace.delete", "file.read", "file.create", "file.edit", "file.delete",
        "run.read", "artifact.read", "member.read", "member.manage", "outcome.read", "chargeback.read",
        "connector.manage", "connector.connect", "connector.reconnect", "connector.sync", "connector.import",
        "connector.disconnect", "connector.delete", "analysis.run", "message.create", "task.create", "task.start",
        "task.transition", "task.cancel", "task.complete", "task.fail", "artifact.generate", "outcome.record",
        "outcome.verify", "invitation.create", "invitation.send", "invitation.revoke", "invitation.fail",
        "member.update", "member.remove", "experiment.promote",
    }
)
ALLOWED_RESOURCE_TYPES = frozenset(
    {"workspace", "file", "connector", "analysis", "message", "task", "artifact", "outcome", "invitation", "member", "experiment"}
)
ALLOWED_RESULTS = frozenset({"allowed", "denied", "failed"})
ALLOWED_REASON_CODES = frozenset(
    {
        "authorized", "permission_denied", "operation_failed", "persistence_failed", "transition_attempt",
        "task_completed", "task_cancelled", "task_failed", "connector_error", "invitation_failed",
        "invalid_request", "conflict", "not_found", "promotion_attempt", "experiment_promoted", "promotion_failed",
    }
)
ALLOWED_CORRELATION_FIELDS = frozenset(
    {"request_id", "run_id", "task_id", "invitation_id", "connector_id", "outcome_event_id", "experiment_version_id"}
)

_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class AuditPersistenceError(RuntimeError):
    pass


class AuditIntegrityError(AuditPersistenceError):
    pass


class _AppendConflict(RuntimeError):
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
    key_id: str
    event_hash: str


class AuditAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_revision: int
    workspace_id: str
    workspace_revision: int
    workspace_event_hash: str
    at: datetime
    previous_hash: str
    key_id: str
    anchor_hash: str


class _AppendBackend(Protocol):
    def read(self, name: str) -> bytes: ...
    def list_names(self, prefix: str) -> list[str]: ...
    def append(self, name: str, payload: bytes, expected_size: int) -> None: ...


class _LocalAppendBackend:
    def read(self, name: str) -> bytes:
        path = _local_stream_path(name)
        if not path.exists():
            return b""
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AuditPersistenceError("local audit read failed") from exc

    def list_names(self, prefix: str) -> list[str]:
        base = _local_stream_path(prefix)
        if not base.exists():
            return []
        root = AUDIT_DIR.resolve()
        return sorted(path.resolve().relative_to(root).as_posix() for path in base.rglob("*.jsonl") if path.is_file())

    def append(self, name: str, payload: bytes, expected_size: int) -> None:
        path = _local_stream_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        current_size = path.stat().st_size if path.exists() else 0
        if current_size != expected_size:
            raise _AppendConflict("local append position changed")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                if os.fstat(descriptor).st_size != expected_size:
                    raise _AppendConflict("local append position changed")
                view = memoryview(payload)
                while view:
                    view = view[os.write(descriptor, view) :]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except _AppendConflict:
            raise
        except OSError as exc:
            raise AuditPersistenceError("local audit append failed") from exc


class _BlobAppendBackend:
    def __init__(self) -> None:
        connection_string = str(os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or "").strip()
        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            account = _storage_account_name()
            if not account:
                raise AuditPersistenceError("durable audit storage account is not configured")
            credential = os.environ.get("AZURE_STORAGE_KEY") or os.environ.get("DF_STORAGE_KEY") or DefaultAzureCredential()
            service = BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net", credential=credential)
        self.container = service.get_container_client(_audit_container_name())
        if not _is_production():
            try:
                self.container.create_container()
            except ResourceExistsError:
                pass

    def read(self, name: str) -> bytes:
        try:
            return bytes(self.container.get_blob_client(name).download_blob().readall())
        except ResourceNotFoundError:
            return b""
        except Exception as exc:
            raise AuditPersistenceError("durable audit read failed") from exc

    def list_names(self, prefix: str) -> list[str]:
        try:
            return sorted(str(item.name) for item in self.container.list_blobs(name_starts_with=prefix))
        except Exception as exc:
            raise AuditPersistenceError("durable audit listing failed") from exc

    def append(self, name: str, payload: bytes, expected_size: int) -> None:
        client = self.container.get_blob_client(name)
        if expected_size == 0:
            try:
                client.create_append_blob(
                    if_none_match="*",
                    content_settings=ContentSettings(content_type="application/x-ndjson; charset=utf-8"),
                )
            except ResourceExistsError:
                pass
            except HttpResponseError as exc:
                if exc.status_code not in {409, 412}:
                    raise AuditPersistenceError("durable audit stream creation failed") from exc
        try:
            client.append_block(payload, length=len(payload), appendpos_condition=expected_size)
        except HttpResponseError as exc:
            if exc.status_code in {409, 412}:
                raise _AppendConflict("durable append position changed") from exc
            raise AuditPersistenceError("durable audit append failed") from exc
        except Exception as exc:
            raise AuditPersistenceError("durable audit append failed") from exc


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
    backend = _backend()
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
    key_id, key = _active_key()
    actor_hash = _actor_hash(actor, key)
    event_id = f"event_{uuid4().hex}"
    at = _now()
    if isinstance(backend, _LocalAppendBackend):
        with _local_lock():
            return _append_event(backend, event_id, actor_hash, key_id, clean_action, clean_resource, clean_metadata, at)
    return _append_event(backend, event_id, actor_hash, key_id, clean_action, clean_resource, clean_metadata, at)


def list_audit_events(workspace_id: str, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    workspace = _workspace_id(workspace_id)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    backend = _backend()
    before_revision = _decode_cursor(cursor)
    if isinstance(backend, _LocalAppendBackend):
        with _local_lock():
            events = _read_anchored_events(backend, workspace)
    else:
        events = _read_anchored_events(backend, workspace)
    descending = [event for event in reversed(events) if before_revision is None or int(event["revision"]) < before_revision]
    page = descending[:limit]
    has_more = len(descending) > limit
    return {
        "workspace_id": workspace,
        "events": page,
        "count": len(page),
        "revision": len(events),
        "has_more": has_more,
        "next_cursor": _encode_cursor(int(page[-1]["revision"])) if page and has_more else None,
        "permissions": {"can_read": True, "can_update": False, "can_delete": False},
    }


def _append_event(
    backend: _AppendBackend,
    event_id: str,
    actor_hash: str,
    key_id: str,
    action: str,
    resource: dict[str, str],
    metadata: dict[str, Any],
    at: str,
) -> dict[str, Any]:
    workspace_id = resource["workspace_id"]
    for attempt in range(MAX_APPEND_RETRIES):
        events = _read_events(backend, workspace_id)
        anchors = _read_anchors(backend)
        try:
            _require_anchor_match(workspace_id, events, anchors)
        except AuditIntegrityError:
            if _can_recover_unanchored_head(workspace_id, events, anchors):
                _anchor_current_head(backend, workspace_id, str(events[-1]["event_id"]))
                continue
            raise
        existing = _event_by_id(events, event_id)
        if existing is not None:
            return existing
        event = _build_event(
            {"actor_hash": actor_hash},
            action,
            resource,
            metadata,
            revision=len(events) + 1,
            previous_hash=events[-1]["event_hash"] if events else GENESIS_HASH,
            event_id=event_id,
            at=at,
            key_id=key_id,
        )
        stream_name = _event_stream_name(workspace_id, _segment_for_revision(int(event["revision"])))
        current = backend.read(stream_name)
        try:
            backend.append(stream_name, _line(event), len(current))
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
        _anchor_current_head(backend, workspace_id, event_id)
        committed = _read_anchored_events(backend, workspace_id)
        persisted = _event_by_id(committed, event_id)
        if persisted is None:
            raise AuditIntegrityError("durable audit append was not retained")
        return persisted
    raise AuditPersistenceError("durable audit append conflict")


def _anchor_current_head(backend: _AppendBackend, workspace_id: str, event_id: str) -> None:
    for _attempt in range(MAX_APPEND_RETRIES):
        events = _read_events(backend, workspace_id)
        if not events or _event_by_id(events, event_id) is None:
            raise AuditIntegrityError("audit event disappeared before anchoring")
        anchors = _read_anchors(backend)
        latest = _latest_workspace_anchor(anchors, workspace_id)
        if latest and int(latest["workspace_revision"]) >= len(events):
            _require_anchor_match(workspace_id, events, anchors)
            return
        if latest and not _can_recover_unanchored_head(workspace_id, events, anchors):
            raise AuditIntegrityError("audit ledger has more than one unanchored revision")
        anchor_revision = len(anchors) + 1
        key_id, _key = _active_key()
        anchor = _build_anchor(
            anchor_revision=anchor_revision,
            workspace_id=workspace_id,
            workspace_revision=len(events),
            workspace_event_hash=str(events[-1]["event_hash"]),
            previous_hash=anchors[-1]["anchor_hash"] if anchors else GENESIS_HASH,
            key_id=key_id,
        )
        name = _anchor_stream_name(_segment_for_revision(anchor_revision))
        current = backend.read(name)
        try:
            backend.append(name, _line(anchor), len(current))
            return
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
    raise AuditPersistenceError("durable audit anchor conflict")


def _read_anchored_events(backend: _AppendBackend, workspace_id: str) -> list[dict[str, Any]]:
    events = _read_events(backend, workspace_id)
    anchors = _read_anchors(backend)
    _require_anchor_match(workspace_id, events, anchors)
    return events


def _read_events(backend: _AppendBackend, workspace_id: str) -> list[dict[str, Any]]:
    names = backend.list_names(_event_stream_prefix(workspace_id))
    raw = []
    for name in names:
        if not re.fullmatch(re.escape(_event_stream_prefix(workspace_id)) + r"[0-9]{8}\.jsonl", name):
            raise AuditIntegrityError("audit event stream name is invalid")
        raw.extend(_parse_lines(backend.read(name), "audit event stream"))
    events: list[dict[str, Any]] = []
    previous_hash = GENESIS_HASH
    seen: set[str] = set()
    for revision, value in enumerate(raw, start=1):
        try:
            model = AuditEvent.model_validate(value)
            event = model.model_dump(mode="json")
            _validate_event_policy(model, event)
        except Exception as exc:
            raise AuditIntegrityError("audit event schema is invalid") from exc
        if event["workspace_id"] != workspace_id or event["revision"] != revision:
            raise AuditIntegrityError("audit event identity is invalid")
        if event["event_id"] in seen or event["previous_hash"] != previous_hash:
            raise AuditIntegrityError("audit event chain is invalid")
        if not hmac.compare_digest(event["event_hash"], _hash_event(event)):
            raise AuditIntegrityError("audit event hash is invalid")
        seen.add(event["event_id"])
        previous_hash = event["event_hash"]
        events.append(event)
    return events


def _read_anchors(backend: _AppendBackend) -> list[dict[str, Any]]:
    raw = []
    for name in backend.list_names("anchors/"):
        if not re.fullmatch(r"anchors/[0-9]{8}\.jsonl", name):
            raise AuditIntegrityError("audit anchor stream name is invalid")
        raw.extend(_parse_lines(backend.read(name), "audit anchor stream"))
    anchors: list[dict[str, Any]] = []
    previous_hash = GENESIS_HASH
    for revision, value in enumerate(raw, start=1):
        try:
            model = AuditAnchor.model_validate(value)
            anchor = model.model_dump(mode="json")
        except Exception as exc:
            raise AuditIntegrityError("audit anchor schema is invalid") from exc
        if anchor["anchor_revision"] != revision or anchor["previous_hash"] != previous_hash:
            raise AuditIntegrityError("audit anchor chain is invalid")
        _workspace_id(anchor["workspace_id"])
        if not hmac.compare_digest(anchor["anchor_hash"], _hash_anchor(anchor)):
            raise AuditIntegrityError("audit anchor hash is invalid")
        previous_hash = anchor["anchor_hash"]
        anchors.append(anchor)
    return anchors


def _require_anchor_match(workspace_id: str, events: list[dict[str, Any]], anchors: list[dict[str, Any]]) -> None:
    latest = _latest_workspace_anchor(anchors, workspace_id)
    if not events and latest is None:
        return
    if not events and latest is not None:
        raise AuditIntegrityError("audit ledger is missing after being anchored")
    if latest is None:
        raise AuditIntegrityError("audit anchor is missing for an existing ledger")
    anchored_revision = int(latest["workspace_revision"])
    if anchored_revision != len(events):
        raise AuditIntegrityError("audit ledger rollback or unanchored revision detected")
    if not hmac.compare_digest(str(latest["workspace_event_hash"]), str(events[-1]["event_hash"])):
        raise AuditIntegrityError("audit ledger rollback hash mismatch")


def _latest_workspace_anchor(anchors: list[dict[str, Any]], workspace_id: str) -> dict[str, Any] | None:
    return next((anchor for anchor in reversed(anchors) if anchor["workspace_id"] == workspace_id), None)


def _can_recover_unanchored_head(
    workspace_id: str,
    events: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
) -> bool:
    latest = _latest_workspace_anchor(anchors, workspace_id)
    if latest is None:
        return False
    anchored_revision = int(latest["workspace_revision"])
    if anchored_revision < 1 or len(events) != anchored_revision + 1:
        return False
    return hmac.compare_digest(
        str(latest["workspace_event_hash"]),
        str(events[anchored_revision - 1]["event_hash"]),
    )


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
    key_id: str | None = None,
) -> dict[str, Any]:
    clean_resource = _clean_resource(resource)
    clean_metadata = _clean_metadata(metadata)
    active_key_id, active_key = _active_key()
    selected_key_id = key_id or active_key_id
    actor_hash = str((actor or {}).get("actor_hash") or "") or _actor_hash(actor, active_key)
    payload: dict[str, Any] = {
        "event_id": event_id or f"event_{uuid4().hex}",
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
        "key_id": selected_key_id,
        "event_hash": "",
    }
    normalized = AuditEvent.model_validate(payload).model_dump(mode="json")
    normalized["event_hash"] = _hash_event(normalized)
    return AuditEvent.model_validate(normalized).model_dump(mode="json")


def _build_anchor(**values: Any) -> dict[str, Any]:
    payload = {**values, "at": _now(), "anchor_hash": ""}
    normalized = AuditAnchor.model_validate(payload).model_dump(mode="json")
    normalized["anchor_hash"] = _hash_anchor(normalized)
    return AuditAnchor.model_validate(normalized).model_dump(mode="json")


def _clean_resource(resource: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(resource, Mapping):
        raise ValueError("resource must be an object")
    return {
        "workspace_id": _workspace_id(resource.get("workspace_id")),
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
        if value not in {None, ""}:
            correlation[key] = _safe_id(value, f"correlation.{key}")
    return {"result": result, "reason_code": reason_code, "correlation": correlation}


def _actor_hash(actor: Mapping[str, Any] | None, key: bytes) -> str:
    source = actor if isinstance(actor, Mapping) else {}
    tenant_id = str(source.get("tenant_id") or source.get("tid") or "").strip().lower()
    actor_id = str(source.get("actor_id") or source.get("oid") or "").strip().lower()
    email = str(source.get("email") or "").strip().lower()
    identity = f"tenant:{tenant_id}|actor:{actor_id}" if actor_id else f"tenant:{tenant_id}|email:{email}" if email else "anonymous"
    return f"actor_{hmac.new(key, identity.encode('utf-8'), hashlib.sha256).hexdigest()[:40]}"


def _active_key() -> tuple[str, bytes]:
    active_id, keys = _key_ring()
    return active_id, keys[active_id]


def _key_ring() -> tuple[str, dict[str, bytes]]:
    configured = str(os.environ.get("DF_AUDIT_HMAC_KEYS") or "").strip()
    active_id = str(os.environ.get("DF_AUDIT_HMAC_ACTIVE_KEY_ID") or "").strip()
    if configured:
        try:
            raw = json.loads(configured)
        except json.JSONDecodeError as exc:
            raise AuditPersistenceError("audit HMAC key ring is invalid") from exc
        if not isinstance(raw, dict) or not raw or not _KEY_ID.fullmatch(active_id):
            raise AuditPersistenceError("audit HMAC active key id is invalid")
        keys: dict[str, bytes] = {}
        for key_id, encoded in raw.items():
            if not _KEY_ID.fullmatch(str(key_id)) or not isinstance(encoded, str):
                raise AuditPersistenceError("audit HMAC key ring is invalid")
            try:
                key = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise AuditPersistenceError("audit HMAC key ring is invalid") from exc
            if len(key) < 32:
                raise AuditPersistenceError("audit HMAC keys must be at least 256-bit decoded secrets")
            keys[str(key_id)] = key
        if active_id not in keys:
            raise AuditPersistenceError("audit HMAC active key is not retained in the key ring")
        return active_id, keys
    if _is_production() or blob_configured():
        raise AuditPersistenceError("DF_AUDIT_HMAC_KEYS and DF_AUDIT_HMAC_ACTIVE_KEY_ID are required")
    if not _local_mode_enabled():
        raise AuditPersistenceError("local audit mode must be enabled explicitly")
    return _local_key_ring()


def _local_key_ring() -> tuple[str, dict[str, bytes]]:
    path = AUDIT_DIR / ".keyring.json"
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="ascii"))
            key = base64.b64decode(str(value["key"]), validate=True)
            if value.get("key_id") != "local-v1" or len(key) < 32:
                raise ValueError
            return "local-v1", {"local-v1": key}
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise AuditIntegrityError("local audit HMAC key ring is invalid") from exc
    key = secrets.token_bytes(32)
    payload = json.dumps({"key_id": "local-v1", "key": base64.b64encode(key).decode("ascii")}, sort_keys=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, payload.encode("ascii"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        return _local_key_ring()
    except OSError as exc:
        raise AuditPersistenceError("local audit HMAC key creation failed") from exc
    return "local-v1", {"local-v1": key}


def _key_for(key_id: str) -> bytes:
    _active, keys = _key_ring()
    try:
        return keys[key_id]
    except KeyError as exc:
        raise AuditIntegrityError("audit event key id is not retained") from exc


def _hash_event(event: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return hmac.new(_key_for(str(event.get("key_id") or "")), _canonical_json(payload), hashlib.sha256).hexdigest()


def _hash_anchor(anchor: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in anchor.items() if key != "anchor_hash"}
    return hmac.new(_key_for(str(anchor.get("key_id") or "")), _canonical_json(payload), hashlib.sha256).hexdigest()


def _validate_event_policy(model: AuditEvent, event: Mapping[str, Any]) -> None:
    _workspace_id(event.get("workspace_id"))
    _safe_id(event.get("resource_id"), "resource_id")
    _allowlisted(event.get("action"), ALLOWED_ACTIONS, "action")
    _allowlisted(event.get("resource_type"), ALLOWED_RESOURCE_TYPES, "resource_type")
    _allowlisted(event.get("result"), ALLOWED_RESULTS, "result")
    if event.get("reason_code") is not None:
        _allowlisted(event.get("reason_code"), ALLOWED_REASON_CODES, "reason_code")
    if not re.fullmatch(r"actor_[0-9a-f]{40}", str(event.get("actor_hash") or "")):
        raise ValueError("actor_hash is invalid")
    if not re.fullmatch(r"event_[0-9a-f]{32}", str(event.get("event_id") or "")):
        raise ValueError("event_id is invalid")
    if not _KEY_ID.fullmatch(str(event.get("key_id") or "")):
        raise ValueError("key_id is invalid")
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


def _backend() -> _AppendBackend:
    if _is_production():
        if _local_mode_enabled():
            raise AuditPersistenceError("local audit mode is prohibited in production")
        if not blob_configured():
            raise AuditPersistenceError("durable Blob audit storage is required in production")
        _verify_production_storage_contract()
        return _BlobAppendBackend()
    if blob_configured():
        return _BlobAppendBackend()
    if _local_mode_enabled():
        return _LocalAppendBackend()
    raise AuditPersistenceError("local audit mode must be enabled explicitly")


def _verify_production_storage_contract() -> None:
    resource_id = str(os.environ.get("DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID") or "").strip().rstrip("/")
    if not resource_id.startswith("/subscriptions/"):
        raise AuditPersistenceError("production audit storage contract resource id is missing")
    api = "api-version=2025-06-01"
    service = _management_get_json(f"{resource_id}/blobServices/default?{api}").get("properties")
    policy = _management_get_json(
        f"{resource_id}/blobServices/default/containers/{_audit_container_name()}/immutabilityPolicies/default?{api}"
    ).get("properties")
    _validate_production_contract(service, policy)


def _management_get_json(resource_path: str) -> dict[str, Any]:
    token = DefaultAzureCredential().get_token("https://management.azure.com/.default").token
    request = urllib.request.Request(
        f"https://management.azure.com{resource_path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            value = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise AuditPersistenceError("production audit storage contract verification failed") from exc
    if not isinstance(value, dict):
        raise AuditPersistenceError("production audit storage contract response is invalid")
    return value


def _validate_production_contract(service: Mapping[str, Any] | None, policy: Mapping[str, Any] | None) -> None:
    service = service if isinstance(service, Mapping) else {}
    policy = policy if isinstance(policy, Mapping) else {}
    versioning = service.get("isVersioningEnabled", service.get("is_versioning_enabled")) is True
    blob_delete = service.get("deleteRetentionPolicy", service.get("delete_retention_policy"))
    container_delete = service.get("containerDeleteRetentionPolicy", service.get("container_delete_retention_policy"))
    blob_delete_ok = isinstance(blob_delete, Mapping) and blob_delete.get("enabled") is True and int(blob_delete.get("days") or 0) > 0
    container_delete_ok = isinstance(container_delete, Mapping) and container_delete.get("enabled") is True and int(container_delete.get("days") or 0) > 0
    locked = str(policy.get("state") or policy.get("policy_mode") or "").lower() == "locked"
    append_ok = policy.get("allowProtectedAppendWrites", policy.get("allow_protected_append_writes")) is True
    if not all((versioning, blob_delete_ok, container_delete_ok, locked, append_ok)):
        raise AuditPersistenceError("production audit storage contract is not satisfied")


def _is_production() -> bool:
    environment = str(os.environ.get("DF_ENVIRONMENT") or "").strip().lower()
    return environment in {"prod", "production"} or bool(
        os.environ.get("CONTAINER_APP_NAME") or os.environ.get("CONTAINER_APP_REVISION") or os.environ.get("WEBSITE_INSTANCE_ID")
    )


def _local_mode_enabled() -> bool:
    return str(os.environ.get("DF_AUDIT_LOCAL_MODE") or "").strip().lower() in {"1", "true", "yes"}


def _storage_account_name() -> str:
    return str(os.environ.get("STORAGE_ACCOUNT_NAME") or os.environ.get("DF_STORAGE_ACCOUNT") or "").strip()


def _audit_container_name() -> str:
    value = str(os.environ.get("DF_AUDIT_CONTAINER") or AUDIT_CONTAINER).strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", value):
        raise AuditPersistenceError("audit container name is invalid")
    return value


@contextmanager
def _local_lock():
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = AUDIT_DIR / ".ledger.lock"
    token = uuid4().hex
    deadline = time.monotonic() + LOCAL_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, token.encode("ascii"))
            os.close(descriptor)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise AuditPersistenceError("local audit append lock is unavailable")
            time.sleep(_LOCK_SLEEP_SECONDS)
        except OSError as exc:
            raise AuditPersistenceError("local audit append lock is unavailable") from exc
    try:
        yield
    finally:
        try:
            if lock_path.read_text(encoding="ascii") == token:
                lock_path.unlink()
        except OSError:
            pass


def _local_stream_path(name: str) -> Path:
    raw = str(name or "")
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("audit stream path must be relative")
    normalized = raw.replace("\\", "/").strip("/")
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("audit stream path is invalid")
    root = AUDIT_DIR.resolve()
    path = (root / normalized).resolve()
    if root not in path.parents:
        raise ValueError("audit stream path escapes audit directory")
    return path


def _event_stream_prefix(workspace_id: str) -> str:
    return f"workspaces/{_workspace_id(workspace_id)}/events/"


def _event_stream_name(workspace_id: str, segment: int) -> str:
    return f"{_event_stream_prefix(workspace_id)}{int(segment):08d}.jsonl"


def _anchor_stream_name(segment: int) -> str:
    return f"anchors/{int(segment):08d}.jsonl"


def _segment_for_revision(revision: int) -> int:
    return ((int(revision) - 1) // STREAM_SEGMENT_EVENTS) + 1


def _parse_lines(payload: bytes, label: str) -> list[dict[str, Any]]:
    if not payload:
        return []
    if not payload.endswith(b"\n"):
        raise AuditIntegrityError(f"{label} is truncated")
    values = []
    for line in payload.splitlines():
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuditIntegrityError(f"{label} is invalid") from exc
        if not isinstance(value, dict):
            raise AuditIntegrityError(f"{label} is invalid")
        values.append(value)
    return values


def _line(value: Mapping[str, Any]) -> bytes:
    return _canonical_json(value) + b"\n"


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _event_by_id(events: list[dict[str, Any]], event_id: str) -> dict[str, Any] | None:
    return next((event for event in events if event.get("event_id") == event_id), None)


def _allowlisted(value: Any, allowed: frozenset[str], field: str) -> str:
    clean = str(value or "").strip().lower()
    if clean not in allowed:
        raise ValueError(f"{field} is not allowlisted")
    return clean


def _workspace_id(value: Any) -> str:
    clean = str(value or "").strip()
    if not _WORKSPACE_ID.fullmatch(clean):
        raise ValueError("workspace_id is invalid")
    return clean


def _safe_id(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not _SAFE_ID.fullmatch(clean) or "@" in clean or "?" in clean or "=" in clean or "\\" in clean:
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


__all__ = [
    "AuditEvent", "AuditIntegrityError", "AuditPersistenceError", "list_audit_events", "record_audit_event",
]
