from __future__ import annotations

import json
import re
import time
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from xml.sax.saxutils import escape

try:
    from ..blob_store import upload_artifact
except ImportError:
    from blob_store import upload_artifact


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"
LOGO_PATH = ROOT / "backend" / "assets" / "dataforge-logo.png"

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\U00002B00-\U00002BFF\uFE0F]"
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_MOJIBAKE_RE = re.compile(r"[\u0080-\u009f]|[ÃÂÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ]{2,}")


def _repair_mojibake_text(value: Any) -> str:
    text = str(value or "")
    if not text or not _MOJIBAKE_RE.search(text):
        return text

    def score(candidate: str) -> int:
        cjk = len(_CJK_RE.findall(candidate))
        markers = len(_MOJIBAKE_RE.findall(candidate))
        controls = sum(1 for ch in candidate if 0x80 <= ord(ch) <= 0x9F)
        replacements = candidate.count("\ufffd")
        return cjk * 8 - markers * 5 - controls * 12 - replacements * 20

    best = text
    best_score = score(text)
    for encoding in ("latin1", "cp1252"):
        try:
            candidate = text.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidate_score = score(candidate)
        if candidate_score > best_score:
            best = candidate
            best_score = candidate_score
    return best


def _repair_mojibake_value(value: Any) -> Any:
    if isinstance(value, str):
        return _repair_mojibake_text(value)
    if isinstance(value, list):
        return [_repair_mojibake_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_mojibake_value(item) for key, item in value.items()}
    return value


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
    proposal = _repair_mojibake_value(proposal)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    doc_meta = proposal.get("doc_meta") if isinstance(proposal.get("doc_meta"), dict) else {}
    version = _safe_name(doc_meta.get("version") or "V1")
    path = OUT_DIR / f"{_safe_name(proposal.get('opportunity_id') or 'proposal')}-{version}-{stamp}.pdf"
    mode = "weasyprint-html-v1"
    pdf_error = ""
    pdf_bytes = None
    try:
        pdf_bytes = _html_pdf(proposal, template)
    except Exception as exc:
        pdf_error = f"html:{type(exc).__name__}: {exc}"[:400]
        try:
            pdf_bytes = _rich_pdf(proposal, template)
            mode = "reportlab-fallback"
        except Exception as exc2:
            mode = "minimal-fallback"
            pdf_error = (pdf_error + f" | reportlab:{type(exc2).__name__}: {exc2}")[:700]
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


_CONF_LABEL = {"data_confirmed": "数据支撑", "market_inferred": "市场推断", "speculative": "待验证"}
_VERDICT = {
    "feasible": ("可行", "#107C10", "#E8F5E9"),
    "recommended": ("建议推进", "#107C10", "#E8F5E9"),
    "conditional": ("有条件可行", "#986F0B", "#FFF4CE"),
    "not_yet_feasible": ("暂不可行", "#986F0B", "#FFF4CE"),
    "not_feasible": ("暂不建议", "#A4262C", "#FDE7E9"),
    "rejected": ("暂不建议", "#A4262C", "#FDE7E9"),
}


