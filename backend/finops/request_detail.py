from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlparse

from .evidence import (
    build_evidence_alias,
    operation_code_for_event,
    operation_label,
)
from .evidence_repository import EvidenceAliasRepository
from .models import FinOpsRequestEvent
from .query import FinOpsQuery


_TRACE_REF = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[-_ ]?key|access[-_ ]?token|token|password|secret)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_TEXT_LIMIT = 4000
_FOUNDRY_HOSTS = {"ai.azure.com", "portal.azure.com", "portal.azure.cn"}


class FinOpsRequestDetailService:
    def __init__(
        self,
        *,
        query_service: Any,
        alias_repository: EvidenceAliasRepository,
        run_loader: Callable[[str], Mapping[str, Any]],
        workspace_name_resolver: Callable[[str], str],
    ) -> None:
        self.query_service = query_service
        self._alias_repository = alias_repository
        self._run_loader = run_loader
        self._workspace_name_resolver = workspace_name_resolver

    def build(
        self,
        query: FinOpsQuery,
        request_ref: str,
        *,
        can_trace: bool,
    ) -> dict[str, Any] | None:
        scoped = self.query_service.request_detail(query, request_ref)
        if scoped is None:
            return None
        public_event = scoped.get("request")
        if not isinstance(public_event, Mapping):
            return None
        event = FinOpsRequestEvent.model_validate(public_event)
        operation_code = operation_code_for_event(event)
        alias = self._alias_repository.get_or_create(
            build_evidence_alias(
                tenant_ref=event.tenant_ref,
                workspace_id=event.workspace_id,
                workspace_name=self._workspace_name_resolver(event.workspace_id),
                object_kind="request",
                object_ref=event.request_ref,
                operation_code=operation_code,
                occurred_at=event.occurred_at,
            )
        )
        run = self._load_scoped_run(event)
        payload: dict[str, Any] = {
            key: value for key, value in scoped.items() if key != "request"
        }
        payload.update(
            {
                "request_ref": event.request_ref,
                "display": {
                    "name": alias.display_name,
                    "operation": operation_label(alias.operation_code),
                    "occurred_at": _iso(event.occurred_at),
                },
                "status": event.status,
                "metrics": {
                    "latency_ms": event.latency_ms,
                    "tokens": event.tokens.model_dump(mode="json"),
                    "estimated_cost": event.estimated_cost.model_dump(mode="json"),
                    "cache": event.cache.model_dump(mode="json"),
                    "result_cache": event.result_cache.model_dump(mode="json"),
                    "provider_cache": event.provider_cache.model_dump(mode="json"),
                    "gateway_coverage": event.gateway_coverage,
                    "evidence_state": event.evidence_state,
                    "error_category": event.error_category,
                    "routing_policy_revision": event.routing_policy_revision,
                },
                "business_request": _business_request(run),
                "business_response": _business_response(run, event),
                "timeline": _timeline(event),
                "links": {},
            }
        )
        if can_trace:
            payload["technical_refs"] = _technical_refs(event, run)
        return payload

    def _load_scoped_run(self, event: FinOpsRequestEvent) -> Mapping[str, Any]:
        if not event.run_id:
            return {}
        try:
            run = self._run_loader(event.run_id)
        except (FileNotFoundError, ValueError):
            return {}
        if not isinstance(run, Mapping):
            return {}
        if str(run.get("run_id") or "") != event.run_id:
            return {}
        if str(run.get("workspace_id") or "") != event.workspace_id:
            return {}
        return run


def _business_request(run: Mapping[str, Any]) -> dict[str, Any]:
    text = _safe_text(run.get("message"))
    return {"text": text, "status": "recorded" if text else "unavailable"}


def _business_response(
    run: Mapping[str, Any],
    event: FinOpsRequestEvent,
) -> dict[str, Any]:
    if event.status != "succeeded":
        return {"text": None, "status": "unavailable"}
    final = run.get("final") if isinstance(run.get("final"), Mapping) else {}
    text = _safe_text(final.get("text"))
    if text is None:
        artifact = run.get("artifact")
        if isinstance(artifact, Mapping):
            answer = artifact.get("answer")
            if isinstance(answer, Mapping):
                text = _safe_text(answer.get("text"))
    return {"text": text, "status": "recorded" if text else "unavailable"}


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    text = _BEARER.sub("Bearer [已隐藏]", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1\2[已隐藏]", text)
    return text[:_TEXT_LIMIT]


def _timeline(event: FinOpsRequestEvent) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if event.gateway_coverage == "apim_governed":
        items.append({"stage": "gateway", "label": "统一入口", "status": "observed"})
    elif event.gateway_coverage == "app_observed":
        items.append({"stage": "gateway", "label": "应用直连", "status": "observed"})
    else:
        items.append({"stage": "gateway", "label": "网关", "status": "unavailable"})
    if event.run_id:
        items.append({"stage": "orchestration", "label": "MAF 编排", "status": "observed"})
    if event.agent_id or event.deployment or event.model:
        label = " · ".join(
            value
            for value in (event.agent_id, event.deployment or event.model)
            if value
        )
        items.append({"stage": "execution", "label": label, "status": "observed"})
    items.append(
        {
            "stage": "response",
            "label": "完成返回" if event.status == "succeeded" else "请求未成功",
            "status": event.status,
            "latency_ms": event.latency_ms,
        }
    )
    return items


def _technical_refs(
    event: FinOpsRequestEvent,
    run: Mapping[str, Any],
) -> dict[str, str]:
    refs: dict[str, str] = {"request_ref": event.request_ref}
    for key, value in (
        ("run_id", event.run_id),
        ("apim_correlation_id", event.apim_correlation_id),
        ("price_card_revision", event.estimated_cost.price_card_revision),
    ):
        text = str(value or "").strip()
        if text:
            refs[key] = text
    trace = run.get("trace") if isinstance(run.get("trace"), Mapping) else {}
    for key in ("trace_id", "agent_id"):
        value = str(trace.get(key) or "").strip()
        if value and _TRACE_REF.fullmatch(value):
            refs[key] = value
    return refs


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_foundry_trace_link(template: str, trace_id: str) -> str | None:
    configured = str(template or "").strip()
    safe_trace_id = str(trace_id or "").strip()
    if "{trace_id}" not in configured or not _TRACE_REF.fullmatch(safe_trace_id):
        return None
    parsed_template = urlparse(configured)
    if (
        parsed_template.scheme != "https"
        or parsed_template.hostname not in _FOUNDRY_HOSTS
    ):
        return None
    result = configured.replace(
        "{trace_id}",
        quote(safe_trace_id, safe="._:-"),
    )
    if "{" in result or "}" in result:
        return None
    parsed_result = urlparse(result)
    if (
        parsed_result.scheme != "https"
        or parsed_result.hostname not in _FOUNDRY_HOSTS
    ):
        return None
    return result
