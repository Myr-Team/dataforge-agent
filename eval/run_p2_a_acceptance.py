"""Generate a machine-readable P2-A acceptance report from component reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_GATES = ("market_relevance", "maf_quality", "tasks", "connectors")
EVIDENCE_KINDS = frozenset({"fixture", "observed"})
OBSERVED_LINEAGE_IDS = frozenset({"observed_id", "build_id", "run_id", "timestamp"})


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _metric_value(value: Any) -> int | float | None:
    if isinstance(value, Mapping):
        value = value.get("value")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _normalized_metric(value: Any, *, production_allowed: bool) -> int | float | str:
    if isinstance(value, Mapping) and value.get("status") == "unknown":
        return "unknown"
    if not production_allowed:
        return "unmeasured"
    numeric = _metric_value(value)
    return numeric if numeric is not None else "unmeasured"


def _performance_metrics(component: Mapping[str, Any], *, production_allowed: bool) -> dict[str, Any]:
    metrics = component.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    return {
        "latency_ms": _normalized_metric(
            metrics.get("latency_ms"), production_allowed=production_allowed
        ),
        "tokens": _normalized_metric(metrics.get("tokens"), production_allowed=production_allowed),
    }


def _valid_observed_lineage(lineage: list[Any]) -> bool:
    return bool(lineage) and all(
        isinstance(item, Mapping)
        and isinstance(item.get("source"), str)
        and bool(item["source"].strip())
        and any(
            isinstance(item.get(key), (str, int, float))
            and not isinstance(item.get(key), bool)
            and bool(str(item[key]).strip())
            for key in OBSERVED_LINEAGE_IDS
        )
        for item in lineage
    )


def _gate(name: str, component: Mapping[str, Any]) -> dict[str, Any]:
    evidence_kind = str(component.get("evidence_kind") or "")
    if evidence_kind not in EVIDENCE_KINDS:
        raise ValueError(f"{name} report must declare evidence_kind fixture or observed")
    sample_count = component.get("sample_count")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
        raise ValueError(f"{name} report must declare a non-negative sample_count")

    passed = bool(component.get("passed"))
    failed_reasons = [str(reason) for reason in _as_list(component.get("failed_reasons")) if str(reason)]
    lineage = _as_list(component.get("inputs")) or _as_list(component.get("lineage"))
    production_requested = bool(component.get("production_claim_allowed"))
    observed_valid = evidence_kind == "observed" and sample_count > 0 and _valid_observed_lineage(lineage)
    if evidence_kind == "observed":
        if sample_count == 0:
            failed_reasons.append("observed_samples_required")
        if not _valid_observed_lineage(lineage):
            failed_reasons.append("observed_lineage_required")
        if not observed_valid:
            passed = False
    if not passed and not failed_reasons:
        failed_reasons = ["gate_failed"]
    production_allowed = production_requested and observed_valid

    return {
        "passed": passed,
        "evidence_kind": evidence_kind,
        "sample_count": sample_count,
        "production_claim_allowed": production_allowed,
        "metrics": _performance_metrics(component, production_allowed=production_allowed),
        "failed_reasons": failed_reasons,
        "inputs_lineage": lineage,
    }


def _run_component(source: str, runner: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return runner()
    except Exception:
        return {
            "evidence_kind": "fixture",
            "sample_count": 0,
            "production_claim_allowed": False,
            "passed": False,
            "failed_reasons": ["component_execution_failed"],
            "inputs": [{"source": source}],
        }


def build_acceptance_report(component_reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Normalize component evidence without promoting fixture results to production claims."""
    missing = [name for name in REQUIRED_GATES if name not in component_reports]
    if missing:
        raise ValueError(f"missing required component reports: {', '.join(missing)}")

    gates = {name: _gate(name, component_reports[name]) for name in REQUIRED_GATES}
    baseline = component_reports.get("baseline")
    if isinstance(baseline, Mapping):
        gates["baseline"] = _gate("baseline", baseline)
    failed_gates = [name for name, gate in gates.items() if not gate["passed"]]
    return {
        "schema_version": 1,
        "report_kind": "p2_a_acceptance",
        "passed": not failed_gates,
        "failed_gates": failed_gates,
        "gates": gates,
    }


def _market_report() -> dict[str, Any]:
    from backend.market_relevance import assess_market_comparison

    context = {
        "opportunity": "retail location intelligence using footfall and dwell time",
        "evidence_digest": "site candidates, rent, transit, footfall, dwell time",
    }
    accepted = assess_market_comparison(
        **context,
        comparison={
            "opportunity_id": "p2-a-market-fixture",
            "competitors": [{
                "name": "Fixture vendor",
                "positioning": "retail site selection and footfall analytics",
                "url": "https://vendor.example",
                "title": "Retail site selection platform",
                "snippet": "Compare sites with footfall and dwell-time intelligence.",
            }],
            "positioning_note": "Fixture-only strict market gate check.",
        },
    )
    unavailable = assess_market_comparison(
        **context,
        comparison={
            "opportunity_id": "p2-a-market-fixture-unavailable",
            "competitors": [{
                "name": "Unrelated fitness product",
                "positioning": "athlete workout tracking",
                "url": "https://unrelated.example",
                "title": "Workout tracking",
                "snippet": "Track athlete workouts and fitness activity.",
            }],
            "positioning_note": "Fixture-only strict market gate check.",
        },
    )
    passed = (
        accepted.get("market_evidence_status") == "available"
        and unavailable.get("market_evidence_status") == "unavailable"
        and "external_market_evidence_unavailable" in unavailable.get("gaps", [])
    )
    return {
        "evidence_kind": "fixture",
        "sample_count": 2,
        "production_claim_allowed": False,
        "passed": passed,
        "failed_reasons": [] if passed else ["strict_market_gate_contract_failed"],
        "inputs": [
            {"source": "backend.market_relevance.assess_market_comparison", "case": "accepted_direct_source"},
            {"source": "backend.market_relevance.assess_market_comparison", "case": "unavailable_without_relevant_source"},
        ],
    }


