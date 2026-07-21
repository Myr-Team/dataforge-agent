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
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
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
MAX_RECORDS_PER_SEGMENT = 10_000
MAX_SEGMENT_INDEX = 99_999_999
PRODUCTION_CONTRACT_CACHE_TTL_SECONDS = 60.0
LOCAL_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_SLEEP_SECONDS = 0.01
_PRODUCTION_CONTRACT_CACHE: dict[tuple[str, str, str, str, str], float] = {}

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
_WORKSPACE_SCOPE = re.compile(r"^ws_[0-9a-f]{40}$")
_RESOURCE_SCOPE = re.compile(r"^res_[0-9a-f]{40}$")
_STORAGE_RESOURCE_ID = re.compile(
    r"^/subscriptions/(?P<subscription>[^/]+)/resourceGroups/(?P<resource_group>[^/]+)/providers/"
    r"Microsoft\.Storage/storageAccounts/(?P<account>[^/]+)$",
    re.IGNORECASE,
)


class _WorkspaceScopeId(str):
    pass


class _ResourceScopeId(str):
    pass


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
    record_count: int
    sealed: bool = False
    content_sha256: str | None = None


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
    event_segment_index: int
    event_stream_name: str
    event_stream_length: int
    event_segment_record_count: int
    at: datetime
    previous_hash: str
    key_id: str
    anchor_hash: str


class GlobalAuditAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_sequence: int
    workspace_id: str
    workspace_revision: int
    workspace_event_hash: str
    workspace_anchor_hash: str
    workspace_anchor_segment_index: int
    workspace_anchor_stream_name: str
    workspace_anchor_stream_length: int
    workspace_anchor_segment_record_count: int
    event_segment_index: int
    event_stream_name: str
    event_stream_length: int
    event_segment_record_count: int
    global_segment_index: int
    global_stream_name: str
    previous_global_segment_index: int
    previous_global_stream_name: str
    previous_global_stream_length: int
    previous_global_segment_record_count: int
    at: datetime
    previous_hash: str
    key_id: str
    global_hash: str


class WorkspaceGlobalReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_revision: int
    workspace_id: str
    workspace_revision: int
    workspace_anchor_hash: str
    workspace_anchor_segment_index: int
    workspace_anchor_stream_name: str
    workspace_anchor_stream_length: int
    workspace_anchor_segment_record_count: int
    global_sequence: int
    global_hash: str
    global_segment_index: int
    global_stream_name: str
    global_stream_length: int
    global_segment_record_count: int
    at: datetime
    previous_hash: str
    key_id: str
    receipt_hash: str


class _AppendBackend(Protocol):
    def read_snapshot(self, name: str) -> _StreamSnapshot: ...
    def read_full(self, name: str, snapshot: _StreamSnapshot) -> bytes: ...
    def read_range(self, name: str, offset: int, length: int, snapshot: _StreamSnapshot) -> bytes: ...
    def list_names(self, prefix: str, limit: int | None = None) -> list[str]: ...
    def append(self, name: str, payload: bytes, snapshot: _StreamSnapshot) -> None: ...
    def seal(self, name: str, snapshot: _StreamSnapshot) -> _StreamSnapshot: ...


class _LocalAppendBackend:
    def read_snapshot(self, name: str) -> _StreamSnapshot:
        path, sealed = _local_read_path(name)
        return self._read_path_snapshot(name, path, sealed=sealed)

    @staticmethod
    def _read_path_snapshot(name: str, path: Path, *, sealed: bool) -> _StreamSnapshot:
        if not path.exists():
            return _StreamSnapshot(name=name, data=b"", head=None, length=0, etag=None, record_count=0)
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
        record_count = _count_local_records(path)
        content_sha256 = _read_local_seal_metadata(name, int(after.st_size), record_count) if sealed else None
        return _snapshot_from_tail(
            name,
            data,
            int(after.st_size),
            _local_etag(after),
            record_count,
            sealed=sealed,
            content_sha256=content_sha256,
        )

    def read_full(self, name: str, snapshot: _StreamSnapshot) -> bytes:
        if snapshot.name != name:
            raise AuditPersistenceError("local audit full-read snapshot is invalid")
        if snapshot.etag is None:
            return b""
        path = _local_sealed_stream_path(name) if snapshot.sealed else _local_active_stream_path(name)
        if snapshot.sealed:
            data = _read_local_segment_bytes(path, snapshot, "sealed audit historical segment")
            _validate_sealed_content_digest(snapshot, data, "sealed audit historical segment")
            return data
        return _read_local_segment_bytes(path, snapshot, "active audit segment full read")

    def read_range(self, name: str, offset: int, length: int, snapshot: _StreamSnapshot) -> bytes:
        path = _local_sealed_stream_path(name) if snapshot.sealed else _local_active_stream_path(name)
        if offset < 0 or length < 0 or offset + length > snapshot.length:
            raise AuditPersistenceError("local audit range is invalid")
        try:
            before = path.stat()
            if before.st_size != snapshot.length or _local_etag(before) != snapshot.etag:
                raise _AppendConflict("local stream changed before range read")
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(length)
            after = path.stat()
        except _AppendConflict:
            raise
        except OSError as exc:
            raise AuditPersistenceError("local audit range read failed") from exc
        if len(data) != length or _local_etag(after) != snapshot.etag:
            raise _AppendConflict("local stream changed during range read")
        return data

    def list_names(self, prefix: str, limit: int | None = None) -> list[str]:
        names: set[str] = set()
        for root in (_local_sealed_root(), _local_active_root()):
            base = _local_scoped_path(root, prefix)
            if base.exists():
                names.update(path.resolve().relative_to(root).as_posix() for path in base.rglob("*.jsonl") if path.is_file())
        ordered = sorted(names)
        return ordered[:limit] if limit is not None else ordered

    def append(self, name: str, payload: bytes, snapshot: _StreamSnapshot) -> None:
        _validate_append_request(name, payload, snapshot)
        if snapshot.sealed or _local_sealed_stream_path(name).exists():
            raise AuditIntegrityError("sealed audit segment cannot be appended")
        path = _local_active_stream_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if snapshot.etag is not None and path.exists():
            current = path.stat()
            if current.st_size != snapshot.length or _local_etag(current) != snapshot.etag:
                raise _AppendConflict("local append snapshot changed")
        elif snapshot.etag is not None or snapshot.length != 0:
            raise _AppendConflict("local append stream disappeared")
        try:
            flags = os.O_APPEND | os.O_WRONLY
            flags |= getattr(os, "O_BINARY", 0)
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

    def seal(self, name: str, snapshot: _StreamSnapshot) -> _StreamSnapshot:
        if snapshot.name != name or snapshot.record_count != MAX_RECORDS_PER_SEGMENT:
            raise AuditPersistenceError("only a full audit segment can be sealed")
        source = _local_active_stream_path(name)
        destination = _local_sealed_stream_path(name)
        if snapshot.sealed:
            destination_data = _read_local_segment_bytes(destination, snapshot, "sealed audit segment")
            if not hmac.compare_digest(hashlib.sha256(destination_data).hexdigest(), str(snapshot.content_sha256)):
                raise AuditIntegrityError("sealed audit content digest does not match signed seal")
            return snapshot
        source_snapshot = self._read_path_snapshot(name, source, sealed=False)
        if (
            source_snapshot.length != snapshot.length
            or source_snapshot.record_count != snapshot.record_count
            or source_snapshot.head != snapshot.head
        ):
            raise _AppendConflict("active audit segment changed before sealing")
        source_data = _read_local_segment_bytes(source, source_snapshot, "active audit segment")
        source_digest = hashlib.sha256(source_data).hexdigest()
        try:
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    destination,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
                    0o400,
                )
                try:
                    view = memoryview(source_data)
                    while view:
                        view = view[os.write(descriptor, view) :]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except FileExistsError:
            pass
        except OSError as exc:
            raise AuditPersistenceError("local audit segment sealing failed") from exc
        destination_data = _read_local_segment_bytes(
            destination,
            self._read_path_snapshot(name, destination, sealed=False),
            "sealed audit segment",
        )
        if not hmac.compare_digest(hashlib.sha256(destination_data).hexdigest(), source_digest):
            raise AuditIntegrityError("sealed audit content digest does not match source")
        metadata = _build_seal_metadata(name, source_snapshot, source_digest)
        _write_or_validate_local_seal_metadata(name, metadata, source_snapshot, source_digest)
        sealed = self.read_snapshot(name)
        return _validate_sealed_snapshot(sealed, source_snapshot, expected_content_sha256=source_digest)


