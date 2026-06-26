from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .blob_store import delete_blob_name, download_blob_json, upload_blob_json
except ImportError:
    from blob_store import delete_blob_name, download_blob_json, upload_blob_json


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "generated-outputs" / "runs"
RUN_REGISTRY_BLOB = "registry/runs.json"
RUN_BLOB_PREFIX = "runs"

_ACTIVE: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()


def start_run(run_id: str, workspace_id: str, message: str) -> None:
    now = _utc_now()
    with _LOCK:
        _ACTIVE[run_id] = {
            "run_id": run_id,
            "conversation_id": run_id,
            "workspace_id": workspace_id,
            "message": message,
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "steps": [],
            "models": [],
            "answer_delta_summary": {"count": 0, "chars": 0},
        }


def record_event(run_id: str | None, event: str, data: Any) -> None:
    if not run_id:
        return
    plain = _plain(data)
    now = _utc_now()
    with _LOCK:
        run = _ACTIVE.get(run_id)
        if not run:
            return
        run["updated_at"] = now
        if event == "answer_delta":
            delta = str((plain or {}).get("delta") or "") if isinstance(plain, dict) else ""
            summary = run.setdefault("answer_delta_summary", {"count": 0, "chars": 0})
            summary["count"] = int(summary.get("count") or 0) + 1
            summary["chars"] = int(summary.get("chars") or 0) + len(delta)
            if delta and not summary.get("first_delta"):
                summary["first_delta"] = delta[:80]
            if delta:
                summary["last_delta"] = delta[-80:]
            return
        step = _compact_step(event, plain, now)
        run.setdefault("steps", []).append(step)
        if event == "model_response" and isinstance(plain, dict):
            run.setdefault("models", []).append(
                {
                    "agent": plain.get("agent"),
                    "response_id": plain.get("response_id"),
                    "usage": plain.get("usage") or {},
                    "mode": plain.get("mode"),
                    "time": now,
                }
            )
        if event == "audit" and isinstance(plain, dict):
            run["audit"] = plain
        if event == "final" and isinstance(plain, dict):
            run["final"] = plain


