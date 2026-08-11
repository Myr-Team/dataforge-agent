from __future__ import annotations

import re
from typing import Any


_CONFIGURED_PROVIDER_REF = re.compile(r"^provider[-_][a-z0-9][a-z0-9_-]{0,71}$")


def safe_configured_provider_ref(value: Any) -> str | None:
    """Keep only the repository's opaque configured-provider reference form."""

    text = str(value or "").strip().lower()
    return text if _CONFIGURED_PROVIDER_REF.fullmatch(text) else None
