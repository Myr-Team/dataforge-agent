from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.finops.assistant import (
    AssistantRequest,
    FinOpsAssistantService,
)


def _request(**metric_updates: object) -> AssistantRequest:
    metric = {
        "metric_id": "cache_hit_rate",
        "label": "缓存命中率",
        "value": 62.5,
        "unit": "%",
        "dimension": "model",
        "dimension_value": "gpt-5",
        "window": {
            "from": "2026-07-01T00:00:00Z",
            "to": "2026-07-26T00:00:00Z",
        },
        "filters": {
            "workspace_id": "ws-a",
            "model": "gpt-5",
        },
        "data_status": "partial",
        "evidence_state": "observed",
        "cache_state": "hit",
    }
    metric.update(metric_updates)
    return AssistantRequest.model_validate(
        {
            "question": "为什么缓存命中率下降？",
            "metric_context": metric,
            "history": [
                {"role": "user", "content": "先解释当前指标。"},
                {"role": "assistant", "content": "我会基于可用证据说明。"},
            ],
        }
    )


def test_assistant_request_rejects_unknown_or_unsafe_context_fields() -> None:
    with pytest.raises(ValidationError):
        _request(secret="must-not-pass")

    with pytest.raises(ValidationError):
        _request(filters={"workspace_id": "ws-a", "resource_id": "/subscriptions/secret"})


def test_assistant_request_defaults_to_quick_and_bounds_model_output() -> None:
    calls: list[int] = []

    def runner(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append(int(kwargs["max_output_tokens"]))
        return {
            "structured": {
                "conclusion": "当前指标可复核。[req_safe]",
                "basis": "依据来自授权证据。[req_safe]",
                "impact": "影响范围有限。",
                "recommendation": "继续观察。",
                "caveat": "仅限当前窗口。",
                "evidence_refs": ["req_safe"],
            }
        }

    service = FinOpsAssistantService(model_runner=runner)
    quick = _request()
    deep = quick.model_copy(update={"mode": "deep"})

    assert quick.mode == "quick"
    service.answer(request=quick, evidence_payload={"evidence_refs": ["req_safe"]})
    service.answer(request=deep, evidence_payload={"evidence_refs": ["req_safe"]})

    assert calls == [650, 1200]


def test_quick_assistant_uses_bounded_model_attempt_and_grounded_fallback() -> None:
    captured: dict[str, object] = {}

    def runner(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        raise TimeoutError("provider timeout")

    result = FinOpsAssistantService(model_runner=runner).answer(
        request=_request(
            label="Gateway coverage",
            value=93.2,
            unit="%",
            policy_type="apim_coverage",
        ),
        evidence_payload={
            "evidence_refs": ["req_safe"],
            "evidence_catalog": [
                {"ref": "req_safe", "display_name": "Demo analysis run"}
            ],
        },
    )

    assert captured["request_timeout_seconds"] == 6.0
    assert captured["retry_limit"] == 0
    assert result.status == "ready"
    assert result.evidence_refs == ["req_safe"]
    assert result.evidence_labels == ["Demo analysis run"]
    assert result.sections is not None
    assert "Gateway coverage" in result.sections.conclusion
    assert "Demo analysis run" in result.sections.basis
    assert result.evidence_state == "observed"


def test_deep_assistant_keeps_fail_closed_behavior_when_model_is_unavailable() -> None:
    result = FinOpsAssistantService(
        model_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("provider timeout")
        )
    ).answer(
        request=_request().model_copy(update={"mode": "deep"}),
        evidence_payload={"evidence_refs": ["req_safe"]},
    )

    assert result.status == "unavailable"
    assert result.evidence_refs == []


def test_assistant_request_accepts_only_bounded_policy_request_evidence() -> None:
    request = _request(
        policy_type="p95_latency",
        evidence_refs=["req_latency_authorized", "req_latency_authorized"],
    )

    assert request.metric_context.policy_type == "p95_latency"
    assert request.metric_context.evidence_refs == ["req_latency_authorized"]

    with pytest.raises(ValidationError):
        _request(policy_type="arbitrary_policy")

    with pytest.raises(ValidationError):
        _request(evidence_refs=["provider-response-id"])

    with pytest.raises(ValidationError):
        _request(evidence_refs=["req_safe:provider"])

    with pytest.raises(ValidationError):
        _request(evidence_refs=["req_one1", "req_two2", "req_three3", "req_four4"])


def test_assistant_returns_only_allowlisted_evidence() -> None:
    def runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "structured": {
                "conclusion": "缓存命中率下降主要来自当前模型范围内的未命中请求。[req_safe]",
                "basis": "所选范围内存在可复核的未命中调用。[req_safe]",
                "impact": "重复分析会增加等待时间与估算成本。",
                "recommendation": "先复核相同工作区的缓存键与有效期。",
                "caveat": "当前判断仅覆盖所选时间范围。",
                "evidence_refs": ["req_safe"],
                "suggested_questions": ["哪些工作区贡献最大？"],
            }
        }

    result = FinOpsAssistantService(model_runner=runner).answer(
        request=_request(),
        evidence_payload={
            "overview": {"cache_hit_rate_pct": 62.5},
            "evidence_refs": ["req_safe"],
            "evidence_catalog": [
                {
                    "ref": "req_safe",
                    "display_name": "工作区 A · 模型调用 · 7月26日 10:00",
                }
            ],
        },
    )

    assert result.status == "ready"
    assert result.evidence_refs == ["req_safe"]
    assert "req_safe" not in result.answer
    assert result.sections is not None
    assert result.sections.conclusion.startswith("缓存命中率下降")
    assert result.sections.basis.startswith("所选范围内")
    assert result.sections.impact == "重复分析会增加等待时间与估算成本。"
    assert result.sections.recommendation == "先复核相同工作区的缓存键与有效期。"
    assert result.sections.caveat == "当前判断仅覆盖所选时间范围。"
    assert result.evidence_labels == ["工作区 A · 模型调用 · 7月26日 10:00"]
    assert result.evidence_state == "observed"
    assert result.suggested_questions == ["哪些工作区贡献最大？"]


