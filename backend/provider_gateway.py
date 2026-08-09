from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .deepseek_provider import ProviderFailure
from .model_policy import ModelRoute
from .provider_client import ProviderInvocation, ProviderResult
from .provider_fallback import may_fallback


RouteInvoker = Callable[[ModelRoute, ProviderInvocation], ProviderResult]
AttemptRecorder = Callable[[dict[str, object]], object]


@dataclass(frozen=True, slots=True)
class GatewayInvocationResult:
    route: ModelRoute
    result: ProviderResult
    fallback_used: bool


class ProviderGateway:
    """Provider-neutral, one-fallback invocation boundary below Agent routing."""

    def __init__(
        self,
        *,
        invoke_route: RouteInvoker,
        record_attempt: AttemptRecorder | None = None,
    ) -> None:
        self._invoke_route = invoke_route
        self._record_attempt = record_attempt

    def invoke(
        self,
        *,
        invocation: ProviderInvocation,
        primary: ModelRoute,
        fallback: ModelRoute | None = None,
        side_effect_started: bool = False,
    ) -> GatewayInvocationResult:
        try:
            result = self._invoke(primary, invocation)
        except ProviderFailure as error:
            self._record_failure(primary, error)
            if fallback is None or not may_fallback(
                error,
                output_started=bool(error.output_started),
                side_effect_started=side_effect_started,
            ):
                raise
            try:
                fallback_result = self._invoke(fallback, invocation)
            except ProviderFailure as fallback_error:
                self._record_failure(fallback, fallback_error)
                raise
            self._record_success(
                fallback,
                fallback_result,
                selection="fallback",
                fallback_reason=error.category,
            )
            return GatewayInvocationResult(
                route=fallback,
                result=fallback_result,
                fallback_used=True,
            )
        self._record_success(primary, result, selection="primary")
        return GatewayInvocationResult(
            route=primary,
            result=result,
            fallback_used=False,
        )

    def _invoke(
        self,
        route: ModelRoute,
        invocation: ProviderInvocation,
    ) -> ProviderResult:
        routed = invocation.model_copy(update={"model_id": route.model_id})
        return self._invoke_route(route, routed)

    def _record_failure(
        self,
        route: ModelRoute,
        error: ProviderFailure,
    ) -> None:
        self._record(
            {
                **_route_evidence(route),
                "state": "failed",
                "safe_error_category": error.category,
                "status_class": _status_class(error.status_code),
                "output_started": bool(error.output_started),
            }
        )

    def _record_success(
        self,
        route: ModelRoute,
        result: ProviderResult,
        *,
        selection: str,
        fallback_reason: str | None = None,
    ) -> None:
        usage = result.usage.model_dump(mode="json")
        self._record(
            {
                **_route_evidence(route),
                "state": "succeeded",
                "safe_error_category": None,
                "latency_ms": result.latency_ms,
                "output_started": result.output_started,
                "usage": usage,
                "selection": selection,
                "fallback_reason": fallback_reason,
            }
        )

    def _record(self, evidence: dict[str, object]) -> None:
        if self._record_attempt is not None:
            self._record_attempt(evidence)


def _route_evidence(route: ModelRoute) -> dict[str, object]:
    return {
        "route_id": route.route_id,
        "provider_type": route.provider_type,
        "provider_id": route.provider_id,
        "model_id": route.model_id,
    }


def _status_class(status_code: int | None) -> str | None:
    if status_code is None:
        return None
    return f"{int(status_code) // 100}xx"


__all__ = [
    "GatewayInvocationResult",
    "ProviderGateway",
]
