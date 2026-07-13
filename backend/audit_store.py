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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol
from uuid import uuid4

from azure.core import MatchConditions
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
STREAM_TAIL_BYTES = 64 * 1024
MAX_STREAM_RECORD_BYTES = 16 * 1024
PRODUCTION_CONTRACT_CACHE_TTL_SECONDS = 60.0
LOCAL_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_SLEEP_SECONDS = 0.01
_PRODUCTION_CONTRACT_CACHE: dict[tuple[str, str, str], float] = {}

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
_STORAGE_RESOURCE_ID = re.compile(
    r"^/subscriptions/(?P<subscription>[^/]+)/resourceGroups/(?P<resource_group>[^/]+)/providers/"
    r"Microsoft\.Storage/storageAccounts/(?P<account>[^/]+)$",
    re.IGNORECASE,
)


class AuditPersistenceError(RuntimeError):
    pass


class AuditIntegrityError(AuditPersistenceError):
    pass


class _AppendConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class _StreamSnapshot:
    name: str
    data: bytes
    head: dict[str, Any] | None
    length: int
    etag: str | None


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
    def read_snapshot(self, name: str) -> _StreamSnapshot: ...
    def read_full(self, name: str) -> bytes: ...
    def append(self, name: str, payload: bytes, snapshot: _StreamSnapshot) -> None: ...


class _LocalAppendBackend:
    def read_snapshot(self, name: str) -> _StreamSnapshot:
        path = _local_stream_path(name)
        if not path.exists():
            return _StreamSnapshot(name=name, data=b"", head=None, length=0, etag=None)
        try:
            before = path.stat()
            with path.open("rb") as stream:
                stream.seek(max(0, before.st_size - STREAM_TAIL_BYTES))
                data = stream.read(STREAM_TAIL_BYTES)
            after = path.stat()
        except OSError as exc:
            raise AuditPersistenceError("local audit read failed") from exc
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise _AppendConflict("local stream changed during snapshot read")
        return _snapshot_from_tail(name, data, int(after.st_size), _local_etag(after))

    def read_full(self, name: str) -> bytes:
        path = _local_stream_path(name)
        if not path.exists():
            return b""
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AuditPersistenceError("local audit read failed") from exc

    def append(self, name: str, payload: bytes, snapshot: _StreamSnapshot) -> None:
        _validate_append_request(name, payload, snapshot)
        path = _local_stream_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if snapshot.etag is not None and path.exists():
            current = path.stat()
            if current.st_size != snapshot.length or _local_etag(current) != snapshot.etag:
                raise _AppendConflict("local append snapshot changed")
        elif snapshot.etag is not None or snapshot.length != 0:
            raise _AppendConflict("local append stream disappeared")
        try:
            flags = os.O_APPEND | os.O_WRONLY
            flags |= os.O_CREAT | os.O_EXCL if snapshot.etag is None else 0
            descriptor = os.open(path, flags, 0o600)
            try:
                opened = os.fstat(descriptor)
                if opened.st_size != snapshot.length or (
                    snapshot.etag is not None and _local_etag(opened) != snapshot.etag
                ):
                    raise _AppendConflict("local append position changed")
                view = memoryview(payload)
                while view:
                    view = view[os.write(descriptor, view) :]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except FileExistsError as exc:
            raise _AppendConflict("local append stream was created concurrently") from exc
        except _AppendConflict:
            raise
        except OSError as exc:
            raise AuditPersistenceError("local audit append failed") from exc


