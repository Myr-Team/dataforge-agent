from __future__ import annotations

from typing import Any


def build_monitoring_snapshot(
    usage: dict[str, Any],
    audit: dict[str, Any],
    *,
    gateway_enabled: bool,
    expected_gateway_id: str | None,
    gateway_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project persisted evidence into a dashboard-safe monitoring contract."""
    totals = usage.get("totals") if isinstance(usage.get("totals"), dict) else {}
    gateway_id = str(expected_gateway_id or "").strip()
    evidence = gateway_evidence if isinstance(gateway_evidence, dict) else {}
    if not gateway_enabled:
        gateway = {
            "state": "not_configured",
            "governed_calls": None,
            "provenance": "apim_not_configured",
        }
    elif gateway_id and str(evidence.get("state") or "").strip().lower() == "verified":
        calls = evidence.get("governed_calls")
        gateway = {
            "state": "verified",
            "governed_calls": int(calls) if isinstance(calls, (int, float)) and not isinstance(calls, bool) and calls >= 0 else None,
            "provenance": "apim_custom_metric",
        }
    elif gateway_id:
        gateway = {
            "state": "configured_unverified",
            "governed_calls": None,
            "provenance": "apim_correlation_pending",
        }
    else:
        gateway = {
            "state": "misconfigured",
            "governed_calls": None,
            "provenance": "apim_gateway_id_missing",
        }
    return {
        "evidence_source": str(usage.get("source") or "run_store"),
        "usage": {
            "status": str(totals.get("usage_status") or "unknown"),
            "total_tokens": totals.get("total_tokens"),
            "input_tokens": totals.get("prompt_tokens"),
            "output_tokens": totals.get("completion_tokens"),
            "known_runs": int(totals.get("known_usage_runs") or 0),
            "unknown_runs": int(totals.get("unknown_usage_runs") or 0),
        },
        "gateway": gateway,
        "reliability": {
            "completed_runs": int(totals.get("completed_runs") or 0),
            "failed_runs": int(totals.get("failed_runs") or 0),
            "audit_events": int(audit.get("count") or 0),
            "provenance": "run_store_and_audit_store",
        },
    }
