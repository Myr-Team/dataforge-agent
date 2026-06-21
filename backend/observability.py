"""Observability + evaluation snapshot for the product/demo.

Surfaces (a) the live tracing backend (Azure Monitor / OpenTelemetry / App
Insights) status and the model wiring, and (b) a bundled offline/cloud
evaluation summary (the calibration gate + E2E suites) so the "we measure and
calibrate this agent" story is visible in the UI, not just in repo files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_EVAL_PATH = Path(__file__).parent / "data" / "eval_summary.json"


def _eval_summary() -> dict[str, Any] | None:
    try:
        return json.loads(_EVAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tracing_status() -> dict[str, Any]:
    conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    enabled = bool(conn)
    try:
        import azure.monitor.opentelemetry  # noqa: F401

        sdk = True
    except Exception:
        sdk = False
    return {
        "app_insights": enabled,
        "otel_sdk": sdk,
        "exporter": "azure-monitor-opentelemetry" if enabled else None,
        "service_name": os.environ.get("OTEL_SERVICE_NAME", "dataforge-backend"),
        "logger_name": "dataforge.trace",
    }


def _models() -> dict[str, Any]:
    return {
        "chat": os.environ.get("DF_CHAT_DEPLOYMENT", "gpt-5.1"),
        "embedding": os.environ.get("DF_EMBEDDING_DEPLOYMENT") or os.environ.get("DF_EMBED_DEPLOYMENT") or os.environ.get("EMBEDDING_DEPLOYMENT"),
        "image": os.environ.get("DF_IMAGE_DEPLOYMENT") or os.environ.get("IMAGE_DEPLOYMENT"),
    }


def observability_snapshot() -> dict[str, Any]:
    return {
        "tracing": _tracing_status(),
        "models": _models(),
        "eval": _eval_summary(),
    }
