from __future__ import annotations

import math
from typing import Any


def finite_nonnegative_money_amount(value: Any) -> float | None:
    """Return only finite, non-negative numeric money amounts."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        amount = float(value)
    except (OverflowError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None
