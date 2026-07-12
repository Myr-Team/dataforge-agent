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
_SOURCE_KEYS = ("file_id", "file_version", "connector_id", "run_id", "artifact_id", "query_hash", "table_name")


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

    for index, run in enumerate(analysis_runs):
        run_id = str(run.get("run_id") or run.get("conversation_id") or "").strip()
        artifact = _artifact(run)
        feasibility = artifact.get("feasibility") if isinstance(artifact.get("feasibility"), dict) else {}
        evidence = _evidence_set(artifact, feasibility)
        metrics = _metrics(artifact.get("iteration_inputs") or run.get("iteration_inputs") or [])
        linked_outcomes = _outcomes_for_run(outcome_items, run_id)
        metrics.extend(_outcome_metrics(linked_outcomes, existing=metrics))
        model_verdict = str(feasibility.get("verdict") or run.get("verdict") or "").strip()
        dimensions = _dimension_decisions(feasibility)
        decision = {
            "opportunity_id": feasibility.get("opportunity_id"),
            "model_verdict": model_verdict or None,
            "verdict": model_verdict or None,
            "confidence": feasibility.get("overall_confidence") or feasibility.get("confidence") or run.get("confidence"),
            "dimensions": dimensions,
            "gaps": [str(item) for item in (feasibility.get("gap_list") or []) if str(item).strip()],
        }
        previous = versions[-1] if versions else None
        evidence_delta = _evidence_delta(previous.get("evidence") if previous else [], evidence)
        traceable_metrics = [item for item in metrics if item.get("provenance") == "observed" and item.get("source")]
        if (
            previous
            and _VERDICT_RANK.get(model_verdict, 0) > _VERDICT_RANK.get(str(previous["decision"].get("verdict") or ""), 0)
            and not evidence_delta["added"]
            and not evidence_delta["strengthened"]
            and not traceable_metrics
        ):
            decision["verdict"] = previous["decision"].get("verdict")
            decision["verdict_guard"] = {
                "before": model_verdict,
                "after": decision["verdict"],
                "reason": "no_new_traceable_evidence",
            }

        attachments = _attachments_for(run_id, snapshots)
        version = {
            "version_id": f"version:{run_id}",
            "label": f"V{index + 1}",
            "ordinal": index + 1,
            "workspace_id": normalized_workspace,
            "run_id": run_id,
            "created_at": run.get("completed_at") or run.get("updated_at") or run.get("started_at"),
            "title": run.get("title") or feasibility.get("opportunity_id") or f"Version {index + 1}",
            "hypothesis": feasibility.get("opportunity_id"),
            "decision": decision,
            "evidence": evidence,
            "metrics": metrics,
            "gaps": decision["gaps"],
            "attachments": attachments,
            "evidence_delta": evidence_delta,
            "evidence_changed": bool(
                evidence_delta["added"]
                or evidence_delta["removed"]
                or evidence_delta["strengthened"]
                or evidence_delta["contradicted"]
                or traceable_metrics
            ),
        }
        version["decision_delta"] = _decision_delta(previous, version)
        versions.append(version)

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
        "evidence_delta": _evidence_delta(source.get("evidence") or [], target.get("evidence") or []),
        "decision_delta": _decision_delta(source, target),
    }


def _is_analysis_version(run: Mapping[str, Any]) -> bool:
    if str(run.get("version_kind") or ""):
        return False
    if str(run.get("status") or "").lower() in {"followup", "followup_edit", "clarify", "error"}:
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
    candidates: list[Mapping[str, Any]] = []
    for dimension in feasibility.get("dimensions") or []:
        if isinstance(dimension, Mapping):
            candidates.extend(item for item in dimension.get("evidence") or [] if isinstance(item, Mapping))
    for item in artifact.get("citations") or (artifact.get("answer") or {}).get("citations") or []:
        if isinstance(item, Mapping):
            candidates.append(item)
    by_ref: dict[str, dict[str, Any]] = {}
    for item in candidates:
        ref = str(item.get("ref") or item.get("source_file") or item.get("marker") or "").strip()
        if not ref:
            continue
        normalized = {
            "ref": ref,
            "source_type": item.get("source_type") or ("corpus" if item.get("source_file") else "unknown"),
            "quote": str(item.get("quote") or item.get("snippet") or "")[:600] or None,
            "confidence": item.get("confidence"),
        }
        current = by_ref.get(ref)
        if current is None or _CONFIDENCE_RANK.get(str(normalized.get("confidence") or ""), 0) > _CONFIDENCE_RANK.get(str(current.get("confidence") or ""), 0):
            by_ref[ref] = {key: value for key, value in normalized.items() if value is not None}
    return sorted(by_ref.values(), key=lambda item: item["ref"])


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
        provenance = (
            "synthetic"
            if kind == "synthetic" or item.get("provenance") == "synthetic"
            else "observed"
            if kind == "observed" and source
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
            }
        )
    return [{key: value for key, value in item.items() if value is not None} for item in metrics]


