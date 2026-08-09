from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.finops.demo_initialize import (
    initialize_demo_workspace,
    persist_demo_artifact_evidence,
    persist_demo_run_evidence,
)
from backend.finops.demo_seed_repository import InMemoryDemoSeedRepository
from backend.finops.anomaly_store import FinOpsAnomalyService, InMemoryAnomalyRepository
from backend.finops.anomalies import (
    AnomalyEvaluationInput,
    DetectedAnomaly,
    evaluate_default_anomalies,
)
from backend.finops.insight_repository import InMemoryInsightRepository
from backend.finops.demo_workspace_seed import seed_demo_workspace
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.repository import InMemoryFinOpsRepository


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


def test_initializer_is_bounded_to_one_opaque_tenant_and_workspace() -> None:
    result = initialize_demo_workspace(
        tenant_ref="tenant_demo_ref",
        allowed_tenant_ref="tenant_demo_ref",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        ledger_repository=InMemoryFinOpsRepository(),
        seed_repository=InMemoryDemoSeedRepository(),
        budget_repository=InMemoryMemberBudgetRepository(),
        hmac_secret="test-secret",
        now=NOW,
    )
    assert result.event_count >= 140

    with pytest.raises(PermissionError, match="allowlisted"):
        initialize_demo_workspace(
            tenant_ref="tenant_demo_ref",
            allowed_tenant_ref="tenant_demo_ref",
            workspace_id="ws-other",
            allowed_workspace_id="ws-demo",
            ledger_repository=InMemoryFinOpsRepository(),
            seed_repository=InMemoryDemoSeedRepository(),
            budget_repository=InMemoryMemberBudgetRepository(),
            hmac_secret="test-secret",
            now=NOW,
        )


def test_initializer_rejects_a_different_tenant_and_missing_hmac_secret() -> None:
    arguments = {
        "workspace_id": "ws-demo",
        "allowed_workspace_id": "ws-demo",
        "ledger_repository": InMemoryFinOpsRepository(),
        "seed_repository": InMemoryDemoSeedRepository(),
        "budget_repository": InMemoryMemberBudgetRepository(),
        "now": NOW,
    }

    with pytest.raises(PermissionError, match="tenant is not allowlisted"):
        initialize_demo_workspace(
            tenant_ref="tenant_other_ref",
            allowed_tenant_ref="tenant_demo_ref",
            hmac_secret="test-secret",
            **arguments,
        )

    with pytest.raises(RuntimeError, match="HMAC"):
        initialize_demo_workspace(
            tenant_ref="tenant_demo_ref",
            allowed_tenant_ref="tenant_demo_ref",
            hmac_secret="",
            **arguments,
        )


def test_initializer_persists_runs_before_source_linked_outcomes() -> None:
    writes: list[str] = []

    result = initialize_demo_workspace(
        tenant_ref="tenant_demo_ref",
        allowed_tenant_ref="tenant_demo_ref",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        ledger_repository=InMemoryFinOpsRepository(),
        seed_repository=InMemoryDemoSeedRepository(),
        budget_repository=InMemoryMemberBudgetRepository(),
        hmac_secret="test-secret",
        run_writer=lambda *_args, **_kwargs: writes.append("runs"),
        outcome_writer=lambda *_args, **_kwargs: writes.append("outcomes"),
        now=NOW,
    )

    assert writes == ["runs", "outcomes"]
    assert len(result.outcome_events) == 2


def test_demo_run_writer_creates_once_and_reuses_owned_records() -> None:
    stored: dict[str, dict[str, object]] = {}
    starts: list[str] = []
    completes: list[str] = []
    values = (
        {
            "run_id": "run_demo_recent_001",
            "message": "重新分析相同数据",
            "final_text": "已完成分析。",
            "status": "completed",
            "trace_id": "a" * 32,
            "trace_agent_id": "Product Architect",
        },
    )

    def get_run(run_id: str):
        if run_id not in stored:
            raise FileNotFoundError(run_id)
        return stored[run_id]

    def start_run(run_id: str, workspace_id: str, message: str, **kwargs):
        starts.append(run_id)
        stored[run_id] = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "message": message,
            "origin": kwargs["origin"],
        }

    def complete_run(run_id: str, **_kwargs):
        completes.append(run_id)
        return stored[run_id]

    first = persist_demo_run_evidence(
        "ws-demo",
        values,
        seed_key="operations-v1",
        get_run_fn=get_run,
        start_run_fn=start_run,
        complete_run_fn=complete_run,
    )
    second = persist_demo_run_evidence(
        "ws-demo",
        values,
        seed_key="operations-v1",
        get_run_fn=get_run,
        start_run_fn=start_run,
        complete_run_fn=complete_run,
    )

    assert first == {
        "created": 1,
        "reused": 0,
        "seed_batch": "operations-v1",
    }
    assert second == {
        "created": 0,
        "reused": 1,
        "seed_batch": "operations-v1",
    }
    assert starts == completes == ["run_demo_recent_001"]


