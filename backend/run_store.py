from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .blob_store import (
        blob_configured,
        compare_and_swap_blob_json,
        delete_blob_name,
        download_blob_json,
        upload_blob_json,
    )
    from .evidence_bundle import (
        sanitize_capability_metadata,
        sanitize_capability_pack_contract,
    )
    from .identity import is_trusted_identity, public_actor
except ImportError:
    from blob_store import (
        blob_configured,
        compare_and_swap_blob_json,
        delete_blob_name,
        download_blob_json,
        upload_blob_json,
    )
    from evidence_bundle import (
        sanitize_capability_metadata,
        sanitize_capability_pack_contract,
    )
    from identity import is_trusted_identity, public_actor


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "generated-outputs" / "runs"
RUN_REGISTRY_BLOB = "registry/runs.json"
RUN_BLOB_PREFIX = "runs"

_ACTIVE: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()


def start_run(run_id: str, workspace_id: str, message: str, actor: dict[str, Any] | None = None) -> None:
    now = _utc_now()
    clean_actor = public_actor(actor or {})
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
        if clean_actor:
            _ACTIVE[run_id]["actor"] = clean_actor
        _ACTIVE[run_id]["trusted_identity"] = is_trusted_identity(clean_actor)


def record_event(run_id: str | None, event: str, data: Any) -> None:
    if not run_id:
        return
    raw_data = _plain(data)
    now = _utc_now()
    with _LOCK:
        run = _ACTIVE.get(run_id)
        if not run:
            return
        plain = _sanitize_event_data(event, raw_data, _capability_scope(run))
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
        step = _compact_step(event, plain, now, _capability_scope(run))
        run.setdefault("steps", []).append(step)
        if event == "model_response" and isinstance(plain, dict):
            run.setdefault("models", []).append(
                {
                    "agent": plain.get("agent"),
                    "model": plain.get("model") or plain.get("model_name"),
                    "response_id": plain.get("response_id"),
                    "usage": plain.get("usage") or {},
                    "mode": plain.get("mode"),
                    "time": now,
                }
            )
        if event == "audit" and isinstance(plain, dict):
            run["audit"] = plain
        if event == "final" and isinstance(plain, dict):
            run["final"] = _sanitize_final(plain, _capability_scope(run))


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
        scope = _capability_scope(run)
        if final is not None:
            run["final"] = _sanitize_final(final, scope)
        if artifact is not None:
            run["artifact"] = _sanitize_artifact(artifact, scope)
        if str(status or "").strip().lower() in {"followup", "followup_edit"}:
            requested_source_id = str(
                (run.get("artifact") or {}).get("source_analysis_run_id")
                if isinstance(run.get("artifact"), dict)
                else ""
            ).strip()
            if not requested_source_id:
                requested_source_id = run_id
            canonical_source_id = resolve_canonical_experiment_source_run_id(
                str(run.get("workspace_id") or ""),
                requested_source_id,
            )
            try:
                source_analysis = get_run(run_id)
            except (FileNotFoundError, ValueError):
                source_analysis = {}
            if _is_completed_analysis(source_analysis):
                completed_at = _utc_now()
                suffix = hashlib.sha1(
                    f"{run_id}:{completed_at}:{run.get('message') or ''}".encode("utf-8")
                ).hexdigest()[:8]
                run["run_id"] = f"{_safe_name(run_id)}-followup-{suffix}"
                run["conversation_id"] = run_id
            if canonical_source_id:
                run["source_run_id"] = canonical_source_id
                run["experiment_version_id"] = f"version:{canonical_source_id}"
        run["status"] = status
        run["completed_at"] = _utc_now()
        run["updated_at"] = run["completed_at"]
        run["duration_ms"] = _duration_ms(run.get("started_at"), run.get("completed_at"))
        run["verdict"] = _verdict(run)
        run["confidence"] = _confidence(run)
        run["step_count"] = len(run.get("steps") or [])
        _assign_canonical_experiment_link(run)
        run["title"] = _run_title(run)
        run["summary"] = _run_summary_text(run)
        run["registry_summary"] = _run_summary(run)
        return _persist_run(run)


def _is_completed_analysis(run: Any) -> bool:
    if not isinstance(run, dict) or str(run.get("version_kind") or ""):
        return False
    if not str(run.get("status") or "").strip().lower().startswith("completed"):
        return False
    artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
    feasibility = artifact.get("feasibility") if isinstance(artifact, dict) else {}
    return isinstance(feasibility, dict) and bool(feasibility.get("verdict") or feasibility.get("dimensions"))


def list_runs(workspace_id: str | None = None) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in _local_run_summaries():
        if item.get("run_id"):
            by_id[str(item["run_id"])] = item
    registry = download_blob_json(RUN_REGISTRY_BLOB) or {}
    for item in registry.get("runs") or []:
        if isinstance(item, dict) and item.get("run_id"):
            # A local full run is newer and contains the authoritative steps/models.
            # Do not overwrite its recomputed summary with an older Blob registry row.
            safe_item = _sanitize_run_capability_metadata(item)
            if isinstance(safe_item, dict):
                by_id.setdefault(str(item["run_id"]), safe_item)
    items = list(by_id.values())
    if workspace_id:
        items = [item for item in items if item.get("workspace_id") == workspace_id]
    return sorted(
        items,
        key=lambda item: (
            str(item.get("time") or item.get("completed_at") or ""),
            str(item.get("run_id") or ""),
        ),
        reverse=True,
    )


def get_run(run_id: str) -> dict[str, Any]:
    safe = _safe_name(run_id)
    path = RUN_DIR / f"{safe}.json"
    if path.exists():
        return _normalize_run_detail(json.loads(path.read_text(encoding="utf-8")))
    data = download_blob_json(f"{RUN_BLOB_PREFIX}/{safe}.json")
    if data:
        return _normalize_run_detail(data)
    raise FileNotFoundError(run_id)


