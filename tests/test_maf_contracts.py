import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.maf_contracts import (
    CollaborationPattern,
    CollaborationPlan,
    MafAgentRecord,
    MafRunSummary,
    MafRuntimeMode,
    canary_selected,
    runtime_mode,
    traffic_percent,
)
from backend.maf_orchestrator import graph_description
import backend.maf_orchestrator as maf_orchestrator


def test_legacy_flag_maps_only_to_audit(monkeypatch):
    monkeypatch.delenv("DF_MAF_RUNTIME", raising=False)
    monkeypatch.setenv("DF_USE_MAF", "1")

    assert runtime_mode() is MafRuntimeMode.AUDIT


def test_explicit_runtime_overrides_legacy_flag(monkeypatch):
    monkeypatch.setenv("DF_MAF_RUNTIME", "full")
    monkeypatch.setenv("DF_USE_MAF", "0")

    assert runtime_mode() is MafRuntimeMode.FULL


@pytest.mark.parametrize("configured", ["", "unsupported"])
def test_present_blank_or_invalid_runtime_disables_legacy_fallback(monkeypatch, configured):
    monkeypatch.setenv("DF_MAF_RUNTIME", configured)
    monkeypatch.setenv("DF_USE_MAF", "1")

    if configured:
        assert runtime_mode() is MafRuntimeMode.OFF
    else:
        assert runtime_mode() is MafRuntimeMode.OFF


@pytest.mark.parametrize("configured", ["off", "audit", "full"])
def test_maf_enabled_uses_runtime_mode(monkeypatch, configured):
    monkeypatch.setattr(maf_orchestrator, "MAF_AVAILABLE", True)
    monkeypatch.setenv("DF_MAF_RUNTIME", configured)
    monkeypatch.setenv("DF_USE_MAF", "1")

    assert maf_orchestrator.maf_enabled() is (configured != "off")


def test_runtime_defaults_to_off(monkeypatch):
    monkeypatch.delenv("DF_MAF_RUNTIME", raising=False)
    monkeypatch.delenv("DF_USE_MAF", raising=False)

    assert runtime_mode() is MafRuntimeMode.OFF


def test_traffic_percent_is_bounded(monkeypatch):
    monkeypatch.setenv("DF_MAF_TRAFFIC_PERCENT", "150")
    assert traffic_percent() == 100

    monkeypatch.setenv("DF_MAF_TRAFFIC_PERCENT", "not-a-number")
    assert traffic_percent() == 0


def test_canary_selection_is_stable(monkeypatch):
    monkeypatch.setenv("DF_MAF_TRAFFIC_PERCENT", "37")

    first = canary_selected("workspace-a", "conversation-a")

    assert first == canary_selected("workspace-a", "conversation-a")


def test_graph_description_is_utf8_clean():
    raw = json.dumps(graph_description(2), ensure_ascii=False)

    assert "审计" in raw
    assert "修订" in raw
    assert "脙" not in raw and "氓庐" not in raw and "?" not in raw


def test_maf_contracts_are_typed_and_serializable():
    plan = CollaborationPlan(
        pattern=CollaborationPattern.BOUNDED_REVIEW,
        agents=[MafAgentRecord(agent_id="df-auditor", role="审计")],
        max_revisions=2,
    )
    summary = MafRunSummary(
        run_id="run-1",
        runtime_mode=MafRuntimeMode.AUDIT,
        collaboration=plan,
    )

    assert summary.model_dump()["collaboration"]["pattern"] == "bounded_review"


def test_collaboration_plan_rejects_more_than_two_revisions():
    with pytest.raises(ValidationError):
        CollaborationPlan(pattern=CollaborationPattern.DIRECT, max_revisions=3)


def test_default_max_revisions_has_hard_limit(monkeypatch):
    monkeypatch.setenv("DF_MAF_MAX_REVISIONS", "99")

    assert maf_orchestrator.default_max_revisions() == 2


@pytest.mark.asyncio
async def test_direct_max_revisions_argument_has_hard_limit(monkeypatch):
    captured = {}

    class FakeWorkflow:
        async def run(self, state):
            captured["max_revisions"] = state.max_revisions
            return SimpleNamespace(get_outputs=lambda: [state])

    monkeypatch.setattr(maf_orchestrator, "build_feasibility_workflow", lambda *args: FakeWorkflow())

    await maf_orchestrator.run_feasibility_audit_loop(
        req={},
        artifact={},
        run_analyst=lambda *args: {},
        audit_fn=lambda *args: (None, {}),
        max_revisions=99,
    )

    assert captured["max_revisions"] == 2


def test_invalid_runtime_mode_is_off(monkeypatch):
    monkeypatch.setenv("DF_MAF_RUNTIME", "unsupported")

    assert runtime_mode() is MafRuntimeMode.OFF
