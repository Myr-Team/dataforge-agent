from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

try:
    from ..artifact_registry import get_artifact, reserve_artifact, write_artifact
    from ..lineage_sql import build_lineage_sql_connection_factory
    from ..outcome_store import upsert_demo_outcome_events
    from ..roi_scenario_store import upsert_demo_roi_scenario
    from ..run_store import complete_run, get_run, record_event, start_run, update_run_proposal
except ImportError:
    from artifact_registry import get_artifact, reserve_artifact, write_artifact
    from lineage_sql import build_lineage_sql_connection_factory
    from outcome_store import upsert_demo_outcome_events
    from roi_scenario_store import upsert_demo_roi_scenario
    from run_store import complete_run, get_run, record_event, start_run, update_run_proposal

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
                    daily_budget_usd=5 if clean_workspace_id == "demo-corpus" else None,
                )
            ),
            origin="synthetic_demo" if workspace_id == "demo-corpus" else "runtime",
        )
    if insight_repository is not None:
        _persist_demo_insights(insight_repository, tenant_ref=clean_tenant_ref, workspace_id=clean_workspace_id, result=result, now=anchor)
    return result


def _persist_demo_insights(repository: Any, *, tenant_ref: str, workspace_id: str, result: DemoSeedResult, now: datetime) -> None:
    generated = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    expires = generated + timedelta(days=1)
    findings = evaluate_default_anomalies(AnomalyEvaluationInput(events=list(result.events), trailing_token_median=1120, daily_budget_usd=5 if workspace_id == "demo-corpus" else None))
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
            evidence_refs=evidence_refs, evidence_state="synthetic_demo" if workspace_id == "demo-corpus" and kind == "finops" else "estimated", confidence=0.8 if kind == "finops" else 0.6,
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
    record_event_fn: Callable[[str | None, str, Any], None] = record_event,
    complete_run_fn: Callable[..., Any] = complete_run,
    artifact_writer_fn: Callable[[str, Mapping[str, Any]], bool] | None = None,
) -> dict[str, int]:
    created = 0
    reused = 0
    replaced = 0
    artifacts_created = 0
    artifacts_reused = 0
    for value in values:
        run_id = str(value.get("run_id") or "").strip()
        message = str(value.get("message") or "").strip()
        if not run_id or not message:
            raise ValueError("demo run evidence is incomplete")
        provenance = str(value.get("provenance") or "").strip()
        evidence_digest = _demo_run_evidence_digest(value) if provenance == "synthetic_demo" else ""
        try:
            current = get_run_fn(run_id)
        except (FileNotFoundError, ValueError):
            current = {}
        if current:
            if (
                str(current.get("workspace_id") or "") != workspace_id
                or str(current.get("origin") or "")
                != ("synthetic_demo" if str(value.get("provenance") or "") == "synthetic_demo" else "operations_demo")
                or str(current.get("message") or "") != message
            ):
                raise RuntimeError("demo run id is already owned by another record")
            current_digest = str((current.get("final") or {}).get("demo_evidence_digest") or "")
            if provenance == "synthetic_demo" and current_digest != evidence_digest:
                replaced += 1
                current = {}
            else:
                reused += 1
        if not current:
            start_run_fn(
                run_id,
                workspace_id,
                message,
                actor=None,
                trace_id=str(value.get("trace_id") or "") or None,
                trace_agent_id=str(value.get("trace_agent_id") or "") or None,
                origin="synthetic_demo" if provenance == "synthetic_demo" else "operations_demo",
            )
            for step in value.get("steps") or ():
                if not isinstance(step, Mapping):
                    continue
                event = str(step.get("event") or "").strip()
                data = step.get("data") if isinstance(step.get("data"), Mapping) else {}
                if event:
                    record_event_fn(run_id, event, dict(data))
            attempt = value.get("model_attempt")
            if isinstance(attempt, Mapping):
                tokens = attempt.get("tokens") if isinstance(attempt.get("tokens"), Mapping) else {}
                result_cache = attempt.get("result_cache") if isinstance(attempt.get("result_cache"), Mapping) else {}
                provider_cache = attempt.get("provider_cache") if isinstance(attempt.get("provider_cache"), Mapping) else {}
                record_event_fn(
                    run_id,
                    "model_response",
                    {
                        "agent": "synthetic_demo",
                        "execution_kind": "maf_agent",
                        "response_id": str(attempt.get("attempt_ref") or ""),
                        "request_ref": attempt.get("request_ref") or value.get("request_ref"),
                        "correlation_ref": attempt.get("correlation_ref") or value.get("correlation_ref"),
                        "attempt_ref": attempt.get("attempt_ref"),
                        "result_id": attempt.get("result_id") or value.get("result_id"),
                        "provider_type": attempt.get("provider_type"),
                        "model_id": attempt.get("model_id"),
                        "model": attempt.get("model_id"),
                        "deployment": attempt.get("model_id"),
                        "route": attempt.get("route"),
                        "route_evidence": attempt.get("route_evidence"),
                        "provenance": provenance,
                        "usage": {
                            "prompt": tokens.get("input"),
                            "completion": tokens.get("output"),
                            "reasoning": tokens.get("reasoning"),
                            "cached_input": tokens.get("cached_input"),
                            "total": tokens.get("total"),
                        },
                        "result_cache": {**result_cache, "provider": "redis"},
                        "cache": {**result_cache, "provider": "redis"},
                        "provider_cache": provider_cache,
                        "gateway_coverage": attempt.get("gateway_coverage"),
                        "cost_estimate": (
                            {
                                "status": "estimated",
                                "amount": attempt.get("cost_usd"),
                                "currency": "USD",
                                "official_price_key": attempt.get("official_price_key"),
                                "price_card_revision": attempt.get("price_card_revision"),
                            }
                            if attempt.get("cost_usd") is not None
                            else {"status": "unavailable", "reason": "price_not_configured"}
                        ),
                    },
                )
                record_event_fn(
                    run_id,
                    "audit",
                    {"provenance": provenance, "request_ref": value.get("request_ref"), "correlation_ref": value.get("correlation_ref"), "attempt_ref": (attempt or {}).get("attempt_ref")},
                )
            final_text = str(value.get("final_text") or "").strip()
            persisted = complete_run_fn(
                run_id,
                status=str(value.get("status") or "completed"),
                final=({"text": final_text, "demo_evidence_digest": evidence_digest} if final_text else {"demo_evidence_digest": evidence_digest}) if provenance == "synthetic_demo" else ({"text": final_text} if final_text else None),
            )
            if not persisted:
                raise RuntimeError("demo run evidence could not be persisted")
            created += 1
        artifact = value.get("artifact")
        if artifact_writer_fn is not None and isinstance(artifact, Mapping):
            if artifact_writer_fn(run_id, artifact):
                artifacts_created += 1
            else:
                artifacts_reused += 1
    result: dict[str, Any] = {
        "created": created,
        "reused": reused,
        "seed_batch": seed_key,
    }
    if replaced:
        result["replaced"] = replaced
    if artifact_writer_fn is not None:
        result.update(
            {
                "artifacts_created": artifacts_created,
                "artifacts_reused": artifacts_reused,
            }
        )
    return result


