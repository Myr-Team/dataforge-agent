from __future__ import annotations

import json
import os
import re
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

# CID 字体渲染不了 emoji，提前剥掉避免豆腐块
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\U00002B00-\U00002BFF️‍]"
)


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


_CONF_LABEL = {"data_confirmed": "证据充分", "market_inferred": "市场推断", "speculative": "证据不足"}
_VERDICT = {
    "feasible": ("可行", "#0a7d4f", "#e8f8f2"),
    "recommended": ("推荐推进", "#0a7d4f", "#e8f8f2"),
    "conditional": ("有条件可行", "#8a5a00", "#fff4dd"),
    "not_yet_feasible": ("暂不可行", "#8a5a00", "#fff4dd"),
    "not_feasible": ("暂不建议", "#b3261e", "#fdecea"),
    "rejected": ("暂不建议", "#b3261e", "#fdecea"),
}


def _rich_pdf(proposal: dict[str, Any], template: str) -> bytes:
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.pdfmetrics import registerFont
    from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    registerFont(UnicodeCIDFont("STSong-Light"))
    FONT = "STSong-Light"
    BLUE = colors.HexColor("#0071e3")
    INK = colors.HexColor("#1d1d1f")
    MUTED = colors.HexColor("#6e6e73")
    LINE = colors.HexColor("#e5e5ea")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.7 * cm,
        rightMargin=1.7 * cm,
        topMargin=2.15 * cm,
        bottomMargin=1.75 * cm,
        title=str(proposal.get("title") or "DataForge 项目建议书"),
        author="DataForge",
    )
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("df-title", parent=base["Title"], fontName=FONT, fontSize=22, leading=27, textColor=INK, spaceAfter=2, alignment=0),
        "meta": ParagraphStyle("df-meta", parent=base["BodyText"], fontName=FONT, fontSize=8.5, leading=12, textColor=MUTED),
        "h2": ParagraphStyle("df-h2", parent=base["Heading2"], fontName=FONT, fontSize=13, leading=17, textColor=BLUE, spaceBefore=14, spaceAfter=2),
        "body": ParagraphStyle("df-body", parent=base["BodyText"], fontName=FONT, fontSize=9.6, leading=15, textColor=INK, spaceAfter=5),
        "small": ParagraphStyle("df-small", parent=base["BodyText"], fontName=FONT, fontSize=8.3, leading=12, textColor=colors.HexColor("#3f4b53")),
        "cellh": ParagraphStyle("df-cellh", parent=base["BodyText"], fontName=FONT, fontSize=8.6, leading=11, textColor=colors.white),
        "boxbody": ParagraphStyle("df-boxbody", parent=base["BodyText"], fontName=FONT, fontSize=9.6, leading=15.5, textColor=INK, spaceAfter=4),
    }

    story: list[Any] = []

    def section(text: str) -> None:
        story.append(Paragraph(_xml(text), styles["h2"]))
        story.append(HRFlowable(width="100%", thickness=0.8, color=LINE, spaceBefore=3, spaceAfter=7))

    # 结论 / 标题数据
    title = proposal.get("title") or str(proposal.get("opportunity_id") or "DataForge 数据产品机会")
    feasibility = proposal.get("feasibility") or {}
    vraw = str(feasibility.get("verdict") or "unknown")
    vlabel, vcol, vbg = _VERDICT.get(vraw, ("待判定", "#3f4b53", "#eef2f6"))
    conf = str(feasibility.get("overall_confidence") or "unknown")
    gen_time = time.strftime("%Y-%m-%d %H:%M")

    def _cover(canvas: Any, doc_: Any) -> None:
        w, h = A4
        canvas.saveState()
        # 顶部细蓝条
        canvas.setFillColor(BLUE)
        canvas.rect(0, h - 0.42 * cm, w, 0.42 * cm, fill=1, stroke=0)
        # 蓝色 hero band
        band_top, band_h = h - 7.4 * cm, 6.4 * cm
        canvas.setFillColor(colors.HexColor("#0a66d6"))
        canvas.rect(0, band_top, w, band_h, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#0071e3"))
        canvas.rect(0, band_top, w, 0.16 * cm, fill=1, stroke=0)
        # 角落极淡装饰圆
        canvas.setStrokeColor(colors.Color(1, 1, 1, alpha=0.16))
        canvas.setLineWidth(1.2)
        canvas.circle(w - 2.2 * cm, band_top + band_h - 1.6 * cm, 2.6 * cm, stroke=1, fill=0)
        canvas.circle(w - 0.6 * cm, band_top + 0.8 * cm, 1.4 * cm, stroke=1, fill=0)
        # 品牌
        canvas.setFillColor(colors.white)
        canvas.setFont(FONT, 13)
        canvas.drawString(2.0 * cm, band_top + band_h - 1.5 * cm, "DataForge")
        canvas.setFillColor(colors.HexColor("#d4e6ff"))
        canvas.setFont(FONT, 8.5)
        canvas.drawString(2.0 * cm, band_top + band_h - 2.05 * cm, "AI Agent · 数据产品可行性引擎")
        # 标题（白字，CJK 按宽度换行，最多 2 行）
        canvas.setFillColor(colors.white)
        max_w = w - 4.0 * cm
        size = 26 if canvas.stringWidth(title, FONT, 26) <= max_w * 2 else 22
        lines, cur = [], ""
        for ch in title:
            if canvas.stringWidth(cur + ch, FONT, size) > max_w and cur:
                lines.append(cur); cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        lines = lines[:2]
        for i, ln in enumerate(lines):
            canvas.setFont(FONT, size)
            canvas.drawString(2.0 * cm, band_top + 2.3 * cm - i * (size + 9) / 28.35 * cm, ln)
        # band 下方：副标题
        canvas.setFillColor(INK)
        canvas.setFont(FONT, 15)
        canvas.drawString(2.0 * cm, band_top - 1.4 * cm, "数据产品可行性建议书")
        # 结论徽章
        bx, by = 2.0 * cm, band_top - 3.5 * cm
        badge = f"可行性结论：{vlabel}"
        bw = canvas.stringWidth(badge, FONT, 12) + 1.2 * cm
        canvas.setFillColor(colors.HexColor(vbg))
        canvas.roundRect(bx, by, bw, 0.95 * cm, 0.18 * cm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor(vcol))
        canvas.rect(bx, by, 0.1 * cm, 0.95 * cm, fill=1, stroke=0)
        canvas.setFont(FONT, 12)
        canvas.drawString(bx + 0.45 * cm, by + 0.32 * cm, badge)
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 9.5)
        canvas.drawString(bx + bw + 0.5 * cm, by + 0.32 * cm, f"整体置信度：{_CONF_LABEL.get(conf, conf)}")
        # 元信息
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 9)
        canvas.drawString(2.0 * cm, by - 1.4 * cm, f"机会 ID：{proposal.get('opportunity_id', 'unknown')}")
        canvas.drawString(2.0 * cm, by - 1.95 * cm, f"生成时间：{gen_time}")
        # 底部
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(2.0 * cm, 2.4 * cm, w - 2.0 * cm, 2.4 * cm)
        canvas.setFillColor(colors.HexColor("#8e8e93"))
        canvas.setFont(FONT, 8)
        canvas.drawString(2.0 * cm, 1.9 * cm, "由 DataForge 多 Agent 系统生成 · 检索增强 + 独立审计 + 校准评分")
        canvas.drawString(2.0 * cm, 1.5 * cm, "仅供内部决策参考")
        canvas.restoreState()

    # 封面（第 1 页，由 _cover 画）→ 正文从第 2 页开始
    story.append(Spacer(1, 1))
    story.append(PageBreak())

    # 结论高亮框（正文页）
    verdict_para = Paragraph(
        f'<font color="{vcol}">可行性结论：{_xml(vlabel)}</font>'
        f'<font color="#6e6e73">　　|　　整体置信度：{_xml(_CONF_LABEL.get(conf, conf))}</font>',
        styles["boxbody"],
    )
    vt = Table([[verdict_para]], colWidths=[doc.width])
    vt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(vbg)),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(vcol)),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(vt)

    # 执行摘要（浅底框）
    section("执行摘要")
    summary_paras = [Paragraph(_xml(p), styles["boxbody"]) for p in _split_paragraphs(proposal.get("executive_summary") or "暂无执行摘要。", 6)]
    st = Table([[summary_paras or [Paragraph("暂无执行摘要。", styles["boxbody"])]]], colWidths=[doc.width])
    st.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f9fe")),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(st)

    # 五维评分表
    dimensions = feasibility.get("dimensions") or []
    if dimensions:
        section("可行性五维评分")
        rows = [[Paragraph("维度", styles["cellh"]), Paragraph("评分", styles["cellh"]), Paragraph("置信度", styles["cellh"]), Paragraph("理由与证据", styles["cellh"])]]
        for dim in dimensions:
            evidence = dim.get("evidence") or []
            evidence_text = "；".join(str(item.get("quote") or "")[:130] for item in evidence[:2] if item.get("quote"))
            cell = _xml(str(dim.get("rationale") or ""))
            if evidence_text:
                cell += f'<br/><font color="#6e6e73">证据：{_xml(evidence_text)}</font>'
            score = dim.get("score")
            score_txt = f"{score}/5" if score not in (None, "") else "—"
            rows.append([
                Paragraph(_xml(str(dim.get("name") or "")), styles["small"]),
                Paragraph(score_txt, styles["small"]),
                Paragraph(_xml(_CONF_LABEL.get(str(dim.get("confidence")), str(dim.get("confidence") or ""))), styles["small"]),
                Paragraph(cell, styles["small"]),
            ])
        c0, c1, c2 = 2.7 * cm, 1.3 * cm, 2.0 * cm
        table = Table(rows, colWidths=[c0, c1, c2, doc.width - c0 - c1 - c2], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dde3ea")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)

    # 关键缺口
    gaps = feasibility.get("gap_list") or []
    if gaps:
        section("关键缺口")
        for gap in gaps[:8]:
            story.append(Paragraph(f'<font color="#0071e3">●</font>　{_xml(gap)}', styles["body"]))

    # 市场与外部来源
    market = proposal.get("market") or {}
    findings = (market.get("external_findings") or [])[:5]
    section("市场与外部来源")
    if findings:
        for item in findings:
            story.append(Paragraph(
                f'<font color="#8a5a00">[市场推断]</font> {_xml(item.get("claim", ""))}'
                f'<br/><font color="#6e6e73">来源：{_xml(item.get("source_title") or item.get("source_url") or "unknown")}</font>',
                styles["body"],
            ))
    else:
        story.append(Paragraph(_xml(str(market.get("positioning_note") or "暂无外部市场补充。")), styles["body"]))

    # 语料与机会
    opportunities = (proposal.get("opportunities") or [])[:5]
    if opportunities:
        section("语料与机会")
        for opportunity in opportunities:
            story.append(Paragraph(
                f'<font color="#1d1d1f">{_xml(opportunity.get("title") or "")}</font>：{_xml(opportunity.get("description") or "")}',
                styles["body"],
            ))

    def _decorate(canvas: Any, doc_: Any) -> None:
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(BLUE)
        canvas.rect(0, h - 0.42 * cm, w, 0.42 * cm, fill=1, stroke=0)
        canvas.setFillColor(BLUE)
        canvas.setFont(FONT, 8)
        canvas.drawString(1.7 * cm, h - 0.98 * cm, "DataForge · 数据产品可行性建议书")
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(1.7 * cm, 1.25 * cm, w - 1.7 * cm, 1.25 * cm)
        canvas.setFillColor(colors.HexColor("#8e8e93"))
        canvas.setFont(FONT, 7.5)
        canvas.drawString(1.7 * cm, 0.9 * cm, "由 DataForge 多 Agent 系统生成 · 仅供内部决策参考")
        canvas.drawRightString(w - 1.7 * cm, 0.9 * cm, f"第 {doc_.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=_cover, onLaterPages=_decorate)
    return buffer.getvalue()


def _split_paragraphs(text: Any, limit: int) -> list[str]:
    paragraphs = [item.strip() for item in str(text or "").splitlines() if item.strip()]
    if not paragraphs:
        paragraphs = [str(text or "").strip()]
    return [item[:1800] for item in paragraphs[:limit] if item]


def _xml(text: Any) -> str:
    cleaned = _EMOJI_RE.sub("", str(text or ""))
    return escape(cleaned).replace("\n", "<br/>")