class _BlobAppendBackend:
    def __init__(self, *, account_name: str | None = None, managed_identity_only: bool = False) -> None:
        connection_string = str(os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or "").strip()
        if managed_identity_only:
            account = str(account_name or "").strip()
            if not account:
                raise AuditPersistenceError("durable audit storage account is not configured")
            credential: Any = ManagedIdentityCredential()
            service = BlobServiceClient(
                account_url=f"https://{account}.blob.core.windows.net",
                credential=credential,
            )
        elif connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
            credential = service.credential
        else:
            account = str(account_name or _storage_account_name()).strip()
            if not account:
                raise AuditPersistenceError("durable audit storage account is not configured")
            credential = os.environ.get("AZURE_STORAGE_KEY") or os.environ.get("DF_STORAGE_KEY") or DefaultAzureCredential()
            service = BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net", credential=credential)
        self.credential = credential
        self.container = service.get_container_client(_audit_container_name())
        self.sealed_container = service.get_container_client(_audit_sealed_container_name())
        if not _is_production():
            for container in (self.container, self.sealed_container):
                try:
                    container.create_container()
                except ResourceExistsError:
                    pass

    def read_snapshot(self, name: str) -> _StreamSnapshot:
        sealed_client = self.sealed_container.get_blob_client(name)
        try:
            properties = sealed_client.get_blob_properties()
        except ResourceNotFoundError:
            sealed_client = None
        except Exception as exc:
            raise AuditPersistenceError("sealed audit properties read failed") from exc
        if sealed_client is not None:
            return self._snapshot_from_properties(name, sealed_client, properties, sealed=True)
        return self._read_active_snapshot(name)

    def _read_active_snapshot(self, name: str) -> _StreamSnapshot:
        client = self.container.get_blob_client(name)
        try:
            properties = client.get_blob_properties()
        except ResourceNotFoundError:
            return _StreamSnapshot(name=name, data=b"", head=None, length=0, etag=None, record_count=0)
        except Exception as exc:
            raise AuditPersistenceError("durable audit properties read failed") from exc
        return self._snapshot_from_properties(name, client, properties, sealed=False)

    def _snapshot_from_properties(self, name: str, client: Any, properties: Any, *, sealed: bool) -> _StreamSnapshot:
        length = int(properties.size or 0)
        etag = str(properties.etag or "")
        if not etag:
            raise AuditPersistenceError("durable audit stream ETag is missing")
        blob_type = str(properties.blob_type or "").lower()
        if sealed:
            if "block" not in blob_type or "append" in blob_type:
                raise AuditIntegrityError("sealed audit stream is not a block blob")
            metadata = properties.metadata if isinstance(properties.metadata, Mapping) else {}
            try:
                record_count = int(metadata.get("df_record_count") or 0)
            except (TypeError, ValueError) as exc:
                raise AuditIntegrityError("sealed audit record count metadata is invalid") from exc
            content_sha256 = _validated_seal_metadata(name, length, record_count, metadata)
        else:
            if "append" not in blob_type:
                raise AuditIntegrityError("active audit stream is not an append blob")
            record_count = properties.append_blob_committed_block_count
            content_sha256 = None
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
        if not isinstance(record_count, int) or record_count < 0:
            raise AuditPersistenceError("durable audit block count is missing")
        return _snapshot_from_tail(
            name,
            data,
            length,
            etag,
            record_count,
            sealed=sealed,
            content_sha256=content_sha256,
        )

    def read_full(self, name: str, snapshot: _StreamSnapshot) -> bytes:
        if snapshot.name != name:
            raise AuditPersistenceError("durable audit full-read snapshot is invalid")
        if snapshot.etag is None:
            return b""
        client = (self.sealed_container if snapshot.sealed else self.container).get_blob_client(name)
        if snapshot.sealed:
            data = _download_blob_segment(client, snapshot, "sealed audit historical segment")
            _validate_sealed_content_digest(snapshot, data, "sealed audit historical segment")
            return data
        try:
            return bytes(
                client.download_blob(etag=snapshot.etag, match_condition=MatchConditions.IfNotModified).readall()
            )
        except HttpResponseError as exc:
            if exc.status_code in {409, 412}:
                raise _AppendConflict("durable stream changed during full read") from exc
            raise AuditPersistenceError("durable audit read failed") from exc
        except Exception as exc:
            if isinstance(exc, AuditPersistenceError):
                raise
            raise AuditPersistenceError("durable audit read failed") from exc

    def read_range(self, name: str, offset: int, length: int, snapshot: _StreamSnapshot) -> bytes:
        if offset < 0 or length < 0 or offset + length > snapshot.length or snapshot.etag is None:
            raise AuditPersistenceError("durable audit range is invalid")
        client = (self.sealed_container if snapshot.sealed else self.container).get_blob_client(name)
        try:
            return bytes(
                client.download_blob(
                    offset=offset,
                    length=length,
                    etag=snapshot.etag,
                    match_condition=MatchConditions.IfNotModified,
                ).readall()
            )
        except HttpResponseError as exc:
            if exc.status_code in {409, 412}:
                raise _AppendConflict("durable stream changed during range read") from exc
            raise AuditPersistenceError("durable audit range read failed") from exc
        except Exception as exc:
            raise AuditPersistenceError("durable audit range read failed") from exc

    def list_names(self, prefix: str, limit: int | None = None) -> list[str]:
        try:
            names = set(self._list_container_names(self.sealed_container, prefix, limit))
            names.update(self._list_container_names(self.container, prefix, limit))
            ordered = sorted(names)
            return ordered[:limit] if limit is not None else ordered
        except Exception as exc:
            raise AuditPersistenceError("durable audit segment listing failed") from exc

    @staticmethod
    def _list_container_names(container: Any, prefix: str, limit: int | None) -> list[str]:
        listing = container.list_blobs(
            name_starts_with=prefix,
            **({"results_per_page": limit} if limit is not None else {}),
        )
        if limit is None:
            return [str(item.name) for item in listing]
        names: list[str] = []
        for page in listing.by_page():
            names.extend(str(item.name) for item in page)
            break
        return names[:limit]

    def append(self, name: str, payload: bytes, snapshot: _StreamSnapshot) -> None:
        _validate_append_request(name, payload, snapshot)
        if snapshot.sealed:
            raise AuditIntegrityError("sealed audit segment cannot be appended")
        try:
            self.sealed_container.get_blob_client(name).get_blob_properties()
        except ResourceNotFoundError:
            pass
        except Exception as exc:
            raise AuditPersistenceError("sealed audit properties read failed") from exc
        else:
            raise AuditIntegrityError("sealed audit segment cannot be appended")
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

    def seal(self, name: str, snapshot: _StreamSnapshot) -> _StreamSnapshot:
        if snapshot.name != name or snapshot.record_count != MAX_RECORDS_PER_SEGMENT:
            raise AuditPersistenceError("only a full audit segment can be sealed")
        source = self.container.get_blob_client(name)
        destination = self.sealed_container.get_blob_client(name)
        if snapshot.sealed:
            destination_data = _download_blob_segment(destination, snapshot, "sealed audit segment")
            if not hmac.compare_digest(hashlib.sha256(destination_data).hexdigest(), str(snapshot.content_sha256)):
                raise AuditIntegrityError("sealed audit content digest does not match signed seal")
            return snapshot
        source_snapshot = snapshot
        try:
            properties = source.get_blob_properties()
            if (
                int(properties.size or 0) != source_snapshot.length
                or str(properties.etag or "") != source_snapshot.etag
                or "append" not in str(properties.blob_type or "").lower()
                or properties.append_blob_committed_block_count != source_snapshot.record_count
            ):
                raise _AppendConflict("active audit segment changed before sealing")
            source_data = _download_blob_segment(source, source_snapshot, "active audit segment")
            source_digest = hashlib.sha256(source_data).hexdigest()
            metadata = _build_seal_metadata(name, source_snapshot, source_digest)
            if hasattr(self.credential, "get_token"):
                token = self.credential.get_token("https://storage.azure.com/.default").token
                destination.upload_blob_from_url(
                    source.url,
                    overwrite=False,
                    metadata=metadata,
                    source_authorization=f"Bearer {token}",
                    source_etag=source_snapshot.etag,
                    source_match_condition=MatchConditions.IfNotModified,
                    include_source_blob_properties=True,
                )
            else:
                destination.upload_blob(
                    source_data,
                    overwrite=False,
                    metadata=metadata,
                    blob_type="BlockBlob",
                    content_settings=ContentSettings(content_type="application/x-ndjson; charset=utf-8"),
                )
        except ResourceExistsError:
            pass
        except HttpResponseError as exc:
            if exc.status_code in {409, 412}:
                try:
                    destination.get_blob_properties()
                except ResourceNotFoundError:
                    raise _AppendConflict("active audit segment changed during sealing") from exc
            else:
                raise AuditPersistenceError("durable audit segment sealing failed") from exc
        except _AppendConflict:
            raise
        except Exception as exc:
            raise AuditPersistenceError("durable audit segment sealing failed") from exc
        sealed = self.read_snapshot(name)
        _validate_sealed_snapshot(sealed, source_snapshot, expected_content_sha256=source_digest)
        try:
            sealed_properties = destination.get_blob_properties()
            sealed_metadata = sealed_properties.metadata if isinstance(sealed_properties.metadata, Mapping) else {}
        except Exception as exc:
            raise AuditPersistenceError("sealed audit metadata verification failed") from exc
        _validated_seal_metadata(
            name,
            source_snapshot.length,
            source_snapshot.record_count,
            sealed_metadata,
            expected_source_etag_sha256=hashlib.sha256(str(source_snapshot.etag).encode("ascii")).hexdigest(),
            expected_content_sha256=source_digest,
        )
        destination_data = _download_blob_segment(destination, sealed, "sealed audit segment")
        destination_digest = hashlib.sha256(destination_data).hexdigest()
        if not hmac.compare_digest(destination_digest, source_digest):
            raise AuditIntegrityError("sealed audit content digest does not match source")
        return sealed


@dataclass(frozen=True)
class _SegmentHead:
    index: int
    name: str
    snapshot: _StreamSnapshot
    head: dict[str, Any] | None


@dataclass(frozen=True)
class _PhysicalCoordinate:
    segment_index: int
    stream_name: str
    stream_length: int
    segment_record_count: int


@dataclass(frozen=True)
class _WorkspaceHeadState:
    workspace_id: str
    event: _SegmentHead
    anchor: _SegmentHead
    receipt: _SegmentHead
    global_anchor: _SegmentHead
    status: Literal["complete", "event_gap", "anchor_gap"]


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
    workspace = _workspace_scope_id(workspace_id)
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
    target_event: dict[str, Any] | None = None
    for _attempt in range(MAX_APPEND_RETRIES):
        try:
            state = _load_workspace_head_state(backend, workspace_id)
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
        if state.status != "complete":
            try:
                _recover_workspace_head(backend, state)
            except _AppendConflict:
                time.sleep(_LOCK_SLEEP_SECONDS)
                continue
            continue
        event_head = state.event.head
        if event_head is not None and event_head.get("event_id") == event_id:
            return event_head
        if target_event is not None:
            persisted = _event_by_id(_read_anchored_events(backend, workspace_id), event_id)
            if persisted is not None:
                return persisted
            raise AuditIntegrityError("appended audit event disappeared before global anchoring")
        event_segment = _segment_for_append(backend, state.event, _event_stream_name, workspace_id)
        event = _build_event(
            {"actor_hash": actor_hash}, action, resource, metadata,
            revision=int(event_head["revision"]) + 1 if event_head else 1,
            previous_hash=str(event_head["event_hash"]) if event_head else GENESIS_HASH,
            event_id=event_id, at=at, key_id=key_id, _metadata_clean=True,
        )
        event_line = _line(event)
        try:
            backend.append(event_segment.name, event_line, event_segment.snapshot)
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
        target_event = event
        event_coordinate = _post_coordinate(event_segment, event_line)
        try:
            anchor, anchor_coordinate = _append_workspace_anchor(
                backend, state.anchor, workspace_id, event, event_coordinate
            )
            global_anchor, global_coordinate = _append_new_global_anchor(
                backend, anchor, anchor_coordinate, event_coordinate
            )
            _append_workspace_receipt(
                backend, state.receipt, workspace_id, anchor, anchor_coordinate, global_anchor, global_coordinate
            )
            return event
        except _AppendConflict:
            time.sleep(_LOCK_SLEEP_SECONDS)
            continue
    raise AuditPersistenceError("durable audit append conflict")


