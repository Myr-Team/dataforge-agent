from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .blob_store import (
        BlobJsonReadError,
        blob_configured,
        compare_and_swap_blob_json,
        delete_blob_name,
        download_blob_json,
        download_blob_json_strict,
        list_blob_json_named_strict,
        upload_blob_json,
    )
    from .context_pack import public_context_pack_metadata
    from .evidence_bundle import (
        sanitize_capability_metadata,
        sanitize_capability_pack_contract,
    )
    from .identity import is_trusted_identity, public_actor
    from .token_integrity import finite_nonnegative_integral_token_count
except ImportError:
    from blob_store import (
        BlobJsonReadError,
        blob_configured,
        compare_and_swap_blob_json,
        delete_blob_name,
        download_blob_json,
        download_blob_json_strict,
        list_blob_json_named_strict,
        upload_blob_json,
    )
    from context_pack import public_context_pack_metadata
    from evidence_bundle import (
        sanitize_capability_metadata,
        sanitize_capability_pack_contract,
    )
    from identity import is_trusted_identity, public_actor
    from token_integrity import finite_nonnegative_integral_token_count


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "generated-outputs" / "runs"
RUN_REGISTRY_BLOB = "registry/runs.json"
WORKSPACE_LINEAGE_BLOB_PREFIX = "registry/workspace-lineage"
RUN_BLOB_PREFIX = "runs"
LINEAGE_HISTORY_LIMIT = 512
ATTACHMENT_HISTORY_LIMIT = 1024
LINEAGE_PENDING_TIMEOUT_SECONDS = 300
WORKSPACE_GENERATION_INITIAL = 1
_ATTACHMENT_VERSION_KINDS = {"plan_draft", "artifact_generation"}
_TRACE_REFERENCE_ID = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_TRACE_REFERENCE_AGENT = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

_ACTIVE: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()
_LINEAGE_REPOSITORY_PROVIDER: Callable[[], Any] | None = None
_LINEAGE_GENERATION_HINTS: dict[str, int] = {}
_LINEAGE_ENVELOPE_KEYS = (
    "canonical_experiment_run_id",
    "canonical_experiment_version_id",
    "canonical_resolution_status",
    "canonical_lineage_status",
    "canonical_lineage_commit_id",
    "canonical_target_commit_id",
    "canonical_target_content_sha256",
    "canonical_lineage_content_sha256",
    "canonical_lineage_sequence",
)
_ATTACHMENT_ENVELOPE_KEYS = (
    "source_run_id",
    "experiment_version_id",
    "experiment_attachment",
    "attachment_commit_status",
    "attachment_commit_id",
    "attachment_payload_sha256",
)


@dataclass(frozen=True)
class _DurableJsonRead:
    status: str
    value: dict[str, Any] | None = None


def start_run(
    run_id: str,
    workspace_id: str,
    message: str,
    actor: dict[str, Any] | None = None,
    *,
    generation: int | None = None,
    trace_id: str | None = None,
    trace_agent_id: str | None = None,
    conversation_id: str | None = None,
    origin: str = "conversation",
) -> None:
    now = _utc_now()
    clean_actor = public_actor(actor or {})
    captured_generation = int(generation or 0)
    if captured_generation < WORKSPACE_GENERATION_INITIAL:
        try:
            captured_generation = _current_sql_generation(
                _resolve_lineage_repository(None), workspace_id
            )
        except Exception:
            captured_generation = 0
    with _LOCK:
        _ACTIVE[run_id] = {
            "run_id": run_id,
            "conversation_id": conversation_id if conversation_id is not None or origin != "conversation" else run_id,
            "origin": origin,
            "workspace_id": workspace_id,
            "message": message,
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "steps": [],
            "models": [],
            "answer_delta_summary": {"count": 0, "chars": 0},
            "workspace_generation": captured_generation,
        }
        if clean_actor:
            _ACTIVE[run_id]["actor"] = clean_actor
        _ACTIVE[run_id]["trusted_identity"] = is_trusted_identity(clean_actor)
        trace = _safe_trace_reference(trace_id, trace_agent_id)
        if trace:
            _ACTIVE[run_id]["trace"] = trace


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
            normalized_usage = _normalized_observed_usage(plain.get("usage"))
            route = plain.get("route") or plain.get("model_route")
            deployment = plain.get("deployment") or plain.get("model_deployment") or plain.get("model") or plain.get("model_name")
            model_record = {
                "agent": plain.get("agent"),
                "model": deployment,
                "route": route,
                "deployment": deployment,
                "selection": plain.get("selection"),
                "fallback_reason": plain.get("fallback_reason"),
                "execution_kind": plain.get("execution_kind"),
                "latency_ms": plain.get("latency_ms"),
                "model_route": route,
                "model_deployment": deployment,
                "response_id": plain.get("response_id"),
                "usage": normalized_usage,
                "mode": plain.get("mode"),
                "time": now,
            }
            for key in ("request_ref", "correlation_ref", "attempt_ref", "result_id"):
                reference = _safe_model_reference(plain.get(key))
                if reference:
                    model_record[key] = reference
            # Persist only opaque configured-provider references while retaining
            # the current route and gateway coverage evidence from mainline.
            for key in ("provider_type", "provider_id", "model_id", "route_evidence", "provenance"):
                value = _safe_model_observation_value(key, plain.get(key))
                if value is not None:
                    model_record[key] = value
            gateway_coverage = str(plain.get("gateway_coverage") or "").strip().lower()
            if gateway_coverage in {"apim_governed", "app_observed", "unmanaged", "unknown"}:
                model_record["gateway_coverage"] = gateway_coverage
            for key in ("policy_revision", "price_card_revision"):
                if key in plain:
                    model_record[key] = plain.get(key)
            if "cost_estimate" in plain:
                model_record["cost_estimate"] = _safe_cost_estimate(plain.get("cost_estimate"))
            for key in ("cache", "result_cache"):
                if key in plain:
                    cache = normalize_cache_meter(plain.get(key))
                    if cache:
                        model_record[key] = cache
            if "provider_cache" in plain:
                provider_cache = normalize_provider_cache_meter(plain.get("provider_cache"))
                if provider_cache:
                    model_record["provider_cache"] = provider_cache
            run.setdefault("models", []).append(model_record)
        if event == "audit" and isinstance(plain, dict):
            run["audit"] = plain
        if event == "final" and isinstance(plain, dict):
            run["final"] = _sanitize_final(plain, _capability_scope(run))


def record_context_pack(run_id: str | None, metadata: dict[str, Any]) -> None:
    if not run_id:
        return
    now = _utc_now()
    with _LOCK:
        run = _ACTIVE.get(run_id)
        if not run:
            return
        safe = _sanitize_context_pack_metadata(metadata)
        if not safe:
            return
        run["updated_at"] = now
        run["context_pack"] = safe
        run.setdefault("steps", []).append(
            _compact_step("context_pack", safe, now, _capability_scope(run))
        )


def complete_run(
    run_id: str,
    *,
    status: str = "completed",
    final: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
    lineage_repository: Any | None = None,
) -> dict[str, Any] | None:
    persisted: dict[str, Any] | None = None
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
                lineage_repository=lineage_repository,
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
                try:
                    canonical_source = get_run(canonical_source_id)
                except (FileNotFoundError, ValueError, KeyError):
                    canonical_source = {}
                canonical_version_id = str(
                    canonical_source.get("canonical_experiment_version_id") or ""
                ).strip()
                if canonical_version_id:
                    run["experiment_version_id"] = canonical_version_id
        run["status"] = status
        run["completed_at"] = _utc_now()
        run["updated_at"] = run["completed_at"]
        run["duration_ms"] = _duration_ms(run.get("started_at"), run.get("completed_at"))
        run["verdict"] = _verdict(run)
        run["confidence"] = _confidence(run)
        run["step_count"] = len(run.get("steps") or [])
        run["title"] = _run_title(run)
        run["summary"] = _run_summary_text(run)
        run["registry_summary"] = _run_summary(run)
        persisted = _persist_run(
            run,
            lineage_repository=lineage_repository,
            analysis_completed_event=True,
        )
    if persisted is not None:
        try:
            try:
                from .finops.ingestion import ingest_completed_run
            except ImportError:
                from finops.ingestion import ingest_completed_run

            ingest_completed_run(persisted)
        except Exception:
            # FinOps is additive and must never make the analysis ledger fail.
            pass
    return persisted


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
    lineage_repository: Any | None = None,
) -> dict[str, Any] | None:
    """Persist a lightweight version snapshot when artifacts are generated from a real analysis.

    This does not create a new feasibility judgement or experiment version. It
    records a deliverable attachment against an existing analysis decision.
    """
    workspace_id = str(workspace_id or "").strip()
    source_run_id = str(source_run_id or "").strip()
    if not workspace_id or not source_run_id or not isinstance(artifact, dict):
        return None
    try:
        repository = _resolve_lineage_repository(lineage_repository)
        committed_version = _sql_version_for_source(
            workspace_id,
            source_run_id,
            experiment_version_id,
            repository,
        )
    except Exception:
        return None
    if committed_version is None:
        return None
    experiment_version_id = str(_commit_value(committed_version, "version_id") or "")
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    if not (feasibility.get("verdict") or feasibility.get("dimensions")):
        return None

    now = _utc_now()
    source_actor: dict[str, Any] = {}
    source_run: dict[str, Any] = {}
    try:
        source_run = get_run(source_run_id)
        source_actor = public_actor(source_run.get("actor") or {})
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
        "workspace_generation": int(_commit_value(committed_version, "generation") or 0),
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
        "experiment_attachment": False,
        "attachment_commit_status": "candidate",
        "produced_kinds": produced_kinds,
    }
    run["verdict"] = _verdict(run)
    run["confidence"] = _confidence(run)
    base_title = _run_title(run)
    run["title"] = _clean_phrase(f"{base_title} · 产物版", 44)
    run["summary"] = _run_summary_text(run)
    run["registry_summary"] = _run_summary(run)
    persisted = _persist_run(run, lineage_repository=repository)
    return persisted if _snapshot_persistence_confirmed(persisted) else None


def record_plan_version(
    *,
    workspace_id: str,
    source_run_id: str,
    experiment_version_id: str | None = None,
    artifact: dict[str, Any],
    text: str,
    lineage_repository: Any | None = None,
) -> dict[str, Any] | None:
    """Persist a lightweight version snapshot when a follow-up creates a plan draft."""
    workspace_id = str(workspace_id or "").strip()
    source_run_id = str(source_run_id or "").strip()
    if not workspace_id or not source_run_id or not isinstance(artifact, dict):
        return None
    try:
        repository = _resolve_lineage_repository(lineage_repository)
        committed_version = _sql_version_for_source(
            workspace_id,
            source_run_id,
            experiment_version_id,
            repository,
        )
    except Exception:
        return None
    if committed_version is None:
        return None
    experiment_version_id = str(_commit_value(committed_version, "version_id") or "")
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
    if not (feasibility.get("verdict") or feasibility.get("dimensions")):
        return None

    now = _utc_now()
    source_actor: dict[str, Any] = {}
    source_run: dict[str, Any] = {}
    try:
        source_run = get_run(source_run_id)
        source_actor = public_actor(source_run.get("actor") or {})
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
        "workspace_generation": int(_commit_value(committed_version, "generation") or 0),
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
        "experiment_attachment": False,
        "attachment_commit_status": "candidate",
        "produced_kinds": ["plan_draft"],
    }
    run["verdict"] = _verdict(run)
    run["confidence"] = _confidence(run)
    run["title"] = _clean_phrase(f"{title_base} · 方案版", 44)
    run["summary"] = _run_summary_text(run)
    run["registry_summary"] = _run_summary(run)
    persisted = _persist_run(run, lineage_repository=repository)
    return persisted if _snapshot_persistence_confirmed(persisted) else None


def _canonical_experiment_version_exists(
    workspace_id: str,
    experiment_version_id: str,
    *,
    generation: int | None = None,
    lineage_repository: Any | None = None,
) -> bool:
    workspace_id = str(workspace_id or "").strip()
    version_id = str(experiment_version_id or "").strip()
    if not workspace_id or not version_id:
        return False
    try:
        repository = _resolve_lineage_repository(lineage_repository)
        active_generation = _current_sql_generation(repository, workspace_id)
        if generation is not None and int(generation) != active_generation:
            return False
        return any(
            str(_commit_value(item, "version_id") or "") == version_id
            for item in repository.list_versions(
                workspace_id=workspace_id,
                generation=active_generation,
            )
        )
    except Exception:
        return False


def resolve_canonical_experiment_source_run_id(
    workspace_id: str,
    source_run_id: str,
    *,
    lineage_repository: Any | None = None,
) -> str | None:
    workspace_id = str(workspace_id or "").strip()
    source_run_id = str(source_run_id or "").strip()
    if not workspace_id or not source_run_id:
        return None
    try:
        source = get_run(source_run_id)
    except (FileNotFoundError, ValueError):
        source = {}
    if source and str(source.get("workspace_id") or "") != workspace_id:
        return None
    try:
        repository = _resolve_lineage_repository(lineage_repository)
        generation = _current_sql_generation(repository, workspace_id)
        versions = repository.list_versions(workspace_id=workspace_id, generation=generation)
    except Exception:
        return None
    declared_target = str(source.get("canonical_experiment_run_id") or source_run_id).strip()
    declared_version = str(source.get("canonical_experiment_version_id") or "").strip()
    for version in versions:
        canonical_run_id = str(_commit_value(version, "canonical_run_id") or "")
        version_id = str(_commit_value(version, "version_id") or "")
        if canonical_run_id == source_run_id:
            return source_run_id
        if (
            declared_target == canonical_run_id
            and declared_version
            and declared_version == version_id
        ):
            return canonical_run_id
    return None


