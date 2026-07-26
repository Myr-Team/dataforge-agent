from __future__ import annotations

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


def test_assistant_returns_only_allowlisted_evidence() -> None:
    def runner(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "structured": {
                "answer": "缓存命中率下降主要来自当前模型范围内的未命中请求。",
                "evidence_refs": ["req_safe"],
                "suggested_questions": ["哪些工作区贡献最大？"],
            }
        }

    result = FinOpsAssistantService(model_runner=runner).answer(
        request=_request(),
        evidence_payload={
            "overview": {"cache_hit_rate_pct": 62.5},
            "evidence_refs": ["req_safe"],
        },
    )

    assert result.status == "ready"
    assert result.evidence_refs == ["req_safe"]
    assert result.evidence_state == "observed"
    assert result.suggested_questions == ["哪些工作区贡献最大？"]


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
