"""Deterministic, opportunity-derived gating for external market evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .schemas import (
    GatedMarketComparison,
    MarketComparison,
    MarketQueryPlan,
    MarketQueryPurpose,
    MarketSourceAssessment,
    _market_context_terms,
)


_PUBLIC_USAGE_FIELDS = {"input_tokens", "output_tokens", "total_tokens"}
_PUBLIC_CACHE_FIELDS = {"hit", "status"}
_SOURCE_EVIDENCE_FIELDS = ("title", "snippet", "excerpt", "url")


_DIRECTNESS_RE = re.compile(
    r"\b(competitors?|alternatives?|compare|comparison|platforms?|products?|services?|software|solutions?|systems?|tools?|workflows?|analytics)\b",
    re.IGNORECASE,
)
_CJK_DIRECTNESS_RE = re.compile(
    r"竞争对手|替代方案|对比|比较|解决方案|服务|平台|产品|工具",
)
_NEGATED_DIRECTNESS_RE = re.compile(
    r"\b(?:not|isn['’]?t|is\s+not|non|unrelated|orthogonal|adjacent)\b.{0,48}\b(?:competitor|alternative|comparison|product|service|solution)\b",
    re.IGNORECASE,
)
_CJK_NEGATED_DIRECTNESS_RE = re.compile(
    r"(?:不是|并非)\s*(?:直接)?(?:竞争对手|替代方案)|非竞争对手|无关",
)
_CONSUMER_ONLY_RE = re.compile(
    r"\b(?:consumer[-\s]*(?:only|facing)|for\s+(?:consumers?|individuals)|personal[-\s]*(?:only|use))\b",
    re.IGNORECASE,
)
_CJK_CONSUMER_ONLY_RE = re.compile(
    r"面向消费者|面向个人|消费者端|仅供个人",
)
_QUERY_PURPOSES: set[str] = {
    "direct_competitor",
    "pricing",
    "demand",
    "regulation",
    "adjacent_pattern",
}


def _text(source: Mapping[str, Any], *keys: str) -> str:
    return " ".join(str(source.get(key) or "").strip() for key in keys if source.get(key)).strip()


def _query_purpose(source: Mapping[str, Any]) -> MarketQueryPurpose:
    value = str(source.get("query_purpose") or "direct_competitor").strip()
    return value if value in _QUERY_PURPOSES else "direct_competitor"  # type: ignore[return-value]


def _public_llm_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, Any] = {}
    for key in ("mode", "response_id"):
        if isinstance(value.get(key), str):
            safe[key] = value[key]
    usage = value.get("usage")
    if isinstance(usage, Mapping):
        filtered_usage = {
            key: amount
            for key, amount in usage.items()
            if key in _PUBLIC_USAGE_FIELDS and isinstance(amount, int | float)
        }
        if filtered_usage:
            safe["usage"] = filtered_usage
    cache = value.get("cache")
    if isinstance(cache, (str, bool)):
        safe["cache"] = cache
    elif isinstance(cache, Mapping):
        filtered_cache = {
            key: cache_value
            for key, cache_value in cache.items()
            if key in _PUBLIC_CACHE_FIELDS and isinstance(cache_value, (str, bool, int, float))
        }
        if filtered_cache:
            safe["cache"] = filtered_cache
    return safe


def _assessment(
    plan: MarketQueryPlan,
    source: Mapping[str, Any],
) -> MarketSourceAssessment:
    opportunity_terms = list(dict.fromkeys([*plan.opportunity_terms, *plan.evidence_terms]))
    target = set(opportunity_terms)
    source_owned_text = _text(source, *_SOURCE_EVIDENCE_FIELDS)
    source_owned_terms = set(_market_context_terms(source_owned_text))
    source_matches = target & source_owned_terms
    matched = sorted(source_matches)
    has_source_evidence = bool(source_owned_text)
    source_strength = min(1.0, len(source_matches) / 3)
    score = source_strength
    negated = bool(_NEGATED_DIRECTNESS_RE.search(source_owned_text))
    cjk_negated = bool(_CJK_NEGATED_DIRECTNESS_RE.search(source_owned_text))
    consumer_only = bool(
        _CONSUMER_ONLY_RE.search(source_owned_text)
        or _CJK_CONSUMER_ONLY_RE.search(source_owned_text)
    )
    direct = bool(_DIRECTNESS_RE.search(source_owned_text) or _CJK_DIRECTNESS_RE.search(source_owned_text))
    sparse_context_match = (
        len(target) <= 4
        and len(source_matches) == 1
        and direct
    )
    meaningful_overlap = (
        (has_source_evidence and len(source_matches) >= 2)
        or sparse_context_match
    )

    if consumer_only:
        verdict = "rejected"
        reasons = ["source language limits the offering to consumer or personal use"]
    elif cjk_negated:
        verdict = "rejected"
        reasons = ["source language explicitly denies direct competitor relevance"]
    elif negated:
        verdict = "adjacent" if matched else "rejected"
        reasons = ["source language explicitly limits direct competitor relevance"]
    elif not has_source_evidence:
        verdict = "rejected"
        reasons = ["source has no title, snippet, excerpt, or URL evidence"]
    elif meaningful_overlap and direct:
        verdict = "accepted"
        reasons = ["source evidence has meaningful opportunity overlap and directness"]
    elif matched:
        verdict = "adjacent"
        reasons = ["source has partial opportunity overlap without enough direct evidence"]
    else:
        verdict = "rejected"
        reasons = ["source has no meaningful overlap with the current opportunity evidence"]

    return MarketSourceAssessment(
        verdict=verdict,
        query_purpose=_query_purpose(source),
        opportunity_terms=opportunity_terms,
        matched_terms=matched,
        deterministic_score=round(score, 4),
        reasons=reasons,
    )


def _assessed_source(plan: MarketQueryPlan, raw: Mapping[str, Any]) -> dict[str, Any]:
    source = dict(raw)
    source["relevance"] = _assessment(plan, source).model_dump(mode="json")
    return source


def _finding_as_source(finding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(finding),
        "name": finding.get("source_title") or finding.get("title") or finding.get("source_url") or "market finding",
        "positioning": finding.get("claim") or finding.get("summary") or finding.get("description") or "external market finding",
        "url": finding.get("source_url") or finding.get("url") or "unavailable",
    }


def _accepted_urls(competitors: list[dict[str, Any]], findings: list[dict[str, Any]]) -> set[str]:
    urls = {str(item.get("url") or "").strip() for item in competitors}
    urls.update(str(item.get("source_url") or item.get("url") or "").strip() for item in findings)
    return {url for url in urls if url and url != "unavailable"}


def _filter_provenance(value: Any, accepted_urls: set[str]) -> Any:
    if not isinstance(value, Mapping):
        return value
    result = dict(value)
    for key in ("sources", "citations"):
        items = result.get(key)
        if isinstance(items, list):
            result[key] = [
                item
                for item in items
                if str(item.get("url") if isinstance(item, Mapping) else item).strip() in accepted_urls
            ]
    return result


def _public_source_items(items: Any, accepted_urls: set[str]) -> list[Any]:
    if not isinstance(items, list):
        return []
    public: list[Any] = []
    for item in items:
        if isinstance(item, str):
            if item in accepted_urls:
                public.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        url_key = "url" if item.get("url") else "source_url"
        url = str(item.get(url_key) or "").strip()
        if url not in accepted_urls:
            continue
        projected = {url_key: url}
        title = item.get("title") or item.get("source_title")
        if isinstance(title, str):
            projected["title" if url_key == "url" else "source_title"] = title
        public.append(projected)
    return public


def _public_tool_provenance(value: Any, accepted_urls: set[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    public: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        if not isinstance(raw, Mapping):
            continue
        item: dict[str, Any] = {}
        for key in ("tool_name", "source_type", "confidence"):
            if isinstance(raw.get(key), str):
                item[key] = raw[key]
        if isinstance(raw.get("latency_ms"), int | float):
            item["latency_ms"] = raw["latency_ms"]
        item["sources"] = _public_source_items(raw.get("sources"), accepted_urls)
        item["citations"] = _public_source_items(raw.get("citations"), accepted_urls)
        if raw.get("error") or raw.get("error_category"):
            item["error_category"] = "unavailable"
        public[str(name)] = item
    return public


def _public_errors(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): "unavailable" for key in value}


def _public_competitors(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    public: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or item.get("excerpt") or "").strip()
        if not url:
            continue
        projected: dict[str, Any] = {
            "name": title or url,
            "positioning": snippet or title or url,
            "url": url,
        }
        if title:
            projected["title"] = title
        if snippet:
            projected["snippet"] = snippet
        if item.get("retrieval_query") is not None:
            projected["retrieval_query"] = item["retrieval_query"]
        if item.get("relevance") is not None:
            projected["relevance"] = item["relevance"]
        public.append(projected)
    return public


def _public_findings(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    public: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        source_url = str(item.get("source_url") or item.get("url") or "").strip()
        source_title = str(item.get("source_title") or item.get("title") or source_url).strip()
        excerpt = str(item.get("snippet") or item.get("excerpt") or source_title).strip()
        if not source_url:
            continue
        projected: dict[str, Any] = {
            "claim": excerpt,
            "source_title": source_title,
            "source_url": source_url,
        }
        if item.get("retrieval_query") is not None:
            projected["retrieval_query"] = item["retrieval_query"]
        if item.get("relevance") is not None:
            projected["relevance"] = item["relevance"]
        public.append(projected)
    return public


def _redact_urls(value: Any, rejected_urls: set[str]) -> Any:
    if isinstance(value, str):
        for url in rejected_urls:
            value = value.replace(url, "[redacted]")
        return value
    if isinstance(value, list):
        return [_redact_urls(item, rejected_urls) for item in value]
    if isinstance(value, Mapping):
        return {key: _redact_urls(item, rejected_urls) for key, item in value.items()}
    return value


def assess_market_comparison(
    opportunity: str,
    evidence_digest: str,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    plan = MarketQueryPlan.from_context(opportunity, evidence_digest)
    accepted: list[dict[str, Any]] = []
    adjacent: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in comparison.get("competitors") or []:
        if not isinstance(raw, Mapping):
            continue
        source = _assessed_source(plan, raw)
        {"accepted": accepted, "adjacent": adjacent, "rejected": rejected}[
            source["relevance"]["verdict"]
        ].append(source)

    accepted_finding_sources: list[dict[str, Any]] = []
    for raw in comparison.get("external_findings") or []:
        if not isinstance(raw, Mapping):
            continue
        assessed = _assessed_source(plan, _finding_as_source(raw))
        verdict = assessed["relevance"]["verdict"]
        if verdict == "accepted":
            accepted_finding_sources.append(assessed)
        else:
            {"adjacent": adjacent, "rejected": rejected}[verdict].append(assessed)

    if not accepted:
        for source in accepted_finding_sources:
            source["relevance"] = {
                **source["relevance"],
                "verdict": "adjacent",
                "reasons": ["no accepted direct competitor anchors this external market finding"],
            }
            adjacent.append(source)
        accepted_finding_sources = []
    accepted_findings = [
        {key: value for key, value in source.items() if key not in {"name", "positioning", "url"}}
        for source in accepted_finding_sources
    ]
    accepted_urls = _accepted_urls(accepted, accepted_findings)
    available = bool(accepted)
    gaps = list(comparison.get("gaps") or [])
    if not available and "external_market_evidence_unavailable" not in gaps:
        gaps.append("external_market_evidence_unavailable")
    result = {
        **dict(comparison),
        "competitors": accepted,
        "external_findings": accepted_findings,
        "sources": [
            item
            for item in comparison.get("sources") or []
            if str(item.get("url") if isinstance(item, Mapping) else item).strip() in accepted_urls
        ],
        "tool_provenance": {
            key: _filter_provenance(value, accepted_urls)
            for key, value in dict(comparison.get("tool_provenance") or {}).items()
        },
        "positioning_note": (
            comparison.get("positioning_note")
            if available
            else "No relevant external market evidence was accepted for this opportunity."
        ),
        "adjacent_sources": adjacent,
        "rejected_sources": rejected,
        "market_evidence_status": "available" if available else "unavailable",
        "gaps": gaps,
        "query_plan": plan.model_dump(mode="json"),
    }
    return GatedMarketComparison.model_validate(result).model_dump(mode="json", by_alias=True)


def unavailable_market_comparison(
    opportunity_id: str,
    positioning_note: str = "External market evidence is unavailable for this opportunity.",
) -> dict[str, Any]:
    return GatedMarketComparison.model_validate(
        {
            "opportunity_id": str(opportunity_id or "current-opportunity").strip() or "current-opportunity",
            "competitors": [],
            "positioning_note": positioning_note,
            "market_evidence_status": "unavailable",
            "gaps": ["external_market_evidence_unavailable"],
        }
    ).model_dump(mode="json", by_alias=True)


def accepted_market_sources(comparison: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in comparison.get("competitors") or [] if isinstance(item, Mapping)]


def public_market_comparison(comparison: Mapping[str, Any]) -> dict[str, Any]:
    if "market_evidence_status" not in comparison:
        public = MarketComparison.model_validate(comparison).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        public["_llm"] = _public_llm_metadata(public.get("_llm"))
        return public
    public = {
        key: value
        for key, value in dict(comparison).items()
        if key not in {"adjacent_sources", "rejected_sources", "query_plan"}
    }
    validated = GatedMarketComparison.model_validate(public).model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    competitors = _public_competitors(validated.get("competitors"))
    findings = _public_findings(validated.get("external_findings"))
    accepted_urls = _accepted_urls(competitors, findings)
    rejected_urls = {
        str(item.get("url") or item.get("source_url") or "").strip()
        for item in comparison.get("rejected_sources") or []
        if isinstance(item, Mapping)
    }
    rejected_urls.discard("")
    result: dict[str, Any] = {
        "opportunity_id": validated["opportunity_id"],
        "competitors": competitors,
        "positioning_note": (
            "Accepted external market evidence is available for this opportunity."
            if competitors
            else "No relevant external market evidence was accepted for this opportunity."
        ),
        "market_evidence_status": validated["market_evidence_status"],
        "gaps": list(validated.get("gaps") or []),
        "_llm": _public_llm_metadata(validated.get("_llm")),
        "errors": _public_errors(validated.get("errors")),
        "tool_provenance": _public_tool_provenance(validated.get("tool_provenance"), accepted_urls),
        "external_findings": findings,
        "sources": _public_source_items(validated.get("sources"), accepted_urls),
    }
    confidence = validated.get("confidence")
    if isinstance(confidence, str):
        result["confidence"] = confidence
    return _redact_urls(result, rejected_urls)


def market_relevance_trace(comparison: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "market_evidence_status": comparison.get("market_evidence_status"),
        "query_plan": dict(comparison.get("query_plan") or {}),
        "adjacent_sources": [dict(item) for item in comparison.get("adjacent_sources") or []],
        "rejected_sources": [dict(item) for item in comparison.get("rejected_sources") or []],
        "gaps": list(comparison.get("gaps") or []),
    }