class _BlobAppendBackend:
    def __init__(self, *, account_name: str | None = None, managed_identity_only: bool = False) -> None:
        connection_string = str(os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or "").strip()
        if managed_identity_only:
            account = str(account_name or "").strip()
            if not account:
                raise AuditPersistenceError("durable audit storage account is not configured")
            service = BlobServiceClient(
                account_url=f"https://{account}.blob.core.windows.net",
                credential=DefaultAzureCredential(),
            )
        elif connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            account = str(account_name or _storage_account_name()).strip()
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

    def read_snapshot(self, name: str) -> _StreamSnapshot:
        client = self.container.get_blob_client(name)
        try:
            properties = client.get_blob_properties()
        except ResourceNotFoundError:
            return _StreamSnapshot(name=name, data=b"", head=None, length=0, etag=None)
        except Exception as exc:
            raise AuditPersistenceError("durable audit properties read failed") from exc
        length = int(properties.size or 0)
        etag = str(properties.etag or "")
        if not etag:
            raise AuditPersistenceError("durable audit stream ETag is missing")
        blob_type = str(properties.blob_type or "").lower()
        if "append" not in blob_type:
            raise AuditIntegrityError("durable audit stream is not an append blob")
        offset = max(0, length - STREAM_TAIL_BYTES)
        try:
            data = bytes(
                client.download_blob(
                    offset=offset,
                    length=length - offset,
                    etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                ).readall()
            ) if length else b""
        except HttpResponseError as exc:
            if exc.status_code in {409, 412}:
                raise _AppendConflict("durable stream changed during snapshot read") from exc
            raise AuditPersistenceError("durable audit tail read failed") from exc
        except Exception as exc:
            raise AuditPersistenceError("durable audit tail read failed") from exc
        return _snapshot_from_tail(name, data, length, etag)

    def read_full(self, name: str) -> bytes:
        client = self.container.get_blob_client(name)
        try:
            properties = client.get_blob_properties()
            etag = str(properties.etag or "")
            if not etag:
                raise AuditPersistenceError("durable audit stream ETag is missing")
            if "append" not in str(properties.blob_type or "").lower():
                raise AuditIntegrityError("durable audit stream is not an append blob")
            return bytes(
                client.download_blob(etag=etag, match_condition=MatchConditions.IfNotModified).readall()
            )
        except ResourceNotFoundError:
            return b""
        except HttpResponseError as exc:
            if exc.status_code in {409, 412}:
                raise _AppendConflict("durable stream changed during full read") from exc
            raise AuditPersistenceError("durable audit read failed") from exc
        except Exception as exc:
            if isinstance(exc, AuditPersistenceError):
                raise
            raise AuditPersistenceError("durable audit read failed") from exc

    def append(self, name: str, payload: bytes, snapshot: _StreamSnapshot) -> None:
        _validate_append_request(name, payload, snapshot)
        client = self.container.get_blob_client(name)
        etag = snapshot.etag
        if etag is None:
            try:
                created = client.create_append_blob(
                    if_none_match="*",
                    content_settings=ContentSettings(content_type="application/x-ndjson; charset=utf-8"),
                )
            except ResourceExistsError:
                raise _AppendConflict("durable append stream was created concurrently")
            except HttpResponseError as exc:
                if exc.status_code in {409, 412}:
                    raise _AppendConflict("durable append stream was created concurrently") from exc
                raise AuditPersistenceError("durable audit stream creation failed") from exc
            etag = str(created.get("etag") or created.get("ETag") or "")
            if not etag:
                raise AuditPersistenceError("created durable audit stream ETag is missing")
        try:
            client.append_block(
                payload,
                length=len(payload),
                appendpos_condition=snapshot.length,
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
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
    key_id, key = _active_key()
    clean_metadata = _clean_metadata(merged_metadata, key)
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
    event_name = _event_stream_name(workspace_id)
    anchor_name = _anchor_stream_name(workspace_id)
    for _attempt in range(MAX_APPEND_RETRIES):
        try:
            event_snapshot, anchor_snapshot, event_head, anchor_head, state = _load_head_state(
                backend, workspace_id, event_name, anchor_name
            )
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
        if state == "recoverable":
            _anchor_current_head(backend, workspace_id, event_head)
            continue
        event = _build_event(
            {"actor_hash": actor_hash},
            action,
            resource,
            metadata,
            revision=int(event_head["revision"]) + 1 if event_head else 1,
            previous_hash=str(event_head["event_hash"]) if event_head else GENESIS_HASH,
            event_id=event_id,
            at=at,
            key_id=key_id,
            _metadata_clean=True,
        )
        try:
            backend.append(event_name, _line(event), event_snapshot)
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
        _anchor_current_head(backend, workspace_id, event)
        return event
    raise AuditPersistenceError("durable audit append conflict")


def _anchor_current_head(
    backend: _AppendBackend,
    workspace_id: str,
    target_event: Mapping[str, Any] | None,
) -> None:
    event_name = _event_stream_name(workspace_id)
    anchor_name = _anchor_stream_name(workspace_id)
    target_revision = int((target_event or {}).get("revision") or 0)
    target_hash = str((target_event or {}).get("event_hash") or "")
    for _attempt in range(MAX_APPEND_RETRIES):
        try:
            event_snapshot, anchor_snapshot, event_head, anchor_head, state = _load_head_state(
                backend, workspace_id, event_name, anchor_name
            )
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
        if event_head is None:
            raise AuditIntegrityError("audit event disappeared before anchoring")
        if state == "anchored":
            anchored_revision = int(anchor_head["workspace_revision"])
            if target_revision and anchored_revision < target_revision:
                raise AuditIntegrityError("audit target event was not anchored")
            if target_revision == anchored_revision and target_hash and not hmac.compare_digest(
                target_hash, str(anchor_head["workspace_event_hash"])
            ):
                raise AuditIntegrityError("audit target event hash was not anchored")
            return
        if state != "recoverable":
            raise AuditIntegrityError("audit ledger cannot be anchored")
        if target_revision and int(event_head["revision"]) < target_revision:
            raise AuditIntegrityError("audit target event disappeared before anchoring")
        key_id, _key = _active_key()
        anchor = _build_anchor(
            anchor_revision=int(anchor_head["anchor_revision"]) + 1 if anchor_head else 1,
            workspace_id=workspace_id,
            workspace_revision=int(event_head["revision"]),
            workspace_event_hash=str(event_head["event_hash"]),
            previous_hash=str(anchor_head["anchor_hash"]) if anchor_head else GENESIS_HASH,
            key_id=key_id,
        )
        try:
            backend.append(anchor_name, _line(anchor), anchor_snapshot)
            return
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
    raise AuditPersistenceError("durable audit anchor conflict")


def _read_anchored_events(backend: _AppendBackend, workspace_id: str) -> list[dict[str, Any]]:
    events = _read_events(backend, workspace_id)
    anchors = _read_anchors(backend, workspace_id)
    _require_anchor_match(workspace_id, events, anchors)
    return events


def _read_events(backend: _AppendBackend, workspace_id: str) -> list[dict[str, Any]]:
    raw = _parse_lines(backend.read_full(_event_stream_name(workspace_id)), "audit event stream")
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


def _read_anchors(backend: _AppendBackend, workspace_id: str) -> list[dict[str, Any]]:
    raw = _parse_lines(backend.read_full(_anchor_stream_name(workspace_id)), "audit anchor stream")
    anchors: list[dict[str, Any]] = []
    previous_hash = GENESIS_HASH
    for revision, value in enumerate(raw, start=1):
        try:
            model = AuditAnchor.model_validate(value)
            anchor = model.model_dump(mode="json")
            _validate_anchor_policy(model, anchor)
        except Exception as exc:
            raise AuditIntegrityError("audit anchor schema is invalid") from exc
        if (
            anchor["workspace_id"] != workspace_id
            or anchor["anchor_revision"] != revision
            or anchor["workspace_revision"] != revision
            or anchor["previous_hash"] != previous_hash
        ):
            raise AuditIntegrityError("audit anchor chain is invalid")
        _workspace_id(anchor["workspace_id"])
        if not hmac.compare_digest(anchor["anchor_hash"], _hash_anchor(anchor)):
            raise AuditIntegrityError("audit anchor hash is invalid")
        previous_hash = anchor["anchor_hash"]
        anchors.append(anchor)
    return anchors


def _require_anchor_match(workspace_id: str, events: list[dict[str, Any]], anchors: list[dict[str, Any]]) -> None:
    latest = anchors[-1] if anchors else None
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


def _head_state(
    event_snapshot: _StreamSnapshot,
    event_head: Mapping[str, Any] | None,
    anchor_head: Mapping[str, Any] | None,
) -> Literal["empty", "anchored", "recoverable"]:
    if event_head is None and anchor_head is None:
        return "empty"
    if event_head is None:
        raise AuditIntegrityError("audit ledger is missing after being anchored")
    event_revision = int(event_head["revision"])
    if anchor_head is None:
        if (
            event_revision == 1
            and event_head["previous_hash"] == GENESIS_HASH
            and event_snapshot.length == len(event_snapshot.data)
            and event_snapshot.data.count(b"\n") == 1
        ):
            return "recoverable"
        raise AuditIntegrityError("audit anchor is missing for an existing ledger")
    anchored_revision = int(anchor_head["workspace_revision"])
    anchored_hash = str(anchor_head["workspace_event_hash"])
    if event_revision == anchored_revision and hmac.compare_digest(str(event_head["event_hash"]), anchored_hash):
        return "anchored"
    if (
        event_revision == anchored_revision + 1
        and hmac.compare_digest(str(event_head["previous_hash"]), anchored_hash)
    ):
        return "recoverable"
    if event_revision < anchored_revision:
        raise AuditIntegrityError("audit ledger rollback or delete detected")
    raise AuditIntegrityError("audit ledger has more than one unanchored revision or a hash mismatch")


def _load_head_state(
    backend: _AppendBackend,
    workspace_id: str,
    event_name: str,
    anchor_name: str,
) -> tuple[
    _StreamSnapshot,
    _StreamSnapshot,
    dict[str, Any] | None,
    dict[str, Any] | None,
    Literal["empty", "anchored", "recoverable"],
]:
    # Anchors are written after events, so this order avoids pairing an old event with a new anchor.
    anchor_snapshot = backend.read_snapshot(anchor_name)
    event_snapshot = backend.read_snapshot(event_name)
    event_head = _validated_event_head(event_snapshot, workspace_id)
    anchor_head = _validated_anchor_head(anchor_snapshot, workspace_id)
    try:
        state = _head_state(event_snapshot, event_head, anchor_head)
    except AuditIntegrityError:
        confirmed_anchor = backend.read_snapshot(anchor_name)
        confirmed_event = backend.read_snapshot(event_name)
        if _snapshot_token(confirmed_anchor) != _snapshot_token(anchor_snapshot) or _snapshot_token(
            confirmed_event
        ) != _snapshot_token(event_snapshot):
            raise _AppendConflict("audit head pair changed during validation")
        raise
    return event_snapshot, anchor_snapshot, event_head, anchor_head, state


def _snapshot_token(snapshot: _StreamSnapshot) -> tuple[int, str | None]:
    return snapshot.length, snapshot.etag


def _validated_event_head(snapshot: _StreamSnapshot, workspace_id: str) -> dict[str, Any] | None:
    if snapshot.head is None:
        if snapshot.length:
            raise AuditIntegrityError("audit event stream head is missing")
        return None
    try:
        model = AuditEvent.model_validate(snapshot.head)
        event = model.model_dump(mode="json")
        _validate_event_policy(model, event)
    except Exception as exc:
        raise AuditIntegrityError("audit event head is invalid") from exc
    if event["workspace_id"] != workspace_id or int(event["revision"]) < 1:
        raise AuditIntegrityError("audit event head identity is invalid")
    if not hmac.compare_digest(str(event["event_hash"]), _hash_event(event)):
        raise AuditIntegrityError("audit event head hash is invalid")
    return event


def _validated_anchor_head(snapshot: _StreamSnapshot, workspace_id: str) -> dict[str, Any] | None:
    if snapshot.head is None:
        if snapshot.length:
            raise AuditIntegrityError("audit anchor stream head is missing")
        return None
    try:
        model = AuditAnchor.model_validate(snapshot.head)
        anchor = model.model_dump(mode="json")
        _validate_anchor_policy(model, anchor)
    except Exception as exc:
        raise AuditIntegrityError("audit anchor head schema is invalid") from exc
    if (
        anchor["workspace_id"] != workspace_id
        or int(anchor["anchor_revision"]) < 1
        or int(anchor["anchor_revision"]) != int(anchor["workspace_revision"])
        or not hmac.compare_digest(str(anchor["anchor_hash"]), _hash_anchor(anchor))
    ):
        raise AuditIntegrityError("audit anchor head is invalid")
    return anchor


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
    _metadata_clean: bool = False,
) -> dict[str, Any]:
    clean_resource = _clean_resource(resource)
    active_key_id, active_key = _active_key()
    selected_key_id = key_id or active_key_id
    selected_key = _key_for(selected_key_id)
    clean_metadata = dict(metadata or {}) if _metadata_clean else _clean_metadata(metadata, selected_key)
    actor_hash = str((actor or {}).get("actor_hash") or "") or _actor_hash(actor, selected_key)
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