def _sql_version_for_source(
    workspace_id: str,
    source_run_id: str,
    requested_version_id: str | None,
    repository: Any,
) -> Any | None:
    try:
        source = get_run(source_run_id)
    except (FileNotFoundError, ValueError):
        source = {}
    if source and str(source.get("workspace_id") or "") != workspace_id:
        return None
    target_run_id = str(source.get("canonical_experiment_run_id") or source_run_id).strip()
    declared_version_id = str(
        requested_version_id or source.get("canonical_experiment_version_id") or ""
    ).strip()
    try:
        generation = _current_sql_generation(repository, workspace_id)
        versions = repository.list_versions(workspace_id=workspace_id, generation=generation)
    except Exception:
        return None
    for version in versions:
        if str(_commit_value(version, "canonical_run_id") or "") != target_run_id:
            continue
        version_id = str(_commit_value(version, "version_id") or "")
        if declared_version_id and declared_version_id != version_id:
            continue
        return version
    return None


def _resolve_lineage_repository(repository: Any | None) -> Any:
    if repository is not None:
        return repository
    if _LINEAGE_REPOSITORY_PROVIDER is not None:
        return _LINEAGE_REPOSITORY_PROVIDER()
    try:
        from .app import get_lineage_repository
    except ImportError:
        from app import get_lineage_repository
    return get_lineage_repository()


def _current_sql_generation(repository: Any, workspace_id: str) -> int:
    generation = int(repository.current_generation(workspace_id=str(workspace_id or "")))
    if generation < WORKSPACE_GENERATION_INITIAL:
        raise RuntimeError("lineage generation is invalid")
    return generation


def _require_current_sql_generation(repository: Any, run: dict[str, Any]) -> int:
    workspace_id = str(run.get("workspace_id") or "")
    captured_generation = int(run.get("workspace_generation") or 0)
    current_generation = _current_sql_generation(repository, workspace_id)
    if captured_generation != current_generation:
        raise RuntimeError("lineage generation is not current")
    return current_generation


def _commit_value(commit: Any, name: str) -> Any:
    return commit.get(name) if isinstance(commit, dict) else getattr(commit, name, None)


def _persisted_canonical_target(workspace_id: str, source: dict[str, Any]) -> str | None:
    return trusted_canonical_experiment_run_id(workspace_id, source)


def trusted_canonical_experiment_run_id(
    workspace_id: str,
    source: dict[str, Any],
    runs_by_id: dict[str, dict[str, Any]] | None = None,
    registry_state: dict[str, Any] | None = None,
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
    registry = registry_state if isinstance(registry_state, dict) else authoritative_run_registry(workspace_id)
    if registry.get("read_status") == "error":
        return None
    if blob_configured():
        persisted_source = _load_authoritative_run(source_id)
        if not isinstance(persisted_source, dict) or not _lineage_record_matches(
            _lineage_record(source), persisted_source
        ):
            return None
        source = persisted_source
    source_summary = _registry_entry(registry, source_id)
    lineage_state = workspace_lineage_state(workspace_id)
    lineage_status = str(lineage_state.get("status") or "")
    if lineage_status in {"stable", "pending"}:
        source_confirmed = _lineage_record_matches(_state_lineage_record(lineage_state, source_id), source)
        if lineage_status == "stable":
            source_confirmed = source_confirmed or _registry_envelope_matches_run(
                source_summary, source, _LINEAGE_ENVELOPE_KEYS
            )
    elif lineage_state:
        source_confirmed = False
    else:
        source_confirmed = _registry_envelope_matches_run(source_summary, source, _LINEAGE_ENVELOPE_KEYS)
    if not source_confirmed:
        return None
    if target_id == source_id:
        return source_id if (
            source.get("canonical_target_commit_id") == source.get("canonical_lineage_commit_id")
            and source.get("canonical_target_content_sha256") == source.get("canonical_lineage_content_sha256")
        ) else None
    target = (runs_by_id or {}).get(target_id)
    if blob_configured():
        target = _load_authoritative_run(target_id)
    elif target is None:
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
        or str(source.get("canonical_target_commit_id") or "")
        != str(target.get("canonical_lineage_commit_id") or "")
        or str(source.get("canonical_target_content_sha256") or "")
        != str(target.get("canonical_lineage_content_sha256") or "")
    ):
        return None
    target_summary = _registry_entry(registry, target_id)
    if lineage_status in {"stable", "pending"}:
        target_confirmed = _lineage_record_matches(_state_lineage_record(lineage_state, target_id), target)
        if lineage_status == "stable":
            target_confirmed = target_confirmed or _registry_envelope_matches_run(
                target_summary, target, _LINEAGE_ENVELOPE_KEYS
            )
    elif lineage_state:
        target_confirmed = False
    else:
        target_confirmed = _registry_envelope_matches_run(target_summary, target, _LINEAGE_ENVELOPE_KEYS)
    if not target_confirmed:
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
    confirmed_snapshots: set[str] = set()
    canonical_ordinals: dict[str, int] = {}
    registry = authoritative_run_registry(workspace_id)
    lineage_state = workspace_lineage_state(workspace_id)

    def use_authoritative_payload(run_id: str, payload: dict[str, Any]) -> None:
        authoritative = copy.deepcopy(payload)
        if run_id in canonical_ordinals:
            authoritative["_canonical_ordinal"] = canonical_ordinals[run_id]
        hydrated[:] = [
            item
            for item in hydrated
            if str(item.get("run_id") or "").strip() != run_id
        ]
        hydrated.append(authoritative)
        by_id[run_id] = authoritative

    if registry.get("read_status") == "error" or lineage_state.get("read_status") == "error":
        unresolved.update(
            str(item.get("run_id") or "")
            for item in hydrated
            if str(item.get("run_id") or "")
        )
        if not unresolved:
            unresolved.add("lineage-storage-unavailable")
        return [
            item
            for item in hydrated
            if str(item.get("version_kind") or "") not in _ATTACHMENT_VERSION_KINDS
        ], sorted(unresolved)[:20]

    legacy_trusted_ids = {
        str(item.get("run_id") or "").strip()
        for item in hydrated
        if _is_completed_analysis(item) and _has_canonical_lineage_metadata(item)
    }
    if not lineage_state and legacy_trusted_ids:
        has_trusted_registry_history = any(
            str(item.get("run_id") or "") in legacy_trusted_ids
            and str(item.get("canonical_lineage_status") or "") == "trusted"
            for item in registry.get("runs") or []
            if isinstance(item, dict)
        )
        if has_trusted_registry_history:
            lineage_state = _bootstrap_workspace_lineage(workspace_id, registry)
        if str(lineage_state.get("status") or "") not in {"stable", "pending"}:
            unresolved.update(legacy_trusted_ids)
            hydrated[:] = [
                item
                for item in hydrated
                if str(item.get("run_id") or "").strip() not in legacy_trusted_ids
                and str(item.get("source_run_id") or "").strip() not in legacy_trusted_ids
            ]
            return hydrated, sorted(unresolved)[:20]

    if str(lineage_state.get("status") or "") in {"stable", "pending"}:
        records: list[dict[str, Any]] = []
        for record in [
            *(lineage_state.get("analysis_history") or []),
            *(lineage_state.get("canonical_history") or []),
            lineage_state.get("latest_source_envelope"),
            lineage_state.get("canonical_target_envelope"),
        ]:
            if isinstance(record, dict):
                records.append(record)
        for record in records:
            run_id = str(record.get("run_id") or "") if isinstance(record, dict) else ""
            if not run_id:
                continue
            target = _load_authoritative_run(run_id)
            if isinstance(target, dict) and _lineage_record_matches(record, target):
                canonical_ordinal = int(record.get("ordinal") or 0)
                if canonical_ordinal:
                    canonical_ordinals[run_id] = canonical_ordinal
                use_authoritative_payload(run_id, target)
            else:
                unresolved.add(run_id)
        for record in lineage_state.get("attachment_history") or []:
            snapshot_id = str(record.get("run_id") or "") if isinstance(record, dict) else ""
            if not snapshot_id:
                continue
            snapshot = _load_authoritative_run(snapshot_id)
            if isinstance(snapshot, dict) and _attachment_record_matches(record, snapshot):
                use_authoritative_payload(snapshot_id, snapshot)
            else:
                unresolved.add(snapshot_id)
        if lineage_state.get("lineage_history_complete") is False:
            unresolved.add("lineage-history-truncated")
        if lineage_state.get("attachment_history_complete") is False:
            unresolved.add("attachment-history-truncated")

    for source in list(hydrated):
        source_id = str(source.get("run_id") or "").strip()
        if _is_completed_analysis(source) and _has_canonical_lineage_metadata(source):
            target_id = str(source.get("canonical_experiment_run_id") or "").strip()
            if target_id and target_id not in by_id and _lineage_envelope_matches(source, target_id):
                target = _load_authoritative_run(target_id)
                if isinstance(target, dict):
                    use_authoritative_payload(target_id, target)
            if trusted_canonical_experiment_run_id(workspace_id, source, by_id, registry) is None:
                unresolved.add(source_id)

    for snapshot in list(hydrated):
        if str(snapshot.get("version_kind") or "") not in _ATTACHMENT_VERSION_KINDS:
            continue
        snapshot_id = str(snapshot.get("run_id") or "").strip()
        source_id = str(snapshot.get("source_run_id") or "").strip()
        version_id = str(snapshot.get("experiment_version_id") or "").strip()
        if not source_id or version_id != f"version:{source_id}" or snapshot.get("experiment_attachment") is not True:
            unresolved.add(snapshot_id)
            continue
        if source_id not in by_id:
            target = _load_authoritative_run(source_id)
            if isinstance(target, dict):
                use_authoritative_payload(source_id, target)
        target = by_id.get(source_id)
        if (
            not isinstance(target, dict)
            or trusted_canonical_experiment_run_id(workspace_id, target, by_id, registry) != source_id
            or not _snapshot_registry_confirmed(snapshot, registry, target, lineage_state)
        ):
            unresolved.add(snapshot_id)
            continue
        confirmed_snapshots.add(snapshot_id)

    visible = [
        item
        for item in hydrated
        if str(item.get("version_kind") or "") not in _ATTACHMENT_VERSION_KINDS
        or str(item.get("run_id") or "") in confirmed_snapshots
    ]
    return visible, sorted(item for item in unresolved if item)[:20]


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
        and str(run.get("canonical_lineage_commit_id") or "").strip()
        and str(run.get("canonical_target_commit_id") or "").strip()
        and str(run.get("canonical_target_content_sha256") or "").strip()
        and str(run.get("canonical_lineage_content_sha256") or "").strip() == _analysis_content_hash(run)
        and int(run.get("canonical_lineage_sequence") or 0) > 0
    )


def _mark_canonical_lineage_trusted(
    run: dict[str, Any],
    canonical_run_id: str,
    *,
    sequence: int,
    target_commit_id: str | None = None,
    target_content_sha256: str | None = None,
) -> None:
    content_hash = _analysis_content_hash(run)
    commit_id = _canonical_lineage_commit_id(run, canonical_run_id, sequence, content_hash)
    run["canonical_experiment_run_id"] = canonical_run_id
    run["canonical_experiment_version_id"] = f"version:{canonical_run_id}"
    run["canonical_resolution_status"] = "resolved"
    run["canonical_lineage_status"] = "trusted"
    run["canonical_lineage_commit_id"] = commit_id
    run["canonical_target_commit_id"] = target_commit_id or commit_id
    run["canonical_target_content_sha256"] = target_content_sha256 or content_hash
    run["canonical_lineage_content_sha256"] = content_hash
    run["canonical_lineage_sequence"] = sequence


