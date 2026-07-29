from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import FinOpsRequestEvent, TokenUsage
from .normalization import opaque_ref
from .official_pricing import estimate_official_cost
from .reconciliation import reconcile_events


_CORRELATION = re.compile(
    r"^(?:[0-9a-f]{32}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$",
    re.IGNORECASE,
)


class ApimLlmObservation(BaseModel):
    """Safe projection of joined APIM LLM and gateway logs.

    The model intentionally has no request/response message or body fields.
    """

    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime
    correlation_id: str
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    deployment: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    is_streaming: bool | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    status_code: int | None = Field(default=None, ge=100, le=599)

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _CORRELATION.fullmatch(text):
            raise ValueError("invalid APIM correlation")
        return text


CorrelationRefResolver = Callable[[str, str, str], str]


def default_correlation_ref(tenant_ref: str, correlation_id: str, secret: str) -> str:
    return opaque_ref("corr", tenant_ref, correlation_id, secret=secret)


def apim_usage_query(from_value: str, to_value: str) -> str:
    start = _parse_time(from_value)
    end = _parse_time(to_value)
    if start > end:
        raise ValueError("APIM query window is invalid")
    start_text = _iso(start)
    end_text = _iso(end)
    return "\n".join(
        (
            "let llm = ApiManagementGatewayLlmLog",
            f"| where TimeGenerated between (datetime({start_text}) .. datetime({end_text}))",
            "| summarize arg_max(TimeGenerated, *) by CorrelationId",
            "| project occurred_at=TimeGenerated, CorrelationId, PromptTokens, CompletionTokens, TotalTokens,",
            "    DeploymentName, ModelName, IsStreamCompletion;",
            "let gateway = ApiManagementGatewayLogs",
            f"| where TimeGenerated between (datetime({start_text}) .. datetime({end_text}))",
            "| summarize arg_max(TimeGenerated, *) by CorrelationId",
            "| project occurred_at=TimeGenerated, CorrelationId, latency_ms=TotalTime, status_code=ResponseCode;",
            "let matched = llm",
            "| join kind=leftouter gateway on CorrelationId",
            "| project occurred_at, correlation_id=CorrelationId, prompt_tokens=PromptTokens,",
            "    completion_tokens=CompletionTokens, total_tokens=TotalTokens,",
            "    deployment=DeploymentName, model=ModelName, is_streaming=IsStreamCompletion,",
            "    latency_ms, status_code, record_kind='llm';",
            "let gateway_only = gateway",
            "| join kind=leftanti llm on CorrelationId",
            "| where status_code >= 400",
            "| project occurred_at, correlation_id=CorrelationId, latency_ms, status_code,",
            "    record_kind='gateway_error';",
            "union matched, gateway_only",
            "| order by occurred_at asc",
        )
    )