def _recover_workspace_head(backend: _AppendBackend, state: _WorkspaceHeadState) -> None:
    if state.status == "event_gap":
        if state.event.head is None:
            raise AuditIntegrityError("audit recovery event is missing")
        event_coordinate = _coordinate(state.event)
        _append_workspace_anchor(
            backend, state.anchor, state.workspace_id, state.event.head, event_coordinate
        )
        return
    if state.status != "anchor_gap" or state.anchor.head is None:
        raise AuditIntegrityError("audit recovery state is invalid")
    anchor = state.anchor.head
    anchor_coordinate = _coordinate(state.anchor)
    global_match = _find_global_anchor(backend, str(anchor["anchor_hash"]))
    if global_match is None:
        event_coordinate = _coordinate_from_anchor(anchor)
        global_anchor, global_coordinate = _append_new_global_anchor(
            backend, anchor, anchor_coordinate, event_coordinate
        )
    else:
        global_anchor, global_coordinate = global_match
    _append_workspace_receipt(
        backend,
        state.receipt,
        state.workspace_id,
        anchor,
        anchor_coordinate,
        global_anchor,
        global_coordinate,
    )


def _append_workspace_anchor(
    backend: _AppendBackend,
    current: _SegmentHead,
    workspace_id: str,
    event: Mapping[str, Any],
    event_coordinate: _PhysicalCoordinate,
) -> tuple[dict[str, Any], _PhysicalCoordinate]:
    target_revision = int(event["revision"])
    if current.head is not None and int(current.head["workspace_revision"]) >= target_revision:
        if (
            int(current.head["workspace_revision"]) == target_revision
            and hmac.compare_digest(str(current.head["workspace_event_hash"]), str(event["event_hash"]))
        ):
            return current.head, _coordinate(current)
        raise AuditIntegrityError("workspace anchor advanced past target event")
    expected_revision = int(current.head["anchor_revision"]) + 1 if current.head else 1
    if target_revision != expected_revision:
        raise AuditIntegrityError("workspace anchor recovery has more than one gap")
    target = _segment_for_append(backend, current, _anchor_stream_name, workspace_id)
    key_id, _key = _active_key()
    anchor = _build_anchor(
        anchor_revision=target_revision,
        workspace_id=workspace_id,
        workspace_revision=target_revision,
        workspace_event_hash=str(event["event_hash"]),
        event_segment_index=event_coordinate.segment_index,
        event_stream_name=event_coordinate.stream_name,
        event_stream_length=event_coordinate.stream_length,
        event_segment_record_count=event_coordinate.segment_record_count,
        previous_hash=str(current.head["anchor_hash"]) if current.head else GENESIS_HASH,
        key_id=key_id,
    )
    payload = _line(anchor)
    backend.append(target.name, payload, target.snapshot)
    return anchor, _post_coordinate(target, payload)


def _append_new_global_anchor(
    backend: _AppendBackend,
    anchor: Mapping[str, Any],
    anchor_coordinate: _PhysicalCoordinate,
    event_coordinate: _PhysicalCoordinate,
) -> tuple[dict[str, Any], _PhysicalCoordinate]:
    current = _load_global_head(backend)
    target = _segment_for_append(backend, current, _global_stream_name, None)
    sequence = int(current.head["global_sequence"]) + 1 if current.head else 1
    previous_coordinate = _coordinate(current) if current.head else _empty_coordinate()
    key_id, _key = _active_key()
    global_anchor = _build_global_anchor(
        global_sequence=sequence,
        workspace_id=str(anchor["workspace_id"]),
        workspace_revision=int(anchor["workspace_revision"]),
        workspace_event_hash=str(anchor["workspace_event_hash"]),
        workspace_anchor_hash=str(anchor["anchor_hash"]),
        workspace_anchor_segment_index=anchor_coordinate.segment_index,
        workspace_anchor_stream_name=anchor_coordinate.stream_name,
        workspace_anchor_stream_length=anchor_coordinate.stream_length,
        workspace_anchor_segment_record_count=anchor_coordinate.segment_record_count,
        event_segment_index=event_coordinate.segment_index,
        event_stream_name=event_coordinate.stream_name,
        event_stream_length=event_coordinate.stream_length,
        event_segment_record_count=event_coordinate.segment_record_count,
        global_segment_index=target.index,
        global_stream_name=target.name,
        previous_global_segment_index=previous_coordinate.segment_index,
        previous_global_stream_name=previous_coordinate.stream_name,
        previous_global_stream_length=previous_coordinate.stream_length,
        previous_global_segment_record_count=previous_coordinate.segment_record_count,
        previous_hash=str(current.head["global_hash"]) if current.head else GENESIS_HASH,
        key_id=key_id,
    )
    payload = _line(global_anchor)
    backend.append(target.name, payload, target.snapshot)
    return global_anchor, _post_coordinate(target, payload)


def _append_workspace_receipt(
    backend: _AppendBackend,
    current: _SegmentHead,
    workspace_id: str,
    anchor: Mapping[str, Any],
    anchor_coordinate: _PhysicalCoordinate,
    global_anchor: Mapping[str, Any],
    global_coordinate: _PhysicalCoordinate,
) -> tuple[dict[str, Any], _PhysicalCoordinate]:
    target_revision = int(anchor["workspace_revision"])
    if current.head is not None and int(current.head["workspace_revision"]) >= target_revision:
        if (
            int(current.head["workspace_revision"]) == target_revision
            and hmac.compare_digest(str(current.head["workspace_anchor_hash"]), str(anchor["anchor_hash"]))
            and hmac.compare_digest(str(current.head["global_hash"]), str(global_anchor["global_hash"]))
        ):
            return current.head, _coordinate(current)
        raise AuditIntegrityError("workspace global receipt advanced past target anchor")
    expected_revision = int(current.head["receipt_revision"]) + 1 if current.head else 1
    if target_revision != expected_revision:
        raise AuditIntegrityError("workspace global receipt recovery has more than one gap")
    target = _segment_for_append(backend, current, _receipt_stream_name, workspace_id)
    key_id, _key = _active_key()
    receipt = _build_receipt(
        receipt_revision=target_revision,
        workspace_id=workspace_id,
        workspace_revision=target_revision,
        workspace_anchor_hash=str(anchor["anchor_hash"]),
        workspace_anchor_segment_index=anchor_coordinate.segment_index,
        workspace_anchor_stream_name=anchor_coordinate.stream_name,
        workspace_anchor_stream_length=anchor_coordinate.stream_length,
        workspace_anchor_segment_record_count=anchor_coordinate.segment_record_count,
        global_sequence=int(global_anchor["global_sequence"]),
        global_hash=str(global_anchor["global_hash"]),
        global_segment_index=global_coordinate.segment_index,
        global_stream_name=global_coordinate.stream_name,
        global_stream_length=global_coordinate.stream_length,
        global_segment_record_count=global_coordinate.segment_record_count,
        previous_hash=str(current.head["receipt_hash"]) if current.head else GENESIS_HASH,
        key_id=key_id,
    )
    payload = _line(receipt)
    backend.append(target.name, payload, target.snapshot)
    return receipt, _post_coordinate(target, payload)


def _read_anchored_events(backend: _AppendBackend, workspace_id: str) -> list[dict[str, Any]]:
    events, event_coordinates = _read_events_with_coordinates(backend, workspace_id)
    anchors, anchor_coordinates = _read_anchors_with_coordinates(backend, workspace_id)
    receipts, _receipt_coordinates = _read_receipts_with_coordinates(backend, workspace_id)
    global_anchors, global_coordinates = _read_global_anchors_with_coordinates(backend)
    _require_anchor_match(
        workspace_id, events, event_coordinates, anchors, anchor_coordinates,
        receipts, global_anchors, global_coordinates,
    )
    return events


def _read_events(backend: _AppendBackend, workspace_id: str) -> list[dict[str, Any]]:
    return _read_events_with_coordinates(backend, workspace_id)[0]


def _read_events_with_coordinates(
    backend: _AppendBackend, workspace_id: str
) -> tuple[list[dict[str, Any]], list[_PhysicalCoordinate]]:
    raw = _read_segment_records(backend, _event_prefix(workspace_id), "audit event stream")
    events: list[dict[str, Any]] = []
    coordinates: list[_PhysicalCoordinate] = []
    previous_hash = GENESIS_HASH
    seen: set[str] = set()
    for revision, (value, coordinate) in enumerate(raw, start=1):
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
        coordinates.append(coordinate)
    return events, coordinates


def _read_anchors(backend: _AppendBackend, workspace_id: str) -> list[dict[str, Any]]:
    return _read_anchors_with_coordinates(backend, workspace_id)[0]


