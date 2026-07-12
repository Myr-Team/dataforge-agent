from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_framework import SupportsAgentRun
from agent_framework_orchestrations import SequentialBuilder

from eval.run_maf_runtime_eval import (
    REQUIRED_METRICS,
    _DeterministicRegistry,
    empty_report_schema,
    run_deterministic_evaluation,
)


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval" / "maf_runtime_cases.json"
EXPECTED_PATTERNS = {
    "direct",
    "concurrent_research",
    "specialist_handoff",
    "bounded_review",
}
FORBIDDEN_ROUTING_FIELDS = {
    "business",
    "business_name",
    "dataset",
    "dataset_name",
    "demo",
    "demo_name",
    "industry",
    "industry_name",
}


def _cases() -> list[dict[str, object]]:
    return json.loads(CASES.read_text(encoding="utf-8"))


def test_eval_cases_cover_all_collaboration_patterns_and_edge_conditions() -> None:
    cases = _cases()

    assert {case["expected_pattern"] for case in cases} == EXPECTED_PATTERNS
    conditions = {condition for case in cases for condition in case["conditions"]}
    assert {
        "weak_evidence",
        "missing_optional_market",
        "ambiguous_followup",
        "high_impact_conclusion",
        "forced_runtime_failure",
    } <= conditions


def test_eval_cases_route_only_on_normalized_semantic_fields() -> None:
    cases = _cases()
    serialized = json.dumps(cases, sort_keys=True).lower()

    for case in cases:
        request = case["request"]
        assert set(request) == {
            "intent",
            "output_mode",
            "needs_workspace",
            "needs_external",
            "high_impact",
            "payload",
        }
        assert not (set(request) & FORBIDDEN_ROUTING_FIELDS)
        assert not (set(request["payload"]) & FORBIDDEN_ROUTING_FIELDS)
    for trigger in ("business_name", "dataset_name", "industry_name", "demo_name"):
        assert trigger not in serialized


def test_empty_report_marks_every_metric_unknown_instead_of_defaulting_to_success() -> None:
    report = empty_report_schema()

    assert report["measurement_scope"] == "deterministic_harness"
    assert report["production_quality_claim"] is False
    assert set(report["metrics"]) == REQUIRED_METRICS
    for metric in report["metrics"].values():
        assert metric["value"] is None
        assert metric["status"] == "unknown"
        assert metric["sample_size"] == 0
        assert metric["measurement_scope"] == "deterministic_harness"
        assert metric["production_quality_claim"] is False


def test_deterministic_fake_satisfies_stable_agent_builder_protocol() -> None:
    registry = _DeterministicRegistry(_cases()[0])
    agent = registry.agent("df-coordinator")

    assert isinstance(agent, SupportsAgentRun)
    assert SequentialBuilder(participants=[agent]).build() is not None


def test_deterministic_eval_measures_metrics_and_preserves_unknown_tokens() -> None:
    report = asyncio.run(run_deterministic_evaluation(CASES))
    cases = _cases()

    assert report["mode"] == "deterministic"
    assert report["measurement_scope"] == "deterministic_harness"
    assert report["production_quality_claim"] is False
    assert set(report["metrics"]) == REQUIRED_METRICS
    assert report["metrics"]["selection_accuracy"]["status"] == "measured"
    assert report["metrics"]["selection_accuracy"]["value"] == 1.0
    assert report["metrics"]["tokens"]["value"] is None
    assert report["metrics"]["tokens"]["status"] == "unknown"
    assert report["metrics"]["fallback_rate"]["value"] > 0
    assert report["metrics"]["groundedness"]["sample_size"] >= len(cases) - 1
    assert {case["actual_pattern"] for case in report["cases"]} == EXPECTED_PATTERNS
    assert {case["case_id"] for case in report["cases"]} == {
        case["id"] for case in cases
    }
    for metric in report["metrics"].values():
        assert metric["measurement_scope"] == "deterministic_harness"
        assert metric["production_quality_claim"] is False
    for name in ("groundedness", "unsupported_claim_rate"):
        assert (
            report["metrics"][name]["interpretation"]
            == "fixture_reference_propagation_contract_check"
        )
    assert "not production answer quality" in report["disclaimer"]


def test_forced_runtime_failure_falls_back_exactly_once() -> None:
    report = asyncio.run(run_deterministic_evaluation(CASES))
    forced = next(
        case
        for case in report["cases"]
        if "forced_runtime_failure" in case["conditions"]
    )

    assert forced["fallback"] is True
    assert forced["fallback_attempts"] == 1
    assert forced["runtime_error_category"] == "forced_runtime_failure"


def test_docs_document_stable_runtime_and_evaluation_scope() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    report = (ROOT / ".superpowers" / "sdd" / "task-6-report.md").read_text(
        encoding="utf-8"
    )

    for content in (english, chinese):
        assert "agent-framework-core==1.11.0" in content
        assert "agent-framework-foundry==1.10.1" in content
        assert "agent-framework-orchestrations==1.0.0" in content
        assert all(f"`{mode}`" in content for mode in ("off", "audit", "full"))
        assert "DF_MAF_TRAFFIC_PERCENT" in content
        assert "Magentic" in content
        assert "Hosted Agents" in content
        assert "exactly once" in content

    for content in (english, chinese, report):
        assert "measurement_scope='deterministic_harness'" in content
        assert "production_quality_claim=false" in content
        assert "fixture/reference-propagation contract checks" in content
        assert "not production answer quality" in content
