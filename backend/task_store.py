from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

try:
    from .blob_store import blob_configured, claim_blob_json, download_blob_json, list_blob_json, upload_blob_json
    from .identity import public_actor
except ImportError:
    from blob_store import blob_configured, claim_blob_json, download_blob_json, list_blob_json, upload_blob_json
    from identity import public_actor


ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "generated-outputs" / "tasks"
TASK_BLOB_PREFIX = "tasks"
_LOCK = threading.RLock()
_TERMINAL = {"partial", "completed", "failed", "cancelled"}
_STATUSES = {"queued", "running", "cancel_requested", *_TERMINAL}
_SENSITIVE_KEY = re.compile(r"(?:password|secret|token|authorization|connection[_-]?string|access[_-]?key|credential)", re.I)


def create_task(payload: Mapping[str, Any], actor: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(payload or {})
    workspace_id = _required_text(source.get("workspace_id"), "workspace_id", 160)
    task_type = _required_text(source.get("task_type") or source.get("operation"), "task_type", 120)
    action = _required_text(source.get("action") or "workspace.read", "action", 120)
    now = _now()
    task = {
        "task_id": f"task_{uuid4().hex[:16]}",
        "workspace_id": workspace_id,
        "task_type": task_type,
        "action": action,
        "status": "queued",
        "attempt": max(1, int(source.get("attempt") or 1)),
        "actor": public_actor(dict(actor or {})),
        "result": _safe_value(source.get("result") if isinstance(source.get("result"), Mapping) else {}),
        "created_at": now,
        "updated_at": now,
    }
    if source.get("retry_of"):
        task["retry_of"] = _required_text(source.get("retry_of"), "retry_of", 100)
    return _persist_task(task)


def get_task(task_id: str) -> dict[str, Any]:
    normalized = _required_text(task_id, "task_id", 100)
    local = _load_local(normalized)
    remote = download_blob_json(_task_blob(normalized)) or {}
    task = remote if str(remote.get("updated_at") or "") > str(local.get("updated_at") or "") else local
    if not task:
        raise FileNotFoundError(normalized)
    return dict(task)


def list_tasks(workspace_id: str | None = None) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    if TASK_DIR.exists():
        for path in TASK_DIR.glob("task_*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict) and item.get("task_id"):
                by_id[str(item["task_id"])] = item
    for item in list_blob_json(f"{TASK_BLOB_PREFIX}/task_"):
        if not isinstance(item, dict) or not item.get("task_id"):
            continue
        current = by_id.get(str(item["task_id"]))
        if current is None or str(item.get("updated_at") or "") > str(current.get("updated_at") or ""):
            by_id[str(item["task_id"])] = item
    tasks = list(by_id.values())
    if workspace_id:
        tasks = [item for item in tasks if str(item.get("workspace_id") or "") == str(workspace_id)]
    return sorted(tasks, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def claim_task(task_id: str, worker: str) -> dict[str, Any] | None:
    normalized = _required_text(task_id, "task_id", 100)
    with _LOCK:
        task = get_task(normalized)
        if task.get("status") != "queued":
            return None
        changes = {"status": "running", "worker": str(worker or "worker")[:120], "started_at": _now(), "updated_at": _now()}
        persistence = task.get("persistence") if isinstance(task.get("persistence"), Mapping) else {}
        if blob_configured() and persistence.get("blob") != "failed":
            claimed = claim_blob_json(_task_blob(normalized), expected_status="queued", changes=changes)
            if claimed is None:
                return None
            _persist_local_task(claimed)
            return claimed
        task.update(changes)
        return _persist_task(task)


def update_task(task_id: str, **changes: Any) -> dict[str, Any]:
    normalized = _required_text(task_id, "task_id", 100)
    with _LOCK:
        task = get_task(normalized)
        requested_status = changes.get("status")
        if task.get("status") in _TERMINAL and requested_status and requested_status != task.get("status"):
            return task
        if requested_status:
            status = str(requested_status)
            if status not in _STATUSES:
                raise ValueError("invalid task status")
            task["status"] = status
        if "progress" in changes:
            progress = int(changes["progress"])
            if progress < 0 or progress > 100 or progress < int(task.get("progress") or 0):
                raise ValueError("progress must be monotonic within an attempt")
            task["progress"] = progress
        if "result" in changes and isinstance(changes["result"], Mapping):
            task["result"] = _safe_value(changes["result"])
        if "error" in changes and isinstance(changes["error"], Mapping):
            task["error"] = _safe_value(changes["error"])
        for key in ("completed_at", "worker"):
            if key in changes and changes[key] is not None:
                task[key] = str(changes[key])[:300]
        task["updated_at"] = _now()
        return _persist_task(task)


def request_cancel(task_id: str) -> dict[str, Any]:
    with _LOCK:
        task = get_task(task_id)
        if task.get("status") in _TERMINAL:
            return task
        return update_task(task_id, status="cancel_requested")


def retry_task(task_id: str, actor: Mapping[str, Any] | None) -> dict[str, Any]:
    task = get_task(task_id)
    if task.get("status") not in {"failed", "partial", "cancelled"}:
        raise ValueError("only terminal failed, partial, or cancelled tasks can be retried")
    return create_task(
        {"workspace_id": task.get("workspace_id"), "task_type": task.get("task_type"), "action": task.get("action"), "retry_of": task.get("task_id"), "attempt": int(task.get("attempt") or 1) + 1},
        actor,
    )


def _persist_task(task: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(task)
    persistence = dict(value.get("persistence") or {}) if isinstance(value.get("persistence"), Mapping) else {}
    persistence.update({"blob": "synced", "updated_at": _now()})
    value["persistence"] = persistence
    _persist_local_task(value)
    try:
        upload_blob_json(_task_blob(str(value.get("task_id") or "")), value)
    except Exception as exc:
        value["persistence"] = {**persistence, "blob": "failed", "error_type": type(exc).__name__, "updated_at": _now()}
        _persist_local_task(value)
    return dict(value)


def _persist_local_task(task: Mapping[str, Any]) -> None:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    path = _task_path(str(task.get("task_id") or ""))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(task), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_local(task_id: str) -> dict[str, Any]:
    path = _task_path(task_id)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _task_path(task_id: str) -> Path:
    return TASK_DIR / f"{_safe_key(task_id)}.json"


def _task_blob(task_id: str) -> str:
    return f"{TASK_BLOB_PREFIX}/{_safe_key(task_id)}.json"


def _safe_key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip(".-")[:180]
    if not clean:
        raise ValueError("identifier is invalid")
    return clean


def _required_text(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text[:limit]


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key)[:120]: _safe_value(item) for key, item in value.items() if not _SENSITIVE_KEY.search(str(key))}
    if isinstance(value, list):
        return [_safe_value(item) for item in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)[:500] if isinstance(value, str) else value
    return str(value)[:500]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["claim_task", "create_task", "get_task", "list_tasks", "request_cancel", "retry_task", "update_task"]
