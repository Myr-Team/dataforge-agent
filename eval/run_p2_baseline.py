"""Build an evidence-labelled P2 baseline report from fixtures or observed runs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "eval" / "p2_reference_cases.json"
DEFAULT_BEARER_TOKEN_ENV = "DATAFORGE_BEARER_TOKEN"
METRIC_NAMES = (
    "quality",
    "latency_ms",
    "tokens",
    "market_relevance",
    "task_failure_rate",
    "connector_recovery_rate",
)
CASE_STRING_FIELDS = ("id", "shape", "goal", "evidence_strength")
CASE_LIST_FIELDS = (
    "schema_roles",
    "expected_required_agents",
    "known_unrelated_source_topics",
)
CASE_FIELDS = frozenset((*CASE_STRING_FIELDS, *CASE_LIST_FIELDS))


def _validate_reference_cases(loaded: Any) -> list[dict[str, Any]]:
    if not isinstance(loaded, list):
        raise ValueError("reference cases must be a JSON list")

    case_ids: set[str] = set()
    for index, case in enumerate(loaded):
        if not isinstance(case, dict) or set(case) != CASE_FIELDS:
            raise ValueError(f"reference case {index} must contain exactly {sorted(CASE_FIELDS)}")
        for field in CASE_STRING_FIELDS:
            value = case[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"reference case {index} field {field} must be a non-empty string")
        for field in CASE_LIST_FIELDS:
            value = case[field]
            if not isinstance(value, list) or not value or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise ValueError(
                    f"reference case {index} field {field} must be a non-empty string list"
                )
        case_id = case["id"]
        if case_id in case_ids:
            raise ValueError("reference case ids must be unique")
        case_ids.add(case_id)
    return loaded


def _validate_run_list(runs: Any) -> list[dict[str, Any]]:
    if not isinstance(runs, list):
        raise ValueError("runs must be a list")
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("run entries must be objects")
        if "metrics" not in run:
            continue
        metrics = run["metrics"]
        if not isinstance(metrics, dict):
            raise ValueError("run metrics must be an object")
        for name, value in metrics.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"run metrics.{name} must be numeric")
    return runs


def reference_cases(path: str | Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    """Load the frozen, domain-neutral fixture suite."""
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    return _validate_reference_cases(loaded)


def aggregate_metrics(
    cases: list[dict[str, Any]], observed_runs: list[dict[str, Any]]
) -> dict[str, float | None]:
    """Aggregate only measurements present in observed, lineaged input."""
    del cases
    values: dict[str, list[float]] = {name: [] for name in METRIC_NAMES}
    for run in observed_runs:
        run_metrics = run.get("metrics", {})
        if not isinstance(run_metrics, dict):
            continue
        for name in METRIC_NAMES:
            value = run_metrics.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[name].append(float(value))
    return {name: fmean(samples) if samples else None for name, samples in values.items()}


def build_report(
    cases: list[dict[str, Any]], observed_runs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return a baseline whose evidence label controls production claims."""
    cases = _validate_reference_cases(cases)
    observed_runs = _validate_run_list(observed_runs)
    observed = bool(observed_runs)
    if observed and any(
        not run.get("build_id") or not run.get("run_id") for run in observed_runs
    ):
        raise ValueError("observed runs require build_id and run_id")

    return {
        "version": "p2-baseline.v1",
        "evidence_kind": "observed" if observed else "fixture",
        "production_claim_allowed": observed,
        "sample_count": len(observed_runs) if observed else len(cases),
        "metrics": aggregate_metrics(cases, observed_runs),
        "lineage": [
            {"build_id": run["build_id"], "run_id": run["run_id"]}
            for run in observed_runs
        ],
    }


def _extract_runs(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return _validate_run_list(payload)
    if isinstance(payload, dict) and "runs" in payload:
        return _validate_run_list(payload["runs"])
    if isinstance(payload, dict):
        return _validate_run_list([payload])
    raise ValueError("run input must be a JSON run, run list, or object with runs")


def load_runs(path: str | Path) -> list[dict[str, Any]]:
    """Load a saved JSON payload or a saved SSE stream of JSON payloads."""
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        return _extract_runs(json.loads(text))
    except json.JSONDecodeError:
        payloads = [
            json.loads(line.removeprefix("data:").strip())
            for line in text.splitlines()
            if line.startswith("data:") and line.removeprefix("data:").strip() != "[DONE]"
        ]
        if not payloads:
            raise ValueError("saved run input is not JSON or SSE data") from None
        runs: list[dict[str, Any]] = []
        for payload in payloads:
            runs.extend(_extract_runs(payload))
        return runs


def fetch_runs(
    api_url: str, bearer_token: str, *, build_id: str
) -> list[dict[str, Any]]:
    """Fetch observed run JSON without persisting the supplied bearer token."""
    request = Request(api_url, headers={"Authorization": f"Bearer {bearer_token}"})
    with urlopen(request, timeout=30) as response:  # nosec B310 - caller supplies URL
        runs = _extract_runs(json.loads(response.read().decode("utf-8-sig")))
    return [{**run, "build_id": build_id} for run in runs]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--runs", type=Path, help="Saved JSON or SSE run capture")
    source.add_argument("--api-url", help="Authenticated URL that returns observed run JSON")
    parser.add_argument("--build-id", help="Authoritative build lineage for --api-url")
    parser.add_argument(
        "--bearer-token-env",
        default=DEFAULT_BEARER_TOKEN_ENV,
        help=f"Environment variable for --api-url bearer token (default: {DEFAULT_BEARER_TOKEN_ENV})",
    )
    args = parser.parse_args(argv)
    if args.api_url:
        if not args.build_id:
            parser.error("--api-url requires --build-id")
        if not os.environ.get(args.bearer_token_env):
            parser.error(
                f"--api-url requires a token in environment variable {args.bearer_token_env}"
            )
    elif args.build_id:
        parser.error("--build-id requires --api-url")
    elif args.bearer_token_env != DEFAULT_BEARER_TOKEN_ENV:
        parser.error("--bearer-token-env requires --api-url")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = reference_cases(args.cases)
    if args.runs:
        observed_runs = load_runs(args.runs)
    elif args.api_url:
        observed_runs = fetch_runs(
            args.api_url,
            os.environ[args.bearer_token_env],
            build_id=args.build_id,
        )
    else:
        observed_runs = []
    report = build_report(cases, observed_runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
