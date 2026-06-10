from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from orchestrator import _coordinator  # noqa: E402
from router import deterministic_route  # noqa: E402
from schemas import ChatRequest  # noqa: E402


CASES = Path(__file__).with_name("routing_cases.jsonl")
MIN_ACCURACY = 0.9
MIN_CONSISTENCY = 0.95


def _load_cases() -> list[dict]:
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


def _shape(decision) -> dict:
    return {
        "intent": decision.intent,
        "output_mode": decision.output_mode,
        "experts": decision.experts,
        "needs_clarification": decision.needs_clarification,
    }


def main() -> int:
    cases = _load_cases()
    if len(cases) < 8:
        raise AssertionError(f"Expected at least 8 routing cases, got {len(cases)}")

    expected_pass = 0
    consistent = 0
    rows = []
    for case in cases:
        router_decision = deterministic_route(case["message"], "demo-corpus", {"doc_count": 8})
        coordinator_decision = _coordinator(ChatRequest(workspace_id="demo-corpus", message=case["message"]))
        expected = {
            "intent": case["expected_intent"],
            "output_mode": case["expected_output_mode"],
            "experts": case["expected_experts"],
        }
        router_shape = _shape(router_decision)
        coordinator_shape = _shape(coordinator_decision)
        expected_ok = all(router_shape[key] == value for key, value in expected.items())
        consistency_ok = router_shape == coordinator_shape
        expected_pass += int(expected_ok)
        consistent += int(consistency_ok)
        rows.append(
            {
                "id": case["id"],
                "expected_ok": expected_ok,
                "consistent": consistency_ok,
                "router": router_shape,
                "coordinator": coordinator_shape,
            }
        )

    accuracy = expected_pass / len(cases)
    consistency = consistent / len(cases)
    result = {
        "ok": accuracy >= MIN_ACCURACY and consistency >= MIN_CONSISTENCY,
        "cases": len(cases),
        "accuracy": accuracy,
        "consistency": consistency,
        "rows": rows,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
