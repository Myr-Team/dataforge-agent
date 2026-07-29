from __future__ import annotations

import pytest

from backend.deepseek_provider import ProviderFailure
from backend.provider_fallback import may_fallback


@pytest.mark.parametrize(
    "failure",
    [
        ProviderFailure("timeout", retryable=True),
        ProviderFailure("provider_timeout", retryable=True),
        ProviderFailure("rate_limited", retryable=True, status_code=429),
        ProviderFailure("provider_unavailable", retryable=True, status_code=500),
        ProviderFailure("provider_unavailable", retryable=True, status_code=503),
    ],
)
def test_fallback_is_allowed_only_for_pre_output_transient_failures(
    failure: ProviderFailure,
) -> None:
    assert may_fallback(
        failure,
        output_started=False,
        side_effect_started=False,
    )


@pytest.mark.parametrize(
    "failure",
    [
        ProviderFailure("invalid_request", retryable=False, status_code=400),
        ProviderFailure("authentication_failed", retryable=False, status_code=401),
        ProviderFailure("insufficient_balance", retryable=False, status_code=402),
        ProviderFailure("invalid_parameters", retryable=False, status_code=422),
        ProviderFailure("content_policy", retryable=False),
    ],
)
def test_fallback_rejects_non_transient_failures(failure: ProviderFailure) -> None:
    assert not may_fallback(
        failure,
        output_started=False,
        side_effect_started=False,
    )


def test_fallback_rejects_any_started_output_or_side_effect() -> None:
    failure = ProviderFailure("rate_limited", retryable=True, status_code=429)

    assert not may_fallback(
        failure,
        output_started=True,
        side_effect_started=False,
    )
    assert not may_fallback(
        failure,
        output_started=False,
        side_effect_started=True,
    )
