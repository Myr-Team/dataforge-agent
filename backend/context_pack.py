from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

try:
    from .schemas import ChatRequest
except ImportError:
    from schemas import ChatRequest


ALLOWED_DURABLE_FACT_KINDS = frozenset(
    {
        "verified_constraint",
        "selected_metric",
        "accepted_scope",
        "evidence_revision",
    }
)
_MAX_DURABLE_FACTS = 6
_MAX_EVIDENCE_REFS = 8
_MAX_WORKSPACE_FACTS = 6
_MAX_AUDIT_CONSTRAINTS = 6


@dataclass(frozen=True)
class ContextPack:
    scope: dict[str, str]
    version: str
    fingerprint: str
    workspace_facts: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    durable_facts: tuple[str, ...]
    audit_constraints: tuple[str, ...]
    prompt_projection: dict[str, Any]
    serialized_for_telemetry: str


def public_context_pack_metadata(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    sanitized: dict[str, Any] = {}
    status = str(data.get("status") or "").strip().lower()
    if status in {"ready", "fallback"}:
        sanitized["status"] = status
    version = str(data.get("version") or "").strip()
    if version:
        sanitized["version"] = version[:64]
    fingerprint = str(data.get("fingerprint") or "").strip()
    if fingerprint:
        sanitized["fingerprint"] = fingerprint[:128]
    fallback_reason = str(data.get("fallback_reason") or "").strip().lower()
    if fallback_reason in {
        "pack_build_failed",
        "conversation_fact_lookup_failed",
        "pack_unavailable",
    }:
        sanitized["fallback_reason"] = fallback_reason
    scope = data.get("scope")
    if isinstance(scope, dict):
        workspace_id = str(scope.get("workspace_id") or "").strip()
        conversation_id = str(scope.get("conversation_id") or "").strip()
        sanitized["scope"] = {
            "workspace_id": workspace_id[:160],
            "conversation_id": conversation_id[:160],
        }
    for source_key, target_key in (
        ("durable_fact_ids", "durable_fact_ids"),
        ("durable_fact_kinds", "durable_fact_kinds"),
    ):
        values = data.get(source_key)
        if isinstance(values, list):
            sanitized[target_key] = [str(item).strip()[:128] for item in values if str(item).strip()][:16]
    for key in ("fact_count", "workspace_fact_count", "audit_constraint_count"):
        value = data.get(key)
        if isinstance(value, int) and value >= 0:
            sanitized[key] = value
    return sanitized


def build_context_pack(
    request: ChatRequest,
    *,
    profile: dict[str, Any],
    analysis: dict[str, Any],
    facts: list[dict[str, Any]],
) -> ContextPack:
    scope = {
        "workspace_id": str(request.workspace_id or "").strip(),
        "conversation_id": str(request.conversation_id or "").strip(),
    }
    selected = _selected_facts(facts, scope)
    workspace_facts = tuple(_workspace_facts(profile))
    evidence_refs = tuple(_evidence_refs(analysis))
    audit_constraints = tuple(_audit_constraints(analysis))
    durable_fact_texts = tuple(str(item["text"]) for item in selected)
    fingerprint = _fingerprint(scope, profile, analysis, selected, evidence_refs)
    telemetry = {
        "status": "ready",
        "version": "context-pack-v1",
        "scope": scope,
        "fingerprint": fingerprint,
        "profile_revision": _revision(profile),
        "analysis_revision": _revision(analysis),
        "evidence_refs": list(evidence_refs),
        "durable_fact_ids": [str(item["fact_id"]) for item in selected],
        "durable_fact_kinds": [str(item["kind"]) for item in selected],
        "fact_count": len(selected),
        "workspace_fact_count": len(workspace_facts),
        "audit_constraint_count": len(audit_constraints),
    }
    prompt_projection = {
        "scope": scope,
        "version": "context-pack-v1",
        "current_message": str(request.message or ""),
        "workspace_profile": {
            "revision": _revision(profile),
            "facts": list(workspace_facts),
        },
        "latest_analysis": {
            "revision": _revision(analysis),
            "verdict": str(analysis.get("verdict") or "").strip() or None,
            "overall_confidence": str(analysis.get("overall_confidence") or "").strip() or None,
            "recommendation": str(analysis.get("recommendation") or "").strip() or None,
            "gaps": [str(item).strip() for item in (analysis.get("gap_list") or []) if str(item).strip()][:4],
            "evidence_refs": list(evidence_refs),
        },
        "durable_facts": [
            {
                "fact_id": str(item["fact_id"]),
                "kind": str(item["kind"]),
                "text": str(item["text"]),
            }
            for item in selected
        ],
        "audit_constraints": list(audit_constraints),
    }
    prompt_projection["workspace_profile"] = {
        key: value
        for key, value in prompt_projection["workspace_profile"].items()
        if value not in (None, "", [], {})
    }
    prompt_projection["latest_analysis"] = {
        key: value
        for key, value in prompt_projection["latest_analysis"].items()
        if value not in (None, "", [], {})
    }
    return ContextPack(
        scope=scope,
        version="context-pack-v1",
        fingerprint=fingerprint,
        workspace_facts=workspace_facts,
        evidence_refs=evidence_refs,
        durable_facts=durable_fact_texts,
        audit_constraints=audit_constraints,
        prompt_projection=prompt_projection,
        serialized_for_telemetry=json.dumps(
            telemetry,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    )


def _selected_facts(facts: list[dict[str, Any]], scope: dict[str, str]) -> list[dict[str, str]]:
    scope_value = f"{scope['workspace_id']}:{scope['conversation_id']}"
    selected: list[dict[str, str]] = []
    for index, raw in enumerate(facts or []):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("scope") or "").strip() != scope_value:
            continue
        kind = str(raw.get("kind") or "").strip()
        if kind not in ALLOWED_DURABLE_FACT_KINDS:
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        fact_id = str(raw.get("fact_id") or raw.get("id") or f"fact-{index}").strip()
        if not fact_id:
            continue
        selected.append(
            {
                "fact_id": fact_id,
                "kind": kind,
                "text": text,
            }
        )
    return selected[-_MAX_DURABLE_FACTS:]


def _workspace_facts(profile: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for label, key in (
        ("Workspace", "name"),
        ("Profile summary", "profile_summary"),
        ("Customer summary", "customer_summary"),
        ("Format", "format"),
    ):
        value = str(profile.get(key) or "").strip()
        if value:
            rows.append(f"{label}: {value}")
    doc_count = profile.get("doc_count")
    if isinstance(doc_count, int) and doc_count >= 0:
        rows.append(f"Document count: {doc_count}")
    return rows[:_MAX_WORKSPACE_FACTS]


def _evidence_refs(analysis: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in analysis.get("evidence_refs") or []:
        text = str(item or "").strip()
        if text and text not in refs:
            refs.append(text)
    for item in analysis.get("citations") or []:
        if not isinstance(item, dict):
            continue
        for key in ("ref", "source_file", "marker"):
            text = str(item.get(key) or "").strip()
            if text and text not in refs:
                refs.append(text)
    return refs[:_MAX_EVIDENCE_REFS]


def _audit_constraints(analysis: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    audit = analysis.get("audit")
    if isinstance(audit, dict):
        verdict = str(audit.get("verdict") or "").strip()
        if verdict:
            rows.append(f"Audit verdict: {verdict}")
        for item in audit.get("issues") or []:
            text = str(item or "").strip()
            if text:
                rows.append(text)
    for item in analysis.get("gap_list") or []:
        text = str(item or "").strip()
        if text:
            rows.append(text)
    deduped: list[str] = []
    for item in rows:
        if item not in deduped:
            deduped.append(item)
    return deduped[:_MAX_AUDIT_CONSTRAINTS]


def _revision(payload: dict[str, Any]) -> str:
    for key in ("revision", "run_id", "updated_at"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _fingerprint(
    scope: dict[str, str],
    profile: dict[str, Any],
    analysis: dict[str, Any],
    facts: list[dict[str, str]],
    evidence_refs: tuple[str, ...],
) -> str:
    payload = {
        "scope": scope,
        "profile_revision": _revision(profile),
        "analysis_revision": _revision(analysis),
        "evidence_refs": list(evidence_refs),
        "durable_fact_ids": [str(item["fact_id"]) for item in facts],
        "durable_fact_kinds": [str(item["kind"]) for item in facts],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
