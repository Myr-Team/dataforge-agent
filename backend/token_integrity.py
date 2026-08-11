from __future__ import annotations

import math
from typing import Any


def finite_nonnegative_integral_token_count(value: Any) -> int | None:
    """Return only authoritative finite, non-negative integral token counts."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None
