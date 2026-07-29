from __future__ import annotations

"""Executable best-effort sweep; deployment scheduling remains a separate task."""

import sys
from datetime import datetime, timezone
from typing import Any


def run_sweep(evaluator: Any, tenant_refs: tuple[str, ...], workspace_scopes: Any | None = None) -> int:
    for tenant_ref in tenant_refs:
        try:
            scope = tuple(workspace_scopes(tenant_ref)) if workspace_scopes else ()
            evaluator.evaluate_tenant(tenant_ref, now=datetime.now(timezone.utc), workspace_ids=scope)
        except Exception:
            # Per-tenant email/evaluation failures are isolated. Callers that
            # cannot enumerate/connect infrastructure should pass no evaluator.
            continue
    return 0


def main() -> int:
    # Fail closed: production construction is explicitly injected by the job
    # runner; a bare invocation must never pretend a sweep succeeded.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
