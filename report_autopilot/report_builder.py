"""
report_builder.py
------------------
Assembles the final client-facing PDF: cover header, the LLM-written
narrative, KPI summary table, and the two charts from charts.py.
Uses reportlab's Platypus layer (flowables) rather than raw canvas
drawing, since a multi-section report needs automatic page-breaking,
which Platypus handles and manual canvas positioning does not.
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable,
)

from report_autopilot.metrics import PeriodComparison, pct_change

DEFAULT_BRAND_COLOR = "#f27038"
DARK = colors.HexColor("#2b2b2b")
GREY = colors.HexColor("#6b6b6b")


def _styles(brand_color):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", fontSize=20, leading=24, textColor=DARK,
        spaceAfter=4, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="ReportSubtitle", fontSize=11, leading=14, textColor=GREY,
        spaceAfter=18,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", fontSize=13, leading=16, textColor=brand_color,
        spaceBefore=14, spaceAfter=8, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="Narrative", fontSize=10.5, leading=15.5, textColor=DARK,
        spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        name="Footer", fontSize=8, leading=10, textColor=GREY,
    ))
    return styles


def _kpi_table(comparison: PeriodComparison, styles, brand_color):
    cur, prev = comparison.current_totals, comparison.previous_totals

    def fmt_change(old, new, is_currency=True):
        change = pct_change(old, new)
        arrow = "" if change is None else ("+" if change >= 0 else "")
        change_str = "n/a" if change is None else f"{arrow}{change:.1f}%"
        return change_str

    rows = [
        ["Metric", "This Period", "Previous Period", "Change"],
        ["Spend", f"{cur.cost:,.0f}", f"{prev.cost:,.0f}", fmt_change(prev.cost, cur.cost)],
        ["Revenue", f"{cur.revenue:,.0f}", f"{prev.revenue:,.0f}", fmt_change(prev.revenue, cur.revenue)],
        ["Conversions", f"{cur.conversions:,.0f}", f"{prev.conversions:,.0f}", fmt_change(prev.conversions, cur.conversions)],
        ["ROAS", f"{cur.roas:.2f}x", f"{prev.roas:.2f}x", fmt_change(prev.roas, cur.roas)],
    ]
    table = Table(rows, colWidths=[1.6 * inch, 1.6 * inch, 1.6 * inch, 1.2 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(brand_color)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _channel_table(comparison: PeriodComparison):
    rows = [["Channel", "Revenue", "Spend", "ROAS", "CPA", "Conversions"]]
    for c in sorted(comparison.current_by_channel, key=lambda c: c.revenue, reverse=True):
        rows.append([
            c.channel, f"{c.revenue:,.0f}", f"{c.cost:,.0f}",
            f"{c.roas:.2f}x", f"{c.cpa:,.0f}" if c.cpa else "n/a", f"{c.conversions:,.0f}",
        ])
    table = Table(rows, colWidths=[1.5 * inch, 1.0 * inch, 1.0 * inch, 0.8 * inch, 0.8 * inch, 1.0 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2b2b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def build_report(
    output_path: str,
    client_name: str,
    comparison: PeriodComparison,
    narrative: str,
    trend_chart_path: str,
    channel_chart_path: str,
    agency_name: str = "Single Grain",
    brand_color: str = DEFAULT_BRAND_COLOR,
):
    """Assembles the full PDF and writes it to output_path."""
    brand_color_obj = colors.HexColor(brand_color)
    styles = _styles(brand_color_obj)
    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
    )

    story = []

    story.append(Paragraph(f"{client_name} — Weekly Performance Report", styles["ReportTitle"]))
    story.append(Paragraph(
        f"{comparison.current_start.strftime('%b %d, %Y')} – "
        f"{comparison.current_end.strftime('%b %d, %Y')} &nbsp;|&nbsp; Prepared by {agency_name}",
        styles["ReportSubtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#eeeeee")))

    story.append(Paragraph("Summary", styles["SectionHeading"]))
    for para in narrative.split("\n\n"):
        if para.strip():
            story.append(Paragraph(para.strip(), styles["Narrative"]))

    story.append(Paragraph("Key Metrics", styles["SectionHeading"]))
    story.append(_kpi_table(comparison, styles, brand_color))
    story.append(Spacer(1, 14))

    story.append(Paragraph("Trend", styles["SectionHeading"]))
    story.append(Image(trend_chart_path, width=6.2 * inch, height=2.66 * inch))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Performance by Channel", styles["SectionHeading"]))
    story.append(Image(channel_chart_path, width=6.2 * inch, height=2.66 * inch))
    story.append(Spacer(1, 10))
    story.append(_channel_table(comparison))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#eeeeee")))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "This report was generated automatically by Report Autopilot. "
        "All figures are computed directly from the source data export; "
        "narrative commentary is AI-assisted and reviewed for accuracy.",
        styles["Footer"],
    ))

    doc.build(story)
    return output_path
