"""Deterministic, privacy-bounded Shenzhen site-selection demo facts.

This module deliberately produces data only.  The allowlisted initializer owns
persistence so the generator can be exercised and reconciled without touching
SQL, run storage, or a provider.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .models import FinOpsRequestEvent, ProviderCacheEvidence, ResultCacheEvidence, TokenUsage
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
    provider_type: str
    model_id: str
    route: str
    route_evidence: str
    tokens: TokenUsage
    provider_cache: ProviderCacheEvidence
    official_price_key: str | None
    price_card_revision: str | None
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
    result_cache: ResultCacheEvidence
    provider_cache: ProviderCacheEvidence
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
    event_by_request = {item.request_ref: item for item in events}
    attempt_by_request = {item.request_ref: item for item in attempts}
    run_by_id = {item.run_id: item for item in runs}
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
        if attempt.attempt_ref != fact.attempt_ref or attempt.run_id != fact.run_id or attempt.correlation_ref != fact.correlation_ref:
            errors.append(f"attempt lineage mismatch for {fact.request_ref}")
        if not run.steps or not run.model_attempts:
            errors.append(f"trace is incomplete for {fact.request_ref}")
        if event.tokens.total is not None and event.tokens.input is not None and event.tokens.output is not None:
            if event.tokens.total != event.tokens.input + event.tokens.output:
                errors.append(f"token total mismatch for {fact.request_ref}")
        if event.tokens.cached_input is not None and event.tokens.input is not None and event.tokens.cached_input > event.tokens.input:
            errors.append(f"cached input exceeds input for {fact.request_ref}")
        if event.tokens.reasoning is not None and event.tokens.output is not None and event.tokens.reasoning > event.tokens.output:
            errors.append(f"reasoning exceeds output for {fact.request_ref}")
        if event.tokens.model_dump() != attempt.tokens.model_dump():
            errors.append(f"event usage mismatch for {fact.request_ref}")
        if event.provider_cache.model_dump() != attempt.provider_cache.model_dump():
            errors.append(f"provider cache mismatch for {fact.request_ref}")
        if attempt.route_evidence != "synthetic":
            errors.append(f"route evidence mismatch for {fact.request_ref}")
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
    if len(events) != 2480 or len(facts) != 2480 or len(attempts) != 2480 or len(runs) != 2480:
        errors.append("declared request/run scale does not reconcile")
    cost = round(sum(item.estimated_cost.amount or 0.0 for item in events), 2)
    if cost != 206.40:
        errors.append(f"monthly cost is {cost:.2f}, expected 206.40")
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
            provider_type=provider_type,
            model_id=model_id,
            route="shenzhen-site-selection",
            route_evidence="synthetic",
            tokens=tokens,
            provider_cache=provider_cache,
            official_price_key=price_key,
            price_card_revision=revision,
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
            result_cache=result_cache,
            provider_cache=provider_cache,
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
