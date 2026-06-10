from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.rag import search


def main() -> int:
    hits = search("demo-corpus", "health diagnosis medical consent clinical outcomes annotated labels", top_k=6)
    text = "\n".join(hit["content"] for hit in hits).lower()
    required = ["medical consent", "clinical", "diagnosis"]
    missing = [term for term in required if term not in text]
    if missing:
        print(json.dumps({"error": "missing evidence", "missing": missing, "hits": hits[:2]}, indent=2), file=sys.stderr)
        return 1

    gaps = [
        "explicit medical consent",
        "clinical governance",
        "validated outcome labels",
        "medical partner review",
    ]
    report = {
        "opportunity_id": "health-diagnosis-product",
        "verdict": "not_yet_feasible",
        "gap_list": gaps,
        "evidence_sources": sorted({hit["source_file"] for hit in hits}),
    }
    if report["verdict"] != "not_yet_feasible" or len(report["gap_list"]) < 3:
        print(json.dumps(report, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