def update_run_proposal(run_id: str, proposal: dict[str, Any]) -> dict[str, Any] | None:
    """Merge newly produced artifacts back into a persisted run."""
    if not run_id or not isinstance(proposal, dict):
        return None
    with _LOCK:
        run = get_run(run_id)
        artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
        artifact = dict(artifact) if isinstance(artifact, dict) else {}
        previous = artifact.get("proposal") if isinstance(artifact.get("proposal"), dict) else {}
        previous = dict(previous or {})
        incoming = sanitize_capability_metadata(_plain(proposal), _capability_scope(run))
        incoming = incoming if isinstance(incoming, dict) else {}

        merged_urls = {}
        for source in (previous.get("artifact_urls"), incoming.get("artifact_urls")):
            if isinstance(source, dict):
                merged_urls.update({key: value for key, value in source.items() if value})

        merged_generated_at = {}
        for source in (previous.get("artifact_generated_at"), incoming.get("artifact_generated_at")):
            if isinstance(source, dict):
                merged_generated_at.update({key: value for key, value in source.items() if value})
        incoming_generated_at = incoming.get("generated_at")
        if incoming_generated_at and isinstance(incoming.get("artifact_urls"), dict):
            for key, value in incoming["artifact_urls"].items():
                if value:
                    merged_generated_at.setdefault(key, incoming_generated_at)

        merged = {**previous, **incoming}
        if merged_urls:
            merged["artifact_urls"] = merged_urls
        if merged_generated_at:
            merged["artifact_generated_at"] = merged_generated_at

        warnings: list[Any] = []
        for source in (previous.get("warnings"), incoming.get("warnings")):
            if isinstance(source, list):
                warnings.extend(item for item in source if item)
        if warnings:
            merged["warnings"] = warnings[-12:]

        artifact["proposal"] = merged
        run["artifact"] = artifact
        if isinstance(run.get("final"), dict):
            final = dict(run["final"])
            final_artifact = final.get("artifact") if isinstance(final.get("artifact"), dict) else {}
            final_artifact = dict(final_artifact or {})
            final_artifact["proposal"] = merged
            final["artifact"] = final_artifact
            run["final"] = final

        run["updated_at"] = _utc_now()
        run["verdict"] = _verdict(run)
        run["confidence"] = _confidence(run)
        run["title"] = _run_title(run)
        run["summary"] = _run_summary_text(run)
        run["registry_summary"] = _run_summary(run)
        return _persist_run(run)


