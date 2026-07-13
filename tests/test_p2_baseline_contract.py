from __future__ import annotations

import json

import pytest

from eval import run_p2_baseline
from eval.run_p2_baseline import (
    build_report,
    fetch_runs,
    load_runs,
    main,
    parse_args,
    reference_cases,
)


REQUIRED_CASE_SHAPES = {
    "site_channel_selection",
    "growth_retention",
    "pricing_productization",
    "operations",
    "campaign_service",
    "risk_data_readiness",
}
REQUIRED_CASE_FIELDS = {
    "id",
    "shape",
    "goal",
    "schema_roles",
    "evidence_strength",
    "expected_required_agents",
    "known_unrelated_source_topics",
}


def test_reference_cases_cover_required_domain_neutral_shapes() -> None:
    cases = reference_cases()

    assert {case["shape"] for case in cases} == REQUIRED_CASE_SHAPES
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert set(case) == REQUIRED_CASE_FIELDS
        assert isinstance(case["id"], str)
        assert isinstance(case["shape"], str)
        assert isinstance(case["goal"], str)
        assert isinstance(case["evidence_strength"], str)
        assert all(isinstance(value, str) for value in case["schema_roles"])
        assert all(
            isinstance(value, str) for value in case["expected_required_agents"]
        )
        assert all(
            isinstance(value, str) for value in case["known_unrelated_source_topics"]
        )
        assert case["goal"]
        assert case["schema_roles"]
        assert case["evidence_strength"]
        assert case["expected_required_agents"]
        assert case["known_unrelated_source_topics"]


def test_baseline_report_separates_observed_and_fixture_metrics() -> None:
    report = build_report(reference_cases(), observed_runs=[])

    assert report["evidence_kind"] == "fixture"
    assert report["production_claim_allowed"] is False
    assert report["sample_count"] == 6
    assert report["metrics"]["market_relevance"] is None


def test_observed_report_requires_build_and_run_lineage() -> None:
    with pytest.raises(ValueError, match="build_id"):
        build_report(reference_cases(), observed_runs=[{"run_id": "r1"}])


def test_observed_report_only_aggregates_reported_measurements() -> None:
    report = build_report(
        reference_cases(),
        observed_runs=[
            {
                "build_id": "build-123",
                "run_id": "run-1",
                "metrics": {"latency_ms": 42, "market_relevance": 0.8},
            }
        ],
    )

    assert report["evidence_kind"] == "observed"
    assert report["production_claim_allowed"] is True
    assert report["sample_count"] == 1
    assert report["metrics"]["latency_ms"] == 42.0
    assert report["metrics"]["market_relevance"] == 0.8
    assert report["metrics"]["tokens"] is None


def test_saved_observed_run_json_accepts_a_utf8_bom(tmp_path) -> None:
    capture = tmp_path / "observed-runs.json"
    capture.write_text(
        '[{"build_id": "build-123", "run_id": "run-1"}]',
        encoding="utf-8-sig",
    )

    assert load_runs(capture) == [{"build_id": "build-123", "run_id": "run-1"}]


def test_reference_cases_reject_duplicate_ids(tmp_path) -> None:
    cases = reference_cases()
    cases[1]["id"] = cases[0]["id"]
    fixture = tmp_path / "invalid-reference-cases.json"
    fixture.write_text(json.dumps(cases), encoding="utf-8")

    with pytest.raises(ValueError, match="unique"):
        reference_cases(fixture)


def test_reference_cases_reject_wrong_field_types(tmp_path) -> None:
    cases = reference_cases()
    cases[2]["schema_roles"] = "not-a-list"
    fixture = tmp_path / "invalid-reference-cases.json"
    fixture.write_text(json.dumps(cases), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_roles"):
        reference_cases(fixture)


@pytest.mark.parametrize(
    "observed_runs",
    [
        [{"build_id": "build-1", "run_id": "run-1", "metrics": []}],
        [
            {
                "build_id": "build-1",
                "run_id": "run-1",
                "metrics": {"latency_ms": "fast"},
            }
        ],
        [
            {
                "build_id": "build-1",
                "run_id": "run-1",
                "metrics": {"unrecognized_metric": "not-numeric"},
            }
        ],
    ],
)
def test_observed_report_rejects_malformed_metric_values(observed_runs) -> None:
    with pytest.raises(ValueError, match="metrics"):
        build_report(reference_cases(), observed_runs=observed_runs)


def test_saved_run_list_rejects_non_object_entries(tmp_path) -> None:
    capture = tmp_path / "invalid-observed-runs.json"
    capture.write_text('["not-a-run"]', encoding="utf-8")

    with pytest.raises(ValueError, match="run entries"):
        load_runs(capture)


def test_fetch_runs_uses_authorization_header_and_authoritative_build_id(
    monkeypatch,
) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'\xef\xbb\xbf[{"run_id": "api-run", "metrics": {"latency_ms": 51}}]'

    def fake_urlopen(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(run_p2_baseline, "urlopen", fake_urlopen)

    runs = fetch_runs(
        "https://example.invalid/p2-runs",
        "api-token",
        build_id="build-authoritative",
    )

    assert captured == {"authorization": "Bearer api-token", "timeout": 30}
    assert runs == [
        {
            "build_id": "build-authoritative",
            "run_id": "api-run",
            "metrics": {"latency_ms": 51},
        }
    ]


def test_api_mode_requires_build_id_and_rejects_direct_bearer_token(tmp_path, capsys) -> None:
    output = tmp_path / "p2-baseline.json"

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--output",
                str(output),
                "--api-url",
                "https://example.invalid",
                "--bearer-token-env",
                "P2_TEST_TOKEN",
            ]
        )
    assert "--build-id" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--output",
                str(output),
                "--api-url",
                "https://example.invalid",
                "--build-id",
                "build-1",
                "--bearer-token",
                "not-accepted",
            ]
        )


def test_api_mode_reads_named_environment_token_without_printing_or_persisting_it(
    tmp_path, monkeypatch, capsys
) -> None:
    output = tmp_path / "p2-api-baseline.json"
    secret = "not-for-output"
    monkeypatch.setenv("P2_TEST_TOKEN", secret)

    def fake_fetch_runs(api_url, bearer_token, *, build_id):
        assert api_url == "https://example.invalid/p2-runs"
        assert bearer_token == secret
        assert build_id == "build-from-cli"
        return [{"build_id": build_id, "run_id": "api-run"}]

    monkeypatch.setattr(run_p2_baseline, "fetch_runs", fake_fetch_runs)

    assert (
        main(
            [
                "--output",
                str(output),
                "--api-url",
                "https://example.invalid/p2-runs",
                "--build-id",
                "build-from-cli",
                "--bearer-token-env",
                "P2_TEST_TOKEN",
            ]
        )
        == 0
    )

    assert secret not in capsys.readouterr().out
    assert secret not in output.read_text(encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["lineage"] == [
        {"build_id": "build-from-cli", "run_id": "api-run"}
    ]
