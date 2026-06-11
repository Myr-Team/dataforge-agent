from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .tabular_profile import clean_cell, infer_table_profile, normalize_headers


def csv_to_profile(path: Path) -> list[dict[str, Any]]:
    text = _read_text(path)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(text.splitlines(), dialect)
    rows = list(reader)
    if not rows:
        return [infer_table_profile(path.stem, [])]
    width = max(len(row) for row in rows)
    headers = normalize_headers(rows[0], width)
    records = []
    for raw in rows[1:]:
        if not any(clean_cell(value) for value in raw):
            continue
        records.append({headers[idx]: clean_cell(raw[idx] if idx < len(raw) else "") for idx in range(width)})
    return [infer_table_profile(path.stem, records)]


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
