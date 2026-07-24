from __future__ import annotations

from datetime import datetime, timezone

import backend.outcome_store as outcome_store
from backend.finops.analysis_agents import FinOpsAnalysisAgent
from backend.finops.anomalies import DetectedAnomaly
from backend.finops.anomaly_store import (
    FinOpsAnomalyService,
    InMemoryAnomalyRepository,
)
from backend.finops.insight_repository import InMemoryInsightRepository
from backend.finops.insight_service import FinOpsInsightService


def _finding(*, severity: str = "warning") -> DetectedAnomaly:
    return DetectedAnomaly.model_validate(
        {
            "anomaly_id": "anom-budget-1",
            "policy_type": "daily_cost_budget",
            "status": "open",
            "severity": severity,
            "workspace_ids": ["ws-a"],
            "observed_value": 82,
            "threshold_value": 80,
            "sample_count": 30,
            "recommendation": "复核预算使用。",
        }
    )


def test_anomaly_trigger_runs_only_for_new_or_materially_changed_finding() -> None:
    triggered: list[tuple[str, str]] = []
    service = FinOpsAnomalyService(
        InMemoryAnomalyRepository(),
        trigger=lambda event, item: triggered.append((event, item.severity)),
    )

    service.reconcile(tenant_ref="tenant-a", findings=[_finding()])
    service.reconcile(tenant_ref="tenant-a", findings=[_finding()])
    service.reconcile(
        tenant_ref="tenant-a",
        findings=[_finding(severity="critical")],
    )

    assert triggered == [
        ("anomaly_created", "warning"),
        ("anomaly_changed", "critical"),
    ]


def test_insight_service_deduplicates_same_trigger_fingerprint() -> None:
    calls = 0

    def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "structured": {
                "title": "预算使用需要关注",
                "summary": "主分析流程是主要成本驱动。",
                "findings": [
                    {
                        "kind": "cost_driver",
                        "statement": "主分析流程贡献主要估算成本。",
                        "evidence_refs": ["req_aaaaaaaaaaaa"],
                    }
                ],
                "evidence_state": "estimated",
                "confidence": 0.8,
                "draft_suggestions": [],
            }
        }

    repository = InMemoryInsightRepository()
    service = FinOpsInsightService(
        repository=repository,
        runner=FinOpsAnalysisAgent(
            repository=repository,
            model_runner=model,
            now=lambda: datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc),
        ),
    )
    arguments = {
        "agent_kind": "finops",
        "tenant_ref": "tenant-a",
        "workspace_ids": ("ws-a",),
        "window": {
            "from": "2026-07-23T00:00:00Z",
            "to": "2026-07-24T00:00:00Z",
        },
        "trigger_type": "anomaly_changed",
        "trigger_ref": "anom-budget-1",
        "source_revision": "rev-1",
        "input_payload": {
            "status": "ready",
            "evidence_refs": ["req_aaaaaaaaaaaa"],
        },
    }

    first = service.analyze(**arguments)
    duplicate = service.analyze(**arguments)

    assert calls == 1
    assert duplicate.insight_id == first.insight_id


def test_failed_refresh_keeps_previous_ready_insight_as_stale() -> None:
    responses = iter(
        [
            {
                "structured": {
                    "title": "成本变化",
                    "summary": "已有可复核结论。",
                    "findings": [
                        {
                            "kind": "cost_driver",
                            "statement": "成本变化有请求证据。",
                            "evidence_refs": ["req_aaaaaaaaaaaa"],
                        }
                    ],
                    "evidence_state": "estimated",
                    "confidence": 0.8,
                    "draft_suggestions": [],
                }
            },
            {"text": "unstructured", "structured": None},
        ]
    )
    repository = InMemoryInsightRepository()
    service = FinOpsInsightService(
        repository=repository,
        runner=FinOpsAnalysisAgent(
            repository=repository,
            model_runner=lambda *_args, **_kwargs: next(responses),
        ),
    )
    base = {
        "agent_kind": "finops",
        "tenant_ref": "tenant-a",
        "workspace_ids": ("ws-a",),
        "window": {
            "from": "2026-07-23T00:00:00Z",
            "to": "2026-07-24T00:00:00Z",
        },
        "trigger_type": "manual",
        "trigger_ref": "manual-a",
        "input_payload": {
            "status": "ready",
            "evidence_refs": ["req_aaaaaaaaaaaa"],
        },
    }
    service.analyze(**base, source_revision="rev-1")
    failed = service.analyze(**base, source_revision="rev-2")

    assert failed.status == "failed"
    latest = service.latest(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        agent_kind="finops",
    )
    assert latest is not None
    assert latest.status == "stale"
    assert latest.summary == "已有可复核结论。"


def test_verified_outcome_emits_non_blocking_roi_trigger(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(outcome_store, "OUTCOME_DIR", tmp_path / "outcomes")
    monkeypatch.setattr(
        outcome_store,
        "download_blob_json",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        outcome_store,
        "upload_blob_json",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        outcome_store,
        "source_is_valid",
        lambda *_args, **_kwargs: True,
    )
    triggered: list[str] = []
    monkeypatch.setattr(
        outcome_store,
        "_VERIFIED_OUTCOME_TRIGGER",
        lambda item: triggered.append(str(item["event_id"])),
    )
    owner = {
        "name": "Owner",
        "email": "owner@contoso.com",
        "actor_id": "owner-a",
        "tenant_id": "tenant-a",
        "source": "easy_auth",
    }
    reviewer = {
        "name": "Reviewer",
        "email": "reviewer@contoso.com",
        "actor_id": "reviewer-a",
        "tenant_id": "tenant-a",
        "source": "easy_auth",
    }
    event = outcome_store.record_outcome_event(
        "ws-a",
        {
            "metric_name": "conversion_rate",
            "unit": "percent",
            "observed_value": 5.4,
            "observed_at": "2026-07-24T01:00:00Z",
            "provenance": "observed",
            "source": {"run_id": "run-a"},
        },
        owner,
    )

    outcome_store.verify_outcome_event(
        "ws-a",
        event["event_id"],
        reviewer,
    )

    assert triggered == [event["event_id"]]