def _maf_report() -> dict[str, Any]:
    from eval.run_maf_runtime_eval import run_deterministic_evaluation

    cases_path = ROOT / "eval" / "maf_runtime_cases.json"
    report = asyncio.run(run_deterministic_evaluation(cases_path))
    metrics = report.get("metrics", {})
    passed = bool(
        report.get("mode") == "deterministic"
        and report.get("production_quality_claim") is False
        and _metric_value(metrics.get("task_completion")) == 1.0
    )
    return {
        "evidence_kind": "fixture",
        "sample_count": len(_as_list(report.get("cases"))),
        "production_claim_allowed": False,
        "passed": passed,
        "failed_reasons": [] if passed else ["maf_deterministic_contract_failed"],
        "inputs": [{"source": "eval/maf_runtime_cases.json", "measurement_scope": report.get("measurement_scope")}],
        "metrics": metrics,
    }


def _baseline_report() -> dict[str, Any]:
    from eval.run_p2_baseline import build_report, reference_cases

    baseline = build_report(reference_cases(), observed_runs=[])
    baseline["passed"] = baseline.get("evidence_kind") == "fixture" and all(
        value is None for value in baseline.get("metrics", {}).values()
    )
    baseline["failed_reasons"] = [] if baseline["passed"] else ["baseline_fixture_contract_failed"]
    baseline["inputs"] = [{"source": "eval/p2_reference_cases.json"}]
    return baseline


def _task_report() -> dict[str, Any]:
    import backend.task_store as task_store

    with TemporaryDirectory(prefix="dataforge-p2-a-tasks-") as temporary:
        original_dir = task_store.TASK_DIR
        original_blob_configured = task_store.blob_configured
        try:
            task_store.TASK_DIR = Path(temporary) / "tasks"
            task_store.blob_configured = lambda: False
            task = task_store.create_task(
                {"workspace_id": "p2-a-fixture", "task_type": "analysis.run", "action": "analysis.run"},
                actor={"actor_id": "p2-a"},
            )
            claimed = task_store.claim_task(str(task["task_id"]), "p2-a-acceptance")
            completed = task_store.update_task(str(task["task_id"]), status="completed")
            passed = bool(claimed and completed.get("status") == "completed")
        finally:
            task_store.TASK_DIR = original_dir
            task_store.blob_configured = original_blob_configured
    return {
        "evidence_kind": "fixture",
        "sample_count": 1,
        "production_claim_allowed": False,
        "passed": passed,
        "failed_reasons": [] if passed else ["durable_task_lifecycle_failed"],
        "inputs": [{"source": "backend.task_store", "case": "create_claim_complete"}],
    }


def _connector_report() -> dict[str, Any]:
    import backend.connector_store as connector_store
    from backend.connector_secret_store import SessionSecretStore
    from backend.connector_store import ConnectorStore

    with TemporaryDirectory(prefix="dataforge-p2-a-connectors-") as temporary:
        original_blob_configured = connector_store.blob_configured
        try:
            connector_store.blob_configured = lambda: False
            secrets = SessionSecretStore(ttl_seconds=60)
            store = ConnectorStore(Path(temporary) / "connectors")
            record = store.create(
                "p2-a-fixture",
                "sql",
                {"server": "sql.example", "database": "sales"},
                {"username": "reader", "password": "fixture-secret"},
                secrets,
            )
            reloaded, payload = ConnectorStore(Path(temporary) / "connectors").reconnect(
                "p2-a-fixture", str(record["connector_id"]), secrets
            )
            serialized = json.dumps(reloaded, sort_keys=True)
            passed = bool(
                reloaded.get("status") == "connected"
                and reloaded.get("persistence") == "session_only"
                and payload == {"username": "reader", "password": "fixture-secret"}
                and "fixture-secret" not in serialized
            )
        finally:
            connector_store.blob_configured = original_blob_configured
    return {
        "evidence_kind": "fixture",
        "sample_count": 1,
        "production_claim_allowed": False,
        "passed": passed,
        "failed_reasons": [] if passed else ["connector_session_lifecycle_failed"],
        "inputs": [{"source": "backend.connector_store", "case": "session_only_create_reconnect"}],
    }


def component_reports() -> dict[str, dict[str, Any]]:
    """Run existing component report producers and local lifecycle checks."""
    return {
        "baseline": _run_component("eval/run_p2_baseline.py", _baseline_report),
        "market_relevance": _run_component("backend.market_relevance", _market_report),
        "maf_quality": _run_component("eval/run_maf_runtime_eval.py", _maf_report),
        "tasks": _run_component("backend.task_store", _task_report),
        "connectors": _run_component("backend.connector_store", _connector_report),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_acceptance_report(component_reports())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "passed": report["passed"], "failed_gates": report["failed_gates"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