_HTML_CSS = """
@page {
  size: A4;
  margin: 20mm 16mm 18mm 16mm;
  @bottom-center { content: "DataForge · 可行性分析报告"; font-size: 8px; color: #9aa1ae; }
  @bottom-right { content: counter(page) " / " counter(pages); font-size: 8px; color: #9aa1ae; }
}
@page :first { margin: 0; @bottom-center { content: ""; } @bottom-right { content: ""; } }
* { box-sizing: border-box; }
html { -weasy-hyphens: none; }
body { font-family: "WenQuanYi Micro Hei","Microsoft YaHei","Noto Sans CJK SC",sans-serif; color: #2b303a; font-size: 10.3px; line-height: 1.72; margin: 0; }
h1,h2,h3 { margin: 0; font-weight: 700; }
p { margin: 0 0 7px; }
.cover { height: 297mm; padding: 0; position: relative; page-break-after: always; }
.cover-band { height: 128mm; background: linear-gradient(135deg,#1e40af 0%,#2563eb 55%,#3b82f6 100%); color: #fff; padding: 26mm 20mm 0; position: relative; }
.cover-brand { display: flex; align-items: center; gap: 12px; margin-bottom: 34mm; }
.cover-brand img { height: 30px; }
.cover-brand .bt { font-size: 15px; font-weight: 700; letter-spacing: .3px; }
.cover-kicker { font-size: 12px; opacity: .9; letter-spacing: 3px; text-transform: uppercase; margin-bottom: 12px; }
.cover-title { font-size: 27px; line-height: 1.24; font-weight: 800; max-width: 170mm; }
.cover-body { padding: 16mm 20mm 0; }
.cover-badges { display: flex; gap: 10px; align-items: center; margin-bottom: 16px; }
.badge { display: inline-block; padding: 6px 16px; border-radius: 999px; font-size: 13px; font-weight: 700; }
.badge-sub { font-size: 10.5px; color: #6b7280; }
.cover-meta { margin-top: 10mm; color: #6b7280; font-size: 10px; border-top: 1px solid #e5e7eb; padding-top: 8px; }
.cover-meta span { margin-right: 18px; }
.cover-brandlogo { position: absolute; right: 20mm; bottom: 8mm; height: 40px; opacity: .95; }
h1.sec { font-size: 15px; color: #0f3a75; border-left: 4px solid #2563eb; padding: 3px 0 3px 12px; margin: 20px 0 11px; background: linear-gradient(90deg,#f4f8ff,transparent); }
h2.sub { font-size: 12px; color: #1d4ed8; margin: 12px 0 6px; }
.lead { color: #374151; }
ul.dots { margin: 4px 0 8px; padding-left: 2px; list-style: none; }
ul.dots li { position: relative; padding-left: 16px; margin-bottom: 5px; }
ul.dots li::before { content: "•"; color: #2563eb; position: absolute; left: 2px; font-weight: 700; }
.scorecard { border: 1px solid #e8edf5; border-radius: 10px; padding: 12px 14px; }
.scorerow { display: flex; align-items: center; gap: 10px; padding: 7px 0; border-bottom: 1px solid #f1f4f9; }
.scorerow:last-child { border-bottom: 0; }
.scorerow .nm { width: 92px; font-weight: 600; color: #374151; flex: none; font-size: 10px; }
.scorerow .track { display: block; flex: 1; height: 8px; background: #eef1f6; border-radius: 999px; overflow: hidden; }
.scorerow .fill { display: block; height: 8px; border-radius: 999px; background: #2563eb; }
.scorerow .sc { width: 34px; text-align: right; font-weight: 700; color: #1d4ed8; flex: none; font-size: 10px; }
.scorerow .cf { width: 56px; flex: none; }
.scorerow .rt { flex: 1.6; color: #6b7280; font-size: 9px; line-height: 1.5; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 6px; font-size: 8.5px; font-weight: 600; }
.chip.data_confirmed { background: #e7f4ee; color: #0a7d4f; }
.chip.market_inferred { background: #fdf3e6; color: #b45309; }
.chip.speculative { background: #eef2f7; color: #475569; }
table.grid { width: 100%; border-collapse: collapse; margin: 8px 0 6px; font-size: 9.2px; }
table.grid th { background: #2563eb; color: #fff; text-align: left; padding: 7px 9px; font-weight: 600; font-size: 9px; }
table.grid td { padding: 7px 9px; border-bottom: 1px solid #eef1f6; vertical-align: top; color: #374151; }
table.grid tr:nth-child(even) td { background: #fafbfe; }
.sev { display: inline-block; padding: 1px 7px; border-radius: 5px; font-size: 8.5px; font-weight: 700; }
.sev.high,.sev.高 { background: #fde7e9; color: #a4262c; }
.sev.medium,.sev.中 { background: #fff4ce; color: #986f0b; }
.sev.low,.sev.低 { background: #e7f4ee; color: #0a7d4f; }
.mk { margin: 6px 0; }
.mk .tag { color: #b45309; font-weight: 700; font-size: 9px; }
.mk .src { color: #9aa1ae; font-size: 8.5px; }
.phase { border: 1px solid #e8edf5; border-radius: 9px; padding: 10px 12px; margin-bottom: 8px; }
.phase .ph { font-weight: 700; color: #0f3a75; font-size: 11px; }
.phase .metric { color: #6b7280; font-size: 9px; margin-top: 2px; }
"""


def _logo_data_uri(source_bytes: bytes | None) -> str:
    if not source_bytes:
        return ""
    import base64 as _b64
    return "data:image/png;base64," + _b64.b64encode(source_bytes).decode("ascii")


def _brand_logo_bytes(proposal: dict[str, Any]) -> bytes | None:
    for source in _brand_logo_sources(proposal):
        loaded = _load_logo_bytes(source)
        if loaded:
            return loaded
    return None


