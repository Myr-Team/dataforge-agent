from __future__ import annotations

import json

from backend.finops.assistant_knowledge import retrieve_finops_knowledge


def test_internal_knowledge_retrieval_returns_distinct_bounded_sections() -> None:
    cost = retrieve_finops_knowledge(
        metric_id="estimated_cost",
        policy_type="unpriced_requests",
        question="为什么有未计价请求，模型价格怎么解释？",
    )
    roi = retrieve_finops_knowledge(
        metric_id="roi_forecast_validation",
        policy_type=None,
        question="MSE 和回归能不能证明 ROI？",
    )

    assert cost[0]["document_id"] == "cost-pricing-handbook"
    assert roi[0]["document_id"] == "roi-regression-guide"
    assert cost[0]["citation"].startswith("内部知识：")
    assert "MSE" in roi[0]["excerpt"]
    assert len(cost) <= 4
    assert len(roi) <= 4
    assert len(json.dumps(cost, ensure_ascii=False)) < 8000
    assert "C:\\" not in json.dumps([cost, roi], ensure_ascii=False)


def test_internal_knowledge_cache_question_prefers_cache_playbook() -> None:
    result = retrieve_finops_knowledge(
        metric_id="cache_hit_rate",
        policy_type="cache_hit_rate",
        question="缓存命中率低怎么判断，能节省多少 Token？",
    )

    assert result[0]["document_id"] == "cache-performance-playbook"
    assert "缓存" in result[0]["section"]
