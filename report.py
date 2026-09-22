"""Generador de informe PDF del tablero — estilo del Streamlit viejo.

Deliberadamente usa solo primitivas nativas de ReportLab (Drawing/Rect/
Table), no imágenes exportadas de Plotly vía kaleido: eso fue lo que hacía
lenta/frágil la versión anterior de la descarga. Un solo cómputo síncrono
por click, sin generación en dos pasos.
"""
from __future__ import annotations

import html
from io import BytesIO

import pandas as pd
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors as rl_colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

WHITE = rl_colors.white


def _safe(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return html.escape(str(value), quote=False)


def _styles(navy: str, muted: str, ink: str, blue: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    NAVY, MUTED, INK, BLUE = HexColor(navy), HexColor(muted), HexColor(ink), HexColor(blue)
    return {
        "title": ParagraphStyle("rpt-title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=NAVY, spaceAfter=5),
        "subtitle": ParagraphStyle("rpt-subtitle", parent=base["BodyText"], fontName="Helvetica", fontSize=10.5, leading=14, textColor=MUTED, spaceAfter=8),
        "h1": ParagraphStyle("rpt-h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=NAVY, spaceBefore=2, spaceAfter=7),
        "h2": ParagraphStyle("rpt-h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=NAVY, spaceAfter=5),
        "body": ParagraphStyle("rpt-body", parent=base["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=INK),
        "caption": ParagraphStyle("rpt-caption", parent=base["BodyText"], fontName="Helvetica", fontSize=7.3, leading=9.2, textColor=MUTED, spaceBefore=3, spaceAfter=3),
        "small_center": ParagraphStyle("rpt-small-center", parent=base["BodyText"], fontName="Helvetica", fontSize=6.6, leading=8, textColor=INK, alignment=TA_CENTER),
        "kpi_value": ParagraphStyle("rpt-kpi-value", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=17, leading=18, textColor=BLUE, alignment=TA_LEFT),
        "kpi_label": ParagraphStyle("rpt-kpi-label", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=6.3, leading=8, textColor=MUTED, alignment=TA_LEFT),
    }


def _page_footer_factory(footer_left: str, border: str, muted: str):
    BORDER, MUTED = HexColor(border), HexColor(muted)

    def _footer(canvas, doc) -> None:
        canvas.saveState()
        width, _ = landscape(A4)
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(1.2 * cm, 0.85 * cm, width - 1.2 * cm, 0.85 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(1.2 * cm, 0.52 * cm, footer_left)
        canvas.drawRightString(width - 1.2 * cm, 0.52 * cm, f"Página {doc.page}")
        canvas.restoreState()

    return _footer


def panel(title: str, content: object, styles: dict, border: str, *, width_cm: float | None = None) -> Table:
    BORDER = HexColor(border)
    cell = [Paragraph(_safe(title), styles["h2"]), content]
    table = Table([[cell]], colWidths=[width_cm * cm] if width_cm else None, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WHITE),
        ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def row_of_panels(items: list[Table], gap_cm: float = 0.3) -> Table:
    row = Table([items], hAlign="LEFT")
    row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), gap_cm * cm)]))
    return row


def kpi_cards(items: list[tuple[str, str]], styles: dict, border: str) -> Table:
    BORDER = HexColor(border)
    row = []
    for label, value in items:
        row.append([
            Paragraph(_safe(value), styles["kpi_value"]),
            Spacer(1, 1),
            Paragraph(_safe(label).upper(), styles["kpi_label"]),
        ])
    n = len(items)
    col_w = (25.6 * cm) / max(1, n)
    table = Table([row], colWidths=[col_w] * n, rowHeights=[1.5 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WHITE),
        ("BOX", (0, 0), (-1, -1), 0.65, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def bar_chart(labels: list[str], values: list[float], color: str, muted: str, border: str, grid: str,
              *, height: float = 185, width: float = 350, label_width: float = 130) -> Drawing:
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    COLOR, MUTED, BORDER, GRID = HexColor(color), HexColor(muted), HexColor(border), HexColor(grid)
    height = max(height, 70 + 15 * len(labels))
    drawing = Drawing(width, height)
    if not values:
        drawing.add(String(10, height / 2, "Sin datos", fontName="Helvetica", fontSize=8, fillColor=MUTED))
        return drawing
    chart = HorizontalBarChart()
    chart.x = label_width
    chart.y = 18
    chart.width = max(100, width - label_width - 22)
    chart.height = max(60, height - 30)
    chart.data = [values]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.fillColor = MUTED
    chart.categoryAxis.labels.boxAnchor = "e"
    chart.categoryAxis.labels.dx = -4
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 6.2
    chart.valueAxis.labels.fillColor = MUTED
    chart.valueAxis.strokeColor = BORDER
    chart.valueAxis.gridStrokeColor = GRID
    chart.valueAxis.visibleGrid = 1
    chart.bars[0].fillColor = COLOR
    chart.bars[0].strokeColor = COLOR
    chart.barSpacing = 4
    drawing.add(chart)
    max_value = max(values) if values else 0
    if max_value > 0:
        for idx, value in enumerate(values):
            y = chart.y + chart.height * (idx + 0.5) / len(values)
            x = chart.x + chart.width * (value / max_value) + 4
            drawing.add(String(x, y - 2.5, f"{value:,.0f}", fontName="Helvetica", fontSize=6.5, fillColor=HexColor("#20252B")))
    return drawing


def composition_bar(labels: list[str], values: list[float], palette: list[str], muted: str,
                     *, width: float = 350, height: float = 115) -> Drawing:
    MUTED = HexColor(muted)
    pairs = [(l, v) for l, v in zip(labels, values) if v > 0]
    drawing = Drawing(width, height)
    total = sum(v for _, v in pairs)
    if total <= 0:
        drawing.add(String(10, height / 2, "Sin datos", fontName="Helvetica", fontSize=8, fillColor=MUTED))
        return drawing
    left, top, bar_w, bar_h = 10, height - 30, width - 20, 24
    cursor = left
    for idx, (label, value) in enumerate(pairs):
        share = value / total
        seg_w = bar_w * share
        color = HexColor(palette[idx % len(palette)])
        drawing.add(Rect(cursor, top, seg_w, bar_h, fillColor=color, strokeColor=WHITE, strokeWidth=0.4))
        if seg_w >= 28:
            drawing.add(String(cursor + seg_w / 2, top + 8, f"{share * 100:.0f}%", textAnchor="middle", fontName="Helvetica-Bold", fontSize=7, fillColor=WHITE))
        cursor += seg_w
    legend_x, legend_y = 10, top - 16
    for idx, (label, value) in enumerate(pairs):
        color = HexColor(palette[idx % len(palette)])
        if legend_x > width - 110:
            legend_x, legend_y = 10, legend_y - 15
        drawing.add(Rect(legend_x, legend_y, 7, 7, fillColor=color, strokeColor=color))
        drawing.add(String(legend_x + 10, legend_y - 0.5, f"{label} ({value:,.0f})", fontName="Helvetica", fontSize=6.4, fillColor=MUTED))
        legend_x += min(160, 30 + 4.3 * len(label))
    return drawing


def build_report(*, title: str, subtitle: str, scope_text: str, as_of_text: str,
                  kpis: list[tuple[str, str]], sections: list[dict], palette: dict[str, str],
                  footer_left: str = "Clúster Salud · Venezuela") -> bytes:
    """sections: [{"title": str, "rows": [[panel, ...], ...], "caption": str | None}, ...]"""
    buffer = BytesIO()
    styles = _styles(palette["navy"], palette["muted"], palette["ink"], palette["blue"])
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.05 * cm, bottomMargin=1.15 * cm,
        title=title, author="Clúster Salud · Venezuela", subject=subtitle,
    )
    story: list = [
        Paragraph(_safe(title), styles["title"]),
        Paragraph(f"{_safe(subtitle)} &nbsp; | &nbsp; {_safe(as_of_text)}", styles["subtitle"]),
        Paragraph(_safe(scope_text), styles["caption"]),
        Spacer(1, 6),
        kpi_cards(kpis, styles, palette["border"]),
        Spacer(1, 10),
    ]
    for section_index, section in enumerate(sections):
        # Cada sección empieza en una página nueva y se mantiene junta para
        # evitar recortes. Así la descarga conserva todas las secciones del
        # tablero y cada página funciona como una lámina independiente.
        section_flow: list = [Paragraph(_safe(section["title"]), styles["h1"]), Spacer(1, 2)]
        for row in section["rows"]:
            # row: list of (panel_title, content_drawing_or_table, width_cm)
            panels = [panel(t, content, styles, palette["border"], width_cm=w) for t, content, w in row]
            section_flow.append(row_of_panels(panels) if len(panels) > 1 else panels[0])
            section_flow.append(Spacer(1, 8))
        if section.get("caption"):
            section_flow.append(Paragraph(_safe(section["caption"]), styles["caption"]))
        section_flow.append(Spacer(1, 10))
        if section_index > 0:
            story.append(PageBreak())
        story.append(KeepTogether(section_flow))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Simulacro metodológico. Generado el {as_of_text}.", styles["caption"]))
    doc.build(story, onFirstPage=_page_footer_factory(footer_left, palette["border"], palette["muted"]),
               onLaterPages=_page_footer_factory(footer_left, palette["border"], palette["muted"]))
    return buffer.getvalue()
