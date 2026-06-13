from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "eval" / "calibration" / "pool.json"
DEFAULT_OUT = ROOT / "docs" / "batch11_calibration_gate.json"

sys.path.insert(0, str(ROOT))

from backend.feasibility_rubric import load_rubric, score_dataset_records  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, default=POOL)
    parser.add_argument("--rubric", type=Path, default=ROOT / "agents" / "rubrics" / "feasibility.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--bad-rubric-smoke", action="store_true")
    args = parser.parse_args()

    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    rubric = load_rubric(args.rubric)
    result = evaluate_pool(pool, rubric)
    if not result["passed"]:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1

    bad_result = None
    if args.bad_rubric_smoke:
        bad_rubric = copy.deepcopy(rubric)
        for dimension in bad_rubric.get("dimensions") or []:
            if dimension.get("name") == "asset_data":
                dimension["weight"] = 0.0
            elif dimension.get("name") == "market":
                dimension["weight"] = 0.8
            elif dimension.get("name") == "technical":
                dimension["weight"] = 0.0
            elif dimension.get("name") == "resource_cost":
                dimension["weight"] = 0.0
            elif dimension.get("name") == "differentiation_risk":
                dimension["weight"] = 0.2
        bad_result = evaluate_pool(pool, bad_rubric)
        if bad_result["passed"]:
            result["passed"] = False
            result["bad_rubric_smoke"] = {
                "passed": False,
                "reason": "A rubric that ignores data sufficiency unexpectedly passed calibration.",
                "bad_result": bad_result,
            }
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 1
        result["bad_rubric_smoke"] = {
            "passed": True,
            "bad_spearman": bad_result["spearman"],
            "bad_inversions": bad_result["inversion_count"],
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def evaluate_pool(pool: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    gate = rubric.get("calibration_gate") or {}
    min_spearman = float(gate.get("min_spearman", 0.8))
    no_inversions = bool(gate.get("no_pairwise_inversions", True))
    rows: list[dict[str, Any]] = []
    for case in pool.get("cases") or []:
        report = score_dataset_records(case.get("records") or [], rubric)
        rows.append(
            {
                "id": case.get("id"),
                "label_score": float(case.get("label_score")),
                "predicted_score": float(report.get("rubric_weighted_score") or 0.0),
                "verdict": report.get("verdict"),
                "scorecard": report.get("rubric_scorecard"),
            }
        )
    labels = [row["label_score"] for row in rows]
    predictions = [row["predicted_score"] for row in rows]
    spearman = _spearman(labels, predictions)
    inversions = _pairwise_inversions(rows)
    passed = spearman >= min_spearman and (not no_inversions or not inversions)
    return {
        "passed": passed,
        "pool_version": pool.get("version"),
        "rubric_version": rubric.get("rubric_version"),
        "min_spearman": min_spearman,
        "spearman": round(spearman, 4),
        "inversion_count": len(inversions),
        "inversions": inversions,
        "cases": rows,
    }


def _spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    rx = _ranks(xs)
    ry = _ranks(ys)
    mean_x = sum(rx) / len(rx)
    mean_y = sum(ry) / len(ry)
    cov = sum((left - mean_x) * (right - mean_y) for left, right in zip(rx, ry))
    var_x = sum((left - mean_x) ** 2 for left in rx)
    var_y = sum((right - mean_y) ** 2 for right in ry)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x * var_y) ** 0.5


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    idx = 0
    while idx < len(ordered):
        end = idx + 1
        while end < len(ordered) and ordered[end][0] == ordered[idx][0]:
            end += 1
        average_rank = (idx + 1 + end) / 2
        for _, original_index in ordered[idx:end]:
            ranks[original_index] = average_rank
        idx = end
    return ranks


def _pairwise_inversions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inversions: list[dict[str, Any]] = []
    for left in rows:
        for right in rows:
            if left["label_score"] <= right["label_score"]:
                continue
            if left["predicted_score"] < right["predicted_score"]:
                inversions.append(
                    {
                        "higher_label": left["id"],
                        "lower_label": right["id"],
                        "higher_prediction": left["predicted_score"],
                        "lower_prediction": right["predicted_score"],
                    }
                )
    return inversions


if __name__ == "__main__":
    raise SystemExit(main())
