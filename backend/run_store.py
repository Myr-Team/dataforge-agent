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
    run["duration_ms"] = _duration_ms(run.get("started_at"), run.get("completed_at"))
    run["verdict"] = _verdict(run)
    run["confidence"] = _confidence(run)
    run["step_count"] = len(run.get("steps") or [])
    run["title"] = _run_title(run)
    run["summary"] = _run_summary_text(run)
    run["registry_summary"] = _run_summary(run)
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
        incoming = _plain(proposal)
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
    summary = dict(run.get("registry_summary") or _run_summary(run))
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
        "started_at": run.get("started_at"),
        "finished_at": run.get("completed_at"),
        "duration_ms": run.get("duration_ms") or _duration_ms(run.get("started_at"), run.get("completed_at") or run.get("updated_at")),
        "workspace_id": run.get("workspace_id"),
        "title": run.get("title") or _run_title(run),
        "summary": run.get("summary") if isinstance(run.get("summary"), str) else _run_summary_text(run),
        "message": _clean_phrase(run.get("message"), 160),
        "verdict": run.get("verdict"),
        "confidence": run.get("confidence"),
        "status": run.get("status"),
        "steps": steps,
        "step_count": len(run.get("steps") or []),
        "maf": _maf_summary(run),
    }


def _normalize_run_detail(run: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    normalized = dict(run)
    if isinstance(normalized.get("summary"), dict):
        normalized["registry_summary"] = normalized.get("summary")
        normalized["summary"] = normalized.get("summary_text") or _run_summary_text(normalized)
    normalized.setdefault("title", _run_title(normalized))
    if not isinstance(normalized.get("summary"), str):
        normalized["summary"] = _run_summary_text(normalized)
    normalized.setdefault("registry_summary", _run_summary(normalized))
    return normalized


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
        summary = data.get("registry_summary") if isinstance(data, dict) else None
        if not isinstance(summary, dict):
            legacy_summary = data.get("summary") if isinstance(data, dict) else None
            summary = legacy_summary if isinstance(legacy_summary, dict) else None
        if not isinstance(summary, dict) and isinstance(data, dict):
            summary = _run_summary(_normalize_run_detail(data))
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