def test_demo_run_writer_persists_each_declared_artifact_once() -> None:
    stored: dict[str, dict[str, object]] = {}
    artifact_writes: list[tuple[str, str]] = []
    values = (
        {
            "run_id": "run_demo_recent_001",
            "message": "分析当前工作区的运营数据",
            "final_text": "已形成运营复盘摘要。",
            "status": "completed",
            "trace_id": "a" * 32,
            "trace_agent_id": "Product Architect",
            "artifact": {
                "kind": "pilot_plan",
                "title": "运营优化试点计划",
                "markdown": "# 运营优化试点计划\n\n按证据复核运营改进。",
            },
        },
    )

    def get_run(run_id: str):
        if run_id not in stored:
            raise FileNotFoundError(run_id)
        return stored[run_id]

    def start_run(run_id: str, workspace_id: str, message: str, **kwargs):
        stored[run_id] = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "message": message,
            "origin": kwargs["origin"],
        }

    def complete_run(run_id: str, **_kwargs):
        return stored[run_id]

    def write_artifact(run_id: str, artifact: dict[str, object]) -> bool:
        current = stored[run_id]
        if current.get("demo_artifact_kind") == artifact["kind"]:
            return False
        current["demo_artifact_kind"] = artifact["kind"]
        artifact_writes.append((run_id, str(artifact["kind"])))
        return True

    first = persist_demo_run_evidence(
        "ws-demo",
        values,
        seed_key="operations-v3",
        get_run_fn=get_run,
        start_run_fn=start_run,
        complete_run_fn=complete_run,
        artifact_writer_fn=write_artifact,
    )
    second = persist_demo_run_evidence(
        "ws-demo",
        values,
        seed_key="operations-v3",
        get_run_fn=get_run,
        start_run_fn=start_run,
        complete_run_fn=complete_run,
        artifact_writer_fn=write_artifact,
    )

    assert first["artifacts_created"] == 1
    assert first["artifacts_reused"] == 0
    assert second["artifacts_created"] == 0
    assert second["artifacts_reused"] == 1
    assert artifact_writes == [("run_demo_recent_001", "pilot_plan")]


def test_demo_artifact_writer_creates_a_ready_openable_artifact_once(tmp_path) -> None:
    current: dict[str, object] = {
        "run_id": "run_demo_recent_001",
        "workspace_id": "ws-demo",
        "origin": "operations_demo",
        "artifact": {"proposal": {"artifact_urls": {}}},
    }
    records: dict[str, dict[str, object]] = {}
    writes: list[bytes] = []
    updates: list[dict[str, object]] = []

    def reserve(**kwargs):
        return {**kwargs, "artifact_name": "artifact-ready-demo.md"}

    def write(reservation, content, output_dir):
        assert output_dir == tmp_path
        writes.append(content)
        record = {
            **reservation,
            "status": "ready",
            "bytes": len(content),
        }
        records[str(record["artifact_name"])] = record
        return record

    def update(_run_id: str, proposal: dict[str, object]):
        updates.append(proposal)
        current["artifact"] = {"proposal": proposal}
        return current

    artifact = {
        "kind": "pilot_plan",
        "title": "运营优化试点计划",
        "markdown": "# 运营优化试点计划\n\n按证据复核。",
    }
    arguments = {
        "get_run_fn": lambda _run_id: current,
        "get_artifact_fn": lambda name: records.get(name),
        "reserve_artifact_fn": reserve,
        "write_artifact_fn": write,
        "update_run_proposal_fn": update,
        "output_dir": tmp_path,
    }

    assert persist_demo_artifact_evidence(
        "run_demo_recent_001", artifact, **arguments
    ) is True
    assert persist_demo_artifact_evidence(
        "run_demo_recent_001", artifact, **arguments
    ) is False
    assert len(writes) == len(updates) == 1
    assert updates[0]["artifact_urls"] == {
        "pilot_plan": "/api/artifacts/artifact-ready-demo.md"
    }