def _html_pdf(proposal: dict[str, Any], template: str) -> bytes:
    from weasyprint import HTML
    from html import escape as esc

    feasibility = proposal.get("feasibility") or {}
    verdict_raw = str(feasibility.get("verdict") or "unknown")
    v_label, v_color, v_bg = _VERDICT.get(verdict_raw, ("待判断", "#475569", "#F3F6FA"))
    confidence = _CONF_LABEL.get(str(feasibility.get("overall_confidence") or ""), str(feasibility.get("overall_confidence") or "unknown"))
    title = esc(str(proposal.get("title") or proposal.get("opportunity_id") or "DataForge 可行性分析报告"))
    opp = esc(str(proposal.get("opportunity_id") or ""))
    date_str = time.strftime("%Y-%m-%d", time.localtime())

    try:
        df_logo = _logo_data_uri(LOGO_PATH.read_bytes() if LOGO_PATH.exists() else None)
    except Exception:
        df_logo = ""
    brand_logo = _logo_data_uri(_brand_logo_bytes(proposal))

    def para(text: str) -> str:
        parts = [esc(p.strip()) for p in str(text or "").split("\n") if p.strip()]
        return "".join(f"<p class='lead'>{p}</p>" for p in parts)

    def dots(items: list, limit: int = 8) -> str:
        li = "".join(f"<li>{esc(str(x).strip())}</li>" for x in (items or []) if str(x).strip())
        return f"<ul class='dots'>{li}</ul>" if li else ""

    S: list[str] = []
    # ---- 执行摘要 ----
    S.append("<h1 class='sec'>执行摘要</h1>")
    S.append(f"<div class='cover-badges'><span class='badge' style='background:{v_bg};color:{v_color}'>{esc(v_label)}</span><span class='badge-sub'>整体置信度：{esc(confidence)}</span></div>")
    headline = str(proposal.get("executive_headline") or "").strip()
    points = [str(p).strip() for p in (proposal.get("executive_points") or []) if str(p).strip()]
    if headline:
        S.append(para(headline))
    if points:
        S.append(dots(points, 6))
    elif proposal.get("executive_summary"):
        S.append(para(proposal.get("executive_summary")))

    # ---- 可行性评分（分数条） ----
    dims = [d for d in (feasibility.get("dimensions") or []) if isinstance(d, dict)]
    if dims:
        S.append("<h1 class='sec'>可行性评分</h1><div class='scorecard'>")
        for d in dims:
            try:
                sc = float(d.get("score") or 0)
            except Exception:
                sc = 0
            pct = max(0, min(100, sc / 5 * 100))
            conf = str(d.get("confidence") or "speculative")
            conf_label = _CONF_LABEL.get(conf, conf)
            S.append(
                f"<div class='scorerow'><span class='nm'>{esc(str(d.get('name') or ''))}</span>"
                f"<span class='track'><span class='fill' style='width:{pct:.0f}%'></span></span>"
                f"<span class='sc'>{esc(str(d.get('score') if d.get('score') not in (None,'') else '-'))}/5</span>"
                f"<span class='cf'><span class='chip {esc(conf)}'>{esc(conf_label)}</span></span>"
                f"<span class='rt'>{esc(str(d.get('rationale') or ''))}</span></div>"
            )
        S.append("</div>")

    # ---- 背景与方法 ----
    background = str(proposal.get("background") or "").strip()
    methodology = [str(m).strip() for m in (proposal.get("methodology") or []) if str(m).strip()]
    if background or methodology:
        S.append("<h1 class='sec'>背景与方法</h1>")
        if background:
            S.append("<h2 class='sub'>项目背景</h2>" + para(background))
        if methodology:
            S.append("<h2 class='sub'>评估方法</h2>" + dots(methodology))

    # ---- 风险与缓解 ----
    risks = [r for r in (proposal.get("risk_register") or []) if isinstance(r, dict)]
    if risks:
        S.append("<h1 class='sec'>风险与缓解清单</h1><table class='grid'><thead><tr><th>缺口/风险</th><th>影响</th><th>缓解动作</th><th>级别</th></tr></thead><tbody>")
        for r in risks[:8]:
            sev = str(r.get("severity") or "").strip()
            sev_cls = {"high": "high", "medium": "medium", "low": "low", "高": "high", "中": "medium", "低": "low"}.get(sev.lower() if sev.isascii() else sev, "medium")
            S.append(f"<tr><td>{esc(str(r.get('gap') or ''))}</td><td>{esc(str(r.get('impact') or ''))}</td><td>{esc(str(r.get('mitigation') or ''))}</td><td><span class='sev {sev_cls}'>{esc(sev or '中')}</span></td></tr>")
        S.append("</tbody></table>")

    # ---- 路线图 ----
    roadmap = [p for p in (proposal.get("roadmap") or []) if isinstance(p, dict)]
    if roadmap:
        S.append("<h1 class='sec'>路线图</h1>")
        for ph in roadmap[:4]:
            steps = "".join(f"<li>{esc(str(s).strip())}</li>" for s in (ph.get("steps") or [])[:4] if str(s).strip())
            metric = esc(str(ph.get("metric") or ""))
            S.append(f"<div class='phase'><div class='ph'>{esc(str(ph.get('phase') or ''))}</div>" + (f"<ul class='dots'>{steps}</ul>" if steps else "") + (f"<div class='metric'>里程碑指标：{metric}</div>" if metric else "") + "</div>")

    # ---- 市场与外部参考 ----
    market = proposal.get("market") or {}
    findings = [f for f in (market.get("external_findings") or []) if isinstance(f, dict)]
    S.append("<h1 class='sec'>市场与外部参考</h1>")
    if findings:
        for f in findings[:5]:
            src = f.get("source_title") or f.get("source_url")
            S.append(f"<div class='mk'><span class='tag'>[市场推断]</span> {esc(str(f.get('claim') or ''))}" + (f"<div class='src'>来源：{esc(str(src))}</div>" if src else "") + "</div>")
    else:
        S.append(para(market.get("positioning_note") or "暂无外部市场补充。"))

    # ---- 证据附录 ----
    ev = [e for e in (proposal.get("evidence_appendix") or []) if isinstance(e, dict)]
    if ev:
        S.append("<h1 class='sec'>证据附录</h1><table class='grid'><thead><tr><th style='width:34px'>编号</th><th style='width:130px'>来源</th><th>摘录</th></tr></thead><tbody>")
        for i, e in enumerate(ev[:10], start=1):
            S.append(f"<tr><td>{i}</td><td>{esc(str(e.get('source_file') or e.get('ref') or 'unknown'))}</td><td>{esc(str(e.get('quote') or ''))}</td></tr>")
        S.append("</tbody></table>")

    cover = (
        "<div class='cover'>"
        "<div class='cover-band'>"
        f"<div class='cover-brand'>{('<img src=\"'+df_logo+'\"/>') if df_logo else ''}<span class='bt'>DataForge</span></div>"
        "<div class='cover-kicker'>Feasibility Analysis Report</div>"
        f"<div class='cover-title'>{title}</div>"
        "</div>"
        "<div class='cover-body'>"
        f"<div class='cover-badges'><span class='badge' style='background:{v_bg};color:{v_color}'>{esc(v_label)}</span><span class='badge-sub'>整体置信度：{esc(confidence)}</span></div>"
        "<p class='lead'>本报告由 DataForge 多智能体协作生成：语料分析、可行性评分、外部市场检索与独立审计校验，结论可溯源、不自欺地放大。</p>"
        f"<div class='cover-meta'><span>生成日期：{date_str}</span>{('<span>机会编号：'+opp+'</span>') if opp else ''}<span>引擎：Azure AI Foundry · gpt-5.1</span></div>"
        + (f"<img class='cover-brandlogo' src='{brand_logo}'/>" if brand_logo else "")
        + "</div></div>"
    )

    html_str = f"<!doctype html><html><head><meta charset='utf-8'><style>{_HTML_CSS}</style></head><body>{cover}{''.join(S)}</body></html>"
    return HTML(string=html_str, base_url=str(ROOT)).write_pdf()


