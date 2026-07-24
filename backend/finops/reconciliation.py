from __future__ import annotations

from .models import FinOpsRequestEvent


def reconcile_events(
    application: FinOpsRequestEvent,
    apim: FinOpsRequestEvent,
) -> FinOpsRequestEvent:
    """Reconcile duplicate observations of one call without summing usage."""
    if application.tenant_ref != apim.tenant_ref or application.request_ref != apim.request_ref:
        raise ValueError("events do not share a tenant-scoped request reference")

    use_apim_usage = not application.tokens.observed and apim.tokens.observed
    usage_source = apim.usage_source if use_apim_usage else application.usage_source
    evidence_state = apim.evidence_state if use_apim_usage else application.evidence_state
    return application.model_copy(
        update={
            "tokens": apim.tokens if use_apim_usage else application.tokens,
            "usage_source": usage_source,
            "evidence_state": evidence_state,
            "gateway_coverage": "apim_governed",
            "correlation_ref": application.correlation_ref or apim.correlation_ref,
            "apim_correlation_id": application.apim_correlation_id or apim.apim_correlation_id,
            "latency_ms": application.latency_ms if application.latency_ms is not None else apim.latency_ms,
            "status": application.status if application.status != "unknown" else apim.status,
            "model": application.model or apim.model,
            "deployment": application.deployment or apim.deployment,
            "streaming": application.streaming if application.streaming is not None else apim.streaming,
            "internal_correlation_key": application.internal_correlation_key or apim.internal_correlation_key,
        }
    )
