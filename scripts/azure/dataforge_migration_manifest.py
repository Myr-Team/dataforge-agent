"""Build migration evidence that never contains Azure identifiers or secrets."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


_GUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_FORBIDDEN_KEYS = {
    "id",
    "subscription_id",
    "tenant_id",
    "principal_id",
    "client_id",
    "secret",
    "value",
    "token",
    "password",
}


def sanitize_resource_inventory(payload: Any) -> list[dict[str, str]]:
    """Project an Azure resource list into the non-sensitive migration schema."""

    rows: list[dict[str, str]] = []
    source = payload if isinstance(payload, list) else []
    for item in source:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "name": str(item.get("name") or ""),
                "type": str(item.get("type") or ""),
                "resource_group": str(
                    item.get("resourceGroup") or item.get("resource_group") or ""
                ),
                "location": str(item.get("location") or ""),
            }
        )
    assert_no_sensitive_values(rows)
    return rows


def resource_names_by_group(
    rows: Iterable[Mapping[str, Any]],
    allowed_groups: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Group only explicitly allowlisted resource groups for deletion review."""

    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for row in rows:
        group = str(row.get("resource_group") or "")
        if group in allowed_groups:
            name = str(row.get("name") or "")
            if name:
                grouped[group].append(name)
    result = {
        group: sorted(set(names))
        for group, names in sorted(grouped.items())
    }
    assert_no_sensitive_values(result)
    return result


def assert_no_sensitive_values(payload: Any) -> None:
    """Reject identifier-shaped values and secret-bearing field names."""

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in _FORBIDDEN_KEYS:
                    raise ValueError("sensitive manifest key")
                visit(child)
            return
        if isinstance(value, (list, tuple, set)):
            for child in value:
                visit(child)
            return
        if _GUID.search(str(value)):
            raise ValueError("sensitive manifest value")

    visit(payload)
