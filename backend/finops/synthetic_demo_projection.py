"""Safe browser projection derived from the deterministic Shenzhen demo bundle."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Any, Callable, Iterable

from .anomalies import AnomalyEvaluationInput, DetectedAnomaly, evaluate_default_anomalies
from .decision_service import build_risk_decision, build_roi_decision
from .demo_workspace_seed import _shenzhen_roi_scenario
from .models import FinOpsRequestEvent
from .official_pricing import load_official_price_catalog
from .roi_economics import calculate_roi
from .synthetic_demo import (
    DEMO_ANCHOR,
    DEMO_BATCH_ID,
    DEMO_WORKSPACE_ID,
    SyntheticDemoBundle,
    SyntheticRequestFact,
    build_synthetic_demo_bundle,
)


_POLICY_LABELS = {
    "error_rate": "调用失败率",
    "p95_latency": "响应时延",
    "daily_cost_budget": "成本预算",
    "token_spike": "Token 异常增长",
    "apim_coverage": "统一入口治理覆盖",
    "unpriced_requests": "计价覆盖",
    "cache_hit_rate": "缓存效率",
}
_POLICY_UNITS = {
    "error_rate": "%",
    "p95_latency": "ms",
    "daily_cost_budget": "%",
    "token_spike": "x",
    "apim_coverage": "%",
    "unpriced_requests": "%",
    "cache_hit_rate": "%",
}
_MINIMUM_SAMPLES = {
    "error_rate": 20,
    "p95_latency": 20,
    "daily_cost_budget": 1,
    "token_spike": 1,
    "apim_coverage": 1,
    "unpriced_requests": 1,
    "cache_hit_rate": 20,
}


def build_shenzhen_browser_projection() -> dict[str, Any]:
    bundle = build_synthetic_demo_bundle(
        workspace_id=DEMO_WORKSPACE_ID,
        batch_id=DEMO_BATCH_ID,
        anchor_at=DEMO_ANCHOR,
        seed="shenzhen-finops-v1",
    )
    findings = evaluate_default_anomalies(
        AnomalyEvaluationInput(
            events=list(bundle.events),
            trailing_token_median=1120,
            daily_budget_usd=5,
        )
    )
    event_by_request = {event.request_ref: event for event in bundle.events}
    fact_by_request = {fact.request_ref: fact for fact in bundle.request_facts}
    risk, scan, policy_refs = _risk_projection(
        bundle,
        findings,
        event_by_request=event_by_request,
        fact_by_request=fact_by_request,
    )
    first, second = bundle.request_facts[:2]
    requested_refs = set(policy_refs_value for refs in policy_refs.values() for policy_refs_value in refs)
    requested_refs.update((first.request_ref, second.request_ref))
    details = {
        request_ref: _request_detail(event_by_request[request_ref], fact_by_request[request_ref])
        for request_ref in requested_refs
    }
    traces = {
        first.run_id: _trace(bundle, first),
        second.run_id: _trace(bundle, second),
    }
    pricing, mappings = _pricing_projection(bundle)
    window = {
        "from": "2026-07-12T12:00:00Z",
        "to": DEMO_ANCHOR.isoformat().replace("+00:00", "Z"),
        "timezone": "UTC",
    }
    endpoints = {
        "bootstrap": _bootstrap(bundle, findings),
        "breakdowns": _workspace_breakdown(bundle),
        "agents": _agent_breakdowns(bundle),
        "roi": _roi_projection(bundle),
        "risk": risk,
        "risk_scan": scan,
        "risk_scan_history": {
            "items": [{
                "scan_ref": scan["scan_ref"],
                "status": "completed",
                "rules_triggered": 7,
                "request_sample_count": len(bundle.events),
                "evidence_coverage_pct": 100,
                "started_at": scan["started_at"],
                "finished_at": scan["finished_at"],
            }],
            "count": 1,
            "workspace_id": DEMO_WORKSPACE_ID,
        },
        "requests": details,
        "dashboard": {
            "workspace_id": DEMO_WORKSPACE_ID,
            "workspace": {"workspace_id": DEMO_WORKSPACE_ID, "name": "深圳选址演示"},
            "workspaces": [{"workspace_id": DEMO_WORKSPACE_ID, "name": "深圳选址演示"}],
            "runs": [_run_row(bundle, first), _run_row(bundle, second)],
            "conversations": [],
            "health": {"ok": True},
        },
        "run_summaries": {
            first.run_id: _run_summary(bundle, first),
            second.run_id: _run_summary(bundle, second),
        },
        "traces": traces,
        "pricing": pricing,
        "price_mappings": mappings,
    }
    return {
        "schema_version": "dataforge.synthetic-demo.browser.v1",
        "canonical_digest": bundle.canonical_digest,
        "summary": {
            "analysis_tasks": len(bundle.analysis_tasks),
            "requests": len(bundle.request_facts),
            "reports": len(bundle.reports),
            "evidence_reviews": len(bundle.evidence_review_tasks),
            "monthly_cost_usd": bundle.monthly_ai_operating_cost_usd,
        },
        "refs": {
            "request": first.request_ref,
            "run": first.run_id,
            "correlation": first.correlation_ref,
            "attempt": first.attempt_ref,
            "result": first.result_id,
            "hit_request": second.request_ref,
            "hit_run": second.run_id,
            "hit_correlation": second.correlation_ref,
            "hit_attempt": second.attempt_ref,
        },
        "gateway_counts": dict(Counter(event.gateway_coverage for event in bundle.events)),
        "model_counts": dict(Counter(event.model for event in bundle.events)),
        "policy_refs": policy_refs,
        "assistant_by_policy": _assistant_projection(policy_refs, event_by_request, fact_by_request),
        "window": window,
        "endpoints": endpoints,
    }


def _roi_projection(bundle: SyntheticDemoBundle) -> dict[str, Any]:
    seed = _shenzhen_roi_scenario(bundle)
    calculation = calculate_roi(
        hours_saved=float(seed["hours_saved"]),
        hourly_value=float(seed["hourly_value"]),
        avoided_loss_or_revenue=float(seed["avoided_loss_or_revenue"]),
        implementation_cost=float(seed["implementation_cost"]),
        monthly_fixed_cost=float(seed["monthly_fixed_cost"]),
        model_cost=float(seed["model_cost"]),
        evaluation_months=int(seed["evaluation_months"]),
    )
    demo_evidence = {
        "provenance": seed["provenance"],
        "production_quality_claim": seed["production_quality_claim"],
        "label": seed["demo_verified_label"],
        "measured": seed["measured"],
        "process": seed["process"],
        "actors": seed["actors"],
        "window": seed["window"],
        "source_refs": seed["source_refs"],
        "evidence_items": seed["evidence_items"],
    }
    scenario = {
        "scenario_id": bundle.scenario_id,
        "title": seed["title"],
        "status": "estimated",
        "inputs": {key: seed[key] for key in (
            "currency", "hours_saved", "hourly_value", "avoided_loss_or_revenue",
            "implementation_cost", "monthly_fixed_cost", "model_cost", "evaluation_months",
        )},
        "result": {"currency": "USD", **asdict(calculation)},
        "demo_evidence": demo_evidence,
    }
    decision = build_roi_decision(
        economics={"funnel": [], "scenarios": [scenario], "verified_roi": {}},
        roi_snapshot={"usage": {"runs": len(bundle.runs)}, "cost_evidence": {}},
        cost_value={"outcome_evidence": {}},
        unit_trend=[],
    )
    return _envelope(decision, bundle)


def _risk_projection(
    bundle: SyntheticDemoBundle,
    findings: list[DetectedAnomaly],
    *,
    event_by_request: dict[str, FinOpsRequestEvent],
    fact_by_request: dict[str, SyntheticRequestFact],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    policy_refs = {finding.policy_type: list(finding.evidence_refs) for finding in findings}
    opportunities: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    evidence_sets: list[dict[str, Any]] = []
    for finding in findings:
        label = _POLICY_LABELS[finding.policy_type]
        opportunities.append({
            "opportunity_id": f"opp-{finding.policy_type.replace('_', '-')}",
            "anomaly_id": finding.anomaly_id,
            "anomaly_status": "open",
            "policy_type": finding.policy_type,
            "title": f"深圳选址 · {label}",
            "recommendation": finding.recommendation,
            "impact": "high" if finding.severity == "critical" else "medium",
            "confidence": "high",
            "effort": "medium",
            "sample_count": finding.sample_count,
            "evidence_refs": list(finding.evidence_refs),
            "expected_impact": {"status": "estimated", "value": finding.observed_value, "currency": "USD"},
            "base_version": bundle.canonical_digest[:16],
        })
        items = []
        for request_ref in finding.evidence_refs:
            event = event_by_request[request_ref]
            fact = fact_by_request[request_ref]
            signal = _policy_signal(finding.policy_type, event)
            summary = {
                "request_ref": request_ref,
                "request_name": fact.title,
                "operation": f"深圳选址 · {label}",
                "model_label": event.model,
                "signal": signal,
                "latency_ms": event.latency_ms,
                "cache_state": event.result_cache.state,
                "status": event.status,
                "error_category": event.error_category,
                "visible_answer_summary": f"{label}使用 synthetic_demo 请求级证据。",
                "technical_refs": _technical_refs(fact),
            }
            summaries.append(summary)
            items.append({"request_ref": request_ref, "signal": signal})
        evidence_sets.append({
            "subject_type": "risk",
            "subject_id": finding.policy_type,
            "policy_type": finding.policy_type,
            "state": "synthetic_demo",
            "items": items,
        })
    decision = build_risk_decision(
        anomalies=[finding.model_dump(mode="json") for finding in findings],
        opportunities=opportunities,
        evidence_summaries=summaries,
        evidence_sets=evidence_sets,
        insight={
            "title": "深圳选址合成风险解读",
            "summary": "七类规则均来自确定性请求事实与真实 scanner；结论仅用于演示验证。",
            "status": "synthetic_demo",
            "evidence_refs": [ref for refs in policy_refs.values() for ref in refs[:1]],
        },
        drafts=[],
        governance_capability={"read_enabled": True, "draft_enabled": True, "actions_enabled": False, "typed_executors": []},
    )
    scan_findings = [{
        "policy_type": finding.policy_type,
        "status": "triggered",
        "severity": finding.severity,
        "observed_value": finding.observed_value,
        "threshold_value": finding.threshold_value,
        "unit": _POLICY_UNITS[finding.policy_type],
        "sample_count": finding.sample_count,
        "minimum_samples": _MINIMUM_SAMPLES[finding.policy_type],
        "reason": f"{_POLICY_LABELS[finding.policy_type]}达到真实 scanner 规则阈值。",
        "recommendation": finding.recommendation,
        "evidence_refs": list(finding.evidence_refs),
        "rule_revision": "synthetic-demo-scanner-v1",
    } for finding in findings]
    scan = {
        "scan_ref": f"rscan_{bundle.canonical_digest[:32]}",
        "status": "completed",
        "policy_revision": "synthetic-demo-scanner-v1",
        "ledger_revision": bundle.canonical_digest,
        "rules_evaluated": len(findings),
        "rules_triggered": len(findings),
        "rules_clear": 0,
        "rules_insufficient": 0,
        "request_sample_count": len(bundle.events),
        "evidence_coverage_pct": 100,
        "findings": scan_findings,
        "evidence_sets": evidence_sets,
        "started_at": bundle.anchor_at.isoformat().replace("+00:00", "Z"),
        "finished_at": bundle.anchor_at.isoformat().replace("+00:00", "Z"),
        "governance": {"mode": "read_only_scan", "automatic_actions": False, "explanation_agent_invoked": False},
    }
    return _envelope(decision, bundle), scan, policy_refs


def _assistant_projection(
    policy_refs: dict[str, list[str]],
    event_by_request: dict[str, FinOpsRequestEvent],
    fact_by_request: dict[str, SyntheticRequestFact],
) -> dict[str, dict[str, Any]]:
    responses: dict[str, dict[str, Any]] = {}
    for policy, refs in policy_refs.items():
        fact = fact_by_request[refs[0]]
        event = event_by_request[refs[0]]
        label = _POLICY_LABELS[policy]
        responses[policy] = {
            "status": "ready",
            "conversation_ref": f"conversation-{policy.replace('_', '-')}",
            "answer": f"深圳选址 {label}仅引用 synthetic_demo 证据。",
            "sections": {
                "conclusion": f"深圳选址的{label}需要按当前规则证据复核。",
                "basis": f"当前回答绑定 {len(refs[:3])} 条请求级证据。",
                "impact": "结论仅适用于演示验证，不代表生产观测。",
                "recommendation": "从请求、运行、关联与尝试引用继续下钻。",
                "caveat": "演示验证结果 · 合成数据。",
            },
            "evidence_state": "synthetic_demo",
            "evidence_refs": refs[:3],
            "evidence_labels": [f"{label}证据 {index + 1}" for index in range(len(refs[:3]))],
            "knowledge_citations": [],
            "context": {**_technical_refs(fact), "policy_type": policy, "provenance": event.provenance},
        }
    return responses


def _bootstrap(bundle: SyntheticDemoBundle, findings: list[DetectedAnomaly]) -> dict[str, Any]:
    events = list(bundle.events)
    priced = sum(event.estimated_cost.amount is not None for event in events)
    governed = sum(event.gateway_coverage == "apim_governed" for event in events)
    token_fields = {
        key: sum(int(getattr(event.tokens, key) or 0) for event in events)
        for key in ("input", "output", "cached_input", "reasoning", "total")
    }
    eligible = [event for event in events if event.result_cache.eligible]
    cache_counts = Counter(event.result_cache.state for event in events)
    hit_rate = round(sum(event.result_cache.state == "hit" for event in eligible) / len(eligible) * 100, 4)
    latencies = sorted(int(event.latency_ms or 0) for event in events if event.latency_ms is not None)
    p95 = latencies[min(len(latencies) - 1, int((len(latencies) - 1) * 0.95))]
    failed = sum(event.status == "failed" for event in events)
    trend = _trend_rows(events)
    departments = _breakdown_rows(events, lambda event: str(event.department_id or "未分配"))
    return _envelope({
        "coverage": {
            "observed_requests": len(events),
            "apim_governed_requests": governed,
            "apim_coverage_pct": round(governed / len(events) * 100, 4),
        },
        "overview": {
            "freshness": {"generated_at": bundle.anchor_at.isoformat().replace("+00:00", "Z")},
            "data_status": "partial",
            "trust": {
                "pricing": {"priced_requests": priced, "unpriced_requests": len(events) - priced, "coverage_pct": round(priced / len(events) * 100, 4), "state": "partial"},
                "tokens": {"known_requests": len(events), "unknown_requests": 0, "coverage_pct": 100, "state": "complete"},
                "apim": {"app_observed_requests": len(events) - governed, "apim_governed_requests": governed, "coverage_pct": round(governed / len(events) * 100, 4), "state": "synthetic_demo"},
            },
            "metrics": {
                "requests": len(events),
                "tokens": {**token_fields, "known_requests": len(events), "unknown_requests": 0},
                "estimated_cost": {"amount": bundle.monthly_ai_operating_cost_usd, "priced_requests": priced, "unpriced_requests": len(events) - priced, "status": "partial"},
                "budget": {"amount": 5, "used_amount": bundle.monthly_ai_operating_cost_usd, "usage_pct": round(bundle.monthly_ai_operating_cost_usd / 5 * 100, 2), "status": "partial", "source": "daily_cost_budget"},
                "latency": {"p50_ms": latencies[len(latencies) // 2], "p95_ms": p95, "known_requests": len(latencies)},
                "error_rate_pct": round(failed / len(events) * 100, 4),
                "success_rate_pct": round((len(events) - failed) / len(events) * 100, 4),
                "cache_hit_rate_pct": hit_rate,
                "cache": {
                    "eligible_requests": len(eligible),
                    "hit": cache_counts["hit"],
                    "miss": cache_counts["miss"],
                    "bypassed": cache_counts["bypassed"],
                    "unavailable": cache_counts["unavailable"],
                    "avoided_tokens": None,
                    "estimated_savings": None,
                    "data_status": "unavailable",
                    "reason": "avoided_tokens_not_recorded",
                },
                "apim_coverage_pct": round(governed / len(events) * 100, 4),
            },
        },
        "trend": {"bucket": "day", "items": trend},
        "departments": {"items": departments},
        "anomalies": {"count": len(findings), "items": [{
            **finding.model_dump(mode="json"),
            "title": f"深圳选址 · {_POLICY_LABELS[finding.policy_type]}",
            "observed_at": bundle.anchor_at.isoformat().replace("+00:00", "Z"),
            "evidence_state": "synthetic_demo",
            "provenance": "synthetic_demo",
        } for finding in findings]},
    }, bundle)


def _workspace_breakdown(bundle: SyntheticDemoBundle) -> dict[str, Any]:
    return _envelope({
        "items": _breakdown_rows(bundle.events, lambda _event: DEMO_WORKSPACE_ID),
        "count": 1,
    }, bundle)


def _agent_breakdowns(bundle: SyntheticDemoBundle) -> dict[str, Any]:
    return _envelope({
        "agents": _breakdown_rows(bundle.events, lambda event: str(event.agent_id or "未记录")),
        "models": _breakdown_rows(bundle.events, lambda event: str(event.model or "未记录"), include_tokens=True),
    }, bundle)


def _breakdown_rows(
    events: Iterable[FinOpsRequestEvent],
    key: Callable[[FinOpsRequestEvent], str],
    *,
    include_tokens: bool = False,
) -> list[dict[str, Any]]:
    groups: dict[str, list[FinOpsRequestEvent]] = defaultdict(list)
    for event in events:
        groups[key(event)].append(event)
    rows = []
    for name, items in sorted(groups.items()):
        costs = [event.estimated_cost.amount for event in items if event.estimated_cost.amount is not None]
        latencies = sorted(int(event.latency_ms or 0) for event in items if event.latency_ms is not None)
        eligible = [event for event in items if event.result_cache.eligible]
        row: dict[str, Any] = {
            "key": name,
            "requests": len(items),
            "tokens": sum(int(event.tokens.total or 0) for event in items),
            "estimated_cost": round(sum(costs), 8) if costs else None,
            "error_rate_pct": round(sum(event.status == "failed" for event in items) / len(items) * 100, 4),
            "success_rate_pct": round(sum(event.status == "succeeded" for event in items) / len(items) * 100, 4),
            "p95_latency_ms": latencies[min(len(latencies) - 1, int((len(latencies) - 1) * 0.95))] if latencies else None,
            "cache_hit_rate_pct": round(sum(event.result_cache.state == "hit" for event in eligible) / len(eligible) * 100, 4) if eligible else None,
            "data_status": "estimated" if len(costs) == len(items) else "unpriced" if not costs else "partial",
        }
        if include_tokens:
            input_tokens = sum(int(event.tokens.input or 0) for event in items)
            cached_tokens = sum(int(event.tokens.cached_input or 0) for event in items)
            row["token_composition"] = {
                "input": input_tokens,
                "cached_input": cached_tokens,
                "uncached_input": input_tokens - cached_tokens,
                "output": sum(int(event.tokens.output or 0) for event in items),
                "reasoning": sum(int(event.tokens.reasoning or 0) for event in items),
                "known_requests": len(items),
                "data_status": "synthetic_demo",
            }
        rows.append(row)
    return rows


def _trend_rows(events: list[FinOpsRequestEvent]) -> list[dict[str, Any]]:
    groups: dict[str, list[FinOpsRequestEvent]] = defaultdict(list)
    for event in events:
        groups[event.occurred_at.date().isoformat()].append(event)
    rows = []
    for day, items in sorted(groups.items()):
        cache_counts = Counter(event.result_cache.state for event in items)
        eligible = [event for event in items if event.result_cache.eligible]
        rows.append({
            "bucket": f"{day}T00:00:00Z",
            "requests": len(items),
            "tokens": {
                "input": sum(int(event.tokens.input or 0) for event in items),
                "output": sum(int(event.tokens.output or 0) for event in items),
                "cached_input": sum(int(event.tokens.cached_input or 0) for event in items),
                "reasoning": sum(int(event.tokens.reasoning or 0) for event in items),
                "total": sum(int(event.tokens.total or 0) for event in items),
            },
            "estimated_cost": round(sum(event.estimated_cost.amount or 0 for event in items), 8),
            "p95_latency_ms": max(int(event.latency_ms or 0) for event in items),
            "cache": {
                "eligible_requests": len(eligible),
                "hit": cache_counts["hit"],
                "miss": cache_counts["miss"],
                "bypassed": cache_counts["bypassed"],
                "unavailable": cache_counts["unavailable"],
                "avoided_tokens": None,
                "estimated_savings": None,
                "data_status": "unavailable",
                "reason": "avoided_tokens_not_recorded",
            },
            "data_status": "synthetic_demo",
        })
    return rows


def _request_detail(event: FinOpsRequestEvent, fact: SyntheticRequestFact) -> dict[str, Any]:
    return {
        "display": {"name": fact.title, "operation": "深圳选址评估", "occurred_at": event.occurred_at.isoformat().replace("+00:00", "Z")},
        "status": event.status,
        "metrics": {
            "latency_ms": event.latency_ms,
            "tokens": event.tokens.model_dump(mode="json"),
            "cache": event.cache.model_dump(mode="json"),
            "result_cache": event.result_cache.model_dump(mode="json"),
            "provider_cache": event.provider_cache.model_dump(mode="json"),
            "estimated_cost": event.estimated_cost.model_dump(mode="json"),
            "gateway_coverage": event.gateway_coverage,
            "evidence_state": event.evidence_state,
            "provenance": event.provenance,
        },
        "business_request": {"status": "unavailable", "text": "未保留完整请求正文。"},
        "business_response": {"status": "unavailable", "text": "未保留完整模型回答。"},
        "technical_refs": _technical_refs(fact),
        "timeline": [{"stage": "model_response", "label": "合成模型尝试，非生产观测", "status": "synthetic_demo", "latency_ms": event.latency_ms}],
        "links": {},
    }


def _trace(bundle: SyntheticDemoBundle, fact: SyntheticRequestFact) -> list[dict[str, Any]]:
    attempt = next(item for item in bundle.model_attempts if item.request_ref == fact.request_ref)
    event = next(item for item in bundle.events if item.request_ref == fact.request_ref)
    detail = {
        "model_id": attempt.model_id,
        "deployment": attempt.deployment,
        "provider_type": attempt.provider_type,
        "provenance": "synthetic_demo",
        **_technical_refs(fact),
        "result_id": fact.result_id,
        "route": attempt.route,
        "route_evidence": attempt.route_evidence,
        "gateway_coverage": attempt.gateway_coverage,
        "tokens": attempt.tokens.model_dump(mode="json"),
        "provider_cache": attempt.provider_cache.model_dump(mode="json"),
        "result_cache": attempt.result_cache.model_dump(mode="json"),
        "cost_estimate": event.estimated_cost.model_dump(mode="json"),
    }
    return [{
        "index": 0,
        "event": "model_response",
        "agent": attempt.agent_id,
        "role": "深圳选址证据汇总",
        "status": "completed",
        "time": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "duration_ms": event.latency_ms,
        "source": "synthetic_demo",
        "detail": detail,
        "data": detail,
    }]


def _run_row(bundle: SyntheticDemoBundle, fact: SyntheticRequestFact) -> dict[str, Any]:
    run = next(item for item in bundle.runs if item.run_id == fact.run_id)
    event = next(item for item in bundle.events if item.request_ref == fact.request_ref)
    return {
        "run_id": run.run_id,
        "title": run.title,
        "summary": "合成演示结果，需以真实业务验证为准。",
        "status": "done",
        "verdict": "feasible",
        "completed_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "time": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "step_count": len(run.steps),
    }


def _run_summary(bundle: SyntheticDemoBundle, fact: SyntheticRequestFact) -> dict[str, Any]:
    event = next(item for item in bundle.events if item.request_ref == fact.request_ref)
    return {
        "run_id": fact.run_id,
        "status": "done",
        "verdict": "feasible",
        "confidence": "synthetic_demo",
        "started_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "finished_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "duration_ms": event.latency_ms,
        "agent_count": 1,
        "tokens": {"total": event.tokens.total, "prompt": event.tokens.input, "completion": event.tokens.output},
        "tool_calls": {"total": 0, "ok": 0, "fail": 0},
        "audit": {"status": "synthetic_demo"},
        "evidence": {"source": "synthetic_demo", "tokens": "run.models[].usage"},
    }


def _pricing_projection(bundle: SyntheticDemoBundle) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = load_official_price_catalog()
    price_keys = {attempt.official_price_key for attempt in bundle.model_attempts if attempt.official_price_key}
    items = []
    for price_key in sorted(price_keys):
        price = catalog.get(price_key)
        if price is None:
            continue
        item = price.model_dump(mode="json")
        item["provider"] = "azure_foundry" if item["provider"] == "azure-openai" else item["provider"]
        item["official_price_key"] = item.pop("price_key")
        items.append(item)
    mappings = {}
    for attempt in bundle.model_attempts:
        if not attempt.official_price_key:
            continue
        mappings[attempt.deployment] = {
            "deployment": attempt.deployment,
            "official_price_key": attempt.official_price_key,
            "mapping_revision": attempt.mapping_revision,
            "price_card_revision": attempt.price_card_revision,
        }
    return {"count": len(items), "items": items}, {"count": len(mappings), "items": list(mappings.values())}


def _policy_signal(policy: str, event: FinOpsRequestEvent) -> dict[str, Any]:
    if policy == "error_rate":
        return {"metric": "request_status", "value": event.status, "unit": "status"}
    if policy == "p95_latency":
        return {"metric": "latency_ms", "value": event.latency_ms, "unit": "ms"}
    if policy == "daily_cost_budget":
        return {"metric": "estimated_cost", "value": event.estimated_cost.amount, "unit": "USD"}
    if policy == "token_spike":
        return {"metric": "tokens_total", "value": event.tokens.total, "unit": "tokens"}
    if policy == "apim_coverage":
        return {"metric": "gateway_coverage", "value": event.gateway_coverage, "unit": "state"}
    if policy == "unpriced_requests":
        return {"metric": "pricing_status", "value": "unpriced" if event.estimated_cost.amount is None else "priced", "unit": "status"}
    return {"metric": "cache_state", "value": event.result_cache.state, "unit": "state"}


def _technical_refs(fact: SyntheticRequestFact) -> dict[str, str]:
    return {
        "request_ref": fact.request_ref,
        "run_id": fact.run_id,
        "correlation_ref": fact.correlation_ref,
        "attempt_ref": fact.attempt_ref,
    }


def _envelope(payload: dict[str, Any], bundle: SyntheticDemoBundle) -> dict[str, Any]:
    return {
        "scope": {"workspace_ids": [DEMO_WORKSPACE_ID], "workspace_count": 1},
        "window": {
            "from": "2026-07-12T12:00:00Z",
            "to": bundle.anchor_at.isoformat().replace("+00:00", "Z"),
            "timezone": "UTC",
        },
        "freshness": {"generated_at": bundle.anchor_at.isoformat().replace("+00:00", "Z")},
        "currency": "USD",
        "data_status": "partial",
        **payload,
    }


def main() -> int:
    # ASCII escapes keep the Node fixture transport deterministic on Windows
    # regardless of the active console code page; JSON.parse restores Unicode.
    print(json.dumps(build_shenzhen_browser_projection(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_shenzhen_browser_projection"]
