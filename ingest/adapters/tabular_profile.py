from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any


def clean_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_headers(headers: list[Any], width: int) -> list[str]:
    names: list[str] = []
    seen: dict[str, int] = {}
    for idx in range(width):
        raw = clean_cell(headers[idx] if idx < len(headers) else "")
        name = raw or f"column_{idx + 1}"
        name = re.sub(r"\s+", " ", name)
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        names.append(name)
    return names


def infer_table_profile(name: str, rows: list[dict[str, Any]], *, max_columns: int = 80) -> dict[str, Any]:
    row_count = len(rows)
    columns = list(dict.fromkeys(key for row in rows for key in row.keys()))[:max_columns]
    column_profiles = [_profile_column(column, [row.get(column) for row in rows], row_count) for column in columns]
    signals, noise = _signal_noise(column_profiles, row_count)
    return {
        "name": name,
        "row_count": row_count,
        "column_count": len(columns),
        "columns": column_profiles,
        "distribution": {
            "row_count": row_count,
            "column_count": len(columns),
            "missing_cell_rate": _missing_cell_rate(column_profiles, row_count),
            "duplicate_row_count": _duplicate_rows(rows),
        },
        "signals": signals,
        "noise": noise,
    }


def summarize_profile(profile: dict[str, Any]) -> str:
    tables = profile.get("tables") or []
    table_count = len(tables)
    total_rows = sum(int(table.get("row_count") or 0) for table in tables)
    total_columns = sum(int(table.get("column_count") or 0) for table in tables)
    all_signals = [item for table in tables for item in table.get("signals", [])]
    all_noise = [item for table in tables for item in table.get("noise", [])]
    schema_bits = []
    for table in tables[:4]:
        columns = ", ".join(col.get("name", "") for col in (table.get("columns") or [])[:8])
        schema_bits.append(f"{table.get('name', 'table')}({columns})")
    signal_text = "；".join(all_signals[:5]) or "暂未发现稳定高信号字段"
    noise_text = "；".join(all_noise[:4]) or "未发现严重噪声"
    return (
        f"数据画像：{profile.get('name', '上传数据')}，格式 {profile.get('format')}，"
        f"包含 {table_count} 个表/文档片段、约 {total_rows} 行、{total_columns} 个字段。"
        f"主要 schema：{'；'.join(schema_bits) or '无结构化字段'}。"
        f"高信号：{signal_text}。噪声/风险：{noise_text}。"
    )


def profile_search_content(profile: dict[str, Any]) -> str:
    lines = [profile.get("profile_summary") or summarize_profile(profile)]
    for table in profile.get("tables") or []:
        lines.append(
            f"表 {table.get('name')}：{table.get('row_count')} 行，"
            f"{table.get('column_count')} 列，缺失率 {table.get('distribution', {}).get('missing_cell_rate', 0):.2%}。"
        )
        for col in (table.get("columns") or [])[:30]:
            detail = (
                f"字段 {col.get('name')} 类型 {col.get('type')}，"
                f"非空 {col.get('non_empty')}，唯一值 {col.get('unique_count')}，"
                f"缺失率 {col.get('missing_rate', 0):.2%}"
            )
            if col.get("numeric"):
                numeric = col["numeric"]
                detail += f"，范围 {numeric.get('min')} 到 {numeric.get('max')}，均值 {numeric.get('mean')}"
            if col.get("top_values"):
                tops = ", ".join(f"{item['value']}({item['count']})" for item in col["top_values"][:5])
                detail += f"，常见值 {tops}"
            lines.append(detail)
        for signal in table.get("signals", [])[:8]:
            lines.append(f"高信号判断：{signal}")
        for item in table.get("noise", [])[:8]:
            lines.append(f"噪声风险：{item}")
    return "\n".join(lines)


