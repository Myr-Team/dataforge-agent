from __future__ import annotations

from backend.finops.analysis_agents import FinOpsAnalysisAgent
from backend.finops.insight_repository import InMemoryInsightRepository


def _input(*, status: str = "ready") -> dict[str, object]:
    if status == "insufficient_data":
        return {
            "status": status,
            "agent_kind": "finops",
            "evidence_refs": [],
            "evidence_gaps": ["请求级成本证据不足"],
        }
    return {
        "status": "ready",
        "agent_kind": "finops",
        "overview": {"metrics": {"requests": 30}},
        "evidence_refs": ["req_aaaaaaaaaaaa"],
        "evidence_gaps": [],
    }


def _analyze(
    runner: FinOpsAnalysisAgent,
    payload: dict[str, object],
):
    return runner.analyze(
        agent_kind="finops",
        tenant_ref="tenant-a",
        workspace_ids=("ws-a",),
        window={
            "from": "2026-07-23T00:00:00Z",
            "to": "2026-07-24T00:00:00Z",
        },
        trigger_type="manual",
        trigger_ref="manual-a",
        source_revision="rev-1",
        input_payload=payload,
    )


def test_agent_accepts_evidence_bound_findings_and_typed_draft_suggestions() -> None:
    calls: list[dict[str, object]] = []

    def model(agent_name, input_text, **kwargs):
        calls.append(
            {
                "agent_name": agent_name,
                "input_text": input_text,
                "kwargs": kwargs,
            }
        )
        return {
            "text": "raw model prose must never be shown",
            "structured": {
                "title": "预算使用需要关注",
                "summary": "主分析流程是当前主要成本驱动。",
                "findings": [
                    {
                        "kind": "cost_driver",
                        "statement": "主分析流程贡献主要估算成本。",
                        "evidence_refs": ["req_aaaaaaaaaaaa"],
                    }
                ],
                "evidence_state": "estimated",
                "confidence": 0.8,
                "draft_suggestions": [
                    {
                        "action_type": "cache_policy",
                        "reason": "当前重复分析请求适合评估缓存。",
                        "payload": {
                            "workspace_id": "ws-a",
                            "enabled": True,
                            "ttl_seconds": 300,
                            "base_version": "v1",
                        },
                    }
                ],
            },
        }

    runner = FinOpsAnalysisAgent(
        repository=InMemoryInsightRepository(),
        model_runner=model,
    )
    insight = _analyze(runner, _input())

    assert insight.status == "ready"
    assert insight.findings[0].evidence_refs == ["req_aaaaaaaaaaaa"]
    assert insight.draft_suggestions[0].action_type == "cache_policy"
    assert len(calls) == 1
    assert "raw model prose" not in insight.model_dump_json()


def test_agent_rejects_unknown_evidence_and_non_structured_output() -> None:
    repository = InMemoryInsightRepository()
    outside = FinOpsAnalysisAgent(
        repository=repository,
        model_runner=lambda *_args, **_kwargs: {
            "text": "do something",
            "structured": {
                "title": "不可信结论",
                "summary": "引用了范围外证据。",
                "findings": [
                    {
                        "kind": "risk",
                        "statement": "范围外证据。",
                        "evidence_refs": ["req_outside_scope"],
                    }
                ],
                "evidence_state": "observed",
                "confidence": 0.5,
                "draft_suggestions": [],
            },
        },
    )
    failed = _analyze(outside, _input())
    assert failed.status == "failed"
    assert failed.findings == []
    assert "范围外证据" not in failed.summary

    non_structured = FinOpsAnalysisAgent(
        repository=InMemoryInsightRepository(),
        model_runner=lambda *_args, **_kwargs: {
            "text": "plain text only",
            "structured": None,
        },
    )
    failed_text = _analyze(non_structured, _input())
    assert failed_text.status == "failed"
    assert "plain text only" not in failed_text.summary


def test_insufficient_input_never_calls_model() -> None:
    calls = 0

    def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("model must not be called")

    runner = FinOpsAnalysisAgent(
        repository=InMemoryInsightRepository(),
        model_runner=model,
    )
    insight = _analyze(runner, _input(status="insufficient_data"))

    assert calls == 0
    assert insight.status == "insufficient_data"
    assert insight.evidence_gaps == ["请求级成本证据不足"]


def test_agent_rejects_script_or_xml_material_in_typed_draft() -> None:
    runner = FinOpsAnalysisAgent(
        repository=InMemoryInsightRepository(),
        model_runner=lambda *_args, **_kwargs: {
            "structured": {
                "title": "建议调整缓存",
                "summary": "包含不允许的任意执行内容。",
                "findings": [
                    {
                        "kind": "optimization",
                        "statement": "建议评估缓存。",
                        "evidence_refs": ["req_aaaaaaaaaaaa"],
                    }
                ],
                "evidence_state": "estimated",
                "confidence": 0.6,
                "draft_suggestions": [
                    {
                        "action_type": "cache_policy",
                        "reason": "测试",
                        "payload": {
                            "workspace_id": "ws-a",
                            "enabled": True,
                            "ttl_seconds": 300,
                            "base_version": "<script>unsafe</script>",
                        },
                    }
                ],
            }
        },
    )

    assert _analyze(runner, _input()).status == "failed"
