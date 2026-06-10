from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "demo_e2e_last_run.json"
API_BASE = os.environ.get(
    "DATAFORGE_API_BASE",
    "https://ca-dataforge-backend.thankfultree-c0fc8321.eastus2.azurecontainerapps.io",
).rstrip("/")


def _collect(message: str) -> list[dict[str, Any]]:
    response = requests.post(
        f"{API_BASE}/api/chat",
        json={"workspace_id": "demo-corpus", "message": message},
        stream=True,
        timeout=240,
    )
    response.raise_for_status()
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in response.iter_lines(decode_unicode=True):
        if not raw:
            continue
        if raw.startswith("event: "):
            current = {"event": raw.removeprefix("event: "), "data": ""}
            events.append(current)
        elif raw.startswith("data: ") and current is not None:
            current["data"] += raw.removeprefix("data: ")
    for item in events:
        try:
            item["data"] = json.loads(item["data"])
        except json.JSONDecodeError:
            item["data"] = {"text": item["data"]}
    return events


def _event_data(events: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [item["data"] for item in events if item["event"] == event]


def _artifact_downloads(proposal: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expected = {
        "pdf": b"%PDF",
        "concept_image": b"\x89PNG",
        "audio_summary": b"RIFF",
    }
    result: dict[str, dict[str, Any]] = {}
    for key, header in expected.items():
        url = proposal["artifact_urls"][key]
        response = requests.get(f"{API_BASE}{url}", timeout=120)
        response.raise_for_status()
        body = response.content
        if not body.startswith(header):
            raise AssertionError(f"{key} header mismatch")
        result[key] = {
            "url": url,
            "bytes": len(body),
            "header_hex": body[:8].hex(),
        }
    return result


def main() -> int:
    health = requests.get(f"{API_BASE}/api/health", timeout=60)
    health.raise_for_status()

    clarify = _collect("Build something.")
    package = _collect("Create a full package with PDF, concept image, and audio for a data product from this workspace.")
    medical = _collect("Can we build a health diagnosis product from this workspace?")

    final_package = _event_data(package, "final")[-1]
    proposal = final_package["artifact"]["proposal"]
    downloads = _artifact_downloads(proposal)
    medical_audits = _event_data(medical, "audit")
    medical_final = _event_data(medical, "final")[-1]
    trace_agents = sorted(
        {
            item["data"].get("agent")
            for item in package
            if item["event"] == "role_change" and item["data"].get("agent")
        }
        | {"df-coordinator"}
    )
    required_agents = {
        "df-coordinator",
        "df-corpus-analyst",
        "df-feasibility-analyst",
        "df-market-researcher",
        "df-producer",
        "df-auditor",
    }

    checks = {
        "health_ok": health.json().get("ok") is True,
        "clarify_short_circuit": any(item["event"] == "clarify" for item in clarify),
        "six_agents_visible": required_agents.issubset(set(trace_agents)),
        "mcp_visible": any(item["event"] == "tool_result" and item["data"].get("name") == "market_lookup" and item["data"].get("count", 0) > 0 for item in package),
        "full_package_artifacts": all(downloads[key]["bytes"] > 1000 for key in downloads),
        "azure_speech_audio": proposal["audio_summary"].get("mode") == "azure-speech",
        "auditor_revise": any(item.get("verdict") == "revise" for item in medical_audits),
        "auditor_pass_after_revision": any(item.get("verdict") == "pass" for item in medical_audits),
        "honest_no": medical_final["artifact"]["feasibility"]["verdict"] == "not_yet_feasible",
    }
    ok = all(checks.values())
    result = {
        "ok": ok,
        "api_base": API_BASE,
        "checks": checks,
        "event_counts": {
            "clarify": len(clarify),
            "full_package": len(package),
            "medical": len(medical),
        },
        "trace_agents": trace_agents,
        "artifact_downloads": downloads,
        "medical_audits": medical_audits,
        "honest_no_summary": medical_final["text"],
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
