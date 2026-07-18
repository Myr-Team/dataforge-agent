from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

try:
    from .blob_store import BlobJsonReadError, blob_configured, download_blob_json_strict, upload_artifact, upload_blob_json
except ImportError:
    from blob_store import BlobJsonReadError, blob_configured, download_blob_json_strict, upload_artifact, upload_blob_json


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_RECORD_DIR = ROOT / "generated-outputs" / "artifact-registry"
ARTIFACT_RECORD_BLOB_PREFIX = "artifact-registry"
_NAME_RE = re.compile(r"^artifact-[A-Za-z0-9_-]{24,128}\.[A-Za-z0-9]{1,12}$")
_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9]{1,12}$")
_STATUSES = {"reserved", "ready"}


class ArtifactPersistenceError(RuntimeError):
    pass


def reserve_artifact(
    *,
    workspace_id: str,
    kind: str,
    content_type: str,
    suffix: str,
) -> dict[str, Any]:
    workspace = _required_text(workspace_id, "workspace_id", 160)
    artifact_kind = _required_text(kind, "kind", 80)
    media_type = _required_text(content_type, "content_type", 200)
    extension = str(suffix or "").strip().lower()
    if not _SUFFIX_RE.fullmatch(extension):
        raise ValueError("artifact suffix is invalid")
    artifact_name = f"artifact-{secrets.token_urlsafe(24)}{extension}"
    record = {
        "artifact_name": artifact_name,
        "workspace_id": workspace,
        "kind": artifact_kind,
        "content_type": media_type,
        "status": "reserved",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _persist(record)
    return dict(record)


def write_artifact(reservation: Mapping[str, Any], content: bytes, output_dir: Path) -> dict[str, Any]:
    record = _validated_record(reservation)
    if record is None or record["status"] != "reserved":
        raise ArtifactPersistenceError("artifact must be reserved before bytes are written")
    current = get_artifact(str(record["artifact_name"]))
    if current is None or current["status"] != "reserved":
        raise ArtifactPersistenceError("artifact reservation is unavailable")
    if current["workspace_id"] != record["workspace_id"]:
        raise ArtifactPersistenceError("artifact reservation workspace mismatch")

    body = bytes(content)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / str(record["artifact_name"])
    try:
        with path.open("xb") as handle:
            handle.write(body)
    except FileExistsError as exc:
        raise ArtifactPersistenceError("artifact storage name collision") from exc
    except OSError as exc:
        raise ArtifactPersistenceError("artifact local write failed") from exc

    blob: dict[str, Any] = {}
    if blob_configured():
        try:
            blob = upload_artifact(str(record["artifact_name"]), body, str(record["content_type"]))
        except Exception as exc:
            raise ArtifactPersistenceError("artifact Blob write failed") from exc
    return mark_artifact_ready(str(record["artifact_name"]), byte_count=len(body), blob=blob)


def mark_artifact_ready(
    artifact_name: str,
    *,
    byte_count: int,
    blob: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record = get_artifact(artifact_name)
    if record is None or record["status"] != "reserved":
        raise ArtifactPersistenceError("artifact reservation is unavailable")
    ready = {
        **record,
        "status": "ready",
        "bytes": max(0, int(byte_count)),
        "ready_at": _now(),
        "updated_at": _now(),
    }
    if blob:
        ready["blob_name"] = str(blob.get("blob_name") or "")
        ready["blob_url"] = str(blob.get("blob_url") or "")
    _persist(ready)
    return dict(ready)


def get_artifact(artifact_name: str) -> dict[str, Any] | None:
    name = _artifact_name(artifact_name)
    if blob_configured():
        try:
            remote = download_blob_json_strict(_record_blob(name))
        except BlobJsonReadError as exc:
            raise ArtifactPersistenceError("durable artifact record read failed") from exc
        return _validated_record(remote, expected_name=name)
    return _validated_record(_load_local(name), expected_name=name)


def _persist(record: Mapping[str, Any]) -> None:
    value = _validated_record(record)
    if value is None:
        raise ArtifactPersistenceError("artifact record is invalid")
    if blob_configured():
        try:
            upload_blob_json(_record_blob(str(value["artifact_name"])), value)
        except Exception as exc:
            raise ArtifactPersistenceError("durable artifact record persistence failed") from exc
    _persist_local(value)


def _persist_local(record: Mapping[str, Any]) -> None:
    ARTIFACT_RECORD_DIR.mkdir(parents=True, exist_ok=True)
    path = _record_path(str(record["artifact_name"]))
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ArtifactPersistenceError("artifact local record persistence failed") from exc


def _load_local(artifact_name: str) -> dict[str, Any] | None:
    path = _record_path(artifact_name)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _validated_record(value: Mapping[str, Any] | None, *, expected_name: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        name = _artifact_name(str(value.get("artifact_name") or ""))
        if expected_name is not None and name != expected_name:
            return None
        status = str(value.get("status") or "")
        if status not in _STATUSES:
            return None
        record = {
            "artifact_name": name,
            "workspace_id": _required_text(value.get("workspace_id"), "workspace_id", 160),
            "kind": _required_text(value.get("kind"), "kind", 80),
            "content_type": _required_text(value.get("content_type"), "content_type", 200),
            "status": status,
            "created_at": _required_text(value.get("created_at"), "created_at", 80),
            "updated_at": _required_text(value.get("updated_at"), "updated_at", 80),
        }
    except ValueError:
        return None
    for key in ("ready_at", "blob_name", "blob_url"):
        if value.get(key) is not None:
            record[key] = str(value[key])
    if value.get("bytes") is not None:
        try:
            record["bytes"] = max(0, int(value["bytes"]))
        except (TypeError, ValueError):
            return None
    return record


def _artifact_name(value: str) -> str:
    name = Path(str(value or "")).name
    if name != value or not _NAME_RE.fullmatch(name):
        raise ValueError("artifact name is invalid")
    return name


def _record_path(artifact_name: str) -> Path:
    return ARTIFACT_RECORD_DIR / f"{_artifact_name(artifact_name)}.json"


def _record_blob(artifact_name: str) -> str:
    return f"{ARTIFACT_RECORD_BLOB_PREFIX}/{_artifact_name(artifact_name)}.json"


def _required_text(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(f"{field} is invalid")
    return text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ArtifactPersistenceError",
    "get_artifact",
    "mark_artifact_ready",
    "reserve_artifact",
    "write_artifact",
]
