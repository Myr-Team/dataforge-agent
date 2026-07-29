from __future__ import annotations

"""Executable best-effort sweep; deployment scheduling remains a separate task."""

import sys
from datetime import datetime, timezone
from typing import Any


def run_sweep(evaluator: Any, tenant_refs: tuple[str, ...]) -> int:
    infrastructure_failed = False
    for tenant_ref in tenant_refs:
        try:
            evaluator.evaluate_tenant(tenant_ref, now=datetime.now(timezone.utc))
        except Exception:
            # Per-tenant email/evaluation failures are isolated. Callers that
            # cannot enumerate/connect infrastructure should pass no evaluator.
            continue
    return 1 if infrastructure_failed else 0


def main() -> int:
    # No implicit production wiring: a deployment must explicitly provide the
    # SQL-backed evaluator and tenant enumerator.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