def summarize_gateway_only_errors(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate Gateway-only 4xx/5xx rows into privacy-safe evidence.

    Gateway-only rows are requests observed in the APIM gateway log that never
    correlate to an LLM completion or application run. Only aggregate counts by
    HTTP status class are surfaced; no correlation identifiers are retained.
    """
    client_error = 0
    server_error = 0
    breakdown: dict[str, int] = {}
    for row in rows:
        try:
            status = int(row.get("status_code"))
        except (TypeError, ValueError):
            continue
        if status < 400 or status > 599:
            continue
        if status < 500:
            client_error += 1
        else:
            server_error += 1
        key = str(status)
        breakdown[key] = breakdown.get(key, 0) + 1
    return {
        "total": client_error + server_error,
        "client_error_4xx": client_error,
        "server_error_5xx": server_error,
        "status_breakdown": dict(sorted(breakdown.items())),
    }


def reconcile_apim_observations(
    application_events: Iterable[FinOpsRequestEvent],
    observations: Iterable[ApimLlmObservation],
    *,
    hmac_secret: str,
    correlation_ref_resolver: CorrelationRefResolver = default_correlation_ref,
) -> list[FinOpsRequestEvent]:
    applications = list(application_events)
    by_correlation: dict[str, FinOpsRequestEvent] = {
        event.correlation_ref: event
        for event in applications
        if event.correlation_ref
    }
    reconciled: dict[tuple[str, str], FinOpsRequestEvent] = {
        (event.tenant_ref, event.request_ref): event for event in applications
    }
    for observation in observations:
        matched: FinOpsRequestEvent | None = None
        correlation_ref: str | None = None
        for tenant_ref in {event.tenant_ref for event in applications}:
            candidate = correlation_ref_resolver(
                tenant_ref,
                observation.correlation_id,
                hmac_secret,
            )
            if candidate in by_correlation:
                matched = by_correlation[candidate]
                correlation_ref = candidate
                break
        if matched is None:
            continue
        apim_event = matched.model_copy(
            update={
                "occurred_at": observation.occurred_at,
                "deployment": observation.deployment or matched.deployment,
                "model": observation.model or matched.model,
                "status": _status(observation.status_code),
                "latency_ms": observation.latency_ms,
                "tokens": TokenUsage(
                    input=observation.prompt_tokens,
                    output=observation.completion_tokens,
                    total=observation.total_tokens,
                ),
                "gateway_coverage": "apim_governed",
                "evidence_state": "estimated" if observation.is_streaming else "observed",
                "usage_source": "apim",
                "streaming": observation.is_streaming,
                "correlation_ref": correlation_ref,
                "apim_correlation_id": observation.correlation_id,
            }
        )
        reconciled[(matched.tenant_ref, matched.request_ref)] = reconcile_events(matched, apim_event)
    return sorted(reconciled.values(), key=lambda event: (event.occurred_at, event.request_ref))


def price_reconciled_events(
    events: Iterable[FinOpsRequestEvent],
    *,
    tenant_ref: str,
    price_mapping_repository: Any,
) -> list[FinOpsRequestEvent]:
    """Price events that recovered usage from APIM using official mappings.

    Only unpriced events with observed usage and a compatible official mapping
    are estimated. Deployments without a mapping remain unpriced; the manual
    price list is never consulted.
    """
    priced: list[FinOpsRequestEvent] = []
    for event in events:
        if (
            price_mapping_repository is not None
            and event.estimated_cost.amount is None
            and event.tokens.observed
        ):
            deployment = event.deployment or event.model
            mapping = (
                price_mapping_repository.get(tenant_ref, deployment)
                if deployment
                else None
            )
            if mapping is not None:
                estimate = estimate_official_cost(
                    mapping.official_price_key,
                    mapping.mapping_revision,
                    event.tokens,
                )
                event = event.model_copy(update={"estimated_cost": estimate})
        priced.append(event)
    return priced


def collect_apim_usage(
    *,
    repository: Any,
    query_rows: Callable[[str], Iterable[dict[str, Any]]],
    tenant_ref: str,
    workspace_ids: tuple[str, ...],
    from_value: str,
    to_value: str,
    hmac_secret: str,
    price_mapping_repository: Any | None = None,
) -> dict[str, Any]:
    application_events = repository.list_events(
        tenant_ref=tenant_ref,
        workspace_ids=workspace_ids,
        from_value=from_value,
        to_value=to_value,
    )
    observations: list[ApimLlmObservation] = []
    gateway_only_rows: list[Mapping[str, Any]] = []
    rejected = 0
    for row in query_rows(apim_usage_query(from_value, to_value)):
        record_kind = str(row.get("record_kind") or "llm") if isinstance(row, Mapping) else "llm"
        if record_kind == "gateway_error":
            gateway_only_rows.append(row)
            continue
        payload = (
            {key: value for key, value in row.items() if key != "record_kind"}
            if isinstance(row, Mapping)
            else row
        )
        try:
            observations.append(ApimLlmObservation.model_validate(payload))
        except (TypeError, ValueError):
            rejected += 1
    reconciled = reconcile_apim_observations(
        application_events,
        observations,
        hmac_secret=hmac_secret,
    )
    reconciled = price_reconciled_events(
        reconciled,
        tenant_ref=tenant_ref,
        price_mapping_repository=price_mapping_repository,
    )
    repository.upsert_events(reconciled)
    correlation_refs = {
        event.correlation_ref for event in application_events if event.correlation_ref
    }
    matched = sum(
        default_correlation_ref(
            tenant_ref,
            observation.correlation_id,
            hmac_secret,
        )
        in correlation_refs
        for observation in observations
    )
    return {
        "application_events": len(application_events),
        "apim_observations": len(observations),
        "rejected_observations": rejected,
        "reconciled_events": matched,
        "unmatched_observations": max(0, len(observations) - matched),
        "gateway_only_errors": summarize_gateway_only_errors(gateway_only_rows),
        "window": {"from": from_value, "to": to_value},
    }


def _status(value: int | None) -> str:
    if value is None:
        return "unknown"
    if 200 <= value < 400:
        return "succeeded"
    return "failed"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
