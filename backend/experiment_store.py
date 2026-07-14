from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from .blob_store import download_blob_json, upload_blob_json
except ImportError:
    from blob_store import download_blob_json, upload_blob_json


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = ROOT / "generated-outputs" / "experiments"
EXPERIMENT_BLOB_PREFIX = "experiments"

_LOCK = threading.RLock()
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
) -> dict[str, Any]:
    normalized_workspace = str(workspace_id or "").strip()
    if not normalized_workspace:
        raise ValueError("workspace_id is required")
    ordered = sorted(
        [item for item in runs if isinstance(item, dict)],
        key=lambda item: str(item.get("completed_at") or item.get("updated_at") or item.get("started_at") or ""),
    )
    analysis_runs = [item for item in ordered if _is_analysis_version(item)]
    snapshots = [item for item in ordered if str(item.get("version_kind") or "") in {"plan_draft", "artifact_generation"}]
    outcome_items = [item for item in (outcomes or []) if isinstance(item, dict)]
    versions: list[dict[str, Any]] = []
    version_aliases: dict[str, str] = {}

    for run in analysis_runs:
        run_id = str(run.get("run_id") or run.get("conversation_id") or "").strip()
        artifact = _artifact(run)
        feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
        evidence, unverifiable_evidence = _evidence_snapshot(artifact, feasibility)
        metrics = _metrics(artifact.get("iteration_inputs") or run.get("iteration_inputs") or [])
        linked_outcomes = _outcomes_for_analysis(outcome_items, run, set(version_aliases) | {run_id})
        metrics.extend(_outcome_metrics(linked_outcomes, existing=metrics))
        model_verdict = str(feasibility.get("verdict") or run.get("verdict") or "").strip()
        decision = {
            "opportunity_id": _normalized_text(feasibility.get("opportunity_id")) or None,
            "model_verdict": model_verdict or None,
            "verdict": model_verdict or None,
            "confidence": feasibility.get("overall_confidence") or feasibility.get("confidence") or run.get("confidence"),
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
        supports_strengthening = bool(evidence_delta["added"] or evidence_delta["strengthened"] or metric_delta["added"])
        if previous and not supports_strengthening:
            decision = _guard_decision_strengthening(previous.get("decision") or {}, decision)
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

        ordinal = len(versions) + 1
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
        should_promote = previous is None or version["evidence_changed"] or bool(version["decision_delta"]["changes"])
        if should_promote:
            versions.append(version)
            version_aliases[run_id] = run_id
        else:
            version_aliases[run_id] = str(previous.get("run_id") or "")

    _attach_snapshots(versions, snapshots, version_aliases)

    return {
        "version": 1,
        "workspace_id": normalized_workspace,
        "generated_at": _now(),
        "versions": versions,
        "count": len(versions),
        "latest_version_id": versions[-1]["version_id"] if versions else None,
        "source": "run_store_plus_outcome_ledger",
    }


def sync_experiment_ledger(
    workspace_id: str,
    runs: list[dict[str, Any]],
    *,
    outcomes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ledger = build_experiment_ledger(workspace_id, runs, outcomes=outcomes)
    with _LOCK:
        path = _local_path(workspace_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        try:
            upload_blob_json(_blob_name(workspace_id), ledger)
        except Exception:
            pass
    return ledger


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
    confidence = _normalized_text(item.get("confidence"))[:40]
    if quote:
        normalized["quote"] = quote
    if confidence:
        normalized["confidence"] = confidence
    for field in _EVIDENCE_STRUCTURED_FIELDS:
        value = item.get(field)
        if value in (None, ""):
            continue
        normalized[field] = _normalized_text(value) if isinstance(value, str) else value
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
            else "observed"
            if kind == "observed" and source and _verification_passed(verification)
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


def _outcome_metrics(outcomes: list[dict[str, Any]], *, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = {_metric_key(item) for item in existing}
    result: list[dict[str, Any]] = []
    for item in outcomes:
        kind = str(item.get("provenance") or "assumption").strip().lower()
        if kind not in {"observed", "synthetic", "target", "assumption"}:
            kind = "assumption"
        source = _source(item.get("source"))
        verification = _verification(item.get("verification"))
        provenance = (
            "observed"
            if kind == "observed" and source and _verification_passed(verification)
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
            **({"score": item.get("score")} if item.get("score") is not None else {}),
            **({"confidence": _normalized_text(item.get("confidence"))} if _normalized_text(item.get("confidence")) else {}),
        }
        dimensions.append(normalized)
    return sorted(dimensions, key=lambda item: str(item.get("name") or ""))


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
    added = [
        {**after[key], "reason": f"Added stable evidence identity: {_evidence_identity_label(after[key])}."}
        for key in sorted(set(after) - set(before))
    ]
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


def _guard_decision_strengthening(previous: Mapping[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    guarded = dict(current)
    changed = False
    previous_verdict = str(previous.get("verdict") or "")
    model_verdict = str(current.get("model_verdict") or current.get("verdict") or "")
    if _VERDICT_RANK.get(model_verdict, 0) > _VERDICT_RANK.get(previous_verdict, 0):
        guarded["verdict"] = previous.get("verdict")
        changed = True
    previous_confidence = str(previous.get("confidence") or "")
    current_confidence = str(current.get("confidence") or "")
    if _CONFIDENCE_RANK.get(current_confidence, 0) > _CONFIDENCE_RANK.get(previous_confidence, 0):
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
        before = previous_dimensions.get(str(dimension.get("name") or ""))
        if before is None:
            unverifiable_dimensions.append(
                {
                    "name": str(dimension.get("name") or ""),
                    "reason": "New dimension has no new traceable evidence.",
                }
            )
            changed = True
            continue
        try:
            if float(dimension.get("score")) > float(before.get("score")):
                dimension["score"] = before.get("score")
                changed = True
        except (TypeError, ValueError):
            pass
        if _CONFIDENCE_RANK.get(str(dimension.get("confidence") or ""), 0) > _CONFIDENCE_RANK.get(str(before.get("confidence") or ""), 0):
            dimension["confidence"] = before.get("confidence")
            changed = True
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
        if before.get(field) != after.get(field):
            changes.append({"field": field, "before": before.get(field), "after": after.get(field)})
    delta = current.get("evidence_delta") if isinstance(current.get("evidence_delta"), Mapping) else _evidence_delta(previous.get("evidence") or [], current.get("evidence") or [])
    reasons: list[str] = []
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
    "build_experiment_ledger",
    "compare_experiment_versions",
    "load_experiment_ledger",
    "sync_experiment_ledger",
]