def _clean_metadata(metadata: Mapping[str, Any] | None, key: bytes) -> dict[str, Any]:
    source = metadata if isinstance(metadata, Mapping) else {}
    result = _allowlisted(source.get("result") or "allowed", ALLOWED_RESULTS, "result")
    reason_value = source.get("reason_code")
    reason_code = None if reason_value in {None, ""} else _allowlisted(reason_value, ALLOWED_REASON_CODES, "reason_code")
    correlation_source = source.get("correlation") if isinstance(source.get("correlation"), Mapping) else {}
    correlation: dict[str, str] = {}
    for field in ALLOWED_CORRELATION_FIELDS:
        value = correlation_source.get(field)
        if value not in {None, ""}:
            raw = str(value).strip()
            if not raw or len(raw.encode("utf-8")) > 8192:
                raise ValueError(f"correlation.{field} is invalid")
            digest = hmac.new(key, f"{field}:{raw}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]
            correlation[field] = f"corr_{digest}"
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
    for value in correlation.values():
        if not re.fullmatch(r"corr_[0-9a-f]{40}", str(value)):
            raise ValueError("correlation is invalid")


def _validate_anchor_policy(model: AuditAnchor, anchor: Mapping[str, Any]) -> None:
    _workspace_id(anchor.get("workspace_id"))
    if int(anchor.get("anchor_revision") or 0) < 1 or int(anchor.get("workspace_revision") or 0) < 1:
        raise ValueError("anchor revision is invalid")
    if not _KEY_ID.fullmatch(str(anchor.get("key_id") or "")):
        raise ValueError("anchor key_id is invalid")
    for field in ("workspace_event_hash", "previous_hash", "anchor_hash"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(anchor.get(field) or "")):
            raise ValueError(f"anchor {field} is invalid")
    if model.at.tzinfo is None or model.at.utcoffset() != timedelta(0):
        raise ValueError("anchor at must be UTC")


def _backend() -> _AppendBackend:
    if _is_production():
        if _local_mode_enabled():
            raise AuditPersistenceError("local audit mode is prohibited in production")
        if str(os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or "").strip():
            raise AuditPersistenceError("storage connection string is prohibited for production audit writes")
        if str(os.environ.get("AZURE_STORAGE_KEY") or os.environ.get("DF_STORAGE_KEY") or "").strip():
            raise AuditPersistenceError("production audit writes require managed identity")
        account_name = _storage_account_name()
        if not account_name:
            raise AuditPersistenceError("durable Blob audit storage is required in production")
        _verify_production_storage_contract(account_name)
        return _BlobAppendBackend(account_name=account_name, managed_identity_only=True)
    if blob_configured():
        return _BlobAppendBackend()
    if _local_mode_enabled():
        return _LocalAppendBackend()
    raise AuditPersistenceError("local audit mode must be enabled explicitly")


def _verify_production_storage_contract(write_account_name: str | None = None) -> str:
    resource_id = str(os.environ.get("DF_AUDIT_STORAGE_ACCOUNT_RESOURCE_ID") or "").strip().rstrip("/")
    match = _STORAGE_RESOURCE_ID.fullmatch(resource_id)
    if match is None:
        raise AuditPersistenceError("production audit storage contract resource id is missing")
    write_account = str(write_account_name or _storage_account_name()).strip().lower()
    expected_subscription = str(os.environ.get("DF_AUDIT_STORAGE_SUBSCRIPTION_ID") or "").strip()
    expected_resource_group = str(os.environ.get("DF_AUDIT_STORAGE_RESOURCE_GROUP") or "").strip()
    if not re.fullmatch(r"[a-z0-9]{3,24}", write_account):
        raise AuditPersistenceError("production audit write account is invalid")
    if match.group("account").lower() != write_account:
        raise AuditPersistenceError("production audit storage account proof does not match write account")
    if not expected_subscription or match.group("subscription").lower() != expected_subscription.lower():
        raise AuditPersistenceError("production audit storage subscription proof does not match expected subscription")
    if not expected_resource_group or match.group("resource_group").lower() != expected_resource_group.lower():
        raise AuditPersistenceError("production audit storage resource group proof does not match expected resource group")
    container_name = _audit_container_name()
    cache_key = (resource_id.lower(), write_account, container_name)
    now = time.monotonic()
    expires_at = _PRODUCTION_CONTRACT_CACHE.get(cache_key)
    if expires_at is not None and now < expires_at:
        return resource_id
    _PRODUCTION_CONTRACT_CACHE.pop(cache_key, None)
    api = "api-version=2025-06-01"
    service = _management_get_json(f"{resource_id}/blobServices/default?{api}").get("properties")
    policy = _management_get_json(
        f"{resource_id}/blobServices/default/containers/{container_name}/immutabilityPolicies/default?{api}"
    ).get("properties")
    _validate_production_contract(service, policy)
    _PRODUCTION_CONTRACT_CACHE[cache_key] = now + PRODUCTION_CONTRACT_CACHE_TTL_SECONDS
    return resource_id


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


def _event_stream_name(workspace_id: str, _segment: int | None = None) -> str:
    return f"workspaces/{_workspace_id(workspace_id)}/events.jsonl"


def _anchor_stream_name(workspace_id: str) -> str:
    return f"workspaces/{_workspace_id(workspace_id)}/anchors.jsonl"


def _local_etag(stat_result: os.stat_result) -> str:
    return f'"{int(stat_result.st_mtime_ns):x}-{int(stat_result.st_size):x}"'


def _snapshot_from_tail(name: str, data: bytes, length: int, etag: str | None) -> _StreamSnapshot:
    if length < 0 or len(data) != min(length, STREAM_TAIL_BYTES):
        raise AuditIntegrityError("audit stream snapshot length is invalid")
    if length == 0:
        if data:
            raise AuditIntegrityError("empty audit stream snapshot is invalid")
        return _StreamSnapshot(name=name, data=b"", head=None, length=0, etag=etag)
    if etag is None or not data.endswith(b"\n"):
        raise AuditIntegrityError("audit stream snapshot is truncated")
    complete = data
    if length > len(data):
        boundary = data.find(b"\n")
        if boundary < 0:
            raise AuditIntegrityError("audit stream record exceeds the bounded tail")
        complete = data[boundary + 1 :]
    lines = complete.splitlines()
    if not lines or len(lines[-1]) + 1 > MAX_STREAM_RECORD_BYTES:
        raise AuditIntegrityError("audit stream head exceeds the record limit")
    try:
        head = json.loads(lines[-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditIntegrityError("audit stream head is invalid") from exc
    if not isinstance(head, dict):
        raise AuditIntegrityError("audit stream head is invalid")
    return _StreamSnapshot(name=name, data=data, head=head, length=length, etag=etag)


def _validate_append_request(name: str, payload: bytes, snapshot: _StreamSnapshot) -> None:
    if snapshot.name != name or snapshot.length < 0:
        raise AuditPersistenceError("audit append snapshot does not match stream")
    if not payload.endswith(b"\n") or len(payload) > MAX_STREAM_RECORD_BYTES:
        raise AuditPersistenceError("audit append record exceeds the bounded record contract")
    parsed = _parse_lines(payload, "audit append record")
    if len(parsed) != 1:
        raise AuditPersistenceError("audit append must contain exactly one record")


def _parse_lines(payload: bytes, label: str) -> list[dict[str, Any]]:
    if not payload:
        return []
    if not payload.endswith(b"\n"):
        raise AuditIntegrityError(f"{label} is truncated")
    values = []
    for line in payload.splitlines():
        if len(line) + 1 > MAX_STREAM_RECORD_BYTES:
            raise AuditIntegrityError(f"{label} record exceeds the bounded record contract")
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
