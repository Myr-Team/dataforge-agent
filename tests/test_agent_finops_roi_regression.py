from __future__ import annotations

import copy
import inspect
import json
import socket
from pathlib import Path

import pytest

from eval import run_agent_finops_roi_regression as runner


CASES_PATH = Path(runner.__file__).with_name("agent_finops_roi_cases.json")


def _dataset() -> dict:
    return runner.load_dataset(CASES_PATH)


def _refresh_digest(dataset: dict) -> None:
    digest = runner.compute_dataset_digest(dataset)
    dataset["dataset"]["digest"] = digest
    dataset["baseline"]["dataset_digest"] = digest
    dataset["candidate"]["dataset_digest"] = digest


def _write_dataset(path: Path, dataset: dict) -> None:
    path.write_text(
        json.dumps(dataset, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _evaluate(dataset: dict) -> dict:
    return runner.evaluate_dataset(
        dataset, baseline_version="baseline-v1", candidate_version="candidate-v1"
    )


def test_fixture_is_synthetic_sanitized_and_has_a_valid_digest() -> None:
    dataset = _dataset()

    assert dataset["measurement_scope"] == "sanitized_fixture"
    assert dataset["dataset"]["digest"] == runner.compute_dataset_digest(dataset)
    assert dataset["baseline"]["dataset_digest"] == dataset["dataset"]["digest"]
    assert dataset["candidate"]["dataset_digest"] == dataset["dataset"]["digest"]
    assert len({case["case_id"] for case in dataset["cases"]}) == len(dataset["cases"])
    serialized = json.dumps(dataset).lower()
    assert "authorization" not in serialized
    assert "secret" not in serialized
    assert "system prompt" not in serialized


def test_report_is_stable_local_and_truthful() -> None:
    dataset = _dataset()

    first = _evaluate(copy.deepcopy(dataset))
    second = _evaluate(copy.deepcopy(dataset))

    assert first == second
    assert first["result"] == "pass"
    assert first["mode"] == "deterministic_local"
    assert first["measurement_scope"] == "sanitized_fixture"
    assert first["production_quality_claim"] is False
    assert first["network_access"] is False
    assert first["invalid_count"] == 0
    assert first["not_applicable_count"] == 1
    assert first["report_digest"].startswith("sha256:")
    assert len(first["report_digest"]) == 71

    scenario = next(
        result
        for result in first["metrics"]["unit_economics"]["cases"]
        if result["case_id"] == "unit-economics-scenario-boundary-001"
    )
    for side in ("baseline", "candidate"):
        assert scenario[side]["verified_status"] == "unavailable"
        assert scenario[side]["verified_roi"] is None
        assert scenario[side]["net_verified_value"] is None


def test_sanitized_runner_fixture_never_emits_verified_roi() -> None:
    report = _evaluate(_dataset())

    for case in report["metrics"]["unit_economics"]["cases"]:
        for side in ("baseline", "candidate"):
            assert case[side]["verified_status"] != "verified"
            assert case[side]["verified_roi"] is None
            assert case[side]["net_verified_value"] is None


def test_cli_passes_without_network_and_writes_same_stable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "socket", reject_network)
    output_one = tmp_path / "report-one.json"
    output_two = tmp_path / "report-two.json"
    base_args = [
        "--mode",
        "deterministic",
        "--cases",
        str(CASES_PATH),
        "--baseline",
        "baseline-v1",
        "--candidate",
        "candidate-v1",
    ]

    assert runner.main([*base_args, "--output", str(output_one)]) == runner.EXIT_PASS
    assert runner.main([*base_args, "--output", str(output_two)]) == runner.EXIT_PASS
    assert output_one.read_bytes() == output_two.read_bytes()


def test_documented_cases_only_cli_uses_deterministic_defaults() -> None:
    args = runner.parse_args(["--cases", str(CASES_PATH)])

    assert args.baseline == "baseline-v1"
    assert args.candidate == "candidate-v1"
    assert args.output.name == "dataforge-agent-finops-local.json"


def test_digest_mismatch_is_invalid_and_uses_distinct_exit_code(tmp_path: Path) -> None:
    dataset = _dataset()
    dataset["candidate"]["dataset_digest"] = "sha256:" + "0" * 64
    cases_path = tmp_path / "digest-mismatch.json"
    output_path = tmp_path / "invalid-report.json"
    _write_dataset(cases_path, dataset)

    assert runner.main(
        [
            "--cases",
            str(cases_path),
            "--baseline",
            "baseline-v1",
            "--candidate",
            "candidate-v1",
            "--output",
            str(output_path),
        ]
    ) == runner.EXIT_INVALID
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["result"] == "invalid"
    assert report["invalid_count"] == 1
    assert report["production_quality_claim"] is False
    assert report["error"]["code"] == "invalid_dataset"
    assert report["error"]["message"] == "dataset digest mismatch"


def test_regression_has_a_distinct_exit_code(tmp_path: Path) -> None:
    dataset = _dataset()
    continuous_case = next(
        case for case in dataset["cases"] if case["case_id"] == "continuous-001"
    )
    continuous_case["candidate"]["value"] = 100.0
    _refresh_digest(dataset)
    cases_path = tmp_path / "regression.json"
    output_path = tmp_path / "regression-report.json"
    _write_dataset(cases_path, dataset)

    assert runner.main(
        [
            "--cases",
            str(cases_path),
            "--baseline",
            "baseline-v1",
            "--candidate",
            "candidate-v1",
            "--output",
            str(output_path),
        ]
    ) == runner.EXIT_REGRESSION
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["result"] == "regression"
    assert any(gate["status"] == "regression" for gate in report["gates"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("continuous_unit", "units are not comparable"),
        ("cost_currency", "currencies are not comparable"),
        ("cost_window", "windows are not comparable"),
        ("benefit_currency", "benefit currency is not comparable"),
        ("benefit_window", "benefit window is not comparable"),
    ],
)
def test_runner_rejects_incomparable_units_currencies_and_windows(
    mutation: str, message: str
) -> None:
    dataset = _dataset()
    if mutation == "continuous_unit":
        case = next(case for case in dataset["cases"] if case["case_id"] == "continuous-001")
        case["candidate"]["unit"] = "hours"
    else:
        case = next(
            case
            for case in dataset["cases"]
            if case["case_id"] == "unit-economics-scenario-comparability-001"
        )
        if mutation == "cost_currency":
            case["candidate"]["cost"]["currency"] = "EUR"
        elif mutation == "cost_window":
            case["candidate"]["cost"]["window"] = "other-window"
            case["candidate"]["monetized_benefit"]["window"] = "other-window"
        elif mutation == "benefit_currency":
            case["candidate"]["monetized_benefit"]["currency"] = "EUR"
        else:
            case["candidate"]["monetized_benefit"]["window"] = "other-window"
    _refresh_digest(dataset)

    with pytest.raises(runner.DatasetValidationError, match=message):
        _evaluate(dataset)


def test_non_finite_json_and_empty_dataset_fail_closed(tmp_path: Path) -> None:
    non_finite_path = tmp_path / "non-finite.json"
    non_finite_path.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(runner.DatasetValidationError, match="non-finite JSON constant"):
        runner.load_dataset(non_finite_path)

    dataset = _dataset()
    dataset["cases"] = []
    _refresh_digest(dataset)
    with pytest.raises(runner.DatasetValidationError, match="cases must not be empty"):
        _evaluate(dataset)


def test_runner_does_not_reference_outcome_write_interfaces() -> None:
    source = inspect.getsource(runner)

    assert "record_outcome_event" not in source
    assert "verify_outcome_event" not in source
    assert "outcome_store" not in source


def test_permission_violation_is_a_hard_regression_gate() -> None:
    dataset = _dataset()
    case = next(case for case in dataset["cases"] if case["case_id"] == "retrieval-001")
    case["candidate"]["hit_ids"].insert(0, "unauthorized-chunk")
    _refresh_digest(dataset)

    report = _evaluate(dataset)
    gate = next(gate for gate in report["gates"] if gate["name"] == "retrieval.permission_violations")
    assert report["result"] == "regression"
    assert gate["status"] == "regression"
    assert gate["candidate_count"] == 1


def test_runner_rejects_non_sanitized_measurement_scope() -> None:
    dataset = _dataset()
    dataset["measurement_scope"] = "production"
    _refresh_digest(dataset)

    with pytest.raises(
        runner.DatasetValidationError,
        match="measurement_scope must be sanitized_fixture",
    ):
        _evaluate(dataset)


def test_runner_rejects_relevant_ids_outside_the_authorized_corpus() -> None:
    dataset = _dataset()
    case = next(case for case in dataset["cases"] if case["case_id"] == "retrieval-001")
    case["expected"]["relevant_ids"].append("unauthorized-qrel")
    _refresh_digest(dataset)

    with pytest.raises(
        runner.DatasetValidationError,
        match="relevant IDs must be authorized",
    ):
        _evaluate(dataset)