def _profile_column(name: str, values: list[Any], row_count: int) -> dict[str, Any]:
    cleaned = [clean_cell(value) for value in values]
    present = [value for value in cleaned if value]
    missing = max(0, row_count - len(present))
    type_name, numeric_values = _infer_type(present)
    counter = Counter(present)
    profile: dict[str, Any] = {
        "name": name,
        "type": type_name,
        "non_empty": len(present),
        "missing": missing,
        "missing_rate": round(missing / row_count, 4) if row_count else 0,
        "unique_count": len(counter),
        "examples": present[:3],
        "top_values": [{"value": value, "count": count} for value, count in counter.most_common(5)],
    }
    if numeric_values:
        profile["numeric"] = {
            "min": _round(min(numeric_values)),
            "max": _round(max(numeric_values)),
            "mean": _round(sum(numeric_values) / len(numeric_values)),
        }
    return profile


def _infer_type(values: list[str]) -> tuple[str, list[float]]:
    if not values:
        return "empty", []
    numeric_values = [_to_number(value) for value in values]
    numeric_present = [value for value in numeric_values if value is not None]
    if len(numeric_present) / len(values) >= 0.85:
        return "number", numeric_present
    if sum(1 for value in values if _looks_bool(value)) / len(values) >= 0.85:
        return "boolean", []
    if sum(1 for value in values if _looks_date(value)) / len(values) >= 0.75:
        return "date", []
    avg_len = sum(len(value) for value in values) / len(values)
    return ("long_text" if avg_len > 80 else "text"), []


def _signal_noise(columns: list[dict[str, Any]], row_count: int) -> tuple[list[str], list[str]]:
    signals: list[str] = []
    noise: list[str] = []
    for col in columns:
        name = str(col.get("name", ""))
        missing_rate = float(col.get("missing_rate") or 0)
        unique_count = int(col.get("unique_count") or 0)
        type_name = col.get("type")
        if missing_rate >= 0.6:
            noise.append(f"{name} 缺失率 {missing_rate:.0%}，不宜直接作为核心判断字段")
            continue
        if row_count > 3 and unique_count <= 1:
            noise.append(f"{name} 几乎为常量，对分群或产品判断贡献较低")
            continue
        if _looks_identifier(name, unique_count, row_count):
            noise.append(f"{name} 更像标识符，可用于追踪但不宜单独推导业务结论")
            continue
        if type_name == "number":
            numeric = col.get("numeric") or {}
            signals.append(f"{name} 是完整度较高的数值字段，范围 {numeric.get('min')} 到 {numeric.get('max')}")
        elif type_name in {"date", "boolean"}:
            signals.append(f"{name} 是可用于分组/趋势分析的 {type_name} 字段")
        elif unique_count >= 2:
            signals.append(f"{name} 有 {unique_count} 个不同取值，可用于分群、筛选或解释差异")
    return signals[:10], noise[:10]


def _missing_cell_rate(columns: list[dict[str, Any]], row_count: int) -> float:
    if not columns or not row_count:
        return 0
    missing = sum(int(col.get("missing") or 0) for col in columns)
    return round(missing / (len(columns) * row_count), 4)


def _duplicate_rows(rows: list[dict[str, Any]]) -> int:
    fingerprints = [tuple(sorted((key, clean_cell(value)) for key, value in row.items())) for row in rows]
    return len(fingerprints) - len(set(fingerprints))


def _to_number(value: str) -> float | None:
    text = value.replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if value.strip().endswith("%"):
        return number / 100
    return number


def _looks_bool(value: str) -> bool:
    return value.lower() in {"true", "false", "yes", "no", "y", "n", "0", "1", "是", "否"}


def _looks_date(value: str) -> bool:
    text = value.strip().replace("/", "-")
    if not re.match(r"^\d{4}-\d{1,2}-\d{1,2}", text):
        return False
    try:
        datetime.fromisoformat(text[:10])
        return True
    except ValueError:
        return False


def _looks_identifier(name: str, unique_count: int, row_count: int) -> bool:
    lowered = name.lower()
    if any(token in lowered for token in ("id", "uuid", "编号", "编码", "单号")):
        return True
    return bool(row_count >= 10 and unique_count / row_count > 0.95)


def _round(value: float) -> int | float:
    rounded = round(value, 4)
    return int(rounded) if rounded.is_integer() else rounded
