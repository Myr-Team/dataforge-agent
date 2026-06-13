from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .tabular_profile import infer_table_profile


def extract_pdf_pages(path: Path) -> list[dict[str, Any]]:
    pages = _extract_with_pypdf(path)
    if not pages:
        pages = _extract_with_text_fallback(path)
    if not pages:
        pages = [{"page": 1, "content": "", "char_count": 0, "extraction_status": "empty_or_scanned"}]
    return pages


def pdf_to_profile(path: Path) -> list[dict[str, Any]]:
    rows = extract_pdf_pages(path)
    table = infer_table_profile(path.stem, rows)
    table["document_type"] = "pdf"
    if any(row.get("extraction_status") == "empty_or_scanned" for row in rows):
        table.setdefault("noise", []).append("PDF 文本抽取为空，可能是扫描件或图片型 PDF，需要 OCR 后再精确分析")
    return [table]


def _extract_with_pypdf(path: Path) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return []
    try:
        reader = PdfReader(str(path))
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = _clean_text(text)
        if text:
            rows.append({"page": index, "content": text[:5000], "char_count": len(text), "extraction_status": "text"})
    return rows


def _extract_with_text_fallback(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    text = raw.decode("latin-1", errors="ignore")
    candidates = re.findall(r"\(([^()]{4,})\)", text)
    cleaned = "\n".join(_clean_text(item) for item in candidates)
    cleaned = _clean_text(cleaned)
    if not cleaned:
        return []
    chunks = [cleaned[start : start + 4000] for start in range(0, len(cleaned), 4000)]
    return [
        {"page": index, "content": chunk, "char_count": len(chunk), "extraction_status": "fallback_text"}
        for index, chunk in enumerate(chunks, start=1)
        if chunk.strip()
    ]


def _clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value
