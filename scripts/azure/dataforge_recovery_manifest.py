"""Build a deterministic, repository-safe DataForge Azure recovery manifest."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


_GUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_FORBIDDEN_KEYS = {
    "access_token",
    "client_id",
    "connection_string",
    "endpoint",
    "id",
    "password",
    "principal_id",
    "secret",
    "subscription_id",
    "subscription_key",
    "tenant_id",
    "token",
    "value",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in ("true", "True", 1, "1"):
        return True
    if value in ("false", "False", 0, "0"):
        return False
    return None


def _project_rows(
    payload: Any,
    fields: Iterable[str],
    *,
    sort_key: str = "name",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, Mapping):
            continue
        row = {field: item.get(field) for field in fields if item.get(field) is not None}
        name = _text(row.get("name"))
        if name and not _GUID.search(name):
            rows.append(row)
    return sorted(rows, key=lambda row: _text(row.get(sort_key)).casefold())


def _project_environment(payload: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        secret_ref = _text(item.get("secret_ref") or item.get("secretRef"))
        if secret_ref:
            rows.append({"name": name, "secret_ref": secret_ref})
        else:
            rows.append({"name": name, "source": "configuration"})
    return sorted(rows, key=lambda row: row["name"].casefold())


def _project_container_apps(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        row: dict[str, Any] = {
            "name": name,
            "resource_group": _text(item.get("resource_group")),
            "image": _text(item.get("image")),
            "environment": _project_environment(item.get("environment")),
        }
        for key in ("min_replicas", "max_replicas", "target_port"):
            projected = _integer(item.get(key))
            if projected is not None:
                row[key] = projected
        ingress_external = _boolean(item.get("ingress_external"))
        if ingress_external is not None:
            row["ingress_external"] = ingress_external
        rows.append(row)
    return sorted(rows, key=lambda row: row["name"].casefold())


def _project_jobs(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        row: dict[str, Any] = {
            "name": name,
            "resource_group": _text(item.get("resource_group")),
            "trigger_type": _text(item.get("trigger_type")),
            "image": _text(item.get("image")),
            "environment": _project_environment(item.get("environment")),
        }
        cron_expression = _text(item.get("cron_expression"))
        if cron_expression:
            row["cron_expression"] = cron_expression
        rows.append(row)
    return sorted(rows, key=lambda row: row["name"].casefold())


def _project_apim(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        row: dict[str, Any] = {
            "name": name,
            "resource_group": _text(item.get("resource_group")),
            "sku": _text(item.get("sku")),
            "apis": _project_rows(item.get("apis"), ("name", "path")),
        }
        capacity = _integer(item.get("capacity"))
        if capacity is not None:
            row["capacity"] = capacity
        rows.append(row)
    return sorted(rows, key=lambda row: row["name"].casefold())


def _project_foundry(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"))
        if not name:
            continue
        deployments = sorted(
            {_text(value) for value in item.get("deployments", []) if _text(value)},
            key=str.casefold,
        )
        rows.append(
            {
                "name": name,
                "resource_group": _text(item.get("resource_group")),
                "deployments": deployments,
            }
        )
    return sorted(rows, key=lambda row: row["name"].casefold())


def build_recovery_manifest(payload: Any) -> dict[str, Any]:
    """Project a raw snapshot into the only schema allowed in the repository."""

    source = payload if isinstance(payload, Mapping) else {}
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": _text(source.get("generated_at")),
        "subscription_display_name": _text(source.get("subscription_display_name")),
        "resource_groups": _project_rows(
            source.get("resource_groups"), ("name", "location")
        ),
        "container_apps": _project_container_apps(source.get("container_apps")),
        "jobs": _project_jobs(source.get("jobs")),
        "sql_databases": _project_rows(
            source.get("sql_databases"),
            ("name", "resource_group", "server", "sku", "status"),
        ),
        "api_management": _project_apim(source.get("api_management")),
        "search_services": _project_rows(
            source.get("search_services"),
            (
                "name",
                "resource_group",
                "sku",
                "replica_count",
                "partition_count",
            ),
        ),
        "foundry": _project_foundry(source.get("foundry")),
        "supporting_resources": _project_rows(
            source.get("supporting_resources"),
            ("name", "type", "resource_group", "location"),
        ),
    }
    assert_repository_safe(manifest)
    return manifest


def assert_repository_safe(payload: Any) -> None:
    """Fail closed on Azure identifiers and secret-bearing repository fields."""

    def visit(value: Any, path: str = "root") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).casefold() in _FORBIDDEN_KEYS:
                    raise ValueError(f"sensitive manifest key at {path}.{key}")
                visit(child, f"{path}.{key}")
            return
        if isinstance(value, (list, tuple, set)):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
            return
        if _GUID.search(str(value)):
            raise ValueError(f"sensitive manifest value at {path}")

    visit(payload, "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    source = json.loads(args.input.read_text(encoding="utf-8-sig"))
    manifest = build_recovery_manifest(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
