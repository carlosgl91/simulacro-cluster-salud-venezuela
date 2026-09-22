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
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors as rl_colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image as RLImage,
)
from reportlab.platypus import (
    KeepTogether,
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


def _iter_rings(geometry: dict | None):
    """Anillos exteriores de un Polygon/MultiPolygon GeoJSON (sin huecos)."""
    if not geometry:
        return
    gtype = geometry.get("type")
    if gtype == "Polygon":
        coords = geometry.get("coordinates") or []
        if coords:
            yield coords[0]
    elif gtype == "MultiPolygon":
        for polygon in geometry.get("coordinates") or []:
            if polygon:
                yield polygon[0]


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    a = tuple(int(hex_a[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(hex_b[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02X}{:02X}{:02X}".format(*(round(a[i] + (b[i] - a[i]) * t) for i in range(3)))


def _map_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    """Fuente TTF escalable para los textos dibujados con PIL.

    Se prueban primero las fuentes del sistema (DejaVu en Linux, que es lo
    que corre en Streamlit Community Cloud; Segoe UI/Arial en Windows, para
    el desarrollo local) y, si no hay ninguna, se usa la fuente escalable
    que Pillow trae incorporada — nunca el bitmap diminuto de
    `load_default()` sin tamaño.
    """
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1: load_default no acepta tamaño
        return ImageFont.load_default()


def _largest_ring(rings: list) -> list | None:
    return max(rings, key=len) if rings else None


def choropleth_image(
    features: list[dict],
    values: dict[str, float],
    *,
    key_prop: str = "join_key",
    low_color: str,
    high_color: str,
    context_color: str,
    border_color: str,
    label_color: str,
    water_color: str = "#FFFFFF",
    value_caption: str = "",
    width_px: int = 1000,
    height_px: int = 470,
    scale: int = 3,
    width_cm: float = 25.0,
    height_cm: float | None = None,
) -> RLImage:
    """Mapa coroplético por estado (PIL puro, sin geopandas/shapely).

    Cada estado con dato se rellena con un color de la rampa
    low_color → high_color según su valor, y lleva una burbuja con la cifra
    y el nombre del estado; los estados sin dato quedan en `context_color`
    como contexto geográfico. El encuadre se calcula sobre los estados CON
    dato (más un margen), no sobre el país entero: si la respuesta está
    concentrada en cuatro estados, el mapa hace zoom ahí en vez de dejar
    Venezuela completa casi vacía.

    values: {nombre de estado: valor}. La comparación con el GeoJSON es
        tolerante a mayúsculas/espacios, pero no a sinónimos.
    """
    w, h = width_px * scale, height_px * scale
    img = PILImage.new("RGB", (w, h), water_color)
    draw = ImageDraw.Draw(img)
    height_cm = height_cm or (width_cm * height_px / width_px)

    def _norm(value: object) -> str:
        return str(value or "").strip().casefold()

    lookup = {_norm(k): float(v) for k, v in values.items() if v is not None}
    geometry = []
    for feature in features:
        rings = list(_iter_rings(feature.get("geometry")))
        if not rings:
            continue
        key = _norm((feature.get("properties") or {}).get(key_prop))
        points = [pt for ring in rings for pt in ring]
        bounds = (min(p[0] for p in points), min(p[1] for p in points),
                  max(p[0] for p in points), max(p[1] for p in points))
        geometry.append((key, rings, bounds))

    focus = [g for g in geometry if g[0] in lookup] or geometry
    if not focus:
        draw.text((20, h // 2), "Sin datos geográficos para este filtro", fill=border_color, font=_map_font(14 * scale))
        img = img.resize((width_px, height_px), PILImage.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return RLImage(buf, width=width_cm * cm, height=height_cm * cm)

    min_lon = min(g[2][0] for g in focus); min_lat = min(g[2][1] for g in focus)
    max_lon = max(g[2][2] for g in focus); max_lat = max(g[2][3] for g in focus)
    pad_lon = max((max_lon - min_lon) * 0.14, 0.25)
    # Margen vertical mayor que el horizontal: deja aire para las etiquetas
    # de nombre que van debajo de cada burbuja.
    pad_lat = max((max_lat - min_lat) * 0.30, 0.30)
    min_lon, max_lon = min_lon - pad_lon, max_lon + pad_lon
    min_lat, max_lat = min_lat - pad_lat, max_lat + pad_lat

    margin = 14 * scale
    span_lon = max(max_lon - min_lon, 1e-6)
    span_lat = max(max_lat - min_lat, 1e-6)
    proj_scale = min((w - 2 * margin) / span_lon, (h - 2 * margin) / span_lat)
    off_x = margin + (w - 2 * margin - span_lon * proj_scale) / 2
    off_y = margin + (h - 2 * margin - span_lat * proj_scale) / 2

    def project(lon: float, lat: float) -> tuple[float, float]:
        return off_x + (lon - min_lon) * proj_scale, off_y + (max_lat - lat) * proj_scale

    visible = [g for g in geometry
               if not (g[2][2] < min_lon or g[2][0] > max_lon or g[2][3] < min_lat or g[2][1] > max_lat)]
    present = [lookup[g[0]] for g in visible if g[0] in lookup]
    v_min, v_max = (min(present), max(present)) if present else (0.0, 0.0)
    v_span = (v_max - v_min) or 1.0

    def ramp(value: float) -> str:
        # La rampa arranca en 0.25 para que el estado con menos valor no
        # quede casi blanco y se confunda con los estados sin dato.
        return _mix(low_color, high_color, 0.25 + 0.75 * ((value - v_min) / v_span))

    for key, rings, _bounds in visible:
        fill = ramp(lookup[key]) if key in lookup else context_color
        for ring in rings:
            pixels = [project(lon, lat) for lon, lat in ring]
            if len(pixels) >= 3:
                draw.polygon(pixels, fill=fill, outline=water_color, width=max(1, scale))

    value_font = _map_font(13 * scale, bold=True)
    name_font = _map_font(10 * scale, bold=True)
    names = {_norm((f.get("properties") or {}).get(key_prop)): str((f.get("properties") or {}).get(key_prop))
             for f in features}
    gap = 4 * scale
    labels = []
    for key, rings, bounds in visible:
        if key not in lookup:
            continue
        ring = _largest_ring(rings)
        cx, cy = project((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        if ring:
            xs = [pt[0] for pt in ring]; ys = [pt[1] for pt in ring]
            cx, cy = project(sum(xs) / len(xs), sum(ys) / len(ys))
        value = lookup[key]
        name = names.get(key, key)
        name_box = draw.textbbox((0, 0), name, font=name_font)
        labels.append({
            "cx": cx, "cy": cy, "ox": cx, "oy": cy,
            "r": (13 + 7 * ((value - v_min) / v_span)) * scale,
            "text": f"{value:,.0f}".replace(",", "."),
            "name": name,
            "nw": name_box[2] - name_box[0] + 6 * scale,
            "nh": name_box[3] - name_box[1] + 6 * scale,
            "value": value,
        })

    def _footprint(item: dict) -> tuple[float, float, float, float]:
        half_w = max(item["r"], item["nw"] / 2)
        return (item["cx"] - half_w, item["cy"] - item["r"],
                item["cx"] + half_w, item["cy"] + item["r"] + gap + item["nh"])

    # Los estados chicos y contiguos del norte (La Guaira, Distrito Capital,
    # Miranda) quedan a pocos píxeles entre sí y sus burbujas/nombres se
    # pisaban. En vez de correcciones a mano por estado, se separan de forma
    # iterativa: mientras dos etiquetas se solapen se empujan en la dirección
    # de menor traslape, y después cada una se une con una línea guía a su
    # posición real en el mapa.
    for _ in range(80):
        collided = False
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                a, b = labels[i], labels[j]
                ax0, ay0, ax1, ay1 = _footprint(a)
                bx0, by0, bx1, by1 = _footprint(b)
                over_x = min(ax1, bx1) - max(ax0, bx0)
                over_y = min(ay1, by1) - max(ay0, by0)
                if over_x <= 0 or over_y <= 0:
                    continue
                collided = True
                if over_x < over_y:
                    shift = over_x / 2 + 1
                    lo, hi = (a, b) if a["cx"] <= b["cx"] else (b, a)
                    lo["cx"] -= shift; hi["cx"] += shift
                else:
                    shift = over_y / 2 + 1
                    lo, hi = (a, b) if a["cy"] <= b["cy"] else (b, a)
                    lo["cy"] -= shift; hi["cy"] += shift
        if not collided:
            break

    for item in labels:
        half_w = max(item["r"], item["nw"] / 2)
        item["cx"] = min(max(item["cx"], half_w + 2), w - half_w - 2)
        item["cy"] = min(max(item["cy"], item["r"] + 2), h - item["r"] - gap - item["nh"] - 2)

    for item in labels:
        if abs(item["cx"] - item["ox"]) + abs(item["cy"] - item["oy"]) > item["r"]:
            draw.line([(item["ox"], item["oy"]), (item["cx"], item["cy"])],
                      fill=label_color, width=max(1, scale))
            dot = max(2, scale)
            draw.ellipse([item["ox"] - dot, item["oy"] - dot, item["ox"] + dot, item["oy"] + dot],
                         fill=label_color)

    for item in labels:
        cx, cy, radius = item["cx"], item["cy"], item["r"]
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                     fill=label_color, outline=water_color, width=max(1, 2 * scale))
        draw.text((cx, cy), item["text"], fill=water_color, font=value_font, anchor="mm")
        box = draw.textbbox((cx, cy + radius + gap), item["name"], font=name_font, anchor="ma")
        pad = 3 * scale
        draw.rounded_rectangle([box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad],
                               radius=4 * scale, fill=water_color, outline=border_color, width=max(1, scale // 2))
        draw.text((cx, cy + radius + gap), item["name"], fill=label_color, font=name_font, anchor="ma")

    if value_caption and present:
        legend_font = _map_font(9 * scale)
        bar_w, bar_h = 150 * scale, 9 * scale
        bx, by = margin + 4 * scale, h - margin - bar_h - 16 * scale
        for i in range(int(bar_w)):
            draw.line([(bx + i, by), (bx + i, by + bar_h)],
                      fill=_mix(low_color, high_color, 0.25 + 0.75 * (i / bar_w)))
        draw.rectangle([bx, by, bx + bar_w, by + bar_h], outline=border_color, width=max(1, scale // 2))
        draw.text((bx, by - 4 * scale), value_caption, fill=label_color, font=legend_font, anchor="lb")
        draw.text((bx, by + bar_h + 3 * scale), f"{v_min:,.0f}".replace(",", "."), fill=border_color, font=legend_font, anchor="la")
        draw.text((bx + bar_w, by + bar_h + 3 * scale), f"{v_max:,.0f}".replace(",", "."), fill=border_color, font=legend_font, anchor="ra")

    img = img.resize((width_px, height_px), PILImage.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return RLImage(buf, width=width_cm * cm, height=height_cm * cm)


# Recorte (en fracciones del ancho/alto) del strip de logotipos que se usa en
# la cinta de encabezado del PDF: toma solo los logos institucionales de la
# primera fila (Clúster Salud, Sala Situacional, Ministerio del Poder Popular
# para la Salud y OPS/OMS). El strip completo trae más de treinta logos de
# socios y, reducido al tamaño de la cinta, quedaría ilegible. Se expresa en
# fracciones —no en píxeles— para que siga funcionando si se reemplaza la
# imagen por otra del mismo diseño en distinta resolución.
BAND_LOGO_CROP = (0.0, 0.02, 0.42, 0.56)


def _band_logo(logo_path: object, crop: tuple[float, float, float, float] | None) -> ImageReader | None:
    if not logo_path:
        return None
    try:
        image = PILImage.open(logo_path).convert("RGB")
        if crop:
            iw, ih = image.size
            box = (int(crop[0] * iw), int(crop[1] * ih), int(crop[2] * iw), int(crop[3] * ih))
            if box[2] > box[0] and box[3] > box[1]:
                image = image.crop(box)
        return ImageReader(image)
    except (OSError, ValueError):
        return None


def _fit_width(canvas, text: str, font: str, size: float, max_width: float) -> str:
    """Recorta `text` con puntos suspensivos para que no invada el panel de logos."""
    if canvas.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed and canvas.stringWidth(trimmed + ellipsis, font, size) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + ellipsis) if trimmed else ""


def _page_decor_factory(*, kicker: str, title: str, subtitle: str, footer_left: str,
                         as_of_text: str, palette: dict[str, str], logo_reader: ImageReader | None):
    """Cinta azul de encabezado + pie de página, repetidos en TODAS las hojas.

    Reemplaza al bloque de título que antes vivía en el flujo del documento
    (y por lo tanto solo aparecía en la primera hoja): así cualquier página
    suelta del PDF sigue identificando de qué tablero, qué formulario y qué
    corte salió.
    """
    NAVY, BLUE = HexColor(palette["navy"]), HexColor(palette["blue"])
    BORDER, MUTED = HexColor(palette["border"]), HexColor(palette["muted"])
    PALE = HexColor(palette.get("pale", "#EAF4FA"))

    def _decor(canvas, doc) -> None:
        canvas.saveState()
        width, height = landscape(A4)
        x0, x1 = 1.2 * cm, width - 1.2 * cm
        band_h = 1.85 * cm
        y1 = height - 0.7 * cm
        y0 = y1 - band_h

        # Degradado navy → azul, con las mismas esquinas redondeadas del
        # encabezado del tablero. linearGradient pinta la región de recorte
        # activa, así que primero se recorta al rectángulo redondeado.
        canvas.saveState()
        band_path = canvas.beginPath()
        band_path.roundRect(x0, y0, x1 - x0, band_h, 7)
        canvas.clipPath(band_path, stroke=0, fill=0)
        canvas.linearGradient(x0, y0, x1, y0, (NAVY, BLUE), extend=True)
        canvas.restoreState()

        text_limit = x1 - 0.6 * cm
        if logo_reader is not None:
            logo_w_px, logo_h_px = logo_reader.getSize()
            logo_h = 0.92 * cm
            logo_w = logo_h * (logo_w_px / logo_h_px)
            panel_w, panel_h = logo_w + 0.34 * cm, logo_h + 0.26 * cm
            panel_x = x1 - 0.4 * cm - panel_w
            panel_y = y0 + (band_h - panel_h) / 2
            canvas.setFillColor(WHITE)
            canvas.roundRect(panel_x, panel_y, panel_w, panel_h, 5, stroke=0, fill=1)
            canvas.drawImage(logo_reader, panel_x + 0.17 * cm, panel_y + 0.13 * cm,
                             width=logo_w, height=logo_h, mask="auto")
            text_limit = panel_x - 0.45 * cm

        text_x = x0 + 0.62 * cm
        max_text = max(2 * cm, text_limit - text_x)
        canvas.setFillColor(PALE)
        canvas.setFont("Helvetica-Bold", 6.6)
        canvas.drawString(text_x, y1 - 0.52 * cm, _fit_width(canvas, kicker.upper(), "Helvetica-Bold", 6.6, max_text))
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 12.5)
        canvas.drawString(text_x, y1 - 1.08 * cm, _fit_width(canvas, title, "Helvetica-Bold", 12.5, max_text))
        canvas.setFillColor(PALE)
        canvas.setFont("Helvetica", 7.4)
        canvas.drawString(text_x, y1 - 1.55 * cm, _fit_width(canvas, subtitle, "Helvetica", 7.4, max_text))

        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(x0, 0.85 * cm, x1, 0.85 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(x0, 0.52 * cm, f"{footer_left} · {as_of_text}")
        canvas.drawRightString(x1, 0.52 * cm, f"Página {doc.page}")
        canvas.restoreState()

    return _decor


def build_report(*, title: str, subtitle: str, scope_text: str, as_of_text: str,
                  kpis: list[tuple[str, str]], sections: list[dict], palette: dict[str, str],
                  footer_left: str = "Clúster Salud · Venezuela",
                  kicker: str = "OPS/OMS · Clúster de Salud · Venezuela",
                  logo_path: object = None) -> bytes:
    """sections: [{"title": str, "rows": [[panel, ...], ...], "caption": str | None}, ...]

    `title`, `subtitle` y `kicker` se dibujan en la cinta azul de encabezado
    que se repite en todas las páginas (ver _page_decor_factory), no en el
    flujo del documento; `logo_path` es el strip de logotipos del que se
    recorta la parte institucional para esa cinta.
    """
    buffer = BytesIO()
    styles = _styles(palette["navy"], palette["muted"], palette["ink"], palette["blue"])
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        # El margen superior deja libre la franja que ocupa la cinta azul
        # (0,7 cm de aire + 1,85 cm de cinta + un respiro).
        leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=2.9 * cm, bottomMargin=1.15 * cm,
        title=title, author="Clúster Salud · Venezuela", subject=subtitle,
    )
    story: list = [
        Paragraph(_safe(scope_text), styles["caption"]),
        Spacer(1, 4),
        kpi_cards(kpis, styles, palette["border"]),
        Spacer(1, 10),
    ]
    for section in sections:
        # Cada sección se mantiene junta (KeepTogether) para no partir un
        # panel a la mitad entre dos páginas, pero SIN forzar un PageBreak
        # entre secciones: así varias secciones cortas comparten página en
        # vez de dejar la mayor parte de cada hoja en blanco (una gráfica
        # chica sola en una página A4 apaisada se veía "a medias").
        # ReportLab solo pasa a la página siguiente cuando el contenido no
        # entra en el espacio restante.
        section_flow: list = [Paragraph(_safe(section["title"]), styles["h1"]), Spacer(1, 2)]
        for row in section["rows"]:
            # row: list of (panel_title, content_drawing_or_table, width_cm)
            panels = [panel(t, content, styles, palette["border"], width_cm=w) for t, content, w in row]
            section_flow.append(row_of_panels(panels) if len(panels) > 1 else panels[0])
            section_flow.append(Spacer(1, 8))
        if section.get("caption"):
            section_flow.append(Paragraph(_safe(section["caption"]), styles["caption"]))
        section_flow.append(Spacer(1, 10))
        story.append(KeepTogether(section_flow))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Simulacro metodológico.", styles["caption"]))
    decor = _page_decor_factory(
        kicker=kicker, title=title, subtitle=subtitle, footer_left=footer_left,
        as_of_text=as_of_text, palette=palette, logo_reader=_band_logo(logo_path, BAND_LOGO_CROP),
    )
    doc.build(story, onFirstPage=decor, onLaterPages=decor)
    return buffer.getvalue()