def test_assistant_cost_question_passes_explanatory_knowledge_without_new_evidence() -> None:
    captured: dict[str, object] = {}

    def runner(_agent: str, payload: str, **_kwargs: object) -> dict[str, object]:
        captured.update(json.loads(payload))
        return {
            "structured": {
                "conclusion": "估算成本主要由高用量请求贡献。[req_safe]",
                "basis": "当前授权范围内存在一条高成本请求。[req_safe]",
                "impact": "成本增长会提高预算消耗速度。",
                "recommendation": "先复核模型和部门归因，再评估缓存优化。",
                "caveat": "估算成本不等于云平台实际账单。",
                "evidence_refs": ["req_safe"],
                "suggested_questions": ["价目覆盖率如何影响可信度？"],
            }
        }

    result = FinOpsAssistantService(model_runner=runner).answer(
        request=_request(
            metric_id="estimated_cost",
            label="估算成本",
            value=468.42,
            unit="USD",
            evidence_state="estimated",
        ),
        evidence_payload={
            "overview": {"estimated_cost": {"amount": 468.42}},
            "evidence_refs": ["req_safe"],
            "evidence_catalog": [
                {"ref": "req_safe", "display_name": "模型成本运行证据"}
            ],
        },
    )

    assert result.status == "ready"
    knowledge = captured["knowledge_context"]
    assert isinstance(knowledge, dict)
    assert knowledge["usage_boundary"].startswith("知识仅用于解释")
    assert {item["id"] for item in knowledge["entries"]} >= {"estimated-cost"}
    assert captured["evidence_refs"] == ["req_safe"]


def test_assistant_normalizes_legacy_answer_into_semantic_sections() -> None:
    def runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "structured": {
                "answer": "当前成本变化由一条高用量调用贡献。[req_safe]",
                "evidence_refs": ["req_safe"],
                "suggested_questions": [],
            }
        }

    result = FinOpsAssistantService(model_runner=runner).answer(
        request=_request(),
        evidence_payload={"evidence_refs": ["req_safe"]},
    )

    assert result.status == "ready"
    assert result.sections is not None
    assert result.sections.conclusion.startswith("当前成本变化")
    assert result.sections.basis
    assert result.sections.recommendation
    assert result.sections.caveat