def _demo_run_evidence_digest(value: Mapping[str, Any]) -> str:
    safe = {
        key: value.get(key)
        for key in ("run_id", "message", "final_text", "status", "provenance", "request_ref", "correlation_ref", "steps", "model_attempt")
    }
    return hashlib.sha256(json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def persist_demo_artifact_evidence(
    run_id: str,
    artifact: Mapping[str, Any],
    *,
    get_run_fn: Callable[[str], Mapping[str, Any]] = get_run,
    get_artifact_fn: Callable[[str], Mapping[str, Any] | None] = get_artifact,
    reserve_artifact_fn: Callable[..., Mapping[str, Any]] = reserve_artifact,
    write_artifact_fn: Callable[..., Mapping[str, Any]] = write_artifact,
    update_run_proposal_fn: Callable[..., Mapping[str, Any] | None] = update_run_proposal,
    output_dir: Path | None = None,
) -> bool:
    current = get_run_fn(run_id)
    workspace_id = str(current.get("workspace_id") or "").strip()
    if not workspace_id or str(current.get("origin") or "") not in {"operations_demo", "synthetic_demo"}:
        raise RuntimeError("demo artifact run is not owned by the initializer")
    kind = str(artifact.get("kind") or "").strip()
    if kind not in {"pilot_plan", "action_plan"}:
        raise ValueError("demo artifact kind is not allowed")
    markdown = str(artifact.get("markdown") or "").strip()
    if not markdown:
        raise ValueError("demo artifact content is required")

    run_artifact = current.get("artifact") if isinstance(current.get("artifact"), Mapping) else {}
    final = current.get("final") if isinstance(current.get("final"), Mapping) else {}
    if not run_artifact and isinstance(final.get("artifact"), Mapping):
        run_artifact = final["artifact"]
    proposal = run_artifact.get("proposal") if isinstance(run_artifact.get("proposal"), Mapping) else {}
    urls = proposal.get("artifact_urls") if isinstance(proposal.get("artifact_urls"), Mapping) else {}
    existing_url = str(urls.get(kind) or "").strip()
    if existing_url:
        existing_name = Path(urlparse(existing_url).path).name
        try:
            existing = get_artifact_fn(existing_name)
        except (ValueError, RuntimeError):
            existing = None
        if (
            isinstance(existing, Mapping)
            and existing.get("status") == "ready"
            and existing.get("workspace_id") == workspace_id
            and existing.get("kind") == kind
        ):
            return False

    reservation = reserve_artifact_fn(
        workspace_id=workspace_id,
        kind=kind,
        content_type="text/markdown; charset=utf-8",
        suffix=".md",
    )
    target_dir = output_dir or Path(__file__).resolve().parents[2] / "generated-outputs"
    record = write_artifact_fn(
        reservation,
        markdown.encode("utf-8"),
        target_dir,
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    artifact_url = f"/api/artifacts/{record['artifact_name']}"
    updated = update_run_proposal_fn(
        run_id,
        {
            "title": str(artifact.get("title") or "运营分析产物").strip()[:120],
            "artifact_urls": {kind: artifact_url},
            "artifact_generated_at": {kind: generated_at},
            "generated_at": generated_at,
        },
    )
    if not isinstance(updated, Mapping):
        raise RuntimeError("demo artifact lineage could not be persisted")
    return True


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
                artifact_writer_fn=persist_demo_artifact_evidence,
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
