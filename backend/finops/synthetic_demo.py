"""Deterministic, privacy-bounded Shenzhen site-selection demo facts.

This module deliberately produces data only.  The allowlisted initializer owns
persistence so the generator can be exercised and reconciled without touching
SQL, run storage, or a provider.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .models import EstimatedCost, FinOpsRequestEvent, ProviderCacheEvidence, ResultCacheEvidence, TokenUsage
from .official_pricing import estimate_official_cost, load_official_price_catalog


DEMO_WORKSPACE_ID = "demo-corpus"
DEMO_SCENARIO_ID = "shenzhen-site-selection-v1"
DEMO_BATCH_ID = "shenzhen-site-selection-20260811-v1"
DEMO_ANCHOR = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
_SEED_REVISION = "shenzhen-finops-v1"
_SOURCE_FILES = (
    "demo_brief.md",
    "candidate_sites.csv",
    "signal_density.csv",
    "surrounding_env.csv",
    "device_events_summary.json",
)
_TERRA_KEY = "azure-openai:gpt-5.6-terra:global-standard:global"
_DEEPSEEK_KEY = "deepseek:deepseek-v4-flash:official"


@dataclass(frozen=True)
class AnalysisTask:
    task_id: str
    title: str
    agent_id: str
    provenance: str = "synthetic_demo"


@dataclass(frozen=True)
class Report:
    report_id: str
    task_id: str
    title: str
    provenance: str = "synthetic_demo"


@dataclass(frozen=True)
class EvidenceReviewTask:
    review_id: str
    report_id: str
    title: str
    provenance: str = "synthetic_demo"


@dataclass(frozen=True)
class SafeModelAttempt:
    attempt_ref: str
    request_ref: str
    run_id: str
    correlation_ref: str
    result_id: str
    task_id: str
    provider_type: str
    model_id: str
    deployment: str
    route: str
    route_evidence: str
    gateway_coverage: str
    status: str
    department_id: str
    agent_id: str
    tokens: TokenUsage
    result_cache: ResultCacheEvidence
    provider_cache: ProviderCacheEvidence
    estimated_cost: EstimatedCost
    official_price_key: str | None
    price_card_revision: str | None
    mapping_revision: int | None
    cost_usd: float | None


@dataclass(frozen=True)
class SyntheticRequestFact:
    request_ref: str
    run_id: str
    correlation_ref: str
    attempt_ref: str
    task_id: str
    result_id: str
    title: str
    provider_type: str
    model_id: str
    deployment: str
    route: str
    gateway_coverage: str
    status: str
    department_id: str
    agent_id: str
    tokens: TokenUsage
    result_cache: ResultCacheEvidence
    provider_cache: ProviderCacheEvidence
    estimated_cost: EstimatedCost
    provenance: str = "synthetic_demo"


@dataclass(frozen=True)
class SyntheticRun:
    run_id: str
    correlation_ref: str
    request_ref: str
    title: str
    steps: tuple[dict[str, str], ...]
    model_attempts: tuple[SafeModelAttempt, ...]
    provenance: str = "synthetic_demo"


@dataclass(frozen=True)
class RoiEvidence:
    scenario_monthly_benefit_usd: float
    monthly_operating_input_usd: float
    scenario_roi_pct: float
    measured_paired_evaluations: int
    measured_historical_hours: float
    measured_assisted_hours: float
    demo_reviewed_savings_hours: float
    outcome_actor_ref: str
    reviewer_actor_ref: str
    production_quality_claim: bool
    demo_verified_label: str


@dataclass(frozen=True)
class ReconciliationReport:
    ok: bool
    request_count: int
    run_count: int
    attempt_count: int
    monthly_cost_usd: float
    errors: tuple[str, ...]


@dataclass(frozen=True)
class SyntheticDemoBundle:
    workspace_id: str
    scenario_id: str
    batch_id: str
    seed: str
    anchor_at: datetime
    corpus_digest: str
    canonical_digest: str
    analysis_tasks: tuple[AnalysisTask, ...]
    request_facts: tuple[SyntheticRequestFact, ...]
    events: tuple[FinOpsRequestEvent, ...]
    model_attempts: tuple[SafeModelAttempt, ...]
    runs: tuple[SyntheticRun, ...]
    reports: tuple[Report, ...]
    evidence_review_tasks: tuple[EvidenceReviewTask, ...]
    roi: RoiEvidence
    monthly_ai_operating_cost_usd: float


def build_synthetic_demo_bundle(
    *,
    workspace_id: str,
    batch_id: str,
    anchor_at: datetime,
    seed: str,
) -> SyntheticDemoBundle:
    """Build the one allowlisted Shenzhen demo projection without persistence."""

    if workspace_id != DEMO_WORKSPACE_ID:
        raise PermissionError("synthetic demo workspace is not allowlisted")
    if batch_id != DEMO_BATCH_ID:
        raise ValueError("synthetic demo batch is not recognized")
    if seed != _SEED_REVISION:
        raise ValueError("synthetic demo seed is not recognized")
    anchor = _utc(anchor_at)
    if anchor != DEMO_ANCHOR:
        raise ValueError("synthetic demo anchor must be fixed")

    tasks = tuple(
        AnalysisTask(
            task_id=f"shenzhen-task-{index:03d}",
            title=f"深圳候选点位分析 {index:03d}",
            agent_id=("Coordinator", "Corpus Analyst", "Market Researcher", "Feasibility Analyst", "Auditor", "Producer")[index % 6],
        )
        for index in range(1, 97)
    )
    reports = tuple(
        Report(
            report_id=f"shenzhen-report-{index:03d}",
            task_id=tasks[(index - 1) % len(tasks)].task_id,
            title=f"深圳选址评估报告 {index:03d}",
        )
        for index in range(1, 79)
    )
    reviews = tuple(
        EvidenceReviewTask(
            review_id=f"shenzhen-evidence-review-{index:03d}",
            report_id=reports[index - 1].report_id,
            title=f"深圳选址证据审阅 {index:03d}",
        )
        for index in range(1, 19)
    )
    events, request_facts, attempts, runs = _build_request_projection(
        workspace_id=workspace_id,
        batch_id=batch_id,
        anchor=anchor,
        tasks=tasks,
    )
    corpus_digest = _corpus_digest()
    roi = RoiEvidence(
        scenario_monthly_benefit_usd=6240.0,
        monthly_operating_input_usd=1586.40,
        scenario_roi_pct=293.3,
        measured_paired_evaluations=18,
        measured_historical_hours=17.8,
        measured_assisted_hours=8.1,
        demo_reviewed_savings_hours=174.6,
        outcome_actor_ref="synthetic_site_selection_outcome_reviewer",
        reviewer_actor_ref="synthetic_site_selection_finance_reviewer",
        production_quality_claim=False,
        demo_verified_label="演示验证结果 · 合成数据",
    )
    canonical_digest = _canonical_digest(
        workspace_id=workspace_id,
        batch_id=batch_id,
        anchor=anchor,
        seed=seed,
        corpus_digest=corpus_digest,
        analysis_tasks=tasks,
        request_facts=request_facts,
        events=events,
        model_attempts=attempts,
        runs=runs,
        reports=reports,
        evidence_review_tasks=reviews,
        roi=roi,
        monthly_ai_operating_cost_usd=round(sum(event.estimated_cost.amount or 0.0 for event in events), 2),
    )
    bundle = SyntheticDemoBundle(
        workspace_id=workspace_id,
        scenario_id=DEMO_SCENARIO_ID,
        batch_id=batch_id,
        seed=seed,
        anchor_at=anchor,
        corpus_digest=corpus_digest,
        canonical_digest=canonical_digest,
        analysis_tasks=tasks,
        request_facts=request_facts,
        events=events,
        model_attempts=attempts,
        runs=runs,
        reports=reports,
        evidence_review_tasks=reviews,
        roi=roi,
        monthly_ai_operating_cost_usd=round(sum(event.estimated_cost.amount or 0.0 for event in events), 2),
    )
    report = reconcile_synthetic_demo(bundle)
    if not report.ok:
        raise ValueError("synthetic demo reconciliation failed: " + "; ".join(report.errors))
    return bundle


def reconcile_synthetic_demo(bundle: SyntheticDemoBundle) -> ReconciliationReport:
    errors: list[str] = []
    if bundle.canonical_digest != canonical_digest_for_bundle(bundle):
        errors.append("canonical digest mismatch")
    events = bundle.events
    facts = bundle.request_facts
    attempts = bundle.model_attempts
    runs = bundle.runs
    _unique(errors, "request refs", [item.request_ref for item in facts])
    _unique(errors, "run ids", [item.run_id for item in facts])
    _unique(errors, "correlation refs", [item.correlation_ref for item in facts])
    _unique(errors, "attempt refs", [item.attempt_ref for item in facts])
    _unique(errors, "event request refs", [item.request_ref for item in events])
    _unique(errors, "attempt request refs", [item.request_ref for item in attempts])
    _unique(errors, "trace run ids", [item.run_id for item in runs])
    event_by_request = {item.request_ref: item for item in events}
    attempt_by_request = {item.request_ref: item for item in attempts}
    run_by_id = {item.run_id: item for item in runs}
    result_ids = {item.result_id for item in facts}
    for fact in facts:
        event = event_by_request.get(fact.request_ref)
        attempt = attempt_by_request.get(fact.request_ref)
        run = run_by_id.get(fact.run_id)
        if event is None or attempt is None or run is None:
            errors.append(f"missing lineage for {fact.request_ref}")
            continue
        if event.run_id != fact.run_id or event.correlation_ref != fact.correlation_ref:
            errors.append(f"event lineage mismatch for {fact.request_ref}")
        if event.attempt_ref != fact.attempt_ref or event.result_id != fact.result_id:
            errors.append(f"event reference mismatch for {fact.request_ref}")
        if event.provenance != "synthetic_demo" or event.usage_source != "synthetic_demo":
            errors.append(f"event provenance mismatch for {fact.request_ref}")
        if (
            attempt.attempt_ref != fact.attempt_ref
            or attempt.run_id != fact.run_id
            or attempt.correlation_ref != fact.correlation_ref
            or attempt.result_id != fact.result_id
            or attempt.task_id != fact.task_id
        ):
            errors.append(f"attempt lineage mismatch for {fact.request_ref}")
        if run.request_ref != fact.request_ref or run.correlation_ref != fact.correlation_ref:
            errors.append(f"run lineage mismatch for {fact.request_ref}")
        if not run.steps or len(run.model_attempts) != 1:
            errors.append(f"trace is incomplete for {fact.request_ref}")
            run_attempt = None
        else:
            run_attempt = run.model_attempts[0]
            if run_attempt != attempt:
                errors.append(f"run attempt mismatch for {fact.request_ref}")
        if event.model != fact.model_id or event.model != attempt.model_id:
            errors.append(f"model mismatch for {fact.request_ref}")
        if event.deployment != fact.deployment or event.deployment != attempt.deployment:
            errors.append(f"deployment mismatch for {fact.request_ref}")
        if event.route != fact.route or event.route != attempt.route:
            errors.append(f"route mismatch for {fact.request_ref}")
        if event.gateway_coverage != fact.gateway_coverage or event.gateway_coverage != attempt.gateway_coverage:
            errors.append(f"gateway mismatch for {fact.request_ref}")
        if event.status != fact.status or event.status != attempt.status:
            errors.append(f"status mismatch for {fact.request_ref}")
        if event.department_id != fact.department_id or event.department_id != attempt.department_id:
            errors.append(f"department mismatch for {fact.request_ref}")
        if event.agent_id != fact.agent_id or event.agent_id != attempt.agent_id:
            errors.append(f"agent mismatch for {fact.request_ref}")
        if event.tokens.total is not None and event.tokens.input is not None and event.tokens.output is not None:
            if event.tokens.total != event.tokens.input + event.tokens.output:
                errors.append(f"token total mismatch for {fact.request_ref}")
        if event.tokens.cached_input is not None and event.tokens.input is not None and event.tokens.cached_input > event.tokens.input:
            errors.append(f"cached input exceeds input for {fact.request_ref}")
        if event.tokens.reasoning is not None and event.tokens.output is not None and event.tokens.reasoning > event.tokens.output:
            errors.append(f"reasoning exceeds output for {fact.request_ref}")
        if event.tokens.model_dump() != attempt.tokens.model_dump() or event.tokens.model_dump() != fact.tokens.model_dump():
            errors.append(f"event usage mismatch for {fact.request_ref}")
        if (
            event.result_cache.model_dump() != fact.result_cache.model_dump()
            or event.result_cache.model_dump() != attempt.result_cache.model_dump()
            or event.cache.state != event.result_cache.state
            or event.cache.eligible != event.result_cache.eligible
        ):
            errors.append(f"result cache mismatch for {fact.request_ref}")
        if (
            event.provider_cache.model_dump() != attempt.provider_cache.model_dump()
            or event.provider_cache.model_dump() != fact.provider_cache.model_dump()
        ):
            errors.append(f"provider cache mismatch for {fact.request_ref}")
        if attempt.route_evidence != "synthetic":
            errors.append(f"route evidence mismatch for {fact.request_ref}")
        event_cost = event.estimated_cost.model_dump()
        if event_cost != attempt.estimated_cost.model_dump() or event_cost != fact.estimated_cost.model_dump():
            errors.append(f"cost evidence mismatch for {fact.request_ref}")
        if (
            attempt.official_price_key != event.estimated_cost.official_price_key
            or attempt.price_card_revision != event.estimated_cost.price_card_revision
            or attempt.mapping_revision != event.estimated_cost.mapping_revision
            or attempt.cost_usd != event.estimated_cost.amount
        ):
            errors.append(f"attempt price evidence mismatch for {fact.request_ref}")
        if event.estimated_cost.amount is not None:
            if not attempt.official_price_key or not attempt.price_card_revision:
                errors.append(f"price mapping missing for {fact.request_ref}")
            else:
                expected = estimate_official_cost(attempt.official_price_key, 1, attempt.tokens)
                if expected is None or event.estimated_cost.model_dump() != expected.model_dump() or attempt.cost_usd != expected.amount:
                    errors.append(f"price recomputation mismatch for {fact.request_ref}")
        elif attempt.cost_usd is not None or attempt.official_price_key or attempt.price_card_revision:
            errors.append(f"unpriced attempt mismatch for {fact.request_ref}")
        if fact.result_cache.state == "hit" and not fact.result_cache.source_result_version:
            errors.append(f"result-cache hit lacks source for {fact.request_ref}")
        elif fact.result_cache.state == "hit" and fact.result_cache.source_result_version not in result_ids:
            errors.append(f"result-cache source is unresolved for {fact.request_ref}")
        if run_attempt is not None and run_attempt.request_ref != fact.request_ref:
            errors.append(f"trace request reference mismatch for {fact.request_ref}")
    if len(events) != 2480 or len(facts) != 2480 or len(attempts) != 2480 or len(runs) != 2480:
        errors.append("declared request/run scale does not reconcile")
    _reconcile_grouped_totals(errors, events=events, facts=facts, attempts=attempts, runs=runs)
    task_ids = [item.task_id for item in bundle.analysis_tasks]
    report_ids = [item.report_id for item in bundle.reports]
    review_ids = [item.review_id for item in bundle.evidence_review_tasks]
    _unique(errors, "analysis task ids", task_ids)
    _unique(errors, "report ids", report_ids)
    _unique(errors, "evidence review ids", review_ids)
    if len(bundle.analysis_tasks) != 96:
        errors.append("expected 96 analysis tasks")
    if len(bundle.reports) != 78:
        errors.append("expected 78 reports")
    if len(bundle.evidence_review_tasks) != 18:
        errors.append("expected 18 evidence reviews")
    task_id_set = set(task_ids)
    report_id_set = set(report_ids)
    if any(item.task_id not in task_id_set for item in facts):
        errors.append("request task reference is unresolved")
    if any(item.task_id not in task_id_set for item in bundle.reports):
        errors.append("report task reference is unresolved")
    if any(item.report_id not in report_id_set for item in bundle.evidence_review_tasks):
        errors.append("evidence review report reference is unresolved")
    referenced_tasks = {item.task_id for item in facts} | {item.task_id for item in bundle.reports}
    if task_id_set - referenced_tasks:
        errors.append("analysis task reference coverage is incomplete")
    cost = round(sum(item.estimated_cost.amount or 0.0 for item in events), 2)
    if cost != 206.40:
        errors.append(f"monthly cost is {cost:.2f}, expected 206.40")
    if bundle.monthly_ai_operating_cost_usd != cost:
        errors.append("declared monthly cost does not reconcile")
    if bundle.roi.production_quality_claim:
        errors.append("synthetic demo cannot make a production-quality claim")
    if bundle.roi.outcome_actor_ref == bundle.roi.reviewer_actor_ref:
        errors.append("outcome and reviewer actors must be distinct")
    if bundle.roi.demo_reviewed_savings_hours != round(
        (bundle.roi.measured_historical_hours - bundle.roi.measured_assisted_hours) * bundle.roi.measured_paired_evaluations,
        1,
    ):
        errors.append("reviewed savings mismatch")
    if bundle.roi.scenario_monthly_benefit_usd <= 0 or bundle.roi.monthly_operating_input_usd <= 0:
        errors.append("ROI currency/window inputs are incomplete")
    return ReconciliationReport(
        ok=not errors,
        request_count=len(facts),
        run_count=len(runs),
        attempt_count=len(attempts),
        monthly_cost_usd=cost,
        errors=tuple(errors),
    )


def _reconcile_grouped_totals(
    errors: list[str],
    *,
    events: tuple[FinOpsRequestEvent, ...],
    facts: tuple[SyntheticRequestFact, ...],
    attempts: tuple[SafeModelAttempt, ...],
    runs: tuple[SyntheticRun, ...],
) -> None:
    projections = {
        "facts": _semantic_totals(facts),
        "attempts": _semantic_totals(attempts),
        "runs": _semantic_totals(tuple(attempt for run in runs for attempt in run.model_attempts)),
    }
    expected = _semantic_totals(events)
    labels = {
        "status": "status",
        "department": "department",
        "agent": "agent",
        "model": "model",
        "route": "route",
        "gateway": "gateway",
        "tokens": "token",
        "result_cache": "result cache",
        "provider_cache": "provider cache",
        "cost": "cost",
    }
    for projection_name, projection in projections.items():
        for key, label in labels.items():
            if projection[key] != expected[key]:
                errors.append(f"{label} grouped totals mismatch for {projection_name}")
    for key, label in labels.items():
        if expected[key] != _DECLARED_GROUP_TOTALS[key]:
            errors.append(f"{label} declared totals mismatch")


def _semantic_totals(items: tuple[object, ...]) -> dict[str, object]:
    dimensions = {
        "status": Counter(),
        "department": Counter(),
        "agent": Counter(),
        "model": Counter(),
        "route": Counter(),
        "gateway": Counter(),
    }
    token_totals = Counter()
    result_cache = Counter()
    provider_cache = Counter()
    cost_shape = Counter()
    cost_amount = Decimal("0")
    for item in items:
        dimensions["status"][str(getattr(item, "status", ""))] += 1
        dimensions["department"][str(getattr(item, "department_id", "") or "")] += 1
        dimensions["agent"][str(getattr(item, "agent_id", "") or "")] += 1
        dimensions["model"][str(getattr(item, "model", None) or getattr(item, "model_id", "") or "")] += 1
        dimensions["route"][str(getattr(item, "route", "") or "")] += 1
        dimensions["gateway"][str(getattr(item, "gateway_coverage", "") or "")] += 1
        tokens = getattr(item, "tokens")
        for key in ("input", "output", "cached_input", "reasoning", "total"):
            value = getattr(tokens, key)
            token_totals[f"{key}:known"] += int(value is not None)
            token_totals[f"{key}:value"] += int(value or 0)
        result = getattr(item, "result_cache")
        result_cache[(result.state, result.eligible, result.reason, result.policy_revision)] += 1
        result_cache["sources"] += int(bool(result.source_result_version))
        provider = getattr(item, "provider_cache")
        provider_cache[f"state:{provider.state}"] += 1
        provider_cache[f"evidence:{provider.evidence_state}"] += 1
        provider_cache["hit_tokens"] += int(provider.hit_tokens or 0)
        provider_cache["miss_tokens"] += int(provider.miss_tokens or 0)
        estimate = getattr(item, "estimated_cost")
        cost_shape[(estimate.status, estimate.currency, estimate.official_price_key, estimate.price_card_revision, estimate.mapping_revision)] += 1
        if estimate.amount is not None:
            cost_amount += Decimal(str(estimate.amount))
    return {
        **dimensions,
        "tokens": token_totals,
        "result_cache": result_cache,
        "provider_cache": provider_cache,
        "cost": {"shape": cost_shape, "amount": cost_amount},
    }


_DECLARED_GROUP_TOTALS: dict[str, object] = {
    "status": Counter({"succeeded": 2467, "failed": 13}),
    "department": Counter({"Site Intelligence": 620, "Market Research": 620, "Feasibility": 620, "Audit": 620}),
    "agent": Counter({"Corpus Analyst": 414, "Market Researcher": 414, "Feasibility Analyst": 413, "Auditor": 413, "Producer": 413, "Coordinator": 413}),
    "model": Counter({"gpt-5.6-terra": 2315, "site-selection-unpriced-adapter": 160, "deepseek-v4-flash": 5}),
    "route": Counter({"shenzhen-site-selection": 2480}),
    "gateway": Counter({"apim_governed": 2349, "app_observed": 131}),
    "tokens": Counter({
        "input:known": 2480,
        "input:value": 248002,
        "output:known": 2480,
        "output:value": 14609945,
        "cached_input:known": 0,
        "cached_input:value": 0,
        "reasoning:known": 0,
        "reasoning:value": 0,
        "total:known": 2480,
        "total:value": 14857947,
    }),
    "result_cache": Counter({
        ("miss", True, "eligible", 1): 1653,
        ("bypassed", False, "live_data", 1): 551,
        ("hit", True, "eligible", 1): 276,
        "sources": 276,
    }),
    "provider_cache": Counter({
        "state:unavailable": 2478,
        "evidence:unavailable": 2478,
        "miss_tokens": 120,
        "hit_tokens": 80,
        "evidence:synthetic": 2,
        "state:miss": 1,
        "state:hit": 1,
    }),
    "cost": {
        "shape": Counter({
            ("estimated", "USD", "azure-openai:gpt-5.6-terra:global-standard:global", "azure-retail-2026-07-27", 1): 2315,
            ("unavailable", "USD", None, None, None): 160,
            ("estimated", "USD", "deepseek:deepseek-v4-flash:official", "deepseek-2026-07-28-v1", 1): 5,
        }),
        "amount": Decimal("206.400000"),
    },
}


def _build_request_projection(*, workspace_id: str, batch_id: str, anchor: datetime, tasks: tuple[AnalysisTask, ...]) -> tuple[tuple[FinOpsRequestEvent, ...], tuple[SyntheticRequestFact, ...], tuple[SafeModelAttempt, ...], tuple[SyntheticRun, ...]]:
    catalog = load_official_price_catalog()
    terra_revision = str(catalog.get(_TERRA_KEY).revision)
    deepseek_revision = str(catalog.get(_DEEPSEEK_KEY).revision)
    event_rows: list[FinOpsRequestEvent] = []
    facts: list[SyntheticRequestFact] = []
    attempts: list[SafeModelAttempt] = []
    runs: list[SyntheticRun] = []
    priced_terra_indices = list(range(165, 2480))
    extra_output, extra_input = 1_219_945, 2
    distributed, remainder = divmod(extra_output, len(priced_terra_indices))
    for index in range(2480):
        request_ref = _opaque("req", batch_id, str(index))
        run_id = f"synthetic-shenzhen-site-selection-{index + 1:04d}"
        correlation_ref = _opaque("corr", batch_id, str(index))
        attempt_ref = _opaque("attempt", batch_id, str(index))
        task = tasks[index % len(tasks)]
        result_id = f"result-shenzhen-{index:04d}"
        provider_type, model_id, price_key, revision = (
            ("deepseek", "deepseek-v4-flash", _DEEPSEEK_KEY, deepseek_revision)
            if index < 5
            else ("azure_foundry", "gpt-5.6-terra", _TERRA_KEY, terra_revision)
        )
        priced = index < 5 or index >= 165
        input_tokens = 100
        output_tokens = 5_000 if index < 5 else 5_400
        if index in priced_terra_indices:
            position = index - priced_terra_indices[0]
            output_tokens += distributed + (1 if position < remainder else 0)
            if position == 0:
                input_tokens += extra_input
        if not priced:
            model_id = "site-selection-unpriced-adapter"
            price_key = None
            revision = None
        occurred_at = _occurred_at(index, anchor)
        result_cache = _result_cache(index, batch_id)
        provider_cache = _provider_cache(index)
        tokens = TokenUsage(input=input_tokens, output=output_tokens, total=input_tokens + output_tokens)
        estimate = estimate_official_cost(price_key, 1, tokens) if price_key else None
        cost_usd = estimate.amount if estimate is not None else None
        event = FinOpsRequestEvent.model_validate({
            "request_ref": request_ref,
            "occurred_at": occurred_at,
            "call_class": "model",
            "tenant_ref": "synthetic_demo_tenant",
            "department_id": ("Site Intelligence", "Market Research", "Feasibility", "Audit")[index % 4],
            "workspace_id": workspace_id,
            "actor_ref": f"synthetic_actor_{index % 6:02d}",
            "run_id": run_id,
            "agent_id": task.agent_id,
            "model": model_id,
            "deployment": model_id,
            "route": "shenzhen-site-selection",
            "execution_kind": "maf_agent",
            "status": "failed" if index >= 2360 and index % 9 == 0 else "succeeded",
            "error_category": "provider_5xx" if index >= 2360 and index % 9 == 0 else None,
            "latency_ms": 6_400 if index >= 2360 and index % 10 == 0 else 900 + (index % 700),
            "tokens": tokens.model_dump(),
            "cache": {"state": result_cache.state, "eligible": result_cache.eligible},
            "result_cache": result_cache.model_dump(),
            "provider_cache": provider_cache.model_dump(),
            "gateway_coverage": "app_observed" if index % 19 == 0 else "apim_governed",
            "estimated_cost": (
                estimate.model_dump() if estimate is not None else {"status": "unavailable", "currency": "USD"}
            ),
            "evidence_state": "synthetic_demo",
            "correlation_ref": correlation_ref,
            "usage_source": "synthetic_demo",
            "provenance": "synthetic_demo",
            "scenario_id": DEMO_SCENARIO_ID,
            "seed_batch": batch_id,
            "attempt_ref": attempt_ref,
            "result_id": result_id,
        })
        attempt = SafeModelAttempt(
            attempt_ref=attempt_ref,
            request_ref=request_ref,
            run_id=run_id,
            correlation_ref=correlation_ref,
            result_id=result_id,
            task_id=task.task_id,
            provider_type=provider_type,
            model_id=model_id,
            deployment=model_id,
            route="shenzhen-site-selection",
            route_evidence="synthetic",
            gateway_coverage=event.gateway_coverage,
            status=event.status,
            department_id=str(event.department_id or ""),
            agent_id=str(event.agent_id or ""),
            tokens=tokens,
            result_cache=result_cache,
            provider_cache=provider_cache,
            estimated_cost=event.estimated_cost,
            official_price_key=price_key,
            price_card_revision=revision,
            mapping_revision=event.estimated_cost.mapping_revision,
            cost_usd=cost_usd,
        )
        fact = SyntheticRequestFact(
            request_ref=request_ref,
            run_id=run_id,
            correlation_ref=correlation_ref,
            attempt_ref=attempt_ref,
            task_id=task.task_id,
            result_id=result_id,
            title=f"深圳选址分析请求 {index + 1:04d}",
            provider_type=provider_type,
            model_id=model_id,
            deployment=model_id,
            route="shenzhen-site-selection",
            gateway_coverage=event.gateway_coverage,
            status=event.status,
            department_id=str(event.department_id or ""),
            agent_id=str(event.agent_id or ""),
            tokens=tokens,
            result_cache=result_cache,
            provider_cache=provider_cache,
            estimated_cost=event.estimated_cost,
        )
        run = SyntheticRun(
            run_id=run_id,
            correlation_ref=correlation_ref,
            request_ref=request_ref,
            title=f"深圳选址评估 · {task.title}",
            steps=(
                {"event": "retrieval", "label": "已授权深圳选址资料检索"},
                {"event": "model_response", "label": "合成模型尝试，非生产观测"},
                {"event": "audit", "label": "候选点位证据审阅"},
            ),
            model_attempts=(attempt,),
        )
        event_rows.append(event)
        facts.append(fact)
        attempts.append(attempt)
        runs.append(run)
    return tuple(event_rows), tuple(facts), tuple(attempts), tuple(runs)


def _result_cache(index: int, batch_id: str) -> ResultCacheEvidence:
    if index == 0:
        return ResultCacheEvidence(eligible=True, state="miss", reason="eligible", policy_revision=1)
    if index == 1:
        return _result_cache_hit(0, batch_id)
    if index % 9 == 0:
        return _result_cache_hit(index - 9, batch_id)
    if index % 3:
        return ResultCacheEvidence(eligible=True, state="miss", reason="eligible", policy_revision=1)
    return ResultCacheEvidence(eligible=False, state="bypassed", reason="live_data", policy_revision=1)


def _result_cache_hit(source_index: int, batch_id: str) -> ResultCacheEvidence:
    return ResultCacheEvidence(
        eligible=True,
        state="hit",
        reason="eligible",
        policy_revision=1,
        source_result_version=f"result-shenzhen-{source_index:04d}",
    )


def _provider_cache(index: int) -> ProviderCacheEvidence:
    if index == 0:
        return ProviderCacheEvidence(state="miss", hit_tokens=0, miss_tokens=100, hit_rate_pct=0, evidence_state="synthetic")
    if index == 1:
        return ProviderCacheEvidence(state="hit", hit_tokens=80, miss_tokens=20, hit_rate_pct=80, evidence_state="synthetic")
    return ProviderCacheEvidence(state="unavailable", evidence_state="unavailable")


def _occurred_at(index: int, anchor: datetime) -> datetime:
    if index >= 2360:
        return anchor - timedelta(minutes=12) + timedelta(seconds=(index - 2360) * 6)
    return anchor - timedelta(days=30) + timedelta(minutes=index * 17)


def canonical_digest_for_bundle(bundle: SyntheticDemoBundle) -> str:
    """Return the canonical digest for every nonvolatile synthetic demo fact."""
    return _canonical_digest(
        workspace_id=bundle.workspace_id,
        batch_id=bundle.batch_id,
        anchor=bundle.anchor_at,
        seed=bundle.seed,
        corpus_digest=bundle.corpus_digest,
        analysis_tasks=bundle.analysis_tasks,
        request_facts=bundle.request_facts,
        events=bundle.events,
        model_attempts=bundle.model_attempts,
        runs=bundle.runs,
        reports=bundle.reports,
        evidence_review_tasks=bundle.evidence_review_tasks,
        roi=bundle.roi,
        monthly_ai_operating_cost_usd=bundle.monthly_ai_operating_cost_usd,
    )


def _canonical_digest(*, workspace_id: str, batch_id: str, anchor: datetime, seed: str, corpus_digest: str, analysis_tasks: tuple[AnalysisTask, ...], request_facts: tuple[SyntheticRequestFact, ...], events: tuple[FinOpsRequestEvent, ...], model_attempts: tuple[SafeModelAttempt, ...], runs: tuple[SyntheticRun, ...], reports: tuple[Report, ...], evidence_review_tasks: tuple[EvidenceReviewTask, ...], roi: RoiEvidence, monthly_ai_operating_cost_usd: float) -> str:
    payload = {
        "schema_version": "dataforge.synthetic-demo.v1",
        "workspace_id": workspace_id,
        "scenario_id": DEMO_SCENARIO_ID,
        "batch_id": batch_id,
        "anchor_at": anchor.isoformat().replace("+00:00", "Z"),
        "seed": seed,
        "corpus_digest": corpus_digest,
        "analysis_tasks": _canonical_value(analysis_tasks),
        "request_facts": _canonical_value(request_facts),
        "events": _canonical_value(events),
        "model_attempts": _canonical_value(model_attempts),
        "runs": _canonical_value(runs),
        "reports": _canonical_value(reports),
        "evidence_review_tasks": _canonical_value(evidence_review_tasks),
        "roi": _canonical_value(roi),
        "monthly_ai_operating_cost_usd": monthly_ai_operating_cost_usd,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return _canonical_value(value.model_dump(mode="json"))
    if hasattr(value, "__dataclass_fields__"):
        return _canonical_value(asdict(value))
    return value


def _corpus_digest() -> str:
    root = Path(__file__).resolve().parents[2] / "workspaces" / DEMO_WORKSPACE_ID
    manifest = root / "workspace.json"
    sources = root / "raw_docs"
    digest = hashlib.sha256()
    for path in (manifest, *(sources / name for name in _SOURCE_FILES)):
        if not path.is_file():
            raise ValueError(f"demo corpus source is missing: {path.name}")
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _opaque(prefix: str, batch_id: str, ordinal: str) -> str:
    digest = hashlib.sha256(f"{batch_id}:{ordinal}".encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _unique(errors: list[str], label: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        errors.append(f"duplicate {label}")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "DEMO_ANCHOR",
    "DEMO_BATCH_ID",
    "DEMO_SCENARIO_ID",
    "DEMO_WORKSPACE_ID",
    "ReconciliationReport",
    "SyntheticDemoBundle",
    "build_synthetic_demo_bundle",
    "canonical_digest_for_bundle",
    "reconcile_synthetic_demo",
]
