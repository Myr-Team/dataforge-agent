from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

try:
    from ..lineage_sql import build_lineage_sql_connection_factory
    from ..outcome_store import upsert_demo_outcome_events
    from ..roi_scenario_store import upsert_demo_roi_scenario
    from ..run_store import complete_run, get_run, start_run
except ImportError:
    from lineage_sql import build_lineage_sql_connection_factory
    from outcome_store import upsert_demo_outcome_events
    from roi_scenario_store import upsert_demo_roi_scenario
    from run_store import complete_run, get_run, start_run

from .demo_workspace_seed import DemoSeedResult, seed_demo_workspace
from .anomalies import AnomalyEvaluationInput, evaluate_default_anomalies
from .anomaly_store import FinOpsAnomalyService
from .insights import AgentFinding, FinOpsInsight, InsightWindow, insight_fingerprint
from .sql_demo_seed import SqlDemoSeedRepository
from .sql_member_budgets import SqlMemberBudgetRepository
from .sql_repository import SqlFinOpsRepository
from .sql_anomalies import SqlFinOpsAnomalyRepository
from .insight_repository import SqlInsightRepository


_OPAQUE_TENANT_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{7,127}$")
_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


def enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def initialize_demo_workspace(
    *,
    tenant_ref: str,
    allowed_tenant_ref: str,
    workspace_id: str,
    allowed_workspace_id: str,
    ledger_repository: Any,
    seed_repository: Any,
    budget_repository: Any,
    hmac_secret: str,
    roi_writer: Callable[..., Any] | None = None,
    outcome_writer: Callable[..., Any] | None = None,
    run_writer: Callable[..., Any] | None = None,
    anomaly_repository: Any | None = None,
    insight_repository: Any | None = None,
    now: datetime | None = None,
) -> DemoSeedResult:
    clean_tenant_ref = str(tenant_ref or "").strip()
    clean_workspace_id = str(workspace_id or "").strip()
    if not _OPAQUE_TENANT_REF.fullmatch(clean_tenant_ref):
        raise ValueError("tenant_ref must be an opaque DataForge tenant reference")
    if clean_tenant_ref != str(allowed_tenant_ref or "").strip():
        raise PermissionError("demo tenant is not allowlisted")
    if not _WORKSPACE_ID.fullmatch(clean_workspace_id):
        raise ValueError("workspace_id is invalid")
    if clean_workspace_id != str(allowed_workspace_id or "").strip():
        raise PermissionError("demo workspace is not allowlisted")
    clean_hmac_secret = str(hmac_secret or "").strip()
    if not clean_hmac_secret:
        raise RuntimeError("FinOps HMAC secret is required")
    result = seed_demo_workspace(
        ledger_repository,
        seed_repository,
        tenant_ref=clean_tenant_ref,
        workspace_id=clean_workspace_id,
        allowed_workspace_id=allowed_workspace_id,
        budget_repository=budget_repository,
        hmac_secret=clean_hmac_secret,
        roi_scenario_writer=roi_writer,
        outcome_events_writer=outcome_writer,
        run_evidence_writer=run_writer,
        now=now,
    )
    anchor = now or datetime.now(timezone.utc)
    if anomaly_repository is not None:
        FinOpsAnomalyService(anomaly_repository).upsert_findings(
            tenant_ref=clean_tenant_ref,
            findings=evaluate_default_anomalies(
                AnomalyEvaluationInput(
                    events=list(result.events),
                    trailing_token_median=1120,
                )
            ),
            origin="runtime",
        )
    if insight_repository is not None:
        _persist_demo_insights(insight_repository, tenant_ref=clean_tenant_ref, workspace_id=clean_workspace_id, result=result, now=anchor)
    return result


def _persist_demo_insights(repository: Any, *, tenant_ref: str, workspace_id: str, result: DemoSeedResult, now: datetime) -> None:
    generated = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    expires = generated + timedelta(days=1)
    findings = evaluate_default_anomalies(AnomalyEvaluationInput(events=list(result.events), trailing_token_median=1120))
    refs = [ref for finding in findings for ref in finding.evidence_refs][:5]
    outcome_refs = [str(item.get("source", {}).get("run_id")) for item in result.outcome_events if item.get("source", {}).get("run_id")]
    specs = (("finops", "demo_finops", "成本、缓存、失败与入口覆盖均有可追溯运营证据。", refs), ("roi", "demo_roi", "ROI 为场景估算；业务结果仍为未验证观察，不能视为已验证 ROI。", outcome_refs))
    for kind, trigger_type, summary, evidence_refs in specs:
        fingerprint = insight_fingerprint(tenant_ref=tenant_ref, workspace_ids=[workspace_id], agent_kind=kind, trigger_type=trigger_type, trigger_ref=result.batch, source_revision=result.batch)
        insight = FinOpsInsight(
            insight_id=f"ins_{fingerprint[:24]}", agent_kind=kind, tenant_ref=tenant_ref, workspace_ids=[workspace_id],
            window=InsightWindow(**{"from": generated - timedelta(days=30), "to": generated}), trigger_type=trigger_type,
            trigger_ref=result.batch, trigger_fingerprint=fingerprint, title="FinOps 运营就绪" if kind == "finops" else "ROI 证据就绪",
            summary=summary, findings=[AgentFinding(kind="risk" if kind == "finops" else "roi", statement=summary, evidence_refs=evidence_refs[:1])],
            evidence_refs=evidence_refs, evidence_state="observed" if kind == "finops" else "estimated", confidence=0.8 if kind == "finops" else 0.6,
            source_revisions={"demo_seed": result.batch}, evidence_gaps=["业务结果尚未独立验证"] if kind == "roi" else [], generated_at=generated, expires_at=expires, status="ready",
        )
        existing = repository.get_by_fingerprint(tenant_ref=tenant_ref, agent_kind=kind, trigger_fingerprint=fingerprint)
        if existing is not None:
            repository.replace(insight)
        else:
            repository.save(insight)