def _canonical_lineage_commit_id(
    run: dict[str, Any],
    canonical_run_id: str,
    sequence: int,
    content_hash: str | None = None,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "workspace_id": str(run.get("workspace_id") or ""),
                "run_id": str(run.get("run_id") or ""),
                "canonical_run_id": canonical_run_id,
                "content_sha256": content_hash or _analysis_content_hash(run),
                "sequence": sequence,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _mark_canonical_lineage_unresolved(run: dict[str, Any], *, candidate: bool = False) -> None:
    for key in _LINEAGE_ENVELOPE_KEYS:
        run.pop(key, None)
    run["canonical_resolution_status"] = "unresolved"
    run["canonical_lineage_status"] = "candidate" if candidate else "unresolved"
    run["canonical_lineage_content_sha256"] = _analysis_content_hash(run)


def _analysis_content_hash(run: dict[str, Any]) -> str:
    artifact = run.get("artifact") or (run.get("final") or {}).get("artifact") or {}
    feasibility = artifact.get("feasibility") if isinstance(artifact, dict) else {}
    iteration_inputs = artifact.get("iteration_inputs") if isinstance(artifact, dict) else []
    payload = {
        "workspace_id": str(run.get("workspace_id") or ""),
        "feasibility": feasibility if isinstance(feasibility, dict) else {},
        "iteration_inputs": iteration_inputs if isinstance(iteration_inputs, list) else [],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _durable_json_read(blob_name: str, local_path: Path) -> _DurableJsonRead:
    if blob_configured():
        try:
            value = download_blob_json_strict(blob_name)
        except BlobJsonReadError:
            return _DurableJsonRead("error")
        return _DurableJsonRead("present", value) if isinstance(value, dict) else _DurableJsonRead("missing")
    if not local_path.exists():
        return _DurableJsonRead("missing")
    try:
        value = json.loads(local_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _DurableJsonRead("error")
    return _DurableJsonRead("present", value) if isinstance(value, dict) else _DurableJsonRead("error")


def authoritative_run_registry(workspace_id: str | None = None) -> dict[str, Any]:
    result = _durable_json_read(RUN_REGISTRY_BLOB, _local_registry_path())
    if result.status == "error":
        return {
            "version": 2,
            "revision": 0,
            "history_truncated": True,
            "read_status": "error",
            "runs": [],
        }
    registry = result.value or {}
    entries = [item for item in registry.get("runs") or [] if isinstance(item, dict)]
    if workspace_id:
        entries = [item for item in entries if str(item.get("workspace_id") or "") == str(workspace_id)]
    return {
        "version": int(registry.get("version") or 2),
        "revision": int(registry.get("revision") or 0),
        "history_truncated": registry.get("history_truncated") is True,
        "read_status": result.status,
        "runs": entries,
    }


def _local_registry_path() -> Path:
    return RUN_DIR / "_registry" / "runs.json"


def _write_local_registry(registry: dict[str, Any]) -> None:
    path = _local_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _workspace_lineage_blob(workspace_id: str) -> str:
    return f"{WORKSPACE_LINEAGE_BLOB_PREFIX}/{_safe_name(workspace_id)}.json"


def _workspace_lineage_path(workspace_id: str) -> Path:
    return RUN_DIR / "_registry" / "workspace-lineage" / f"{_safe_name(workspace_id)}.json"


def workspace_lineage_state(workspace_id: str) -> dict[str, Any]:
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        return {}
    result = _durable_json_read(
        _workspace_lineage_blob(workspace_id),
        _workspace_lineage_path(workspace_id),
    )
    if result.status == "missing":
        return {}
    if result.status == "error":
        return {"workspace_id": workspace_id, "status": "unavailable", "read_status": "error"}
    value = result.value or {}
    if not isinstance(value, dict) or str(value.get("workspace_id") or "") != workspace_id:
        return {"workspace_id": workspace_id, "status": "unavailable", "read_status": "error"}
    return {**copy.deepcopy(value), "read_status": "present"}


def _workspace_generation(state: dict[str, Any]) -> int:
    return max(WORKSPACE_GENERATION_INITIAL, int(state.get("generation") or WORKSPACE_GENERATION_INITIAL))


def _write_local_workspace_lineage(state: dict[str, Any]) -> None:
    path = _workspace_lineage_path(str(state.get("workspace_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _cas_workspace_lineage(
    current: dict[str, Any],
    changes: dict[str, Any],
) -> dict[str, Any] | None:
    expected_revision = int(current.get("revision") or 0)
    if current.get("read_status") == "error":
        return None
    current = {key: value for key, value in current.items() if key != "read_status"}
    value = {**current, **copy.deepcopy(changes), "revision": expected_revision + 1}
    workspace_id = str(value.get("workspace_id") or "")
    if not workspace_id:
        return None
    if not blob_configured():
        latest = workspace_lineage_state(workspace_id)
        if latest.get("read_status") == "error":
            return None
        if int(latest.get("revision") or 0) != expected_revision:
            return None
        _write_local_workspace_lineage(value)
        return value
    return compare_and_swap_blob_json(
        _workspace_lineage_blob(workspace_id),
        expected_revision=expected_revision,
        changes=value,
    )


def initialize_workspace_lineage(
    workspace_id: str,
    *,
    no_prior_analysis: bool = False,
) -> dict[str, Any]:
    """Persist an explicit first-analysis proof for a newly known workspace."""
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id or not no_prior_analysis:
        return {}
    if authoritative_run_registry().get("read_status") == "error":
        return {}
    for _attempt in range(8 if blob_configured() else 1):
        current = workspace_lineage_state(workspace_id)
        if current.get("read_status") == "error":
            return {}
        if current:
            return current
        created = _cas_workspace_lineage(
            {"workspace_id": workspace_id, "revision": 0},
            {
                "version": 2,
                "workspace_id": workspace_id,
                "status": "stable",
                "generation": WORKSPACE_GENERATION_INITIAL,
                "genesis_proof": "no_prior_analysis",
                "analysis_count": 0,
                "canonical_count": 0,
                "latest_run_id": None,
                "latest_canonical_run_id": None,
                "analysis_history": [],
                "canonical_history": [],
                "attachment_history": [],
                "lineage_history_complete": True,
                "attachment_history_complete": True,
            },
        )
        if created:
            return created
    return {}


def recreate_workspace_generation(
    workspace_id: str,
    *,
    lineage_repository: Any | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    """Start a new generation only after the SQL lifecycle accepts it."""
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        return {"workspace_id": workspace_id, "status": "unavailable"}
    current_generation: int | None = None
    try:
        repository = _resolve_lineage_repository(lineage_repository)
        current_generation = _current_sql_generation(repository, workspace_id)
        if generation is not None and int(generation) != current_generation:
            raise RuntimeError("lineage generation is not current")
        next_generation = int(
            repository.recreate_workspace(
                workspace_id=workspace_id,
                generation=current_generation,
                actor_metadata=None,
            )
        )
    except Exception:
        unavailable = {
            "workspace_id": workspace_id,
            "status": "unavailable",
            "reason": "lineage_unavailable",
        }
        if current_generation is not None:
            unavailable["generation"] = current_generation
        return unavailable
    _LINEAGE_GENERATION_HINTS[workspace_id] = next_generation
    return {
        "version": 3,
        "workspace_id": workspace_id,
        "status": "stable",
        "generation": next_generation,
        "source": "sql_lineage",
    }


def _legacy_recreate_workspace_generation(workspace_id: str) -> dict[str, Any]:
    """Explicitly start a clean generation after a fully confirmed purge."""
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        return {}
    for _attempt in range(8 if blob_configured() else 1):
        current = workspace_lineage_state(workspace_id)
        registry = authoritative_run_registry()
        if (
            current.get("read_status") == "error"
            or registry.get("read_status") == "error"
            or str(current.get("status") or "") != "purged"
            or any(
                str(item.get("workspace_id") or "") == workspace_id
                for item in registry.get("runs") or []
                if isinstance(item, dict)
            )
            or not _workspace_publications_absent(workspace_id)
        ):
            return {}
        recreated = _cas_workspace_lineage(
            current,
            {
                "version": 2,
                "workspace_id": workspace_id,
                "status": "stable",
                "generation": _workspace_generation(current) + 1,
                "genesis_proof": "workspace_recreated",
                "recreated_at": _utc_now(),
                "analysis_count": 0,
                "canonical_count": 0,
                "pending_run_id": None,
                "pending_started_at": None,
                "pending_candidate_content_sha256": None,
                "latest_run_id": None,
                "latest_canonical_run_id": None,
                "latest_source_envelope": None,
                "canonical_target_envelope": None,
                "analysis_history": [],
                "canonical_history": [],
                "attachment_history": [],
                "lineage_history_complete": True,
                "attachment_history_complete": True,
                "purge_integrity_failure": None,
                "late_writer_cleanup_errors": None,
            },
        )
        if recreated:
            return recreated
    return {}


def _lineage_record(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(run.get("run_id") or ""),
        **{key: run.get(key) for key in _LINEAGE_ENVELOPE_KEYS},
    }


def _lineage_record_matches(record: Any, run: dict[str, Any]) -> bool:
    return bool(
        isinstance(record, dict)
        and str(record.get("run_id") or "") == str(run.get("run_id") or "")
        and all(record.get(key) == run.get(key) for key in _LINEAGE_ENVELOPE_KEYS)
    )


def _state_lineage_record(state: dict[str, Any], run_id: str) -> dict[str, Any]:
    candidates: list[Any] = [
        state.get("latest_source_envelope"),
        state.get("canonical_target_envelope"),
        *(state.get("analysis_history") or []),
        *(state.get("canonical_history") or []),
    ]
    return next(
        (
            item
            for item in candidates
            if isinstance(item, dict) and str(item.get("run_id") or "") == str(run_id or "")
        ),
        {},
    )


def _attachment_record(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(run.get("run_id") or ""),
        **{key: run.get(key) for key in _ATTACHMENT_ENVELOPE_KEYS},
    }


def _attachment_record_matches(record: Any, run: dict[str, Any]) -> bool:
    return bool(
        isinstance(record, dict)
        and str(record.get("run_id") or "") == str(run.get("run_id") or "")
        and all(record.get(key) == run.get(key) for key in _ATTACHMENT_ENVELOPE_KEYS)
    )


def _bounded_history(
    items: list[dict[str, Any]],
    item: dict[str, Any],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    run_id = str(item.get("run_id") or "")
    updated = [entry for entry in items if str(entry.get("run_id") or "") != run_id]
    updated.append(copy.deepcopy(item))
    complete = len(updated) <= limit
    return updated[-limit:], complete


def _load_authoritative_run_read(run_id: str) -> _DurableJsonRead:
    safe = _safe_name(run_id)
    result = _durable_json_read(
        f"{RUN_BLOB_PREFIX}/{safe}.json",
        RUN_DIR / f"{safe}.json",
    )
    if result.status != "present" or not isinstance(result.value, dict):
        return result
    return _DurableJsonRead("present", _normalize_run_detail(result.value))


def _load_authoritative_run(run_id: str) -> dict[str, Any] | None:
    result = _load_authoritative_run_read(run_id)
    return result.value if result.status == "present" else None


def _registry_entry(registry: dict[str, Any], run_id: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in registry.get("runs") or []
            if isinstance(item, dict) and str(item.get("run_id") or "") == str(run_id or "")
        ),
        {},
    )


def _registry_envelope_matches_run(
    summary: dict[str, Any],
    run: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    return bool(summary) and all(summary.get(key) == run.get(key) for key in keys)


def _attachment_commit_id(run: dict[str, Any], source: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "workspace_id": str(run.get("workspace_id") or ""),
                "run_id": str(run.get("run_id") or ""),
                "source_run_id": str(run.get("source_run_id") or ""),
                "experiment_version_id": str(run.get("experiment_version_id") or ""),
                "source_commit_id": str(source.get("canonical_lineage_commit_id") or ""),
                "payload_sha256": str(run.get("attachment_payload_sha256") or ""),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _attachment_payload_hash(run: dict[str, Any]) -> str:
    artifact = run.get("artifact") if isinstance(run.get("artifact"), dict) else {}
    payload = {
        "workspace_id": str(run.get("workspace_id") or ""),
        "run_id": str(run.get("run_id") or ""),
        "source_run_id": str(run.get("source_run_id") or ""),
        "experiment_version_id": str(run.get("experiment_version_id") or ""),
        "version_kind": str(run.get("version_kind") or ""),
        "produced_kinds": run.get("produced_kinds") if isinstance(run.get("produced_kinds"), list) else [],
        "artifact": artifact,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _snapshot_registry_confirmed(
    snapshot: dict[str, Any],
    registry: dict[str, Any],
    source: dict[str, Any],
    lineage_state: dict[str, Any] | None = None,
) -> bool:
    snapshot_id = str(snapshot.get("run_id") or "")
    summary = _registry_entry(registry, snapshot_id)
    state = lineage_state if isinstance(lineage_state, dict) else workspace_lineage_state(
        str(snapshot.get("workspace_id") or "")
    )
    state_status = str(state.get("status") or "")
    if state_status in {"stable", "pending"}:
        record = next(
            (
                item
                for item in state.get("attachment_history") or []
                if isinstance(item, dict) and str(item.get("run_id") or "") == snapshot_id
            ),
            {},
        )
        persistence_confirmed = _attachment_record_matches(record, snapshot)
    elif state:
        persistence_confirmed = False
    else:
        persistence_confirmed = _registry_envelope_matches_run(
            summary, snapshot, _ATTACHMENT_ENVELOPE_KEYS
        )
    return bool(
        snapshot.get("experiment_attachment") is True
        and str(snapshot.get("attachment_commit_status") or "") == "confirmed"
        and str(snapshot.get("attachment_payload_sha256") or "") == _attachment_payload_hash(snapshot)
        and str(snapshot.get("attachment_commit_id") or "") == _attachment_commit_id(snapshot, source)
        and persistence_confirmed
    )


def _snapshot_persistence_confirmed(run: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(run, dict)
        and run.get("experiment_attachment") is True
        and run.get("attachment_commit_status") == "confirmed"
        and (run.get("persistence") or {}).get("confirmed") is True
    )


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
    *,
    registry_state: dict[str, Any] | None = None,
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
        registry_state=registry_state,
    )


def _registry_history_is_truncated() -> bool:
    try:
        registry = authoritative_run_registry()
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


def purge_workspace_runs(
    workspace_id: str,
    *,
    lineage_repository: Any | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    """Commit SQL purge first, then best-effort payload cleanup."""
    workspace_id = str(workspace_id or "").strip()
    try:
        repository = _resolve_lineage_repository(lineage_repository)
        active_generation = _current_sql_generation(repository, workspace_id)
        if generation is not None and int(generation) != active_generation:
            raise RuntimeError("lineage generation is not current")
        repository.purge_workspace(
            workspace_id=workspace_id,
            generation=active_generation,
            actor_metadata=None,
        )
    except Exception:
        return {
            "workspace_id": workspace_id,
            "run_ids": [],
            "deleted_local_runs": 0,
            "deleted_blob_runs": 0,
            "registry_updated": False,
            "lineage_updated": False,
            "status": "unavailable",
            "reason": "lineage_unavailable",
        }

    _LINEAGE_GENERATION_HINTS[workspace_id] = active_generation
    payload_failed = False
    run_ids: set[str] = set()
    try:
        run_ids.update(
            str(item.get("run_id") or "")
            for item in list_runs(workspace_id)
            if isinstance(item, dict) and item.get("run_id")
        )
    except Exception:
        payload_failed = True
    if blob_configured():
        try:
            run_ids.update(
                str(value.get("run_id") or "")
                for _name, value in list_blob_json_named_strict(f"{RUN_BLOB_PREFIX}/")
                if isinstance(value, dict)
                and str(value.get("workspace_id") or "") == workspace_id
                and value.get("run_id")
            )
        except Exception:
            payload_failed = True
    run_ids.discard("")

    deleted_local = 0
    deleted_blob = 0
    for run_id in sorted(run_ids):
        path = RUN_DIR / f"{_safe_name(run_id)}.json"
        try:
            if path.exists():
                path.unlink()
                deleted_local += 1
        except Exception:
            payload_failed = True
        if blob_configured():
            try:
                if delete_blob_name(f"{RUN_BLOB_PREFIX}/{_safe_name(run_id)}.json"):
                    deleted_blob += 1
            except Exception:
                payload_failed = True

    registry_updated = False
    try:
        registry = authoritative_run_registry()
        if registry.get("read_status") != "error":
            entries = [
                item
                for item in registry.get("runs") or []
                if isinstance(item, dict) and str(item.get("workspace_id") or "") != workspace_id
            ]
            expected_revision = int(registry.get("revision") or 0)
            value = {
                "version": int(registry.get("version") or 2),
                "revision": expected_revision + 1,
                "history_truncated": registry.get("history_truncated") is True,
                "runs": entries,
            }
            if blob_configured():
                registry_updated = compare_and_swap_blob_json(
                    RUN_REGISTRY_BLOB,
                    expected_revision=expected_revision,
                    changes=value,
                ) is not None
            else:
                _write_local_registry(value)
                registry_updated = True
        if not registry_updated:
            payload_failed = True
    except Exception:
        payload_failed = True
    try:
        set_flagship_plan(workspace_id, None)
    except Exception:
        payload_failed = True
    return {
        "workspace_id": workspace_id,
        "generation": active_generation,
        "run_ids": sorted(run_ids),
        "deleted_local_runs": deleted_local,
        "deleted_blob_runs": deleted_blob,
        "registry_updated": registry_updated,
        "lineage_updated": True,
        "status": "purged",
        "payload_state": "unavailable" if payload_failed else "available",
        **({"payload_reason": "payload_cleanup_failed"} if payload_failed else {}),
    }


def _legacy_purge_workspace_runs(workspace_id: str) -> dict[str, Any]:
    """Delete one workspace after acquiring its durable lineage lifecycle."""
    workspace_id = str(workspace_id or "").strip()
    registry = authoritative_run_registry()
    lifecycle = _begin_workspace_purge(workspace_id, registry)
    if not lifecycle:
        return {
            "workspace_id": workspace_id,
            "run_ids": [],
            "deleted_local_runs": 0,
            "deleted_blob_runs": 0,
            "registry_updated": False,
            "status": "unavailable",
        }
    run_ids = {
        str(item.get("run_id") or "")
        for item in list_runs(workspace_id)
        if item.get("run_id")
    }
    if blob_configured():
        try:
            named_runs = list_blob_json_named_strict(f"{RUN_BLOB_PREFIX}/")
        except BlobJsonReadError:
            return {
                "workspace_id": workspace_id,
                "run_ids": [],
                "deleted_local_runs": 0,
                "deleted_blob_runs": 0,
                "registry_updated": False,
                "lineage_updated": False,
                "status": "unavailable",
            }
        run_ids.update(
            str(value.get("run_id") or "")
            for _name, value in named_runs
            if isinstance(value, dict)
            and str(value.get("workspace_id") or "") == workspace_id
            and value.get("run_id")
        )
    for key in ("analysis_history", "canonical_history", "attachment_history"):
        run_ids.update(
            str(item.get("run_id") or "")
            for item in lifecycle.get(key) or []
            if isinstance(item, dict) and item.get("run_id")
        )
    for key in ("latest_run_id", "latest_canonical_run_id", "last_failed_pending_run_id"):
        if lifecycle.get(key):
            run_ids.add(str(lifecycle[key]))
    run_ids = sorted(item for item in run_ids if item)
    deleted_local = 0
    deleted_blob = 0
    deletion_confirmed = True
    for run_id in run_ids:
        safe = _safe_name(run_id)
        path = RUN_DIR / f"{safe}.json"
        if path.exists():
            try:
                path.unlink()
                deleted_local += 1
            except Exception:
                deletion_confirmed = False
        if path.exists():
            deletion_confirmed = False
        if blob_configured():
            try:
                if delete_blob_name(f"{RUN_BLOB_PREFIX}/{safe}.json"):
                    deleted_blob += 1
            except Exception:
                deletion_confirmed = False
            if _load_authoritative_run_read(run_id).status != "missing":
                deletion_confirmed = False
    if not deletion_confirmed:
        return {
            "workspace_id": workspace_id,
            "run_ids": run_ids,
            "deleted_local_runs": deleted_local,
            "deleted_blob_runs": deleted_blob,
            "registry_updated": False,
            "lineage_updated": False,
            "status": "unavailable",
        }
    registry_updated = False
    committed_registry: dict[str, Any] | None = None
    for _attempt in range(8 if blob_configured() else 1):
        registry = authoritative_run_registry()
        if registry.get("read_status") == "error":
            break
        entries = [
            item
            for item in registry.get("runs") or []
            if isinstance(item, dict) and item.get("workspace_id") != workspace_id
        ]
        expected_revision = int(registry.get("revision") or 0)
        value = {
            "version": int(registry.get("version") or 2),
            "revision": expected_revision + 1,
            "history_truncated": registry.get("history_truncated") is True,
            "runs": entries,
        }
        if not blob_configured():
            _write_local_registry(value)
            registry_updated = True
            committed_registry = value
            break
        committed = compare_and_swap_blob_json(
            RUN_REGISTRY_BLOB,
            expected_revision=expected_revision,
            changes=value,
        )
        if committed:
            registry_updated = True
            committed_registry = committed
            break
    lineage_purged = False
    if registry_updated and committed_registry:
        final_lineage = _confirm_workspace_purge_integrity(
            workspace_id,
            lifecycle,
            int(committed_registry.get("revision") or 0),
        )
        if final_lineage:
            for _attempt in range(8 if blob_configured() else 1):
                lineage = workspace_lineage_state(workspace_id)
                if (
                    lineage.get("read_status") == "error"
                    or str(lineage.get("status") or "") != "purging"
                    or int(lineage.get("revision") or 0) != int(final_lineage.get("revision") or 0)
                ):
                    break
                purged_state = _cas_workspace_lineage(
                    lineage,
                    {
                        "version": 2,
                        "status": "purged",
                        "generation": _workspace_generation(lineage),
                        "purged_at": _utc_now(),
                        "genesis_proof": "purged_no_history",
                        "analysis_count": 0,
                        "canonical_count": 0,
                        "pending_run_id": None,
                        "pending_started_at": None,
                        "pending_candidate_content_sha256": None,
                        "latest_run_id": None,
                        "latest_canonical_run_id": None,
                        "latest_source_envelope": None,
                        "canonical_target_envelope": None,
                        "analysis_history": [],
                        "canonical_history": [],
                        "attachment_history": [],
                        "lineage_history_complete": True,
                        "attachment_history_complete": True,
                    },
                )
                if purged_state:
                    if _confirm_workspace_purge_integrity(
                        workspace_id,
                        purged_state,
                        int(committed_registry.get("revision") or 0),
                    ):
                        lineage_purged = True
                    else:
                        _invalidate_finalized_workspace_purge(workspace_id, purged_state)
                    break
    try:
        set_flagship_plan(workspace_id, None)
    except Exception:
        pass
    return {
        "workspace_id": workspace_id,
        "run_ids": run_ids,
        "deleted_local_runs": deleted_local,
        "deleted_blob_runs": deleted_blob,
        "registry_updated": registry_updated,
        "lineage_updated": lineage_purged,
        "status": "purged" if registry_updated and lineage_purged else "unavailable",
    }


def _confirm_workspace_purge_integrity(
    workspace_id: str,
    lifecycle: dict[str, Any],
    registry_revision: int,
) -> dict[str, Any]:
    lineage = workspace_lineage_state(workspace_id)
    expected_status = str(lifecycle.get("status") or "purging")
    if (
        lineage.get("read_status") == "error"
        or str(lineage.get("status") or "") != expected_status
        or int(lineage.get("revision") or 0) != int(lifecycle.get("revision") or 0)
    ):
        return {}
    registry = authoritative_run_registry()
    if (
        registry.get("read_status") == "error"
        or int(registry.get("revision") or 0) != registry_revision
        or any(
            str(item.get("workspace_id") or "") == workspace_id
            for item in registry.get("runs") or []
            if isinstance(item, dict)
        )
    ):
        return {}
    if not _workspace_publications_absent(workspace_id):
        return {}
    return lineage


def _invalidate_finalized_workspace_purge(
    workspace_id: str,
    purged_state: dict[str, Any],
) -> dict[str, Any]:
    current = workspace_lineage_state(workspace_id)
    if (
        current.get("read_status") == "error"
        or str(current.get("status") or "") != "purged"
        or int(current.get("revision") or 0) != int(purged_state.get("revision") or 0)
    ):
        return {}
    failure = (
        "late_writer_cleanup_failed"
        if not _workspace_publications_absent(workspace_id)
        else "purge_post_finalize_integrity_failed"
    )
    return _cas_workspace_lineage(
        current,
        {
            "status": "purging",
            "purge_integrity_failure": failure,
            "purge_finalization_rejected_at": _utc_now(),
        },
    ) or {}


def _local_workspace_runs_absent(workspace_id: str) -> bool:
    if not RUN_DIR.exists():
        return True
    try:
        paths = list(RUN_DIR.glob("*.json"))
    except OSError:
        return False
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(value, dict):
            return False
        if str(value.get("workspace_id") or "") == workspace_id:
            return False
    return True


def _workspace_publications_absent(workspace_id: str) -> bool:
    if blob_configured():
        try:
            named_runs = list_blob_json_named_strict(f"{RUN_BLOB_PREFIX}/")
        except BlobJsonReadError:
            return False
        if any(
            str(value.get("workspace_id") or "") == workspace_id
            for _name, value in named_runs
            if isinstance(value, dict)
        ):
            return False
    return _local_workspace_runs_absent(workspace_id)


def _begin_workspace_purge(
    workspace_id: str,
    registry: dict[str, Any],
) -> dict[str, Any]:
    if not workspace_id or registry.get("read_status") == "error":
        return {}
    for _attempt in range(8 if blob_configured() else 1):
        current = workspace_lineage_state(workspace_id)
        if current.get("read_status") == "error":
            return {}
        if not current:
            current = _bootstrap_workspace_lineage(workspace_id, registry)
        if not current and any(
            str(item.get("workspace_id") or "") == workspace_id
            for item in registry.get("runs") or []
            if isinstance(item, dict)
        ):
            current = _cas_workspace_lineage(
                {"workspace_id": workspace_id, "revision": 0},
                {
                    "version": 2,
                    "workspace_id": workspace_id,
                    "status": "purging",
                    "generation": WORKSPACE_GENERATION_INITIAL,
                    "genesis_proof": "purge_registry_rows_present",
                    "analysis_count": 0,
                    "canonical_count": 0,
                    "analysis_history": [],
                    "canonical_history": [],
                    "attachment_history": [],
                    "lineage_history_complete": False,
                    "attachment_history_complete": False,
                    "purge_started_at": _utc_now(),
                },
            ) or {}
        if not current:
            return {}
        status = str(current.get("status") or "")
        if status == "purging":
            return current
        if status == "pending":
            if not _pending_is_stale(current):
                return {}
            recovered = _recover_pending_lineage(current, registry)
            if not recovered:
                return {}
            current = recovered
            status = str(current.get("status") or "")
        if status not in {"stable", "purged"}:
            return {}
        claimed = _cas_workspace_lineage(
            current,
            {
                "status": "purging",
                "purge_started_at": _utc_now(),
                "pending_run_id": None,
            },
        )
        if claimed:
            return claimed
    return {}


def _bootstrap_workspace_lineage(
    workspace_id: str,
    registry: dict[str, Any],
) -> dict[str, Any]:
    current = workspace_lineage_state(workspace_id)
    if current.get("read_status") == "error" or registry.get("read_status") == "error":
        return {}
    if current:
        return current
    if registry.get("history_truncated") is True:
        return {}
    workspace_rows = [
        item
        for item in registry.get("runs") or []
        if isinstance(item, dict) and str(item.get("workspace_id") or "") == workspace_id
    ]
    row_ids = [str(item.get("run_id") or "").strip() for item in workspace_rows]
    if any(not run_id for run_id in row_ids) or len(row_ids) != len(set(row_ids)):
        return {}
    summaries = [
        item
        for item in workspace_rows
        if not str(item.get("version_kind") or "")
        and any(item.get(key) is not None and item.get(key) != "" for key in _LINEAGE_ENVELOPE_KEYS)
    ]
    if any(str(item.get("canonical_lineage_status") or "") != "trusted" for item in summaries):
        return {}
    if not summaries:
        return initialize_workspace_lineage(workspace_id, no_prior_analysis=True)
    ordered_summaries = sorted(
        summaries,
        key=lambda item: (int(item.get("canonical_lineage_sequence") or 0), str(item.get("run_id") or "")),
    )
    sequences = [int(item.get("canonical_lineage_sequence") or 0) for item in ordered_summaries]
    if sequences != list(range(1, len(ordered_summaries) + 1)):
        return {}
    analysis_history: list[dict[str, Any]] = []
    canonical_history: list[dict[str, Any]] = []
    loaded: dict[str, dict[str, Any]] = {}
    for summary in ordered_summaries:
        run_id = str(summary.get("run_id") or "")
        source = _load_authoritative_run(run_id)
        if (
            not isinstance(source, dict)
            or str(source.get("run_id") or "") != run_id
            or str(source.get("workspace_id") or "") != workspace_id
            or not _is_completed_analysis(source)
            or not _registry_envelope_matches_run(summary, source, _LINEAGE_ENVELOPE_KEYS)
            or str(source.get("canonical_lineage_commit_id") or "")
            != _canonical_lineage_commit_id(
                source,
                str(source.get("canonical_experiment_run_id") or ""),
                int(source.get("canonical_lineage_sequence") or 0),
            )
            or not _lineage_envelope_matches(
                source, str(source.get("canonical_experiment_run_id") or "")
            )
        ):
            return {}
        loaded[run_id] = source

    for summary in ordered_summaries:
        run_id = str(summary.get("run_id") or "")
        source = loaded[run_id]
        target_id = str(source.get("canonical_experiment_run_id") or "")
        target = loaded.get(target_id)
        source_sequence = int(source.get("canonical_lineage_sequence") or 0)
        target_sequence = int(target.get("canonical_lineage_sequence") or 0) if target else 0
        if (
            not isinstance(target, dict)
            or str(target.get("workspace_id") or "") != workspace_id
            or str(target.get("canonical_experiment_run_id") or "") != target_id
            or not _lineage_envelope_matches(target, target_id)
            or str(source.get("canonical_target_commit_id") or "")
            != str(target.get("canonical_lineage_commit_id") or "")
            or str(source.get("canonical_target_content_sha256") or "")
            != str(target.get("canonical_lineage_content_sha256") or "")
            or (target_id != run_id and target_sequence >= source_sequence)
        ):
            return {}
        analysis_history.append(_lineage_record(source))
        if target_id == run_id:
            canonical_history.append({**_lineage_record(source), "ordinal": len(canonical_history) + 1})
    latest_summary = ordered_summaries[-1]
    source_id = str(latest_summary.get("run_id") or "")
    target_id = str(latest_summary.get("canonical_experiment_run_id") or "")
    source = loaded.get(source_id) or _load_authoritative_run(source_id)
    target = loaded.get(target_id) or _load_authoritative_run(target_id)
    target_summary = _registry_entry(registry, target_id)
    if (
        not isinstance(source, dict)
        or not isinstance(target, dict)
        or not _registry_envelope_matches_run(latest_summary, source, _LINEAGE_ENVELOPE_KEYS)
        or not _registry_envelope_matches_run(target_summary, target, _LINEAGE_ENVELOPE_KEYS)
        or not _lineage_envelope_matches(source, target_id)
        or not _lineage_envelope_matches(target, target_id)
    ):
        return {}
    attachment_history: list[dict[str, Any]] = []
    snapshot_summaries = [
        item
        for item in workspace_rows
        if str(item.get("version_kind") or "") in _ATTACHMENT_VERSION_KINDS
        or item.get("experiment_attachment") is True
        or bool(str(item.get("attachment_commit_status") or ""))
        or bool(str(item.get("attachment_commit_id") or ""))
        or bool(str(item.get("attachment_payload_sha256") or ""))
    ]
    for summary in snapshot_summaries:
        snapshot_id = str(summary.get("run_id") or "")
        snapshot = _load_authoritative_run(snapshot_id)
        snapshot_source_id = str(snapshot.get("source_run_id") or "") if isinstance(snapshot, dict) else ""
        snapshot_source = loaded.get(snapshot_source_id)
        source_summary = _registry_entry(registry, snapshot_source_id)
        version_kind = str(snapshot.get("version_kind") or "") if isinstance(snapshot, dict) else ""
        if (
            not isinstance(snapshot, dict)
            or not isinstance(snapshot_source, dict)
            or str(snapshot.get("run_id") or "") != snapshot_id
            or str(snapshot.get("workspace_id") or "") != workspace_id
            or str(snapshot_source.get("workspace_id") or "") != workspace_id
            or version_kind not in _ATTACHMENT_VERSION_KINDS
            or str(summary.get("version_kind") or "") != version_kind
            or snapshot.get("experiment_attachment") is not True
            or str(snapshot.get("attachment_commit_status") or "") != "confirmed"
            or not snapshot_source_id
            or str(snapshot.get("experiment_version_id") or "") != f"version:{snapshot_source_id}"
            or str(snapshot_source.get("canonical_experiment_run_id") or "") != snapshot_source_id
            or not _lineage_envelope_matches(snapshot_source, snapshot_source_id)
            or not _registry_envelope_matches_run(
                source_summary, snapshot_source, _LINEAGE_ENVELOPE_KEYS
            )
            or not _registry_envelope_matches_run(summary, snapshot, _ATTACHMENT_ENVELOPE_KEYS)
            or str(snapshot.get("attachment_payload_sha256") or "") != _attachment_payload_hash(snapshot)
            or str(snapshot.get("attachment_commit_id") or "")
            != _attachment_commit_id(snapshot, snapshot_source)
        ):
            return {}
        attachment_history.append(_attachment_record(snapshot))
    created = _cas_workspace_lineage(
        {"workspace_id": workspace_id, "revision": 0},
        {
            "version": 2,
            "workspace_id": workspace_id,
            "status": "stable",
            "generation": WORKSPACE_GENERATION_INITIAL,
            "genesis_proof": "registry_history_complete",
            "analysis_count": len(ordered_summaries),
            "canonical_count": len(canonical_history),
            "latest_run_id": source_id,
            "latest_canonical_run_id": target_id,
            "latest_source_envelope": _lineage_record(source),
            "canonical_target_envelope": _lineage_record(target),
            "analysis_history": analysis_history[-LINEAGE_HISTORY_LIMIT:],
            "canonical_history": canonical_history[-LINEAGE_HISTORY_LIMIT:],
            "attachment_history": attachment_history[-ATTACHMENT_HISTORY_LIMIT:],
            "lineage_history_complete": True,
            "attachment_history_complete": len(attachment_history) <= ATTACHMENT_HISTORY_LIMIT,
        },
    )
    return created or workspace_lineage_state(workspace_id)


def _reserve_workspace_lineage(
    workspace_id: str,
    registry: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    if registry.get("read_status") == "error":
        return {}
    run_id = str(candidate.get("run_id") or "")
    attempts = 100 if blob_configured() else 1
    for _attempt in range(attempts):
        current = workspace_lineage_state(workspace_id) or _bootstrap_workspace_lineage(workspace_id, registry)
        if not current or current.get("read_status") == "error":
            return {}
        status = str(current.get("status") or "")
        if status == "pending" and _pending_is_stale(current):
            current_registry = authoritative_run_registry()
            recovered = _recover_pending_lineage(current, current_registry)
            if recovered:
                continue
            return {}
        if status in {"purging", "purged"}:
            return {}
        if status != "stable":
            if blob_configured():
                time.sleep(0.005)
                continue
            return {}
        candidate_generation = int(candidate.get("workspace_generation") or 0)
        current_generation = _workspace_generation(current)
        if candidate_generation != current_generation:
            return {}
        reserved = _cas_workspace_lineage(
            current,
            {
                "status": "pending",
                "generation": current_generation,
                "pending_run_id": run_id,
                "pending_started_at": _utc_now(),
                "pending_candidate_content_sha256": _analysis_content_hash(candidate),
            },
        )
        if reserved:
            return reserved
        if blob_configured():
            time.sleep(0.005)
    return {}


def _pending_is_stale(state: dict[str, Any]) -> bool:
    raw = str(state.get("pending_started_at") or "").strip()
    if not raw:
        return True
    try:
        started = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - started).total_seconds() >= LINEAGE_PENDING_TIMEOUT_SECONDS


def _remove_registry_run(
    registry: dict[str, Any],
    run_id: str,
) -> dict[str, Any] | None:
    if registry.get("read_status") == "error":
        return None
    entries = [
        item
        for item in registry.get("runs") or []
        if isinstance(item, dict) and str(item.get("run_id") or "") != run_id
    ]
    expected_revision = int(registry.get("revision") or 0)
    value = {
        "version": int(registry.get("version") or 2),
        "revision": expected_revision + 1,
        "history_truncated": registry.get("history_truncated") is True,
        "runs": entries,
    }
    if not blob_configured():
        _write_local_registry(value)
        return value
    return compare_and_swap_blob_json(
        RUN_REGISTRY_BLOB,
        expected_revision=expected_revision,
        changes=value,
    )


def _recover_pending_lineage(
    pending: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    if (
        pending.get("read_status") == "error"
        or registry.get("read_status") == "error"
        or str(pending.get("status") or "") != "pending"
        or not _pending_is_stale(pending)
    ):
        return {}
    run_id = str(pending.get("pending_run_id") or "")
    expected_hash = str(pending.get("pending_candidate_content_sha256") or "")
    source_result = _load_authoritative_run_read(run_id)
    if source_result.status == "error":
        return {}
    source = source_result.value if source_result.status == "present" else None
    summary = _registry_entry(registry, run_id)
    if (
        isinstance(source, dict)
        and expected_hash
        and str(source.get("canonical_lineage_content_sha256") or "") == expected_hash
        and _registry_envelope_matches_run(summary, source, _LINEAGE_ENVELOPE_KEYS)
        and _lineage_envelope_matches(source, str(source.get("canonical_experiment_run_id") or ""))
    ):
        target_id = str(source.get("canonical_experiment_run_id") or "")
        target_result = _load_authoritative_run_read(target_id)
        if target_result.status == "error":
            return {}
        target = target_result.value if target_result.status == "present" else None
        if (
            isinstance(target, dict)
            and _lineage_envelope_matches(target, target_id)
            and str(source.get("canonical_target_commit_id") or "")
            == str(target.get("canonical_lineage_commit_id") or "")
            and str(source.get("canonical_target_content_sha256") or "")
            == str(target.get("canonical_lineage_content_sha256") or "")
            and (
                _lineage_record_matches(_state_lineage_record(pending, target_id), target)
                or _registry_envelope_matches_run(
                    _registry_entry(registry, target_id), target, _LINEAGE_ENVELOPE_KEYS
                )
                or target_id == run_id
            )
        ):
            return _finalize_workspace_lineage(pending, source, target)
    if summary:
        cleaned = _remove_registry_run(registry, run_id)
        if cleaned is None:
            return {}
    current = workspace_lineage_state(str(pending.get("workspace_id") or ""))
    if (
        current.get("read_status") == "error"
        or int(current.get("revision") or 0) != int(pending.get("revision") or 0)
        or str(current.get("status") or "") != "pending"
        or str(current.get("pending_run_id") or "") != run_id
    ):
        return {}
    return _cas_workspace_lineage(
        current,
        {
            "status": "stable",
            "pending_run_id": None,
            "pending_started_at": None,
            "pending_candidate_content_sha256": None,
            "last_failed_pending_run_id": run_id,
            "last_failed_pending_at": _utc_now(),
        },
    ) or {}


def _finalize_workspace_lineage(
    reserved: dict[str, Any],
    proposed: dict[str, Any],
    target: dict[str, Any] | None,
) -> dict[str, Any]:
    workspace_id = str(proposed.get("workspace_id") or "")
    current = workspace_lineage_state(workspace_id)
    if (
        not isinstance(target, dict)
        or int(current.get("revision") or 0) != int(reserved.get("revision") or 0)
        or str(current.get("status") or "") != "pending"
        or str(current.get("pending_run_id") or "") != str(proposed.get("run_id") or "")
        or not _lineage_envelope_matches(proposed, str(proposed.get("canonical_experiment_run_id") or ""))
        or not _lineage_envelope_matches(target, str(target.get("run_id") or ""))
    ):
        return {}
    analysis_history, analysis_complete = _bounded_history(
        [item for item in reserved.get("analysis_history") or [] if isinstance(item, dict)],
        _lineage_record(proposed),
        limit=LINEAGE_HISTORY_LIMIT,
    )
    canonical_history = [
        item for item in reserved.get("canonical_history") or [] if isinstance(item, dict)
    ]
    canonical_count = int(reserved.get("canonical_count") or len(canonical_history))
    canonical_complete = True
    if str(proposed.get("canonical_experiment_run_id") or "") == str(proposed.get("run_id") or ""):
        canonical_count += 1
        canonical_history, canonical_complete = _bounded_history(
            canonical_history,
            {**_lineage_record(proposed), "ordinal": canonical_count},
            limit=LINEAGE_HISTORY_LIMIT,
        )
    return _cas_workspace_lineage(
        current,
        {
            "version": 2,
            "status": "stable",
            "generation": _workspace_generation(reserved),
            "analysis_count": int(reserved.get("analysis_count") or 0) + 1,
            "canonical_count": canonical_count,
            "latest_run_id": str(proposed.get("run_id") or ""),
            "latest_canonical_run_id": str(proposed.get("canonical_experiment_run_id") or ""),
            "latest_source_envelope": _lineage_record(proposed),
            "canonical_target_envelope": _lineage_record(target),
            "analysis_history": analysis_history,
            "canonical_history": canonical_history,
            "attachment_history": [
                item for item in reserved.get("attachment_history") or [] if isinstance(item, dict)
            ],
            "lineage_history_complete": bool(
                reserved.get("lineage_history_complete", True)
                and analysis_complete
                and canonical_complete
            ),
            "pending_run_id": None,
            "pending_started_at": None,
            "pending_candidate_content_sha256": None,
        },
    ) or {}


def _confirmed_authoritative_run(
    workspace_id: str,
    run_id: str,
    registry: dict[str, Any],
) -> dict[str, Any] | None:
    persisted = _load_authoritative_run(run_id)
    if not isinstance(persisted, dict) or not _lineage_envelope_matches(
        persisted, str(persisted.get("canonical_experiment_run_id") or "")
    ):
        return None
    summary = _registry_entry(registry, run_id)
    state = workspace_lineage_state(workspace_id)
    state_status = str(state.get("status") or "")
    if state_status in {"stable", "pending"}:
        confirmed = _lineage_record_matches(_state_lineage_record(state, run_id), persisted)
        if state_status == "stable":
            confirmed = confirmed or _registry_envelope_matches_run(
                summary, persisted, _LINEAGE_ENVELOPE_KEYS
            )
    elif state:
        confirmed = False
    else:
        confirmed = _registry_envelope_matches_run(summary, persisted, _LINEAGE_ENVELOPE_KEYS)
    return persisted if confirmed else None


def _workspace_lineage_write_guard(
    workspace_id: str,
    expected_generation: int,
) -> dict[str, Any] | None:
    state = workspace_lineage_state(workspace_id)
    if state.get("read_status") == "error" or str(state.get("status") or "") in {"purging", "purged"}:
        return None
    generation = _workspace_generation(state)
    if expected_generation <= 0:
        if state and "generation" in state:
            return None
        expected_generation = generation
    if generation != expected_generation:
        return None
    return _workspace_lineage_guard_from_state(state)


def _workspace_lineage_guard_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "present": bool(state),
        "revision": int(state.get("revision") or 0),
        "status": str(state.get("status") or ""),
        "generation": _workspace_generation(state),
    }


def _workspace_lineage_guard_matches(workspace_id: str, guard: dict[str, Any]) -> bool:
    state = workspace_lineage_state(workspace_id)
    if state.get("read_status") == "error" or str(state.get("status") or "") in {"purging", "purged"}:
        return False
    if bool(state) != bool(guard.get("present")):
        return False
    return (
        str(state.get("status") or "") == str(guard.get("status") or "")
        and _workspace_generation(state) == int(guard.get("generation") or 0)
    )


def _record_late_workspace_writer(
    workspace_id: str,
    run_id: str,
    writer_generation: int,
    cleanup_errors: list[str] | None = None,
) -> dict[str, Any]:
    for _attempt in range(8 if blob_configured() else 1):
        state = workspace_lineage_state(workspace_id)
        status = str(state.get("status") or "")
        if state.get("read_status") == "error" or not state:
            return {}
        changes: dict[str, Any] = {
            "last_rejected_writer_run_id": run_id,
            "last_rejected_writer_generation": writer_generation,
            "last_rejected_writer_at": _utc_now(),
        }
        if cleanup_errors:
            changes["late_writer_cleanup_errors"] = cleanup_errors[:4]
            if status in {"purging", "purged"}:
                changes["purge_integrity_failure"] = "late_writer_cleanup_failed"
            else:
                changes["publication_integrity_failure"] = "rejected_writer_cleanup_failed"
        if status == "purged":
            changes.update(
                {
                    "status": "purging",
                    "purge_integrity_failure": (
                        "late_writer_cleanup_failed" if cleanup_errors else "late_writer_after_purge"
                    ),
                }
            )
        committed = _cas_workspace_lineage(state, changes)
        if committed:
            return committed
    return {}


def _record_rejected_writer_failure(
    workspace_id: str,
    run_id: str,
    writer_generation: int,
    cleanup_errors: list[str],
) -> dict[str, Any]:
    for _attempt in range(8 if blob_configured() else 1):
        state = workspace_lineage_state(workspace_id)
        status = str(state.get("status") or "")
        if state.get("read_status") == "error" or not state:
            return {}
        changes: dict[str, Any] = {
            "rejected_writer_record_failure_run_id": run_id,
            "rejected_writer_record_failure_generation": writer_generation,
            "rejected_writer_record_failure_at": _utc_now(),
        }
        if cleanup_errors:
            changes["late_writer_cleanup_errors"] = cleanup_errors[:4]
        if status in {"purging", "purged"}:
            changes["status"] = "purging"
            changes["purge_integrity_failure"] = (
                "late_writer_cleanup_failed" if cleanup_errors else "late_writer_record_failed"
            )
        else:
            changes["publication_integrity_failure"] = "rejected_writer_record_failed"
        committed = _cas_workspace_lineage(state, changes)
        if committed:
            return committed
    return {}


def _cleanup_rejected_registry_run(run_id: str) -> bool:
    for _attempt in range(8 if blob_configured() else 1):
        registry = authoritative_run_registry()
        if registry.get("read_status") == "error":
            return False
        if not _registry_entry(registry, run_id):
            return True
        if _remove_registry_run(registry, run_id) is not None:
            return True
    return False


def _workspace_write_unavailable(
    run: dict[str, Any],
    registry: dict[str, Any],
    warning: str,
) -> dict[str, Any]:
    run_id = str(run.get("run_id") or "")
    persisted_result = _load_authoritative_run_read(run_id)
    persisted = persisted_result.value if persisted_result.status == "present" else None
    if (
        isinstance(persisted, dict)
        and _is_completed_analysis(persisted)
        and _registry_envelope_matches_run(
            _registry_entry(registry, run_id),
            persisted,
            _LINEAGE_ENVELOPE_KEYS,
        )
    ):
        unavailable = copy.deepcopy(persisted)
    else:
        unavailable = copy.deepcopy(run)
        if _is_completed_analysis(unavailable):
            _mark_canonical_lineage_unresolved(unavailable, candidate=True)
        if str(unavailable.get("version_kind") or "") in _ATTACHMENT_VERSION_KINDS:
            unavailable["experiment_attachment"] = False
            unavailable["attachment_commit_status"] = "candidate"
            unavailable.pop("attachment_commit_id", None)
            unavailable.pop("attachment_payload_sha256", None)
    unavailable["persistence"] = {
        "mode": "unavailable",
        "confirmed": False,
        "update_status": "unavailable",
        "warning": warning,
    }
    return _replace_run(run, unavailable)


def _reject_published_workspace_write(
    run: dict[str, Any],
    blob_name: str,
    registry: dict[str, Any],
    warning: str,
) -> dict[str, Any]:
    workspace_id = str(run.get("workspace_id") or "")
    run_id = str(run.get("run_id") or "")
    cleanup_errors: list[str] = []
    if blob_configured():
        try:
            delete_blob_name(blob_name)
            if isinstance(download_blob_json_strict(blob_name), dict):
                cleanup_errors.append("remote run blob remains present")
        except Exception as exc:
            cleanup_errors.append(f"remote cleanup {type(exc).__name__}: {exc}"[:300])
    local_path = Path(str(run.get("local_path") or ""))
    if local_path.is_file():
        try:
            local_path.unlink()
        except OSError as exc:
            cleanup_errors.append(f"local cleanup {type(exc).__name__}: {exc}"[:300])
    if local_path.exists():
        cleanup_errors.append("local run file remains present")
    if not _cleanup_rejected_registry_run(run_id):
        cleanup_errors.append("registry cleanup could not be confirmed")
    recorded = _record_late_workspace_writer(
        workspace_id,
        run_id,
        int(run.get("workspace_generation") or 0),
        cleanup_errors,
    )
    failure_marker = {}
    if not recorded:
        failure_marker = _record_rejected_writer_failure(
            workspace_id,
            run_id,
            int(run.get("workspace_generation") or 0),
            cleanup_errors,
        )
    unavailable = _workspace_write_unavailable(run, authoritative_run_registry(), warning)
    persistence = unavailable.setdefault("persistence", {})
    persistence["rejection_record_status"] = "recorded" if recorded else "failed"
    if not recorded:
        persistence["rejection_failure_marker_status"] = (
            "recorded" if failure_marker else "failed"
        )
    if cleanup_errors:
        persistence["cleanup_errors"] = cleanup_errors[:4]
    return unavailable


def _finalize_generation_bound_publication(
    run: dict[str, Any],
    path: Path,
    blob_name: str,
    registry: dict[str, Any],
    lineage_guard: dict[str, Any],
    warning: str,
) -> dict[str, Any]:
    try:
        _write_run_file(path, run)
    except Exception as exc:
        persistence = run.setdefault("persistence", {})
        if blob_configured():
            persistence["local_cache_error"] = f"{type(exc).__name__}: {exc}"[:500]
        else:
            persistence.update(
                {
                    "mode": "confirmed_unchanged",
                    "confirmed": False,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
    if (
        int(run.get("workspace_generation") or 0) == int(lineage_guard.get("generation") or 0)
        and _workspace_lineage_guard_matches(str(run.get("workspace_id") or ""), lineage_guard)
    ):
        return run
    return _reject_published_workspace_write(run, blob_name, registry, warning)


def _persist_run(
    run: dict[str, Any],
    *,
    lineage_repository: Any | None = None,
    analysis_completed_event: bool = False,
) -> dict[str, Any]:
    with _LOCK:
        return _persist_run_locked(
            run,
            lineage_repository=lineage_repository,
            analysis_completed_event=analysis_completed_event,
        )


def _persist_run_locked(
    run: dict[str, Any],
    *,
    lineage_repository: Any | None = None,
    analysis_completed_event: bool = False,
) -> dict[str, Any]:
    sanitized = _sanitize_run_capability_metadata(run)
    if isinstance(sanitized, dict):
        run.clear()
        run.update(sanitized)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(str(run.get("run_id") or "run"))
    path = RUN_DIR / f"{safe}.json"
    run["local_path"] = str(path)
    blob_name = f"{RUN_BLOB_PREFIX}/{safe}.json"
    if _is_completed_analysis(run):
        try:
            repository = _resolve_lineage_repository(lineage_repository)
            if analysis_completed_event:
                return _commit_sql_analysis(run, path, blob_name, repository)
            return _refresh_sql_analysis_payload(run, path, blob_name, repository)
        except Exception:
            return _sql_lineage_unavailable(run)
    if str(run.get("version_kind") or "") in _ATTACHMENT_VERSION_KINDS:
        try:
            repository = _resolve_lineage_repository(lineage_repository)
            return _commit_sql_snapshot(run, path, blob_name, repository)
        except Exception:
            return _sql_lineage_unavailable(run)
    # Ordinary run telemetry has no experiment-version membership. It must
    # remain observable when SQL lineage is intentionally unavailable, while
    # an available repository still fences stale workspace generations.
    repository = None
    try:
        repository = _resolve_lineage_repository(lineage_repository)
        _require_current_sql_generation(repository, run)
    except Exception as exc:
        if type(exc).__name__ != "LineageUnavailable":
            return _sql_lineage_unavailable(run)
        repository = None
    return _persist_generic_run(run, path, blob_name, repository=repository)


def _commit_sql_analysis(
    run: dict[str, Any],
    path: Path,
    blob_name: str,
    repository: Any,
) -> dict[str, Any]:
    try:
        from .experiment_store import analysis_lineage_fingerprints
    except ImportError:
        from experiment_store import analysis_lineage_fingerprints

    workspace_id = str(run.get("workspace_id") or "")
    generation = _require_current_sql_generation(repository, run)
    decision_fingerprint, evidence_fingerprint = analysis_lineage_fingerprints(run)
    committed = repository.commit_analysis(
        workspace_id=workspace_id,
        generation=generation,
        canonical_run_id=str(run.get("run_id") or ""),
        decision_fingerprint=decision_fingerprint,
        evidence_fingerprint=evidence_fingerprint,
        actor_metadata=None,
    )
    committed_generation = int(_commit_value(committed, "generation") or 0)
    if committed_generation != generation:
        raise RuntimeError("lineage commit generation mismatch")
    canonical_run_id = str(_commit_value(committed, "canonical_run_id") or "")
    version_id = str(_commit_value(committed, "version_id") or "")
    ordinal = int(_commit_value(committed, "ordinal") or 0)
    if not canonical_run_id or not version_id or ordinal < 1:
        raise RuntimeError("lineage commit is incomplete")
    run.update(
        {
            "workspace_generation": committed_generation,
            "canonical_experiment_run_id": canonical_run_id,
            "canonical_experiment_version_id": version_id,
            "canonical_resolution_status": "resolved",
            "canonical_lineage_status": "trusted",
            "canonical_lineage_commit_id": version_id,
            "canonical_target_commit_id": version_id,
            "canonical_target_content_sha256": _analysis_content_hash(run),
            "canonical_lineage_content_sha256": _analysis_content_hash(run),
            "canonical_lineage_sequence": ordinal,
            "_canonical_ordinal": ordinal,
        }
    )
    _LINEAGE_GENERATION_HINTS[workspace_id] = committed_generation
    run["registry_summary"] = _run_summary(run)
    return _publish_sql_committed_payload(run, path, blob_name, repository)


def _refresh_sql_analysis_payload(
    run: dict[str, Any],
    path: Path,
    blob_name: str,
    repository: Any,
) -> dict[str, Any]:
    workspace_id = str(run.get("workspace_id") or "")
    generation = _require_current_sql_generation(repository, run)
    version_id = str(run.get("canonical_experiment_version_id") or "")
    canonical_run_id = str(run.get("canonical_experiment_run_id") or run.get("run_id") or "")
    version = next(
        (
            item
            for item in repository.list_versions(workspace_id=workspace_id, generation=generation)
            if str(_commit_value(item, "version_id") or "") == version_id
            and str(_commit_value(item, "canonical_run_id") or "") == canonical_run_id
        ),
        None,
    )
    if version is None:
        raise RuntimeError("canonical SQL version is unavailable")
    run["workspace_generation"] = generation
    run["registry_summary"] = _run_summary(run)
    return _publish_sql_committed_payload(run, path, blob_name, repository)


def _commit_sql_snapshot(
    run: dict[str, Any],
    path: Path,
    blob_name: str,
    repository: Any,
) -> dict[str, Any]:
    workspace_id = str(run.get("workspace_id") or "")
    generation = _require_current_sql_generation(repository, run)
    payload_sha256 = _attachment_payload_hash(run)
    committed = repository.attach_snapshot(
        workspace_id=workspace_id,
        generation=generation,
        version_id=str(run.get("experiment_version_id") or ""),
        kind=str(run.get("version_kind") or ""),
        source_run_id=str(run.get("source_run_id") or ""),
        payload_sha256=payload_sha256,
        actor_metadata=None,
    )
    if int(_commit_value(committed, "generation") or 0) != generation:
        raise RuntimeError("attachment commit generation mismatch")
    attachment_id = str(_commit_value(committed, "attachment_id") or "")
    if not attachment_id:
        raise RuntimeError("attachment commit is incomplete")
    run.update(
        {
            "experiment_attachment": True,
            "attachment_commit_status": "confirmed",
            "attachment_commit_id": attachment_id,
            "attachment_payload_sha256": payload_sha256,
        }
    )
    _LINEAGE_GENERATION_HINTS[workspace_id] = generation
    run["registry_summary"] = _run_summary(run)
    return _publish_sql_committed_payload(run, path, blob_name, repository)


def _publish_sql_committed_payload(
    run: dict[str, Any],
    path: Path,
    blob_name: str,
    repository: Any,
) -> dict[str, Any]:
    _require_current_sql_generation(repository, run)
    payload_failed = False
    run["persistence"] = {
        "mode": "local_and_blob" if blob_configured() else "local",
        "confirmed": True,
        "payload_state": "available",
    }
    try:
        _write_run_file(path, run)
    except Exception:
        payload_failed = True
    if blob_configured() and not payload_failed:
        try:
            _require_current_sql_generation(repository, run)
            upload_blob_json(blob_name, run)
        except Exception:
            payload_failed = True
    if not payload_failed:
        try:
            registry = authoritative_run_registry()
            committed_registry = _commit_registry_summary(registry, _run_summary(run))
            if committed_registry is None:
                payload_failed = True
        except Exception:
            payload_failed = True
    if payload_failed:
        run["persistence"] = {
            "mode": "degraded",
            "confirmed": True,
            "payload_state": "unavailable",
            "reason": "payload_publication_failed",
        }
        try:
            _write_run_file(path, run)
        except Exception:
            pass
    else:
        try:
            _write_run_file(path, run)
            if blob_configured():
                _require_current_sql_generation(repository, run)
                upload_blob_json(blob_name, run)
        except Exception:
            run["persistence"] = {
                "mode": "degraded",
                "confirmed": True,
                "payload_state": "unavailable",
                "reason": "payload_publication_failed",
            }
            try:
                _write_run_file(path, run)
            except Exception:
                pass
    return run


def _sql_lineage_unavailable(run: dict[str, Any]) -> dict[str, Any]:
    unavailable = copy.deepcopy(run)
    for key in _LINEAGE_ENVELOPE_KEYS:
        unavailable.pop(key, None)
    unavailable.pop("_canonical_ordinal", None)
    unavailable["persistence"] = {
        "mode": "unavailable",
        "confirmed": False,
        "reason": "lineage_unavailable",
    }
    return _replace_run(run, unavailable)


def _commit_analysis_candidate(run: dict[str, Any], path: Path, blob_name: str) -> dict[str, Any]:
    candidate = copy.deepcopy(run)
    _mark_canonical_lineage_unresolved(candidate, candidate=True)
    candidate["persistence"] = {"mode": "candidate", "confirmed": False, "blob_name": blob_name}
    candidate["registry_summary"] = _run_summary(candidate)
    _write_run_file(path, candidate)
    registry = authoritative_run_registry()
    reserved = _reserve_workspace_lineage(
        str(candidate.get("workspace_id") or ""),
        registry,
        candidate,
    )
    if not reserved:
        candidate["persistence"]["error"] = "workspace lineage reservation unavailable"
        _write_run_file(path, candidate)
        return _replace_run(run, candidate)
    if blob_configured():
        try:
            upload_blob_json(blob_name, candidate)
        except Exception as exc:
            candidate["persistence"] = {
                "mode": "local_candidate",
                "confirmed": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
            _write_run_file(path, candidate)
            return _replace_run(run, candidate)
    for _attempt in range(8 if blob_configured() else 1):
        registry = authoritative_run_registry()
        if registry.get("read_status") == "error":
            break
        proposed = _proposed_analysis_commit(candidate, registry, reserved)
        if proposed is None:
            break
        summary = _run_summary(proposed)
        committed = _commit_registry_summary(registry, summary)
        if committed is None:
            continue
        proposed["registry_summary"] = summary
        proposed["persistence"] = {
            "mode": "local_and_blob" if blob_configured() else "local",
            "confirmed": False,
            "blob_name": blob_name if blob_configured() else None,
            "registry_revision": int(committed.get("revision") or 0),
        }
        if blob_configured():
            try:
                upload_blob_json(blob_name, proposed)
            except Exception as exc:
                candidate["persistence"] = {
                    "mode": "candidate",
                    "confirmed": False,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
                _write_run_file(path, candidate)
                return _replace_run(run, candidate)
        target = proposed if str(proposed.get("canonical_experiment_run_id") or "") == str(proposed.get("run_id") or "") else _latest_confirmed_analysis(committed, str(candidate.get("workspace_id") or ""), reserved)
        finalized = _finalize_workspace_lineage(reserved, proposed, target)
        if not finalized:
            candidate["persistence"]["error"] = "workspace lineage confirmation unavailable"
            _write_run_file(path, candidate)
            return _replace_run(run, candidate)
        proposed["persistence"]["confirmed"] = True
        publication = _finalize_generation_bound_publication(
            proposed,
            path,
            blob_name,
            committed,
            _workspace_lineage_guard_from_state(finalized),
            "workspace lineage changed after local analysis publication",
        )
        return _replace_run(run, publication)

    candidate["persistence"] = {
        "mode": "candidate",
        "confirmed": False,
        "error": "run registry conditional update could not be confirmed",
    }
    _write_run_file(path, candidate)
    return _replace_run(run, candidate)


def _proposed_analysis_commit(
    candidate: dict[str, Any],
    registry: dict[str, Any],
    lineage_state: dict[str, Any],
) -> dict[str, Any] | None:
    workspace_id = str(candidate.get("workspace_id") or "")
    latest = _latest_confirmed_analysis(registry, workspace_id, lineage_state)
    basis: list[dict[str, Any]] = []
    if latest:
        basis.append(_strip_lineage_for_comparison(latest, sequence=1))
    elif int(lineage_state.get("analysis_count") or 0) > 0:
        return None
    trial = _strip_lineage_for_comparison(candidate, sequence=2)
    basis.append(trial)
    resolved = _resolve_canonical_from_runs(
        workspace_id,
        basis,
        str(candidate.get("run_id") or ""),
        registry_state={"history_truncated": False, "runs": []},
    )
    if not resolved:
        return None
    proposed = copy.deepcopy(candidate)
    sequence = int(lineage_state.get("analysis_count") or 0) + 1
    target_commit_id = None
    target_content_sha256 = None
    if resolved != str(candidate.get("run_id") or ""):
        if not latest or str(latest.get("run_id") or "") != resolved:
            return None
        target_commit_id = str(latest.get("canonical_lineage_commit_id") or "") or None
        target_content_sha256 = str(latest.get("canonical_lineage_content_sha256") or "") or None
        if not target_commit_id:
            return None
    _mark_canonical_lineage_trusted(
        proposed,
        resolved,
        sequence=sequence,
        target_commit_id=target_commit_id,
        target_content_sha256=target_content_sha256,
    )
    return proposed


def _latest_confirmed_analysis(
    registry: dict[str, Any],
    workspace_id: str,
    lineage_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    state = lineage_state if isinstance(lineage_state, dict) else workspace_lineage_state(workspace_id)
    if registry.get("read_status") == "error" or state.get("read_status") == "error":
        return None
    if state and str(state.get("status") or "") in {"stable", "pending"}:
        if int(state.get("analysis_count") or 0) == 0:
            return None
        source_record = state.get("latest_source_envelope")
        target_record = state.get("canonical_target_envelope")
        source_id = str(source_record.get("run_id") or "") if isinstance(source_record, dict) else ""
        target_id = str(target_record.get("run_id") or "") if isinstance(target_record, dict) else ""
        source = _load_authoritative_run(source_id) if source_id else None
        target = _load_authoritative_run(target_id) if target_id else None
        if (
            not isinstance(source, dict)
            or not isinstance(target, dict)
            or not _lineage_record_matches(source_record, source)
            or not _lineage_record_matches(target_record, target)
            or str(source.get("canonical_experiment_run_id") or "") != target_id
            or str(source.get("canonical_target_commit_id") or "") != str(target.get("canonical_lineage_commit_id") or "")
            or str(source.get("canonical_target_content_sha256") or "") != str(target.get("canonical_lineage_content_sha256") or "")
            or not _lineage_envelope_matches(source, target_id)
            or not _lineage_envelope_matches(target, target_id)
        ):
            return None
        return target
    if state:
        return None
    summaries = sorted(
        [
            item
            for item in registry.get("runs") or []
            if isinstance(item, dict)
            and str(item.get("workspace_id") or "") == workspace_id
            and str(item.get("canonical_lineage_status") or "") == "trusted"
            and not str(item.get("version_kind") or "")
        ],
        key=lambda item: (int(item.get("canonical_lineage_sequence") or 0), str(item.get("run_id") or "")),
        reverse=True,
    )
    if not summaries:
        return None
    latest_summary = summaries[0]
    source_id = str(latest_summary.get("run_id") or "")
    target_id = str(latest_summary.get("canonical_experiment_run_id") or "")
    if not target_id:
        return None
    source = _load_authoritative_run(source_id)
    if (
        not isinstance(source, dict)
        or not _registry_envelope_matches_run(latest_summary, source, _LINEAGE_ENVELOPE_KEYS)
        or not _lineage_envelope_matches(source, target_id)
    ):
        return None
    target = _load_authoritative_run(target_id)
    if not isinstance(target, dict):
        return None
    expected_target_hash = str(latest_summary.get("canonical_target_content_sha256") or "")
    if _analysis_content_hash(target) != expected_target_hash:
        return None
    target_summary = _registry_entry(registry, target_id)
    if not _registry_envelope_matches_run(target_summary, target, _LINEAGE_ENVELOPE_KEYS):
        return None
    if not _lineage_envelope_matches(target, target_id):
        return None
    expected_target_commit = str(latest_summary.get("canonical_target_commit_id") or "")
    if expected_target_commit and expected_target_commit != str(target.get("canonical_lineage_commit_id") or ""):
        return None
    return target


def _strip_lineage_for_comparison(run: dict[str, Any], *, sequence: int) -> dict[str, Any]:
    result = copy.deepcopy(run)
    for key in _LINEAGE_ENVELOPE_KEYS:
        result.pop(key, None)
    result["_lineage_sequence"] = sequence
    return result


def _persist_confirmed_run_update(
    run: dict[str, Any],
    path: Path,
    blob_name: str,
    lineage_guard: dict[str, Any],
) -> dict[str, Any]:
    workspace_id = str(run.get("workspace_id") or "")
    for _attempt in range(8 if blob_configured() else 1):
        registry = authoritative_run_registry()
        if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
            return _workspace_write_unavailable(run, registry, "workspace lineage changed during run update")
        summary_before = _registry_entry(registry, str(run.get("run_id") or ""))
        lineage_state = workspace_lineage_state(str(run.get("workspace_id") or ""))
        state_record = _state_lineage_record(lineage_state, str(run.get("run_id") or ""))
        if not (
            _registry_envelope_matches_run(summary_before, run, _LINEAGE_ENVELOPE_KEYS)
            or (
                str(lineage_state.get("status") or "") == "stable"
                and _lineage_record_matches(state_record, run)
            )
        ):
            run["persistence"] = {"mode": "confirmed_unchanged", "confirmed": False, "error": "lineage registry changed"}
            return run
        summary = _run_summary(run)
        committed = _commit_registry_summary(registry, summary)
        if committed is None:
            continue
        if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
            return _workspace_write_unavailable(run, committed, "workspace lineage changed during run update")
        run["registry_summary"] = summary
        run["persistence"] = {
            "mode": "local_and_blob" if blob_configured() else "local",
            "confirmed": True,
            "registry_revision": int(committed.get("revision") or 0),
        }
        if blob_configured():
            try:
                upload_blob_json(blob_name, run)
            except Exception as exc:
                run["persistence"] = {
                    "mode": "confirmed_unchanged",
                    "confirmed": False,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
                return run
            if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
                return _reject_published_workspace_write(
                    run,
                    blob_name,
                    committed,
                    "workspace lineage changed during run update",
                )
        return _finalize_generation_bound_publication(
            run,
            path,
            blob_name,
            committed,
            lineage_guard,
            "workspace lineage changed after local run update publication",
        )
    run["persistence"] = {"mode": "confirmed_unchanged", "confirmed": False, "error": "registry update unconfirmed"}
    return run


def _confirm_workspace_attachment(
    snapshot: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    workspace_id = str(snapshot.get("workspace_id") or "")
    source_id = str(source.get("run_id") or "")
    for _attempt in range(20 if blob_configured() else 1):
        state = workspace_lineage_state(workspace_id)
        if state.get("read_status") == "error":
            return {}
        if str(state.get("status") or "") != "stable":
            if blob_configured() and str(state.get("status") or "") == "pending":
                time.sleep(0.005)
                continue
            return {}
        if not _lineage_record_matches(_state_lineage_record(state, source_id), source):
            return {}
        history, complete = _bounded_history(
            [item for item in state.get("attachment_history") or [] if isinstance(item, dict)],
            _attachment_record(snapshot),
            limit=ATTACHMENT_HISTORY_LIMIT,
        )
        committed = _cas_workspace_lineage(
            state,
            {
                "attachment_history": history,
                "attachment_history_complete": bool(
                    state.get("attachment_history_complete", True) and complete
                ),
            },
        )
        if committed:
            return committed
    return {}


def _commit_snapshot_candidate(
    run: dict[str, Any],
    path: Path,
    blob_name: str,
    lineage_guard: dict[str, Any],
) -> dict[str, Any]:
    workspace_id = str(run.get("workspace_id") or "")
    candidate = copy.deepcopy(run)
    candidate["experiment_attachment"] = False
    candidate["attachment_commit_status"] = "candidate"
    candidate.pop("attachment_commit_id", None)
    candidate.pop("attachment_payload_sha256", None)
    candidate["persistence"] = {"mode": "candidate", "confirmed": False, "blob_name": blob_name}
    candidate["registry_summary"] = _run_summary(candidate)
    if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
        return _workspace_write_unavailable(
            run,
            authoritative_run_registry(),
            "workspace lineage changed before attachment publication",
        )
    _write_run_file(path, candidate)
    if blob_configured():
        if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
            try:
                path.unlink()
            except OSError:
                pass
            return _workspace_write_unavailable(
                run,
                authoritative_run_registry(),
                "workspace lineage changed before attachment publication",
            )
        try:
            upload_blob_json(blob_name, candidate)
        except Exception as exc:
            candidate["persistence"]["error"] = f"{type(exc).__name__}: {exc}"[:500]
            return _replace_run(run, candidate)
        if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
            return _reject_published_workspace_write(
                run,
                blob_name,
                authoritative_run_registry(),
                "workspace lineage changed during attachment publication",
            )
    for _attempt in range(8 if blob_configured() else 1):
        registry = authoritative_run_registry()
        if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
            return _reject_published_workspace_write(
                run,
                blob_name,
                registry,
                "workspace lineage changed during attachment publication",
            )
        source_id = str(candidate.get("source_run_id") or "")
        try:
            source = get_run(source_id)
        except (FileNotFoundError, ValueError, KeyError):
            source = {}
        if trusted_canonical_experiment_run_id(
            str(candidate.get("workspace_id") or ""), source, {source_id: source}, registry
        ) != source_id:
            break
        proposed = copy.deepcopy(candidate)
        proposed["experiment_attachment"] = True
        proposed["attachment_commit_status"] = "confirmed"
        proposed["attachment_payload_sha256"] = _attachment_payload_hash(proposed)
        proposed["attachment_commit_id"] = _attachment_commit_id(proposed, source)
        summary = _run_summary(proposed)
        committed = _commit_registry_summary(registry, summary)
        if committed is None:
            continue
        if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
            return _reject_published_workspace_write(
                run,
                blob_name,
                committed,
                "workspace lineage changed during attachment publication",
            )
        proposed["registry_summary"] = summary
        proposed["persistence"] = {
            "mode": "local_and_blob" if blob_configured() else "local",
            "confirmed": False,
            "registry_revision": int(committed.get("revision") or 0),
        }
        if blob_configured():
            try:
                upload_blob_json(blob_name, proposed)
            except Exception as exc:
                candidate["persistence"]["error"] = f"{type(exc).__name__}: {exc}"[:500]
                _write_run_file(path, candidate)
                return _replace_run(run, candidate)
            if not _workspace_lineage_guard_matches(workspace_id, lineage_guard):
                return _reject_published_workspace_write(
                    run,
                    blob_name,
                    committed,
                    "workspace lineage changed during attachment publication",
                )
        attachment_state = _confirm_workspace_attachment(proposed, source)
        if not attachment_state:
            candidate["persistence"]["error"] = "attachment workspace confirmation unavailable"
            _write_run_file(path, candidate)
            return _replace_run(run, candidate)
        attachment_guard = _workspace_lineage_guard_from_state(attachment_state)
        proposed["persistence"]["confirmed"] = True
        publication = _finalize_generation_bound_publication(
            proposed,
            path,
            blob_name,
            committed,
            attachment_guard,
            "workspace lineage changed after local attachment publication",
        )
        return _replace_run(run, publication)
    candidate["persistence"]["error"] = "attachment registry confirmation unavailable"
    _write_run_file(path, candidate)
    return _replace_run(run, candidate)


def _persist_generic_run(
    run: dict[str, Any],
    path: Path,
    blob_name: str,
    *,
    repository: Any | None,
) -> dict[str, Any]:
    summary = _run_summary(run)
    try:
        registry = authoritative_run_registry()
        committed = _commit_registry_summary(registry, summary)
        if committed is None:
            raise RuntimeError("run registry conditional update could not be confirmed")
        if blob_configured():
            upload_blob_json(blob_name, run)
        if repository is not None:
            try:
                _require_current_sql_generation(repository, run)
            except Exception:
                return _reject_published_workspace_write(
                    run,
                    blob_name,
                    committed,
                    "workspace generation changed during generic run publication",
                )
        run["persistence"] = {"mode": "local_and_blob" if blob_configured() else "local", "confirmed": True}
        run["registry_summary"] = summary
        _write_run_file(path, run)
        return run
    except Exception:
        return _sql_lineage_unavailable(run)


def _commit_registry_summary(registry: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any] | None:
    if registry.get("read_status") == "error":
        return None
    entries = [item for item in registry.get("runs") or [] if isinstance(item, dict)]
    entries = [item for item in entries if item.get("run_id") != summary.get("run_id")]
    entries.append(summary)
    entries = sorted(
        entries,
        key=lambda item: (str(item.get("time") or ""), str(item.get("run_id") or "")),
        reverse=True,
    )
    expected_revision = int(registry.get("revision") or 0)
    value = {
        "version": 2,
        "revision": expected_revision + 1,
        "history_truncated": registry.get("history_truncated") is True or len(entries) > 300,
        "runs": entries[:300],
    }
    if not blob_configured():
        _write_local_registry(value)
        return value
    return compare_and_swap_blob_json(
        RUN_REGISTRY_BLOB,
        expected_revision=expected_revision,
        changes=value,
    )


def _write_run_file(path: Path, run: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _replace_run(target: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    target.clear()
    target.update(value)
    return target


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
        "workspace_generation": run.get("workspace_generation"),
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
        "canonical_lineage_commit_id": run.get("canonical_lineage_commit_id"),
        "canonical_target_commit_id": run.get("canonical_target_commit_id"),
        "canonical_target_content_sha256": run.get("canonical_target_content_sha256"),
        "canonical_lineage_content_sha256": run.get("canonical_lineage_content_sha256"),
        "canonical_lineage_sequence": run.get("canonical_lineage_sequence"),
        "attachment_commit_status": run.get("attachment_commit_status"),
        "attachment_commit_id": run.get("attachment_commit_id"),
        "attachment_payload_sha256": run.get("attachment_payload_sha256"),
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
    trace = _safe_trace_reference(run.get("trace"))
    if trace:
        summary["trace"] = trace
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
    trace = _safe_trace_reference(normalized.get("trace"))
    if trace:
        normalized["trace"] = trace
    else:
        normalized.pop("trace", None)
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


def _safe_trace_reference(value: Any, agent_id: Any | None = None) -> dict[str, str] | None:
    if isinstance(value, dict):
        trace_id = value.get("trace_id")
        resolved_agent_id = value.get("agent_id")
    else:
        trace_id = value
        resolved_agent_id = agent_id
    safe_trace_id = str(trace_id or "").strip().lower()
    safe_agent_id = str(resolved_agent_id or "").strip()
    if not _TRACE_REFERENCE_ID.fullmatch(safe_trace_id):
        return None
    if not _TRACE_REFERENCE_AGENT.fullmatch(safe_agent_id):
        return None
    return {"trace_id": safe_trace_id, "agent_id": safe_agent_id}


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
    context_pack = artifact.get("context_pack")
    if isinstance(context_pack, dict):
        artifact["context_pack"] = _sanitize_context_pack_metadata(context_pack)
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
    if event == "model_response" and isinstance(data, dict):
        return _sanitize_model_response_event_data(data)
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


def _sanitize_model_response_event_data(data: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(data)
    sanitized.pop("provider_usage", None)
    sanitized["usage"] = _normalized_observed_usage(data.get("usage"))
    if "cost_estimate" in data:
        sanitized["cost_estimate"] = _safe_cost_estimate(data.get("cost_estimate"))
    if "cache" in data:
        cache = normalize_cache_meter(data.get("cache"))
        if cache:
            sanitized["cache"] = cache
        else:
            sanitized.pop("cache", None)
    if "result_cache" in data:
        result_cache = normalize_cache_meter(data.get("result_cache"))
        if result_cache:
            sanitized["result_cache"] = result_cache
        else:
            sanitized.pop("result_cache", None)
    if "provider_cache" in data:
        provider_cache = normalize_provider_cache_meter(data.get("provider_cache"))
        if provider_cache:
            sanitized["provider_cache"] = provider_cache
        else:
            sanitized.pop("provider_cache", None)
    for key in ("provider_type", "provider_id", "model_id", "route_evidence", "provenance"):
        safe_value = _safe_model_observation_value(key, data.get(key))
        if safe_value is None:
            sanitized.pop(key, None)
        else:
            sanitized[key] = safe_value
    for key in ("request_ref", "correlation_ref", "attempt_ref", "result_id"):
        reference = _safe_model_reference(data.get(key))
        if reference is None:
            sanitized.pop(key, None)
        else:
            sanitized[key] = reference
    return sanitized


def normalize_cache_meter(value: Any) -> dict[str, Any]:
    """Return the cache fields that are safe to persist with a model event."""
    data = dict(value) if isinstance(value, dict) else {}
    state = str(data.get("state") or "").strip().lower()
    if state not in {"hit", "miss", "unavailable", "bypassed"}:
        return {}
    provider = str(data.get("provider") or "").strip().lower()
    if provider != "redis":
        return {}
    safe = {"state": state, "provider": provider}
    if isinstance(data.get("eligible"), bool):
        safe["eligible"] = data["eligible"]
    allowed_reasons = {
        "eligible",
        "disabled",
        "live_data",
        "side_effecting_tools",
        "unstable_conversation",
        "data_revision_missing",
        "lookup_unavailable",
        "not_recorded",
    }
    reason = str(data.get("reason") or "").strip().lower()
    if reason in allowed_reasons:
        safe["reason"] = reason
    policy_revision = data.get("policy_revision")
    if (
        isinstance(policy_revision, int)
        and not isinstance(policy_revision, bool)
        and policy_revision >= 0
    ):
        safe["policy_revision"] = policy_revision
    source_result_version = str(
        data.get("source_result_version") or ""
    ).strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", source_result_version):
        safe["source_result_version"] = source_result_version
    elapsed_ms = data.get("elapsed_ms")
    if isinstance(elapsed_ms, (int, float)) and not isinstance(elapsed_ms, bool):
        try:
            elapsed = int(elapsed_ms)
        except (OverflowError, ValueError):
            elapsed = -1
        if elapsed >= 0:
            safe["elapsed_ms"] = elapsed
    if state == "hit":
        source_usage = _normalized_observed_usage(data.get("source_usage"))
        if source_usage:
            safe["source_usage"] = source_usage
        source_cost = _safe_cost_estimate(data.get("source_cost_estimate"))
        if source_cost.get("status") == "estimated":
            source_cost.pop("formula", None)
            safe["source_cost_estimate"] = source_cost
    return safe


def normalize_provider_cache_meter(value: Any) -> dict[str, Any]:
    data = dict(value) if isinstance(value, dict) else {}
    state = str(data.get("state") or "").strip().lower()
    if state not in {"hit", "partial_hit", "miss", "unavailable"}:
        return {}
    hit = finite_nonnegative_integral_token_count(data.get("hit_tokens"))
    miss = finite_nonnegative_integral_token_count(data.get("miss_tokens"))
    synthetic = str(data.get("evidence_state") or "").strip().lower() == "synthetic"
    evidence_state = (
        "synthetic" if synthetic and hit is not None and miss is not None else "observed"
        if hit is not None and miss is not None
        else "partial"
        if hit is not None or miss is not None
        else "unavailable"
    )
    denominator = (hit or 0) + (miss or 0) if hit is not None and miss is not None else 0
    return {
        "state": state if hit is not None and miss is not None else "unavailable",
        "hit_tokens": hit,
        "miss_tokens": miss,
        "hit_rate_pct": round((hit or 0) / denominator * 100, 2) if denominator else None,
        "evidence_state": evidence_state,
    }


def _safe_cost_estimate(value: Any) -> dict[str, Any]:
    data = dict(value) if isinstance(value, dict) else {}
    status = str(data.get("status") or "unavailable").strip().lower()
    if status != "estimated":
        reason = str(data.get("reason") or "price_not_configured").strip().lower()
        return {"status": "unavailable", "reason": reason if reason in {"usage_not_recorded", "price_not_configured"} else "price_not_configured"}
    amount = data.get("amount")
    revision = data.get("price_card_revision")
    route_id = str(data.get("route_id") or "").strip().lower()
    currency = str(data.get("currency") or "").strip().upper()
    if (
        not isinstance(amount, (int, float))
        or isinstance(amount, bool)
        or amount < 0
        or not (
            (isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0)
            or (isinstance(revision, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", revision))
        )
        or not (re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", route_id) or str(data.get("official_price_key") or "").strip())
        or not re.fullmatch(r"[A-Z]{3}", currency)
    ):
        return {"status": "unavailable", "reason": "price_not_configured"}
    safe = {
        "status": "estimated",
        "currency": currency,
        "amount": round(float(amount), 6),
        "price_card_revision": revision,
        **({"route_id": route_id} if route_id else {}),
        "formula": "input_tokens/1_000_000*input_per_million + output_tokens/1_000_000*output_per_million",
    }
    key = str(data.get("official_price_key") or "").strip()
    if key and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,239}", key):
        safe["official_price_key"] = key
    return safe


def _sanitize_context_pack_metadata(data: Any) -> dict[str, Any]:
    return public_context_pack_metadata(data)


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
        finite_nonnegative_integral_token_count(usage.get(key)) is not None
        for key in (
            "prompt_tokens",
            "input_tokens",
            "completion_tokens",
            "output_tokens",
            "total_tokens",
            "prompt",
            "completion",
            "total",
        )
    )


def _usage_from_dict(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") if "usage" in data else data
    if not isinstance(usage, dict):
        return {"total": 0, "prompt": 0, "completion": 0}
    prompt = _first_valid_token_count(usage, "prompt_tokens", "input_tokens", "prompt") or 0
    completion = _first_valid_token_count(usage, "completion_tokens", "output_tokens", "completion") or 0
    total = _first_valid_token_count(usage, "total_tokens", "total")
    if total is None:
        total = prompt + completion
    return {"total": total, "prompt": prompt, "completion": completion}


def _first_valid_token_count(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        token_count = finite_nonnegative_integral_token_count(usage.get(key))
        if token_count is not None:
            return token_count
    return None


def _safe_model_observation_value(key: str, value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if key == "provider_type":
        normalized = text.lower()
        return normalized if normalized in {"azure_foundry", "deepseek"} else None
    if key == "provider_id":
        try:
            from .model_references import safe_configured_provider_ref
        except ImportError:
            from model_references import safe_configured_provider_ref
        return safe_configured_provider_ref(text)
    if key == "model_id":
        return text if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", text) else None
    if key == "route_evidence":
        normalized = text.lower()
        return normalized if normalized in {"observed", "selected", "inferred", "synthetic", "unavailable"} else None
    if key == "provenance":
        normalized = text.lower()
        return normalized if normalized in {"runtime", "synthetic_demo"} else None
    return None


def _safe_model_reference(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{7,159}", text) else None


def _normalized_observed_usage(data: Any) -> dict[str, int | None]:
    usage = data.get("usage") if isinstance(data, dict) and "usage" in data else data
    if not isinstance(usage, dict):
        return {}
    source_pairs = {
        "prompt": ("prompt", "input_tokens"),
        "completion": ("completion", "output_tokens"),
        "total": ("total", "total_tokens"),
        "cached_input": ("cached_input",),
        "reasoning": ("reasoning", "reasoning_tokens"),
    }
    if not any(source in usage for sources in source_pairs.values() for source in sources):
        return {}
    normalized: dict[str, int | None] = {}
    for target, sources in source_pairs.items():
        if target in {"cached_input", "reasoning"} and not any(
            source in usage for source in sources
        ):
            continue
        normalized[target] = None
        for source in sources:
            value = usage.get(source)
            token_count = finite_nonnegative_integral_token_count(value)
            if token_count is not None:
                normalized[target] = token_count
                break
    return normalized


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