def _read_anchors_with_coordinates(
    backend: _AppendBackend, workspace_id: str
) -> tuple[list[dict[str, Any]], list[_PhysicalCoordinate]]:
    raw = _read_segment_records(backend, _anchor_prefix(workspace_id), "audit anchor stream")
    anchors: list[dict[str, Any]] = []
    coordinates: list[_PhysicalCoordinate] = []
    previous_hash = GENESIS_HASH
    for revision, (value, coordinate) in enumerate(raw, start=1):
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
        if not hmac.compare_digest(anchor["anchor_hash"], _hash_anchor(anchor)):
            raise AuditIntegrityError("audit anchor hash is invalid")
        previous_hash = anchor["anchor_hash"]
        anchors.append(anchor)
        coordinates.append(coordinate)
    return anchors, coordinates


def _read_receipts_with_coordinates(
    backend: _AppendBackend, workspace_id: str
) -> tuple[list[dict[str, Any]], list[_PhysicalCoordinate]]:
    raw = _read_segment_records(backend, _receipt_prefix(workspace_id), "workspace global receipt stream")
    receipts: list[dict[str, Any]] = []
    coordinates: list[_PhysicalCoordinate] = []
    previous_hash = GENESIS_HASH
    for revision, (value, coordinate) in enumerate(raw, start=1):
        receipt = _validated_receipt(value, workspace_id)
        if (
            int(receipt["receipt_revision"]) != revision
            or int(receipt["workspace_revision"]) != revision
            or receipt["previous_hash"] != previous_hash
        ):
            raise AuditIntegrityError("workspace global receipt chain is invalid")
        previous_hash = str(receipt["receipt_hash"])
        receipts.append(receipt)
        coordinates.append(coordinate)
    return receipts, coordinates


def _read_global_anchors_with_coordinates(
    backend: _AppendBackend,
) -> tuple[list[dict[str, Any]], list[_PhysicalCoordinate]]:
    raw = _read_segment_records(backend, _global_prefix(), "global audit anchor stream")
    anchors: list[dict[str, Any]] = []
    coordinates: list[_PhysicalCoordinate] = []
    previous_hash = GENESIS_HASH
    previous_coordinate = _empty_coordinate()
    for sequence, (value, coordinate) in enumerate(raw, start=1):
        anchor = _validated_global_anchor(value)
        if int(anchor["global_sequence"]) != sequence or anchor["previous_hash"] != previous_hash:
            raise AuditIntegrityError("global audit anchor chain is invalid")
        if not _global_previous_coordinate_matches(anchor, previous_coordinate):
            raise AuditIntegrityError("global audit physical chain is invalid")
        if int(anchor["global_segment_index"]) != coordinate.segment_index or anchor["global_stream_name"] != coordinate.stream_name:
            raise AuditIntegrityError("global audit segment identity is invalid")
        previous_hash = str(anchor["global_hash"])
        previous_coordinate = coordinate
        anchors.append(anchor)
        coordinates.append(coordinate)
    return anchors, coordinates


def _require_anchor_match(
    workspace_id: str,
    events: list[dict[str, Any]],
    event_coordinates: list[_PhysicalCoordinate],
    anchors: list[dict[str, Any]],
    anchor_coordinates: list[_PhysicalCoordinate],
    receipts: list[dict[str, Any]],
    global_anchors: list[dict[str, Any]],
    global_coordinates: list[_PhysicalCoordinate],
) -> None:
    if not events and not anchors and not receipts:
        if any(item["workspace_id"] == workspace_id for item in global_anchors):
            raise AuditIntegrityError("global audit history proves a deleted workspace prefix")
        return
    if not events:
        raise AuditIntegrityError("audit ledger is missing after being anchored")
    if len(events) != len(anchors) or len(anchors) != len(receipts):
        raise AuditIntegrityError("global audit history proves a workspace prefix rollback")
    global_by_anchor: dict[str, list[tuple[dict[str, Any], _PhysicalCoordinate]]] = {}
    for item, coordinate in zip(global_anchors, global_coordinates):
        if item["workspace_id"] == workspace_id:
            global_by_anchor.setdefault(str(item["workspace_anchor_hash"]), []).append((item, coordinate))
    if len(global_by_anchor) != len(anchors):
        raise AuditIntegrityError("global audit history proves a workspace rollback or duplicate prefix")
    for event, event_coordinate, anchor, anchor_coordinate, receipt in zip(
        events, event_coordinates, anchors, anchor_coordinates, receipts
    ):
        if (
            not hmac.compare_digest(str(anchor["workspace_event_hash"]), str(event["event_hash"]))
            or _coordinate_from_anchor(anchor) != event_coordinate
            or not hmac.compare_digest(str(receipt["workspace_anchor_hash"]), str(anchor["anchor_hash"]))
            or _anchor_coordinate_from_receipt(receipt) != anchor_coordinate
        ):
            raise AuditIntegrityError("audit physical anchor chain is invalid")
        matches = global_by_anchor.get(str(anchor["anchor_hash"]), [])
        if len(matches) != 1:
            raise AuditIntegrityError("global audit history is missing or duplicates a workspace anchor")
        global_anchor, global_coordinate = matches[0]
        if (
            int(receipt["global_sequence"]) != int(global_anchor["global_sequence"])
            or not hmac.compare_digest(str(receipt["global_hash"]), str(global_anchor["global_hash"]))
            or _global_coordinate_from_receipt(receipt) != global_coordinate
            or _workspace_anchor_coordinate_from_global(global_anchor) != anchor_coordinate
        ):
            raise AuditIntegrityError("global audit receipt physical proof is invalid")


def _load_workspace_head_state(backend: _AppendBackend, workspace_id: str) -> _WorkspaceHeadState:
    try:
        return _load_workspace_head_state_once(backend, workspace_id)
    except AuditIntegrityError as original:
        try:
            _load_workspace_head_state_once(backend, workspace_id)
        except AuditIntegrityError:
            raise original
        raise _AppendConflict("audit head streams changed during cross-stream validation") from original


def _load_workspace_head_state_once(backend: _AppendBackend, workspace_id: str) -> _WorkspaceHeadState:
    receipt = _load_latest_segment(
        backend, _receipt_prefix(workspace_id), _receipt_stream_name, workspace_id,
        lambda snapshot: _validated_receipt_head(snapshot, workspace_id), "workspace receipt",
    )
    anchor = _load_latest_segment(
        backend, _anchor_prefix(workspace_id), _anchor_stream_name, workspace_id,
        lambda snapshot: _validated_anchor_head(snapshot, workspace_id), "workspace anchor",
    )
    event = _load_latest_segment(
        backend, _event_prefix(workspace_id), _event_stream_name, workspace_id,
        lambda snapshot: _validated_event_head(snapshot, workspace_id), "workspace event",
    )
    global_anchor = _load_global_head(backend)
    event_revision = int(event.head["revision"]) if event.head else 0
    anchor_revision = int(anchor.head["workspace_revision"]) if anchor.head else 0
    receipt_revision = int(receipt.head["workspace_revision"]) if receipt.head else 0
    if (
        global_anchor.head is not None
        and global_anchor.head["workspace_id"] == workspace_id
        and int(global_anchor.head["workspace_revision"]) > receipt_revision
    ):
        raise AuditIntegrityError("global audit head proves a later workspace prefix")
    if anchor_revision > event_revision or receipt_revision > anchor_revision:
        raise AuditIntegrityError("audit workspace prefix rollback or delete detected")
    if event_revision == anchor_revision == receipt_revision:
        status: Literal["complete", "event_gap", "anchor_gap"] = "complete"
    elif event_revision == anchor_revision + 1 and anchor_revision == receipt_revision:
        status = "event_gap"
    elif event_revision == anchor_revision and anchor_revision == receipt_revision + 1:
        status = "anchor_gap"
    else:
        raise AuditIntegrityError("audit ledger has more than one uncommitted physical record")
    _validate_workspace_physical_state(backend, event, anchor, receipt, global_anchor, status)
    return _WorkspaceHeadState(workspace_id, event, anchor, receipt, global_anchor, status)


def _load_global_head(backend: _AppendBackend) -> _SegmentHead:
    current = _load_latest_segment(
        backend, _global_prefix(), _global_stream_name, None,
        _validated_global_anchor_head, "global anchor",
    )
    if current.head is not None:
        _validate_global_head_physical_chain(backend, current)
    return current


def _load_latest_segment(
    backend: _AppendBackend,
    prefix: str,
    name_factory: Any,
    workspace_id: str | None,
    validator: Any,
    label: str,
) -> _SegmentHead:
    names = backend.list_names(prefix, limit=1)
    indexed: list[tuple[int, str]] = []
    for name in names:
        index = _segment_index(prefix, name)
        if index is None:
            raise AuditIntegrityError(f"{label} segment name is invalid")
        indexed.append((index, name))
    if indexed:
        index, name = indexed[0]
    else:
        index = 1
        name = name_factory(workspace_id, index) if workspace_id is not None else name_factory(index)
    snapshot = backend.read_snapshot(name)
    head = validator(snapshot)
    if head is None:
        if indexed:
            raise AuditIntegrityError(f"{label} segment is unexpectedly empty")
        return _SegmentHead(index, name, snapshot, None)
    ordinal = _head_ordinal(head)
    expected_index, expected_count = _ordinal_coordinate(ordinal)
    if index != expected_index or snapshot.record_count != expected_count:
        raise AuditIntegrityError(f"{label} physical record count or segment identity is invalid")
    return _SegmentHead(index, name, snapshot, head)


def _segment_for_append(
    backend: _AppendBackend,
    current: _SegmentHead,
    name_factory: Any,
    workspace_id: str | None,
) -> _SegmentHead:
    if current.snapshot.record_count < MAX_RECORDS_PER_SEGMENT:
        return current
    _validate_sealed_snapshot(backend.seal(current.name, current.snapshot), current.snapshot)
    index = current.index + 1
    name = name_factory(workspace_id, index) if workspace_id is not None else name_factory(index)
    snapshot = backend.read_snapshot(name)
    if snapshot.length or snapshot.record_count or snapshot.head is not None:
        raise _AppendConflict("audit segment rotated concurrently")
    return _SegmentHead(index, name, snapshot, None)


def _segment_byte_limit() -> int:
    return MAX_RECORDS_PER_SEGMENT * MAX_STREAM_RECORD_BYTES


