from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .blob_store import download_blob_json, upload_blob_json
    from .outcome_store import list_outcome_events, outcome_is_authoritative
except ImportError:
    from blob_store import download_blob_json, upload_blob_json
    from outcome_store import list_outcome_events, outcome_is_authoritative


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = ROOT / "generated-outputs" / "experiments"
EXPERIMENT_BLOB_PREFIX = "experiments"

_LOCK = threading.RLock()
_LINEAGE_REPOSITORY_PROVIDER: Any | None = None
_VERDICT_RANK = {
    "insufficient_evidence": 0,
    "not_yet_feasible": 1,
    "not_feasible": 1,
    "conditional": 2,
    "feasible": 3,
    "recommended": 3,
}
_CONFIDENCE_RANK = {
    "speculative": 0,
    "market_inferred": 1,
    "data_confirmed": 2,
}
_SOURCE_KEYS = (
    "file_id",
    "file_version",
    "connector_id",
    "connector_version",
    "run_id",
    "artifact_id",
    "query_hash",
    "table_name",
)
_TRACEABLE_SOURCE_TYPES = {"corpus", "computed", "workspace_computed"}
_EVIDENCE_STRUCTURED_FIELDS = ("value", "unit", "status", "direction", "polarity")


def build_experiment_ledger(
    workspace_id: str,
    runs: list[dict[str, Any]],
    *,
    outcomes: list[dict[str, Any]] | None = None,
    registry_state: dict[str, Any] | None = None,
    authoritative_versions: Sequence[Any] | None = None,
    authoritative_attachments: Sequence[Any] | None = None,
    authoritative_generation: int | None = None,
) -> dict[str, Any]:
    normalized_workspace = str(workspace_id or "").strip()
    if not normalized_workspace:
        raise ValueError("workspace_id is required")
    if authoritative_versions is not None:
        return _build_sql_experiment_ledger(
            normalized_workspace,
            runs,
            authoritative_versions,
            outcomes=outcomes,
            authoritative_attachments=authoritative_attachments or (),
            authoritative_generation=authoritative_generation,
        )
    if registry_state is None:
        try:
            from .run_store import authoritative_run_registry
        except ImportError:
            from run_store import authoritative_run_registry
        registry_state = authoritative_run_registry(normalized_workspace)
    ordered = sorted(
        [
            item
            for item in runs
            if isinstance(item, dict) and str(item.get("workspace_id") or "") == normalized_workspace
        ],
        key=_run_order_key,
    )
    analysis_runs = [item for item in ordered if _is_analysis_version(item)]
    snapshots = [item for item in ordered if str(item.get("version_kind") or "") in {"plan_draft", "artifact_generation"}]
    runs_by_id = {
        str(item.get("run_id") or item.get("conversation_id") or "").strip(): item
        for item in ordered
        if str(item.get("run_id") or item.get("conversation_id") or "").strip()
    }
    outcome_items = [item for item in (outcomes or []) if isinstance(item, dict)]
    versions: list[dict[str, Any]] = []
    version_aliases: dict[str, str] = {}
    unresolved_lineage: set[str] = (
        {"lineage-storage-unavailable"}
        if registry_state.get("read_status") == "error"
        else set()
    )

    for run in analysis_runs:
        run_id = str(run.get("run_id") or run.get("conversation_id") or "").strip()
        canonical_run_id = str(run.get("canonical_experiment_run_id") or "").strip()
        if _has_lineage_metadata(run):
            trusted_target = _trusted_lineage_target(normalized_workspace, run, runs_by_id, registry_state)
            if trusted_target is None:
                unresolved_lineage.add(run_id)
                version_aliases[run_id] = ""
                continue
            canonical_run_id = trusted_target
        elif registry_state.get("history_truncated") is True:
            unresolved_lineage.add(run_id)
            version_aliases[run_id] = ""
            continue
        if str(run.get("canonical_resolution_status") or "").strip().lower() == "unresolved":
            unresolved_lineage.add(run_id)
            version_aliases[run_id] = ""
            continue
        if canonical_run_id and canonical_run_id != run_id:
            version_aliases[run_id] = canonical_run_id
            continue
        artifact = _artifact(run)
        feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
        evidence, unverifiable_evidence = _evidence_snapshot(artifact, feasibility)
        dimension_evidence = _dimension_evidence_keys(feasibility)
        metrics = _metrics(artifact.get("iteration_inputs") or run.get("iteration_inputs") or [])
        linked_outcomes = _outcomes_for_analysis(outcome_items, run, set(version_aliases) | {run_id})
        metrics.extend(_outcome_metrics(linked_outcomes, existing=metrics, workspace_id=normalized_workspace))
        model_verdict = _normalized_token(feasibility.get("verdict") or run.get("verdict"))
        decision = {
            "opportunity_id": _normalized_text(feasibility.get("opportunity_id")) or None,
            "model_verdict": model_verdict or None,
            "verdict": model_verdict or None,
            "confidence": _normalized_token(
                feasibility.get("overall_confidence") or feasibility.get("confidence") or run.get("confidence")
            ) or None,
            "dimensions": _dimension_decisions(feasibility),
            "gaps": sorted({_normalized_text(item) for item in (feasibility.get("gap_list") or []) if _normalized_text(item)}),
        }
        previous = versions[-1] if versions else None
        evidence_delta = _evidence_delta(
            previous.get("evidence") if previous else [],
            evidence,
            unverifiable=unverifiable_evidence,
        )
        metric_delta = _metric_delta(previous.get("metrics") if previous else [], metrics)
        supports_strengthening = False
        if previous:
            authoritative_dimensions = _authoritative_dimension_changes(
                dimension_evidence,
                evidence_delta,
            )
            supports_strengthening = bool(authoritative_dimensions)
            decision = _guard_decision_strengthening(
                previous.get("decision") or {},
                decision,
                supports_strengthening=supports_strengthening,
                authoritative_dimensions=authoritative_dimensions,
            )
        authoritative_change = bool(
            evidence_delta["added"]
            or evidence_delta["removed"]
            or evidence_delta["contradicted"]
            or evidence_delta["strengthened"]
            or metric_delta["added"]
        )
        if previous and not authoritative_change and (
            metric_delta["non_authoritative"] or unverifiable_evidence
        ):
            decision = _retain_previous_decision(previous.get("decision") or {}, decision)

        persisted_ordinal = int(run.get("_canonical_ordinal") or 0)
        ordinal = persisted_ordinal if persisted_ordinal > 0 else len(versions) + 1
        version = {
            "version_id": f"version:{run_id}",
            "label": f"V{ordinal}",
            "ordinal": ordinal,
            "workspace_id": normalized_workspace,
            "run_id": run_id,
            "created_at": run.get("completed_at") or run.get("updated_at") or run.get("started_at"),
            "title": run.get("title") or feasibility.get("opportunity_id") or f"Version {ordinal}",
            "hypothesis": _normalized_text(feasibility.get("opportunity_id")) or None,
            "decision": decision,
            "evidence": evidence,
            "unverifiable_evidence": unverifiable_evidence,
            "metrics": metrics,
            "gaps": decision["gaps"],
            "attachments": {"plans": [], "artifacts": []},
            "evidence_delta": evidence_delta,
            "metric_delta": metric_delta,
            "evidence_changed": bool(
                evidence_delta["added"]
                or evidence_delta["removed"]
                or evidence_delta["strengthened"]
                or evidence_delta["contradicted"]
                or metric_delta["added"]
            ),
        }
        version["decision_delta"] = _decision_delta(previous, version)
        should_promote = bool(
            persisted_ordinal > 0
            or previous is None
            or version["evidence_changed"]
            or version["decision_delta"]["changes"]
        )
        if should_promote:
            versions.append(version)
            version_aliases[run_id] = run_id
        else:
            version_aliases[run_id] = str(previous.get("run_id") or "")

    _attach_snapshots(versions, snapshots, version_aliases)

    lineage_resolution = {
        "status": "unavailable" if unresolved_lineage else "resolved",
        **({"unresolved_run_ids": sorted(unresolved_lineage)[:20]} if unresolved_lineage else {}),
    }
    return {
        "version": 1,
        "workspace_id": normalized_workspace,
        "generated_at": _now(),
        "versions": versions,
        "count": len(versions),
        "latest_version_id": versions[-1]["version_id"] if versions else None,
        "lineage_resolution": lineage_resolution,
        "source": "run_store_plus_outcome_ledger",
    }