def test_initializer_upgrades_legacy_batch_and_persists_repeatable_demo_findings() -> None:
    ledger = InMemoryFinOpsRepository()
    seeds = InMemoryDemoSeedRepository()
    anomalies = InMemoryAnomalyRepository()
    insights = InMemoryInsightRepository()
    anomaly_service = FinOpsAnomalyService(anomalies)
    anomaly_service.upsert_findings(
        tenant_ref="tenant_demo_ref",
        findings=[
            DetectedAnomaly(
                anomaly_id="anom-manual-review",
                policy_type="error_rate",
                severity="warning",
                observed_value=7,
                threshold_value=5,
                sample_count=25,
                workspace_ids=["ws-demo"],
                recommendation="Keep this manually managed finding.",
                evidence_refs=["req_manual_review_001"],
            )
        ],
    )
    anomaly_service.acknowledge(
        tenant_ref="tenant_demo_ref",
        anomaly_id="anom-manual-review",
        actor_ref="actor-reviewer",
    )

    legacy = seed_demo_workspace(
        ledger, seeds, tenant_ref="tenant_demo_ref", workspace_id="ws-demo",
        allowed_workspace_id="ws-demo", batch="operations-v1", now=NOW,
    )

    first = initialize_demo_workspace(
        tenant_ref="tenant_demo_ref", allowed_tenant_ref="tenant_demo_ref",
        workspace_id="ws-demo", allowed_workspace_id="ws-demo", ledger_repository=ledger,
        seed_repository=seeds, budget_repository=InMemoryMemberBudgetRepository(),
        hmac_secret="test-secret", anomaly_repository=anomalies, insight_repository=insights,
        now=NOW,
    )
    second = initialize_demo_workspace(
        tenant_ref="tenant_demo_ref", allowed_tenant_ref="tenant_demo_ref",
        workspace_id="ws-demo", allowed_workspace_id="ws-demo", ledger_repository=ledger,
        seed_repository=seeds, budget_repository=InMemoryMemberBudgetRepository(),
        hmac_secret="test-secret", anomaly_repository=anomalies, insight_repository=insights,
        now=NOW.replace(hour=9),
    )

    assert first.batch == second.batch == "operations-v3"
    assert seeds.list_request_refs(tenant_ref="tenant_demo_ref", workspace_id="ws-demo", batch="operations-v1") == ()
    assert set(seeds.list_request_refs(tenant_ref="tenant_demo_ref", workspace_id="ws-demo", batch="operations-v3")) == {event.request_ref for event in second.events}
    assert {event.request_ref for event in legacy.events}.isdisjoint({event.request_ref for event in first.events})
    assert len(anomalies.list("tenant_demo_ref")) == 7
    assert len({item.policy_type for item in anomalies.list("tenant_demo_ref")}) == 6
    preserved = anomalies.get("tenant_demo_ref", "anom-manual-review")
    assert preserved is not None
    assert preserved.status == "acknowledged"
    assert preserved.observed_value == 7
    FinOpsAnomalyService(anomalies).reconcile(
        tenant_ref="tenant_demo_ref",
        findings=evaluate_default_anomalies(
            AnomalyEvaluationInput(
                events=list(second.events),
                trailing_token_median=1120,
            )
        ),
        scope_workspace_ids=("ws-demo",),
    )
    assert len(anomalies.list("tenant_demo_ref")) == 7
    stored = insights.list(tenant_ref="tenant_demo_ref", authorized_workspace_ids=("ws-demo",), limit=10).items
    assert len(stored) == 2
    assert {item.agent_kind for item in stored} == {"finops", "roi"}
    assert all(item.status == "ready" and item.expires_at > item.generated_at for item in stored)
    assert any("未验证" in item.summary for item in stored if item.agent_kind == "roi")
    assert all(item.evidence_refs for item in stored)