def _download_blob_segment(client: Any, snapshot: _StreamSnapshot, label: str) -> bytes:
    if snapshot.etag is None or snapshot.length < 1 or snapshot.length > _segment_byte_limit():
        raise AuditIntegrityError(f"{label} length is outside the bounded seal contract")
    try:
        data = bytes(
            client.download_blob(
                offset=0,
                length=snapshot.length,
                etag=snapshot.etag,
                match_condition=MatchConditions.IfNotModified,
            ).readall()
        )
    except HttpResponseError as exc:
        if exc.status_code in {409, 412}:
            raise _AppendConflict(f"{label} changed during content hashing") from exc
        raise AuditPersistenceError(f"{label} content hashing failed") from exc
    except Exception as exc:
        raise AuditPersistenceError(f"{label} content hashing failed") from exc
    _validate_exact_segment_bytes(data, snapshot, label)
    return data


def _read_local_segment_bytes(path: Path, snapshot: _StreamSnapshot, label: str) -> bytes:
    if snapshot.etag is None or snapshot.length < 1 or snapshot.length > _segment_byte_limit():
        raise AuditIntegrityError(f"{label} length is outside the bounded seal contract")
    try:
        before = path.stat()
        if before.st_size != snapshot.length or _local_etag(before) != snapshot.etag:
            raise _AppendConflict(f"{label} changed before content hashing")
        data = path.read_bytes()
        after = path.stat()
    except _AppendConflict:
        raise
    except OSError as exc:
        raise AuditPersistenceError(f"{label} content hashing failed") from exc
    if _local_etag(after) != snapshot.etag:
        raise _AppendConflict(f"{label} changed during content hashing")
    _validate_exact_segment_bytes(data, snapshot, label)
    return data


def _validate_exact_segment_bytes(data: bytes, snapshot: _StreamSnapshot, label: str) -> None:
    tail_length = min(snapshot.length, STREAM_TAIL_BYTES)
    if (
        len(data) != snapshot.length
        or data.count(b"\n") != snapshot.record_count
        or data[-tail_length:] != snapshot.data
    ):
        raise AuditIntegrityError(f"{label} bytes do not match the validated snapshot")


def _validate_sealed_content_digest(snapshot: _StreamSnapshot, data: bytes, label: str) -> None:
    expected = str(snapshot.content_sha256 or "")
    actual = hashlib.sha256(data).hexdigest()
    if (
        not snapshot.sealed
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
        or not hmac.compare_digest(actual, expected)
    ):
        raise AuditIntegrityError(f"{label} content digest does not match signed seal")


def _seal_envelope(
    name: str,
    length: int,
    record_count: int,
    source_etag_sha256: str,
    content_sha256: str,
    key_id: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "stream_name": name,
        "stream_length": length,
        "record_count": record_count,
        "source_etag_sha256": source_etag_sha256,
        "content_sha256": content_sha256,
        "key_id": key_id,
    }


def _build_seal_metadata(name: str, source: _StreamSnapshot, content_sha256: str) -> dict[str, str]:
    if source.etag is None or not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
        raise AuditIntegrityError("audit segment seal source identity is invalid")
    source_etag_sha256 = hashlib.sha256(source.etag.encode("ascii")).hexdigest()
    key_id, key = _active_key()
    envelope = _seal_envelope(
        name,
        source.length,
        source.record_count,
        source_etag_sha256,
        content_sha256,
        key_id,
    )
    signature = hmac.new(key, _canonical_json(envelope), hashlib.sha256).hexdigest()
    return {
        "df_seal_version": "1",
        "df_stream_length": str(source.length),
        "df_record_count": str(source.record_count),
        "df_source_etag_sha256": source_etag_sha256,
        "df_content_sha256": content_sha256,
        "df_seal_key_id": key_id,
        "df_seal_hmac": signature,
    }


