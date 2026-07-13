from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from .blob_store import blob_configured, compare_and_swap_blob_json, delete_blob_json_if_revision, download_blob_json_strict, list_blob_json_named_strict, upload_blob_json
    from .connector_secret_store import SecretExpiredError, SecretStore, expected_secret_reference
except ImportError:
    from blob_store import blob_configured, compare_and_swap_blob_json, delete_blob_json_if_revision, download_blob_json_strict, list_blob_json_named_strict, upload_blob_json
    from connector_secret_store import SecretExpiredError, SecretStore, expected_secret_reference


_SENSITIVE = re.compile(r"password|passwd|pwd|connection.?string|sas|token|secret|credential|username|user.?id", re.IGNORECASE)
_KINDS = {"sql", "blob"}


class ConnectorStoreError(RuntimeError):
    code = "connector_store_unavailable"

    def __init__(self, code: str | None = None) -> None:
        if code:
            self.code = code
        super().__init__(self.code)


class ConnectorConflictError(ConnectorStoreError):
    code = "connector_conflict"


class ConnectorDeleteError(ConnectorStoreError):
    code = "connector_delete_failed"


class ConnectorStore:
    """Durable connector metadata. Credential material belongs only in SecretStore."""

    def __init__(self, root: Path | str | None = None) -> None:
        configured = os.environ.get("DF_CONNECTOR_STORE_DIR")
        self.root = Path(root or configured or "workspaces/_connectors")

    def create(
        self,
        workspace_id: str,
        kind: str,
        metadata: Mapping[str, Any],
        secret: Mapping[str, str],
        secret_store: SecretStore,
    ) -> dict[str, Any]:
        workspace = _identifier(workspace_id, "workspace_id")
        connector_kind = _kind(kind)
        connector_id = f"{connector_kind}_{uuid.uuid4().hex[:16]}"
        reference = secret_store.put(workspace, connector_id, secret)
        record = {
            "connector_id": connector_id,
            "workspace_id": workspace,
            "kind": connector_kind,
            "status": "connected",
            "persistence": str(secret_store.persistence),
            "secret_ref": _secret_reference(reference),
            "metadata": _safe_metadata(metadata),
            "created_at": _now(),
            "updated_at": _now(),
            "revision": 1,
        }
        try:
            self._write(record)
        except Exception:
            try:
                secret_store.delete(workspace, connector_id, reference)
            except Exception:
                pass
            raise
        return record

    def get(self, workspace_id: str, connector_id: str) -> dict[str, Any]:
        path = self._path(workspace_id, connector_id)
        if blob_configured():
            remote = download_blob_json_strict(self._blob_name(workspace_id, connector_id))
            if remote is None:
                raise FileNotFoundError("Connector record not found")
            if not isinstance(remote, dict):
                raise RuntimeError("Connector record is invalid")
            record = _safe_record(remote, expected_workspace_id=workspace_id, expected_connector_id=connector_id)
            self._write_local(record)
            return record
        if not path.exists():
            raise FileNotFoundError("Connector record not found")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Connector record is unavailable") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("Connector record is invalid")
        return _safe_record(raw, expected_workspace_id=workspace_id, expected_connector_id=connector_id)

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        if blob_configured():
            records: list[dict[str, Any]] = []
            for blob_name, item in list_blob_json_named_strict(self._blob_prefix(workspace_id)):
                if not isinstance(item, dict):
                    raise ValueError("Connector record is invalid")
                record = _safe_record(item, expected_workspace_id=workspace_id)
                if blob_name != self._blob_name(workspace_id, str(record["connector_id"])):
                    raise ValueError("Connector record identity does not match its storage path")
                records.append(record)
            for record in records:
                self._write_local(record)
            return sorted(records, key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        directory = self.root / _safe_component(workspace_id)
        if not directory.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in directory.glob("*.json"):
            try:
                records.append(self.get(workspace_id, path.stem))
            except (FileNotFoundError, RuntimeError):
                continue
        return sorted(records, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def update(self, workspace_id: str, connector_id: str, **changes: Any) -> dict[str, Any]:
        requested_revision = changes.pop("expected_revision", None)
        for _ in range(3):
            if blob_configured():
                record = self.get(workspace_id, connector_id)
                if requested_revision is not None and int(record["revision"]) != int(requested_revision):
                    raise ConnectorConflictError()
                updated = self._changed_record(record, changes)
                try:
                    self._write(updated, expected_revision=int(record["revision"]))
                    return updated
                except ConnectorConflictError:
                    if requested_revision is not None:
                        raise
                    continue
            path = self._path(workspace_id, connector_id)
            with self._local_lock(path):
                record = self.get(workspace_id, connector_id)
                if requested_revision is not None and int(record["revision"]) != int(requested_revision):
                    raise ConnectorConflictError()
                updated = self._changed_record(record, changes)
                self._write_local(updated, lock_held=True)
                return updated
        raise ConnectorConflictError()

    def transition(self, workspace_id: str, connector_id: str, *, expected_status: str | tuple[str, ...] | None = None, **changes: Any) -> dict[str, Any]:
        """Retry a lifecycle transition from a freshly observed revision."""
        allowed = {expected_status} if isinstance(expected_status, str) else set(expected_status or ())
        for _ in range(3):
            record = self.get(workspace_id, connector_id)
            if allowed and str(record.get("status") or "") not in allowed:
                raise ConnectorConflictError()
            try:
                return self.update(workspace_id, connector_id, expected_revision=record["revision"], **changes)
            except ConnectorConflictError:
                continue
        raise ConnectorConflictError()

    def reconnect(self, workspace_id: str, connector_id: str, secret_store: SecretStore) -> tuple[dict[str, Any], dict[str, str]]:
        record = self.get(workspace_id, connector_id)
        try:
            payload = secret_store.get(workspace_id, connector_id, str(record["secret_ref"]))
        except SecretExpiredError:
            # session-only state is local to this process and must not mutate durable state.
            if record.get("persistence") != "session_only":
                self.transition(workspace_id, connector_id, status="expired", error="secret_expired")
            raise
        record = self.transition(workspace_id, connector_id, status="connected", error=None)
        return record, payload

    def disconnect(self, workspace_id: str, connector_id: str) -> dict[str, Any]:
        return self.transition(workspace_id, connector_id, status="disconnected", error=None)

    def delete(self, workspace_id: str, connector_id: str, secret_store: SecretStore, *, _attempt: int = 0) -> None:
        try:
            record = self.get(workspace_id, connector_id)
        except FileNotFoundError:
            return
        phase = str(record.get("delete_phase") or "")
        if phase == "record_deleted":
            return
        if phase not in {"deleting", "secret_deleted"}:
            record = self.transition(workspace_id, connector_id, status="deleting", delete_pending=True, delete_phase="deleting", error=None)
            phase = "deleting"
        if phase == "deleting":
            try:
                secret_store.delete(workspace_id, connector_id, str(record["secret_ref"]))
            except Exception as exc:
                if not _secret_is_missing(exc):
                    self._mark_delete_error(workspace_id, connector_id, "secret_delete_failed")
                    raise ConnectorDeleteError("connector_secret_delete_failed") from None
            try:
                record = self.transition(workspace_id, connector_id, status="deleting", delete_pending=True, delete_phase="secret_deleted", error=None)
            except ConnectorConflictError:
                if _attempt >= 2:
                    raise
                return self.delete(workspace_id, connector_id, secret_store, _attempt=_attempt + 1)
        try:
            self._delete_record(record)
        except FileNotFoundError:
            return
        except ConnectorConflictError:
            if _attempt >= 2:
                raise
            return self.delete(workspace_id, connector_id, secret_store, _attempt=_attempt + 1)
        except Exception:
            self._mark_delete_error(workspace_id, connector_id, "record_delete_failed")
            raise ConnectorDeleteError("connector_record_delete_failed") from None

    def _write(self, record: Mapping[str, Any], *, expected_revision: int | None = None) -> None:
        safe = _safe_record(record)
        if blob_configured():
            try:
                if expected_revision is None:
                    upload_blob_json(self._blob_name(str(safe["workspace_id"]), str(safe["connector_id"])), safe)
                elif compare_and_swap_blob_json(self._blob_name(str(safe["workspace_id"]), str(safe["connector_id"])), expected_revision=expected_revision, changes=safe) is None:
                    raise ConnectorConflictError()
            except ConnectorConflictError:
                raise
            except Exception:
                raise ConnectorStoreError() from None
        self._write_local(safe)

    def _write_local(self, record: Mapping[str, Any], *, lock_held: bool = False) -> None:
        safe = _safe_record(record)
        path = self._path(str(safe["workspace_id"]), str(safe["connector_id"]))
        if not lock_held:
            with self._local_lock(path):
                self._write_local(safe, lock_held=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(safe, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temp.replace(path)

    def _delete_record(self, record: Mapping[str, Any]) -> None:
        workspace_id = str(record["workspace_id"])
        connector_id = str(record["connector_id"])
        expected_revision = int(record["revision"])
        if blob_configured():
            try:
                deleted = delete_blob_json_if_revision(self._blob_name(workspace_id, connector_id), expected_revision=expected_revision)
            except Exception:
                raise ConnectorStoreError("connector_record_delete_failed") from None
            if deleted is None:
                try:
                    self.get(workspace_id, connector_id)
                except FileNotFoundError:
                    return
                raise ConnectorConflictError()
            path = self._path(workspace_id, connector_id)
            with self._local_lock(path):
                path.unlink(missing_ok=True)
            return
        path = self._path(workspace_id, connector_id)
        with self._local_lock(path):
            if not path.exists():
                return
            current = self.get(workspace_id, connector_id)
            if int(current["revision"]) != expected_revision:
                raise ConnectorConflictError()
            path.unlink()

    def _changed_record(self, record: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
        updated = dict(record)
        for key in ("status", "delete_pending", "delete_phase", "pending_task_id", "sync_token"):
            if key in changes:
                if changes[key] is None:
                    updated.pop(key, None)
                else:
                    updated[key] = changes[key]
        if "metadata" in changes:
            updated["metadata"] = _safe_metadata(changes["metadata"])
        if "error" in changes:
            error = str(changes["error"] or "")
            if error:
                updated["error"] = re.sub(r"[^a-z0-9_.-]", "_", error.lower())[:80]
            else:
                updated.pop("error", None)
        updated["updated_at"] = _now()
        updated["revision"] = int(record.get("revision") or 0) + 1
        return _safe_record(updated)

    def _mark_delete_error(self, workspace_id: str, connector_id: str, code: str) -> None:
        try:
            current = self.get(workspace_id, connector_id)
            self.transition(workspace_id, connector_id, status="error", delete_pending=True, delete_phase=current.get("delete_phase") or "deleting", error=code)
        except (FileNotFoundError, ConnectorStoreError):
            pass

    @contextmanager
    def _local_lock(self, path: Path):
        lock = path.with_name(f"{path.name}.lock")
        token = uuid.uuid4().hex
        deadline = time.monotonic() + 3.0
        while True:
            try:
                lock.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 120:
                        lock.replace(lock.with_name(f"{lock.name}.stale-{uuid.uuid4().hex}"))
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise ConnectorConflictError()
                time.sleep(0.01)
        try:
            os.write(descriptor, json.dumps({"token": token, "pid": os.getpid()}).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            yield
        finally:
            try:
                owner = json.loads(lock.read_text(encoding="utf-8"))
                if owner.get("token") == token:
                    lock.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError, AttributeError):
                pass

    def _path(self, workspace_id: str, connector_id: str) -> Path:
        return self.root / _safe_component(workspace_id) / f"{_safe_component(connector_id)}.json"

    def _blob_prefix(self, workspace_id: str) -> str:
        return f"connectors/{_safe_component(workspace_id)}/"

    def _blob_name(self, workspace_id: str, connector_id: str) -> str:
        return f"{self._blob_prefix(workspace_id)}{_safe_component(connector_id)}.json"


def _safe_record(value: Mapping[str, Any], *, expected_workspace_id: str | None = None, expected_connector_id: str | None = None) -> dict[str, Any]:
    workspace = _identifier(value.get("workspace_id"), "workspace_id")
    connector_id = _identifier(value.get("connector_id"), "connector_id")
    if expected_workspace_id and workspace != _identifier(expected_workspace_id, "workspace_id"):
        raise ValueError("Connector record identity does not match its storage path")
    if expected_connector_id and connector_id != _identifier(expected_connector_id, "connector_id"):
        raise ValueError("Connector record identity does not match its storage path")
    kind = _kind(value.get("kind"))
    record = {
        "connector_id": connector_id,
        "workspace_id": workspace,
        "kind": kind,
        "status": str(value.get("status") or "disconnected")[:32],
        "persistence": "key_vault" if value.get("persistence") == "key_vault" else "session_only",
        "secret_ref": _secret_reference(value.get("secret_ref")),
        "metadata": _safe_metadata(value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}),
        "created_at": str(value.get("created_at") or _now())[:80],
        "updated_at": str(value.get("updated_at") or _now())[:80],
        "revision": max(1, int(value.get("revision") or 1)),
    }
    if value.get("delete_pending"):
        record["delete_pending"] = True
    if value.get("delete_phase") in {"deleting", "secret_deleted", "record_deleted"}:
        record["delete_phase"] = str(value["delete_phase"])
    if value.get("error"):
        record["error"] = re.sub(r"[^a-z0-9_.-]", "_", str(value["error"]).lower())[:80]
    if value.get("pending_task_id"):
        record["pending_task_id"] = _identifier(value["pending_task_id"], "pending_task_id")
    if value.get("sync_token"):
        token = str(value["sync_token"])
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ValueError("Invalid connector sync token")
        record["sync_token"] = token
    if record["status"] == "finalizing" and ("pending_task_id" not in record or "sync_token" not in record):
        raise ValueError("Finalizing connector record is incomplete")
    if record["secret_ref"] != expected_secret_reference(record["persistence"], workspace, connector_id):
        raise ValueError("Connector record secret reference does not match its trusted identity")
    return record


def _safe_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key)[:80]: _safe_value(item)
        for key, item in value.items()
        if not _SENSITIVE.search(str(key)) and _safe_value(item) is not None
    }


def _safe_value(value: Any) -> str | int | bool | list[str] | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and 0 <= value <= 10**12:
        return value
    if isinstance(value, (list, tuple)):
        return [str(item)[:160] for item in value if not _SENSITIVE.search(str(item))][:100]
    if isinstance(value, str) and not _SENSITIVE.search(value):
        return value[:240]
    return None


def _secret_reference(value: Any) -> str:
    reference = str(value or "")
    if not re.fullmatch(r"(?:kv|session):[A-Za-z0-9_-]{8,160}", reference):
        raise ValueError("Invalid connector secret reference")
    return reference


def _identifier(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", text):
        raise ValueError(f"Invalid {field}")
    return text


def _kind(value: Any) -> str:
    kind = str(value or "").lower()
    if kind not in _KINDS:
        raise ValueError("Unsupported connector kind")
    return kind


def _safe_component(value: Any) -> str:
    return _identifier(value, "identifier")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _secret_is_missing(exc: BaseException) -> bool:
    return isinstance(exc, (FileNotFoundError, KeyError)) or type(exc).__name__ in {"ResourceNotFoundError", "SecretNotFoundError"}


__all__ = ["ConnectorConflictError", "ConnectorDeleteError", "ConnectorStore", "ConnectorStoreError"]
