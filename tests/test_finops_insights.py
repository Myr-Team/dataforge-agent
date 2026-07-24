from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.finops.insight_repository import InMemoryInsightRepository
from backend.finops.insights import FinOpsInsight, insight_fingerprint


def _insight(**updates) -> FinOpsInsight:
    now = datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc)
    payload = {
        "insight_id": "ins_aaaaaaaaaaaa",
        "agent_kind": "finops",
        "tenant_ref": "tenant-a",
        "workspace_ids": ["ws-a"],
        "window": {
            "from": "2026-07-23T00:00:00Z",
            "to": "2026-07-24T00:00:00Z",
        },
        "trigger_type": "anomaly_changed",
        "trigger_ref": "anom-budget-1",
        "trigger_fingerprint": insight_fingerprint(
            tenant_ref="tenant-a",
            workspace_ids=["ws-a"],
            agent_kind="finops",
            trigger_type="anomaly_changed",
            trigger_ref="anom-budget-1",
            source_revision="rev-1",
        ),
        "title": "预算使用上升",
        "summary": "成本上升主要来自主分析流程。",
        "findings": [
            {
                "kind": "cost_driver",
                "statement": "主分析流程贡献本时段主要估算成本。",
                "evidence_refs": ["req_aaaaaaaaaaaa"],
            }
        ],
        "evidence_refs": ["req_aaaaaaaaaaaa"],
        "evidence_state": "estimated",
        "confidence": 0.82,
        "source_revisions": {"price_card": "price-2026-07"},
        "evidence_gaps": [],
        "draft_suggestions": [],
        "generated_at": now,
        "expires_at": now + timedelta(hours=6),
        "status": "ready",
    }
    payload.update(updates)
    return FinOpsInsight.model_validate(payload)


def test_ready_insight_requires_scoped_evidence_for_every_finding() -> None:
    with pytest.raises(ValidationError):
        _insight(findings=[{"kind": "risk", "statement": "风险上升", "evidence_refs": []}])
    with pytest.raises(ValidationError):
        _insight(workspace_ids=[])
    with pytest.raises(ValidationError):
        _insight(
            findings=[
                {
                    "kind": "risk",
                    "statement": "风险上升",
                    "evidence_refs": ["req_outside"],
                }
            ]
        )


def test_insufficient_data_requires_explicit_evidence_gaps() -> None:
    with pytest.raises(ValidationError):
        _insight(
            status="insufficient_data",
            findings=[],
            evidence_refs=[],
            evidence_gaps=[],
            confidence=None,
        )

    insight = _insight(
        status="insufficient_data",
        title="证据不足",
        summary="当前无法形成可复核结论。",
        findings=[],
        evidence_refs=[],
        evidence_gaps=["已验证结果事件不足"],
        confidence=None,
    )
    assert insight.status == "insufficient_data"


def test_repository_is_tenant_scoped_deduplicated_and_workspace_bounded() -> None:
    repository = InMemoryInsightRepository()
    first = repository.save(_insight())
    duplicate = repository.save(_insight(title="不得覆盖已保存结果"))
    repository.save(
        _insight(
            insight_id="ins_bbbbbbbbbbbb",
            tenant_ref="tenant-b",
            trigger_fingerprint="b" * 64,
        )
    )

    assert duplicate.title == first.title
    page = repository.list(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-a",),
        agent_kind="finops",
        limit=20,
    )
    assert [item.insight_id for item in page.items] == ["ins_aaaaaaaaaaaa"]
    assert repository.list(
        tenant_ref="tenant-a",
        authorized_workspace_ids=("ws-other",),
        agent_kind="finops",
        limit=20,
    ).items == []


def test_repository_rejects_cross_tenant_insight_id_collision() -> None:
    repository = InMemoryInsightRepository()
    repository.save(_insight())
    with pytest.raises(ValueError, match="tenant"):
        repository.save(
            _insight(
                tenant_ref="tenant-b",
                trigger_fingerprint="b" * 64,
            )
        )
