from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

try:
    from .blob_store import BlobJsonReadError, blob_configured, claim_blob_json, download_blob_json, download_blob_json_strict, list_blob_json, upload_blob_json
    from .identity import public_actor
    from .run_store import get_run, list_runs
    from .task_store import TaskPersistenceError, activate_prepared_task, claim_task, create_task, get_task, list_tasks, update_task
except ImportError:
    from blob_store import BlobJsonReadError, blob_configured, claim_blob_json, download_blob_json, download_blob_json_strict, list_blob_json, upload_blob_json
    from identity import public_actor
    from run_store import get_run, list_runs
    from task_store import TaskPersistenceError, activate_prepared_task, claim_task, create_task, get_task, list_tasks, update_task


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_JOB_DIR = ROOT / "generated-outputs" / "artifact-jobs"
ARTIFACT_JOB_BLOB_PREFIX = "artifact-jobs"
ARTIFACT_JOB_REGISTRY_BLOB = f"{ARTIFACT_JOB_BLOB_PREFIX}/registry.json"
ARTIFACT_JOB_STALE_SECONDS = max(60, int(os.environ.get("DF_ARTIFACT_JOB_STALE_SECONDS", "900")))

_LOCK = threading.RLock()
_TERMINAL = {"partial", "completed", "failed", "cancelled"}
_KINDS = {"pdf", "concept_image", "audio", "pilot_plan", "action_plan", "roadmap", "validation_plan"}
_RESULT_KIND = {"audio": "audio_summary"}


class ArtifactJobPersistenceError(RuntimeError):
    pass


