from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.finops.demo_initialize import (
    initialize_demo_workspace,
    persist_demo_run_evidence,
)
from backend.finops.demo_seed_repository import InMemoryDemoSeedRepository
from backend.finops.member_budget_repository import InMemoryMemberBudgetRepository
from backend.finops.repository import InMemoryFinOpsRepository


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


def test_initializer_is_bounded_to_one_opaque_tenant_and_workspace() -> None:
    result = initialize_demo_workspace(
        tenant_ref="tenant_demo_ref",
        workspace_id="ws-demo",
        allowed_workspace_id="ws-demo",
        ledger_repository=InMemoryFinOpsRepository(),
        seed_repository=InMemoryDemoSeedRepository(),
        budget_repository=InMemoryMemberBudgetRepository(),
        now=NOW,
    )
    assert result.event_count >= 140

    with pytest.raises(PermissionError, match="allowlisted"):
        initialize_demo_workspace(
            tenant_ref="tenant_demo_ref",
            workspace_id="ws-other",
            allowed_workspace_id="ws-demo",
            ledger_repository=InMemoryFinOpsRepository(),
            seed_repository=InMemoryDemoSeedRepository(),
            budget_repository=InMemoryMemberBudgetRepository(),
            now=NOW,
        )


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