def persist_demo_run_evidence(
    workspace_id: str,
    values: tuple[Mapping[str, Any], ...],
    *,
    seed_key: str,
    get_run_fn: Callable[[str], Mapping[str, Any]] = get_run,
    start_run_fn: Callable[..., Any] = start_run,
    complete_run_fn: Callable[..., Any] = complete_run,
) -> dict[str, int]:
    created = 0
    reused = 0
    for value in values:
        run_id = str(value.get("run_id") or "").strip()
        message = str(value.get("message") or "").strip()
        if not run_id or not message:
            raise ValueError("demo run evidence is incomplete")
        try:
            current = get_run_fn(run_id)
        except (FileNotFoundError, ValueError):
            current = {}
        if current:
            if (
                str(current.get("workspace_id") or "") != workspace_id
                or str(current.get("origin") or "") != "operations_demo"
                or str(current.get("message") or "") != message
            ):
                raise RuntimeError("demo run id is already owned by another record")
            reused += 1
            continue
        start_run_fn(
            run_id,
            workspace_id,
            message,
            actor=None,
            trace_id=str(value.get("trace_id") or "") or None,
            trace_agent_id=str(value.get("trace_agent_id") or "") or None,
            origin="operations_demo",
        )
        final_text = str(value.get("final_text") or "").strip()
        persisted = complete_run_fn(
            run_id,
            status=str(value.get("status") or "completed"),
            final={"text": final_text} if final_text else None,
        )
        if not persisted:
            raise RuntimeError("demo run evidence could not be persisted")
        created += 1
    return {"created": created, "reused": reused, "seed_batch": seed_key}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Initialize the allowlisted DataForge operations demo workspace."
    )
    parser.add_argument(
        "--tenant-ref",
        default=os.environ.get("DF_FINOPS_DEMO_TENANT_REF", ""),
        help="Opaque tenant_ref returned by an authorized FinOps scope.",
    )
    parser.add_argument(
        "--workspace-id",
        default=os.environ.get("DF_FINOPS_DEMO_WORKSPACE_ID", ""),
    )
    arguments = parser.parse_args(argv)
    if not enabled(os.environ.get("DF_FINOPS_DEMO_SEED_ENABLED")):
        raise RuntimeError("DF_FINOPS_DEMO_SEED_ENABLED is disabled")
    if not enabled(os.environ.get("DF_FINOPS_SQL_ENABLED")):
        raise RuntimeError("DF_FINOPS_SQL_ENABLED is required")
    allowed_workspace_id = str(
        os.environ.get("DF_FINOPS_DEMO_WORKSPACE_ID") or ""
    ).strip()
    allowed_tenant_ref = str(
        os.environ.get("DF_FINOPS_DEMO_TENANT_REF") or ""
    ).strip()
    if arguments.workspace_id != allowed_workspace_id:
        raise PermissionError("demo workspace is not allowlisted")
    if arguments.tenant_ref != allowed_tenant_ref:
        raise PermissionError("demo tenant is not allowlisted")
    hmac_secret = str(os.environ.get("DF_FINOPS_HMAC_SECRET") or "").strip()
    if not hmac_secret:
        raise RuntimeError("FinOps HMAC secret is required")

    factory = build_lineage_sql_connection_factory()
    run_stats: dict[str, int] = {}
    outcome_stats: dict[str, Any] = {}

    def write_runs(
        workspace_id: str,
        values: tuple[Mapping[str, Any], ...],
        *,
        seed_key: str,
    ) -> None:
        run_stats.update(
            persist_demo_run_evidence(
                workspace_id,
                values,
                seed_key=seed_key,
            )
        )

    result = initialize_demo_workspace(
        tenant_ref=arguments.tenant_ref,
        allowed_tenant_ref=allowed_tenant_ref,
        workspace_id=arguments.workspace_id,
        allowed_workspace_id=allowed_workspace_id,
        ledger_repository=SqlFinOpsRepository(connection_factory=factory),
        seed_repository=SqlDemoSeedRepository(connection_factory=factory),
        budget_repository=SqlMemberBudgetRepository(connection_factory=factory),
        hmac_secret=hmac_secret,
        roi_writer=lambda workspace_id, payload, *, seed_key: (
            upsert_demo_roi_scenario(
                workspace_id,
                payload,
                actor=None,
                seed_key=seed_key,
            )
        ),
        outcome_writer=lambda workspace_id, values, *, seed_key: (
            outcome_stats.update(
                upsert_demo_outcome_events(
                    workspace_id,
                    values,
                    seed_key=seed_key,
                )
            )
        ),
        run_writer=write_runs,
        anomaly_repository=SqlFinOpsAnomalyRepository(connection_factory=factory),
        insight_repository=SqlInsightRepository(connection_factory=factory),
        now=datetime.now(timezone.utc),
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "workspace_id": arguments.workspace_id,
                "seed_batch": result.batch,
                "event_count": result.event_count,
                "created": result.created,
                "updated": result.updated,
                "run_evidence": run_stats,
                "outcome_evidence": outcome_stats,
                "roi_scenario": result.roi_scenario.get("title"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
