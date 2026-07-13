from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from .blob_store import blob_configured, delete_blob_name, download_blob_json_strict, list_blob_json_strict, upload_blob_json
    from .connector_secret_store import SecretExpiredError, SecretStore
except ImportError:
    from blob_store import blob_configured, delete_blob_name, download_blob_json_strict, list_blob_json_strict, upload_blob_json
    from connector_secret_store import SecretExpiredError, SecretStore


_SENSITIVE = re.compile(r"password|passwd|pwd|connection.?string|sas|token|secret|credential|username|user.?id", re.IGNORECASE)
_KINDS = {"sql", "blob"}


class ConnectorDeleteError(RuntimeError):
    pass


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
        }
        try:
            self._write(record)
        except Exception:
            try:
                secret_store.delete(reference)
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
            record = _safe_record(remote)
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
        return _safe_record(raw)

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        if blob_configured():
            records = [_safe_record(item) for item in list_blob_json_strict(self._blob_prefix(workspace_id)) if isinstance(item, dict)]
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
        record = self.get(workspace_id, connector_id)
        for key in ("status", "delete_pending"):
            if key in changes:
                record[key] = changes[key]
        if "metadata" in changes:
            record["metadata"] = _safe_metadata(changes["metadata"])
        if "error" in changes:
            error = str(changes["error"] or "")
            if error:
                record["error"] = re.sub(r"[^a-z0-9_.-]", "_", error.lower())[:80]
            else:
                record.pop("error", None)
        record["updated_at"] = _now()
        self._write(record)
        return record

    def reconnect(self, workspace_id: str, connector_id: str, secret_store: SecretStore) -> tuple[dict[str, Any], dict[str, str]]:
        record = self.get(workspace_id, connector_id)
        try:
            payload = secret_store.get(str(record["secret_ref"]))
        except SecretExpiredError:
            self.update(workspace_id, connector_id, status="expired", error="secret_expired")
            raise
        record = self.update(workspace_id, connector_id, status="connected", error=None)
        return record, payload

    def disconnect(self, workspace_id: str, connector_id: str) -> dict[str, Any]:
        return self.update(workspace_id, connector_id, status="disconnected", error=None)

    def delete(self, workspace_id: str, connector_id: str, secret_store: SecretStore) -> None:
        record = self.update(workspace_id, connector_id, status="deleting", delete_pending=True, error=None)
        try:
            secret_store.delete(str(record["secret_ref"]))
        except Exception as exc:
            self.update(workspace_id, connector_id, status="error", delete_pending=True, error="secret_delete_failed")
            raise ConnectorDeleteError("Connector secret delete failed; record retained for retry") from exc
        try:
            if blob_configured() and not delete_blob_name(self._blob_name(workspace_id, connector_id)):
                raise OSError("durable connector record delete failed")
            self._path(workspace_id, connector_id).unlink(missing_ok=True)
        except OSError as exc:
            self.update(workspace_id, connector_id, status="error", delete_pending=True, error="record_delete_failed")
            raise ConnectorDeleteError("Connector record delete failed after secret deletion; retry is required") from exc

    def _write(self, record: Mapping[str, Any]) -> None:
        safe = _safe_record(record)
        if blob_configured():
            upload_blob_json(self._blob_name(str(safe["workspace_id"]), str(safe["connector_id"])), safe)
        self._write_local(safe)

    def _write_local(self, record: Mapping[str, Any]) -> None:
        safe = _safe_record(record)
        path = self._path(str(safe["workspace_id"]), str(safe["connector_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(safe, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temp.replace(path)

    def _path(self, workspace_id: str, connector_id: str) -> Path:
        return self.root / _safe_component(workspace_id) / f"{_safe_component(connector_id)}.json"

    def _blob_prefix(self, workspace_id: str) -> str:
        return f"connectors/{_safe_component(workspace_id)}/"

    def _blob_name(self, workspace_id: str, connector_id: str) -> str:
        return f"{self._blob_prefix(workspace_id)}{_safe_component(connector_id)}.json"


def _safe_record(value: Mapping[str, Any]) -> dict[str, Any]:
    workspace = _identifier(value.get("workspace_id"), "workspace_id")
    connector_id = _identifier(value.get("connector_id"), "connector_id")
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
    }
    if value.get("delete_pending"):
        record["delete_pending"] = True
    if value.get("error"):
        record["error"] = re.sub(r"[^a-z0-9_.-]", "_", str(value["error"]).lower())[:80]
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


__all__ = ["ConnectorDeleteError", "ConnectorStore"]