def _rich_pdf(proposal: dict[str, Any], template: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.pdfmetrics import registerFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    registerFont(UnicodeCIDFont("STSong-Light"))
    font = "STSong-Light"
    latin_font = "Helvetica"
    blue = colors.HexColor("#2563EB")
    blue_dark = colors.HexColor("#0F3A75")
    blue_mid = colors.HexColor("#1D4ED8")
    text_gray = colors.HexColor("#374151")
    muted = colors.HexColor("#6B7280")
    line = colors.HexColor("#D7DEE8")
    pale = colors.HexColor("#F7FAFF")

    buffer = BytesIO()
    doc_title = str(proposal.get("title") or proposal.get("opportunity_id") or "DataForge 项目建议书")
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=2.1 * cm,
        bottomMargin=1.72 * cm,
        title=doc_title,
        author="DataForge",
    )

    base = getSampleStyleSheet()
    styles = {
        "cover_kicker": ParagraphStyle("cover-kicker", parent=base["BodyText"], fontName=font, fontSize=11, leading=15, textColor=blue_dark),
        "toc_title": ParagraphStyle("toc-title", parent=base["Title"], fontName=font, fontSize=24, leading=30, textColor=blue_dark, spaceAfter=22),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName=font,
            fontSize=18.5,
            leading=24,
            textColor=blue_dark,
            spaceBefore=18,
            spaceAfter=10,
            backColor=pale,
            borderColor=line,
            borderWidth=0.35,
            borderPadding=7,
        ),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=font, fontSize=13.2, leading=18, textColor=blue_mid, spaceBefore=12, spaceAfter=6),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName=font, fontSize=10.1, leading=17.2, textColor=text_gray, spaceAfter=7.5),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName=font, fontSize=8.7, leading=12.8, textColor=text_gray),
        "muted": ParagraphStyle("muted", parent=base["BodyText"], fontName=font, fontSize=8.3, leading=12, textColor=muted),
        "table_header": ParagraphStyle("table-header", parent=base["BodyText"], fontName=font, fontSize=8.3, leading=11, textColor=colors.white),
        "toc": ParagraphStyle("toc", parent=base["BodyText"], fontName=font, fontSize=10.4, leading=15.5, textColor=text_gray),
    }

    feasibility = proposal.get("feasibility") or {}
    doc_meta = proposal.get("doc_meta") or {}
    verdict_raw = str(feasibility.get("verdict") or "unknown")
    verdict_label, verdict_color, verdict_bg = _VERDICT.get(verdict_raw, ("待判断", "#666666", "#F3F6FA"))
    confidence = _CONF_LABEL.get(str(feasibility.get("overall_confidence") or ""), str(feasibility.get("overall_confidence") or "unknown"))

    story: list[Any] = [Spacer(1, 1), PageBreak()]
    _add_toc(story, styles, doc.width)
    story.append(PageBreak())

    _h1(story, styles, "执行摘要")
    _add_badge_table(story, styles, doc.width, verdict_label, verdict_color, verdict_bg, confidence)
    headline = str(proposal.get("executive_headline") or "").strip()
    points = [str(p).strip() for p in (proposal.get("executive_points") or []) if str(p).strip()]
    if headline:
        story.append(Paragraph(_xml(headline), styles["body"]))
    if points:
        for point in points[:6]:
            story.append(Paragraph(f'<font color="#0078D4">•</font> {_xml(point)}', styles["body"]))
    else:
        for para in _split_paragraphs(proposal.get("executive_summary") or "暂无执行摘要。", 6):
            story.append(Paragraph(_xml(para), styles["body"]))

    background = str(proposal.get("background") or "").strip()
    methodology = [str(item).strip() for item in (proposal.get("methodology") or []) if str(item).strip()]
    if background or methodology:
        _h1(story, styles, "背景与方法")
        if background:
            _h2(story, styles, "项目背景")
            story.append(Paragraph(_xml(background), styles["body"]))
        if methodology:
            _h2(story, styles, "评估方法")
            for item in methodology:
                story.append(Paragraph(f'<font color="#0078D4">•</font> {_xml(item)}', styles["body"]))

    dimensions = [item for item in (feasibility.get("dimensions") or []) if isinstance(item, dict)]
    if dimensions:
        _h1(story, styles, "可行性评分")
        rows = [[
            Paragraph("维度", styles["table_header"]),
            Paragraph("评分", styles["table_header"]),
            Paragraph("置信度", styles["table_header"]),
            Paragraph("理由与证据", styles["table_header"]),
        ]]
        for dim in dimensions:
            score = dim.get("score")
            rows.append([
                Paragraph(_xml(dim.get("name") or ""), styles["small"]),
                Paragraph(_xml(f"{score}/5" if score not in (None, "") else "-"), styles["small"]),
                Paragraph(_xml(_CONF_LABEL.get(str(dim.get("confidence")), str(dim.get("confidence") or ""))), styles["small"]),
                Paragraph(_xml(dim.get("rationale") or ""), styles["small"]),
            ])
        _add_table(story, rows, [2.6 * cm, 1.25 * cm, 2.05 * cm, doc.width - 5.9 * cm], blue)

    risk_register = [item for item in (proposal.get("risk_register") or []) if isinstance(item, dict)]
    if risk_register:
        _h1(story, styles, "风险与缓解清单")
        rows = [[
            Paragraph("缺口/风险", styles["table_header"]),
            Paragraph("影响", styles["table_header"]),
            Paragraph("缓解动作", styles["table_header"]),
            Paragraph("级别", styles["table_header"]),
        ]]
        for risk in risk_register[:8]:
            rows.append([
                Paragraph(_xml(risk.get("gap") or ""), styles["small"]),
                Paragraph(_xml(risk.get("impact") or ""), styles["small"]),
                Paragraph(_xml(risk.get("mitigation") or ""), styles["small"]),
                Paragraph(_xml(risk.get("severity") or ""), styles["small"]),
            ])
        first = 3.65 * cm
        last = 1.8 * cm
        _add_table(story, rows, [first, (doc.width - first - last) / 2, (doc.width - first - last) / 2, last], blue)

    roadmap = [item for item in (proposal.get("roadmap") or []) if isinstance(item, dict)]
    if roadmap:
        _h1(story, styles, "路线图")
        rows = [[Paragraph("阶段", styles["table_header"]), Paragraph("关键动作", styles["table_header"]), Paragraph("里程碑指标", styles["table_header"])]]
        for phase in roadmap[:4]:
            steps = "<br/>".join(_xml(step) for step in (phase.get("steps") or [])[:4])
            rows.append([
                Paragraph(_xml(phase.get("phase") or ""), styles["small"]),
                Paragraph(steps or "-", styles["small"]),
                Paragraph(_xml(phase.get("metric") or ""), styles["small"]),
            ])
        _add_table(story, rows, [3.2 * cm, doc.width - 7.0 * cm, 3.8 * cm], blue)

    market = proposal.get("market") or {}
    findings = [item for item in (market.get("external_findings") or []) if isinstance(item, dict)]
    _h1(story, styles, "市场与外部参考")
    if findings:
        for item in findings[:5]:
            story.append(Paragraph(f'<font color="#986F0B">[市场推断]</font> {_xml(item.get("claim") or "")}', styles["body"]))
            source = item.get("source_title") or item.get("source_url")
            if source:
                story.append(Paragraph(f"来源：{_xml(source)}", styles["muted"]))
    else:
        story.append(Paragraph(_xml(market.get("positioning_note") or "暂无外部市场补充。"), styles["body"]))

    opportunities = [item for item in (proposal.get("opportunities") or []) if isinstance(item, dict)]
    if opportunities:
        _h1(story, styles, "语料机会")
        for item in opportunities[:5]:
            story.append(Paragraph(f'<font color="#004E8C">{_xml(item.get("title") or "")}</font>：{_xml(item.get("description") or "")}', styles["body"]))

    evidence_appendix = [item for item in (proposal.get("evidence_appendix") or []) if isinstance(item, dict)]
    if evidence_appendix:
        _h1(story, styles, "证据附录")
        rows = [[Paragraph("编号", styles["table_header"]), Paragraph("来源", styles["table_header"]), Paragraph("摘录", styles["table_header"])]]
        for idx, item in enumerate(evidence_appendix[:10], start=1):
            rows.append([
                Paragraph(str(idx), styles["small"]),
                Paragraph(_xml(item.get("source_file") or item.get("ref") or "unknown"), styles["small"]),
                Paragraph(_xml(item.get("quote") or ""), styles["small"]),
            ])
        _add_table(story, rows, [1.1 * cm, 4.5 * cm, doc.width - 5.6 * cm], blue)

    def cover(canvas: Any, doc_: Any) -> None:
        _draw_cover(canvas, A4, font, latin_font, proposal, doc_title, verdict_label, verdict_color, verdict_bg, confidence, doc_meta)

    def decorate(canvas: Any, doc_: Any) -> None:
        _draw_page_frame(canvas, A4, font, latin_font, doc_title, doc_.page, blue, line, muted, proposal)

    doc.build(story, onFirstPage=cover, onLaterPages=decorate)
    return buffer.getvalue()


