from __future__ import annotations

import errno
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

try:
    from .blob_store import blob_configured, claim_blob_json, compare_and_swap_blob_json, download_blob_json, list_blob_json, upload_blob_json
    from .identity import public_actor
except ImportError:
    from blob_store import blob_configured, claim_blob_json, compare_and_swap_blob_json, download_blob_json, list_blob_json, upload_blob_json
    from identity import public_actor


ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "generated-outputs" / "tasks"
TASK_BLOB_PREFIX = "tasks"
LOCAL_CLAIM_STALE_SECONDS = 300
LOCAL_TASK_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK = threading.RLock()
_TERMINAL = {"partial", "completed", "failed", "cancelled"}
_STATUSES = {"preparing", "queued", "running", "cancel_requested", *_TERMINAL}
_TASK_FIELDS = {
    "task_id", "workspace_id", "task_type", "action", "status", "attempt", "actor", "result", "error",
    "progress", "revision", "retry_of", "created_at", "updated_at", "started_at", "completed_at",
}
_PUBLIC_ACTOR_FIELDS = {"name", "email", "actor_id", "tenant_id", "source"}
_RESULT_IDS = {"task_id", "job_id", "artifact_job_id", "ingest_job_id", "file_id", "artifact_id", "run_id", "conversation_id"}
_RESULT_URLS = {"url", "artifact_url"}
_SENSITIVE = re.compile(r"(?:password|secret|token|authorization|connection[_-]?string|access[_-]?key|credential|accountkey|sharedaccesssignature|sig=|bearer\s+)", re.I)
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.:/-]{1,300}$")


class TaskPersistenceError(RuntimeError):
    pass


