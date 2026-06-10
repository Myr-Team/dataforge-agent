from __future__ import annotations

import json
import logging
import os
from typing import Any


LOGGER = logging.getLogger("dataforge.trace")
logging.basicConfig(level=logging.WARNING)
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
LOGGER.propagate = False
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    LOGGER.addHandler(handler)


def configure_monitoring() -> None:
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=connection_string, logger_name="dataforge.trace")
        LOGGER.info("dataforge_monitoring_configured")
    except Exception as exc:
        LOGGER.warning("dataforge_monitoring_fallback %s", exc)


def trace_event(event: str, data: Any, conversation_id: str | None = None) -> None:
    payload = {
        "event": event,
        "conversation_id": conversation_id,
        "data": data,
    }
    LOGGER.info("dataforge_trace %s", json.dumps(payload, ensure_ascii=False, default=str))
