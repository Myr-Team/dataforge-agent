from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

try:
    from ..blob_store import upload_artifact
except ImportError:
    from blob_store import upload_artifact


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"


def _minimal_pdf(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 11 Tf 50 780 Td 14 TL ({safe[:2600]}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(stream.encode('utf-8'))} >> stream\n{stream}\nendstream endobj",
    ]
    body = "%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(body.encode("utf-8")))
        body += obj + "\n"
    xref_at = len(body.encode("utf-8"))
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets[1:]:
        body += f"{off:010d} 00000 n \n"
    body += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return body.encode("utf-8")


def render_pdf_report(proposal: dict[str, Any], template: str = "project_proposal") -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    path = OUT_DIR / f"{proposal.get('opportunity_id', 'proposal')}-{stamp}.pdf"
    mode = "reportlab-project-proposal"
    pdf_error = ""
    try:
        pdf_bytes = _rich_pdf(proposal, template)
    except Exception as exc:
        mode = "minimal-fallback"
        pdf_error = f"{type(exc).__name__}: {exc}"[:700]
        text = f"DataForge {template}\n\n{json.dumps(proposal, indent=2, ensure_ascii=False)}"
        pdf_bytes = _minimal_pdf(text)
    path.write_bytes(pdf_bytes)
    result: dict[str, Any] = {
        "pdf_blob_url": path.as_uri(),
        "artifact_name": path.name,
        "artifact_url": f"/api/artifacts/{path.name}",
        "bytes": path.stat().st_size,
        "local_path": str(path),
        "mode": mode,
        "pdf_error": pdf_error,
    }
    try:
        blob = upload_artifact(path.name, pdf_bytes, "application/pdf")
        result["pdf_blob_url"] = blob.get("blob_url") or result["pdf_blob_url"]
        result["blob_name"] = blob.get("blob_name")
    except Exception as exc:
        result["blob_error"] = f"{type(exc).__name__}: {exc}"[:500]
    return result


def _rich_pdf(proposal: dict[str, Any], template: str) -> bytes:
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.pdfmetrics import registerFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    registerFont(UnicodeCIDFont("STSong-Light"))
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=str(proposal.get("title") or "DataForge 项目建议书"),
    )
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("df-title", parent=base["Title"], fontName="STSong-Light", fontSize=20, leading=26, spaceAfter=14),
        "h2": ParagraphStyle("df-h2", parent=base["Heading2"], fontName="STSong-Light", fontSize=13, leading=18, spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("df-body", parent=base["BodyText"], fontName="STSong-Light", fontSize=9.2, leading=14, spaceAfter=5),
        "small": ParagraphStyle("df-small", parent=base["BodyText"], fontName="STSong-Light", fontSize=8, leading=11, textColor=colors.HexColor("#3f4b53")),
    }
    story: list[Any] = []
    title = proposal.get("title") or str(proposal.get("opportunity_id") or "DataForge 数据产品机会")
    story.append(Paragraph(_xml(f"DataForge 项目建议书：{title}"), styles["title"]))
    story.append(Paragraph(_xml(f"模板：{template}；机会 ID：{proposal.get('opportunity_id', 'unknown')}"), styles["small"]))
    story.append(Spacer(1, 6))

    summary = proposal.get("executive_summary") or "暂无执行摘要。"
    story.append(Paragraph("执行摘要", styles["h2"]))
    for paragraph in _split_paragraphs(summary, 6):
        story.append(Paragraph(_xml(paragraph), styles["body"]))

    feasibility = proposal.get("feasibility") or {}
    story.append(Paragraph("可行性结论", styles["h2"]))
    story.append(
        Paragraph(
            _xml(
                f"结论：{feasibility.get('verdict', 'unknown')}；整体置信度："
                f"{feasibility.get('overall_confidence', 'unknown')}。"
            ),
            styles["body"],
        )
    )
    dimensions = feasibility.get("dimensions") or []
    if dimensions:
        rows = [[Paragraph(_xml("维度"), styles["small"]), Paragraph(_xml("评分"), styles["small"]), Paragraph(_xml("置信度"), styles["small"]), Paragraph(_xml("证据与理由"), styles["small"])]]
        for dim in dimensions:
            evidence = dim.get("evidence") or []
            evidence_text = "；".join(
                f"{item.get('ref', 'unknown')}: {str(item.get('quote') or '')[:180]}" for item in evidence[:3]
            )
            rationale = str(dim.get("rationale") or "")
            rows.append(
                [
                    Paragraph(_xml(str(dim.get("name") or "")), styles["small"]),
                    Paragraph(_xml(str(dim.get("score") or "")), styles["small"]),
                    Paragraph(_xml(str(dim.get("confidence") or "")), styles["small"]),
                    Paragraph(_xml(f"{rationale} 证据：{evidence_text}"), styles["small"]),
                ]
            )
        table = Table(rows, colWidths=[2.5 * cm, 1.2 * cm, 2.4 * cm, 11.0 * cm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f4")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#ccd5da")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(table)

    gaps = feasibility.get("gap_list") or []
    if gaps:
        story.append(Paragraph("关键缺口", styles["h2"]))
        for gap in gaps[:8]:
            story.append(Paragraph(_xml(f"• {gap}"), styles["body"]))

    market = proposal.get("market") or {}
    story.append(Paragraph("市场与外部来源", styles["h2"]))
    for item in (market.get("external_findings") or [])[:5]:
        story.append(
            Paragraph(
                _xml(
                    f"[{item.get('confidence', 'market_inferred')}] {item.get('claim', '')} "
                    f"来源：{item.get('source_title') or item.get('source_url') or 'unknown'}"
                ),
                styles["body"],
            )
        )
    if not market.get("external_findings"):
        story.append(Paragraph(_xml(str(market.get("positioning_note") or "暂无外部市场补充。")), styles["body"]))

    story.append(Paragraph("语料与机会", styles["h2"]))
    for opportunity in (proposal.get("opportunities") or [])[:5]:
        story.append(Paragraph(_xml(f"{opportunity.get('title')}: {opportunity.get('description')}"), styles["body"]))

    doc.build(story)
    return buffer.getvalue()


def _split_paragraphs(text: Any, limit: int) -> list[str]:
    paragraphs = [item.strip() for item in str(text or "").splitlines() if item.strip()]
    if not paragraphs:
        paragraphs = [str(text or "").strip()]
    return [item[:1800] for item in paragraphs[:limit] if item]


def _xml(text: Any) -> str:
    return escape(str(text or "")).replace("\n", "<br/>")