def complete_run(
    run_id: str,
    *,
    status: str = "completed",
    final: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with _LOCK:
        run = _ACTIVE.pop(run_id, None)
    if not run:
        return None
    if final is not None:
        run["final"] = _plain(final)
    if artifact is not None:
        run["artifact"] = _plain(artifact)
    run["status"] = status
    run["completed_at"] = _utc_now()
    run["updated_at"] = run["completed_at"]
    run["verdict"] = _verdict(run)
    run["confidence"] = _confidence(run)
    run["step_count"] = len(run.get("steps") or [])
    run["summary"] = _run_summary(run)
    return _persist_run(run)


def list_runs(workspace_id: str | None = None) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in _local_run_summaries():
        if item.get("run_id"):
            by_id[str(item["run_id"])] = item
    registry = download_blob_json(RUN_REGISTRY_BLOB) or {}
    for item in registry.get("runs") or []:
        if isinstance(item, dict) and item.get("run_id"):
            by_id[str(item["run_id"])] = item
    items = list(by_id.values())
    if workspace_id:
        items = [item for item in items if item.get("workspace_id") == workspace_id]
    return sorted(items, key=lambda item: str(item.get("time") or item.get("completed_at") or ""), reverse=True)


def get_run(run_id: str) -> dict[str, Any]:
    safe = _safe_name(run_id)
    path = RUN_DIR / f"{safe}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = download_blob_json(f"{RUN_BLOB_PREFIX}/{safe}.json")
    if data:
        return data
    raise FileNotFoundError(run_id)


PLAN_FLAGSHIP_BLOB = "registry/plan-flagship.json"


def _flagship_map() -> dict[str, str]:
    data = download_blob_json(PLAN_FLAGSHIP_BLOB) or {}
    mapping = data.get("flagship")
    return mapping if isinstance(mapping, dict) else {}


def get_flagship_plan(workspace_id: str) -> str | None:
    """Return the run_id marked as the workspace's flagship plan, if any."""
    return _flagship_map().get(workspace_id)


def set_flagship_plan(workspace_id: str, run_id: str | None) -> dict[str, Any]:
    """Mark (or clear, when run_id is falsy) the workspace's flagship plan."""
    mapping = _flagship_map()
    if run_id:
        mapping[workspace_id] = run_id
    else:
        mapping.pop(workspace_id, None)
    try:
        upload_blob_json(PLAN_FLAGSHIP_BLOB, {"version": 1, "flagship": mapping})
    except Exception:
        pass
    return {"workspace_id": workspace_id, "flagship_run_id": mapping.get(workspace_id)}


def purge_workspace_runs(workspace_id: str) -> dict[str, Any]:
    """Delete persisted run records for one workspace from local storage and Blob registry."""
    run_ids = sorted({str(item.get("run_id") or "") for item in list_runs(workspace_id) if item.get("run_id")})
    deleted_local = 0
    deleted_blob = 0
    for run_id in run_ids:
        safe = _safe_name(run_id)
        path = RUN_DIR / f"{safe}.json"
        if path.exists():
            try:
                path.unlink()
                deleted_local += 1
            except Exception:
                pass
        if delete_blob_name(f"{RUN_BLOB_PREFIX}/{safe}.json"):
            deleted_blob += 1
    try:
        registry = download_blob_json(RUN_REGISTRY_BLOB) or {}
        entries = [item for item in registry.get("runs") or [] if isinstance(item, dict) and item.get("workspace_id") != workspace_id]
        upload_blob_json(RUN_REGISTRY_BLOB, {"version": registry.get("version") or 1, "runs": entries})
    except Exception:
        pass
    try:
        set_flagship_plan(workspace_id, None)
    except Exception:
        pass
    return {
        "workspace_id": workspace_id,
        "run_ids": run_ids,
        "deleted_local_runs": deleted_local,
        "deleted_blob_runs": deleted_blob,
    }


def _persist_run(run: dict[str, Any]) -> dict[str, Any]:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(str(run.get("run_id") or "run"))
    path = RUN_DIR / f"{safe}.json"
    run["local_path"] = str(path)
    summary = dict(run.get("summary") or _run_summary(run))
    blob_name = f"{RUN_BLOB_PREFIX}/{safe}.json"
    try:
        run["persistence"] = {"mode": "local_and_blob", "blob_name": blob_name}
        upload_blob_json(blob_name, run)
        registry = download_blob_json(RUN_REGISTRY_BLOB) or {}
        entries = [item for item in registry.get("runs") or [] if isinstance(item, dict)]
        entries = [item for item in entries if item.get("run_id") != run.get("run_id")]
        entries.append(summary)
        entries = sorted(entries, key=lambda item: str(item.get("time") or ""), reverse=True)[:300]
        upload_blob_json(RUN_REGISTRY_BLOB, {"version": 1, "runs": entries})
    except Exception as exc:
        run["persistence"] = {"mode": "local_only", "error": f"{type(exc).__name__}: {exc}"[:500]}
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return run


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    steps = []
    for step in (run.get("steps") or [])[:24]:
        data = step.get("data") or {}
        steps.append(
            {
                "time": step.get("time"),
                "event": step.get("event"),
                "agent": data.get("agent") if isinstance(data, dict) else None,
                "name": data.get("name") if isinstance(data, dict) else None,
            }
        )
    return {
        "run_id": run.get("run_id"),
        "time": run.get("completed_at") or run.get("updated_at") or run.get("started_at"),
        "workspace_id": run.get("workspace_id"),
        "verdict": run.get("verdict"),
        "confidence": run.get("confidence"),
        "status": run.get("status"),
        "steps": steps,
        "step_count": len(run.get("steps") or []),
        "maf": _maf_summary(run),
    }


def _maf_summary(run: dict[str, Any]) -> dict[str, Any] | None:
    """Summarise the Microsoft Agent Framework workflow activity for run history."""
    graph: dict[str, Any] | None = None
    revisions = 0
    audit_rounds = 0
    for step in run.get("steps") or []:
        event = step.get("event")
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        if event == "maf_workflow":
            graph = data
        elif event == "audit":
            audit_rounds += 1
        elif event == "role_change" and data.get("orchestrator") == "maf" and data.get("agent") == "df-feasibility-analyst":
            revisions += 1
    if graph is None:
        return None
    return {
        "framework": graph.get("framework"),
        "framework_version": graph.get("framework_version"),
        "pattern": graph.get("pattern"),
        "max_revisions": graph.get("max_revisions"),
        "revisions": revisions,
        "audit_rounds": audit_rounds,
    }


def _local_run_summaries() -> list[dict[str, Any]]:
    if not RUN_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in RUN_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        summary = data.get("summary") if isinstance(data, dict) else None
        if isinstance(summary, dict):
            items.append(summary)
    return items


def _compact_step(event: str, data: Any, timestamp: str) -> dict[str, Any]:
    return {
        "time": timestamp,
        "event": event,
        "data": _truncate(_plain(data), depth=0),
    }


def _truncate(value: Any, *, depth: int) -> Any:
    if depth > 5:
        return str(value)[:300]
    if isinstance(value, dict):
        return {str(key): _truncate(item, depth=depth + 1) for key, item in list(value.items())[:80]}
    if isinstance(value, list):
        return [_truncate(item, depth=depth + 1) for item in value[:80]]
    if isinstance(value, str):
        return value if len(value) <= 5000 else value[:5000] + "...[truncated]"
    return value


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _verdict(run: dict[str, Any]) -> str | None:
    artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
    feasibility = artifact.get("feasibility") or {}
    if isinstance(feasibility, dict):
        return feasibility.get("verdict")
    return None


def _confidence(run: dict[str, Any]) -> str | None:
    artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
    feasibility = artifact.get("feasibility") or {}
    if isinstance(feasibility, dict):
        return feasibility.get("overall_confidence")
    return None


def _safe_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "")).strip("-")
    if text:
        return text[:120]
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