def record_artifact_version(
    *,
    workspace_id: str,
    source_run_id: str,
    experiment_version_id: str | None = None,
    artifact: dict[str, Any],
    proposal: dict[str, Any],
    kinds: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Persist a lightweight version snapshot when artifacts are generated from a real analysis.

    This does not create a new feasibility judgement or experiment version. It
    records a deliverable attachment against an existing analysis decision.
    """
    workspace_id = str(workspace_id or "").strip()
    source_run_id = str(source_run_id or "").strip()
    if not workspace_id or not source_run_id or not isinstance(artifact, dict):
        return None
    expected_experiment_version_id = f"version:{source_run_id}"
    experiment_version_id = str(experiment_version_id or expected_experiment_version_id).strip()
    if experiment_version_id != expected_experiment_version_id:
        return None
    if not _canonical_experiment_version_exists(workspace_id, experiment_version_id):
        return None
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    if not (feasibility.get("verdict") or feasibility.get("dimensions")):
        return None

    now = _utc_now()
    source_actor: dict[str, Any] = {}
    try:
        source_actor = public_actor(get_run(source_run_id).get("actor") or {})
    except Exception:
        source_actor = {}
    source_safe = _safe_name(source_run_id)
    suffix = hashlib.sha1(f"{source_run_id}:{now}:{json.dumps(kinds or [], ensure_ascii=False, default=str)}".encode("utf-8")).hexdigest()[:8]
    version_run_id = f"{source_safe}-artifact-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{suffix}"
    merged_artifact = _sanitize_artifact(
        artifact,
        {"workspace_id": workspace_id, "scope_id": source_run_id},
    )
    merged_proposal = _plain(proposal if isinstance(proposal, dict) else {})
    if merged_proposal:
        merged_artifact["proposal"] = merged_proposal
    produced_kinds = [str(kind) for kind in (kinds or []) if str(kind).strip()]
    title_base = _clean_opportunity_text(feasibility.get("opportunity_id")) or _message_topic(merged_artifact.get("answer", {}).get("text") if isinstance(merged_artifact.get("answer"), dict) else "")
    run = {
        "run_id": version_run_id,
        "conversation_id": source_run_id,
        "workspace_id": workspace_id,
        "message": f"生成产物版本：{', '.join(produced_kinds) or 'artifact'}",
        "status": "completed",
        "started_at": now,
        "completed_at": now,
        "updated_at": now,
        "duration_ms": 0,
        "steps": [
            {
                "time": now,
                "event": "artifact_version",
                "data": {
                    "source_run_id": source_run_id,
                    "experiment_version_id": experiment_version_id,
                    "produced_kinds": produced_kinds,
                    "artifact_urls": (merged_proposal.get("artifact_urls") if isinstance(merged_proposal, dict) else {}) or {},
                },
            }
        ],
        "models": [],
        "actor": source_actor,
        "artifact": merged_artifact,
        "final": {
            "text": f"{title_base or '当前方案'} 已生成产物版本。",
            "artifact": merged_artifact,
            "source_run_id": source_run_id,
            "experiment_version_id": experiment_version_id,
            "version_kind": "artifact_generation",
        },
        "version_kind": "artifact_generation",
        "source_run_id": source_run_id,
        "experiment_version_id": experiment_version_id,
        "experiment_attachment": True,
        "produced_kinds": produced_kinds,
    }
    run["verdict"] = _verdict(run)
    run["confidence"] = _confidence(run)
    base_title = _run_title(run)
    run["title"] = _clean_phrase(f"{base_title} · 产物版", 44)
    run["summary"] = _run_summary_text(run)
    run["registry_summary"] = _run_summary(run)
    return _persist_run(run)


def record_plan_version(
    *,
    workspace_id: str,
    source_run_id: str,
    experiment_version_id: str | None = None,
    artifact: dict[str, Any],
    text: str,
) -> dict[str, Any] | None:
    """Persist a lightweight version snapshot when a follow-up creates a plan draft."""
    workspace_id = str(workspace_id or "").strip()
    source_run_id = str(source_run_id or "").strip()
    if not workspace_id or not source_run_id or not isinstance(artifact, dict):
        return None
    expected_experiment_version_id = f"version:{source_run_id}"
    experiment_version_id = str(experiment_version_id or expected_experiment_version_id).strip()
    if experiment_version_id != expected_experiment_version_id:
        return None
    if not _canonical_experiment_version_exists(workspace_id, experiment_version_id):
        return None
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    if not (feasibility.get("verdict") or feasibility.get("dimensions")):
        return None

    now = _utc_now()
    source_actor: dict[str, Any] = {}
    try:
        source_actor = public_actor(get_run(source_run_id).get("actor") or {})
    except Exception:
        source_actor = public_actor(artifact.get("actor") or {})
    source_safe = _safe_name(source_run_id)
    suffix = hashlib.sha1(f"{source_run_id}:{now}:plan_draft".encode("utf-8")).hexdigest()[:8]
    version_run_id = f"{source_safe}-plan-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{suffix}"
    merged_artifact = _sanitize_artifact(
        artifact,
        {"workspace_id": workspace_id, "scope_id": source_run_id},
    )
    plan_text = str(text or "").strip() or str((artifact.get("answer") or {}).get("text") or "").strip()
    merged_artifact["plan_draft"] = {
        "text": plan_text,
        "generated_at": now,
        "source_run_id": source_run_id,
    }
    title_base = (
        _clean_opportunity_text(feasibility.get("opportunity_id"))
        or _message_topic(plan_text)
        or _message_topic((merged_artifact.get("answer") or {}).get("text") if isinstance(merged_artifact.get("answer"), dict) else "")
        or "当前方案"
    )
    run = {
        "run_id": version_run_id,
        "conversation_id": source_run_id,
        "workspace_id": workspace_id,
        "message": "生成方案草稿版本",
        "status": "completed",
        "started_at": now,
        "completed_at": now,
        "updated_at": now,
        "duration_ms": 0,
        "steps": [
            {
                "time": now,
                "event": "plan_draft_version",
                "data": {
                    "source_run_id": source_run_id,
                    "experiment_version_id": experiment_version_id,
                    "produced_kinds": ["plan_draft"],
                },
            }
        ],
        "models": [],
        "actor": source_actor,
        "artifact": merged_artifact,
        "final": {
            "text": plan_text,
            "artifact": merged_artifact,
            "source_run_id": source_run_id,
            "experiment_version_id": experiment_version_id,
            "version_kind": "plan_draft",
        },
        "version_kind": "plan_draft",
        "source_run_id": source_run_id,
        "experiment_version_id": experiment_version_id,
        "experiment_attachment": True,
        "produced_kinds": ["plan_draft"],
    }
    run["verdict"] = _verdict(run)
    run["confidence"] = _confidence(run)
    run["title"] = _clean_phrase(f"{title_base} · 方案版", 44)
    run["summary"] = _run_summary_text(run)
    run["registry_summary"] = _run_summary(run)
    return _persist_run(run)


def _canonical_experiment_version_exists(workspace_id: str, experiment_version_id: str) -> bool:
    version_id = str(experiment_version_id or "").strip()
    if not version_id.startswith("version:"):
        return False
    source_run_id = version_id.removeprefix("version:").strip()
    if not source_run_id:
        return False
    return resolve_canonical_experiment_source_run_id(workspace_id, source_run_id) == source_run_id


def resolve_canonical_experiment_source_run_id(workspace_id: str, source_run_id: str) -> str | None:
    workspace_id = str(workspace_id or "").strip()
    source_run_id = str(source_run_id or "").strip()
    if not workspace_id or not source_run_id:
        return None
    try:
        source = get_run(source_run_id)
    except (FileNotFoundError, ValueError):
        return None
    if str(source.get("workspace_id") or "") != workspace_id:
        return None
    persisted = _persisted_canonical_target(workspace_id, source)
    if persisted:
        return persisted
    if _has_canonical_lineage_metadata(source):
        return None
    if _registry_history_is_truncated():
        return None
    runs = _workspace_run_details(workspace_id)
    resolved = _resolve_canonical_from_runs(workspace_id, runs, source_run_id)
    return resolved


def _assign_canonical_experiment_link(run: dict[str, Any]) -> None:
    if not _is_completed_analysis(run):
        return
    workspace_id = str(run.get("workspace_id") or "").strip()
    run_id = str(run.get("run_id") or "").strip()
    if not workspace_id or not run_id:
        return
    existing = [item for item in _workspace_run_details(workspace_id) if str(item.get("run_id") or "") != run_id]
    analyses = [item for item in existing if _is_completed_analysis(item)]
    basis: list[dict[str, Any]]
    if analyses:
        latest = max(
            analyses,
            key=lambda item: (
                str(item.get("completed_at") or item.get("updated_at") or item.get("started_at") or ""),
                str(item.get("run_id") or ""),
            ),
        )
        canonical_id = _persisted_canonical_target(workspace_id, latest)
        if canonical_id:
            try:
                canonical = get_run(canonical_id)
            except (FileNotFoundError, ValueError):
                canonical = {}
            basis = [item for item in (canonical, latest, run) if isinstance(item, dict) and item]
        elif _registry_history_is_truncated():
            _mark_canonical_lineage_unresolved(run)
            return
        else:
            basis = [*existing, run]
    elif _registry_history_is_truncated():
        _mark_canonical_lineage_unresolved(run)
        return
    else:
        basis = [run]
    resolved = _resolve_canonical_from_runs(workspace_id, basis, run_id)
    if resolved:
        _mark_canonical_lineage_trusted(run, resolved)
    else:
        _mark_canonical_lineage_unresolved(run)


def _persisted_canonical_target(workspace_id: str, source: dict[str, Any]) -> str | None:
    if not _is_completed_analysis(source):
        return None
    if str(source.get("workspace_id") or "") != workspace_id:
        return None
    target_id = str(source.get("canonical_experiment_run_id") or "").strip()
    if not target_id or not _lineage_envelope_matches(source, target_id):
        return None
    if target_id == str(source.get("run_id") or "").strip():
        return target_id
    try:
        target = get_run(target_id)
    except (FileNotFoundError, ValueError, KeyError):
        return None
    if (
        str(target.get("workspace_id") or "") != workspace_id
        or not _is_completed_analysis(target)
        or str(target.get("run_id") or "").strip() != target_id
    ):
        return None
    return target_id if _lineage_envelope_matches(target, target_id) else None


def trusted_canonical_experiment_run_id(
    workspace_id: str,
    source: dict[str, Any],
    runs_by_id: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """Validate a persisted lineage edge without recomputing or trusting caller fields."""
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id or not isinstance(source, dict) or not _is_completed_analysis(source):
        return None
    if str(source.get("workspace_id") or "") != workspace_id:
        return None
    target_id = str(source.get("canonical_experiment_run_id") or "").strip()
    source_id = str(source.get("run_id") or "").strip()
    if not source_id or not target_id or not _lineage_envelope_matches(source, target_id):
        return None
    if target_id == source_id:
        return source_id
    target = (runs_by_id or {}).get(target_id)
    if target is None:
        try:
            target = get_run(target_id)
        except (FileNotFoundError, ValueError, KeyError):
            return None
    if (
        not isinstance(target, dict)
        or str(target.get("workspace_id") or "") != workspace_id
        or str(target.get("run_id") or "").strip() != target_id
        or not _is_completed_analysis(target)
        or not _lineage_envelope_matches(target, target_id)
    ):
        return None
    return target_id


def hydrate_canonical_experiment_runs(
    workspace_id: str,
    runs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Hydrate trusted canonical targets omitted from a bounded recent-run view."""
    workspace_id = str(workspace_id or "").strip()
    hydrated = [item for item in runs if isinstance(item, dict)]
    by_id = {
        str(item.get("run_id") or "").strip(): item
        for item in hydrated
        if str(item.get("run_id") or "").strip()
    }
    unresolved: set[str] = set()

    for source in list(hydrated):
        source_id = str(source.get("run_id") or "").strip()
        if _is_completed_analysis(source) and _has_canonical_lineage_metadata(source):
            target_id = str(source.get("canonical_experiment_run_id") or "").strip()
            if target_id and target_id not in by_id and _lineage_envelope_matches(source, target_id):
                try:
                    target = get_run(target_id)
                except (FileNotFoundError, ValueError, KeyError):
                    target = None
                if isinstance(target, dict):
                    by_id[target_id] = target
                    hydrated.append(target)
            if trusted_canonical_experiment_run_id(workspace_id, source, by_id) is None:
                unresolved.add(source_id)

    for snapshot in list(hydrated):
        if str(snapshot.get("version_kind") or "") not in {"plan_draft", "artifact_generation"}:
            continue
        snapshot_id = str(snapshot.get("run_id") or "").strip()
        source_id = str(snapshot.get("source_run_id") or "").strip()
        version_id = str(snapshot.get("experiment_version_id") or "").strip()
        if not source_id or version_id != f"version:{source_id}" or snapshot.get("experiment_attachment") is not True:
            unresolved.add(snapshot_id)
            continue
        if source_id not in by_id:
            try:
                target = get_run(source_id)
            except (FileNotFoundError, ValueError, KeyError):
                target = None
            if isinstance(target, dict):
                by_id[source_id] = target
                hydrated.append(target)
        target = by_id.get(source_id)
        if not isinstance(target, dict) or trusted_canonical_experiment_run_id(workspace_id, target, by_id) != source_id:
            unresolved.add(snapshot_id)

    return hydrated, sorted(item for item in unresolved if item)[:20]


def _has_canonical_lineage_metadata(run: dict[str, Any]) -> bool:
    return any(
        key in run
        for key in (
            "canonical_experiment_run_id",
            "canonical_experiment_version_id",
            "canonical_resolution_status",
            "canonical_lineage_status",
        )
    )


def _lineage_envelope_matches(run: dict[str, Any], target_id: str) -> bool:
    return bool(
        target_id
        and str(run.get("canonical_experiment_run_id") or "").strip() == target_id
        and str(run.get("canonical_experiment_version_id") or "").strip() == f"version:{target_id}"
        and str(run.get("canonical_resolution_status") or "").strip().lower() == "resolved"
        and str(run.get("canonical_lineage_status") or "").strip().lower() == "trusted"
    )


def _mark_canonical_lineage_trusted(run: dict[str, Any], canonical_run_id: str) -> None:
    run["canonical_experiment_run_id"] = canonical_run_id
    run["canonical_experiment_version_id"] = f"version:{canonical_run_id}"
    run["canonical_resolution_status"] = "resolved"
    run["canonical_lineage_status"] = "trusted"


def _mark_canonical_lineage_unresolved(run: dict[str, Any]) -> None:
    run.pop("canonical_experiment_run_id", None)
    run.pop("canonical_experiment_version_id", None)
    run["canonical_resolution_status"] = "unresolved"
    run["canonical_lineage_status"] = "unresolved"


def _workspace_run_details(workspace_id: str) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for summary in list_runs(workspace_id):
        run_id = str(summary.get("run_id") or "").strip()
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        try:
            detail = get_run(run_id)
        except (FileNotFoundError, ValueError, KeyError):
            continue
        if isinstance(detail, dict):
            runs.append(detail)
    return runs


def _resolve_canonical_from_runs(
    workspace_id: str,
    runs: list[dict[str, Any]],
    source_run_id: str,
) -> str | None:
    try:
        from .experiment_store import resolve_canonical_experiment_run_id
        from .outcome_store import list_outcome_events
    except ImportError:
        from experiment_store import resolve_canonical_experiment_run_id
        from outcome_store import list_outcome_events
    try:
        outcomes = list_outcome_events(workspace_id)
    except (OSError, ValueError):
        outcomes = []
    return resolve_canonical_experiment_run_id(
        workspace_id,
        runs,
        source_run_id,
        outcomes=outcomes,
    )


def _registry_history_is_truncated() -> bool:
    try:
        registry = download_blob_json(RUN_REGISTRY_BLOB) or {}
    except Exception:
        return True
    entries = [item for item in registry.get("runs") or [] if isinstance(item, dict)]
    return registry.get("history_truncated") is True or len(entries) >= 300


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
    with _LOCK:
        return _persist_run_locked(run)


def _persist_run_locked(run: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_run_capability_metadata(run)
    if isinstance(sanitized, dict):
        run.clear()
        run.update(sanitized)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(str(run.get("run_id") or "run"))
    path = RUN_DIR / f"{safe}.json"
    run["local_path"] = str(path)
    summary = _run_summary(run)
    run["registry_summary"] = summary
    blob_name = f"{RUN_BLOB_PREFIX}/{safe}.json"
    try:
        run["persistence"] = {"mode": "local_and_blob", "blob_name": blob_name}
        upload_blob_json(blob_name, run)
        _persist_registry_summary(summary)
    except Exception as exc:
        if _is_completed_analysis(run) and blob_configured():
            _mark_canonical_lineage_unresolved(run)
            summary = _run_summary(run)
            run["registry_summary"] = summary
            try:
                upload_blob_json(blob_name, run)
            except Exception:
                pass
        run["persistence"] = {"mode": "local_only", "error": f"{type(exc).__name__}: {exc}"[:500]}
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return run


def _persist_registry_summary(summary: dict[str, Any]) -> None:
    attempts = 8 if blob_configured() else 1
    for _attempt in range(attempts):
        registry = download_blob_json(RUN_REGISTRY_BLOB) or {}
        entries = [item for item in registry.get("runs") or [] if isinstance(item, dict)]
        entries = [item for item in entries if item.get("run_id") != summary.get("run_id")]
        entries.append(summary)
        entries = sorted(
            entries,
            key=lambda item: (str(item.get("time") or ""), str(item.get("run_id") or "")),
            reverse=True,
        )
        history_truncated = registry.get("history_truncated") is True or len(entries) > 300
        expected_revision = int(registry.get("revision") or 0)
        value = {
            "version": 1,
            "revision": expected_revision + 1,
            "history_truncated": history_truncated,
            "runs": entries[:300],
        }
        if not blob_configured():
            upload_blob_json(RUN_REGISTRY_BLOB, value)
            return
        updated = compare_and_swap_blob_json(
            RUN_REGISTRY_BLOB,
            expected_revision=expected_revision,
            changes=value,
        )
        if updated is not None:
            return
    raise RuntimeError("run registry conditional update could not be confirmed")


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    run = _sanitize_run_capability_metadata(run)
    if not isinstance(run, dict):
        return {}
    artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
    proposal = artifact.get("proposal") if isinstance(artifact, dict) and isinstance(artifact.get("proposal"), dict) else {}
    artifact_urls = proposal.get("artifact_urls") if isinstance(proposal.get("artifact_urls"), dict) else {}
    iteration_inputs = artifact.get("iteration_inputs") if isinstance(artifact, dict) and isinstance(artifact.get("iteration_inputs"), list) else []
    capability_packs, capability_pack_provenance = _capability_pack_contract(
        artifact,
        _capability_scope(run),
    )
    computed_duration = _duration_ms(
        run.get("started_at"),
        run.get("completed_at") or run.get("updated_at"),
    )
    duration_ms = computed_duration if computed_duration is not None else run.get("duration_ms")
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
    summary = {
        "run_id": run.get("run_id"),
        "time": run.get("completed_at") or run.get("updated_at") or run.get("started_at"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("completed_at"),
        "duration_ms": duration_ms,
        "workspace_id": run.get("workspace_id"),
        "title": run.get("title") or _run_title(run),
        "summary": run.get("summary") if isinstance(run.get("summary"), str) else _run_summary_text(run),
        "message": _clean_phrase(run.get("message"), 160),
        "verdict": run.get("verdict"),
        "confidence": run.get("confidence"),
        "status": run.get("status"),
        "version_kind": run.get("version_kind"),
        "source_run_id": run.get("source_run_id"),
        "experiment_version_id": run.get("experiment_version_id"),
        "experiment_attachment": run.get("experiment_attachment") is True,
        "canonical_experiment_run_id": run.get("canonical_experiment_run_id"),
        "canonical_experiment_version_id": run.get("canonical_experiment_version_id"),
        "canonical_resolution_status": run.get("canonical_resolution_status"),
        "canonical_lineage_status": run.get("canonical_lineage_status"),
        "produced_kinds": run.get("produced_kinds") or [],
        "iteration_inputs": iteration_inputs[:12],
        "artifact_urls": {key: value for key, value in (artifact_urls or {}).items() if value},
        "capability_packs": capability_packs,
        "capability_pack_ids": [str(item["pack_id"]) for item in capability_packs],
        "steps": steps,
        "step_count": len(run.get("steps") or []),
        "maf": _maf_summary(run),
        "tokens": _token_usage(run),
        "actor": public_actor(run.get("actor") if isinstance(run.get("actor"), dict) else {}),
    }
    if capability_pack_provenance:
        summary["capability_pack_provenance"] = capability_pack_provenance
    return summary


def _normalize_run_detail(run: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    normalized = _sanitize_run_capability_metadata(run)
    if not isinstance(normalized, dict):
        return {}
    scope = _capability_scope(normalized)
    normalized["artifact"] = _sanitize_artifact(normalized.get("artifact"), scope)
    if isinstance(normalized.get("final"), dict):
        normalized["final"] = _sanitize_final(normalized["final"], scope)
    if isinstance(normalized.get("summary"), dict):
        normalized["registry_summary"] = normalized.get("summary")
        normalized["summary"] = normalized.get("summary_text") or _run_summary_text(normalized)
    normalized.setdefault("title", _run_title(normalized))
    if not isinstance(normalized.get("summary"), str):
        normalized["summary"] = _run_summary_text(normalized)
    normalized["actor"] = public_actor(normalized.get("actor") if isinstance(normalized.get("actor"), dict) else {})
    normalized["tokens"] = _token_usage(normalized)
    normalized["maf"] = _maf_summary(normalized)
    capability_packs, capability_pack_provenance = _capability_pack_contract(
        normalized.get("artifact") or (normalized.get("final") or {}).get("artifact") or {},
        scope,
    )
    normalized["capability_packs"] = capability_packs
    normalized["capability_pack_ids"] = [str(item["pack_id"]) for item in capability_packs]
    if capability_pack_provenance:
        normalized["capability_pack_provenance"] = capability_pack_provenance
    else:
        normalized.pop("capability_pack_provenance", None)
    normalized["registry_summary"] = _run_summary(normalized)
    return normalized


def _capability_scope(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    workspace_id = str(value.get("workspace_id") or "").strip()
    scope_id = str(value.get("conversation_id") or value.get("run_id") or "").strip()
    if not workspace_id or not scope_id:
        return {}
    return {"workspace_id": workspace_id, "scope_id": scope_id}


def _capability_pack_contract(
    artifact: Any,
    expected_scope: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not isinstance(artifact, dict) or not isinstance(artifact.get("capability_packs"), list):
        return [], {}
    return sanitize_capability_pack_contract(
        artifact["capability_packs"],
        artifact.get("capability_pack_provenance"),
        expected_scope,
    )


def _capability_packs(artifact: Any, expected_scope: dict[str, str] | None = None) -> list[dict[str, Any]]:
    return _capability_pack_contract(artifact, expected_scope)[0]


def _copy_capability_pack_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(record) for record in records]


def _clear_capability_metadata(value: Any) -> None:
    """Clear nested pack metadata before the generic registry sanitizer can rebuild it."""
    if isinstance(value, dict):
        has_metadata = any(
            key in value
            for key in ("capability_pack_ids", "capability_packs", "capability_pack_provenance")
        )
        for item in value.values():
            _clear_capability_metadata(item)
        if has_metadata:
            value["capability_pack_ids"] = []
            value["capability_packs"] = []
            value.pop("capability_pack_provenance", None)
    elif isinstance(value, list):
        for item in value:
            _clear_capability_metadata(item)


def _hydrate_maf_evidence_bundle(
    container: dict[str, Any],
    selected_packs: list[dict[str, Any]],
    provenance: dict[str, str],
) -> None:
    """Rebuild MAF's ID-only metadata from its sibling selected-pack contract."""
    if not selected_packs:
        return
    maf = container.get("maf")
    if not isinstance(maf, dict):
        return
    metadata = maf.get("evidence_bundle")
    if not isinstance(metadata, dict):
        return
    if "capability_pack_ids" not in metadata and "capability_packs" not in metadata:
        return
    metadata["capability_packs"] = _copy_capability_pack_records(selected_packs)
    metadata["capability_pack_ids"] = [str(record["pack_id"]) for record in selected_packs]
    metadata["capability_pack_provenance"] = dict(provenance)


def _hydrate_artifact_capability_metadata(
    value: Any,
    expected_scope: dict[str, str] | None,
) -> dict[str, Any]:
    """Use only the artifact's selected contract to repair nested MAF metadata."""
    artifact = _plain(value)
    if not isinstance(artifact, dict):
        return {}
    selected_packs, provenance = _capability_pack_contract(artifact, expected_scope)
    _clear_capability_metadata(artifact)
    if selected_packs:
        artifact["capability_packs"] = _copy_capability_pack_records(selected_packs)
        artifact["capability_pack_provenance"] = dict(provenance)
        _hydrate_maf_evidence_bundle(artifact, selected_packs, provenance)
    return artifact


def _sanitize_run_capability_metadata(value: Any) -> Any:
    """Sanitize a run after reconstructing MAF's legacy ID-only metadata safely."""
    run = _plain(value)
    if not isinstance(run, dict):
        return sanitize_capability_metadata(run)
    scope = _capability_scope(run)

    artifact = run.get("artifact")
    if isinstance(artifact, dict):
        run["artifact"] = _hydrate_artifact_capability_metadata(artifact, scope)

    final = run.get("final")
    if isinstance(final, dict) and isinstance(final.get("artifact"), dict):
        hydrated_final = dict(final)
        hydrated_final["artifact"] = _hydrate_artifact_capability_metadata(final["artifact"], scope)
        run["final"] = hydrated_final

    if isinstance(run.get("registry_summary"), dict):
        _clear_capability_metadata(run["registry_summary"])

    selected_summary_packs, summary_provenance = _capability_pack_contract(run, scope)
    if isinstance(run.get("maf"), dict):
        _clear_capability_metadata(run["maf"])
    if selected_summary_packs:
        run["capability_packs"] = _copy_capability_pack_records(selected_summary_packs)
        run["capability_pack_provenance"] = dict(summary_provenance)
        _hydrate_maf_evidence_bundle(run, selected_summary_packs, summary_provenance)

    return sanitize_capability_metadata(run, scope)


def _sanitize_artifact(value: Any, expected_scope: dict[str, str] | None = None) -> dict[str, Any]:
    artifact = sanitize_capability_metadata(
        _hydrate_artifact_capability_metadata(value, expected_scope),
        expected_scope,
    )
    if not isinstance(artifact, dict):
        return {}
    return artifact


def _sanitize_final(value: Any, expected_scope: dict[str, str] | None = None) -> dict[str, Any]:
    final = sanitize_capability_metadata(_plain(value), expected_scope)
    if not isinstance(final, dict):
        return {}
    if "artifact" in final:
        final["artifact"] = _sanitize_artifact(final.get("artifact"), expected_scope)
    return final


def _sanitize_event_data(
    event: str,
    data: Any,
    expected_scope: dict[str, str] | None = None,
) -> Any:
    if event != "capability_pack_selection" or not isinstance(data, dict):
        return data
    packs, provenance = sanitize_capability_pack_contract(
        data.get("capability_packs") if isinstance(data.get("capability_packs"), list) else [],
        data.get("capability_pack_provenance"),
        expected_scope,
    )
    sanitized = {
        "source": "normalized_goal_schema_profile_quality",
        "capability_packs": packs,
        "capability_pack_ids": [str(record["pack_id"]) for record in packs],
    }
    if provenance:
        sanitized["capability_pack_provenance"] = provenance
    return sanitized


_VERDICT_LABELS = {
    "feasible": "可行",
    "recommended": "建议推进",
    "conditional": "有条件可行",
    "not_yet_feasible": "暂不可行",
    "not_feasible": "暂不建议",
    "rejected": "暂不建议",
    "clarify": "待澄清",
    "followup_edit": "跟进",
    "corpus_qa": "资料问答",
}


def _run_title(run: dict[str, Any]) -> str:
    artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    has_feasibility_signal = bool(feasibility.get("opportunity_id") or feasibility.get("verdict"))
    message_topic = _message_topic(run.get("message"))
    topic = (
        (_clean_opportunity_text(feasibility.get("opportunity_id")) if has_feasibility_signal else "")
        or ("" if has_feasibility_signal else message_topic)
        or _first_opportunity_title(artifact)
        or message_topic
        or _clean_phrase(run.get("workspace_id"), 36)
        or "DataForge 分析"
    )
    verdict = str(run.get("verdict") or (feasibility or {}).get("verdict") or "").strip()
    label = _VERDICT_LABELS.get(verdict, "")
    if label and label not in topic:
        title = f"{topic} · {label}"
    else:
        title = topic
    return _clean_phrase(title, 34) or "DataForge 分析"


def _run_summary_text(run: dict[str, Any]) -> str:
    artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    has_feasibility_signal = bool(feasibility.get("opportunity_id") or feasibility.get("verdict"))
    message_topic = _message_topic(run.get("message"))
    title = _clean_phrase(
        (_clean_opportunity_text(feasibility.get("opportunity_id")) if has_feasibility_signal else "")
        or ("" if has_feasibility_signal else message_topic)
        or _first_opportunity_title(artifact)
        or message_topic,
        44,
    )
    verdict = str(run.get("verdict") or feasibility.get("verdict") or "").strip()
    verdict_text = _VERDICT_LABELS.get(verdict, verdict) if verdict else ""
    confidence = str(run.get("confidence") or feasibility.get("overall_confidence") or "").strip()
    gap = _first_clean_item(feasibility.get("gap_list"))
    recommendation = _clean_phrase(feasibility.get("recommendation"), 100)
    evidence = _evidence_hint(artifact)
    parts: list[str] = []
    if title:
        parts.append(f"围绕“{title}”")
    if verdict_text:
        parts.append(f"结论为{verdict_text}")
    if confidence:
        parts.append(f"置信度{confidence}")
    if evidence:
        parts.append(f"依据{evidence}")
    if gap:
        parts.append(f"主要缺口是{gap}")
    elif recommendation:
        parts.append(f"建议{recommendation}")
    if not parts:
        return _clean_phrase(run.get("message"), 120) or "本次运行已完成。"
    sentence = "，".join(parts)
    return _clean_phrase(sentence.rstrip("。") + "。", 180)


def _clean_opportunity_text(value: Any) -> str:
    text = _clean_phrase(value, 64)
    if not text:
        return ""
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\b(workspace|product|opportunity|analysis|feasibility)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -_·:：")
    if not text or text.lower() in {"data", "product", "workspace"}:
        return ""
    return _clean_phrase(text, 44)


def _first_opportunity_title(artifact: dict[str, Any]) -> str:
    corpus = artifact.get("corpus") if isinstance(artifact.get("corpus"), dict) else {}
    for item in corpus.get("opportunities") or []:
        if isinstance(item, dict):
            title = _clean_phrase(item.get("title") or item.get("id"), 44)
            if title and not _low_information_title(title):
                return title
    return ""


def _low_information_title(value: Any) -> bool:
    text = _clean_phrase(value, 64)
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    if re.fullmatch(r"[\[\](),.;:，。;:、\s\d+-]+", compact):
        return True
    if re.fullmatch(r"row[-_ ]?\d+|chunk[-_ ]?\d+|profile|unknown", text, flags=re.I):
        return True
    return False


def _message_topic(value: Any) -> str:
    text = _clean_phrase(value, 180)
    if not text:
        return ""
    text = re.sub(r"(?i)\b(please|help|analyze|analysis|feasibility|report|generate|create)\b", " ", text)
    text = re.sub(r"(请|帮我|基于|当前|工作区|分析|生成|输出|报告|项目书|可行性|方案|一次|一下)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_·:：，。；;？！?")
    if len(text) > 42:
        text = text[:42].rstrip(" -_·:：，。；;")
    return text


def _first_clean_item(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    for item in value:
        text = _clean_phrase(item, 90)
        if text:
            return text
    return ""


def _evidence_hint(artifact: dict[str, Any]) -> str:
    citations = artifact.get("citations") or ((artifact.get("answer") or {}).get("citations") if isinstance(artifact.get("answer"), dict) else [])
    if isinstance(citations, list):
        for item in citations:
            if isinstance(item, dict):
                source = _clean_phrase(item.get("title") or item.get("source_file") or item.get("ref"), 54)
                if source:
                    return source
    corpus = artifact.get("corpus") if isinstance(artifact.get("corpus"), dict) else {}
    hits = corpus.get("hits") if isinstance(corpus, dict) else []
    if isinstance(hits, list) and hits:
        hit = hits[0] if isinstance(hits[0], dict) else {}
        return _clean_phrase(hit.get("title") or hit.get("source_file"), 54)
    return ""


def _clean_phrase(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = text.strip(" -_·:：，。；;？！?")
    return text[:limit].strip(" -_·:：，。；;") if limit else text


def _maf_summary(run: dict[str, Any]) -> dict[str, Any] | None:
    """Summarise the Microsoft Agent Framework workflow activity for run history."""
    graph: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    revisions = 0
    audit_rounds = 0
    fallback = False
    completed_agents: list[str] = []
    agent_work_ms = 0.0
    has_agent_work = False
    workflow_starts: list[int] = []
    workflow_completions: list[int] = []
    token_usage: dict[str, int] = {}
    persisted_maf: dict[str, Any] = {}
    artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
    if isinstance(artifact, dict) and isinstance(artifact.get("maf"), dict):
        persisted_maf = artifact["maf"]
    for step in run.get("steps") or []:
        event = step.get("event")
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        if event == "maf_workflow":
            graph = data
        elif event == "maf_plan":
            plan = data
        elif event == "maf_fallback":
            fallback = True
        elif event == "maf_agent_completed":
            agent_id = str(data.get("agent_id") or data.get("agent") or "").strip()
            if data.get("status") == "completed" and agent_id and agent_id not in completed_agents:
                completed_agents.append(agent_id)
            if isinstance(data.get("duration_ms"), (int, float)):
                agent_work_ms += max(0.0, float(data["duration_ms"]))
                has_agent_work = True
            started_ns = data.get("started_ns")
            completed_ns = data.get("completed_ns")
            if isinstance(started_ns, int) and isinstance(completed_ns, int) and completed_ns >= started_ns:
                workflow_starts.append(started_ns)
                workflow_completions.append(completed_ns)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            for target, source in (
                ("prompt", "input_tokens"),
                ("completion", "output_tokens"),
                ("total", "total_tokens"),
            ):
                value = data.get(source) if source in data else usage.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    token_usage[target] = token_usage.get(target, 0) + max(0, int(value))
        elif event == "maf_review":
            for code in data.get("reason_codes") or []:
                match = re.fullmatch(r"revision:(\d+)", str(code))
                if match:
                    revisions = max(revisions, int(match.group(1)))
        elif event == "audit":
            audit_rounds += 1
        elif event == "role_change" and data.get("orchestrator") == "maf" and data.get("agent") == "df-feasibility-analyst":
            revisions += 1
    if plan is not None or fallback:
        selected_agents = [str(item) for item in (plan or {}).get("selected_agents") or []]
        workflow_duration_ms = None
        if workflow_starts and workflow_completions:
            workflow_duration_ms = int(
                round((max(workflow_completions) - min(workflow_starts)) / 1_000_000)
            )
        return {
            "runtime": "maf",
            "mode": (plan or {}).get("mode"),
            "selected_agents": selected_agents,
            "completed_agents": completed_agents,
            "selection_reason_codes": [str(item) for item in (plan or {}).get("reason_codes") or []],
            "fallback": fallback,
            "rounds": revisions,
            "duration_ms": workflow_duration_ms,
            "agent_work_ms": int(round(agent_work_ms)) if has_agent_work else None,
            "tokens": token_usage or None,
            "execution_budget": persisted_maf.get("execution_budget") if isinstance(persisted_maf.get("execution_budget"), dict) else None,
            "evidence_bundle": persisted_maf.get("evidence_bundle") if isinstance(persisted_maf.get("evidence_bundle"), dict) else None,
        }
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
        # Rebuild from the full run every time. Persisted registry_summary values
        # can predate later steps, token usage, or artifact updates.
        summary = _run_summary(_normalize_run_detail(data)) if isinstance(data, dict) else None
        if isinstance(summary, dict):
            items.append(summary)
    return items


def _compact_step(
    event: str,
    data: Any,
    timestamp: str,
    expected_scope: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "time": timestamp,
        "event": event,
        "data": _truncate(sanitize_capability_metadata(_plain(data), expected_scope), depth=0),
    }


def _truncate(value: Any, *, depth: int) -> Any:
    if depth > 5:
        return "[truncated]"
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


def _token_usage(run: dict[str, Any]) -> dict[str, int] | None:
    total = {"total": 0, "prompt": 0, "completion": 0}
    model_sources = [item.get("usage") for item in run.get("models") or [] if isinstance(item, dict)]
    has_model_usage = any(_usage_is_observed(item) for item in model_sources)
    sources = list(model_sources)
    for step in run.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("event") == "model_response" and has_model_usage:
            continue
        data = step.get("data") if isinstance(step.get("data"), dict) else {}
        if data.get("usage"):
            sources.append(data.get("usage"))
    observed = False
    for usage in sources:
        if not _usage_is_observed(usage):
            continue
        observed = True
        item = _usage_from_dict(usage if isinstance(usage, dict) else {})
        total["total"] += item.get("total") or 0
        total["prompt"] += item.get("prompt") or 0
        total["completion"] += item.get("completion") or 0
    return total if observed else None


def _usage_is_observed(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    usage = data.get("usage") if "usage" in data else data
    if not isinstance(usage, dict):
        return False
    return any(
        key in usage and isinstance(usage.get(key), (int, float)) and not isinstance(usage.get(key), bool)
        for key in (
            "prompt_tokens",
            "input_tokens",
            "completion_tokens",
            "output_tokens",
            "total_tokens",
            "total",
        )
    )


def _usage_from_dict(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") if "usage" in data else data
    if not isinstance(usage, dict):
        return {"total": 0, "prompt": 0, "completion": 0}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or usage.get("total") or prompt + completion)
    return {"total": total, "prompt": prompt, "completion": completion}


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


def _duration_ms(start: Any, end: Any) -> int | None:
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    if not start_dt or not end_dt:
        return None
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
