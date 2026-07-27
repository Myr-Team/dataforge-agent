from __future__ import annotations

from typing import Protocol


class ProviderFailureLike(Protocol):
    category: str
    retryable: bool
    status_code: int | None


_TRANSIENT_CATEGORIES = frozenset(
    {
        "timeout",
        "provider_timeout",
        "rate_limited",
        "provider_unavailable",
    }
)


def may_fallback(
    error: ProviderFailureLike,
    *,
    output_started: bool,
    side_effect_started: bool,
) -> bool:
    """Permit one fallback only while the failed attempt is still unobservable."""
    if output_started or side_effect_started:
        return False
    category = str(getattr(error, "category", "") or "").strip().lower()
    if category not in _TRANSIENT_CATEGORIES:
        return False
    if not bool(getattr(error, "retryable", False)):
        return False
    status_code = getattr(error, "status_code", None)
    if category == "rate_limited":
        return status_code in {None, 429}
    if category == "provider_unavailable" and status_code is not None:
        return 500 <= int(status_code) <= 599
    return True


__all__ = ["may_fallback"]