def _outcomes_for_run(outcomes: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in outcomes
        if isinstance(item.get("source"), dict) and str(item["source"].get("run_id") or "") == run_id
    ]


def _outcome_metrics(outcomes: list[dict[str, Any]], *, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = {(str(item.get("metric_name") or ""), str(item.get("observed_value") or item.get("value") or "")) for item in existing}
    result: list[dict[str, Any]] = []
    for item in outcomes:
        key = (str(item.get("metric_name") or ""), str(item.get("observed_value") or ""))
        if not key[0] or key in keys:
            continue
        result.append(
            {
                "metric_name": key[0],
                "value": item.get("observed_value"),
                "unit": item.get("unit"),
                "kind": "observed",
                "provenance": "observed",
                "source": _source(item.get("source")),
                "verification": item.get("verification"),
                "observed_at": item.get("observed_at"),
            }
        )
    return result


def _source(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: str(value.get(key))[:240]
        for key in _SOURCE_KEYS
        if value.get(key) not in (None, "")
    }


def _dimension_decisions(feasibility: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            key: item.get(key)
            for key in ("name", "score", "confidence", "rationale")
            if item.get(key) is not None
        }
        for item in feasibility.get("dimensions") or []
        if isinstance(item, Mapping) and item.get("name")
    ]


def _attachments_for(source_run_id: str, snapshots: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    plans: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for item in snapshots:
        if str(item.get("source_run_id") or item.get("conversation_id") or "") != source_run_id:
            continue
        artifact = _artifact(item)
        if item.get("version_kind") == "plan_draft":
            draft = artifact.get("plan_draft") if isinstance(artifact.get("plan_draft"), dict) else {}
            plans.append({"run_id": item.get("run_id"), "text": draft.get("text"), "created_at": item.get("completed_at")})
        elif item.get("version_kind") == "artifact_generation":
            proposal = artifact.get("proposal") if isinstance(artifact.get("proposal"), dict) else {}
            artifacts.append({"run_id": item.get("run_id"), "urls": proposal.get("artifact_urls") or {}, "created_at": item.get("completed_at")})
    return {"plans": plans, "artifacts": artifacts}


def _evidence_delta(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, list[Any]]:
    before = {str(item.get("ref") or ""): item for item in previous if item.get("ref")}
    after = {str(item.get("ref") or ""): item for item in current if item.get("ref")}
    added = [after[ref] for ref in sorted(set(after) - set(before))]
    removed = [before[ref] for ref in sorted(set(before) - set(after))]
    strengthened = [
        {"ref": ref, "before": before[ref].get("confidence"), "after": after[ref].get("confidence")}
        for ref in sorted(set(before) & set(after))
        if _CONFIDENCE_RANK.get(str(after[ref].get("confidence") or ""), 0)
        > _CONFIDENCE_RANK.get(str(before[ref].get("confidence") or ""), 0)
    ]
    return {"added": added, "removed": removed, "strengthened": strengthened, "contradicted": []}


def _decision_delta(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    if previous is None:
        return {"changed": True, "changes": [{"field": "version", "before": None, "after": "V1"}], "reasons": ["建立首版分析基线"], "summary": "建立首版分析基线"}
    before = previous.get("decision") if isinstance(previous.get("decision"), Mapping) else {}
    after = current.get("decision") if isinstance(current.get("decision"), Mapping) else {}
    changes: list[dict[str, Any]] = []
    for field in ("opportunity_id", "verdict", "confidence", "dimensions", "gaps"):
        if before.get(field) != after.get(field):
            changes.append({"field": field, "before": before.get(field), "after": after.get(field)})
    delta = current.get("evidence_delta") if isinstance(current.get("evidence_delta"), Mapping) else _evidence_delta(previous.get("evidence") or [], current.get("evidence") or [])
    reasons: list[str] = []
    if delta.get("added"):
        reasons.append(f"新增 {len(delta['added'])} 项可追溯证据")
    if delta.get("strengthened"):
        reasons.append(f"{len(delta['strengthened'])} 项证据强度提升")
    observed = [item for item in current.get("metrics") or [] if item.get("provenance") == "observed"]
    if observed:
        reasons.append(f"回填 {len(observed)} 项带来源实测指标")
    guard = after.get("verdict_guard") if isinstance(after.get("verdict_guard"), Mapping) else None
    if guard:
        reasons.append("新证据不足，结论已保持上一版档位")
    changed = bool(changes or reasons)
    return {
        "changed": changed,
        "changes": changes,
        "reasons": reasons,
        "summary": "；".join(reasons) if reasons else "暂无可比较的新证据",
    }


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