def test_assistant_fails_closed_when_model_cites_foreign_evidence() -> None:
    def runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "structured": {
                "answer": "无法验证的结论。",
                "evidence_refs": ["req_foreign"],
                "suggested_questions": [],
            }
        }

    result = FinOpsAssistantService(model_runner=runner).answer(
        request=_request(),
        evidence_payload={"evidence_refs": ["req_safe"]},
    )

    assert result.status == "unavailable"
    assert result.evidence_refs == []
    assert result.answer == "当前分析暂不可用，运营数据本身不受影响。"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("answer", "旧格式正文提到 req_other_workspace。"),
        ("conclusion", "结论提到 req_other_workspace。"),
        ("basis", "依据提到 req_other_workspace。"),
        ("impact", "影响提到 req_other_workspace。"),
        ("recommendation", "建议提到 req_other_workspace。"),
        ("caveat", "边界提到 req_other_workspace。"),
        ("suggested_questions", ["继续查看 req_other_workspace 吗？"]),
    ],
)
def test_assistant_rejects_foreign_request_refs_in_any_model_prose(
    field: str,
    value: object,
) -> None:
    structured: dict[str, object] = {
        "conclusion": "当前结论仅引用已授权证据 req_safe。",
        "basis": "依据来自 req_safe。",
        "impact": "影响仍需复核。",
        "recommendation": "先查看 req_safe。",
        "caveat": "仅适用于当前范围。",
        "evidence_refs": ["req_safe"],
        "suggested_questions": [],
    }
    if field == "answer":
        structured.pop("conclusion")
    structured[field] = value

    result = FinOpsAssistantService(
        model_runner=lambda *_args, **_kwargs: {"structured": structured}
    ).answer(
        request=_request(),
        evidence_payload={
            "evidence_refs": ["req_safe"],
            "evidence_catalog": [
                {"ref": "req_safe", "display_name": "缓存运行证据"}
            ],
        },
    )

    assert result.status == "unavailable"
    assert result.evidence_refs == []
    assert "req_other_workspace" not in result.model_dump_json()


def test_assistant_replaces_allowed_request_refs_in_sections_and_suggestions() -> None:
    result = FinOpsAssistantService(
        model_runner=lambda *_args, **_kwargs: {
            "structured": {
                "conclusion": "req_safe 显示缓存命中率下降。",
                "basis": "依据是 req_safe。",
                "impact": "req_safe 对等待时间有影响。",
                "recommendation": "先复核 req_safe。",
                "caveat": "仅覆盖 req_safe 所在窗口。",
                "evidence_refs": ["req_safe"],
                "suggested_questions": ["req_safe 与上一周期相比如何？"],
            }
        }
    ).answer(
        request=_request(),
        evidence_payload={
            "evidence_refs": ["req_safe"],
            "evidence_catalog": [
                {"ref": "req_safe", "display_name": "缓存运行证据"}
            ],
        },
    )

    assert result.status == "ready"
    assert result.sections is not None
    assert "req_safe" not in result.sections.model_dump_json()
    assert all("req_safe" not in item for item in result.suggested_questions)
    assert "缓存运行证据" in result.sections.model_dump_json()
    assert "缓存运行证据" in result.suggested_questions[0]


def test_assistant_replaces_overlapping_and_bracketed_allowed_refs_exactly() -> None:
    result = FinOpsAssistantService(
        model_runner=lambda *_args, **_kwargs: {
            "structured": {
                "conclusion": "[ req_safe ] 与 req_safe_extra 分别代表两条证据。",
                "basis": "先看 req_safe_extra，再看 [req_safe]。",
                "impact": "两条证据需要分别复核。",
                "recommendation": "比较 [req_safe] 和 [ req_safe_extra ]。",
                "caveat": "仅限当前证据集。",
                "evidence_refs": ["req_safe", "req_safe_extra"],
                "suggested_questions": ["[req_safe_extra] 与 req_safe 有何差异？"],
            }
        }
    ).answer(
        request=_request(),
        evidence_payload={
            "evidence_refs": ["req_safe", "req_safe_extra"],
            "evidence_catalog": [
                {"ref": "req_safe", "display_name": "缓存证据一"},
                {"ref": "req_safe_extra", "display_name": "缓存证据二"},
            ],
        },
    )

    assert result.status == "ready"
    assert result.sections is not None
    assert result.sections.conclusion == "缓存证据一 与 缓存证据二 分别代表两条证据。"
    assert result.sections.basis == "先看 缓存证据二，再看 缓存证据一。"
    assert result.sections.recommendation == "比较 缓存证据一 和 缓存证据二。"
    assert result.suggested_questions == ["缓存证据二 与 缓存证据一 有何差异？"]
    assert "req_safe" not in result.sections.model_dump_json()


def test_assistant_does_not_invent_an_answer_without_evidence() -> None:
    calls = 0

    def runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise AssertionError("model must not run without evidence")

    result = FinOpsAssistantService(model_runner=runner).answer(
        request=_request(evidence_state="unavailable", cache_state="unavailable"),
        evidence_payload={"evidence_refs": []},
    )

    assert calls == 0
    assert result.status == "insufficient_data"
    assert result.answer == "当前指标缺少可复核证据，暂不能生成分析结论。"