def resolve_canonical_experiment_run_id(
    workspace_id: str,
    runs: list[dict[str, Any]],
    source_run_id: str,
    *,
    outcomes: list[dict[str, Any]] | None = None,
    registry_state: dict[str, Any] | None = None,
) -> str | None:
    requested = str(source_run_id or "").strip()
    if not requested:
        return None
    ordered = sorted(
        [item for item in runs if isinstance(item, dict) and _is_analysis_version(item)],
        key=_run_order_key,
    )
    prefix: list[dict[str, Any]] = []
    for run in ordered:
        prefix.append(run)
        run_id = str(run.get("run_id") or run.get("conversation_id") or "").strip()
        if run_id != requested:
            continue
        ledger = build_experiment_ledger(
            workspace_id,
            prefix,
            outcomes=outcomes,
            registry_state=registry_state,
        )
        versions = [item for item in ledger.get("versions") or [] if isinstance(item, Mapping)]
        if not versions:
            return None
        return str(versions[-1].get("run_id") or "").strip() or None
    return None


def sync_experiment_ledger(
    workspace_id: str,
    runs: list[dict[str, Any]],
    *,
    outcomes: list[dict[str, Any]] | None = None,
    lineage_repository: Any | None = None,
    generation: int | None = None,
) -> dict[str, Any]:
    normalized_workspace = str(workspace_id or "").strip()
    if not normalized_workspace:
        raise ValueError("workspace_id is required")
    active_generation = 1
    try:
        repository = _resolve_lineage_repository(lineage_repository)
        active_generation = int(repository.current_generation(workspace_id=normalized_workspace))
        if active_generation < 1:
            raise ValueError("invalid lineage generation")
        if generation is not None and int(generation) != active_generation:
            raise ValueError("stale lineage generation")
        authoritative_versions = repository.list_versions(
            workspace_id=normalized_workspace,
            generation=active_generation,
        )
        authoritative_attachments = repository.list_attachments(
            workspace_id=normalized_workspace,
            generation=active_generation,
        )
    except Exception:
        return {
            "version": 1,
            "workspace_id": normalized_workspace,
            "generation": active_generation,
            "generated_at": _now(),
            "versions": [],
            "count": 0,
            "latest_version_id": None,
            "lineage_resolution": {"status": "unavailable", "reason": "lineage_unavailable"},
            "source": "sql_lineage",
        }
    hydrated_runs = list(runs)
    hydrated_ids = {
        str(item.get("run_id") or item.get("conversation_id") or "")
        for item in hydrated_runs
        if isinstance(item, dict)
    }
    try:
        from .run_store import get_run
    except ImportError:
        from run_store import get_run
    for committed in authoritative_versions:
        canonical_run_id = str(_commit_value(committed, "canonical_run_id") or "")
        if not canonical_run_id or canonical_run_id in hydrated_ids:
            continue
        try:
            payload = get_run(canonical_run_id)
        except (FileNotFoundError, ValueError, KeyError):
            continue
        if isinstance(payload, dict):
            hydrated_runs.append(payload)
            hydrated_ids.add(canonical_run_id)
    ledger = build_experiment_ledger(
        normalized_workspace,
        hydrated_runs,
        outcomes=outcomes,
        authoritative_versions=authoritative_versions,
        authoritative_attachments=authoritative_attachments,
        authoritative_generation=active_generation,
    )
    with _LOCK:
        path = _local_path(normalized_workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        try:
            upload_blob_json(_blob_name(normalized_workspace), ledger)
        except Exception:
            ledger["payload_publication"] = {
                "status": "unavailable",
                "reason": "payload_publication_failed",
            }
            temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
    return ledger


def analysis_lineage_fingerprints(run: Mapping[str, Any]) -> tuple[str, str]:
    """Fingerprint only evidence-backed decision data used for SQL promotion."""
    artifact = _artifact(run)
    feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), Mapping) else {}
    evidence, _unverifiable = _evidence_snapshot(artifact, feasibility)
    evidence_delta = _evidence_delta([], evidence)
    authoritative_dimensions = _authoritative_dimension_changes(
        _dimension_evidence_keys(feasibility),
        evidence_delta,
    )
    decision = _guard_decision_strengthening(
        {},
        {
            "model_verdict": _normalized_token(feasibility.get("verdict")) or None,
            "verdict": _normalized_token(feasibility.get("verdict")) or None,
            "confidence": _normalized_token(
                feasibility.get("overall_confidence") or feasibility.get("confidence")
            )
            or None,
            "dimensions": _dimension_decisions(feasibility),
        },
        supports_strengthening=bool(authoritative_dimensions),
        authoritative_dimensions=authoritative_dimensions,
    )
    decision_projection = {
        "verdict": _normalized_decision_field("verdict", decision.get("verdict")),
        "confidence": _normalized_decision_field("confidence", decision.get("confidence")),
        "dimensions": _normalized_decision_field("dimensions", decision.get("dimensions")),
    }
    evidence_projection = {
        "evidence": evidence,
        "authoritative_observed_metrics": _authoritative_observed_metrics(run),
    }
    return _stable_sha256(decision_projection), _stable_sha256(evidence_projection)


