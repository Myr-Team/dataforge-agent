from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def markdown_to_profile(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    headings = [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")]
    words = re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", text)
    cjk_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    signals = []
    noise = []
    if headings:
        signals.append(f"文档包含 {len(headings)} 个标题层级，可作为主题导航")
    if len(words) >= 80:
        signals.append(f"正文约 {len(words)} 个词/片段，适合做摘要、问答和机会提炼")
    if cjk_chars:
        signals.append("包含中文内容，适合中文检索与中文交互")
    if len(text.strip()) < 300:
        noise.append("文档较短，单独支撑产品结论的证据密度偏低")
    return [
        {
            "name": path.stem,
            "row_count": 1,
            "column_count": 4,
            "columns": [
                {"name": "title", "type": "text", "non_empty": 1 if headings else 0, "missing": 0 if headings else 1, "missing_rate": 0 if headings else 1, "unique_count": len(set(headings))},
                {"name": "body", "type": "long_text", "non_empty": 1 if text.strip() else 0, "missing": 0 if text.strip() else 1, "missing_rate": 0 if text.strip() else 1, "unique_count": 1 if text.strip() else 0},
                {"name": "word_count", "type": "number", "non_empty": 1, "missing": 0, "missing_rate": 0, "unique_count": 1, "numeric": {"min": len(words), "max": len(words), "mean": len(words)}},
                {"name": "heading_count", "type": "number", "non_empty": 1, "missing": 0, "missing_rate": 0, "unique_count": 1, "numeric": {"min": len(headings), "max": len(headings), "mean": len(headings)}},
            ],
            "distribution": {
                "row_count": 1,
                "column_count": 4,
                "missing_cell_rate": 0,
                "duplicate_row_count": 0,
                "char_count": len(text),
                "heading_count": len(headings),
            },
            "signals": signals,
            "noise": noise or ["未发现严重噪声"],
            "excerpt": text[:1200],
        }
    ]
