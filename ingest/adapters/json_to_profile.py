from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .tabular_profile import clean_cell, infer_table_profile


def json_to_profile(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    tables = _tables_from_json(data, path.stem)
    return [infer_table_profile(table_name, records) for table_name, records in tables]


def _tables_from_json(data: Any, fallback_name: str) -> list[tuple[str, list[dict[str, Any]]]]:
    if isinstance(data, dict):
        tables: list[tuple[str, list[dict[str, Any]]]] = []
        for key, value in data.items():
            if not isinstance(value, list):
                continue
            dict_records = [_flatten(item) for item in value if isinstance(item, dict)]
            if dict_records:
                tables.append((str(key), dict_records))
        if tables:
            return tables
    table_name, records = _records_from_json(data, fallback_name)
    return [(table_name, records)]


def _records_from_json(data: Any, fallback_name: str) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(data, list):
        return fallback_name, [_flatten(item) for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        list_items = [(key, value) for key, value in data.items() if isinstance(value, list)]
        dict_list_items = [
            (key, value)
            for key, value in list_items
            if value and sum(1 for item in value if isinstance(item, dict)) / len(value) >= 0.8
        ]
        if dict_list_items:
            key, value = max(dict_list_items, key=lambda item: len(item[1]))
            return key, [_flatten(item) for item in value if isinstance(item, dict)]
        return fallback_name, [_flatten(data)]
    return fallback_name, [{"value": clean_cell(data)}]


def _flatten(item: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in item.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, name))
        elif isinstance(value, list):
            flat[name] = json.dumps(value, ensure_ascii=False)
        else:
            flat[name] = clean_cell(value)
    return flat
