"""Deterministic evaluation gate for the DataForge MAF team runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_framework import (  # noqa: E402
    AgentResponse,
    AgentResponseUpdate,
    AgentSession,
    ResponseStream,
)
from backend.maf_team_runtime import (  # noqa: E402
    MafTeamRequest,
    MafTeamRuntime,
    TransientAgentError,
    select_collaboration_plan,
)
from backend.feasibility_rubric import load_rubric, rubric_version  # noqa: E402


REQUIRED_METRICS = frozenset(
    {
        "selection_accuracy",
        "groundedness",
        "unsupported_claim_rate",
        "latency_ms",
        "tokens",
        "task_completion",
        "fallback_rate",
    }
)
MEASUREMENT_SCOPE = "deterministic_harness"
FIXTURE_REFERENCE_INTERPRETATION = "fixture_reference_propagation_contract_check"
REPORT_DISCLAIMER = (
    "groundedness and unsupported_claim_rate are fixture/reference-propagation "
    "contract checks, not production answer quality measurements"
)


def _unknown_metric(unit: str, *, interpretation: str | None = None) -> dict[str, Any]:
    metric = {
        "value": None,
        "status": "unknown",
        "unit": unit,
        "sample_size": 0,
        "measurement_scope": MEASUREMENT_SCOPE,
        "production_quality_claim": False,
    }
    if interpretation is not None:
        metric["interpretation"] = interpretation
    return metric


def empty_report_schema() -> dict[str, Any]:
    """Return a truthful empty report; unknown measurements remain null."""
    return {
        "schema_version": 1,
        "mode": "unknown",
        "measurement_scope": MEASUREMENT_SCOPE,
        "production_quality_claim": False,
        "disclaimer": REPORT_DISCLAIMER,
        "metrics": {
            "selection_accuracy": _unknown_metric("ratio"),
            "groundedness": _unknown_metric(
                "ratio", interpretation=FIXTURE_REFERENCE_INTERPRETATION
            ),
            "unsupported_claim_rate": _unknown_metric(
                "ratio", interpretation=FIXTURE_REFERENCE_INTERPRETATION
            ),
            "latency_ms": _unknown_metric("milliseconds"),
            "tokens": _unknown_metric("tokens"),
            "task_completion": _unknown_metric("ratio"),
            "fallback_rate": _unknown_metric("ratio"),
        },
        "runtimes": {},
        "cases": [],
    }


class _DeterministicAgent:
    def __init__(self, agent_id: str, registry: "_DeterministicRegistry") -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = f"Deterministic {agent_id}"
        self._registry = registry

    @staticmethod
    def _payload_text(messages: Any) -> str:
        if isinstance(messages, str):
            return messages
        if isinstance(messages, list) and messages:
            return str(getattr(messages[-1], "text", "{}") or "{}")
        return "{}"

    async def _execute(self, payload: str) -> dict[str, Any]:
        self._registry.calls.append(self.id)
        await asyncio.sleep(0)
        failure = self._registry.failures.get(self.id)
        if failure is not None:
            raise failure
        queued = self._registry.outputs[self.id]
        return queued.popleft() if queued else {"completed": True}

    def run(self, messages: Any = None, *, stream: bool = False, **_kwargs: Any) -> Any:
        payload = self._payload_text(messages)
        if not stream:
            async def complete() -> AgentResponse[dict[str, Any]]:
                output = await self._execute(payload)
                return AgentResponse(messages=[], agent_id=self.id, value=output)

            return complete()

        holder: dict[str, dict[str, Any]] = {}

        async def updates():
            holder["output"] = await self._execute(payload)
            yield AgentResponseUpdate(agent_id=self.id)

        def finalize(_updates: Any) -> AgentResponse[dict[str, Any]]:
            return AgentResponse(messages=[], agent_id=self.id, value=holder["output"])

        return ResponseStream(updates(), finalizer=finalize)

    def create_session(self, *, session_id: str | None = None) -> AgentSession:
        return AgentSession(session_id=session_id)

    def get_session(
        self,
        service_session_id: str,
        *,
        session_id: str | None = None,
    ) -> AgentSession:
        return AgentSession(service_session_id=service_session_id, session_id=session_id)


class _DeterministicRegistry:
    def __init__(self, case: Mapping[str, Any]) -> None:
        self.calls: list[str] = []
        self.outputs: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        for agent_id, outputs in case.get("agent_outputs", {}).items():
            self.outputs[agent_id].extend(dict(output) for output in outputs)
        self.failures = {
            agent_id: TransientAgentError("deterministic optional branch failure")
            for agent_id in case.get("failing_agents", [])
        }
        self._agents = {
            agent_id: _DeterministicAgent(agent_id, self)
            for agent_id in (
                "df-coordinator",
                "df-corpus-analyst",
                "df-market-researcher",
                "df-feasibility-analyst",
                "df-auditor",
                "df-producer",
            )
        }

    def agent(self, agent_id: str) -> _DeterministicAgent:
        return self._agents[agent_id]


class ForcedRuntimeFailure(RuntimeError):
    pass


def _request_with_authoritative_evidence(case: Mapping[str, Any]) -> dict[str, Any]:
    request = json.loads(json.dumps(case["request"]))
    request["rubric"] = load_rubric()
    request["rubric_version"] = rubric_version(request["rubric"])
    if not request.get("needs_workspace"):
        return request
    refs = [
        str(ref)
        for ref in case.get("available_evidence", [])
        if str(ref).startswith("workspace:")
    ]
    hits = [
        {
            "id": ref,
            "source_file": "deterministic-evidence.json",
            "chunk_id": ref.replace(":", "-"),
            "content": f"Deterministic fixture evidence for {ref}.",
        }
        for ref in refs
    ]
    catalog = [
        {
            "source_type": "corpus",
            "ref": ref,
            "quote": f"Deterministic fixture evidence for {ref}.",
        }
        for ref in refs
    ]
    request["authoritative_corpus"] = {
        "hits": hits,
        "profile": {"asset_evidence": catalog},
        "opportunities": [],
    }
    request["evidence_catalog"] = catalog
    return request


def _claims(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        raw_claims = value.get("claims")
        if isinstance(raw_claims, list):
            found.extend(dict(claim) for claim in raw_claims if isinstance(claim, Mapping))
        for key, nested in value.items():
            if key != "claims":
                found.extend(_claims(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_claims(nested))
    return found


def _evidence_observation(result: Mapping[str, Any], available: set[str]) -> dict[str, Any]:
    claims = _claims(result)
    supported = 0
    references: set[str] = set()
    for claim in claims:
        refs = {str(ref) for ref in claim.get("evidence_refs", []) if str(ref)}
        references.update(refs)
        if refs & available:
            supported += 1
    return {
        "claim_count": len(claims),
        "supported_claim_count": supported,
        "unsupported_claim_count": len(claims) - supported,
        "evidence_refs": sorted(references),
    }


def _measured(
    value: float | int,
    unit: str,
    sample_size: int,
    *,
    interpretation: str | None = None,
) -> dict[str, Any]:
    metric = {
        "value": round(float(value), 6),
        "status": "measured",
        "unit": unit,
        "sample_size": sample_size,
        "measurement_scope": MEASUREMENT_SCOPE,
        "production_quality_claim": False,
    }
    if interpretation is not None:
        metric["interpretation"] = interpretation
    return metric


def _aggregate_metrics(rows: list[dict[str, Any]], *, selection_applicable: bool) -> dict[str, Any]:
    metrics = empty_report_schema()["metrics"]
    if not rows:
        return metrics

    if selection_applicable:
        correct = sum(row["actual_pattern"] == row["expected_pattern"] for row in rows)
        metrics["selection_accuracy"] = _measured(correct / len(rows), "ratio", len(rows))

    claim_count = sum(row["evidence"]["claim_count"] for row in rows)
    supported = sum(row["evidence"]["supported_claim_count"] for row in rows)
    unsupported = sum(row["evidence"]["unsupported_claim_count"] for row in rows)
    if claim_count:
        metrics["groundedness"] = _measured(
            supported / claim_count,
            "ratio",
            claim_count,
            interpretation=FIXTURE_REFERENCE_INTERPRETATION,
        )
        metrics["unsupported_claim_rate"] = _measured(
            unsupported / claim_count,
            "ratio",
            claim_count,
            interpretation=FIXTURE_REFERENCE_INTERPRETATION,
        )

    metrics["latency_ms"] = _measured(
        sum(row["latency_ms"] for row in rows) / len(rows),
        "milliseconds",
        len(rows),
    )
    token_values = [row["tokens"] for row in rows if row["tokens"] is not None]
    if len(token_values) == len(rows):
        metrics["tokens"] = _measured(sum(token_values), "tokens", len(rows))
    metrics["task_completion"] = _measured(
        sum(bool(row["task_completed"]) for row in rows) / len(rows),
        "ratio",
        len(rows),
    )
    metrics["fallback_rate"] = _measured(
        sum(bool(row["fallback"]) for row in rows) / len(rows),
        "ratio",
        len(rows),
    )
    return metrics


async def _timed(call: Callable[[], Awaitable[Mapping[str, Any]]]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter_ns()
    result = dict(await call())
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return result, elapsed_ms


async def _legacy_case(case: Mapping[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0)
    return dict(case["legacy_result"])


async def _maf_case(
    case: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], bool, int | None]:
    if case.get("force_runtime_failure"):
        raise ForcedRuntimeFailure("forced deterministic runtime failure")
    registry = _DeterministicRegistry(case)
    request = MafTeamRequest.model_validate(_request_with_authoritative_evidence(case))
    result = await MafTeamRuntime(registry).run(request)
    artifact = result.artifact.model_dump() if hasattr(result.artifact, "model_dump") else result.artifact
    tokens = result.summary.metadata.get("tokens")
    measured_tokens = int(tokens) if isinstance(tokens, int) and not isinstance(tokens, bool) else None
    completed = result.summary.status in {"completed", "degraded"}
    return dict(artifact), registry.calls, completed, measured_tokens


async def run_deterministic_evaluation(cases_path: Path | str) -> dict[str, Any]:
    """Execute deterministic legacy and MAF comparisons with no network calls."""
    path = Path(cases_path)
    cases = json.loads(path.read_text(encoding="utf-8"))
    legacy_rows: list[dict[str, Any]] = []
    maf_rows: list[dict[str, Any]] = []

    for case in cases:
        request = case["request"]
        plan = select_collaboration_plan(
            intent=request["intent"],
            output_mode=request["output_mode"],
            needs_workspace=request["needs_workspace"],
            needs_external=request["needs_external"],
            high_impact=request["high_impact"],
        )
        actual_pattern = plan.pattern.value
        available = {str(item) for item in case.get("available_evidence", [])}

        legacy_result, legacy_latency = await _timed(lambda: _legacy_case(case))
        legacy_rows.append(
            {
                "case_id": case["id"],
                "expected_pattern": case["expected_pattern"],
                "actual_pattern": None,
                "latency_ms": legacy_latency,
                "tokens": legacy_result.get("tokens"),
                "task_completed": bool(legacy_result.get("completed")),
                "fallback": False,
                "evidence": _evidence_observation(legacy_result, available),
            }
        )

        fallback_attempts = 0
        runtime_error_category = None
        calls: list[str] = []
        measured_tokens: int | None = None
        started = time.perf_counter_ns()
        try:
            maf_result, calls, task_completed, measured_tokens = await _maf_case(case)
            fallback = False
        except ForcedRuntimeFailure:
            runtime_error_category = "forced_runtime_failure"
            fallback_attempts += 1
            maf_result = await _legacy_case(case)
            task_completed = bool(maf_result.get("completed"))
            measured_tokens = maf_result.get("tokens")
            fallback = True
        maf_latency = (time.perf_counter_ns() - started) / 1_000_000
        evidence = _evidence_observation(maf_result, available)
        maf_rows.append(
            {
                "case_id": case["id"],
                "conditions": list(case["conditions"]),
                "expected_pattern": case["expected_pattern"],
                "actual_pattern": actual_pattern,
                "selected_agents": list(plan.selected_agents),
                "invoked_agents": calls,
                "latency_ms": maf_latency,
                "tokens": measured_tokens,
                "task_completed": task_completed,
                "fallback": fallback,
                "fallback_attempts": fallback_attempts,
                "runtime_error_category": runtime_error_category,
                "evidence": evidence,
            }
        )

    report = empty_report_schema()
    report["mode"] = "deterministic"
    report["metrics"] = _aggregate_metrics(maf_rows, selection_applicable=True)
    report["runtimes"] = {
        "legacy": {
            "metrics": _aggregate_metrics(legacy_rows, selection_applicable=False),
            "cases": legacy_rows,
        },
        "maf": {"metrics": report["metrics"], "cases": maf_rows},
    }
    report["cases"] = maf_rows
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("deterministic",), required=True)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "eval" / "maf_runtime_cases.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = asyncio.run(run_deterministic_evaluation(args.cases))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "metrics": report["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
