from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .excel_to_records import excel_to_records
from .pdf_to_profile import extract_pdf_pages
from .tabular_profile import clean_cell, normalize_headers


def upload_to_records(
    path: Path,
    rel_path: str,
    workspace_id: str,
    *,
    content_type: str | None = None,
) -> list[dict[str, Any]]:
    fmt = _detect_format(path, content_type)
    if fmt == "csv":
        return csv_to_records(path, rel_path, workspace_id)
    if fmt == "json":
        return json_to_records(path, rel_path, workspace_id)
    if fmt == "excel":
        return excel_to_records(path, rel_path, workspace_id)
    if fmt == "markdown":
        return text_to_chunks(path, rel_path, workspace_id)
    if fmt == "pdf":
        return pdf_to_chunks(path, rel_path, workspace_id)
    return []


def csv_to_records(path: Path, rel_path: str, workspace_id: str) -> list[dict[str, Any]]:
    text = _read_text(path)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        return []
    width = max(len(row) for row in rows)
    headers = normalize_headers(rows[0], width)
    records: list[dict[str, Any]] = []
    file_id = _safe_id(path.stem)
    for row_number, raw in enumerate(rows[1:], start=2):
        pairs = [
            (headers[idx], clean_cell(raw[idx] if idx < len(raw) else ""))
            for idx in range(width)
            if clean_cell(raw[idx] if idx < len(raw) else "")
        ]
        if not pairs:
            continue
        content = _record_content(pairs)
        chunk_id = f"{file_id}-row-{row_number}"
        records.append(_search_doc(workspace_id, path, rel_path, chunk_id, content, "csv", row=str(row_number)))
    return records


def json_to_records(path: Path, rel_path: str, workspace_id: str) -> list[dict[str, Any]]:
    data = json.loads(_read_text(path))
    records: list[dict[str, Any]] = []
    file_id = _safe_id(path.stem)
    for table_name, item in _iter_json_records(data, path.stem):
        pairs = [(key, clean_cell(value)) for key, value in _flatten(item).items() if clean_cell(value)]
        if not pairs:
            continue
        row_number = len(records) + 1
        table_id = _safe_id(table_name)
        chunk_id = f"{file_id}-{table_id}-record-{row_number}"
        content = f"collection: {table_name}; " + _record_content(pairs)
        records.append(
            _search_doc(
                workspace_id,
                path,
                rel_path,
                chunk_id,
                content,
                "json",
                sheet=table_name,
                row=str(row_number),
            )
        )
    return records


def text_to_chunks(path: Path, rel_path: str, workspace_id: str, size: int = 1100, overlap: int = 150) -> list[dict[str, Any]]:
    text = _read_text(path)
    chunks = _text_chunks(text, size=size, overlap=overlap)
    file_id = _safe_id(path.stem)
    records: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        chunk_id = f"{file_id}-chunk-{idx:03d}"
        records.append(_search_doc(workspace_id, path, rel_path, chunk_id, chunk, "markdown"))
    return records


def pdf_to_chunks(path: Path, rel_path: str, workspace_id: str, size: int = 1100, overlap: int = 150) -> list[dict[str, Any]]:
    pages = extract_pdf_pages(path)
    file_id = _safe_id(path.stem)
    records: list[dict[str, Any]] = []
    for page in pages:
        page_no = str(page.get("page") or len(records) + 1)
        text = str(page.get("content") or "")
        for idx, chunk in enumerate(_text_chunks(text, size=size, overlap=overlap)):
            chunk_id = f"{file_id}-page-{page_no}-chunk-{idx:03d}"
            records.append(_search_doc(workspace_id, path, rel_path, chunk_id, chunk, "pdf", row=page_no))
    return records


def _iter_json_records(data: Any, fallback_name: str) -> list[tuple[str, Any]]:
    if isinstance(data, list):
        return [(fallback_name, item) for item in data]
    if isinstance(data, dict):
        items: list[tuple[str, Any]] = []
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    items.append((str(key), item))
        if items:
            return items
        return [(fallback_name, data)]
    return [(fallback_name, {"value": data})]


def _flatten(item: Any, prefix: str = "") -> dict[str, str]:
    if not isinstance(item, dict):
        return {prefix or "value": clean_cell(item)}
    flat: dict[str, str] = {}
    for key, value in item.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, name))
        elif isinstance(value, list):
            for idx, child in enumerate(value, start=1):
                child_name = f"{name}.{idx}"
                if isinstance(child, dict):
                    flat.update(_flatten(child, child_name))
                else:
                    flat[child_name] = clean_cell(child)
            if not value:
                flat[name] = "[]"
        else:
            flat[name] = clean_cell(value)
    return flat


def _record_content(pairs: list[tuple[str, str]]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in pairs if value)


def _text_chunks(text: str, size: int, overlap: int) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not normalized:
        return []
    sections = _heading_sections(normalized)
    chunks: list[str] = []
    for section in sections:
        if len(section) <= size:
            chunks.append(section.strip())
            continue
        start = 0
        while start < len(section):
            end = min(start + size, len(section))
            if end < len(section):
                boundary = section.rfind("\n\n", start, end)
                if boundary > start + 300:
                    end = boundary
            chunks.append(section[start:end].strip())
            if end >= len(section):
                break
            start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]


def _heading_sections(text: str) -> list[str]:
    lines = text.splitlines()
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("#") and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return ["\n".join(section).strip() for section in sections if "\n".join(section).strip()]


def _search_doc(
    workspace_id: str,
    path: Path,
    rel_path: str,
    chunk_id: str,
    content: str,
    document_type: str,
    *,
    sheet: str | None = None,
    row: str | None = None,
) -> dict[str, Any]:
    return {
        "@search.action": "mergeOrUpload",
        "id": f"{workspace_id}-{chunk_id}",
        "workspace_id": workspace_id,
        "title": f"{path.stem} {chunk_id}",
        "content": content[:8000],
        "source_file": rel_path,
        "chunk_id": chunk_id,
        "document_type": document_type,
        "language": _detect_language(content),
        "sheet": sheet,
        "row": row,
    }


def _detect_format(path: Path, content_type: str | None = None) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    if suffix in {".xlsx", ".xlsm"}:
        return "excel"
    if suffix in {".md", ".markdown", ".txt", ".text"}:
        return "markdown"
    if suffix == ".pdf":
        return "pdf"
    content = (content_type or "").lower()
    if "json" in content:
        return "json"
    if "csv" in content or "comma-separated" in content:
        return "csv"
    if "spreadsheet" in content or "excel" in content:
        return "excel"
    if "markdown" in content or "text/plain" in content:
        return "markdown"
    if "pdf" in content:
        return "pdf"
    return "unknown"


def _detect_language(text: str) -> str:
    return "zh-Hans" if any("\u4e00" <= char <= "\u9fff" for char in text) else "en"


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "upload"


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