def _validated_seal_metadata(
    name: str,
    length: int,
    record_count: int,
    metadata: Mapping[str, Any] | None,
    *,
    expected_source_etag_sha256: str | None = None,
    expected_content_sha256: str | None = None,
) -> str:
    value = metadata if isinstance(metadata, Mapping) else {}
    try:
        metadata_length = int(value.get("df_stream_length") or 0)
        metadata_count = int(value.get("df_record_count") or 0)
    except (TypeError, ValueError) as exc:
        raise AuditIntegrityError("sealed audit content digest metadata is invalid") from exc
    source_etag_sha256 = str(value.get("df_source_etag_sha256") or "")
    content_sha256 = str(value.get("df_content_sha256") or "")
    key_id = str(value.get("df_seal_key_id") or "")
    signature = str(value.get("df_seal_hmac") or "")
    if (
        str(value.get("df_seal_version") or "") != "1"
        or metadata_length != length
        or metadata_count != record_count
        or not re.fullmatch(r"[0-9a-f]{64}", source_etag_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", content_sha256)
        or not _KEY_ID.fullmatch(key_id)
        or not re.fullmatch(r"[0-9a-f]{64}", signature)
        or (
            expected_source_etag_sha256 is not None
            and not hmac.compare_digest(source_etag_sha256, expected_source_etag_sha256)
        )
        or (
            expected_content_sha256 is not None
            and not hmac.compare_digest(content_sha256, expected_content_sha256)
        )
    ):
        raise AuditIntegrityError("sealed audit content digest metadata is invalid")
    envelope = _seal_envelope(
        name,
        length,
        record_count,
        source_etag_sha256,
        content_sha256,
        key_id,
    )
    expected_signature = hmac.new(_key_for(key_id), _canonical_json(envelope), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise AuditIntegrityError("sealed audit seal signature is invalid")
    return content_sha256


def _validate_sealed_snapshot(
    sealed: _StreamSnapshot,
    source: _StreamSnapshot,
    *,
    expected_content_sha256: str | None = None,
) -> _StreamSnapshot:
    if (
        not sealed.sealed
        or sealed.name != source.name
        or sealed.length != source.length
        or sealed.record_count != source.record_count
        or sealed.head != source.head
        or not re.fullmatch(r"[0-9a-f]{64}", str(sealed.content_sha256 or ""))
        or (
            expected_content_sha256 is not None
            and not hmac.compare_digest(str(sealed.content_sha256), expected_content_sha256)
        )
    ):
        raise AuditIntegrityError("sealed audit segment does not match its active source")
    return sealed


def _head_ordinal(head: Mapping[str, Any]) -> int:
    for field in ("revision", "anchor_revision", "receipt_revision", "global_sequence"):
        if field in head:
            return int(head[field])
    raise AuditIntegrityError("audit stream head ordinal is missing")


def _ordinal_coordinate(ordinal: int) -> tuple[int, int]:
    if ordinal < 1:
        raise AuditIntegrityError("audit stream ordinal is invalid")
    return ((ordinal - 1) // MAX_RECORDS_PER_SEGMENT) + 1, ((ordinal - 1) % MAX_RECORDS_PER_SEGMENT) + 1


def _post_coordinate(segment: _SegmentHead, payload: bytes) -> _PhysicalCoordinate:
    return _PhysicalCoordinate(
        segment.index, segment.name,
        segment.snapshot.length + len(payload), segment.snapshot.record_count + 1,
    )


def _coordinate(segment: _SegmentHead) -> _PhysicalCoordinate:
    return _PhysicalCoordinate(
        segment.index, segment.name, segment.snapshot.length, segment.snapshot.record_count
    )


def _empty_coordinate() -> _PhysicalCoordinate:
    return _PhysicalCoordinate(0, "", 0, 0)


def _coordinate_from_anchor(anchor: Mapping[str, Any]) -> _PhysicalCoordinate:
    return _PhysicalCoordinate(
        int(anchor["event_segment_index"]), str(anchor["event_stream_name"]),
        int(anchor["event_stream_length"]), int(anchor["event_segment_record_count"]),
    )


def _anchor_coordinate_from_receipt(receipt: Mapping[str, Any]) -> _PhysicalCoordinate:
    return _PhysicalCoordinate(
        int(receipt["workspace_anchor_segment_index"]), str(receipt["workspace_anchor_stream_name"]),
        int(receipt["workspace_anchor_stream_length"]), int(receipt["workspace_anchor_segment_record_count"]),
    )


def _workspace_anchor_coordinate_from_global(anchor: Mapping[str, Any]) -> _PhysicalCoordinate:
    return _PhysicalCoordinate(
        int(anchor["workspace_anchor_segment_index"]), str(anchor["workspace_anchor_stream_name"]),
        int(anchor["workspace_anchor_stream_length"]), int(anchor["workspace_anchor_segment_record_count"]),
    )


def _global_coordinate_from_receipt(receipt: Mapping[str, Any]) -> _PhysicalCoordinate:
    return _PhysicalCoordinate(
        int(receipt["global_segment_index"]), str(receipt["global_stream_name"]),
        int(receipt["global_stream_length"]), int(receipt["global_segment_record_count"]),
    )


def _global_previous_coordinate_matches(anchor: Mapping[str, Any], coordinate: _PhysicalCoordinate) -> bool:
    return (
        int(anchor["previous_global_segment_index"]) == coordinate.segment_index
        and str(anchor["previous_global_stream_name"]) == coordinate.stream_name
        and int(anchor["previous_global_stream_length"]) == coordinate.stream_length
        and int(anchor["previous_global_segment_record_count"]) == coordinate.segment_record_count
    )


def _validate_workspace_physical_state(
    backend: _AppendBackend,
    event: _SegmentHead,
    anchor: _SegmentHead,
    receipt: _SegmentHead,
    global_anchor: _SegmentHead,
    status: str,
) -> None:
    if status == "complete":
        if event.head is None:
            if anchor.head is not None or receipt.head is not None:
                raise AuditIntegrityError("audit empty physical state is invalid")
            return
        if anchor.head is None or receipt.head is None:
            raise AuditIntegrityError("audit committed physical proof is missing")
        if _coordinate_from_anchor(anchor.head) != _coordinate(event):
            raise AuditIntegrityError("workspace anchor event physical coordinates do not match")
        if _anchor_coordinate_from_receipt(receipt.head) != _coordinate(anchor):
            raise AuditIntegrityError("workspace receipt anchor physical coordinates do not match")
        _verify_receipt_global_coordinate(backend, receipt.head, global_anchor)
        return
    if status == "event_gap":
        if event.head is None:
            raise AuditIntegrityError("audit one-gap event is missing")
        if anchor.head is None:
            if int(event.head["revision"]) != 1 or event.snapshot.record_count != 1 or event.index != 1:
                raise AuditIntegrityError("audit anchor is missing for more than one physical record")
            _require_exact_range_records(backend, event, 0, 1, "genesis audit recovery")
        else:
            if not hmac.compare_digest(str(event.head["previous_hash"]), str(anchor.head["workspace_event_hash"])):
                raise AuditIntegrityError("audit one-gap event hash does not follow anchor")
            _require_one_record_after(backend, _coordinate_from_anchor(anchor.head), event, "audit event recovery")
        return
    if anchor.head is None:
        raise AuditIntegrityError("audit one-gap anchor is missing")
    if receipt.head is None:
        if int(anchor.head["anchor_revision"]) != 1 or anchor.snapshot.record_count != 1 or anchor.index != 1:
            raise AuditIntegrityError("workspace receipt is missing for more than one anchor record")
        _require_exact_range_records(backend, anchor, 0, 1, "genesis anchor recovery")
    else:
        if not hmac.compare_digest(str(anchor.head["previous_hash"]), str(receipt.head["workspace_anchor_hash"])):
            raise AuditIntegrityError("audit one-gap anchor hash does not follow receipt")
        _require_one_record_after(
            backend, _anchor_coordinate_from_receipt(receipt.head), anchor, "workspace anchor recovery"
        )


def _verify_receipt_global_coordinate(
    backend: _AppendBackend,
    receipt: Mapping[str, Any],
    global_head: _SegmentHead,
) -> None:
    if global_head.head is None or int(global_head.head["global_sequence"]) < int(receipt["global_sequence"]):
        raise AuditIntegrityError("global audit prefix rollback detected")
    coordinate = _global_coordinate_from_receipt(receipt)
    value = _record_ending_at(
        backend,
        coordinate,
        "global audit receipt",
        require_sealed=coordinate.segment_index < global_head.index,
    )
    global_anchor = _validated_global_anchor(value)
    if (
        int(global_anchor["global_sequence"]) != int(receipt["global_sequence"])
        or not hmac.compare_digest(str(global_anchor["global_hash"]), str(receipt["global_hash"]))
        or not hmac.compare_digest(str(global_anchor["workspace_anchor_hash"]), str(receipt["workspace_anchor_hash"]))
    ):
        raise AuditIntegrityError("global audit receipt physical proof does not match")


def _validate_global_head_physical_chain(backend: _AppendBackend, current: _SegmentHead) -> None:
    head = current.head
    if head is None:
        return
    sequence = int(head["global_sequence"])
    previous = _PhysicalCoordinate(
        int(head["previous_global_segment_index"]), str(head["previous_global_stream_name"]),
        int(head["previous_global_stream_length"]), int(head["previous_global_segment_record_count"]),
    )
    if sequence == 1:
        if previous != _empty_coordinate() or head["previous_hash"] != GENESIS_HASH:
            raise AuditIntegrityError("global audit genesis physical coordinates are invalid")
        _require_exact_range_records(backend, current, 0, 1, "global audit genesis")
        return
    expected_index, expected_count = _ordinal_coordinate(sequence - 1)
    if previous.segment_index != expected_index or previous.segment_record_count != expected_count:
        raise AuditIntegrityError("global audit previous physical coordinate is invalid")
    _require_one_record_after(backend, previous, current, "global audit physical tail")


def _require_one_record_after(
    backend: _AppendBackend,
    previous: _PhysicalCoordinate,
    current: _SegmentHead,
    label: str,
) -> None:
    if current.name == previous.stream_name and current.index == previous.segment_index:
        if (
            current.snapshot.record_count != previous.segment_record_count + 1
            or current.snapshot.length <= previous.stream_length
        ):
            raise AuditIntegrityError(f"{label} does not contain exactly one physical record")
        _require_exact_range_records(backend, current, previous.stream_length, 1, label)
        return
    if (
        current.index != previous.segment_index + 1
        or previous.segment_record_count != MAX_RECORDS_PER_SEGMENT
        or current.snapshot.record_count != 1
    ):
        raise AuditIntegrityError(f"{label} segment rotation is invalid")
    previous_snapshot = backend.read_snapshot(previous.stream_name)
    if (
        not previous_snapshot.sealed
        or previous_snapshot.length != previous.stream_length
        or previous_snapshot.record_count != previous.segment_record_count
    ):
        raise AuditIntegrityError(f"{label} previous segment physical proof does not match")
    _require_exact_range_records(backend, current, 0, 1, label)


def _require_exact_range_records(
    backend: _AppendBackend,
    current: _SegmentHead,
    offset: int,
    count: int,
    label: str,
) -> None:
    if offset < 0 or offset > current.snapshot.length:
        raise AuditIntegrityError(f"{label} committed byte offset is invalid")
    data = backend.read_range(current.name, offset, current.snapshot.length - offset, current.snapshot)
    records = _parse_lines(data, label)
    if len(records) != count:
        raise AuditIntegrityError(f"{label} must contain exactly one physical record")
    if records[-1] != current.snapshot.head:
        raise AuditIntegrityError(f"{label} physical head does not match")


def _record_ending_at(
    backend: _AppendBackend,
    coordinate: _PhysicalCoordinate,
    label: str,
    *,
    require_sealed: bool = False,
) -> dict[str, Any]:
    snapshot = backend.read_snapshot(coordinate.stream_name)
    if (
        (require_sealed and not snapshot.sealed)
        or snapshot.length < coordinate.stream_length
        or snapshot.record_count < coordinate.segment_record_count
        or coordinate.stream_length < 1
    ):
        raise AuditIntegrityError(f"{label} committed physical coordinate is missing")
    if snapshot.record_count == coordinate.segment_record_count and snapshot.length != coordinate.stream_length:
        raise AuditIntegrityError(f"{label} committed physical length does not match")
    offset = max(0, coordinate.stream_length - MAX_STREAM_RECORD_BYTES)
    data = backend.read_range(coordinate.stream_name, offset, coordinate.stream_length - offset, snapshot)
    if not data.endswith(b"\n"):
        raise AuditIntegrityError(f"{label} committed record is truncated")
    lines = data.splitlines()
    if not lines:
        raise AuditIntegrityError(f"{label} committed record is missing")
    try:
        value = json.loads(lines[-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditIntegrityError(f"{label} committed record is invalid") from exc
    if not isinstance(value, dict):
        raise AuditIntegrityError(f"{label} committed record is invalid")
    return value


def _find_global_anchor(
    backend: _AppendBackend, workspace_anchor_hash: str
) -> tuple[dict[str, Any], _PhysicalCoordinate] | None:
    anchors, coordinates = _read_global_anchors_with_coordinates(backend)
    matches = [
        (anchor, coordinate)
        for anchor, coordinate in zip(anchors, coordinates)
        if hmac.compare_digest(str(anchor["workspace_anchor_hash"]), workspace_anchor_hash)
    ]
    if len(matches) > 1:
        raise AuditIntegrityError("global audit history duplicates a workspace anchor")
    return matches[0] if matches else None


def _read_segment_records(
    backend: _AppendBackend, prefix: str, label: str
) -> list[tuple[dict[str, Any], _PhysicalCoordinate]]:
    names = backend.list_names(prefix)
    indexed: list[tuple[int, str]] = []
    for name in names:
        index = _segment_index(prefix, name)
        if index is None:
            raise AuditIntegrityError(f"{label} segment name is invalid")
        indexed.append((index, name))
    indexed.sort()
    if indexed and [item[0] for item in indexed] != list(range(1, indexed[-1][0] + 1)):
        raise AuditIntegrityError(f"{label} segment gap or delete detected")
    records: list[tuple[dict[str, Any], _PhysicalCoordinate]] = []
    for position, (index, name) in enumerate(indexed):
        snapshot = backend.read_snapshot(name)
        if position < len(indexed) - 1 and not snapshot.sealed:
            raise AuditIntegrityError(f"{label} old segment is not sealed")
        data = backend.read_full(name, snapshot)
        parsed = _parse_lines(data, label)
        if not parsed or len(parsed) > MAX_RECORDS_PER_SEGMENT:
            raise AuditIntegrityError(f"{label} segment record count is invalid")
        if position < len(indexed) - 1 and len(parsed) != MAX_RECORDS_PER_SEGMENT:
            raise AuditIntegrityError(f"{label} old segment is not physically full")
        offset = 0
        lines = data.splitlines(keepends=True)
        for value, line in zip(parsed, lines):
            offset += len(line)
            records.append((value, _PhysicalCoordinate(index, name, offset, len(records) % MAX_RECORDS_PER_SEGMENT + 1)))
    return records


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


def _validated_receipt_head(snapshot: _StreamSnapshot, workspace_id: str) -> dict[str, Any] | None:
    if snapshot.head is None:
        if snapshot.length:
            raise AuditIntegrityError("workspace global receipt head is missing")
        return None
    return _validated_receipt(snapshot.head, workspace_id)


def _validated_global_anchor_head(snapshot: _StreamSnapshot) -> dict[str, Any] | None:
    if snapshot.head is None:
        if snapshot.length:
            raise AuditIntegrityError("global audit anchor head is missing")
        return None
    return _validated_global_anchor(snapshot.head)


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


def _build_global_anchor(**values: Any) -> dict[str, Any]:
    payload = {**values, "at": _now(), "global_hash": ""}
    normalized = GlobalAuditAnchor.model_validate(payload).model_dump(mode="json")
    normalized["global_hash"] = _hash_global_anchor(normalized)
    return GlobalAuditAnchor.model_validate(normalized).model_dump(mode="json")


def _build_receipt(**values: Any) -> dict[str, Any]:
    payload = {**values, "at": _now(), "receipt_hash": ""}
    normalized = WorkspaceGlobalReceipt.model_validate(payload).model_dump(mode="json")
    normalized["receipt_hash"] = _hash_receipt(normalized)
    return WorkspaceGlobalReceipt.model_validate(normalized).model_dump(mode="json")


def _clean_resource(resource: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(resource, Mapping):
        raise ValueError("resource must be an object")
    return {
        "workspace_id": _workspace_scope_id(resource.get("workspace_id")),
        "resource_type": _allowlisted(resource.get("resource_type"), ALLOWED_RESOURCE_TYPES, "resource_type"),
        "resource_id": _resource_scope_id(resource.get("resource_id")),
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


def _scope_key() -> bytes:
    active_id, keys = _key_ring()
    scope_key_id = str(os.environ.get("DF_AUDIT_HMAC_SCOPE_KEY_ID") or "").strip()
    if not scope_key_id:
        if _is_production() or blob_configured():
            raise AuditPersistenceError("DF_AUDIT_HMAC_SCOPE_KEY_ID is required")
        scope_key_id = active_id
    if not _KEY_ID.fullmatch(scope_key_id) or scope_key_id not in keys:
        raise AuditPersistenceError("audit HMAC scope key id is not retained in the key ring")
    return keys[scope_key_id]


def _hash_event(event: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return hmac.new(_key_for(str(event.get("key_id") or "")), _canonical_json(payload), hashlib.sha256).hexdigest()


def _hash_anchor(anchor: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in anchor.items() if key != "anchor_hash"}
    return hmac.new(_key_for(str(anchor.get("key_id") or "")), _canonical_json(payload), hashlib.sha256).hexdigest()


def _hash_global_anchor(anchor: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in anchor.items() if key != "global_hash"}
    return hmac.new(_key_for(str(anchor.get("key_id") or "")), _canonical_json(payload), hashlib.sha256).hexdigest()


def _hash_receipt(receipt: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    return hmac.new(_key_for(str(receipt.get("key_id") or "")), _canonical_json(payload), hashlib.sha256).hexdigest()


def _validate_event_policy(model: AuditEvent, event: Mapping[str, Any]) -> None:
    if not _WORKSPACE_SCOPE.fullmatch(str(event.get("workspace_id") or "")):
        raise ValueError("workspace_id is not pseudonymous")
    if not _RESOURCE_SCOPE.fullmatch(str(event.get("resource_id") or "")):
        raise ValueError("resource_id is not pseudonymous")
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
    workspace_id = str(anchor.get("workspace_id") or "")
    if not _WORKSPACE_SCOPE.fullmatch(workspace_id):
        raise ValueError("anchor workspace_id is not pseudonymous")
    workspace_scope = _stored_workspace_scope_id(workspace_id)
    if int(anchor.get("anchor_revision") or 0) < 1 or int(anchor.get("workspace_revision") or 0) < 1:
        raise ValueError("anchor revision is invalid")
    if not _KEY_ID.fullmatch(str(anchor.get("key_id") or "")):
        raise ValueError("anchor key_id is invalid")
    for field in ("workspace_event_hash", "previous_hash", "anchor_hash"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(anchor.get(field) or "")):
            raise ValueError(f"anchor {field} is invalid")
    if model.at.tzinfo is None or model.at.utcoffset() != timedelta(0):
        raise ValueError("anchor at must be UTC")
    _validate_physical_fields(
        anchor,
        "event",
        _event_stream_name(workspace_scope, int(anchor.get("event_segment_index") or 0)),
    )


def _validated_receipt(value: Mapping[str, Any], workspace_id: str) -> dict[str, Any]:
    try:
        model = WorkspaceGlobalReceipt.model_validate(value)
        receipt = model.model_dump(mode="json")
    except Exception as exc:
        raise AuditIntegrityError("workspace global receipt schema is invalid") from exc
    if (
        receipt["workspace_id"] != workspace_id
        or int(receipt["receipt_revision"]) < 1
        or int(receipt["receipt_revision"]) != int(receipt["workspace_revision"])
        or not hmac.compare_digest(str(receipt["receipt_hash"]), _hash_receipt(receipt))
    ):
        raise AuditIntegrityError("workspace global receipt is invalid")
    _validate_signed_common(model.at, receipt, "receipt", ("workspace_anchor_hash", "global_hash", "previous_hash", "receipt_hash"))
    _validate_physical_fields(
        receipt,
        "workspace_anchor",
        _anchor_stream_name(_stored_workspace_scope_id(workspace_id), int(receipt["workspace_anchor_segment_index"])),
    )
    _validate_physical_fields(
        receipt,
        "global",
        _global_stream_name(int(receipt["global_segment_index"])),
    )
    if int(receipt["global_sequence"]) < 1:
        raise AuditIntegrityError("workspace global receipt sequence is invalid")
    return receipt


def _validated_global_anchor(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        model = GlobalAuditAnchor.model_validate(value)
        anchor = model.model_dump(mode="json")
    except Exception as exc:
        raise AuditIntegrityError("global audit anchor schema is invalid") from exc
    workspace_id = str(anchor["workspace_id"])
    if (
        not _WORKSPACE_SCOPE.fullmatch(workspace_id)
        or int(anchor["global_sequence"]) < 1
        or int(anchor["workspace_revision"]) < 1
        or not hmac.compare_digest(str(anchor["global_hash"]), _hash_global_anchor(anchor))
    ):
        raise AuditIntegrityError("global audit anchor is invalid")
    _validate_signed_common(
        model.at,
        anchor,
        "global anchor",
        ("workspace_event_hash", "workspace_anchor_hash", "previous_hash", "global_hash"),
    )
    workspace_scope = _stored_workspace_scope_id(workspace_id)
    _validate_physical_fields(
        anchor,
        "workspace_anchor",
        _anchor_stream_name(workspace_scope, int(anchor["workspace_anchor_segment_index"])),
    )
    _validate_physical_fields(
        anchor,
        "event",
        _event_stream_name(workspace_scope, int(anchor["event_segment_index"])),
    )
    expected_global_name = _global_stream_name(int(anchor["global_segment_index"]))
    if anchor["global_stream_name"] != expected_global_name:
        raise AuditIntegrityError("global audit stream identity is invalid")
    previous_index = int(anchor["previous_global_segment_index"])
    previous_name = str(anchor["previous_global_stream_name"])
    if previous_index == 0:
        if previous_name or int(anchor["previous_global_stream_length"]) or int(anchor["previous_global_segment_record_count"]):
            raise AuditIntegrityError("global audit genesis coordinates are invalid")
    else:
        _validate_physical_fields(anchor, "previous_global", _global_stream_name(previous_index))
    return anchor


def _validate_signed_common(
    at: datetime,
    value: Mapping[str, Any],
    label: str,
    hash_fields: tuple[str, ...],
) -> None:
    if not _KEY_ID.fullmatch(str(value.get("key_id") or "")):
        raise AuditIntegrityError(f"{label} key id is invalid")
    for field in hash_fields:
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get(field) or "")):
            raise AuditIntegrityError(f"{label} {field} is invalid")
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise AuditIntegrityError(f"{label} timestamp must be UTC")


def _validate_physical_fields(value: Mapping[str, Any], prefix: str, expected_name: str) -> None:
    index = int(value.get(f"{prefix}_segment_index") or 0)
    name = str(value.get(f"{prefix}_stream_name") or "")
    length = int(value.get(f"{prefix}_stream_length") or 0)
    count = int(value.get(f"{prefix}_segment_record_count") or 0)
    if index < 1 or name != expected_name or length < 1 or count < 1 or count > MAX_RECORDS_PER_SEGMENT:
        raise AuditIntegrityError(f"{prefix} physical coordinates are invalid")


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
    sealed_container_name = _audit_sealed_container_name()
    legal_hold_tag = _audit_legal_hold_tag()
    cache_key = (resource_id.lower(), write_account, container_name, sealed_container_name, legal_hold_tag)
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
    container = _management_get_json(
        f"{resource_id}/blobServices/default/containers/{container_name}?{api}"
    ).get("properties")
    sealed_policy = _management_get_json(
        f"{resource_id}/blobServices/default/containers/{sealed_container_name}/immutabilityPolicies/default?{api}"
    ).get("properties")
    sealed_container = _management_get_json(
        f"{resource_id}/blobServices/default/containers/{sealed_container_name}?{api}"
    ).get("properties")
    _validate_production_contract(
        service,
        policy,
        container,
        legal_hold_tag,
        sealed_policy,
        sealed_container,
    )
    _PRODUCTION_CONTRACT_CACHE[cache_key] = now + PRODUCTION_CONTRACT_CACHE_TTL_SECONDS
    return resource_id


def _management_get_json(resource_path: str) -> dict[str, Any]:
    # Production audit writes already use the app's system-assigned identity.
    # Use that same identity for the ARM contract proof so an unrelated
    # AZURE_CLIENT_ID cannot redirect this security-critical path.
    token = ManagedIdentityCredential().get_token("https://management.azure.com/.default").token
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


def _validate_production_contract(
    service: Mapping[str, Any] | None,
    policy: Mapping[str, Any] | None,
    container: Mapping[str, Any] | None = None,
    legal_hold_tag: str | None = None,
    sealed_policy: Mapping[str, Any] | None = None,
    sealed_container: Mapping[str, Any] | None = None,
) -> None:
    service = service if isinstance(service, Mapping) else {}
    versioning = service.get("isVersioningEnabled", service.get("is_versioning_enabled")) is True
    blob_delete = service.get("deleteRetentionPolicy", service.get("delete_retention_policy"))
    container_delete = service.get("containerDeleteRetentionPolicy", service.get("container_delete_retention_policy"))
    blob_delete_ok = isinstance(blob_delete, Mapping) and blob_delete.get("enabled") is True and int(blob_delete.get("days") or 0) > 0
    container_delete_ok = isinstance(container_delete, Mapping) and container_delete.get("enabled") is True and int(container_delete.get("days") or 0) > 0
    expected_tag = str(legal_hold_tag or "").strip()
    active_ok = _validate_container_contract(policy, container, expected_tag, protected_append=True)
    sealed_ok = _validate_container_contract(
        sealed_policy,
        sealed_container,
        expected_tag,
        protected_append=False,
    )
    if not all((versioning, blob_delete_ok, container_delete_ok, active_ok)):
        raise AuditPersistenceError("production active audit storage contract is not satisfied")
    if not sealed_ok:
        raise AuditPersistenceError("production sealed audit storage contract is not satisfied")


def _validate_container_contract(
    policy: Mapping[str, Any] | None,
    container: Mapping[str, Any] | None,
    expected_tag: str,
    *,
    protected_append: bool,
) -> bool:
    policy = policy if isinstance(policy, Mapping) else {}
    container = container if isinstance(container, Mapping) else {}
    locked = str(policy.get("state") or policy.get("policy_mode") or "").lower() == "locked"
    append_value = policy.get("allowProtectedAppendWrites", policy.get("allow_protected_append_writes"))
    append_ok = append_value is protected_append
    legal_hold = container.get("legalHold", container.get("legal_hold"))
    legal_hold = legal_hold if isinstance(legal_hold, Mapping) else {}
    hold_active = container.get("hasLegalHold", container.get("has_legal_hold")) is True and legal_hold.get(
        "hasLegalHold", legal_hold.get("has_legal_hold")
    ) is True
    tags = legal_hold.get("tags")
    active_tags = {
        str(item.get("tag") or "")
        for item in tags if isinstance(item, Mapping)
    } if isinstance(tags, list) else set()
    legal_hold_ok = (
        bool(expected_tag)
        and hold_active
        and expected_tag in active_tags
    )
    return locked and append_ok and legal_hold_ok


def _is_production() -> bool:
    environment = str(os.environ.get("DF_ENVIRONMENT") or "").strip().lower()
    if environment in {"preview", "staging", "test"}:
        return False
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


def _audit_sealed_container_name() -> str:
    value = str(os.environ.get("DF_AUDIT_SEALED_CONTAINER") or f"{AUDIT_CONTAINER}-sealed").strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", value):
        raise AuditPersistenceError("sealed audit container name is invalid")
    if value == _audit_container_name():
        raise AuditPersistenceError("active and sealed audit containers must be different")
    return value


def _audit_legal_hold_tag() -> str:
    value = str(os.environ.get("DF_AUDIT_LEGAL_HOLD_TAG") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]{3,23}", value):
        raise AuditPersistenceError("production audit legal hold tag is missing or invalid")
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


def _local_active_root() -> Path:
    return (AUDIT_DIR / "active").resolve()


def _local_sealed_root() -> Path:
    return (AUDIT_DIR / "sealed").resolve()


def _local_scoped_path(root: Path, name: str) -> Path:
    raw = str(name or "")
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("audit stream path must be relative")
    normalized = raw.replace("\\", "/").strip("/")
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("audit stream path is invalid")
    path = (root / normalized).resolve()
    if root not in path.parents:
        raise ValueError("audit stream path escapes audit directory")
    return path


def _local_active_stream_path(name: str) -> Path:
    return _local_scoped_path(_local_active_root(), name)


def _local_sealed_stream_path(name: str) -> Path:
    return _local_scoped_path(_local_sealed_root(), name)


def _local_seal_metadata_path(name: str) -> Path:
    stream = _local_sealed_stream_path(name)
    return stream.with_name(f"{stream.name}.seal.json")


def _read_local_seal_metadata(name: str, length: int, record_count: int) -> str:
    path = _local_seal_metadata_path(name)
    try:
        metadata = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditIntegrityError("sealed audit content digest metadata is missing or invalid") from exc
    return _validated_seal_metadata(name, length, record_count, metadata)


def _write_or_validate_local_seal_metadata(
    name: str,
    metadata: Mapping[str, str],
    source: _StreamSnapshot,
    content_sha256: str,
) -> None:
    path = _local_seal_metadata_path(name)
    payload = _canonical_json(metadata) + b"\n"
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o400,
        )
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        pass
    except OSError as exc:
        raise AuditPersistenceError("local audit seal metadata write failed") from exc
    try:
        persisted = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditIntegrityError("sealed audit content digest metadata is missing or invalid") from exc
    source_etag_sha256 = hashlib.sha256(str(source.etag or "").encode("ascii")).hexdigest()
    _validated_seal_metadata(
        name,
        source.length,
        source.record_count,
        persisted,
        expected_source_etag_sha256=source_etag_sha256,
        expected_content_sha256=content_sha256,
    )


def _local_read_path(name: str) -> tuple[Path, bool]:
    sealed = _local_sealed_stream_path(name)
    if sealed.exists():
        return sealed, True
    return _local_active_stream_path(name), False


def _local_stream_path(name: str) -> Path:
    return _local_active_stream_path(name)


def _event_prefix(workspace_id: str) -> str:
    return f"workspaces/{_workspace_scope_id(workspace_id)}/events/"


def _anchor_prefix(workspace_id: str) -> str:
    return f"workspaces/{_workspace_scope_id(workspace_id)}/anchors/"


def _receipt_prefix(workspace_id: str) -> str:
    return f"global/workspaces/{_workspace_scope_id(workspace_id)}/receipts/"


def _global_prefix() -> str:
    return "global/anchors/"


def _event_stream_name(workspace_id: str, segment: int | None = None) -> str:
    return _segmented_name(_event_prefix(workspace_id), int(segment or 1))


def _anchor_stream_name(workspace_id: str, segment: int | None = None) -> str:
    return _segmented_name(_anchor_prefix(workspace_id), int(segment or 1))


def _receipt_stream_name(workspace_id: str, segment: int | None = None) -> str:
    return _segmented_name(_receipt_prefix(workspace_id), int(segment or 1))


def _global_stream_name(segment: int | None = None) -> str:
    return _segmented_name(_global_prefix(), int(segment or 1))


def _segmented_name(prefix: str, index: int) -> str:
    if index < 1 or index > MAX_SEGMENT_INDEX:
        raise AuditPersistenceError("audit segment index is outside the supported range")
    reverse_index = MAX_SEGMENT_INDEX - index
    return f"{prefix}{reverse_index:08d}/{index:08d}.jsonl"


def _segment_index(prefix: str, name: str) -> int | None:
    match = re.fullmatch(
        rf"{re.escape(prefix)}(?P<reverse>[0-9]{{8}})/(?P<index>[0-9]{{8}})\.jsonl",
        name,
    )
    if match is None:
        return None
    index = int(match.group("index"))
    if index < 1 or index > MAX_SEGMENT_INDEX or int(match.group("reverse")) != MAX_SEGMENT_INDEX - index:
        return None
    return index


def _local_etag(stat_result: os.stat_result) -> str:
    return f'"{int(stat_result.st_mtime_ns):x}-{int(stat_result.st_size):x}"'


def _count_local_records(path: Path) -> int:
    count = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                count += chunk.count(b"\n")
    except OSError as exc:
        raise AuditPersistenceError("local audit record count failed") from exc
    return count


def _snapshot_from_tail(
    name: str,
    data: bytes,
    length: int,
    etag: str | None,
    record_count: int,
    *,
    sealed: bool = False,
    content_sha256: str | None = None,
) -> _StreamSnapshot:
    if length < 0 or len(data) != min(length, STREAM_TAIL_BYTES):
        raise AuditIntegrityError("audit stream snapshot length is invalid")
    if not isinstance(record_count, int) or isinstance(record_count, bool) or record_count < 0:
        raise AuditIntegrityError("audit stream record count is invalid")
    if length == 0:
        if data or record_count:
            raise AuditIntegrityError("empty audit stream snapshot is invalid")
        return _StreamSnapshot(
            name=name,
            data=b"",
            head=None,
            length=0,
            etag=etag,
            record_count=0,
            sealed=sealed,
            content_sha256=content_sha256,
        )
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
    if record_count < 1:
        raise AuditIntegrityError("non-empty audit stream record count is invalid")
    return _StreamSnapshot(
        name=name,
        data=data,
        head=head,
        length=length,
        etag=etag,
        record_count=record_count,
        sealed=sealed,
        content_sha256=content_sha256,
    )


def _validate_append_request(name: str, payload: bytes, snapshot: _StreamSnapshot) -> None:
    if snapshot.name != name or snapshot.length < 0 or snapshot.record_count >= MAX_RECORDS_PER_SEGMENT:
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


def _pseudonymize_workspace_id(value: Any) -> _WorkspaceScopeId:
    if isinstance(value, _WorkspaceScopeId):
        return value
    clean = str(value or "").strip()
    raw = _external_identifier(clean, "workspace_id")
    if raw in {".", ".."} or "/" in raw or "\\" in raw or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("workspace_id is invalid")
    return _WorkspaceScopeId(_pseudonym("ws", "workspace", raw))


def _pseudonymize_resource_id(value: Any) -> _ResourceScopeId:
    if isinstance(value, _ResourceScopeId):
        return value
    clean = str(value or "").strip()
    return _ResourceScopeId(_pseudonym("res", "resource", _external_identifier(clean, "resource_id")))


def _workspace_scope_id(value: Any) -> _WorkspaceScopeId:
    return _pseudonymize_workspace_id(value)


def _resource_scope_id(value: Any) -> _ResourceScopeId:
    return _pseudonymize_resource_id(value)


def _stored_workspace_scope_id(value: Any) -> _WorkspaceScopeId:
    clean = str(value or "")
    if not _WORKSPACE_SCOPE.fullmatch(clean):
        raise AuditIntegrityError("stored workspace scope is invalid")
    return _WorkspaceScopeId(clean)


def _external_identifier(value: str, field: str) -> str:
    if not value or len(value.encode("utf-8")) > 8192 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} is invalid")
    return value


def _pseudonym(prefix: str, domain: str, value: str) -> str:
    digest = hmac.new(_scope_key(), f"{domain}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]
    return f"{prefix}_{digest}"


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