def _authoritative_observed_metrics(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    workspace_id = str(run.get("workspace_id") or "").strip()
    run_id = str(run.get("run_id") or run.get("conversation_id") or "").strip()
    if not workspace_id or not run_id:
        return []
    try:
        outcomes = list_outcome_events(workspace_id)
    except Exception:
        return []
    linked = _outcomes_for_analysis(
        [item for item in outcomes if isinstance(item, dict)],
        run,
        {run_id},
    )
    metrics = _outcome_metrics(linked, existing=[], workspace_id=workspace_id)
    projection = [
        {
            "metric_name": item.get("metric_name"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "source": item.get("source"),
            "verification": item.get("verification"),
            "observed_at": item.get("observed_at"),
        }
        for item in metrics
        if item.get("provenance") == "observed" and item.get("source")
    ]
    return sorted(projection, key=_metric_key)


def _build_sql_experiment_ledger(
    workspace_id: str,
    runs: list[dict[str, Any]],
    authoritative_versions: Sequence[Any],
    *,
    outcomes: list[dict[str, Any]] | None,
    authoritative_attachments: Sequence[Any],
    authoritative_generation: int | None,
) -> dict[str, Any]:
    ordered_commits = sorted(authoritative_versions, key=lambda item: int(_commit_value(item, "ordinal") or 0))
    runs_by_id = {
        str(item.get("run_id") or item.get("conversation_id") or ""): item
        for item in runs
        if isinstance(item, dict)
    }
    canonical_payloads: list[dict[str, Any]] = []
    for commit in ordered_commits:
        run_id = str(_commit_value(commit, "canonical_run_id") or "")
        payload = runs_by_id.get(run_id)
        if not isinstance(payload, dict):
            continue
        if str((payload.get("persistence") or {}).get("payload_state") or "") == "unavailable":
            continue
        hydrated = copy.deepcopy(payload)
        for key in (
            "canonical_experiment_run_id",
            "canonical_experiment_version_id",
            "canonical_resolution_status",
            "canonical_lineage_status",
            "canonical_lineage_commit_id",
            "canonical_target_commit_id",
            "canonical_target_content_sha256",
            "canonical_lineage_content_sha256",
            "canonical_lineage_sequence",
        ):
            hydrated.pop(key, None)
        hydrated["_canonical_ordinal"] = int(_commit_value(commit, "ordinal") or 0)
        canonical_payloads.append(hydrated)

    hydrated_ledger = build_experiment_ledger(
        workspace_id,
        canonical_payloads,
        outcomes=outcomes,
        registry_state={"history_truncated": False},
    )
    hydrated_by_run = {
        str(item.get("run_id") or ""): item
        for item in hydrated_ledger.get("versions") or []
        if isinstance(item, dict)
    }
    versions: list[dict[str, Any]] = []
    unavailable_run_ids: list[str] = []
    for commit in ordered_commits:
        run_id = str(_commit_value(commit, "canonical_run_id") or "")
        version_id = str(_commit_value(commit, "version_id") or "")
        ordinal = int(_commit_value(commit, "ordinal") or 0)
        version = copy.deepcopy(hydrated_by_run.get(run_id) or {})
        if not version:
            unavailable_run_ids.append(run_id)
            version = {
                "workspace_id": workspace_id,
                "run_id": run_id,
                "decision": {},
                "evidence": [],
                "metrics": [],
                "attachments": {"plans": [], "artifacts": []},
                "payload": {"status": "unavailable"},
            }
        else:
            version["payload"] = {"status": "available"}
        version.update(
            {
                "version_id": version_id,
                "ordinal": ordinal,
                "label": f"V{ordinal}",
                "workspace_id": workspace_id,
                "run_id": run_id,
                "generation": int(_commit_value(commit, "generation") or 0),
            }
        )
        versions.append(version)

    by_version_id = {str(item.get("version_id") or ""): item for item in versions}
    attachments_by_key = {
        (
            str(_commit_value(item, "version_id") or ""),
            str(_commit_value(item, "kind") or ""),
            str(_commit_value(item, "source_run_id") or ""),
            str(_commit_value(item, "payload_sha256") or ""),
        ): item
        for item in authoritative_attachments
    }
    hydrated_attachment_ids: set[str] = set()
    invalid_snapshot_ids: list[str] = []
    for snapshot in runs:
        if not isinstance(snapshot, dict) or str(snapshot.get("version_kind") or "") not in {
            "plan_draft",
            "artifact_generation",
        }:
            continue
        target = by_version_id.get(str(snapshot.get("experiment_version_id") or ""))
        if target is None:
            continue
        if str((snapshot.get("persistence") or {}).get("payload_state") or "") == "unavailable":
            invalid_snapshot_ids.append(str(snapshot.get("run_id") or ""))
            continue
        attachment = attachments_by_key.get(
            (
                str(snapshot.get("experiment_version_id") or ""),
                str(snapshot.get("version_kind") or ""),
                str(snapshot.get("source_run_id") or ""),
                _snapshot_payload_sha256(snapshot),
            )
        )
        attachment_id = str(_commit_value(attachment, "attachment_id") or "")
        if attachment is None or not attachment_id:
            invalid_snapshot_ids.append(str(snapshot.get("run_id") or ""))
            continue
        if attachment_id in hydrated_attachment_ids:
            continue
        hydrated_attachment_ids.add(attachment_id)
        artifact = _artifact(snapshot)
        if snapshot.get("version_kind") == "plan_draft":
            draft = artifact.get("plan_draft") if isinstance(artifact.get("plan_draft"), dict) else {}
            target["attachments"]["plans"].append(
                {"run_id": snapshot.get("run_id"), "text": draft.get("text"), "created_at": snapshot.get("completed_at")}
            )
        elif snapshot.get("version_kind") == "artifact_generation":
            proposal = artifact.get("proposal") if isinstance(artifact.get("proposal"), dict) else {}
            target["attachments"]["artifacts"].append(
                {"run_id": snapshot.get("run_id"), "urls": proposal.get("artifact_urls") or {}, "created_at": snapshot.get("completed_at")}
            )

    generation = int(authoritative_generation or 0)
    if generation < 1:
        generation = int(_commit_value(ordered_commits[-1], "generation") or 1) if ordered_commits else 1
    return {
        "version": 1,
        "workspace_id": workspace_id,
        "generation": generation,
        "generated_at": _now(),
        "versions": versions,
        "count": len(versions),
        "latest_version_id": versions[-1]["version_id"] if versions else None,
        "lineage_resolution": {
            "status": "unavailable" if unavailable_run_ids or invalid_snapshot_ids else "resolved",
            **(
                {"unresolved_run_ids": sorted(set(unavailable_run_ids + invalid_snapshot_ids))[:20]}
                if unavailable_run_ids or invalid_snapshot_ids
                else {}
            ),
        },
        "source": "sql_lineage",
    }


def _commit_value(commit: Any, name: str) -> Any:
    return commit.get(name) if isinstance(commit, Mapping) else getattr(commit, name, None)


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _snapshot_payload_sha256(snapshot: Mapping[str, Any]) -> str:
    artifact = snapshot.get("artifact") if isinstance(snapshot.get("artifact"), Mapping) else {}
    return hashlib.sha256(
        json.dumps(
            {
                "workspace_id": str(snapshot.get("workspace_id") or ""),
                "run_id": str(snapshot.get("run_id") or ""),
                "source_run_id": str(snapshot.get("source_run_id") or ""),
                "experiment_version_id": str(snapshot.get("experiment_version_id") or ""),
                "version_kind": str(snapshot.get("version_kind") or ""),
                "produced_kinds": snapshot.get("produced_kinds")
                if isinstance(snapshot.get("produced_kinds"), list)
                else [],
                "artifact": artifact,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


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


def _has_lineage_metadata(run: Mapping[str, Any]) -> bool:
    return any(
        key in run
        for key in (
            "canonical_experiment_run_id",
            "canonical_experiment_version_id",
            "canonical_resolution_status",
            "canonical_lineage_status",
        )
    )


def _trusted_lineage_target(
    workspace_id: str,
    run: dict[str, Any],
    runs_by_id: dict[str, dict[str, Any]],
    registry_state: dict[str, Any],
) -> str | None:
    try:
        from .run_store import trusted_canonical_experiment_run_id
    except ImportError:
        from run_store import trusted_canonical_experiment_run_id
    return trusted_canonical_experiment_run_id(workspace_id, run, runs_by_id, registry_state)


def load_experiment_ledger(workspace_id: str) -> dict[str, Any]:
    path = _local_path(workspace_id)
    local: dict[str, Any] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            local = parsed if isinstance(parsed, dict) else {}
        except (OSError, json.JSONDecodeError):
            local = {}
    remote = download_blob_json(_blob_name(workspace_id)) or {}
    return remote if str(remote.get("generated_at") or "") > str(local.get("generated_at") or "") else local


def compare_experiment_versions(ledger: Mapping[str, Any], from_id: str, to_id: str) -> dict[str, Any]:
    by_id = {
        str(item.get("version_id") or item.get("run_id") or ""): item
        for item in ledger.get("versions") or []
        if isinstance(item, dict)
    }
    source = by_id.get(str(from_id)) or next((item for item in by_id.values() if item.get("run_id") == from_id), None)
    target = by_id.get(str(to_id)) or next((item for item in by_id.values() if item.get("run_id") == to_id), None)
    if source is None or target is None:
        raise FileNotFoundError("experiment version not found")
    return {
        "from": source,
        "to": target,
        "evidence_delta": _evidence_delta(
            source.get("evidence") or [],
            target.get("evidence") or [],
            unverifiable=target.get("unverifiable_evidence") or [],
        ),
        "decision_delta": _decision_delta(source, target),
    }


def _is_analysis_version(run: Mapping[str, Any]) -> bool:
    if str(run.get("version_kind") or ""):
        return False
    if not str(run.get("status") or "").strip().lower().startswith("completed"):
        return False
    feasibility = _artifact(run).get("feasibility")
    return isinstance(feasibility, dict) and bool(feasibility.get("verdict") or feasibility.get("dimensions"))


def _artifact(run: Mapping[str, Any]) -> dict[str, Any]:
    direct = run.get("artifact")
    if isinstance(direct, dict):
        return direct
    final = run.get("final")
    nested = final.get("artifact") if isinstance(final, dict) else None
    return nested if isinstance(nested, dict) else {}


def _evidence_set(artifact: Mapping[str, Any], feasibility: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _evidence_snapshot(artifact, feasibility)[0]


def _evidence_snapshot(
    artifact: Mapping[str, Any],
    feasibility: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[Mapping[str, Any]] = []
    for dimension in feasibility.get("dimensions") or []:
        if isinstance(dimension, Mapping):
            candidates.extend(item for item in dimension.get("evidence") or [] if isinstance(item, Mapping))
    for item in artifact.get("citations") or (artifact.get("answer") or {}).get("citations") or []:
        if isinstance(item, Mapping):
            candidates.append(item)
    normalized_candidates = [_normalized_evidence(item) for item in candidates]
    normalized_candidates = [
        item
        for item in normalized_candidates
        if not _has_more_specific_evidence_identity(item, normalized_candidates)
    ]
    untrusted_keys = {
        _evidence_key(item)
        for item in normalized_candidates
        if item.get("source_type") not in _TRACEABLE_SOURCE_TYPES or not item.get("source")
    }
    by_identity: dict[str, dict[str, Any]] = {}
    unverifiable: dict[str, dict[str, Any]] = {}
    for normalized in normalized_candidates:
        ref = str(normalized.get("ref") or "")
        source_type = str(normalized.get("source_type") or "unknown")
        if not ref:
            continue
        identity = _evidence_key(normalized)
        if identity in untrusted_keys or source_type not in _TRACEABLE_SOURCE_TYPES or not normalized.get("source"):
            unverifiable[identity] = {
                **normalized,
                "reason": "Evidence source is not traceable to a stable source identity.",
            }
            continue
        current = by_identity.get(identity)
        if (
            current is None
            or _CONFIDENCE_RANK.get(str(normalized.get("confidence") or ""), 0)
            > _CONFIDENCE_RANK.get(str(current.get("confidence") or ""), 0)
        ):
            by_identity[identity] = normalized
    return (
        sorted(by_identity.values(), key=_evidence_key),
        sorted(unverifiable.values(), key=_evidence_key),
    )


def _normalized_evidence(item: Mapping[str, Any]) -> dict[str, Any]:
    ref = _evidence_ref(item)
    normalized: dict[str, Any] = {
        "ref": ref,
        "source_type": _evidence_source_type(item),
        "source": _evidence_source(item, ref),
    }
    quote = _normalized_text(item.get("quote") or item.get("snippet"))[:600]
    confidence = _normalized_token(item.get("confidence"))[:40]
    if quote:
        normalized["quote"] = quote
    if confidence:
        normalized["confidence"] = confidence
    for field in ("value", "unit"):
        value = item.get(field)
        if value in (None, ""):
            continue
        normalized[field] = _normalized_number(value) if field == "value" else _normalized_text(value)
    status = _evidence_status(item.get("status"))
    if status:
        normalized["status"] = status
    raw_direction = item.get("direction")
    direction = _evidence_orientation(_normalized_token(raw_direction)) or _normalized_token(raw_direction)
    if direction:
        normalized["direction"] = direction
    polarity = _evidence_polarity(item.get("polarity"))
    if polarity:
        normalized["polarity"] = polarity
    return normalized


def _has_more_specific_evidence_identity(
    candidate: Mapping[str, Any],
    candidates: list[dict[str, Any]],
) -> bool:
    source = _source(candidate.get("source"))
    if not source:
        return False
    for other in candidates:
        if other is candidate or other.get("ref") != candidate.get("ref"):
            continue
        other_source = _source(other.get("source"))
        if len(other_source) <= len(source):
            continue
        if all(other_source.get(key) == value for key, value in source.items()):
            return True
    return False


def _evidence_ref(item: Mapping[str, Any]) -> str:
    return _normalized_text(item.get("ref") or item.get("source_file") or item.get("marker"))[:240]


def _evidence_source_type(item: Mapping[str, Any]) -> str:
    return str(item.get("source_type") or ("corpus" if item.get("source_file") else "unknown")).strip().lower()


def _evidence_source(item: Mapping[str, Any], ref: str) -> dict[str, str]:
    nested = item.get("source") if isinstance(item.get("source"), Mapping) else {}
    combined = {**dict(nested), **{key: item.get(key) for key in _SOURCE_KEYS if item.get(key) not in (None, "")}}
    if not combined.get("file_id") and item.get("source_file"):
        combined["file_id"] = item.get("source_file")
    if not combined.get("file_version") and item.get("source_version"):
        combined["file_version"] = item.get("source_version")
    if not combined.get("file_id") and "#" in ref:
        combined["file_id"] = ref.split("#", 1)[0]
    return _source(combined)


def _evidence_key(item: Mapping[str, Any]) -> str:
    return json.dumps(
        {"ref": str(item.get("ref") or ""), "source": _source(item.get("source"))},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _metrics(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    metrics: list[dict[str, Any]] = []
    for item in raw[:50]:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("metric_name") or item.get("label") or "").strip()
        if not name:
            continue
        kind = str(item.get("kind") or item.get("provenance") or "assumption").strip().lower()
        source = _source(item.get("source"))
        verification = _verification(item.get("verification"))
        provenance = (
            "synthetic"
            if kind == "synthetic" or item.get("provenance") == "synthetic"
            else "reported_unverified"
            if kind == "observed"
            else "target"
            if kind == "target"
            else "assumption"
        )
        metrics.append(
            {
                "metric_name": name[:120],
                "value": item.get("value"),
                "unit": str(item.get("unit") or "")[:40] or None,
                "kind": kind,
                "provenance": provenance,
                "source": source or None,
                "verification": verification or None,
            }
        )
    return [{key: value for key, value in item.items() if value is not None} for item in metrics]


def _outcomes_for_analysis(
    outcomes: list[dict[str, Any]],
    run: Mapping[str, Any],
    lineage_run_ids: set[str],
) -> list[dict[str, Any]]:
    completed_at = str(run.get("completed_at") or run.get("updated_at") or "")
    return [
        item
        for item in outcomes
        if isinstance(item.get("source"), dict)
        and str(item["source"].get("run_id") or "") in lineage_run_ids
        and _recorded_before(item, completed_at)
    ]


def _recorded_before(item: Mapping[str, Any], completed_at: str) -> bool:
    recorded_at = str(item.get("created_at") or item.get("observed_at") or "").strip()
    if not recorded_at:
        return True
    return bool(completed_at and recorded_at <= completed_at)


def _outcome_metrics(
    outcomes: list[dict[str, Any]],
    *,
    existing: list[dict[str, Any]],
    workspace_id: str = "",
) -> list[dict[str, Any]]:
    keys = {_metric_key(item) for item in existing}
    result: list[dict[str, Any]] = []
    for item in outcomes:
        kind = str(item.get("provenance") or "assumption").strip().lower()
        if kind not in {"observed", "synthetic", "target", "assumption"}:
            kind = "assumption"
        source = _source(item.get("source"))
        verification = _verification(item.get("verification"))
        authoritative = kind == "observed" and outcome_is_authoritative(workspace_id, item)
        provenance = (
            "observed"
            if authoritative
            else "reported_unverified"
            if kind == "observed"
            else kind
        )
        value = item.get("observed_value")
        if kind == "target" and item.get("target_value") is not None:
            value = item.get("target_value")
        elif kind == "assumption" and item.get("baseline_value") is not None:
            value = item.get("baseline_value")
        metric = {
            "metric_name": _normalized_text(item.get("metric_name"))[:120],
            "value": value,
            "unit": _normalized_text(item.get("unit"))[:40] or None,
            "kind": kind,
            "provenance": provenance,
            "source": source,
            "verification": verification,
            "observed_at": item.get("observed_at"),
        }
        key = _metric_key(metric)
        if not metric["metric_name"] or not metric["source"] or key in keys:
            continue
        result.append({field: value for field, value in metric.items() if value is not None})
        keys.add(key)
    return result


def _verification(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    status = _normalized_text(value.get("status")).lower()[:32]
    result: dict[str, Any] = {"status": status} if status else {}
    for key in ("verification_event_id", "verified_at"):
        text = _normalized_text(value.get(key))[:120]
        if text:
            result[key] = text
    if value.get("trusted_identity") is True:
        result["trusted_identity"] = True
    reviewer = value.get("reviewer") if isinstance(value.get("reviewer"), Mapping) else {}
    reviewer_label = _normalized_text(reviewer.get("subject_label"))[:120]
    if reviewer_label:
        result["reviewer"] = {"subject_label": reviewer_label}
    event = value.get("event") if isinstance(value.get("event"), Mapping) else {}
    if event:
        safe_event = {
            key: event.get(key)
            for key in ("event_id", "workspace_id", "kind", "outcome_event_id", "created_at")
            if event.get(key) not in (None, "")
        }
        actor = event.get("actor") if isinstance(event.get("actor"), Mapping) else {}
        actor_label = _normalized_text(actor.get("subject_label"))[:120]
        if actor_label:
            safe_event["actor"] = {"subject_label": actor_label}
        if safe_event:
            result["event"] = safe_event
    return result


def _verification_passed(value: Any) -> bool:
    verification = _verification(value)
    return verification.get("status") in {"verified", "passed"}


def _source(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: str(value.get(key))[:240]
        for key in _SOURCE_KEYS
        if value.get(key) not in (None, "")
    }


def _dimension_decisions(feasibility: Mapping[str, Any]) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []
    for item in feasibility.get("dimensions") or []:
        if not isinstance(item, Mapping) or not _normalized_text(item.get("name")):
            continue
        normalized = {
            "name": _normalized_text(item.get("name")),
            **({"score": _normalized_number(item.get("score"))} if item.get("score") is not None else {}),
            **({"confidence": _normalized_token(item.get("confidence"))} if _normalized_token(item.get("confidence")) else {}),
        }
        dimensions.append(normalized)
    return sorted(dimensions, key=lambda item: str(item.get("name") or ""))


def _dimension_evidence_keys(feasibility: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for dimension in feasibility.get("dimensions") or []:
        if not isinstance(dimension, Mapping):
            continue
        name = _dimension_identity(dimension.get("name"))
        if not name:
            continue
        keys: set[str] = set()
        for item in dimension.get("evidence") or []:
            if not isinstance(item, Mapping):
                continue
            normalized = _normalized_evidence(item)
            if (
                normalized.get("ref")
                and normalized.get("source")
                and normalized.get("source_type") in _TRACEABLE_SOURCE_TYPES
            ):
                keys.add(_evidence_key(normalized))
        result[name] = keys
    return result


def _authoritative_dimension_changes(
    current: Mapping[str, set[str]],
    delta: Mapping[str, list[dict[str, Any]]],
) -> set[str]:
    changed_keys = {
        _evidence_key(item)
        for category in ("added", "strengthened")
        for item in delta.get(category) or []
        if isinstance(item, Mapping)
        and (
            item.get("change_class") == "favorable"
            or (category == "strengthened" and item.get("change_class") in (None, "favorable"))
        )
    }
    return {
        name
        for name, keys in current.items()
        if keys.intersection(changed_keys)
    }


def _attach_snapshots(
    versions: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    aliases: Mapping[str, str],
) -> None:
    by_run_id = {str(item.get("run_id") or ""): item for item in versions}
    for item in snapshots:
        declared_version_id = str(item.get("experiment_version_id") or "")
        declared_run_id = declared_version_id.removeprefix("version:") if declared_version_id.startswith("version:") else ""
        source_run_id = str(item.get("source_run_id") or item.get("conversation_id") or "")
        target_run_id = aliases.get(declared_run_id or source_run_id, declared_run_id or source_run_id)
        target = by_run_id.get(target_run_id)
        if target is None:
            continue
        artifact = _artifact(item)
        if item.get("version_kind") == "plan_draft":
            draft = artifact.get("plan_draft") if isinstance(artifact.get("plan_draft"), dict) else {}
            target["attachments"]["plans"].append(
                {"run_id": item.get("run_id"), "text": draft.get("text"), "created_at": item.get("completed_at")}
            )
        elif item.get("version_kind") == "artifact_generation":
            proposal = artifact.get("proposal") if isinstance(artifact.get("proposal"), dict) else {}
            target["attachments"]["artifacts"].append(
                {"run_id": item.get("run_id"), "urls": proposal.get("artifact_urls") or {}, "created_at": item.get("completed_at")}
            )


def _evidence_delta(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    unverifiable: list[dict[str, Any]] | None = None,
) -> dict[str, list[Any]]:
    before = {_evidence_key(item): item for item in previous if item.get("ref")}
    after = {_evidence_key(item): item for item in current if item.get("ref")}
    common = sorted(set(before) & set(after))
    added = [_added_evidence_delta(after[key]) for key in sorted(set(after) - set(before))]
    removed = [
        {**before[key], "reason": f"Removed stable evidence identity: {_evidence_identity_label(before[key])}."}
        for key in sorted(set(before) - set(after))
    ]
    contradicted: list[dict[str, Any]] = []
    strengthened: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for key in common:
        prior = before[key]
        latest = after[key]
        structured_changes = [
            field
            for field in _EVIDENCE_STRUCTURED_FIELDS
            if prior.get(field) != latest.get(field)
        ]
        prior_confidence = str(prior.get("confidence") or "")
        latest_confidence = str(latest.get("confidence") or "")
        prior_rank = _CONFIDENCE_RANK.get(prior_confidence, 0)
        latest_rank = _CONFIDENCE_RANK.get(latest_confidence, 0)
        semantic_change = _evidence_semantic_change(prior, latest)
        if semantic_change:
            category, reason = semantic_change
            target = strengthened if category == "strengthened" else contradicted
            target.append(
                {
                    "ref": latest.get("ref"),
                    "source": latest.get("source") or {},
                    "before": {field: prior.get(field) for field in structured_changes},
                    "after": {field: latest.get(field) for field in structured_changes},
                    "reason": reason,
                    "change_class": "favorable" if category == "strengthened" else (
                        "conflict" if "conflict" in reason.lower() else "adverse"
                    ),
                }
            )
            continue
        if structured_changes or latest_rank < prior_rank:
            reason_parts = []
            if structured_changes:
                reason_parts.append(f"structured fields changed: {', '.join(structured_changes)}")
            if latest_rank < prior_rank:
                reason_parts.append(f"confidence decreased from {prior_confidence or 'unknown'} to {latest_confidence or 'unknown'}")
            contradicted.append(
                {
                    "ref": latest.get("ref"),
                    "source": latest.get("source") or {},
                    "before": {field: prior.get(field) for field in structured_changes} | {"confidence": prior.get("confidence")},
                    "after": {field: latest.get(field) for field in structured_changes} | {"confidence": latest.get("confidence")},
                    "reason": f"Evidence changed adversely: {'; '.join(reason_parts)}.",
                }
            )
            continue
        if latest_rank > prior_rank:
            strengthened.append(
                {
                    "ref": latest.get("ref"),
                    "source": latest.get("source") or {},
                    "before": prior.get("confidence"),
                    "after": latest.get("confidence"),
                    "reason": f"Evidence confidence increased from {prior_confidence or 'unknown'} to {latest_confidence or 'unknown'}.",
                }
            )
            continue
        unchanged.append(
            {
                **latest,
                "reason": f"Stable evidence identity and structured fields are unchanged: {_evidence_identity_label(latest)}.",
            }
        )
    unverifiable_items = []
    for item in unverifiable or []:
        reason = _normalized_text(item.get("reason"))
        unverifiable_items.append(
            {
                **item,
                "reason": reason or "Evidence cannot be verified against a stable source identity.",
            }
        )
    return {
        "added": added,
        "removed": removed,
        "contradicted": contradicted,
        "strengthened": strengthened,
        "unchanged": unchanged,
        "unverifiable": unverifiable_items,
    }


def _evidence_semantic_change(
    prior: Mapping[str, Any],
    latest: Mapping[str, Any],
) -> tuple[str, str] | None:
    signals: list[tuple[str, str]] = []
    prior_status = _evidence_status(prior.get("status"))
    latest_status = _evidence_status(latest.get("status"))
    status_rank = {"failed": 0, "passed": 1}
    if prior_status != latest_status and prior_status in status_rank and latest_status in status_rank:
        favorable = status_rank[latest_status] > status_rank[prior_status]
        adverb = "favorably" if favorable else "adversely"
        signals.append(
            (
                "favorable" if favorable else "adverse",
                f"status changed {adverb} from {prior_status} to {latest_status}",
            )
        )

    prior_polarity = _evidence_polarity(prior.get("polarity"))
    latest_polarity = _evidence_polarity(latest.get("polarity"))
    polarity_rank = {"negative": 0, "positive": 1}
    if (
        prior_polarity != latest_polarity
        and prior_polarity in polarity_rank
        and latest_polarity in polarity_rank
    ):
        favorable = polarity_rank[latest_polarity] > polarity_rank[prior_polarity]
        adverb = "favorably" if favorable else "adversely"
        signals.append(
            (
                "favorable" if favorable else "adverse",
                f"polarity changed {adverb} from {prior_polarity} to {latest_polarity}",
            )
        )

    prior_confidence = _normalized_token(prior.get("confidence"))
    latest_confidence = _normalized_token(latest.get("confidence"))
    prior_confidence_rank = _CONFIDENCE_RANK.get(prior_confidence)
    latest_confidence_rank = _CONFIDENCE_RANK.get(latest_confidence)
    if (
        prior_confidence != latest_confidence
        and prior_confidence_rank is not None
        and latest_confidence_rank is not None
    ):
        favorable = latest_confidence_rank > prior_confidence_rank
        adverb = "favorably" if favorable else "adversely"
        signals.append(
            (
                "favorable" if favorable else "adverse",
                f"confidence changed {adverb} from {prior_confidence} to {latest_confidence}",
            )
        )

    prior_direction = _evidence_orientation(_normalized_token(prior.get("direction")))
    latest_direction = _evidence_orientation(_normalized_token(latest.get("direction")))
    if prior_direction and latest_direction and prior_direction != latest_direction:
        signals.append(
            (
                "adverse",
                f"direction changed from {prior_direction}-is-better to {latest_direction}-is-better",
            )
        )

    direction = _normalized_token(latest.get("direction") or prior.get("direction"))
    orientation = _evidence_orientation(direction)
    prior_value = _decimal_value(prior.get("value"))
    latest_value = _decimal_value(latest.get("value"))
    prior_unit = _normalized_text(prior.get("unit"))
    latest_unit = _normalized_text(latest.get("unit"))
    if prior_unit != latest_unit:
        signals.append(
            (
                "adverse",
                f"unit changed from {prior_unit or 'unknown'} to {latest_unit or 'unknown'}",
            )
        )
    if not orientation and prior_value is not None and latest_value is not None and prior_value != latest_value:
        signals.append(
            (
                "adverse",
                f"value changed from {prior.get('value')} to {latest.get('value')} without a direction",
            )
        )
    if orientation and prior_value is not None and latest_value is not None and prior_value != latest_value:
        favorable = latest_value > prior_value if orientation == "higher" else latest_value < prior_value
        effect = "favorably" if favorable else "adversely"
        signals.append(
            (
                "favorable" if favorable else "adverse",
                f"value changed {effect} from {prior.get('value')} to {latest.get('value')} "
                f"with {orientation}-is-better direction",
            )
        )

    favorable_reasons = [reason for signal, reason in signals if signal == "favorable"]
    adverse_reasons = [reason for signal, reason in signals if signal == "adverse"]
    if favorable_reasons and adverse_reasons:
        return (
            "contradicted",
            f"Evidence signals conflict: {'; '.join(favorable_reasons + adverse_reasons)}.",
        )
    if adverse_reasons:
        return "contradicted", f"Evidence changed adversely: {'; '.join(adverse_reasons)}."
    if favorable_reasons:
        return "strengthened", f"Evidence strengthened: {'; '.join(favorable_reasons)}."
    return None


def _added_evidence_delta(item: Mapping[str, Any]) -> dict[str, Any]:
    favorable: list[str] = []
    adverse: list[str] = []
    status = _evidence_status(item.get("status"))
    if status == "passed":
        favorable.append("status is passed")
    elif status == "failed":
        adverse.append("status is failed")
    polarity = _evidence_polarity(item.get("polarity"))
    if polarity == "positive":
        favorable.append("polarity is positive")
    elif polarity == "negative":
        adverse.append("polarity is negative")
    if favorable and adverse:
        change_class = "conflict"
        signal_reason = f"signals conflict: {'; '.join(favorable + adverse)}"
    elif adverse:
        change_class = "adverse"
        signal_reason = f"signals are adverse: {'; '.join(adverse)}"
    elif favorable:
        change_class = "favorable"
        signal_reason = f"signals are wholly favorable: {'; '.join(favorable)}"
    else:
        change_class = "neutral"
        signal_reason = "no structured favorable status or polarity is present"
    return {
        **item,
        "change_class": change_class,
        "reason": f"Added stable evidence identity: {_evidence_identity_label(item)}; {signal_reason}.",
    }


def _evidence_orientation(value: str) -> str | None:
    if value in {"higher", "higher_is_better", "increase", "increasing", "positive", "up"}:
        return "higher"
    if value in {"lower", "lower_is_better", "decrease", "decreasing", "negative", "down"}:
        return "lower"
    return None


def _evidence_status(value: Any) -> str:
    token = _normalized_token(value)
    if token in {"pass", "passed", "verified"}:
        return "passed"
    if token in {"fail", "failed"}:
        return "failed"
    return token


def _evidence_polarity(value: Any) -> str:
    token = _normalized_token(value)
    if token in {"positive", "favorable", "supporting", "supports"}:
        return "positive"
    if token in {"negative", "adverse", "contradicting", "contradicts"}:
        return "negative"
    return token


def _evidence_identity_label(item: Mapping[str, Any]) -> str:
    source = _source(item.get("source"))
    parts = [f"ref={str(item.get('ref') or '')}"]
    parts.extend(f"{key}={source[key]}" for key in _SOURCE_KEYS if source.get(key))
    return ", ".join(parts)


def _metric_key(item: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "metric_name": _normalized_text(item.get("metric_name") or item.get("label")),
            "value": item.get("value"),
            "unit": _normalized_text(item.get("unit")),
            "provenance": str(item.get("provenance") or item.get("kind") or ""),
            "source": _source(item.get("source")),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _metric_delta(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    before = {_metric_key(item): item for item in previous if item.get("metric_name")}
    after = {_metric_key(item): item for item in current if item.get("metric_name")}
    new_keys = sorted(set(after) - set(before))
    return {
        "added": [
            after[key]
            for key in new_keys
            if after[key].get("provenance") == "observed" and after[key].get("source")
        ],
        "non_authoritative": [
            after[key]
            for key in new_keys
            if after[key].get("provenance") != "observed" or not after[key].get("source")
        ],
        "unchanged": [after[key] for key in sorted(set(after) & set(before))],
    }


def _retain_previous_decision(previous: Mapping[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    retained = dict(current)
    for field in ("opportunity_id", "verdict", "confidence", "dimensions"):
        retained[field] = previous.get(field)
    retained["verdict_guard"] = {
        "before": current.get("model_verdict") or current.get("verdict"),
        "after": previous.get("verdict"),
        "reason": "non_authoritative_feedback",
    }
    return retained


def _guard_decision_strengthening(
    previous: Mapping[str, Any],
    current: dict[str, Any],
    *,
    supports_strengthening: bool = False,
    authoritative_dimensions: set[str] | None = None,
) -> dict[str, Any]:
    guarded = dict(current)
    changed = False
    authoritative_dimensions = authoritative_dimensions or set()
    previous_verdict = str(previous.get("verdict") or "")
    model_verdict = str(current.get("model_verdict") or current.get("verdict") or "")
    if not supports_strengthening and _VERDICT_RANK.get(model_verdict, 0) > _VERDICT_RANK.get(previous_verdict, 0):
        guarded["verdict"] = previous.get("verdict")
        changed = True
    previous_confidence = str(previous.get("confidence") or "")
    current_confidence = str(current.get("confidence") or "")
    if not supports_strengthening and _CONFIDENCE_RANK.get(current_confidence, 0) > _CONFIDENCE_RANK.get(previous_confidence, 0):
        guarded["confidence"] = previous.get("confidence")
        changed = True
    previous_dimensions = {
        str(item.get("name") or ""): item
        for item in previous.get("dimensions") or []
        if isinstance(item, Mapping)
    }
    dimensions: list[dict[str, Any]] = []
    unverifiable_dimensions: list[dict[str, str]] = []
    for item in current.get("dimensions") or []:
        dimension = dict(item)
        dimension_name = str(dimension.get("name") or "")
        dimension_identity = _dimension_identity(dimension_name)
        before = next(
            (
                value
                for name, value in previous_dimensions.items()
                if _dimension_identity(name) == dimension_identity
            ),
            None,
        )
        if before is None and dimension_identity not in authoritative_dimensions:
            unverifiable_dimensions.append(
                {
                    "name": dimension_name,
                    "reason": "New or strengthened dimension has no new traceable evidence linked to its identity.",
                }
            )
            changed = True
            continue
        if before is None:
            dimensions.append(dimension)
            continue
        strengthened = False
        try:
            strengthened = float(dimension.get("score")) > float(before.get("score"))
            if strengthened and dimension_identity not in authoritative_dimensions:
                dimension["score"] = before.get("score")
                changed = True
        except (TypeError, ValueError):
            pass
        confidence_strengthened = _CONFIDENCE_RANK.get(str(dimension.get("confidence") or ""), 0) > _CONFIDENCE_RANK.get(str(before.get("confidence") or ""), 0)
        if confidence_strengthened and dimension_identity not in authoritative_dimensions:
            dimension["confidence"] = before.get("confidence")
            changed = True
        if (strengthened or confidence_strengthened) and dimension_identity not in authoritative_dimensions:
            unverifiable_dimensions.append(
                {
                    "name": dimension_name,
                    "reason": "New or strengthened dimension has no new traceable evidence linked to its identity.",
                }
            )
        dimensions.append(dimension)
    guarded["dimensions"] = dimensions
    if unverifiable_dimensions:
        guarded["unverifiable_dimensions"] = unverifiable_dimensions
    if changed:
        guarded["verdict_guard"] = {
            "before": model_verdict or None,
            "after": guarded.get("verdict"),
            "reason": "no_new_traceable_evidence",
        }
    return guarded


def _decision_delta(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    if previous is None:
        return {"changed": True, "changes": [{"field": "version", "before": None, "after": "V1"}], "reasons": ["建立首版分析基线"], "summary": "建立首版分析基线"}
    before = previous.get("decision") if isinstance(previous.get("decision"), Mapping) else {}
    after = current.get("decision") if isinstance(current.get("decision"), Mapping) else {}
    changes: list[dict[str, Any]] = []
    for field in ("verdict", "confidence", "dimensions"):
        before_value = _normalized_decision_field(field, before.get(field))
        after_value = _normalized_decision_field(field, after.get(field))
        if before_value != after_value:
            reason = (
                f"Normalized decision field {field} changed from "
                f"{_decision_value_label(before_value)} to {_decision_value_label(after_value)}."
            )
            changes.append(
                {
                    "field": field,
                    "before": before_value,
                    "after": after_value,
                    "reason": reason,
                }
            )
    delta = current.get("evidence_delta") if isinstance(current.get("evidence_delta"), Mapping) else _evidence_delta(previous.get("evidence") or [], current.get("evidence") or [])
    reasons: list[str] = [str(item["reason"]) for item in changes]
    if delta.get("added"):
        reasons.append(f"Added {len(delta['added'])} source-linked evidence item(s)")
    if delta.get("removed"):
        reasons.append(f"Removed {len(delta['removed'])} source-linked evidence item(s)")
    if delta.get("contradicted"):
        reasons.append(f"Contradicted {len(delta['contradicted'])} source-linked evidence item(s)")
    if delta.get("strengthened"):
        reasons.append(f"Strengthened {len(delta['strengthened'])} source-linked evidence item(s)")
    metric_delta = current.get("metric_delta") if isinstance(current.get("metric_delta"), Mapping) else {}
    if metric_delta.get("added"):
        reasons.append(f"Added {len(metric_delta['added'])} source-linked observed metric")
    guard = after.get("verdict_guard") if isinstance(after.get("verdict_guard"), Mapping) else None
    if guard:
        reasons.append("Blocked decision strengthening without new traceable evidence")
    changed = bool(changes or reasons)
    return {
        "changed": changed,
        "changes": changes,
        "reasons": reasons,
        "summary": "; ".join(reasons) if reasons else "No comparable evidence or normalized decision change",
    }


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _run_order_key(item: Mapping[str, Any]) -> tuple[int, str, str]:
    sequence = int(item.get("canonical_lineage_sequence") or item.get("_lineage_sequence") or 0)
    timestamp = str(item.get("completed_at") or item.get("updated_at") or item.get("started_at") or "")
    run_id = str(item.get("run_id") or item.get("conversation_id") or "")
    return sequence, timestamp, run_id


def _normalized_token(value: Any) -> str:
    return _normalized_text(value).lower()


def _dimension_identity(value: Any) -> str:
    return _normalized_token(value)


def _verdict_identity(value: Any) -> str:
    token = _normalized_token(value)
    rank = _VERDICT_RANK.get(token)
    if rank is None:
        return token
    return {
        0: "insufficient_evidence",
        1: "not_yet_feasible",
        2: "conditional",
        3: "feasible",
    }[rank]


def _normalized_decision_field(field: str, value: Any) -> Any:
    if field == "verdict":
        return _verdict_identity(value)
    if field == "confidence":
        return _normalized_token(value)
    if field == "dimensions":
        dimensions: list[dict[str, Any]] = []
        for item in value or []:
            if not isinstance(item, Mapping) or not _dimension_identity(item.get("name")):
                continue
            normalized = {"name": _dimension_identity(item.get("name"))}
            if item.get("score") is not None:
                normalized["score"] = _normalized_number(item.get("score"))
            if _normalized_token(item.get("confidence")):
                normalized["confidence"] = _normalized_token(item.get("confidence"))
            dimensions.append(normalized)
        return sorted(dimensions, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return value


def _decision_value_label(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _decimal_value(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _normalized_number(value: Any) -> Any:
    number = _decimal_value(value)
    if number is None:
        return _normalized_text(value) if isinstance(value, str) else value
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _safe_key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip(".-")[:160]
    if not clean:
        raise ValueError("workspace_id is invalid")
    return clean


def _local_path(workspace_id: str) -> Path:
    return EXPERIMENT_DIR / f"{_safe_key(workspace_id)}.json"


def _blob_name(workspace_id: str) -> str:
    return f"{EXPERIMENT_BLOB_PREFIX}/{_safe_key(workspace_id)}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "analysis_lineage_fingerprints",
    "build_experiment_ledger",
    "compare_experiment_versions",
    "load_experiment_ledger",
    "resolve_canonical_experiment_run_id",
    "sync_experiment_ledger",
]