def _add_toc(story: list[Any], styles: dict[str, Any], width: float) -> None:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    story.append(Paragraph("目录", styles["toc_title"]))
    entries = [
        ("执行摘要", "3"),
        ("背景与方法", "4"),
        ("可行性评分", "5"),
        ("风险与缓解清单", "6"),
        ("路线图", "7"),
        ("市场与外部参考", "8"),
        ("证据附录", "9"),
    ]
    rows = []
    for label, page in entries:
        rows.append([
            Paragraph(_xml(label), styles["toc"]),
            Paragraph("." * 72, styles["toc"]),
            Paragraph(page, styles["toc"]),
        ])
    table = Table(rows, colWidths=[5.1 * 28.35, width - 5.95 * 28.35, 0.85 * 28.35])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#A6A6A6")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.18, colors.HexColor("#EEF2F7")),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(table)


def _h1(story: list[Any], styles: dict[str, Any], text: str) -> None:
    from reportlab.platypus import Paragraph

    story.append(Paragraph(_xml(text), styles["h1"]))


def _h2(story: list[Any], styles: dict[str, Any], text: str) -> None:
    from reportlab.platypus import Paragraph

    story.append(Paragraph(_xml(text), styles["h2"]))


def _add_badge_table(
    story: list[Any],
    styles: dict[str, Any],
    width: float,
    verdict: str,
    verdict_color: str,
    verdict_bg: str,
    confidence: str,
) -> None:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    para = Paragraph(
        f'<font color="{verdict_color}">可行性结论：{_xml(verdict)}</font>'
        f'<font color="#666666">　|　整体置信度：{_xml(confidence)}</font>',
        styles["body"],
    )
    table = Table([[para]], colWidths=[width])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(verdict_bg)),
        ("LINEBEFORE", (0, 0), (0, -1), 4, colors.HexColor(verdict_color)),
        ("BOX", (0, 0), (-1, -1), 0.35, colors.HexColor("#D6DCE5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))


def _add_table(story: list[Any], rows: list[list[Any]], col_widths: list[float], header_color: Any) -> None:
    from reportlab.lib import colors
    from reportlab.platypus import Spacer, Table, TableStyle

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_color),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("GRID", (0, 0), (-1, -1), 0.28, colors.HexColor("#D7DEE8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))


def _draw_cover(
    canvas: Any,
    page_size: tuple[float, float],
    font: str,
    latin_font: str,
    proposal: dict[str, Any],
    title: str,
    verdict_label: str,
    verdict_color: str,
    verdict_bg: str,
    confidence: str,
    doc_meta: dict[str, Any],
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    w, h = page_size
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#F8FAFC"))
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.roundRect(1.35 * cm, 1.28 * cm, w - 2.7 * cm, h - 2.55 * cm, 0.18 * cm, fill=1, stroke=0)
    _draw_brand_mark(canvas, 2.0 * cm, h - 1.7 * cm, 1.38 * cm, proposal)

    canvas.setFillColor(colors.HexColor("#0F3A75"))
    canvas.setFont(f"{latin_font}-Bold", 12)
    canvas.drawString(3.75 * cm, h - 2.05 * cm, "DataForge")
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.setFont(font, 8.5)
    canvas.drawString(3.75 * cm, h - 2.55 * cm, "数据商机化与可校准可行性分析")

    band_y = h - 8.45 * cm
    band_h = 2.05 * cm
    for i in range(96):
        ratio = i / 95
        r = 0.10 + (0.42 - 0.10) * ratio
        g = 0.39 + (0.83 - 0.39) * ratio
        b = 0.92 + (1.00 - 0.92) * ratio
        canvas.setFillColor(colors.Color(r, g, b))
        canvas.rect(1.35 * cm + i * (w - 2.7 * cm) / 96, band_y, (w - 2.7 * cm) / 96 + 1, band_h, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#DBEAFE"))
    canvas.circle(w - 3.2 * cm, band_y + band_h * 0.55, 1.28 * cm, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#EFF6FF"))
    canvas.circle(w - 5.4 * cm, band_y + band_h * 0.28, 0.52 * cm, fill=1, stroke=0)

    canvas.setFillColor(colors.HexColor("#111827"))
    title_y = h - 5.05 * cm
    for idx, line in enumerate(_wrap_canvas_text(canvas, title, font, 26, w - 4.0 * cm)[:3]):
        _draw_mixed_string(canvas, 2.0 * cm, title_y - idx * 0.9 * cm, line, font, f"{latin_font}-Bold", 26, max_width=w - 4.0 * cm)
    canvas.setFillColor(colors.white)
    canvas.setFont(font, 12)
    canvas.drawString(2.0 * cm, h - 7.5 * cm, "项目建议书")

    badge = f"结论：{verdict_label}"
    canvas.setFillColor(colors.HexColor(verdict_bg))
    canvas.roundRect(2.0 * cm, band_y - 1.35 * cm, 5.8 * cm, 0.8 * cm, 0.12 * cm, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor(verdict_color))
    canvas.setFont(font, 10.5)
    canvas.drawString(2.25 * cm, band_y - 1.08 * cm, badge)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.drawString(8.1 * cm, band_y - 1.08 * cm, f"置信度：{confidence}")

    date = str(doc_meta.get("generated_date") or time.strftime("%Y-%m-%d"))
    doc_id = str(doc_meta.get("doc_id") or proposal.get("opportunity_id") or "DF-PROPOSAL")
    canvas.setFont(font, 9)
    _draw_mixed_string(canvas, 2.0 * cm, 3.3 * cm, f"生成日期：{date}", font, latin_font, 9, max_width=w - 4.0 * cm)
    _draw_mixed_string(canvas, 2.0 * cm, 2.8 * cm, f"文档编号：{doc_id}", font, latin_font, 9, max_width=w - 4.0 * cm)
    _draw_mixed_string(canvas, 2.0 * cm, 2.3 * cm, f"版本：{doc_meta.get('version', 'v1')}", font, latin_font, 9, max_width=w - 4.0 * cm)
    canvas.setStrokeColor(colors.HexColor("#D7DEE8"))
    canvas.line(2.0 * cm, 1.75 * cm, w - 2.0 * cm, 1.75 * cm)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    _draw_mixed_string(canvas, 2.0 * cm, 1.3 * cm, "DataForge 多智能体系统生成 - 仅供内部决策参考", font, latin_font, 8, max_width=w - 4.0 * cm)
    canvas.restoreState()


def _draw_brand_mark(canvas: Any, x: float, top_y: float, size: float, proposal: dict[str, Any] | None = None) -> None:
    try:
        image = _brand_logo_reader(proposal or {})
        if image:
            canvas.drawImage(
                image,
                x,
                top_y - size,
                width=size,
                height=size,
                preserveAspectRatio=True,
                mask="auto",
            )
            return
    except Exception:
        pass
    _draw_logo(canvas, x, top_y - size * 0.1, size / 3.05)


def _brand_logo_reader(proposal: dict[str, Any]) -> Any:
    from reportlab.lib.utils import ImageReader

    for source in _brand_logo_sources(proposal):
        loaded = _load_logo_bytes(source)
        if loaded:
            return ImageReader(BytesIO(loaded))
    if LOGO_PATH.exists():
        return ImageReader(str(LOGO_PATH))
    return None


def _brand_logo_sources(proposal: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    for key in ("brand_logo_url", "logo_url"):
        value = str(proposal.get(key) or "").strip()
        if value:
            sources.append(value)
    images = [item for item in (proposal.get("reference_images") or []) if isinstance(item, dict)]
    logos = []
    others = []
    for item in images:
        role = str(item.get("role") or "").strip().lower()
        name = str(item.get("name") or item.get("filename") or item.get("source_file") or "").strip().lower()
        url = str(item.get("url") or item.get("artifact_url") or item.get("source_file") or item.get("local_path") or "").strip()
        if not url:
            continue
        if role == "logo" or "logo" in name or "brand" in name:
            logos.append(url)
        else:
            others.append(url)
    return _dedupe_sources([*sources, *logos, *others])


def _dedupe_sources(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _load_logo_bytes(source: str) -> bytes | None:
    value = str(source or "").strip()
    if not value:
        return None
    if value.startswith("/api/workspaces/"):
        parts = [unquote(part) for part in value.split("/") if part]
        if len(parts) >= 5 and parts[0] == "api" and parts[1] == "workspaces" and parts[3] == "reference-images":
            try:
                from ..workspace_store import get_reference_image_content
            except ImportError:
                from workspace_store import get_reference_image_content

            downloaded = get_reference_image_content(parts[2], parts[4])
            return _compact_logo_bytes(downloaded[0]) if downloaded else None
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        with urllib.request.urlopen(value, timeout=12) as response:
            return _compact_logo_bytes(response.read(2 * 1024 * 1024))
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
        return _compact_logo_bytes(path.read_bytes()) if path.exists() and path.is_file() else None
    path = Path(value)
    if path.exists() and path.is_file():
        return _compact_logo_bytes(path.read_bytes())
    return None


def _compact_logo_bytes(content: bytes | None, max_px: int = 384) -> bytes | None:
    if not content:
        return None
    try:
        from PIL import Image

        image = Image.open(BytesIO(content)).convert("RGBA")
        image.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        out = BytesIO()
        image.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return content


def _draw_logo(canvas: Any, x: float, y: float, unit: float) -> None:
    from reportlab.lib import colors

    canvas.saveState()
    blue = colors.HexColor("#0078D4")
    cyan = colors.HexColor("#50E6FF")
    dark = colors.HexColor("#004E8C")

    canvas.setLineJoin(1)
    canvas.setLineCap(1)
    canvas.setLineWidth(2.6)

    canvas.setStrokeColor(cyan)
    canvas.line(x + unit * 0.08, y - unit * 2.0, x + unit * 1.55, y - unit * 2.78)
    canvas.line(x + unit * 1.55, y - unit * 2.78, x + unit * 3.02, y - unit * 2.0)

    canvas.setStrokeColor(blue)
    canvas.line(x + unit * 0.2, y - unit * 1.05, x + unit * 1.55, y - unit * 1.76)
    canvas.line(x + unit * 1.55, y - unit * 1.76, x + unit * 2.9, y - unit * 1.05)

    canvas.setFillColor(blue)
    path = canvas.beginPath()
    path.moveTo(x + unit * 0.35, y - unit * 0.25)
    path.lineTo(x + unit * 1.55, y + unit * 0.38)
    path.lineTo(x + unit * 2.75, y - unit * 0.25)
    path.lineTo(x + unit * 1.55, y - unit * 0.9)
    path.close()
    canvas.drawPath(path, fill=1, stroke=0)

    canvas.setFillColor(dark)
    dot = unit * 0.12
    for dx, dy in ((1.08, -0.24), (1.55, 0.02), (2.02, -0.24)):
        canvas.circle(x + unit * dx, y + unit * dy, dot, fill=1, stroke=0)
    canvas.restoreState()


def _draw_page_frame(canvas: Any, page_size: tuple[float, float], font: str, latin_font: str, doc_name: str, page: int, blue: Any, line: Any, muted: Any, proposal: dict[str, Any]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.units import cm

    w, h = page_size
    canvas.saveState()
    canvas.setFillColor(blue)
    canvas.rect(0, h - 0.28 * cm, w, 0.28 * cm, fill=1, stroke=0)
    _draw_brand_mark(canvas, 1.75 * cm, h - 0.58 * cm, 0.36 * cm, proposal)
    canvas.setFillColor(colors.HexColor("#004E8C"))
    canvas.setFont(f"{latin_font}-Bold", 8)
    canvas.drawString(2.18 * cm, h - 0.95 * cm, "DataForge")
    header_x = 2.18 * cm + canvas.stringWidth("DataForge", f"{latin_font}-Bold", 8) + 3
    canvas.setFont(font, 8)
    canvas.drawString(header_x, h - 0.95 * cm, " 项目建议书")
    canvas.setStrokeColor(line)
    canvas.setLineWidth(0.45)
    canvas.line(1.75 * cm, 1.22 * cm, w - 1.75 * cm, 1.22 * cm)
    canvas.setFillColor(muted)
    _draw_mixed_string(canvas, 1.75 * cm, 0.86 * cm, _truncate(doc_name, 54), font, latin_font, 7.5, max_width=w - 4.0 * cm)
    canvas.setFont(latin_font, 7.5)
    canvas.drawRightString(w - 1.75 * cm, 0.86 * cm, f"{page}")
    canvas.restoreState()


def _draw_mixed_string(
    canvas: Any,
    x: float,
    y: float,
    text: str,
    cjk_font: str,
    latin_font: str,
    size: float,
    max_width: float | None = None,
) -> None:
    start_x = x
    chunk = ""
    chunk_font = ""

    def flush() -> None:
        nonlocal x, chunk, chunk_font
        if not chunk:
            return
        canvas.setFont(chunk_font, size)
        if max_width is not None and x + canvas.stringWidth(chunk, chunk_font, size) > start_x + max_width:
            chunk = ""
            return
        canvas.drawString(x, y, chunk)
        x += canvas.stringWidth(chunk, chunk_font, size)
        chunk = ""

    for char in str(text or ""):
        next_font = latin_font if ord(char) < 128 else cjk_font
        if chunk and next_font != chunk_font:
            flush()
        chunk_font = next_font
        chunk += char
    flush()


def _wrap_canvas_text(canvas: Any, text: str, font: str, size: float, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in str(text or ""):
        if canvas.stringWidth(current + char, font, size) <= max_width or not current:
            current += char
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def _split_paragraphs(text: Any, limit: int) -> list[str]:
    paragraphs = [item.strip() for item in str(text or "").splitlines() if item.strip()]
    if not paragraphs:
        paragraphs = [str(text or "").strip()]
    return [item[:1800] for item in paragraphs[:limit] if item]


def _xml(text: Any) -> str:
    cleaned = _EMOJI_RE.sub("", str(text or ""))
    cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*(?!\*)", "", cleaned)
    cleaned = cleaned.replace("`", "").replace("__", "")
    return escape(cleaned).replace("\n", "<br/>")


def _safe_name(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "")).strip("-")
    return text[:90] or "proposal"


def _truncate(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "..."