def create_artifact_job(
    payload: Mapping[str, Any],
    *,
    actor: Mapping[str, Any] | None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    request = dict(payload or {})
    workspace_id = _required_text(request.get("workspace_id"), "workspace_id", 160)
    source_run_id = _required_text(
        request.get("conversation_id") or request.get("run_id"),
        "conversation_id",
        180,
    )
    kinds = _normalize_kinds(request.get("kinds"))
    source_run = get_run(source_run_id)
    if str(source_run.get("workspace_id") or "") != workspace_id:
        raise ValueError("source run does not belong to the requested workspace")
    key_hash = _idempotency_hash(idempotency_key) if idempotency_key else None
    with _LOCK:
        if key_hash:
            existing = next(
                (
                    item
                    for item in list_artifact_jobs(workspace_id)
                    if item.get("idempotency_hash") == key_hash and item.get("status") not in _TERMINAL
                ),
                None,
            )
            if existing:
                return existing
        now = _now()
        opportunity = request.get("feasibility") if isinstance(request.get("feasibility"), Mapping) else {}
        title = str(opportunity.get("opportunity_id") or request.get("title") or "项目产物").strip()[:100]
        job = {
            "job_id": f"artifact_job_{uuid4().hex[:16]}",
            "workspace_id": workspace_id,
            "source_run_id": source_run_id,
            "requested_kinds": kinds,
            "plan_version": _source_plan_version(workspace_id, source_run_id),
            "idempotency_hash": key_hash,
            "display_name": title,
            "status": "queued",
            "actor": public_actor(dict(actor or {})),
            "artifacts": {},
            "errors": {},
            "warnings": [],
            "retryable": False,
            "created_at": now,
            "updated_at": now,
        }
        task = create_task(
            {
                "workspace_id": workspace_id,
                "task_type": "artifact.generate",
                "action": "artifact.generate",
                "result": {"artifact_job_id": job["job_id"]},
                "initial_status": "preparing",
            },
            actor,
        )
        job["task_id"] = task["task_id"]
        try:
            persisted = _persist_job(job)
        except ArtifactJobPersistenceError:
            _compensate_task(str(task["task_id"]))
            raise
        try:
            activated = activate_prepared_task(str(task["task_id"]))
        except Exception as exc:
            raise ArtifactJobPersistenceError("durable artifact task activation failed") from exc
        if activated is None:
            raise ArtifactJobPersistenceError("durable artifact task activation failed")
        return persisted


def get_artifact_job(job_id: str, *, strict_blob_read: bool = False) -> dict[str, Any]:
    normalized = _required_text(job_id, "job_id", 100)
    path = _job_path(normalized)
    local: dict[str, Any] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            local = parsed if isinstance(parsed, dict) else {}
        except (OSError, json.JSONDecodeError):
            local = {}
    try:
        remote = (download_blob_json_strict(_job_blob(normalized)) if strict_blob_read else download_blob_json(_job_blob(normalized))) or {}
    except BlobJsonReadError as exc:
        raise TaskPersistenceError("durable artifact job read failed") from exc
    job = remote if str(remote.get("updated_at") or "") > str(local.get("updated_at") or "") else local
    if not job:
        raise FileNotFoundError(normalized)
    return _recover_stale(job)


def list_artifact_jobs(workspace_id: str | None = None) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    if ARTIFACT_JOB_DIR.exists():
        for path in ARTIFACT_JOB_DIR.glob("artifact_job_*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict) and item.get("job_id"):
                by_id[str(item["job_id"])] = item
    for item in list_blob_json(f"{ARTIFACT_JOB_BLOB_PREFIX}/artifact_job_"):
        if not isinstance(item, dict) or not item.get("job_id"):
            continue
        current = by_id.get(str(item["job_id"]))
        if current is None or str(item.get("updated_at") or "") > str(current.get("updated_at") or ""):
            by_id[str(item["job_id"])] = item
    registry = download_blob_json(ARTIFACT_JOB_REGISTRY_BLOB) or {}
    for item in registry.get("jobs") or []:
        if not isinstance(item, dict) or not item.get("job_id"):
            continue
        current = by_id.get(str(item["job_id"]))
        if current is None or str(item.get("updated_at") or "") > str(current.get("updated_at") or ""):
            by_id[str(item["job_id"])] = item
    jobs = [_recover_stale(item) for item in by_id.values()]
    if workspace_id:
        jobs = [item for item in jobs if item.get("workspace_id") == workspace_id]
    return sorted(jobs, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def run_artifact_job(job_id: str) -> dict[str, Any]:
    candidate = get_artifact_job(job_id)
    if candidate.get("task_id"):
        if candidate.get("status") != "queued":
            return candidate
        task = _claim_linked_task(candidate)
        if task is None:
            if _linked_task_cancel_requested(candidate):
                cancelled = _update_job(job_id, status="cancelled", completed_at=_now(), retryable=False)
                return _sync_linked_task(cancelled)
            return get_artifact_job(job_id)
        job = _claim_job(job_id, linked_task_claimed=True)
        if job is None:
            return get_artifact_job(job_id)
    else:
        job = _claim_job(job_id)
        if job is None:
            return get_artifact_job(job_id)
    try:
        result = _produce(_producer_payload(job))
    except Exception as exc:
        failed = _update_job(
            job_id,
            status="failed",
            completed_at=_now(),
            retryable=True,
            errors={"job": {"message": "产物生成任务失败，可重试。", "error_type": type(exc).__name__}},
        )
        return _sync_linked_task(failed)

    warnings = [item for item in (result.get("warnings") or []) if isinstance(item, dict)]
    warning_by_kind = {str(item.get("kind") or ""): item for item in warnings if item.get("kind")}
    urls = result.get("artifact_urls") if isinstance(result.get("artifact_urls"), dict) else {}
    artifacts: dict[str, dict[str, Any]] = {}
    errors: dict[str, dict[str, Any]] = {}
    for requested in job.get("requested_kinds") or []:
        result_kind = _RESULT_KIND.get(requested, requested)
        value = result.get(result_kind) if isinstance(result.get(result_kind), dict) else {}
        url = urls.get(result_kind) or value.get("artifact_url")
        if url:
            artifacts[result_kind] = {**value, "artifact_url": url}
            continue
        warning = warning_by_kind.get(result_kind) or warning_by_kind.get(requested)
        errors[result_kind] = {
            "message": str((warning or {}).get("message") or f"{result_kind} 生成失败。")[:300],
            "error": str((warning or {}).get("error") or value.get("error") or "no artifact url")[:500],
        }
    status = "completed" if artifacts and not errors else "partial" if artifacts else "failed"
    completed = _update_job(
        job_id,
        status=status,
        artifacts=artifacts,
        errors=errors,
        warnings=warnings,
        result_meta={
            key: result.get(key)
            for key in ("persisted_run_id", "version_run_id", "generated_at", "artifact_generated_at")
            if result.get(key) is not None
        },
        retryable=status in {"partial", "failed"},
        completed_at=_now(),
    )
    return _sync_linked_task(completed)


def _claim_linked_task(job: Mapping[str, Any]) -> dict[str, Any] | None:
    task_id = str(job.get("task_id") or "")
    return claim_task(task_id, "artifact-worker") if task_id else None


def _linked_task_cancel_requested(job: Mapping[str, Any]) -> bool:
    task_id = str(job.get("task_id") or "")
    if not task_id:
        return False
    try:
        return get_task(task_id).get("status") == "cancel_requested"
    except FileNotFoundError:
        return False


def _sync_linked_task(job: Mapping[str, Any]) -> dict[str, Any]:
    task_id = str(job.get("task_id") or "")
    if not task_id:
        return dict(job)
    status = str(job.get("status") or "failed")
    changes: dict[str, Any] = {
        "status": status if status in {"partial", "completed", "failed", "cancelled"} else "failed",
        "result": {"artifact_job_id": job.get("job_id")},
        "completed_at": job.get("completed_at") or _now(),
    }
    if status in {"failed", "partial"}:
        changes["error"] = {"message": "artifact job did not complete", "status": status}
    try:
        update_task(task_id, **changes)
    except (FileNotFoundError, ValueError):
        pass
    return dict(job)


def _producer_payload(job: Mapping[str, Any]) -> dict[str, Any]:
    source_run_id = str(job.get("source_run_id") or "")
    run = get_run(source_run_id)
    if str(run.get("workspace_id") or "") != str(job.get("workspace_id") or ""):
        raise ValueError("source run does not belong to the requested workspace")
    artifact = run.get("artifact") if isinstance(run.get("artifact"), dict) else {}
    if not artifact:
        final = run.get("final") if isinstance(run.get("final"), dict) else {}
        artifact = final.get("artifact") if isinstance(final.get("artifact"), dict) else {}
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    if not feasibility:
        raise ValueError("source analysis has no feasibility artifact")
    return {
        "workspace_id": job.get("workspace_id"),
        "conversation_id": source_run_id,
        "feasibility": feasibility,
        "corpus": artifact.get("corpus") or {},
        "market": artifact.get("market") or {},
        "audit": artifact.get("audit") or {},
        "answer": artifact.get("answer") or {},
        "proposal": artifact.get("proposal") or {},
        "reference_images": artifact.get("reference_images") or [],
        "narrative": artifact.get("narrative"),
        "plan_version": job.get("plan_version") or "V1",
        "kinds": list(job.get("requested_kinds") or []),
    }


def _produce(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from .orchestrator import produce_from_existing_report
    except ImportError:
        from orchestrator import produce_from_existing_report
    return produce_from_existing_report(payload)


def _update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _LOCK:
        job = get_artifact_job(job_id)
        job.update(changes)
        job["updated_at"] = _now()
        return _persist_job(job)


def _claim_job(job_id: str, *, linked_task_claimed: bool = False) -> dict[str, Any] | None:
    with _LOCK:
        job = get_artifact_job(job_id)
        if str(job.get("status") or "") != "queued":
            return None
        task_id = str(job.get("task_id") or "")
        if task_id and not linked_task_claimed:
            try:
                if get_task(task_id).get("status") != "queued":
                    return None
            except FileNotFoundError:
                return None
        changes = {
            "status": "running",
            "started_at": _now(),
            "updated_at": _now(),
            "retryable": False,
        }
        blob_state = (job.get("persistence") or {}).get("blob") if isinstance(job.get("persistence"), Mapping) else None
        if blob_configured() and blob_state != "failed":
            claimed = claim_blob_json(
                _job_blob(job_id),
                expected_status="queued",
                changes=changes,
            )
            if claimed is None:
                return None
            _persist_local_job(claimed)
            _persist_registry(claimed)
            return claimed
        job.update(changes)
        return _persist_job(job)


def _persist_job(job: dict[str, Any]) -> dict[str, Any]:
    persistence = dict(job.get("persistence") or {}) if isinstance(job.get("persistence"), Mapping) else {}
    persistence.update({"blob": "synced", "updated_at": _now()})
    job["persistence"] = persistence
    if blob_configured():
        try:
            upload_blob_json(_job_blob(str(job.get("job_id") or "")), job)
        except Exception as exc:
            raise ArtifactJobPersistenceError("durable artifact job persistence failed") from exc
    _persist_local_job(job)
    _persist_registry(job)
    return dict(job)


def _compensate_task(task_id: str) -> bool:
    try:
        update_task(task_id, status="failed", error={"category": "artifact_job", "code": "persistence_failed"})
    except Exception:
        return False
    return True


def recover_prepared_artifact_tasks(workspace_id: str | None = None) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    for task in list_tasks(workspace_id):
        if task.get("task_type") != "artifact.generate" or task.get("status") != "preparing":
            continue
        task_id = str(task.get("task_id") or "")
        result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
        job_id = str(result.get("artifact_job_id") or "")
        try:
            job = get_artifact_job(job_id, strict_blob_read=True) if job_id else None
        except FileNotFoundError:
            job = None
        if job is None:
            update_task(task_id, status="failed", error={"category": "artifact_job", "code": "persistence_failed"})
            continue
        target = str(job.get("status") or "queued")
        if target in _TERMINAL:
            _sync_linked_task(job)
            continue
        if target != "queued":
            raise TaskPersistenceError("prepared artifact recovery found a non-queued job")
        if activate_prepared_task(task_id) is not None:
            recovered.append(dict(job))
            continue
        current = get_task(task_id)
        if current.get("status") != "preparing":
            continue
        raise TaskPersistenceError("prepared artifact task activation did not commit")
    return recovered


def _persist_local_job(job: Mapping[str, Any]) -> None:
    ARTIFACT_JOB_DIR.mkdir(parents=True, exist_ok=True)
    path = _job_path(str(job.get("job_id") or ""))
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _persist_registry(job: Mapping[str, Any]) -> None:
    registry = download_blob_json(ARTIFACT_JOB_REGISTRY_BLOB) or {}
    by_id = {
        str(item.get("job_id")): item
        for item in registry.get("jobs") or []
        if isinstance(item, dict) and item.get("job_id")
    }
    summary_keys = (
        "job_id",
        "workspace_id",
        "source_run_id",
        "requested_kinds",
        "plan_version",
        "idempotency_hash",
        "display_name",
        "status",
        "actor",
        "artifacts",
        "errors",
        "warnings",
        "retryable",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "result_meta",
    )
    by_id[str(job.get("job_id"))] = {key: job.get(key) for key in summary_keys if job.get(key) is not None}
    value = {
        "version": 1,
        "updated_at": _now(),
        "jobs": sorted(by_id.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)[:500],
    }
    try:
        upload_blob_json(ARTIFACT_JOB_REGISTRY_BLOB, value)
    except Exception:
        pass


def _recover_stale(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("status") not in {"queued", "running"}:
        return job
    updated = _parse_time(job.get("updated_at"))
    if updated is None or (_now_timestamp() - updated) <= ARTIFACT_JOB_STALE_SECONDS:
        return job
    recovered = dict(job)
    recovered.update(
        {
            "status": "failed",
            "retryable": True,
            "completed_at": _now(),
            "updated_at": _now(),
            "errors": {"job": {"message": "生成进程已中断，可重新发起任务。", "error_type": "worker_interrupted"}},
        }
    )
    return _persist_job(recovered)


def _normalize_kinds(value: Any) -> list[str]:
    candidates = value if isinstance(value, list) else ["pdf", "concept_image"]
    result: list[str] = []
    for item in candidates:
        kind = "audio" if str(item) == "audio_summary" else str(item or "").strip()
        if kind in _KINDS and kind not in result:
            result.append(kind)
    if not result:
        raise ValueError("at least one supported artifact kind is required")
    return result


def _source_plan_version(workspace_id: str, source_run_id: str) -> str:
    try:
        summaries = list_runs(workspace_id)
    except Exception:
        return "V1"
    analyses = [
        item
        for item in summaries
        if not str(item.get("version_kind") or "")
        and str(item.get("status") or "").lower() not in {"followup", "followup_edit", "clarify", "error"}
        and item.get("verdict")
    ]
    analyses.sort(key=lambda item: str(item.get("time") or item.get("completed_at") or item.get("started_at") or ""))
    for index, item in enumerate(analyses):
        if str(item.get("run_id") or item.get("conversation_id") or "") == source_run_id:
            return f"V{index + 1}"
    return f"V{max(1, len(analyses))}"


def _idempotency_hash(value: str | None) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:24]


def _job_path(job_id: str) -> Path:
    return ARTIFACT_JOB_DIR / f"{_safe_key(job_id)}.json"


def _job_blob(job_id: str) -> str:
    return f"{ARTIFACT_JOB_BLOB_PREFIX}/{_safe_key(job_id)}.json"


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


def _parse_time(value: Any) -> float | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _now_timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ArtifactJobPersistenceError",
    "create_artifact_job",
    "get_artifact_job",
    "list_artifact_jobs",
    "recover_prepared_artifact_tasks",
    "run_artifact_job",
]