def create_task(payload: Mapping[str, Any], actor: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(payload or {})
    now = _now()
    task = {
        "task_id": f"task_{uuid4().hex[:16]}",
        "workspace_id": _required_text(source.get("workspace_id"), "workspace_id", 160),
        "task_type": _required_text(source.get("task_type") or source.get("operation"), "task_type", 120),
        "action": _required_text(source.get("action") or "workspace.read", "action", 120),
        "status": "preparing" if source.get("initial_status") == "preparing" else "queued",
        "attempt": max(1, int(source.get("attempt") or 1)),
        "actor": _public_actor(actor),
        "result": _safe_result(source.get("result")),
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    if source.get("retry_of"):
        task["retry_of"] = _required_text(source.get("retry_of"), "retry_of", 100)
    return _persist_new(task)


def get_task(task_id: str) -> dict[str, Any]:
    normalized = _required_text(task_id, "task_id", 100)
    local = _load_local(normalized)
    remote = download_blob_json(_task_blob(normalized)) or {}
    task = remote if remote else local
    if not task:
        raise FileNotFoundError(normalized)
    return _clean_task(task)


def list_tasks(workspace_id: str | None = None) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    if TASK_DIR.exists():
        for path in TASK_DIR.glob("task_*.json"):
            item = _load_path(path)
            if item.get("task_id"):
                by_id[str(item["task_id"])] = item
    for item in list_blob_json(f"{TASK_BLOB_PREFIX}/task_"):
        cleaned = _clean_task(item)
        if cleaned.get("task_id"):
            by_id[str(cleaned["task_id"])] = cleaned
    tasks = list(by_id.values())
    if workspace_id:
        tasks = [item for item in tasks if item.get("workspace_id") == str(workspace_id)]
    return sorted(tasks, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def claim_task(task_id: str, worker: str) -> dict[str, Any] | None:
    normalized = _required_text(task_id, "task_id", 100)
    if not blob_configured():
        return _claim_local_task(normalized)
    with _LOCK:
        task = get_task(normalized)
        if task.get("status") != "queued":
            return None
        changes = {"status": "running", "started_at": _now(), "updated_at": _now(), "revision": int(task.get("revision") or 0) + 1}
        try:
            claimed = claim_blob_json(_task_blob(normalized), expected_status="queued", changes=changes)
        except Exception as exc:
            raise TaskPersistenceError("durable task claim failed") from exc
        if claimed is None:
            return None
        cleaned = _clean_task(claimed)
        _persist_local_task(cleaned)
        return cleaned


def activate_prepared_task(task_id: str) -> dict[str, Any] | None:
    normalized = _required_text(task_id, "task_id", 100)
    if not blob_configured():
        return _update_local_task(normalized, {"status": "queued"}, expected_status="preparing")
    with _LOCK:
        task = get_task(normalized)
        if task.get("status") != "preparing":
            return None
        updated = _apply_changes(task, {"status": "queued"})
        try:
            committed = compare_and_swap_blob_json(
                _task_blob(normalized),
                expected_revision=int(task.get("revision") or 0),
                changes=updated,
            )
        except Exception as exc:
            raise TaskPersistenceError("durable task activation failed") from exc
        if committed is None:
            return None
        cleaned = _clean_task(committed)
        _persist_local_task(cleaned)
        return cleaned


def update_task(task_id: str, **changes: Any) -> dict[str, Any]:
    normalized = _required_text(task_id, "task_id", 100)
    if not blob_configured():
        return _update_local_task(normalized, changes)
    with _LOCK:
        task = get_task(normalized)
        updated = _apply_changes(task, changes)
        if updated is task:
            return task
        try:
            committed = compare_and_swap_blob_json(
                _task_blob(normalized),
                expected_revision=int(task.get("revision") or 0),
                changes=updated,
            )
        except Exception as exc:
            raise TaskPersistenceError("durable task update failed") from exc
        if committed is None:
            return get_task(normalized)
        cleaned = _clean_task(committed)
        _persist_local_task(cleaned)
        return cleaned


def request_cancel(task_id: str) -> dict[str, Any]:
    return update_task(task_id, status="cancel_requested")


def retry_task(task_id: str, actor: Mapping[str, Any] | None) -> dict[str, Any]:
    task = get_task(task_id)
    if task.get("status") not in {"failed", "partial", "cancelled"}:
        raise ValueError("only terminal failed, partial, or cancelled tasks can be retried")
    return create_task(
        {
            "workspace_id": task.get("workspace_id"), "task_type": task.get("task_type"), "action": task.get("action"),
            "retry_of": task.get("task_id"), "attempt": int(task.get("attempt") or 1) + 1,
        },
        actor,
    )


def _persist_new(task: Mapping[str, Any]) -> dict[str, Any]:
    value = _clean_task(task)
    if blob_configured():
        try:
            upload_blob_json(_task_blob(str(value.get("task_id") or "")), value)
        except Exception as exc:
            raise TaskPersistenceError("durable task create failed") from exc
    return _persist_local_task(value)


def _persist_local_task(task: Mapping[str, Any]) -> dict[str, Any]:
    value = _clean_task(task)
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    path = _task_path(str(value.get("task_id") or ""))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return value


def _claim_local_task(task_id: str) -> dict[str, Any] | None:
    lock_path = _claim_lock_path(task_id)
    for _ in range(3):
        token = _try_acquire_local_task_lock(lock_path)
        if token is None:
            task = get_task(task_id)
            if task.get("status") != "queued" or not _recover_stale_claim_lock(lock_path, task):
                return None
            continue
        try:
            task = get_task(task_id)
            if task.get("status") != "queued":
                return None
            task.update({"status": "running", "started_at": _now(), "updated_at": _now(), "revision": int(task.get("revision") or 0) + 1})
            return _persist_local_task(task)
        finally:
            _release_claim_lock(lock_path, token)
    return None


def _update_local_task(
    task_id: str,
    changes: Mapping[str, Any],
    *,
    expected_status: str | None = None,
) -> dict[str, Any] | None:
    lock_path = _claim_lock_path(task_id)
    deadline = time.monotonic() + LOCAL_TASK_LOCK_TIMEOUT_SECONDS
    while True:
        token = _try_acquire_local_task_lock(lock_path)
        if token is not None:
            try:
                task = get_task(task_id)
                if expected_status is not None and task.get("status") != expected_status:
                    return None
                updated = _apply_changes(task, changes)
                return task if updated is task else _persist_local_task(updated)
            finally:
                _release_claim_lock(lock_path, token)
        _recover_stale_claim_lock(lock_path, {})
        if time.monotonic() >= deadline:
            raise TaskPersistenceError("local task transition lock is unavailable")
        time.sleep(0.01)


def _try_acquire_local_task_lock(lock_path: Path) -> str | None:
    reclaim_path = _reclaim_lock_path(lock_path)
    if reclaim_path.exists():
        return None
    token = uuid4().hex
    payload = {"token": token, "pid": os.getpid(), "created_at": _now()}
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    try:
        os.write(descriptor, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        os.fsync(descriptor)
    except OSError:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    if reclaim_path.exists():
        _release_claim_lock(lock_path, token)
        return None
    return token


def _recover_stale_claim_lock(lock_path: Path, task: Mapping[str, Any]) -> bool:
    try:
        stat = lock_path.stat()
    except FileNotFoundError:
        return True
    if time.time() - stat.st_mtime < LOCAL_CLAIM_STALE_SECONDS:
        return False
    owner = _read_lock_owner(lock_path)
    if owner is None or _pid_is_live(owner.get("pid")) is not False:
        return False
    reclaim_path = _reclaim_lock_path(lock_path)
    reclaim_token = _try_acquire_reclaim_lock(reclaim_path)
    if reclaim_token is None:
        return False
    try:
        try:
            current = lock_path.stat()
        except FileNotFoundError:
            return True
        current_owner = _read_lock_owner(lock_path)
        if (
            current.st_mtime_ns != stat.st_mtime_ns
            or current_owner != owner
            or _pid_is_live(owner.get("pid")) is not False
        ):
            return False
        stale_path = lock_path.with_name(f"{lock_path.name}.stale-{uuid4().hex}")
        try:
            os.replace(lock_path, stale_path)
        except FileNotFoundError:
            return True
        try:
            stale_path.unlink(missing_ok=True)
        except OSError:
            pass
        return True
    finally:
        _release_claim_lock(reclaim_path, reclaim_token)


def _read_lock_owner(lock_path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping) or not isinstance(value.get("token"), str):
        return None
    try:
        pid = int(value.get("pid"))
    except (TypeError, ValueError):
        return None
    return {"token": value["token"], "pid": pid}


def _pid_is_live(pid: Any) -> bool | None:
    try:
        process_id = int(pid)
    except (TypeError, ValueError):
        return None
    if process_id <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, process_id)
        if not handle:
            error = ctypes.get_last_error()
            return False if error in {87, 1168} else True if error == 5 else None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return False if exc.errno == errno.ESRCH else None
    return True


def _try_acquire_reclaim_lock(reclaim_path: Path) -> str | None:
    token = uuid4().hex
    try:
        descriptor = os.open(str(reclaim_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    try:
        os.write(descriptor, json.dumps({"token": token, "pid": os.getpid()}).encode("utf-8"))
        os.fsync(descriptor)
        return token
    finally:
        os.close(descriptor)


def _release_claim_lock(lock_path: Path, token: str) -> None:
    try:
        owner = _read_lock_owner(lock_path)
        if owner and owner.get("token") == token:
            lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _apply_changes(task: dict[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    requested_status = str(changes.get("status") or task.get("status") or "")
    current_status = str(task.get("status") or "")
    if current_status in _TERMINAL:
        return task
    if requested_status not in _STATUSES or not _transition_allowed(current_status, requested_status):
        raise ValueError("invalid task status transition")
    updated = dict(task)
    updated["status"] = requested_status
    if "progress" in changes:
        progress = int(changes["progress"])
        if progress < 0 or progress > 100 or progress < int(task.get("progress") or 0):
            raise ValueError("progress must be monotonic within an attempt")
        updated["progress"] = progress
    if isinstance(changes.get("result"), Mapping):
        updated["result"] = _safe_result(changes["result"])
    if isinstance(changes.get("error"), Mapping):
        error = _safe_error(changes["error"])
        if error:
            updated["error"] = error
    for key in ("started_at", "completed_at"):
        if changes.get(key):
            updated[key] = str(changes[key])[:80]
    if requested_status in _TERMINAL and "completed_at" not in updated:
        updated["completed_at"] = _now()
    updated["revision"] = int(task.get("revision") or 0) + 1
    updated["updated_at"] = _now()
    return _clean_task(updated)


def _transition_allowed(current: str, target: str) -> bool:
    allowed = {
        "preparing": {"preparing", "queued", "cancel_requested", "cancelled", "failed"},
        "queued": {"queued", "running", "cancel_requested", "cancelled", "failed"},
        "running": {"running", "cancel_requested", "partial", "completed", "failed", "cancelled"},
        "cancel_requested": {"cancel_requested", "cancelled", "partial", "completed", "failed"},
    }
    return target in allowed.get(current, set())


def _clean_task(value: Mapping[str, Any]) -> dict[str, Any]:
    task = {key: value[key] for key in _TASK_FIELDS if key in value}
    task["task_id"] = _required_text(task.get("task_id"), "task_id", 100)
    task["workspace_id"] = _required_text(task.get("workspace_id"), "workspace_id", 160)
    task["task_type"] = _required_text(task.get("task_type"), "task_type", 120)
    task["action"] = _required_text(task.get("action"), "action", 120)
    task["status"] = str(task.get("status") or "queued")
    task["attempt"] = max(1, int(task.get("attempt") or 1))
    task["revision"] = max(1, int(task.get("revision") or 1))
    task["actor"] = _public_actor(task.get("actor") if isinstance(task.get("actor"), Mapping) else {})
    task["result"] = _safe_result(task.get("result"))
    if isinstance(task.get("error"), Mapping):
        error = _safe_error(task["error"])
        if error:
            task["error"] = error
        else:
            task.pop("error", None)
    return task


def _public_actor(actor: Mapping[str, Any] | None) -> dict[str, Any]:
    clean = public_actor(dict(actor or {}))
    return {
        key: str(clean[key])[:160]
        for key in _PUBLIC_ACTOR_FIELDS
        if clean.get(key) and not _SENSITIVE.search(str(clean[key]))
    }


def _safe_result(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    safe: dict[str, Any] = {}
    for key, item in source.items():
        name = str(key)
        if name in _RESULT_IDS and _safe_text(item):
            safe[name] = str(item)
        elif (name == "count" or name.endswith("_count") or name == "bytes") and isinstance(item, int) and 0 <= item <= 10**12:
            safe[name] = item
        elif name in _RESULT_URLS and _safe_project_url(item):
            safe[name] = str(item)
    return safe


def _safe_error(value: Mapping[str, Any]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key in ("category", "code"):
        item = value.get(key)
        if _safe_text(item):
            safe[key] = str(item)
    return safe


def _safe_project_url(value: Any) -> bool:
    text = str(value or "")
    parsed = urlparse(text)
    return text.startswith("/api/") and not parsed.query and not parsed.fragment and not _SENSITIVE.search(text)


def _safe_text(value: Any) -> bool:
    text = str(value or "")
    return bool(_SAFE_TEXT.fullmatch(text)) and not _SENSITIVE.search(text)


def _load_local(task_id: str) -> dict[str, Any]:
    return _load_path(_task_path(task_id))


def _load_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _clean_task(value) if isinstance(value, dict) else {}


def _task_path(task_id: str) -> Path:
    return TASK_DIR / f"{_safe_key(task_id)}.json"


def _claim_lock_path(task_id: str) -> Path:
    return TASK_DIR / f"{_safe_key(task_id)}.claim"


def _reclaim_lock_path(lock_path: Path) -> Path:
    return lock_path.with_name(f"{lock_path.name}.reclaim")


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["TaskPersistenceError", "claim_task", "create_task", "get_task", "list_tasks", "request_cancel", "retry_task", "update_task"]
