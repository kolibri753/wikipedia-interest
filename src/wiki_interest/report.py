"""PDF report from analysis.json (reportlab, pure Python).

Design rules, and why:
- Every number, verdict and chart comes from analysis.json (deterministic).
  The model's prose goes into a clearly labelled "Analyst notes" section, so a
  reader can always tell measurement from interpretation.
- Page 1 is the shareable one-pager: parameters, per-language table, the
  cross-language share chart, the caveats that matter, the analyst notes and a
  fixed limitations footer. Page 2 (optional) shows the per-language monthly
  charts for readers who want the shape.
- Fonts: DejaVu Sans from matplotlib's package data. reportlab's built-in
  fonts have no Czech diacritics or Cyrillic and would print black boxes.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LIMITATIONS = (
    "Wikipedia pageviews measure attention on Wikipedia, not demand or willingness to pay. Each language is represented "
    "by one article (plus its redirect titles); articles may be broader or narrower across editions and may be missing. "
    "Human-labelled pageviews fell across Wikipedia editions during this window and Wikimedia revised its bot "
    "classification in 2025 (both measured here); the Wikimedia Foundation attributes part of the wider decline to AI "
    "answer engines, which this data cannot confirm. The \u201crelative\u201d figures (share of the whole edition) partly "
    "control for edition-wide shifts. Spike dates say when, not why. Method details: references/methodology.md in the skill."
)


def _register_fonts() -> tuple[str, str]:
    """Return (regular, bold) font names, preferring DejaVu Sans shipped with matplotlib."""
    try:
        import matplotlib

        base = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
        pdfmetrics.registerFont(TTFont("DejaVu", str(base / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(base / "DejaVuSans-Bold.ttf")))
        return "DejaVu", "DejaVu-Bold"
    except Exception:  # pragma: no cover - fallback keeps the report producible
        return "Helvetica", "Helvetica-Bold"


def _styles():
    reg, bold = _register_fonts()
    s = {
        "title": ParagraphStyle("t", fontName=bold, fontSize=15, leading=18, spaceAfter=2),
        "sub": ParagraphStyle(
            "s", fontName=reg, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"), spaceAfter=6
        ),
        "h": ParagraphStyle("h", fontName=bold, fontSize=10, leading=12, spaceBefore=6, spaceAfter=3),
        "body": ParagraphStyle("b", fontName=reg, fontSize=8.5, leading=11, alignment=TA_LEFT),
        "small": ParagraphStyle("sm", fontName=reg, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444")),
        "cell": ParagraphStyle("c", fontName=reg, fontSize=7.5, leading=9),
        "cellb": ParagraphStyle("cb", fontName=bold, fontSize=7.5, leading=9),
        "bullet": ParagraphStyle("bl", fontName=reg, fontSize=8.5, leading=11, leftIndent=10, bulletIndent=0),
    }
    return s


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:+.0%}"


def _num(v) -> str:
    return "n/a" if v is None else f"{v:,.0f}"


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _table(full: dict, st) -> Table:
    head = [
        "Lang",
        "Article analysed",
        "Views/mo (median)",
        "YoY",
        "Trend/yr",
        "Months up",
        "Per M edition",
        "Relative",
        "Verdict",
        "Trust",
    ]
    rows = [[Paragraph(h, st["cellb"]) for h in head]]
    # Highest share of edition attention first: the comparable measure decides the order.
    ordered = sorted(
        full["languages"].items(), key=lambda kv: -((kv[1].get("relative") or {}).get("median_per_million") or 0)
    )
    for lang, d in ordered:
        rel = d.get("relative") or {}
        tt = d.get("trend_test") or {}
        months_up = f"{tt.get('months_up')}/{tt.get('pairs')}" if tt.get("pairs") else "n/a"
        rows.append(
            [
                Paragraph(lang, st["cellb"]),
                Paragraph(_esc(d["article"]), st["cell"]),
                Paragraph(_num(d["volume"]["median_monthly"]), st["cell"]),
                Paragraph(_pct(d["growth"].get("yoy")), st["cell"]),
                Paragraph(_pct(d["growth"].get("annualized_trend")), st["cell"]),
                Paragraph(months_up, st["cell"]),
                Paragraph(
                    "n/a" if rel.get("median_per_million") is None else f"{rel['median_per_million']:.1f}", st["cell"]
                ),
                Paragraph(f"{rel.get('verdict', 'n/a')}", st["cell"]),
                Paragraph(f"{d['verdict']} ({d['magnitude'] or 'n/a'})", st["cell"]),
                Paragraph(f"{d['trust']['score']} {d['trust']['label']}", st["cellb"]),
            ]
        )
    for lang, sugg in (full.get("missing") or {}).items():
        rows.append(
            [Paragraph(lang, st["cellb"]), Paragraph("no article for this topic", st["cell"])]
            + [Paragraph("—", st["cell"])] * 8
        )
    t = Table(
        rows,
        colWidths=[1.0 * cm, 3.6 * cm, 1.8 * cm, 1.2 * cm, 1.5 * cm, 1.5 * cm, 1.6 * cm, 1.6 * cm, 2.3 * cm, 1.5 * cm],
        repeatRows=1,
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#888888")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return t


def _caveats(full: dict) -> list[str]:
    out = []
    for lang, d in full["languages"].items():
        t = d["trust"]
        for r in t.get("reasons", [])[:2]:
            out.append(f"<b>{lang}</b>: {_esc(r)}")
        for n in t.get("notes", [])[:1]:
            out.append(f"<b>{lang}</b>: {_esc(n)}")
    for w in full.get("warnings", []):
        out.append(_esc(w))
    return out[:7]


def _notes_flowables(md: str, st) -> list:
    """Very small Markdown subset: headings become bold lines, '-'/'*' become bullets, blank lines separate paragraphs."""
    flow = []
    para: list[str] = []

    def flush():
        if para:
            flow.append(Paragraph(_inline(" ".join(para)), st["body"]))
            para.clear()

    lines = md.splitlines()
    # a leading "Analyst notes" heading duplicates the section title the report adds
    while lines and re.fullmatch(r"#*\s*analyst notes:?\s*", lines[0].strip(), re.I):
        lines.pop(0)
    for line in lines:
        s = line.strip()
        if not s:
            flush()
            continue
        m = re.match(r"^(\d+)[.)]\s+(.*)", s)
        if s.startswith("#"):
            flush()
            flow.append(Paragraph(f"<b>{_inline(s.lstrip('#').strip())}</b>", st["body"]))
        elif s[:2] in ("- ", "* ", "• "):
            flush()
            flow.append(Paragraph(_inline(s[2:]), st["bullet"], bulletText="\u2022"))
        elif m:
            flush()
            flow.append(Paragraph(_inline(m.group(2)), st["bullet"], bulletText=m.group(1) + "."))
        else:
            para.append(s)
    flush()
    return flow


def _inline(s: str) -> str:
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", s)
    return s


def build_report(
    full: dict,
    out_path: Path,
    notes_md: str | None = None,
    title: str | None = None,
    charts_dir: Path | None = None,
    appendix: bool = True,
) -> Path:
    st = _styles()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.3 * cm,
        bottomMargin=1.2 * cm,
        title=title or f"Wikipedia interest: {full.get('label') or full['topic']}",
        author="wikipedia-interest skill",
    )
    langs = ", ".join(full["languages"].keys())
    w = full["window"]
    concept = full.get("label") or full["topic"]
    first_article = next((d["article"] for d in full["languages"].values()), None)
    if first_article and first_article.lower() != concept.lower():
        concept = (
            f"{concept} ({first_article})"  # Wikidata labels can be terse ("English"); the article title disambiguates
        )
    story = [
        Paragraph(_esc(title or f"Interest in \u201c{concept}\u201d on Wikipedia \u2014 {langs}"), st["title"]),
        Paragraph(
            _esc(
                f"Wikimedia pageviews, {w['start']} to {w['end']} ({w['months']} complete months), agent={full['agent']}, "
                f"access={full['access']}, redirect titles {'included' if full.get('redirects_included') else 'excluded'}. "
                f"Concept: {full.get('label') or full['topic']} ({full.get('qid') or 'no Wikidata item'}) \u2014 {full.get('description') or ''}. "
                f"Generated {date.today().isoformat()}."
            ),
            st["sub"],
        ),
        _table(full, st),
        Spacer(1, 4),
        Paragraph(
            "Rows are ordered by share of edition attention, highest first. YoY = last 12 months vs the 12 before. Trend/yr = robust, "
            "seasonality-controlled annualized change with spike days removed. Months up = months above the same month a year earlier. "
            "Per M edition = median article views per million views of that whole edition (the comparable measure). "
            "Relative = verdict on that share.",
            st["small"],
        ),
    ]
    charts_dir = charts_dir or out_path.parent
    share = charts_dir / "chart_share.png"
    if share.exists():
        story += [Spacer(1, 4), Image(str(share), width=18 * cm, height=6.5 * cm)]
    cav = _caveats(full)
    if cav:
        story += [Paragraph("What limits confidence", st["h"])]
        story += [Paragraph(c, st["bullet"], bulletText="\u2022") for c in cav]
    if notes_md and notes_md.strip():
        cmp_ = full.get("comparison") or {}
        if cmp_.get("statement"):
            story += [Paragraph("Comparison (computed)", st["h"]), Paragraph(_esc(cmp_["statement"]), st["body"])]
        story += [Paragraph("Analyst notes", st["h"])] + _notes_flowables(notes_md, st)
    elif (full.get("comparison") or {}).get("statement"):
        story += [
            Paragraph("Comparison (computed)", st["h"]),
            Paragraph(_esc(full["comparison"]["statement"]), st["body"]),
        ]
    story += [Spacer(1, 6), Paragraph("<b>Limitations.</b> " + _esc(LIMITATIONS), st["small"])]
    monthly = charts_dir / "chart_monthly.png"
    if appendix and monthly.exists():
        n = max(1, len(full["languages"]))
        h = min(24 * cm, 18 * cm * (2.6 * n + 0.8) / 10)
        story += [
            PageBreak(),
            Paragraph(
                "Appendix: monthly human pageviews per language (bars) and the spike-removed series (line)", st["h"]
            ),
            Image(str(monthly), width=18 * cm, height=h),
        ]
    doc.build(story)
    return out_path
