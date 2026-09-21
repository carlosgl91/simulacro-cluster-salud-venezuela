"""Simulacro ejecutivo del tablero del Clúster Salud para los nuevos F01 y F02.

Los datos históricos se usan únicamente para ensayar la estructura. Las fechas del
calendario piloto son simuladas y están identificadas como tales en la interfaz.
"""

from __future__ import annotations

import base64
import io
import json
import math
import re
import sys
import tempfile
import textwrap
import unicodedata
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# The desktop preview keeps its document libraries in a shared runtime.  Add
# that location only after the data stack is loaded so its NumPy build cannot
# shadow the application's own installation.
_shared_packages = Path(r"C:\Users\claud\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages")
if _shared_packages.exists():
    sys.path.append(str(_shared_packages))

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
NAVY, BLUE, RED, YELLOW = "#18395F", "#356A9A", "#C94B3C", "#E8C247"
INK, MUTED, PAPER, PALE, TEAL = "#20252B", "#68737E", "#F7F5EF", "#EAF0F5", "#4B8A91"
GREEN, GREEN_LIGHT = "#2E7D5B", "#7FB196"
FOCUS_COLORS = {"Establecimiento de salud": RED, "Salud pública": YELLOW}
CALENDAR_YEAR = 2026
# Catálogo real de "Tipo de lugar de la intervención" (XLSForm F01/F02,
# lista `intervention_place_type`). El histórico no trae este dato, así que
# se asigna de forma determinística para poder mostrar las 9 categorías.
PUBLIC_PLACE_TYPES = [
    "Comunidad",
    "Alojamiento temporal / Albergue",
    "Establecimiento de salud",
    "Centro comunitario",
    "Escuela / Institución educativa",
    "Espacio público",
    "Brigada móvil / Punto móvil de atención",
    "Centro de protección",
    "Otro (especificar)",
]
# Catálogo real de la pregunta 4.1 del F02 ("Seleccione las áreas o
# servicios del establecimiento de salud que recibieron apoyo durante este
# período") — la comparten F01 (bloque 5.1, dato real en areas.csv) y F02,
# pero el histórico de reportes periódicos (resultados.csv) nunca capturó
# esta pregunta por período. Autorizado explícitamente por la usuaria:
# simular estos datos de forma determinística para este simulacro,
# mientras no exista un histórico real de reportes F02 con esta pregunta.
FACILITY_AREA_CATALOG = [
    "Agua, saneamiento e higiene",
    "Atención materna y obstétrica",
    "Atención neonatal",
    "Cirugía",
    "Consulta externa",
    "Emergencias",
    "Farmacia y dispensación de medicamentos",
    "Gestión y manejo de residuos hospitalarios",
    "Hospitalización",
    "Imagenología",
    "Laboratorio clínico",
    "Medicina interna",
    "Odontología",
    "Pediatría",
    "Rehabilitación",
    "Salud mental y apoyo psicosocial",
    "Salud sexual y reproductiva",
    "Vacunación",
]

st.set_page_config(page_title="Tablero de la Respuesta en Salud · Venezuela", page_icon="✚", layout="wide")


@st.cache_data
def load_municipio_geojson() -> dict:
    """Límites municipales reales de Venezuela (OCHA/HDX vía el repositorio anterior del tablero),
    para mapas de polígonos en vez de puntos. Se agrega "join_key" = "Estado | Municipio" a cada
    feature porque el nombre de municipio solo no es único (p. ej. "Libertador" existe en varios
    estados) — así se puede unir sin ambigüedad con los datos reales de estado+municipio."""
    with open(DATA / "geografia" / "limites_municipales.geojson", encoding="utf-8") as f:
        geo = json.load(f)
    for feature in geo["features"]:
        props = feature["properties"]
        props["join_key"] = f"{props['adm1_name']} | {props['adm2_name']}"
    return geo


@st.cache_data
def load_data() -> dict[str, pd.DataFrame]:
    files = {
        "facilities": DATA / "establecimientos" / "establecimientos.csv",
        "facility_areas": DATA / "establecimientos" / "areas.csv",
        "supports": DATA / "establecimientos" / "apoyos.csv",
        "places": DATA / "cobertura" / "puntos_atencion.csv",
        "offered": DATA / "cobertura" / "acciones_ofertadas.csv",
        "reports": DATA / "acciones" / "reportes.csv",
        "results": DATA / "acciones" / "resultados.csv",
    }
    out = {k: pd.read_csv(v, encoding="utf-8-sig") for k, v in files.items()}
    for frame in out.values():
        for col in ("organizacion", "estado", "municipio"):
            if col in frame:
                frame[col] = frame[col].fillna("No indicado").astype(str)
                if col == "organizacion":
                    frame[col] = frame[col].map(canonical_org)
    for key in ("facilities", "reports", "results"):
        if "fecha_reporte" in out[key]:
            out[key]["fecha_reporte"] = pd.to_datetime(out[key]["fecha_reporte"], errors="coerce")
    for col in ("desde", "hasta"):
        out["places"][col] = pd.to_datetime(out["places"][col], errors="coerce")
    return out


def clean_service(value: object) -> str:
    text = str(value or "No indicado").strip()
    return re.sub(r"^\d+(?:\.\d+)*\s*", "", text)


def normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


# El histórico trae la misma organización escrita de varias formas (mayúsculas,
# acentos, nombre corto vs. razón social completa), lo que inflaba los conteos
# de "socios" al contarlas como organizaciones distintas. Se normalizan a un
# único nombre canónico antes de cualquier conteo o gráfica.
ORG_CANONICAL = {
    "medicos sin fronteras": "Médicos Sin Fronteras",
    "oim": "OIM - Organización Internacional para las Migraciones",
    "oim organizacion internacional para las migraciones": "OIM - Organización Internacional para las Migraciones",
    "accion solidaria": "Acción Solidaria",
    "fundacion vive mas": "Fundación Vive Más (FVM)",
    "fundacion vive mas fvm": "Fundación Vive Más (FVM)",
    "fundaensalud": "FUNDAENSALUD (Fundación Educando en Salud)",
    "fundaensalud fundacion educando en salud": "FUNDAENSALUD (Fundación Educando en Salud)",
    "fondo de naciones unidas para la infancia": "UNICEF",
}


def canonical_org(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    return ORG_CANONICAL.get(normalize_name(text), text)


def classify_health_facility(name: object, reported_type: object) -> str:
    if pd.notna(reported_type) and str(reported_type).strip() and str(reported_type).lower() != "nan":
        return str(reported_type).strip()
    text = normalize_name(name)
    if "hospital" in text or "materno" in text: return "Hospital"
    if "ambulatorio" in text: return "Ambulatorio"
    if "cdi" in text: return "Centro de Diagnóstico Integral (CDI)"
    if "cpt" in text or "consultorio" in text: return "Consultorio o CPT"
    return "Otro establecimiento"


def area_from_indicator(code: object) -> str:
    m = re.match(r"(?:3|4)\.(\d+)", str(code))
    lookup = {
        "1": "Salud mental y apoyo psicosocial", "2": "Enfermedades no transmisibles",
        "3": "Enfermedades transmisibles, VIH e ITS", "4": "Salud materna y perinatal",
        "5": "Salud sexual y reproductiva", "6": "Violencia sexual", "7": "Salud nutricional",
        "8": "Inmunización", "9": "Lesiones y traumas", "10": "Discapacidad",
        "11": "Acciones institucionales", "12": "Atención primaria",
        "13": "Vigilancia epidemiológica", "14": "Acciones comunitarias", "15": "Gestión de casos",
    }
    return lookup.get(m.group(1), "Otra área") if m else "Otra área"


@st.cache_data
def prepare() -> dict[str, pd.DataFrame]:
    t = load_data()
    required_place_columns={"parroquia","donantes","tipo_organizacion","modalidad_implementacion","desde","hasta"}
    if not required_place_columns.issubset(t["places"].columns):
        t["places"]=pd.read_csv(DATA / "cobertura" / "puntos_atencion.csv",encoding="utf-8-sig")
        for col in ("desde","hasta"): t["places"][col]=pd.to_datetime(t["places"][col],errors="coerce")
        for col in ("organizacion","estado","municipio","parroquia"): t["places"][col]=t["places"][col].fillna("No reportada" if col=="parroquia" else "No indicado").astype(str)
        t["places"]["organizacion"]=t["places"]["organizacion"].map(canonical_org)

    # Vocabulario alineado con los formularios F01/F02 vigentes (XLSForm
    # F01_guia_enlace_simple_20260914 / F02_lista_organizaciones_completa_20260915).
    # El histórico usa categorías de los formularios viejos ("Nacional") y
    # tiene un error real donde una modalidad mixta queda como el texto de
    # ambas opciones concatenado; se remapea a las opciones reales vigentes.
    t["places"]["tipo_organizacion"] = t["places"]["tipo_organizacion"].replace(
        {"Nacional": "ONG Nacional", "Internacional": "ONG Internacional"}
    )
    # Hash por organización (no por fila) para que todos los puntos de una
    # misma organización compartan siempre el mismo tipo, en vez de quedar
    # inconsistentes entre sí.
    org_seed = pd.util.hash_pandas_object(t["places"]["organizacion"].astype(str), index=False).astype("uint64")
    # "Sociedad Civil" y "Otro" son categorías reales que nunca aparecen en el
    # histórico; se asignan a una porción pequeña y determinística de
    # organizaciones para poder visualizar el catálogo completo en el simulacro.
    t["places"].loc[(org_seed % 11).eq(0), "tipo_organizacion"] = "Sociedad Civil"
    t["places"].loc[(org_seed % 13).eq(0), "tipo_organizacion"] = "Otro"

    t["places"]["modalidad_implementacion"] = t["places"]["modalidad_implementacion"].replace(
        {"Implementador directo Socio implementador": "Ambas"}
    )

    fac = t["facilities"].copy()
    fac["foco"] = "Establecimiento de salud"
    fac["lugar"] = fac["nombre_establecimiento"]
    fac["id_punto"] = "FAC-" + fac["id_establecimiento"].astype(str)
    fac["tipo_punto"] = fac.apply(lambda row: classify_health_facility(row["nombre_establecimiento"],row.get("tipo_establecimiento")),axis=1)
    fac["parroquia"] = "No reportada"

    rep = t["reports"].copy()
    facility_names = {normalize_name(v) for v in fac["nombre_establecimiento"].dropna()}
    health_terms = ("hospital", "ambulatorio", "consultorio", "clinica", "cdi ", "cpt ", "materno infantil", "maternidad")
    rep["foco"] = rep["nombre_sitio"].map(
        lambda v: "Establecimiento de salud"
        if normalize_name(v) in facility_names or any(term in f"{normalize_name(v)} " for term in health_terms)
        else "Salud pública"
    )
    facility_type_lookup = (
        fac.dropna(subset=["nombre_establecimiento"]).assign(_key=lambda x:x.nombre_establecimiento.map(normalize_name))
        .drop_duplicates("_key").set_index("_key")["tipo_establecimiento"].to_dict()
    )
    place_parish_lookup = (
        t["places"].dropna(subset=["nombre_sitio"]).assign(_key=lambda x:x.nombre_sitio.map(normalize_name))
        .drop_duplicates("_key").set_index("_key")["parroquia"].to_dict()
    )
    rep["tipo_punto"] = rep.apply(
        lambda row: classify_health_facility(
            row["nombre_sitio"], facility_type_lookup.get(normalize_name(row["nombre_sitio"]))
        )
        if row["foco"] == "Establecimiento de salud"
        else None,
        axis=1,
    )
    # El campo real "tipo_lugar" del histórico está vacío ("No reportado") en
    # prácticamente todas las filas de Salud pública, así que se asigna el
    # catálogo real (`intervention_place_type`) de forma determinística para
    # poder mostrar las 9 categorías con datos de ejemplo en el simulacro.
    public_mask = rep["foco"].eq("Salud pública")
    public_seed = pd.util.hash_pandas_object(
        rep.loc[public_mask, "organizacion"].astype(str) + "|" + rep.loc[public_mask, "nombre_sitio"].astype(str),
        index=False,
    ).astype("uint64")
    rep.loc[public_mask, "tipo_punto"] = [
        PUBLIC_PLACE_TYPES[value % len(PUBLIC_PLACE_TYPES)] for value in public_seed
    ]
    rep["lugar"] = rep["nombre_sitio"]
    rep["parroquia"] = rep["nombre_sitio"].map(lambda v: place_parish_lookup.get(normalize_name(v),"No reportada"))
    rep["parroquia"] = rep["parroquia"].fillna("No reportada")
    rep["id_punto"] = rep["organizacion"].astype(str) + " | " + rep["nombre_sitio"].astype(str)
    public_points = rep[rep.foco.eq("Salud pública")].sort_values("fecha_reporte").drop_duplicates("id_punto", keep="last")

    points = pd.concat([
        fac[["id_punto", "organizacion", "estado", "municipio", "parroquia", "lugar", "latitud", "longitud", "foco", "tipo_punto", "fecha_reporte"]],
        public_points[["id_punto", "organizacion", "estado", "municipio", "parroquia", "lugar", "latitud", "longitud", "foco", "tipo_punto", "fecha_reporte"]],
    ], ignore_index=True)
    points["latitud"] = pd.to_numeric(points["latitud"], errors="coerce")
    points["longitud"] = pd.to_numeric(points["longitud"], errors="coerce")
    # Los formularios históricos no incluían inversión. Se genera una muestra
    # estable solo para probar cómo se leerá el nuevo campo F01 en el tablero.
    seed = pd.util.hash_pandas_object(points["id_punto"].astype(str), index=False).astype("uint64")
    # 78 valores distintos (300 a 8.000, paso 100) en vez de los 16 de antes:
    # con pocos valores posibles, el ranking de "mayor inversión" quedaba con
    # varios puntos empatados justo en el máximo ($8.0k repetido), lo que se
    # veía poco creíble. Con más granularidad el total sigue rondando el
    # mismo orden de magnitud (~USD 1.000.000 sobre el universo completo)
    # pero el top 10-12 ya no se ve idéntico ni tan "en cuadrícula".
    points["inversion_usd"] = ((seed % 78) + 3) * 100

    results = t["results"].copy()
    results["area"] = results["indicador_codigo"].map(area_from_indicator)
    results = results.merge(rep[["id_reporte", "organizacion", "estado", "municipio", "lugar", "fecha_reporte", "foco"]], on="id_reporte", how="left", suffixes=("", "_rep"))

    facility_services = t["facility_areas"].merge(fac[["id_establecimiento", "organizacion", "estado", "municipio", "lugar", "tipo_punto"]], on="id_establecimiento", how="left")
    facility_services["servicio"] = facility_services["area_servicio"].map(clean_service)
    # Bloque 5.2 del F01 ("Seleccione el tipo o los tipos de apoyo que serán
    # proporcionados"): es una pregunta real distinta de 5.1 (áreas/servicios
    # que reciben apoyo) — datos reales de apoyos.csv, nunca antes usados en
    # el tablero.
    facility_supports = t["supports"].merge(fac[["id_establecimiento", "organizacion", "estado", "municipio", "lugar", "tipo_punto"]], on="id_establecimiento", how="left")
    facility_supports["tipo_apoyo"] = facility_supports["tipo_apoyo"].map(clean_service)
    offered = t["offered"].merge(t["places"][["id_servicio", "organizacion", "estado", "municipio", "nombre_sitio"]], on="id_servicio", how="left")
    offered["servicio"] = offered["servicio"].map(clean_service)
    offered["lugar"] = offered["nombre_sitio"]

    donor_base = t["places"].copy()
    if "donantes" not in donor_base.columns:
        donor_base = pd.read_csv(DATA / "cobertura" / "puntos_atencion.csv", encoding="utf-8-sig")
    donor_sources = donor_base[["id_servicio","organizacion","estado","municipio","donantes"]].copy()
    donor_sources["donantes"] = donor_sources["donantes"].fillna("").astype(str).str.strip().replace({"":"Sin fuente reportada","nan":"Sin fuente reportada"})
    donor_sources["donantes"] = donor_sources["donantes"].str.split(r"\s*[,;]\s*",regex=True)
    donor_sources = donor_sources.explode("donantes",ignore_index=True)

    # Calendario de brigadas de salud pública: SOLO puntos que declararon
    # fecha de inicio real en el F01 ("4. Vigencia de la intervención" de
    # puntos_atencion.csv), expandidos día por día usando los días de la
    # semana que cada punto realmente declaró operar — sin fechas
    # estimadas ni inventadas. Si un punto no declaró fecha de inicio (o no
    # marcó ningún día de la semana), no aparece en el calendario.
    year_start = pd.Timestamp(f"{CALENDAR_YEAR}-01-01")
    year_end = pd.Timestamp(f"{CALENDAR_YEAR}-12-31")
    weekday_cols = ["dia_lunes", "dia_martes", "dia_miercoles", "dia_jueves", "dia_viernes", "dia_sabado", "dia_domingo"]
    service_labels = {
        "serv_atencion_primaria": "Atención primaria",
        "serv_salud_mental_psicosocial": "Salud mental y apoyo psicosocial",
        "serv_enfermedades_no_transmisibles": "Enfermedades no transmisibles",
        "serv_enfermedades_transmisibles": "Enfermedades transmisibles, VIH e ITS",
        "serv_salud_materna_perinatal": "Salud materna y perinatal",
        "serv_prevencion_atencion_vih": "Prevención y atención VIH",
        "serv_salud_sexual_reproductiva": "Salud sexual y reproductiva",
        "serv_violencia_sexual": "Violencia sexual",
        "serv_salud_nutricional": "Salud nutricional",
        "serv_vacunacion": "Vacunación",
        "serv_alerta_temprana_vigilancia_epidemiologica": "Vigilancia epidemiológica",
        "serv_lesiones_traumas": "Lesiones y traumas",
        "serv_acciones_institucionales": "Acciones institucionales",
        "serv_acciones_comunitarias": "Acciones comunitarias",
        "serv_gestion_casos": "Gestión de casos",
    }

    vigencia_real = t["places"].copy()
    vigencia_real["desde"] = pd.to_datetime(vigencia_real["desde"], errors="coerce")
    vigencia_real["hasta"] = pd.to_datetime(vigencia_real["hasta"], errors="coerce")
    vigencia_real = vigencia_real.dropna(subset=["desde"])
    vigencia_real["hasta_efectiva"] = vigencia_real["hasta"].fillna(year_end)

    calendar_rows = []
    for row in vigencia_real.itertuples():
        active_weekdays = {i for i, col in enumerate(weekday_cols) if getattr(row, col, 0) == 1}
        if not active_weekdays:
            continue
        activity = next((label for col, label in service_labels.items() if getattr(row, col, 0) == 1), "Brigada de salud pública")
        jornada = "Tarde" if getattr(row, "horario_tarde", 0) == 1 and getattr(row, "horario_manana", 0) != 1 else "Mañana"
        start = max(row.desde, year_start)
        end = min(row.hasta_efectiva, year_end)
        if start > end:
            continue
        for day in pd.date_range(start, end):
            if day.weekday() in active_weekdays:
                calendar_rows.append({
                    "id_punto": row.id_servicio, "organizacion": row.organizacion, "estado": row.estado,
                    "municipio": row.municipio, "parroquia": row.parroquia, "lugar": row.nombre_sitio,
                    "actividad": activity, "jornada": jornada, "fecha": day, "foco": "Salud pública",
                })
    sample = pd.DataFrame(calendar_rows, columns=["id_punto","organizacion","estado","municipio","parroquia","lugar","actividad","jornada","fecha","foco"])
    return {**t, "reports_prepared": rep, "points": points, "public_points": public_points, "results_joined": results,
            "facility_services_joined": facility_services, "facility_supports_joined": facility_supports, "offered_joined": offered, "donor_sources": donor_sources,
            "calendar": sample}


def img_data(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii") if path.exists() else ""


def num(value: float | int) -> str:
    return f"{value:,.0f}".replace(",", ".")


def plot(fig: go.Figure, height: int = 420, key: str | None = None, margin: dict | None = None, is_map: bool = False) -> None:
    if not getattr(fig.layout.title, "text", None):
        fig.update_layout(title={"text": ""})
    # Ajuste centralizado de legibilidad: fuente más grande y margen derecho
    # suficiente para que las etiquetas "outside" de las barras no se
    # recorten contra el borde de la figura (cliponaxis=False evita que
    # Plotly las corte incluso cuando quedan cerca del máximo del eje).
    fig.update_traces(selector=dict(type="bar"), cliponaxis=False)
    # Las etiquetas de cada treemap conservan el tamaño específico de su
    # sección; sobrescribirlo aquí las reducía incluso después de ajustarlas.
    fig.update_traces(selector=dict(type="pie"), textfont=dict(size=13))
    fig.update_layout(height=height, margin=margin or (dict(l=0, r=0, t=0, b=44) if is_map else dict(l=16, r=54, t=52, b=18)), paper_bgcolor=PAPER,
                      plot_bgcolor=PAPER, font=dict(family="Arial", color=INK, size=13),
                      title_font=dict(family="Arial", color=NAVY, size=19), legend_title_text="")
    if is_map:
        # Leyenda horizontal debajo del mapa (no a la derecha): así el mapa
        # ocupa todo el ancho de la figura y el modebar (cámara/+/-/home/
        # pantalla completa), que Plotly siempre ubica arriba a la derecha
        # de TODA la figura, queda superpuesto sobre el mapa en sí — antes,
        # con la leyenda a la derecha, el modebar terminaba flotando sobre
        # esa columna en blanco en vez de sobre el mapa.
        fig.update_layout(legend=dict(font=dict(size=16), orientation="h", yanchor="top", y=-0.03, xanchor="left", x=0, tracegroupgap=28))
    else:
        fig.update_layout(legend=dict(font=dict(size=12)))
    fig.update_xaxes(gridcolor="#DDD8CD", zeroline=False, automargin=True, tickfont=dict(size=12))
    fig.update_yaxes(gridcolor="#DDD8CD", zeroline=False, automargin=True, tickfont=dict(size=13))
    if is_map:
        # Mapas: se deshabilita el zoom con la rueda del mouse (para que
        # hacer scroll de la página no achique el mapa sin querer). Los
        # íconos por defecto de Plotly son gris claro y casi no se notan
        # sobre el mapa — se fuerza un fondo blanco sólido y color oscuro
        # para que se vean claramente encima de las tejas del mapa.
        fig.update_layout(modebar=dict(bgcolor="rgba(255,255,255,0.95)", color=NAVY, activecolor=RED, orientation="h"))
        map_config = {
            "displayModeBar": True, "scrollZoom": False, "displaylogo": False,
            "modeBarButtonsToRemove": ["pan2d", "lasso2d", "select2d", "resetViewMapbox"],
        }
        st.plotly_chart(fig, width="stretch", config=map_config, key=key)
    else:
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=key)


def tile_name(name: str) -> str:
    # Conservar el nombre completo: los saltos de línea permiten leerlo
    # también en los bloques pequeños sin recurrir a siglas nuevas.
    return "<br>".join(textwrap.wrap(name, width=18, break_long_words=False))


def set_org_tile_labels(fig) -> None:
    # La jerarquía visual acompaña la cantidad de puntos, con un rango de
    # tamaños marcado para que la diferencia se note de un vistazo; Plotly
    # todavía puede reducir más el texto si un bloque queda angosto.
    labels=[]
    for name,value in zip(fig.data[0].labels,fig.data[0].values):
        size=24 if value>=20 else 19 if value>=10 else 15 if value>=5 else 12 if value>=3 else 9
        labels.append(f'<span style="font-size:{size}px">{name}<br>{value:g}</span>')
    fig.update_traces(text=labels,texttemplate="%{text}",textfont_size=24)


def integer_colorbar_ticks(cmax: int, target_steps: int = 5) -> tuple[list[int], list[str]]:
    """Genera ticks enteros y parejos para una barra de color (0, 5, 10, 15…), en vez
    de forzar el máximo exacto como último tick suelto (p. ej. …,10,12,13)."""
    cmax = max(int(cmax), 1)
    raw_step = cmax / target_steps
    magnitude = 10 ** math.floor(math.log10(raw_step)) if raw_step >= 1 else 1
    step = next((c * magnitude for c in (1, 2, 5, 10) if c * magnitude >= raw_step), 10 * magnitude)
    step = max(1, int(step))
    top = (cmax // step + 1) * step
    vals = list(range(0, int(top) + 1, step))
    return vals, [str(v) for v in vals]


def metric(label: str, value: object, note: str) -> str:
    return f'<div class="metric"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>'


def section_band(title: str, question: str = "") -> None:
    question_html = f"<span>{question}</span>" if question else ""
    st.markdown(f'<div class="section-band">{title}{question_html}</div>', unsafe_allow_html=True)


def _export_copy(fig: go.Figure) -> go.Figure:
    """Copia la gráfica a un tamaño fijo para insertarla en el PDF."""

    export_fig = go.Figure(fig)
    export_fig.update_layout(width=1500, height=880, margin=dict(l=50, r=50, t=70, b=50))
    return export_fig


# Plantilla de página para el PDF, calcada del formato del tablero anterior
# (cluster-salud-venezuela.streamlit.app → "Descargar infografía general"):
# banda navy superior con logos y título, tarjetas de métricas, paneles
# blancos tipo tarjeta por gráfica, y pie con fuente + número de página.
_PDF_HEADER_H = 96
_PDF_SIDE = 30


def _pdf_page_shell(c, w: float, h: float, kicker: str, title: str, subtitle: str, page_label: str) -> None:
    c.setFillColor(colors.HexColor(PAPER)); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(colors.HexColor(NAVY)); c.rect(0, h - _PDF_HEADER_H, w, _PDF_HEADER_H, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#9FC3E8")); c.setFont("Helvetica-Bold", 8.5)
    c.drawString(_PDF_SIDE + 8, h - 24, kicker.upper())
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 21)
    c.drawString(_PDF_SIDE + 8, h - 48, title[:70])
    c.setFillColor(colors.HexColor("#C9DCEC")); c.setFont("Helvetica", 10)
    c.drawString(_PDF_SIDE + 8, h - 65, subtitle[:95])
    logo_path = ROOT / "logos_formularios_hd.png"
    box_h = 0.0
    if logo_path.exists():
        try:
            logo_img = ImageReader(str(logo_path))
            logo_w, logo_h = logo_img.getSize()
            box_w = 250.0; box_h = box_w * logo_h / logo_w
            box_x = w - _PDF_SIDE - 8 - box_w; box_y = h - 16 - box_h
            c.setFillColor(colors.white); c.roundRect(box_x - 8, box_y - 6, box_w + 16, box_h + 12, 5, fill=1, stroke=0)
            c.drawImage(logo_img, box_x, box_y, width=box_w, height=box_h)
        except Exception:
            box_h = 0.0
    c.setFillColor(colors.white); c.setFont("Helvetica", 8)
    c.drawRightString(w - _PDF_SIDE - 8, h - _PDF_HEADER_H + 10, page_label)


def _pdf_footer(c, w: float, page_num: int, total_pages: int) -> None:
    c.setStrokeColor(colors.HexColor(NAVY)); c.setLineWidth(1.2)
    c.line(_PDF_SIDE, 30, w - _PDF_SIDE, 30)
    c.setFillColor(colors.HexColor(MUTED)); c.setFont("Helvetica", 7.5)
    c.drawString(_PDF_SIDE, 18, "Fuente: simulacro con datos históricos y muestras piloto · Clúster Salud Venezuela · OPS/OMS")
    c.setFillColor(colors.HexColor(NAVY)); c.setFont("Helvetica-Bold", 7.5)
    c.drawRightString(w - _PDF_SIDE, 18, f"Página {page_num} de {total_pages}")


def _pdf_card(c, x: float, y: float, w: float, h: float) -> None:
    c.setFillColor(colors.white); c.roundRect(x, y, w, h, 7, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#E2E6EA")); c.setLineWidth(0.7); c.roundRect(x, y, w, h, 7, fill=0, stroke=1)


def make_pdf(
    module: str,
    metrics: list[tuple[str, str]],
    notes: list[str],
    figures: list[tuple[str, go.Figure]],
) -> tuple[bytes, int]:
    """Devuelve el PDF (formato de tarjetas con banda de marca, igual que el tablero anterior) y el número de gráficas que no pudieron exportarse."""
    buffer = io.BytesIO(); page = landscape(A4); c = canvas.Canvas(buffer, pagesize=page)
    w, h = page
    today_label = pd.Timestamp.today().strftime("%d/%m/%Y")

    # Las gráficas se exportan todas juntas con pio.write_images (un solo
    # navegador Chromium para todo el lote) en vez de una llamada a
    # to_image() por gráfica: esta última abre y cierra un navegador por
    # cada imagen, lo que además de ser mucho más lento es la causa más
    # probable de descargas que fallaban por completo al pedir varias
    # secciones a la vez.
    failed = 0
    ok: list[tuple[Path, str]] = []
    tmp_dir_ctx = tempfile.TemporaryDirectory()
    tmp_dir = tmp_dir_ctx.name
    if figures:
        export_figs = [_export_copy(fig) for _, fig in figures]
        paths = [Path(tmp_dir) / f"fig_{i}.png" for i in range(len(export_figs))]
        try:
            pio.write_images(export_figs, paths, format="png", scale=2)
            ok = list(zip(paths, [name for name, _ in figures]))
        except Exception:
            # Si el lote falla por una sola gráfica problemática, se
            # reintenta una por una para no perder toda la descarga.
            for (name, fig), path in zip(figures, paths):
                try:
                    pio.write_image(_export_copy(fig), path, format="png", scale=2)
                    ok.append((path, name))
                except Exception:
                    failed += 1

    total_pages = 1 + len(ok)
    page_num = 1

    # Portada: banda de marca, tarjetas de métricas y lectura ejecutiva en
    # un panel blanco — mismo lenguaje visual que las páginas de gráficas.
    _pdf_page_shell(c, w, h, "Simulacro Clúster Salud", module, "Infografía general con los datos y filtros actualmente aplicados en el tablero.", f"Generado el {today_label}")
    card_top = h - _PDF_HEADER_H - 14
    metrics_h = 78
    _pdf_card(c, _PDF_SIDE, card_top - metrics_h, w - 2 * _PDF_SIDE, metrics_h)
    card_w = (w - 2 * _PDF_SIDE - 16) / max(len(metrics), 1)
    for i, (label, value) in enumerate(metrics):
        cx = _PDF_SIDE + 8 + i * card_w
        avail = card_w - 14
        # El valor y la etiqueta se ajustan al ancho real de la tarjeta (en
        # vez de un tamaño/recorte fijo): con 7 tarjetas en una fila, textos
        # largos como "Total profesionales reportados" o "USD 959.600" se
        # solapaban con la tarjeta siguiente.
        vsize = 18.0
        while vsize > 9 and stringWidth(value, "Helvetica-Bold", vsize) > avail:
            vsize -= 0.5
        c.setFillColor(colors.HexColor(NAVY)); c.setFont("Helvetica-Bold", vsize)
        c.drawString(cx, card_top - 32, value)
        words = label.upper().split(); lines: list[str] = []; line = ""
        for word in words:
            trial = (line + " " + word).strip()
            if line and stringWidth(trial, "Helvetica", 7.5) > avail:
                lines.append(line); line = word
            else:
                line = trial
        if line: lines.append(line)
        c.setFillColor(colors.HexColor(MUTED)); c.setFont("Helvetica", 7.5)
        for li, ln in enumerate(lines[:2]):
            c.drawString(cx, card_top - 48 - li * 9, ln)
    notes_top = card_top - metrics_h - 14
    notes_bottom = 46
    _pdf_card(c, _PDF_SIDE, notes_bottom, w - 2 * _PDF_SIDE, notes_top - notes_bottom)
    c.setFillColor(colors.HexColor(NAVY)); c.setFont("Helvetica-Bold", 13)
    c.drawString(_PDF_SIDE + 18, notes_top - 24, "Lectura ejecutiva")
    c.setFillColor(colors.HexColor(INK)); c.setFont("Helvetica", 9.5)
    y = notes_top - 44
    for note in notes:
        words = note.split(); line = ""
        for word in words:
            if stringWidth(line + " " + word, "Helvetica", 9.5) > w - 2 * _PDF_SIDE - 56:
                c.drawString(_PDF_SIDE + 18, y, "•  " + line); y -= 15; line = word
            else:
                line = (line + " " + word).strip()
        c.drawString(_PDF_SIDE + 18, y, "•  " + line); y -= 20
    _pdf_footer(c, w, page_num, total_pages)

    # Una página horizontal por gráfica, cada una dentro de su propio panel
    # blanco tipo tarjeta. Las gráficas en pantalla no llevan título propio
    # (el título visible es un encabezado de Streamlit aparte, fuera de la
    # figura de Plotly), así que el título de cada página se dibuja aparte
    # con reportlab (el nombre de la sección a la que pertenece la gráfica).
    for path, name in ok:
        page_num += 1
        c.showPage()
        _pdf_page_shell(c, w, h, module, name, f"Gráfica {page_num - 1} de {len(ok)} · Simulacro Clúster Salud", f"Generado el {today_label}")
        panel_top = h - _PDF_HEADER_H - 14
        panel_bottom = 46
        _pdf_card(c, _PDF_SIDE, panel_bottom, w - 2 * _PDF_SIDE, panel_top - panel_bottom)
        image = ImageReader(str(path))
        img_w, img_h = image.getSize()
        max_w, max_h = w - 2 * _PDF_SIDE - 36, panel_top - panel_bottom - 30
        scale = min(max_w / img_w, max_h / img_h)
        draw_w, draw_h = img_w * scale, img_h * scale
        img_x = _PDF_SIDE + 18 + (max_w - draw_w) / 2
        img_y = panel_bottom + 14 + (max_h - draw_h) / 2
        c.drawImage(image, img_x, img_y, width=draw_w, height=draw_h)
        _pdf_footer(c, w, page_num, total_pages)

    c.save()
    pdf_bytes = buffer.getvalue()
    tmp_dir_ctx.cleanup()
    return pdf_bytes, failed


d = prepare()
fac, reports, results, points = d["facilities"], d["reports_prepared"], d["results_joined"], d["points"]

st.markdown(f"""
<style>
  .stApp{{background:{PAPER};color:{INK}}}.block-container{{max-width:1480px;padding-top:1.1rem;padding-bottom:4rem}}
  [data-testid="stSidebar"]{{background:#F0F3F5;border-right:1px solid #D7DEE4}}
  .logo-crop{{max-width:1660px;margin:0 auto 20px;padding:0 8px;background:transparent}}
  .logo-crop img{{width:100%;height:auto;display:block;filter:contrast(1.02);}}
  .hero{{background:linear-gradient(120deg,{NAVY},{BLUE});color:white;border-radius:18px;padding:16px 28px;margin:0 0 14px;box-shadow:0 12px 26px #17365d22}}
  .hero b{{font-size:.72rem;letter-spacing:.16em}}.hero h1{{font-size:2.3rem;margin:.35rem 0 .25rem;color:white}}.hero p{{font-size:1rem;margin:0;color:#E9F1F7}}
  .hero small{{display:block;margin-top:.3rem;color:#C9DCEC;font-size:.82rem}}
  .module-head{{border-left:6px solid {YELLOW};background:white;padding:14px 18px;border-radius:8px;margin:18px 0}}
  .module-head h2{{margin:0;color:{NAVY};font-size:1.55rem}}.module-head p{{margin:4px 0 0;color:{MUTED}}}
  .section-band{{display:flex;align-items:center;flex-wrap:wrap;gap:.35rem .65rem;background:{PALE};border:1px solid #C9DFEC;border-left:5px solid {NAVY};color:{NAVY};border-radius:9px;padding:.6rem .85rem;margin:.9rem 0 .65rem;font-weight:800;font-size:.92rem;letter-spacing:.02em}}
  .section-band span{{color:{MUTED};font-weight:500;font-style:italic;font-size:.88rem;letter-spacing:0}}
  .metric-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin:12px 0 22px}}
  .metric{{background:white;border:1px solid #D9E0E5;border-top:4px solid {BLUE};padding:14px 16px;min-height:106px;text-align:center}}
  .metric span{{display:block;text-transform:uppercase;font-size:.68rem;letter-spacing:.08em;color:{MUTED};font-weight:700}}
  .metric strong{{display:block;font-size:2rem;color:{NAVY};margin:.2rem 0}}.metric small{{color:{MUTED}}}
  .pilot{{background:#FFF6D8;border:1px solid #E7CF72;border-radius:8px;padding:10px 13px;color:#67551C;font-size:.85rem}}
  .index-label{{color:{MUTED};font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;margin:10px 0 8px}}
  .index{{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 20px}}
  .index a{{text-decoration:none;color:{NAVY};background:white;border:1px solid #CBD5DD;border-radius:999px;padding:7px 12px;font-weight:700;font-size:.82rem}}
  .index a::before{{content:"↓ ";color:{BLUE}}}
  .ext-links{{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 20px}}
  .ext-links a{{text-decoration:none;color:{NAVY};background:white;border:1px solid #CBD5DD;border-radius:999px;padding:7px 12px;font-weight:700;font-size:.82rem}}
  .ext-links a::after{{content:" ↗";color:{BLUE}}}
  .sidebar-nav{{display:flex;gap:8px;margin:4px 0 16px}}
  .sidebar-nav a{{flex:1;text-decoration:none;text-align:center;color:{NAVY};background:white;border:1px solid #CBD5DD;border-radius:8px;padding:8px 6px;font-weight:700;font-size:.78rem}}
  .sidebar-nav a:hover{{border-color:{BLUE};color:{BLUE}}}
  .section-kicker{{color:{RED};text-transform:uppercase;letter-spacing:.11em;font-size:.7rem;font-weight:800;margin-top:22px}}
  .section-rule{{border-top:1px solid #C8C1B4;margin:24px 0 8px}}
  [data-testid="stRadio"] label p{{color:{INK}!important;font-weight:700}}
  h2,h3{{color:{NAVY}}}
  .block-container h2{{font-size:1.9rem!important;line-height:1.23;font-weight:650}}
  .block-container h3{{font-size:1.45rem!important;line-height:1.28;font-weight:650}}
  .block-container h4{{font-size:1.16rem!important;line-height:1.3;font-weight:600}}
  .block-container .module-head h2{{font-size:1.55rem!important}}
  .stTabs [data-baseweb="tab-list"]{{gap:1.2rem;border-bottom:1px solid #AEBBC5}}
  .stTabs [aria-selected="true"]{{border-bottom:4px solid {RED}}}
  @media print{{[data-testid="stSidebar"],header,[data-testid="stToolbar"],.stRadio,.stDownloadButton{{display:none!important}}.block-container{{max-width:none;padding:0}}.hero{{box-shadow:none}}.print-break{{break-before:page}}}}
  @media(max-width:900px){{.metric-row{{grid-template-columns:repeat(2,1fr)}}}}
</style>
""", unsafe_allow_html=True)

st.markdown('<div id="top"></div>', unsafe_allow_html=True)
logo = img_data(ROOT / "logos_formularios_hd.png")
if logo:
    st.markdown(f'<div class="logo-crop"><img src="data:image/png;base64,{logo}" alt="Logotipos de las organizaciones participantes"></div>', unsafe_allow_html=True)
report_dates = pd.concat([
    pd.to_datetime(fac["fecha_reporte"], errors="coerce"),
    pd.to_datetime(reports["fecha_reporte"], errors="coerce"),
]).dropna()
date_floor, date_ceiling = (
    (report_dates.min().date(), report_dates.max().date())
    if not report_dates.empty
    else (pd.Timestamp.today().date(), pd.Timestamp.today().date())
)
hero_today_label = pd.Timestamp.today().strftime("%d/%m/%Y")
st.markdown(f'<div class="hero"><b>OPS/OMS · CLÚSTER DE SALUD · VENEZUELA</b><h1>Tablero de la Respuesta en Salud</h1><p>Presencia operativa, programación de actividades y resultados reportados</p><small>Fecha de consulta: {hero_today_label} · Periodo de reportes: {date_floor.strftime("%d/%m/%Y")} – {date_ceiling.strftime("%d/%m/%Y")}</small></div>', unsafe_allow_html=True)

module_options=["Registro de organizaciones e intervenciones", "Reportes periódicos"]
module_index=1 if st.query_params.get("vista")=="reportes" else 0
module = st.radio("Vista principal", module_options, index=module_index, horizontal=True, label_visibility="collapsed")

states = sorted(set(points["estado"].dropna()) | set(reports["estado"].dropna()))
orgs = sorted(set(points["organizacion"].dropna()) | set(reports["organizacion"].dropna()))
parishes = sorted(set(points["parroquia"].dropna()) | set(d["places"].get("parroquia",pd.Series(dtype="object")).dropna()))
st.sidebar.markdown("### Filtros")
state = st.sidebar.selectbox("Estado", ["Todos"] + states)
org = st.sidebar.selectbox("Organización", ["Todas"] + orgs)
parish = st.sidebar.selectbox("Parroquia", ["Todas"] + parishes)
focus = st.sidebar.selectbox("Foco", ["Ambos", "Establecimiento de salud", "Salud pública"])
# Barra deslizable (tipo timelapse) en vez de un selector de fecha, para
# poder estirarla hacia atrás o adelante y consultar también el pasado.
# Por defecto muestra solo los últimos 30 días CON datos (lo más parecido a
# "activo ahora" que permite este set de reportes históricos) — se angosta
# o se amplía hasta el inicio del período para ver el acumulado completo.
default_start = max(date_floor, date_ceiling - timedelta(days=30))
date_start, date_end = st.sidebar.slider(
    "Rango de fecha del reporte",
    min_value=date_floor,
    max_value=date_ceiling,
    value=(default_start, date_ceiling),
    format="DD/MM/YYYY",
)
nav_buttons = '<div class="sidebar-nav"><a href="#top">Inicio</a>'
if module.startswith("Registro"):
    nav_buttons += '<a href="#calendario-f01">Calendario</a>'
nav_buttons += '</div>'
st.sidebar.markdown(nav_buttons, unsafe_allow_html=True)

def filt(frame: pd.DataFrame, has_focus: bool = False) -> pd.DataFrame:
    out=frame.copy()
    if state != "Todos" and "estado" in out: out=out[out["estado"].eq(state)]
    if org != "Todas" and "organizacion" in out: out=out[out["organizacion"].eq(org)]
    if parish != "Todas" and "parroquia" in out: out=out[out["parroquia"].fillna("No reportada").eq(parish)]
    if has_focus and focus != "Ambos" and "foco" in out: out=out[out["foco"].eq(focus)]
    if "fecha_reporte" in out:
        report_day = pd.to_datetime(out["fecha_reporte"], errors="coerce").dt.date
        out = out[report_day.isna() | ((report_day >= date_start) & (report_day <= date_end))]
    return out


if module.startswith("Registro"):
    section_figures: dict[str, list[go.Figure]] = {}
    p=filt(points,True)
    # Universo de socios: el registro de organizaciones (con modalidad,
    # donantes y personal) es la fuente correcta para "Socios", no el listado
    # de puntos de intervención (que solo cubre presencia física). Se calcula
    # una sola vez aquí para que la tarjeta resumen y la sección "Socios" usen
    # siempre el mismo número. "Vigente" ahora se evalúa contra el rango de
    # la barra de fecha (no contra "hoy" fijo): así el número de socios se
    # mueve junto con el resto de las gráficas al angostar o ampliar el
    # rango, en vez de quedar fijo mientras todo lo demás sí cambia.
    window_start,window_end=pd.Timestamp(date_start),pd.Timestamp(date_end)
    staffing=filt(d["places"].copy()); staffing=staffing[(staffing.desde.isna()|staffing.desde.le(window_end))&(staffing.hasta.isna()|staffing.hasta.ge(window_start))]; all_staff_cols=[c for c in staffing.columns if c.startswith("personal_")]
    active_places=staffing
    # El calendario es exclusivo de las brigadas de Salud pública, para que los
    # socios vean dónde estarán otros y no dupliquen esfuerzos; no depende del
    # filtro de Foco de la barra lateral.
    cal=filt(d["calendar"],False); cal=cal[cal.foco.eq("Salud pública")]
    f01_report_dates=pd.concat([pd.to_datetime(d["facilities"].fecha_reporte,errors="coerce"),pd.to_datetime(d["reports_prepared"].fecha_reporte,errors="coerce")]).dropna()
    first_f01=f01_report_dates.min().strftime("%d/%m/%Y"); last_f01=f01_report_dates.max().strftime("%d/%m/%Y")
    section_band("SOCIOS Y APOYOS DEL CLÚSTER SALUD", "¿Quiénes son los socios, qué ofrecen, a qué establecimientos de salud apoyan y qué acciones de salud pública realizan?")
    st.markdown('<div class="index-label">Ir a la sección</div><div class="index"><a href="#mapa-f01">01 · Mapa</a><a href="#calendario-f01">02 · Calendario</a><a href="#socios-f01">03 · Socios</a><a href="#territorio-f01">04 · Alcance territorial</a><a href="#apoyos-establecimientos-f01">05 · Apoyos a establecimientos</a><a href="#acciones-publicas-f01">06 · Acciones de salud pública</a><a href="#capacidad-f01">07 · Capacidad operativa</a><a href="#inversion-f01">08 · Inversión</a><a href="#donantes-f01">09 · Fuentes de apoyo</a></div>',unsafe_allow_html=True)
    professional_total=pd.to_numeric(staffing[all_staff_cols].stack(),errors="coerce").sum()
    parish_count=p.loc[p.parroquia.ne("No reportada"),"parroquia"].nunique()
    cards=[
        ("Estados",num(p.estado.nunique()),""),
        ("Municipios",num(p.municipio.nunique()),""),
        ("Parroquias",num(parish_count),""),
        ("Socios",num(p.organizacion.nunique()),""),
        ("Puntos de intervención",num(p.id_punto.nunique()),""),
        ("Total profesionales reportados",num(professional_total),""),
        ("Inversión registrada",f"USD {num(p.inversion_usd.sum())}",""),
    ]
    st.markdown('<h3>Panorama general de la oferta vigente</h3>',unsafe_allow_html=True)
    st.markdown('<div class="metric-row" style="grid-template-columns:repeat(7,1fr)">'+''.join(metric(*x) for x in cards)+'</div>',unsafe_allow_html=True)
    st.markdown('<div id="mapa-f01" class="section-kicker">01 · Localización</div><h2>Mapa de intervenciones registradas</h2>',unsafe_allow_html=True)
    mapped=p.dropna(subset=["latitud","longitud"])
    if mapped.empty: st.info("No hay coordenadas para estos filtros.")
    else:
        mapped=mapped.copy(); mapped["foco_formulario"]=mapped.foco.map({"Establecimiento de salud":"Acciones en el establecimiento de salud","Salud pública":"Acciones de salud pública"})
        map_focus_colors={"Acciones en el establecimiento de salud":RED,"Acciones de salud pública":YELLOW}
        fig=px.scatter_map(mapped,lat="latitud",lon="longitud",color="foco_formulario",size="inversion_usd",size_max=19,hover_name="lugar",hover_data={"organizacion":True,"estado":True,"municipio":True,"inversion_usd":':$,.0f'},color_discrete_map=map_focus_colors,map_style="carto-positron",zoom=8.7,center={"lat":10.30,"lon":-66.98},title="",labels={"foco_formulario":"Foco de intervención"})
        fig.update_traces(marker={"opacity":.88})
        # Plotly no permite fijar un zoom mínimo (solo "bounds" para acotar el
        # panning/zoom-out a una región): no existe forma de bloquear el
        # zoom-out y dejar solo zoom-in con esta librería. Como mitigación se
        # acota la vista a la región de los puntos (con margen), así no se
        # puede alejar hasta ver, por ejemplo, todo el planeta. Se usa una
        # caja de sanidad (Venezuela continental) para descartar solo
        # coordenadas realmente erróneas antes de tomar min/max — con
        # percentiles se recortaban puntos reales del extremo este (p. ej.
        # Oritapo/Carúpano) que sí deben verse.
        sane=mapped[mapped.latitud.between(-2,14)&mapped.longitud.between(-76,-58)]
        ref=sane if not sane.empty else mapped
        lat_lo,lat_hi=ref.latitud.min(),ref.latitud.max()
        lon_lo,lon_hi=ref.longitud.min(),ref.longitud.max()
        lat_pad=max(0.2,(lat_hi-lat_lo)*0.25); lon_pad=max(0.2,(lon_hi-lon_lo)*0.25)
        fig.update_layout(map=dict(bounds=dict(
            west=max(-180,lon_lo-lon_pad), east=min(180,lon_hi+lon_pad),
            south=max(-90,lat_lo-lat_pad), north=min(90,lat_hi+lat_pad),
        )))
        # La leyenda muestra cuántos puntos hay de cada color (no solo el
        # nombre del foco), para dar más detalle sin agregar otro elemento
        # aparte del mapa.
        focus_counts=mapped.foco_formulario.value_counts()
        fig.for_each_trace(lambda t: t.update(name=f"{t.name} ({num(focus_counts.get(t.name,0))})"))
        plot(fig,590,"f01_map",is_map=True)
        section_figures.setdefault("Resumen territorial", []).append(fig)
        st.caption("El color distingue el foco y el tamaño del punto representa la inversión registrada en USD. En este simulacro, los montos son ilustrativos. El zoom con la rueda del mouse está desactivado para evitar cambios accidentales al hacer scroll de la página; use los botones +/- del mapa.")

    st.markdown('<div id="calendario-f01" class="section-rule"></div><div class="section-kicker">02 · Agenda operativa</div><h2>Calendario de brigadas de salud pública · {}</h2>'.format(CALENDAR_YEAR),unsafe_allow_html=True)
    # Fuera de un expander a propósito: el clic para seleccionar un día
    # (on_select) dejaba de funcionar cuando este calendario quedaba anidado
    # dentro de un st.expander.
    cal_f=filt(cal,False)
    if cal_f.empty:
        st.info("No hay brigadas vigentes para los filtros seleccionados (solo se muestran puntos con fecha de inicio real declarada en el F01).")
    else:
        month_abbr_es={1:"Ene",2:"Feb",3:"Mar",4:"Abr",5:"May",6:"Jun",7:"Jul",8:"Ago",9:"Sep",10:"Oct",11:"Nov",12:"Dic"}
        weekday_abbr_es=["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]

        def _fmt_date_es(value: pd.Timestamp) -> str:
            return f"{value.day:02d} {month_abbr_es[value.month]} {value.year}"

        def _shift_pick(key: str, options: list, delta: int) -> None:
            current=st.session_state.get(key, options[0])
            idx=options.index(current) if current in options else 0
            st.session_state[key]=options[min(max(idx+delta, 0), len(options)-1)]

        def _calendar_heatmap(grid_df: pd.DataFrame, x_col: str, y_col: str, x_order: list, y_order: list, cmax: int, height: int, key: str, show_text: bool = True, hide_yaxis: bool = False):
            z=grid_df.pivot(index=y_col, columns=x_col, values="actividades").reindex(index=y_order, columns=x_order)
            iso=grid_df.pivot(index=y_col, columns=x_col, values="fecha_iso").reindex(index=y_order, columns=x_order)
            tickvals,ticktext=integer_colorbar_ticks(cmax)
            xi=list(range(len(x_order))); yi=list(range(len(y_order)))
            # go.Heatmap no dispara eventos de selección de clic en
            # Streamlit (on_select solo funciona con scatter/bar/histogram),
            # por eso "seleccionar una fecha" nunca abría el detalle. Se
            # dibuja la cuadrícula con marcadores cuadrados de go.Scatter en
            # su lugar: mismo aspecto visual (color por intensidad), pero sí
            # es clicable.
            cell_px=max(24,min(68,int(700/max(len(x_order),1))))
            cell_xs,cell_ys,cell_colors,cell_cds,cell_counts=[],[],[],[],[]
            for r in range(len(y_order)):
                for c in range(len(x_order)):
                    zv=z.values[r][c]
                    if pd.isna(zv):
                        continue
                    cell_xs.append(c); cell_ys.append(r); cell_colors.append(zv)
                    cell_cds.append(iso.values[r][c]); cell_counts.append(str(int(zv)))
            fig=go.Figure()
            # Franja de fin de semana (sáb/dom) sombreada detrás de la
            # cuadrícula, como en un calendario real — un detalle sutil que
            # ayuda a ubicarse de un vistazo, no solo por el encabezado.
            weekend_cols=[i for i,t in enumerate(x_order) if t in ("Sáb","Dom")]
            for wc in weekend_cols:
                fig.add_shape(type="rect", x0=wc-0.5, x1=wc+0.5, y0=-0.6, y1=len(y_order)-0.4,
                              fillcolor="#EAE3D2", opacity=0.5, line_width=0, layer="below")
            # Degradado de 4 tonos (en vez de 2 planos) para dar más
            # profundidad visual, y "grout lines" color papel más gruesas
            # entre celdas para que la cuadrícula se vea como una tarjeta,
            # no como bloques pegados.
            fig.add_trace(go.Scatter(
                x=cell_xs, y=cell_ys, mode="markers",
                marker=dict(symbol="square", size=cell_px, color=cell_colors, cmin=0, cmax=cmax,
                            colorscale=[[0, "#FBF8EE"], [0.35, "#F1DFA0"], [0.7, "#E7C662"], [1, "#D9A62E"]], line=dict(width=4, color=PAPER),
                            colorbar=dict(title=dict(text="Actividades",font=dict(size=13,color=MUTED)), thickness=16, tickfont=dict(size=13), tickmode="array", tickvals=tickvals, ticktext=ticktext, outlinewidth=0)),
                customdata=cell_cds, text=cell_counts,
                hovertemplate="%{customdata}<br>Actividades: %{text}<extra></extra>",
                showlegend=False,
            ))
            if show_text:
                day=grid_df.pivot(index=y_col, columns=x_col, values="dia").reindex(index=y_order, columns=x_order)
                xs,day_ys,count_ys,cds,day_text,count_text=[],[],[],[],[],[]
                for r in range(len(y_order)):
                    for c in range(len(x_order)):
                        dv=day.values[r][c]
                        if pd.isna(dv):
                            continue
                        cv=z.values[r][c]
                        xs.append(c); day_ys.append(r+0.34); count_ys.append(r-0.08)
                        cds.append(iso.values[r][c])
                        day_text.append(str(int(dv)))
                        count_text.append(str(int(cv)) if pd.notna(cv) and cv>0 else "")
                fig.add_trace(go.Scatter(x=xs, y=day_ys, mode="text", text=day_text,
                                          textfont=dict(size=10, color=RED), customdata=cds,
                                          hoverinfo="skip", showlegend=False))
                fig.add_trace(go.Scatter(x=xs, y=count_ys, mode="text", text=count_text,
                                          textfont=dict(size=22, color=INK), customdata=cds,
                                          hoverinfo="skip", showlegend=False))
            fig.update_xaxes(tickmode="array", tickvals=xi, ticktext=[f"<b>{t}</b>" for t in x_order], side="top", title=None, showgrid=False, zeroline=False, tickfont=dict(size=13,color=MUTED), range=[-0.6,len(x_order)-0.4])
            fig.update_yaxes(tickmode="array", tickvals=yi, ticktext=[f"<b>{t}</b>" for t in y_order], showgrid=False, zeroline=False, tickfont=dict(size=13,color=MUTED), range=[len(y_order)-0.4,-0.6])
            if hide_yaxis:
                fig.update_yaxes(visible=False)
            fig.update_layout(height=height, margin=dict(l=16,r=16,t=48,b=8), paper_bgcolor=PAPER, plot_bgcolor=PAPER, font=dict(family="Arial", color=INK, size=13))
            event=st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key=key, on_select="rerun", selection_mode="points")
            return fig, event

        st.caption(f"Cubre los {num(cal_f.id_punto.nunique())} puntos de salud pública que declararon fecha de inicio real de la intervención en el F01 (dato tomado de puntos_atencion.csv, no estimado); por eso no cubre todo el universo de puntos del resumen.")

        calendar_view=st.radio("Vista del calendario",["Anual","Mensual","Semanal","Diario"],horizontal=True,key="calendar_view_mode",index=1)
        selected_date=None
        selection_points=[]

        if calendar_view=="Anual":
            grid=cal_f.copy(); grid["month_period"]=grid.fecha.dt.to_period("M"); grid["dia"]=grid.fecha.dt.day
            months=sorted(grid["month_period"].unique()) or list(pd.period_range(f"{CALENDAR_YEAR}-01", f"{CALENDAR_YEAR}-12", freq="M"))
            cell_rows=[]
            for m in months:
                month_label=month_abbr_es[m.month]
                for dday in range(1, m.days_in_month+1):
                    date_val=pd.Timestamp(year=m.year, month=m.month, day=dday)
                    count=int(grid.loc[grid.month_period.eq(m) & grid.dia.eq(dday)].shape[0])
                    cell_rows.append({"mes":month_label,"dia":dday,"fecha_iso":date_val.strftime("%Y-%m-%d"),"actividades":count})
            grid_df=pd.DataFrame(cell_rows)
            month_order=[month_abbr_es[m.month] for m in months]
            day_order=list(range(1,32))
            cmax=max(int(grid_df["actividades"].max()) if not grid_df.empty else 0, 1)
            fig,event=_calendar_heatmap(grid_df,"dia","mes",day_order,month_order,cmax,min(480,90+30*len(months)),"calendar_grid_annual",show_text=False)
            section_figures.setdefault("Calendarios", []).append(fig)
            st.caption(f"Cada fila es un mes y cada columna un día del mes; el color indica cuántos puntos tienen una brigada vigente ese día (pase el mouse para ver el número exacto). Haga clic en un día para ver el detalle.")
            selection_points=event.selection.points if event and event.selection else []
            if selection_points:
                selected_date=pd.Timestamp(selection_points[0]["customdata"])
            active_days=sorted(grid_df.loc[grid_df.actividades.gt(0), "fecha_iso"].unique())
            day_labels={iso: _fmt_date_es(pd.Timestamp(iso)) for iso in active_days}
            picked_iso=st.selectbox("O elija una fecha con actividad",options=["— Seleccionar —", *active_days],format_func=lambda iso: day_labels.get(iso, iso),key="calendar_date_picker_annual")
            if selected_date is None and picked_iso != "— Seleccionar —":
                selected_date=pd.Timestamp(picked_iso)

        elif calendar_view=="Mensual":
            all_months=list(pd.period_range(f"{CALENDAR_YEAR}-01", f"{CALENDAR_YEAR}-12", freq="M"))
            months_with_activity=set(cal_f.fecha.dt.to_period("M").unique())
            month_labels={m: f"{month_abbr_es[m.month]} {m.year}" for m in all_months}
            # Por defecto se abre en el mes actual (no en el primero con
            # actividad): es el calendario de "ahora", no un archivo histórico.
            today_month=pd.Timestamp.today().to_period("M")
            default_month_index=next((i for i,m in enumerate(all_months) if m==today_month), next((i for i,m in enumerate(all_months) if m in months_with_activity), 0))
            nav_l,nav_mid,nav_r=st.columns([1,6,1])
            with nav_l: st.button("◀",key="cal_month_prev",on_click=_shift_pick,args=("calendar_month_picker",all_months,-1))
            with nav_r: st.button("▶",key="cal_month_next",on_click=_shift_pick,args=("calendar_month_picker",all_months,1))
            with nav_mid:
                picked_month=st.selectbox(
                    "Mes",options=all_months,
                    format_func=lambda m: month_labels[m]+(" · con actividad" if m in months_with_activity else ""),
                    index=default_month_index,key="calendar_month_picker",
                )
            days_in_month=picked_month.days_in_month
            first_weekday=pd.Timestamp(year=picked_month.year,month=picked_month.month,day=1).weekday()
            month_rows=cal_f[cal_f.fecha.dt.to_period("M").eq(picked_month)]
            month_counts=month_rows.groupby(month_rows.fecha.dt.day).size()
            cell_rows=[]
            for dday in range(1, days_in_month+1):
                date_val=pd.Timestamp(year=picked_month.year,month=picked_month.month,day=dday)
                week_of_month=(dday-1+first_weekday)//7
                count=int(month_counts.get(dday,0))
                cell_rows.append({"dia":dday,"weekday":weekday_abbr_es[date_val.weekday()],"semana":f"Semana {week_of_month+1}","semana_idx":week_of_month,"fecha_iso":date_val.strftime("%Y-%m-%d"),"actividades":count})
            grid_df=pd.DataFrame(cell_rows)
            n_weeks=int(grid_df["semana_idx"].max())+1
            week_order=[f"Semana {i+1}" for i in range(n_weeks)]
            cmax=max(int(grid_df["actividades"].max()) if not grid_df.empty else 0, 1)
            fig,event=_calendar_heatmap(grid_df,"weekday","semana",weekday_abbr_es,week_order,cmax,min(420,130+56*n_weeks),"calendar_grid",hide_yaxis=True)
            section_figures.setdefault("Calendarios", []).append(fig)
            st.caption("El número grande es la cantidad de puntos con una brigada vigente ese día (el chico, el día del mes). Haga clic en un día para ver el detalle automáticamente.")
            selection_points=event.selection.points if event and event.selection else []
            if selection_points:
                selected_date=pd.Timestamp(selection_points[0]["customdata"])

            active_days=sorted(grid_df.loc[grid_df.actividades.gt(0), "fecha_iso"].unique())
            day_labels={iso: _fmt_date_es(pd.Timestamp(iso)) for iso in active_days}
            picked_iso=st.selectbox(
                "O elija una fecha con actividad de este mes",
                options=["— Seleccionar —", *active_days],
                format_func=lambda iso: day_labels.get(iso, iso),
                key="calendar_date_picker_month",
            )
            if selected_date is None and picked_iso != "— Seleccionar —":
                selected_date=pd.Timestamp(picked_iso)

        elif calendar_view=="Semanal":
            weeks=list(pd.period_range(start=f"{CALENDAR_YEAR}-01-01", end=f"{CALENDAR_YEAR}-12-31", freq="W-SUN"))
            week_labels={w: f"{w.start_time.strftime('%d/%m')} – {w.end_time.strftime('%d/%m')}" for w in weeks}
            cal_week_period=cal_f.fecha.dt.to_period("W-SUN")
            weeks_with_activity=set(cal_week_period.unique())
            # Por defecto, la semana actual (no la primera con actividad).
            today_week=pd.Timestamp.today().to_period("W-SUN")
            default_index=next((i for i,w in enumerate(weeks) if w==today_week), next((i for i,w in enumerate(weeks) if w in weeks_with_activity), 0))
            nav_l,nav_mid,nav_r=st.columns([1,6,1])
            with nav_l: st.button("◀",key="cal_week_prev",on_click=_shift_pick,args=("calendar_week_picker",weeks,-1))
            with nav_r: st.button("▶",key="cal_week_next",on_click=_shift_pick,args=("calendar_week_picker",weeks,1))
            with nav_mid:
                picked_week=st.selectbox(
                    "Semana",options=weeks,
                    format_func=lambda w: week_labels[w]+(" · con actividad" if w in weeks_with_activity else ""),
                    index=default_index,key="calendar_week_picker",
                )
            week_days=pd.date_range(picked_week.start_time.normalize(), picked_week.end_time.normalize())
            week_rows=cal_f[cal_week_period.eq(picked_week)]
            week_counts=week_rows.groupby(week_rows.fecha.dt.date).size()
            week_df=pd.DataFrame({"fecha": week_days})
            week_df["dia"]=week_df.fecha.dt.day
            week_df["dia_semana"]=[weekday_abbr_es[dt.weekday()] for dt in week_df.fecha]
            week_df["actividades"]=[int(week_counts.get(dt.date(),0)) for dt in week_df.fecha]
            week_df["fecha_iso"]=week_df.fecha.dt.strftime("%Y-%m-%d")
            week_df["fila"]="Actividades"
            cmax=max(int(week_df["actividades"].max()) if not week_df.empty else 0, 1)
            fig,event=_calendar_heatmap(week_df,"dia_semana","fila",weekday_abbr_es,["Actividades"],cmax,240,"calendar_week_grid",hide_yaxis=True)
            section_figures.setdefault("Calendarios", []).append(fig)
            st.caption(f"Semana del {week_labels[picked_week]}. El número grande es la cantidad de puntos con una brigada vigente ese día (el chico, el día del mes). Haga clic en un día para ver el detalle.")
            selection_points=event.selection.points if event and event.selection else []
            default_day_index=int(week_df["actividades"].to_numpy().argmax()) if not week_df.empty else 0
            picked_day=st.selectbox("O elija un día de la semana",options=list(week_df.fecha),format_func=_fmt_date_es,index=default_day_index,key="calendar_day_picker_week")
            if selection_points:
                selected_date=pd.Timestamp(selection_points[0]["customdata"])
            else:
                selected_date=picked_day

        else:  # Diario
            all_days=list(pd.date_range(f"{CALENDAR_YEAR}-01-01", f"{CALENDAR_YEAR}-12-31"))
            active_days_ts=sorted(cal_f.fecha.unique())
            # Por defecto, el día de hoy (no el primero con actividad).
            today_norm=pd.Timestamp.today().normalize()
            if today_norm in all_days:
                default_index=all_days.index(today_norm)
            elif active_days_ts:
                default_index=all_days.index(pd.Timestamp(active_days_ts[0]))
            else:
                default_index=0
            nav_l,nav_mid,nav_r=st.columns([1,6,1])
            with nav_l: st.button("◀",key="cal_day_prev",on_click=_shift_pick,args=("calendar_day_picker",all_days,-1))
            with nav_r: st.button("▶",key="cal_day_next",on_click=_shift_pick,args=("calendar_day_picker",all_days,1))
            with nav_mid:
                selected_date=st.selectbox("Seleccione un día",options=all_days,format_func=_fmt_date_es,index=default_index,key="calendar_day_picker")

        if selected_date is not None:
            detail=cal_f[cal_f.fecha.eq(selected_date)]
            if calendar_view=="Diario":
                day_cards=[
                    ("Actividades",num(len(detail)),""),
                    ("Organizaciones",num(detail.organizacion.nunique()),""),
                    ("Lugares",num(detail.lugar.nunique()),""),
                    ("Estados",num(detail.estado.nunique()),""),
                ]
                st.markdown('<div class="metric-row" style="grid-template-columns:repeat(4,1fr)">'+''.join(metric(*x) for x in day_cards)+'</div>',unsafe_allow_html=True)
            st.markdown(f"#### Actividades del {_fmt_date_es(selected_date)}")
            if detail.empty:
                st.info("No hay actividades registradas para esta fecha con los filtros actuales.")
            else:
                st.dataframe(
                    detail[["organizacion","estado","municipio","lugar","actividad","jornada"]].rename(columns={"organizacion":"Organización","estado":"Estado","municipio":"Municipio","lugar":"Lugar","actividad":"Actividad","jornada":"Jornada"}),
                    hide_index=True, width="stretch",
                )
        else:
            st.info("Haga clic en un día del calendario para ver el detalle de organizaciones y actividades planificadas.")

    st.markdown(f'<div id="socios-f01" class="section-kicker">03 · Socios</div><h2>Organizaciones y su alcance · {num(p.organizacion.nunique())} socios · {num(p.id_punto.nunique())} puntos</h2>',unsafe_allow_html=True)
    st.markdown("### Principales socios por cantidad de puntos")
    # Barras horizontales apiladas por foco (rojo/amarillo, igual que el
    # mapa): reemplaza el treemap anterior — con esto solo ya se ve tanto el
    # ranking de socios como su distribución por foco en una sola gráfica,
    # así que no hace falta una segunda figura aparte para lo mismo.
    # Se usan los valores reales de "points" (formularios llenados) tal
    # cual, sin acotar al universo de socios con vigencia activa del
    # encabezado — son fuentes distintas por diseño (ver comentario en
    # active_places) y por ahora se muestra cada una con su propio dato real.
    org_points=p.assign(foco_formulario=p.foco.map({"Establecimiento de salud":"Acciones en el establecimiento de salud","Salud pública":"Acciones de salud pública"})).groupby(["foco_formulario","organizacion"]).id_punto.nunique().reset_index(name="puntos")
    if org_points.empty:
        st.info("No hay organizaciones para los filtros seleccionados.")
    else:
        org_order=org_points.groupby("organizacion").puntos.sum().nlargest(20).sort_values(ascending=False).index.tolist()
        # Nombres largos ("OIM - Organización Internacional para las
        # Migraciones") empujaban el área de barras muy a la derecha,
        # dejándolas angostas. Se recortan solo para la etiqueta del eje
        # (el nombre completo se sigue viendo en el hover) para que la
        # gráfica arranque más a la izquierda y las barras aprovechen casi
        # todo el ancho disponible.
        short_name=lambda n: n if len(n)<=22 else n[:21].rstrip()+"…"

        def _orgs_bar(names: list, key: str):
            rows=org_points[org_points.organizacion.isin(names)].copy()
            rows["organizacion_corta"]=rows.organizacion.map(short_name)
            # Segmentos de 2 puntos o menos no llevan número: a ese tamaño
            # el valor ya se lee por el tono/ancho de la franja y el rótulo
            # solo agrega ruido visual.
            rows["puntos_label"]=rows.puntos.map(lambda v: str(v) if v>2 else "")
            order=[short_name(n) for n in names]
            fig=px.bar(rows,x="puntos",y="organizacion_corta",color="foco_formulario",orientation="h",barmode="stack",text="puntos_label",
                       custom_data=["organizacion"],
                       category_orders={"organizacion_corta":order},
                       color_discrete_map={"Acciones en el establecimiento de salud":RED,"Acciones de salud pública":YELLOW},
                       labels={"puntos":"Puntos de intervención","organizacion_corta":"","foco_formulario":"Foco de intervención"})
            fig.update_traces(textposition="inside",textfont=dict(size=14,color=INK),constraintext="none",cliponaxis=False,
                              hovertemplate="%{customdata[0]}<br>%{x} puntos<extra></extra>")
            fig.update_layout(legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="right",x=1),bargap=0.35,
                              xaxis=dict(tickfont=dict(size=13)),yaxis=dict(tickfont=dict(size=14),automargin=True))
            plot(fig,max(420,54*len(names)+100),key,margin=dict(l=4,r=54,t=52,b=18))
            section_figures.setdefault("Presencia de socios", []).append(fig)

        top10,rest=org_order[:10],org_order[10:]
        _orgs_bar(top10,"top_orgs_overall_bar")
        if rest:
            with st.expander(f"Ver los {len(rest)} socios restantes"):
                _orgs_bar(rest,"rest_orgs_overall_bar")
    st.caption("Top 10 organizaciones por total de puntos de intervención, ordenadas de mayor a menor y apiladas por foco de intervención. Los segmentos de 2 puntos o menos no muestran número.")

    st.markdown("### Distribución de intervenciones por foco")
    st.markdown("#### Puntos registrados por foco de intervención")
    point_distribution=p.assign(foco_formulario=p.foco.map({"Establecimiento de salud":"Acciones en el establecimiento de salud","Salud pública":"Acciones de salud pública"})).groupby("foco_formulario").id_punto.nunique().reset_index(name="puntos")
    fig=px.pie(point_distribution,names="foco_formulario",values="puntos",hole=.58,color="foco_formulario",color_discrete_map={"Acciones en el establecimiento de salud":RED,"Acciones de salud pública":YELLOW},labels={"foco_formulario":"Foco de intervención","puntos":"Puntos registrados"})
    fig.update_traces(textinfo="label+value+percent",textposition="outside",pull=[.02,.02],marker_line_color=PAPER,marker_line_width=3)
    fig.update_layout(showlegend=False); plot(fig,410,"f01_focus_distribution")
    section_figures.setdefault("Presencia de socios", []).append(fig)
    st.caption("El gráfico muestra cómo se distribuye el total de puntos registrados en F01 entre los dos focos de intervención.")

    st.markdown("### Composición de socios")
    comp=active_places.groupby("tipo_organizacion").organizacion.nunique().reset_index(name="socios").sort_values("socios")
    # Ancho completo y etiquetas "outside": con 6 categorías (algunas con un
    # solo socio) el donut a media columna recortaba los nombres más largos.
    fig=px.pie(comp,names="tipo_organizacion",values="socios",hole=.5,color_discrete_sequence=[NAVY,BLUE,TEAL,YELLOW,RED,"#8A6FA8"])
    fig.update_traces(textinfo="label+percent",textposition="outside",textfont=dict(size=13))
    fig.update_layout(showlegend=False)
    plot(fig,520,"partner_composition",margin=dict(l=110,r=110,t=52,b=30))
    section_figures.setdefault("Presencia de socios", []).append(fig)

    st.markdown("#### Modalidad de implementación")
    impl=active_places.groupby("modalidad_implementacion").organizacion.nunique().reset_index(name="socios").sort_values("socios")
    fig=px.bar(impl,x="socios",y="modalidad_implementacion",orientation="h",text="socios",color_discrete_sequence=[BLUE],labels={"socios":"Socios","modalidad_implementacion":""}); fig.update_traces(textposition="outside"); plot(fig,320,"implementation_mode")
    section_figures.setdefault("Presencia de socios", []).append(fig)

    st.markdown('<div id="territorio-f01" class="section-kicker">04 · Alcance territorial</div><h2>Presencia territorial de socios</h2>',unsafe_allow_html=True)
    territory=active_places.copy(); territory["parroquia"]=territory.parroquia.fillna("No reportada")
    top_states=territory.groupby("estado").organizacion.nunique().nlargest(8).sort_values().reset_index(name="socios")
    top_munis=territory.groupby(["estado","municipio"]).organizacion.nunique().nlargest(10).sort_values().reset_index(name="socios"); top_munis["territorio"]=top_munis.municipio+" · "+top_munis.estado
    tl,tr=st.columns(2)
    with tl:
        st.markdown("#### Estados con mayor presencia")
        # Color por intensidad (no un solo tono plano): la barra más larga ya
        # lo dice, pero el degradado refuerza a simple vista dónde hay más
        # presencia, sobre todo al exportar la gráfica sola al PDF.
        fig=px.bar(top_states,x="socios",y="estado",orientation="h",text="socios",color="socios",color_continuous_scale=[[0,"#CBD9E8"],[1,NAVY]],labels={"socios":"Socios activos","estado":""})
        fig.update_traces(textposition="outside"); fig.update_layout(coloraxis_showscale=False); plot(fig,390,"top_states_f01")
        section_figures.setdefault("Presencia de socios", []).append(fig)
    with tr:
        st.markdown("#### Municipios con mayor presencia")
        fig=px.bar(top_munis,x="socios",y="territorio",orientation="h",text="socios",color="socios",color_continuous_scale=[[0,"#CBE4E4"],[1,TEAL]],labels={"socios":"Socios activos","territorio":""})
        fig.update_traces(textposition="outside"); fig.update_layout(coloraxis_showscale=False); plot(fig,390,"top_munis_f01")
        section_figures.setdefault("Presencia de socios", []).append(fig)
    # Verificación explícita (no solo una nota genérica): se calcula cuántas
    # organizaciones están en más de un estado y cuánto explican esa
    # diferencia, para que la suma > total no se lea como un error de conteo.
    multi_state=territory.groupby("organizacion").estado.nunique(); multi_state=multi_state[multi_state.gt(1)]
    overcount_states=int((multi_state-1).sum())
    total_socios=active_places.organizacion.nunique()
    if not multi_state.empty:
        examples=", ".join(multi_state.sort_values(ascending=False).index[:3])
        overlap_note=f"Verificado: {len(multi_state)} organización(es) — por ejemplo {examples} — tienen presencia en más de un estado y se cuentan una vez en cada uno, lo que explica exactamente esos {overcount_states} de diferencia."
    else:
        overlap_note="Verificado: ninguna organización tiene presencia en más de un estado en los filtros actuales, por lo que la suma coincide con el total."
    st.caption(f"El eje cuenta organizaciones (socios) con presencia vigente, no puntos de intervención — la suma de las barras ({int(top_states.socios.sum())}) puede superar el total de {num(total_socios)} socios con vigencia activa (distinto del conteo de la tarjeta resumen, que cuenta socios con puntos en el período seleccionado). {overlap_note} Expanda un estado para consultar municipios, parroquias y socios con presencia vigente.")
    for state_name in sorted(territory.estado.dropna().unique()):
        state_rows=territory[territory.estado.eq(state_name)]; state_total=state_rows.organizacion.nunique()
        with st.expander(f"{state_name} · {state_total} socio(s)"):
            hierarchy=state_rows.groupby(["municipio","parroquia"])["organizacion"].apply(lambda s:", ".join(sorted(set(s)))).reset_index(name="Socios con presencia")
            hierarchy.columns=["Municipio","Parroquia","Socios con presencia"]
            st.dataframe(hierarchy,hide_index=True,width="stretch")

    # Vocabulario alineado al F01 real (grupo "Servicios y apoyos de la
    # organización": pregunta 5.1 "Seleccione las áreas o servicios del
    # establecimiento de salud que reciben apoyo" y 5.2 "tipo(s) de apoyo que
    # serán proporcionados"). El formulario nunca usa la palabra "oferta"
    # para esto — usa "apoyo".
    st.markdown('<div id="apoyos-establecimientos-f01" class="section-kicker">05 · Apoyos a establecimientos de salud</div><h2>Servicios y apoyos que las organizaciones brindan</h2>',unsafe_allow_html=True)
    facility_points_all=p[p.foco.eq("Establecimiento de salud")]
    facility_type_filter=st.selectbox("Filtrar por tipo de establecimiento de salud",["Todos"]+sorted(facility_points_all.tipo_punto.dropna().unique()),key="facility_type_filter")
    facility_points=facility_points_all[facility_points_all.tipo_punto.eq(facility_type_filter)] if facility_type_filter!="Todos" else facility_points_all
    facility_types=facility_points.groupby("tipo_punto").id_punto.nunique().reset_index(name="puntos")
    facility_orgs=facility_points.groupby("organizacion").id_punto.nunique().reset_index(name="puntos")

    # La dona ocupa menos ancho para que el cuadro de organizaciones (lo
    # más importante de esta comparación) tenga más espacio.
    facility_left,facility_right=st.columns([1.6,3.4],gap="small")
    with facility_left:
        st.markdown("#### Tipo de establecimiento de salud")
        if facility_types.empty: st.info("No hay establecimientos para los filtros seleccionados.")
        else:
            fig=px.pie(facility_types,names="tipo_punto",values="puntos",hole=.52,
                       color_discrete_sequence=[NAVY,"#548FC5","#D97732","#6B9F70","#806CA5","#91A4B4","#3568B4"],
                       labels={"tipo_punto":"Tipo de establecimiento","puntos":"Establecimientos"})
            fig.update_traces(textinfo="percent",textposition="inside",marker_line_color=PAPER,marker_line_width=3)
            # Leyenda vertical (no horizontal): con la columna angosta, una
            # leyenda en fila se salía del recuadro; en columna se lee bien
            # sin importar el ancho disponible.
            fig.update_layout(annotations=[dict(text=f"<b>{num(facility_types.puntos.sum())}</b><br>establecimientos",x=.5,y=.5,showarrow=False,font=dict(size=15,color=INK))],
                              legend=dict(orientation="v",yanchor="top",y=-.05,xanchor="center",x=.5,font=dict(size=11)))
            plot(fig,560,"facility_type_donut",margin=dict(l=12,r=12,t=10,b=160))
            section_figures.setdefault("Presencia de socios", []).append(fig)
    with facility_right:
        st.markdown("#### Organizaciones que apoyan establecimientos de salud")
        if facility_orgs.empty: st.info("No hay organizaciones para los filtros seleccionados.")
        else:
            facility_orgs["nombre_corto"]=facility_orgs.organizacion.map(tile_name)
            fig=px.treemap(facility_orgs,path=["nombre_corto"],values="puntos",color="organizacion",custom_data=["organizacion"],
                           color_discrete_sequence=["#3568B4","#9AC7F1","#E34D40","#EAA29A","#65AAA1","#9FE4AD","#F4D37D","#6E51AA","#E68A39"],
                           labels={"organizacion":"Organización","puntos":"Establecimientos"})
            fig.update_traces(hovertemplate="%{customdata[0]}<br>%{value} establecimientos<extra></extra>",
                              marker_line_color=PAPER,marker_line_width=3)
            set_org_tile_labels(fig)
            fig.update_layout(showlegend=False)
            plot(fig,590,"facility_orgs_treemap",margin=dict(l=4,r=4,t=10,b=12))
            section_figures.setdefault("Presencia de socios", []).append(fig)
            st.caption("El tamaño de cada bloque representa el número de establecimientos apoyados.")

    st.markdown("#### Áreas o servicios del establecimiento que reciben apoyo")
    fs=filt(d["facility_services_joined"])
    if facility_type_filter!="Todos": fs=fs[fs.tipo_punto.eq(facility_type_filter)]
    ft=fs.servicio.value_counts().head(10).reset_index(); ft.columns=["servicio","puntos"]; ft["foco"]="Establecimiento de salud"
    if ft.empty: st.info("No hay servicios registrados para los filtros seleccionados.")
    else:
        fig=px.treemap(ft,path=["servicio"],values="puntos",color="puntos",color_continuous_scale=[[0,"#F8D8D3"],[1,RED]])
        fig.update_traces(texttemplate="<b>%{label}</b><br>%{value} puntos",textfont_size=15); plot(fig,540,"facility_services_tree")
        section_figures.setdefault("Paquetes de apoyo", []).append(fig)

    # F01 tiene dos preguntas reales distintas para este foco (5.1 áreas que
    # reciben apoyo, arriba; 5.2 tipo de apoyo brindado, aquí abajo, en el
    # mismo orden que el formulario) — antes solo se mostraba la primera.
    st.markdown("#### Tipo de apoyo proporcionado")
    fsup=filt(d["facility_supports_joined"])
    if facility_type_filter!="Todos": fsup=fsup[fsup.tipo_punto.eq(facility_type_filter)]
    ftsup=fsup.tipo_apoyo.value_counts().head(10).reset_index(); ftsup.columns=["tipo_apoyo","puntos"]
    if ftsup.empty: st.info("No hay tipos de apoyo registrados para los filtros seleccionados.")
    else:
        fig=px.treemap(ftsup,path=["tipo_apoyo"],values="puntos",color="puntos",color_continuous_scale=[[0,"#F8D8D3"],[1,RED]])
        fig.update_traces(texttemplate="<b>%{label}</b><br>%{value} puntos",textfont_size=15); plot(fig,540,"facility_supports_tree")
        section_figures.setdefault("Paquetes de apoyo", []).append(fig)

    # Vocabulario alineado al F01 real: la organización "realiza acciones de
    # salud pública" en ciertas "áreas temáticas" (pregunta sobre
    # intervention_areas) — tampoco usa "oferta".
    st.markdown('<div id="acciones-publicas-f01" class="section-kicker">06 · Acciones de salud pública</div><h2>Áreas de acción y cobertura de salud pública</h2>',unsafe_allow_html=True)
    public_points_for_charts=p[p.foco.eq("Salud pública")]
    public_types=public_points_for_charts.groupby("tipo_punto").id_punto.nunique().reset_index(name="puntos")
    public_orgs=public_points_for_charts.groupby("organizacion").id_punto.nunique().reset_index(name="puntos")
    public_left,public_right=st.columns([1.6,3.4],gap="small")
    with public_left:
        st.markdown("#### Tipo de lugar de intervención")
        if public_types.empty: st.info("No hay lugares de intervención para los filtros seleccionados.")
        else:
            # Misma dona multicolor que "Tipo de establecimiento de salud",
            # en vez de barra de un solo color, para que ambos focos se
            # lean con el mismo lenguaje visual.
            fig=px.pie(public_types,names="tipo_punto",values="puntos",hole=.52,
                       color_discrete_sequence=[YELLOW,"#548FC5","#D97732","#6B9F70","#806CA5","#91A4B4","#3568B4","#C9A63C","#7F5539"],
                       labels={"tipo_punto":"Tipo de lugar","puntos":"Lugares"})
            fig.update_traces(textinfo="percent",textposition="inside",marker_line_color=PAPER,marker_line_width=3)
            fig.update_layout(annotations=[dict(text=f"<b>{num(public_types.puntos.sum())}</b><br>lugares",x=.5,y=.5,showarrow=False,font=dict(size=15,color=INK))],
                              legend=dict(orientation="v",yanchor="top",y=-.05,xanchor="center",x=.5,font=dict(size=11)))
            plot(fig,560,"public_type_donut",margin=dict(l=12,r=12,t=10,b=160))
            section_figures.setdefault("Presencia de socios", []).append(fig)
    with public_right:
        st.markdown("#### Organizaciones con acciones de salud pública")
        if public_orgs.empty: st.info("No hay organizaciones para los filtros seleccionados.")
        else:
            public_orgs["nombre_corto"]=public_orgs.organizacion.map(tile_name)
            fig=px.treemap(public_orgs,path=["nombre_corto"],values="puntos",color="organizacion",custom_data=["organizacion"],
                           color_discrete_sequence=["#3568B4","#9AC7F1","#E34D40","#EAA29A","#65AAA1","#9FE4AD","#F4D37D","#6E51AA","#E68A39"],
                           labels={"organizacion":"Organización","puntos":"Lugares"})
            fig.update_traces(hovertemplate="%{customdata[0]}<br>%{value} lugares<extra></extra>",
                              marker_line_color=PAPER,marker_line_width=3)
            set_org_tile_labels(fig)
            fig.update_layout(showlegend=False)
            plot(fig,590,"public_orgs_treemap",margin=dict(l=4,r=4,t=10,b=12))
            section_figures.setdefault("Presencia de socios", []).append(fig)
            st.caption("El tamaño de cada bloque representa el número de lugares de intervención.")

    po=filt(d["offered_joined"]); pt=po.servicio.value_counts().head(10).reset_index(); pt.columns=["servicio","puntos"]; pt["foco"]="Salud pública"
    st.markdown("#### Áreas temáticas de acciones de salud pública")
    if pt.empty: st.info("No hay acciones registradas para los filtros seleccionados.")
    else:
        fig=px.treemap(pt,path=["servicio"],values="puntos",color="puntos",color_continuous_scale=[[0,"#FFF3B4"],[1,YELLOW]])
        fig.update_traces(texttemplate="<b>%{label}</b><br>%{value} puntos",textfont_size=15); plot(fig,540,"public_actions_tree")
        section_figures.setdefault("Paquetes de apoyo", []).append(fig)

    st.markdown('<div id="capacidad-f01" class="section-kicker">07 · Capacidad operativa</div><h2>Modalidad de atención y personal disponible</h2>',unsafe_allow_html=True)
    st.markdown("### Modalidad de atención (%)")
    mode_labels={"modalidad_intramural":"Atención intramural","modalidad_extramural":"Atención extramural","modalidad_movil":"Unidad Móvil","modalidad_sede_propia":"Sede Propia","modalidad_pago_atenciones":"Pago por atenciones","modalidad_oficina":"Oficina","modalidad_otra":"Otra"}
    mode_values=pd.DataFrame({"modalidad":list(mode_labels.values()),"selecciones":[pd.to_numeric(active_places.get(col,0),errors="coerce").fillna(0).sum() if col in active_places else 0 for col in mode_labels]})
    mode_total=max(mode_values.selecciones.sum(),1); mode_values["porcentaje"]=mode_values.selecciones/mode_total*100; mode_values=mode_values[mode_values.selecciones.gt(0)].sort_values("porcentaje")
    fig=px.bar(mode_values,x="porcentaje",y="modalidad",orientation="h",text=mode_values.porcentaje.map(lambda x:f"{x:.1f}%"),color_discrete_sequence=[TEAL],labels={"porcentaje":"Porcentaje de selecciones","modalidad":""}); fig.update_traces(textposition="outside"); plot(fig,420,"care_modalities")
    section_figures.setdefault("Paquetes de apoyo", []).append(fig)
    st.caption("Las modalidades son de selección múltiple; el porcentaje muestra la distribución de las selecciones reportadas.")

    st.markdown("### Filtrar por socio y punto")
    st.caption("Aplica al personal disponible y a los días/horarios de atención, ambos más abajo. Incluye todos los socios y puntos registrados en el F01 (ambos focos); el personal y los horarios solo existen para los que completaron ese bloque del formulario — si no hay datos, se indica debajo de cada gráfica.")
    # Filtro local encadenado (Estado → Municipio → Socio → Punto), igual
    # que en el tablero anterior: independiente de los filtros de la barra
    # lateral, para poder acotar solo esta gráfica de personal y la de
    # días/horarios de atención de abajo. Las opciones de cada control salen
    # del universo completo de puntos registrados en F01 ("p", ambos focos,
    # no solo el bloque de cobertura/personal que es más chico) para que
    # también aparezcan los socios de Salud pública; el dato de personal y
    # horarios en sí sigue viniendo exclusivamente de "staffing" (el único
    # con esos campos reales) — cuando no hay datos para la combinación
    # elegida, las gráficas de abajo ya lo indican en vez de inventar algo.
    # Socio va antes que Punto a propósito: si un punto tiene más de una
    # organización (p. ej. "La Guaira" o "La Peñita" en los datos piloto),
    # dejar Socio en "Todos" y elegir el punto muestra esas organizaciones
    # en el mensaje de abajo, en vez de ocultarlas.
    wf_state_col,wf_muni_col,wf_org_col,wf_point_col=st.columns(4)
    with wf_state_col:
        wf_state=st.selectbox("Estado",["Todos"]+sorted(set(staffing.estado.dropna())|set(p.estado.dropna())),key="workforce_state")
    p_scope=p[p.estado.eq(wf_state)] if wf_state!="Todos" else p
    staff_scope=staffing[staffing.estado.eq(wf_state)] if wf_state!="Todos" else staffing
    with wf_muni_col:
        wf_muni=st.selectbox("Municipio",["Todos"]+sorted(set(staff_scope.municipio.dropna())|set(p_scope.municipio.dropna())),key="workforce_municipality")
    p_scope=p_scope[p_scope.municipio.eq(wf_muni)] if wf_muni!="Todos" else p_scope
    staff_scope=staff_scope[staff_scope.municipio.eq(wf_muni)] if wf_muni!="Todos" else staff_scope
    with wf_org_col:
        wf_org=st.selectbox("Socio",["Todos"]+sorted(set(staff_scope.organizacion.dropna())|set(p_scope.organizacion.dropna())),key="workforce_organization")
    p_scope=p_scope[p_scope.organizacion.eq(wf_org)] if wf_org!="Todos" else p_scope
    staff_scope=staff_scope[staff_scope.organizacion.eq(wf_org)] if wf_org!="Todos" else staff_scope
    with wf_point_col:
        wf_point=st.selectbox("Punto",["Todos"]+sorted(set(staff_scope.nombre_sitio.dropna())|set(p_scope.lugar.dropna())),key="workforce_point")
    wf_scope=staff_scope[staff_scope.nombre_sitio.eq(wf_point)] if wf_point!="Todos" else staff_scope
    if wf_point!="Todos" and wf_org=="Todos":
        point_orgs=sorted(wf_scope.organizacion.dropna().unique())
        if len(point_orgs)>1:
            st.caption(f"Este punto tiene más de una organización registrada: {', '.join(point_orgs)}.")
        elif wf_scope.empty:
            st.caption("Este punto no tiene personal ni horarios reportados en el bloque de cobertura del F01 (bloque distinto del registro general de puntos).")

    st.markdown("### Personal disponible por perfil")
    staff_cols=[c for c in staffing.columns if c.startswith("personal_")]
    totals=pd.to_numeric(wf_scope[staff_cols].stack(),errors="coerce").unstack().sum().sort_values(ascending=False).head(12).sort_values().reset_index(); totals.columns=["perfil","personas"]; totals["perfil"]=totals.perfil.str.replace("personal_","",regex=False).str.replace("_"," ").str.title()
    totals["perfil"]=totals["perfil"].replace({"Medico":"Médicos(as)","Enfermero":"Enfermeros(as)","Administrativos":"Administrativos(as)","Psicologo":"Psicólogos(as)","Pediatria":"Pediatría","Ginecologia":"Ginecología","Odontologos":"Odontólogos(as)","Psiquiatra":"Psiquiatras"})
    if totals.empty or totals.personas.sum()==0:
        st.info("No hay personal reportado para la selección actual.")
    else:
        fig=px.treemap(totals,path=["perfil"],values="personas",color="personas",color_continuous_scale=[[0,"#E5F0F8"],[.55,"#5D98CB"],[1,NAVY]])
        fig.update_traces(texttemplate="<b>%{label}</b><br>%{value}",textfont_size=15); plot(fig,590,"staffing_tree")
        section_figures.setdefault("Personal", []).append(fig)

    st.markdown("### Días y horarios de atención")
    st.caption("Usa el filtro de Estado, Municipio, Socio y Punto de la sección anterior.")
    day_cols={"dia_lunes":"Lun","dia_martes":"Mar","dia_miercoles":"Mié","dia_jueves":"Jue","dia_viernes":"Vie","dia_sabado":"Sáb","dia_domingo":"Dom"}
    hour_cols={"horario_manana":"Mañana","horario_tarde":"Tarde","horario_noche":"Noche","horario_24_7":"24 horas"}
    schedule_scope=wf_scope[wf_scope.get("dias_horas_aplica",pd.Series(dtype="object")).eq("Aplica")] if "dias_horas_aplica" in wf_scope else wf_scope.iloc[0:0]
    if schedule_scope.empty or not set(day_cols)|set(hour_cols)<=set(schedule_scope.columns):
        st.info("No hay días u horarios de atención reportados para la selección actual.")
    else:
        matrix=pd.DataFrame(
            [[int((pd.to_numeric(schedule_scope[d],errors="coerce").fillna(0)*pd.to_numeric(schedule_scope[h],errors="coerce").fillna(0)).sum()) for d in day_cols] for h in hour_cols],
            index=list(hour_cols.values()),columns=list(day_cols.values()),
        )
        if matrix.values.sum()==0:
            st.info(f"Los {len(schedule_scope)} punto(s) de la selección actual marcaron el bloque de días/horarios como «Aplica», pero no registraron ningún día ni horario específico.")
        else:
            fig=px.imshow(matrix,text_auto=True,color_continuous_scale=[[0,"#EFF4F8"],[1,NAVY]],labels=dict(x="Día",y="Horario",color="Puntos"),aspect="auto",zmin=0,zmax=max(1,matrix.values.max()))
            fig.update_xaxes(side="top"); fig.update_traces(textfont_size=14)
            plot(fig,340,"schedule_heatmap")
            section_figures.setdefault("Personal", []).append(fig)
            st.caption(f"Con base en los {len(schedule_scope)} puntos que reportaron días y horarios de atención en el F01 (bloque «Días y horas de atención»); el resto de los puntos de la selección actual no tiene este dato registrado.")

    st.markdown(f'<div id="inversion-f01" class="section-kicker">08 · Recursos movilizados</div><h2>Inversión registrada · USD {num(p.inversion_usd.sum())}</h2>',unsafe_allow_html=True)
    st.caption(f"Corresponde a los puntos con reportes entre el {first_f01} y el {last_f01} (periodo de reportes del F01 indicado arriba).")
    inv_all=p.groupby(["lugar","foco"],as_index=False).inversion_usd.sum().sort_values("inversion_usd",ascending=False)
    st.markdown("### Puntos con mayor inversión")
    # Rojo/amarillo por foco, igual que el mapa y "Puntos registrados por
    # foco": esta gráfica también compara ambos focos a la vez.
    investment_focus_colors={"Establecimiento de salud":RED,"Salud pública":YELLOW}
    # Nombres de sitio muy largos (algunos combinan varios lugares en un solo
    # texto, p. ej. "Campamentos transitorios: Polideportivo José María
    # Vargas, Estadio Cesar Nieves y Mare Abajo") empujaban las barras fuera
    # de la vista. Se recorta solo la etiqueta del eje; el nombre completo
    # sigue disponible en el hover.
    short_lugar=lambda n: n if len(n)<=34 else n[:33].rstrip()+"…"
    def _investment_bar(rows,key,height):
        # Importante: la categoría del eje es "lugar" completo, no el
        # nombre recortado — dos sitios distintos podían compartir los
        # mismos primeros 34 caracteres (p. ej. dos "Campamentos
        # transitorios: Polideportivo…" distintos) y truncarlos como
        # categoría los fusionaba en una sola barra, rompiendo el orden y
        # sumando montos de dos puntos distintos como si fueran uno. El
        # recorte ahora es solo texto de la etiqueta (ticktext), no la
        # categoría que Plotly usa para agrupar y ordenar.
        order=rows.groupby("lugar").inversion_usd.sum().sort_values(ascending=False).index.tolist()
        fig=px.bar(rows,x="inversion_usd",y="lugar",color="foco",orientation="h",text_auto="$.2s",category_orders={"lugar":order},color_discrete_map=investment_focus_colors,labels={"inversion_usd":"Inversión (USD)","lugar":"","foco":"Foco de intervención"},barmode="stack")
        fig.update_traces(textposition="outside")
        fig.update_yaxes(tickvals=order,ticktext=[short_lugar(n) for n in order])
        plot(fig,height,key)
        section_figures.setdefault("Inversión", []).append(fig)
    top_inv,rest_inv=inv_all.head(12),inv_all.iloc[12:]
    _investment_bar(top_inv,"f01_investment",540)
    if not rest_inv.empty:
        with st.expander(f"Ver los {len(rest_inv)} puntos restantes"):
            _investment_bar(rest_inv,"f01_investment_rest",max(320,40*len(rest_inv)+80))

    st.markdown("### Inversión por municipio")
    # Un solo gráfico, apilado por foco. Se usa municipio (no parroquia):
    # el dato real de parroquia solo existe para 3 puntos de salud pública,
    # todos en Vargas, y los establecimientos de salud nunca lo declaran
    # (no es un campo real de establecimientos.csv) — con eso no se puede
    # mostrar el foco de establecimiento ni más de un estado. Municipio sí
    # tiene dato real para ambos focos en varios estados.
    muni_inv=p.groupby(["estado","municipio","foco"],as_index=False).inversion_usd.sum()
    ranked=muni_inv.groupby("municipio").inversion_usd.sum().sort_values(ascending=False)
    if ranked.empty:
        st.info("No hay inversión registrada por municipio para los filtros seleccionados.")
    else:
        # El municipio líder puede concentrar mucha más inversión que el
        # resto; se destaca también como cifra editorial en una tarjeta
        # arriba del gráfico (con su desglose por foco), pero sigue
        # apareciendo como la primera barra del ranking de abajo — ver nota
        # más abajo sobre por qué ya no se excluye del gráfico.
        top_municipio=ranked.index[0]; top_total=ranked.iloc[0]
        top_breakdown=muni_inv[muni_inv.municipio.eq(top_municipio)].set_index("foco").inversion_usd
        top_estab=top_breakdown.get("Establecimiento de salud",0); top_salud=top_breakdown.get("Salud pública",0)
        share_estab=int(round(100*top_estab/top_total)) if top_total else 0
        multiple=f" ({int(top_total/ranked.iloc[1])}x el siguiente municipio)" if len(ranked)>1 and ranked.iloc[1] else ""
        st.markdown(f'''<div style="background:{PALE};border-left:4px solid {NAVY};border-radius:2px;padding:16px 22px;margin-bottom:16px;">
<div style="font-size:11px;font-weight:600;color:{MUTED};text-transform:uppercase;letter-spacing:.06em;">Municipio con mayor inversión</div>
<div style="display:flex;align-items:baseline;gap:10px;margin-top:2px;">
<span style="font-size:28px;font-weight:700;color:{INK};font-family:Georgia,serif;">{top_municipio}</span>
<span style="font-size:17px;color:{MUTED};">USD {num(top_total)}</span>
</div>
<div style="font-size:13px;color:{MUTED};margin-top:4px;">{share_estab}% en establecimientos de salud (USD {num(top_estab)}) · {100-share_estab}% en salud pública (USD {num(top_salud)}){multiple} — también es la primera barra del gráfico de abajo.</div>
</div>''',unsafe_allow_html=True)
        # Antes se excluía el municipio con mayor inversión del gráfico de
        # abajo (para que su barra, mucho más grande, no aplastara al resto
        # en la misma escala lineal) y solo se mostraba en la tarjeta de
        # arriba — pero eso hacía parecer que faltaba en la lista, lo cual
        # confundía más de lo que ayudaba. Ahora aparece en ambos lugares:
        # la tarjeta explica el detalle, y el gráfico lo muestra como la
        # primera barra del ranking completo, sin ocultar nada.
        # Vargas concentra ~20x la inversión del siguiente municipio (tiene
        # muchos más puntos, no es un error): en escala lineal esto aplasta
        # el resto a barras de 1-2px con las etiquetas superpuestas. Se usa
        # escala logarítmica para que todos los municipios queden legibles
        # a la vez, y se ocultan las etiquetas de segmentos muy chicos
        # (<USD 3.000) para que el texto no se solape sobre barras angostas.
        order=ranked.index.tolist()
        muni_inv["etiqueta"]=muni_inv.inversion_usd.map(lambda v: f"${v:,.0f}" if v>=3000 else "")
        fig=px.bar(muni_inv,x="inversion_usd",y="municipio",color="foco",orientation="h",barmode="stack",text="etiqueta",category_orders={"municipio":order},color_discrete_map=investment_focus_colors,labels={"inversion_usd":"Inversión (USD)","municipio":"","foco":"Foco de intervención"})
        fig.update_traces(textposition="outside",textfont=dict(size=13),marker_line_width=0,constraintext="none",cliponaxis=False)
        fig.update_layout(bargap=0.35,legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="right",x=1))
        fig.update_xaxes(type="log",showgrid=True,gridcolor="#E4DFD2",tickfont=dict(size=12))
        fig.update_yaxes(showgrid=False,tickfont=dict(size=13,color=INK),ticksuffix="  ")
        plot(fig,max(320,54+42*len(order)),"f01_muni_investment",margin=dict(l=16,r=64,t=28,b=18))
        st.caption("Escala logarítmica: Vargas concentra mucha más inversión que el resto (más puntos registrados), así que una escala lineal dejaría las demás barras ilegibles.")
        section_figures.setdefault("Inversión", []).append(fig)
    st.caption("Los montos son ilustrativos (datos históricos no incluían inversión). Cada barra se apila por foco de intervención (salud pública / establecimiento de salud). No se desglosa por parroquia porque ese dato real solo existe para 3 puntos de salud pública (todos en Vargas); los establecimientos de salud no lo declaran.")

    st.markdown('<div id="donantes-f01" class="section-kicker">09 · Fuentes de apoyo</div><h2>Fuentes declaradas por los socios</h2>',unsafe_allow_html=True)
    donor=filt(d["donor_sources"].copy())
    donor["estado_fuente"]=donor["donantes"].map(lambda x:"Fuente identificada" if x != "Sin fuente reportada" else "Sin fuente reportada")
    source_status=donor.groupby("estado_fuente").id_servicio.nunique().reset_index(name="puntos")
    named=donor[donor.donantes.ne("Sin fuente reportada")][["id_servicio","donantes"]].copy()
    named_sources=named.groupby("donantes").id_servicio.nunique().nlargest(10).sort_values().reset_index(name="puntos")
    dl,dr=st.columns([.72,1.28])
    with dl:
        st.markdown("### Disponibilidad de información")
        fig=px.pie(source_status,names="estado_fuente",values="puntos",hole=.62,color="estado_fuente",color_discrete_map={"Fuente identificada":BLUE,"Sin fuente reportada":"#B8B2A6"})
        fig.update_traces(textinfo="percent+value",textposition="inside"); plot(fig,470,"donor_status")
        section_figures.setdefault("Fuentes de apoyo", []).append(fig)
    with dr:
        st.markdown("### Fuentes mencionadas con mayor frecuencia")
        if named_sources.empty: st.info("No se identificaron fuentes para los filtros seleccionados.")
        else:
            fig=px.bar(named_sources,x="puntos",y="donantes",orientation="h",text="puntos",color_discrete_sequence=[NAVY],labels={"puntos":"Puntos respaldados","donantes":""})
            fig.update_traces(textposition="outside"); plot(fig,470,"donor_sources")
            section_figures.setdefault("Fuentes de apoyo", []).append(fig)
    st.caption("La visualización cuenta puntos en los que una organización declaró una fuente de apoyo. No representa montos ni atribuye financiamiento a un servicio específico.")
    st.caption(f"Datos actualizados al {last_f01}. La información refleja lo reportado por los socios del Clúster Salud y puede estar sujeta a cambios.")

    st.markdown("### Enlaces de interés")
    st.markdown(
        '<div class="ext-links">'
        '<a href="https://healthcluster.who.int/countries-and-regions/venezuela-(bolivarian-republic-of)" target="_blank">Clúster de Salud · Venezuela (Health Cluster)</a>'
        '<a href="https://www.paho.org/en/paho-response-2026-venezuela-earthquakes" target="_blank">OPS/OMS · Respuesta a los terremotos en Venezuela 2026</a>'
        '<a href="https://www.paho.org/es" target="_blank">OPS/OMS · Sitio oficial</a>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown("### Descargar infografía")
    # Igual que el tablero anterior (cluster-salud-venezuela.streamlit.app):
    # un solo botón que genera la infografía general con todas las gráficas
    # del tablero, sin selector de secciones.
    st.caption("Genera la infografía general con los datos y filtros actualmente aplicados en el tablero.")
    export_items=[(name,fig) for name,figs in section_figures.items() for fig in figs]
    export_signature=(tuple((a,str(b)) for a,b,_ in cards),len(export_items))
    # El PDF solo se genera cuando se pide explícitamente (no en cada rerun
    # de la página) porque exportar varias gráficas con kaleido es lento; sin
    # este paso, cualquier clic en el tablero volvería a regenerar todo el
    # PDF y haría sentir la app lenta. Se guarda en session_state para no
    # perderlo en reruns posteriores, y se invalida solo si cambian los
    # datos filtrados — así el botón, con un solo texto y lugar en pantalla
    # ("Descargar infografía general (PDF)"), pasa de disparar la
    # generación a disparar la descarga real del navegador sin que aparezcan
    # dos botones distintos a la vez.
    if st.session_state.get("f01_pdf_signature")!=export_signature:
        st.session_state.pop("f01_pdf_bytes",None)
    pdf_slot=st.empty()
    pdf_notes=[
        "El mapa distingue los dos focos de intervención (establecimientos de salud y salud pública); el tamaño del punto representa la inversión registrada.",
        "La oferta, el personal y la inversión se presentan por separado para establecimientos de salud y acciones de salud pública; no deben sumarse como una misma categoría.",
        "Los datos corresponden a los filtros de Estado, Organización, Parroquia, Foco y período actualmente aplicados en el tablero.",
    ]
    if "f01_pdf_bytes" not in st.session_state:
        if pdf_slot.button("Descargar infografía general (PDF)", key="f01_prepare_pdf", width="stretch"):
            with st.spinner("Generando infografía…"):
                pdf,failed_charts=make_pdf(
                    "Registro de organizaciones e intervenciones · F01",
                    [(a,str(b)) for a,b,_ in cards],
                    pdf_notes,
                    export_items,
                )
            st.session_state["f01_pdf_bytes"]=pdf
            st.session_state["f01_pdf_signature"]=export_signature
            if failed_charts:
                st.warning(f"{failed_charts} gráfica(s) no pudieron incluirse en el PDF; el resto de la infografía se generó normalmente.")
            pdf_slot.download_button("Descargar infografía general (PDF)",st.session_state["f01_pdf_bytes"],"infografia_F01.pdf","application/pdf",key="f01_download_pdf", width="stretch")
    else:
        pdf_slot.download_button("Descargar infografía general (PDF)",st.session_state["f01_pdf_bytes"],"infografia_F01.pdf","application/pdf",key="f01_download_pdf", width="stretch")
else:
    section_figures: dict[str, list[go.Figure]] = {}
    r=filt(reports,True); ids=set(r.id_reporte); res=results[results.id_reporte.isin(ids)].copy(); active=r.sort_values("fecha_reporte").drop_duplicates(["organizacion","nombre_sitio"],keep="last")
    section_band("REPORTE DE ACCIONES", "¿Qué acciones reportan los socios, dónde se realizan y qué resultados registran?")
    st.markdown('<div class="index-label">Ir a la sección</div><div class="index"><a href="#mapa-f02">01 · Mapa</a><a href="#reportantes-f02">Organizaciones que reportaron</a><a href="#focos-f02">02 · Resumen de los Reportes</a><a href="#reportes-establecimientos-f02">03 · Reportes de establecimientos de salud</a><a href="#reportes-acciones-f02">04 · Reportes de acciones de salud pública</a><a href="#resultados-f02">05 · Cobertura de reportes</a></div>',unsafe_allow_html=True)
    # Suma solo indicadores en unidad "personas" de salud pública (no mezcla
    # con casos/procedimientos/kits); igual que el resto del tablero, es una
    # suma de indicadores y no representa personas únicas.
    people_reached=res.loc[res.foco.eq("Salud pública") & res.unidad.str.lower().eq("personas"),"total"].sum()
    cards=[
        ("Estados",num(r.estado.nunique()),""),
        ("Municipios",num(r.municipio.nunique()),""),
        ("Parroquias",num(r.loc[r.parroquia.ne("No reportada"),"parroquia"].nunique()),""),
        ("Organizaciones reportantes",num(r.organizacion.nunique()),""),
        ("Reportes periódicos",num(r.id_reporte.nunique()),""),
        ("Personas atendidas en salud pública",num(people_reached),"suma de indicadores, no personas únicas"),
    ]
    st.markdown('<h3>Panorama general de reportes periódicos</h3>',unsafe_allow_html=True)
    st.markdown('<div class="metric-row" style="grid-template-columns:repeat(6,1fr)">'+''.join(metric(*x) for x in cards)+'</div>',unsafe_allow_html=True)
    st.markdown('<div id="mapa-f02" class="section-kicker">01 · Distribución territorial y georreferenciación</div><h2>Mapa de puntos con reporte periódico</h2>',unsafe_allow_html=True)
    point_reports=r.groupby(["organizacion","nombre_sitio"]).id_reporte.nunique().reset_index(name="reportes_punto")
    mapped=active.dropna(subset=["latitud","longitud"]).merge(point_reports,on=["organizacion","nombre_sitio"],how="left")
    mapped["foco_formulario"]=mapped.foco.map({"Establecimiento de salud":"Acciones en el establecimiento de salud","Salud pública":"Acciones de salud pública"})
    map_focus_colors={"Acciones en el establecimiento de salud":RED,"Acciones de salud pública":YELLOW}
    fig=px.scatter_map(mapped,lat="latitud",lon="longitud",color="foco_formulario",size="reportes_punto",size_max=19,hover_name="nombre_sitio",hover_data={"organizacion":True,"fecha_reporte":True,"estado":True,"municipio":True,"reportes_punto":True},color_discrete_map=map_focus_colors,map_style="carto-positron",zoom=8.7,center={"lat":10.30,"lon":-66.98},title="",labels={"foco_formulario":"Foco de intervención","reportes_punto":"Reportes del punto"})
    fig.update_traces(marker={"opacity":.88})
    if not mapped.empty:
        # Mismo tratamiento que el mapa F01: bounds por caja de sanidad +
        # min/max real (no percentiles, para no recortar puntos reales del
        # extremo este) y conteo por color en la leyenda.
        sane=mapped[mapped.latitud.between(-2,14)&mapped.longitud.between(-76,-58)]
        ref=sane if not sane.empty else mapped
        lat_lo,lat_hi=ref.latitud.min(),ref.latitud.max()
        lon_lo,lon_hi=ref.longitud.min(),ref.longitud.max()
        lat_pad=max(0.2,(lat_hi-lat_lo)*0.25); lon_pad=max(0.2,(lon_hi-lon_lo)*0.25)
        fig.update_layout(map=dict(bounds=dict(
            west=max(-180,lon_lo-lon_pad), east=min(180,lon_hi+lon_pad),
            south=max(-90,lat_lo-lat_pad), north=min(90,lat_hi+lat_pad),
        )))
        focus_counts=mapped.foco_formulario.value_counts()
        fig.for_each_trace(lambda t: t.update(name=f"{t.name} ({num(focus_counts.get(t.name,0))})"))
    plot(fig,590,"f02_map",is_map=True)
    section_figures.setdefault("Resumen territorial", []).append(fig)
    st.caption("Este mapa muestra exclusivamente puntos que presentaron actividad en F02; no representa todo el universo registrado en F01. El zoom con la rueda del mouse está desactivado para evitar cambios accidentales al hacer scroll de la página; use los botones +/- del mapa.")

    st.markdown('<div id="reportantes-f02"></div>',unsafe_allow_html=True)
    st.markdown("### Organizaciones que reportaron y puntos de intervención")
    # Barras apiladas por foco (no un solo color) para poder ver, dentro del
    # total de cada organización, cuánto corresponde a establecimientos de
    # salud y cuánto a salud pública.
    org_points_by_focus=active.assign(foco_formulario=active.foco.map({"Establecimiento de salud":"Acciones en el establecimiento de salud","Salud pública":"Acciones de salud pública"})).groupby(["organizacion","foco_formulario"]).nombre_sitio.nunique().reset_index(name="puntos")
    if org_points_by_focus.empty:
        st.info("No hay organizaciones con reportes para los filtros seleccionados.")
    else:
        # Orden descendente (mayor arriba, como un ranking editorial) — antes
        # quedaba ascendente y el más chico terminaba arriba.
        org_order_full=org_points_by_focus.groupby("organizacion").puntos.sum().sort_values(ascending=False).index.tolist()

        def _f02_org_bar(names: list, key: str):
            rows=org_points_by_focus[org_points_by_focus.organizacion.isin(names)].copy()
            # Segmentos de 1-2 puntos no llevan número: a ese ancho el texto
            # queda apretado contra el borde de la barra y se ve mal.
            rows["etiqueta"]=rows.puntos.map(lambda v: str(v) if v>=3 else "")
            fig=px.bar(rows,x="puntos",y="organizacion",color="foco_formulario",orientation="h",barmode="stack",
                       category_orders={"organizacion":names},
                       color_discrete_map={"Acciones en el establecimiento de salud":RED,"Acciones de salud pública":YELLOW},
                       text="etiqueta",labels={"puntos":"Puntos con reporte","organizacion":"","foco_formulario":"Foco"})
            fig.update_traces(textposition="inside",insidetextanchor="middle",textfont=dict(color="white",size=14),constraintext="none")
            fig.update_layout(bargap=.35,legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="right",x=1))
            plot(fig,max(420,54*len(names)+90),key,margin=dict(l=4,r=54,t=52,b=18))
            section_figures.setdefault("Resumen territorial", []).append(fig)

        top10,rest=org_order_full[:10],org_order_full[10:]
        _f02_org_bar(top10,"f02_org_points")
        if rest:
            with st.expander(f"Ver las {len(rest)} organizaciones restantes"):
                _f02_org_bar(rest,"f02_org_points_rest")
        # Verificación explícita: el número de organizaciones en esta
        # gráfica debe coincidir con la tarjeta "Organizaciones reportantes"
        # del resumen, ya que ambas cuentan sobre el mismo universo
        # filtrado (r/active).
        rest_note=f"las {len(rest)} restantes se pueden ver en el desplegable." if rest else "incluye todas las organizaciones reportantes."
        st.caption(f"Cuenta puntos distintos (no reportes); una organización con varios puntos aparece una sola vez, con sus puntos divididos entre los dos focos. Top 10 organizaciones por total de puntos, ordenadas de mayor a menor; {rest_note}")

    st.markdown('<div id="focos-f02" class="section-rule"></div><div class="section-kicker">02 · Distribución por foco</div><h2>Reportes y puntos activos por foco de intervención</h2>',unsafe_allow_html=True)
    r_foco=r.assign(foco_formulario=r.foco.map({"Establecimiento de salud":"Acciones en el establecimiento de salud","Salud pública":"Acciones de salud pública"}))
    active_foco=active.assign(foco_formulario=active.foco.map({"Establecimiento de salud":"Acciones en el establecimiento de salud","Salud pública":"Acciones de salud pública"}))
    focus_reports=r_foco.groupby("foco_formulario").id_reporte.nunique().reset_index(name="reportes")
    focus_points=active_foco.groupby("foco_formulario").nombre_sitio.nunique().reset_index(name="puntos")
    focus_orgs=active_foco.groupby("foco_formulario").organizacion.nunique().reset_index(name="socios")
    focus_summary=focus_reports.merge(focus_points,on="foco_formulario",how="outer").merge(focus_orgs,on="foco_formulario",how="outer").fillna(0)
    total_focus_reports=max(float(focus_summary.reportes.sum()),1)
    # Antes se mostraba un número grande (reportes) y, chiquito debajo, otro
    # número distinto (puntos) sin explicar la diferencia — se leía como un
    # error ("aparece 30 y luego 26"). Ahora cada cifra tiene su propia
    # etiqueta, y se agrega cuántas organizaciones (socios) hay en cada foco.
    for focus_name in ["Acciones en el establecimiento de salud","Acciones de salud pública"]:
        row=focus_summary[focus_summary.foco_formulario.eq(focus_name)]
        reports_value=int(row.reportes.iloc[0]) if not row.empty else 0
        points_value=int(row.puntos.iloc[0]) if not row.empty else 0
        orgs_value=int(row.socios.iloc[0]) if not row.empty else 0
        share=reports_value/total_focus_reports*100
        st.markdown(f"#### {focus_name}")
        focus_block=[
            ("Reportes periódicos",num(reports_value),f"{share:.1f}% de los reportes"),
            ("Puntos con reporte",num(points_value),"un mismo punto puede reportar más de una vez"),
            ("Organizaciones (socios)",num(orgs_value),"con al menos un reporte en este foco"),
        ]
        st.markdown('<div class="metric-row" style="grid-template-columns:repeat(3,1fr)">'+''.join(metric(*x) for x in focus_block)+'</div>',unsafe_allow_html=True)
    long_focus=focus_summary.melt(id_vars="foco_formulario",value_vars=["reportes","puntos"],var_name="medida",value_name="cantidad")
    long_focus["medida"]=long_focus.medida.map({"reportes":"Reportes periódicos enviados","puntos":"Puntos que reportaron"})
    fig=px.bar(long_focus,x="cantidad",y="foco_formulario",color="medida",orientation="h",barmode="group",text="cantidad",color_discrete_map={"Reportes periódicos enviados":NAVY,"Puntos que reportaron":TEAL},labels={"cantidad":"Cantidad","foco_formulario":"","medida":"Qué se cuenta"})
    fig.update_traces(textposition="outside"); fig.update_layout(bargap=.30,bargroupgap=.08); plot(fig,460,"f02_focus_compare")
    section_figures.setdefault("Resumen de los Reportes", []).append(fig)
    st.caption("La barra azul cuenta formularios F02 recibidos; la barra verde cuenta puntos distintos que enviaron reportes. Un mismo punto puede presentar más de un reporte durante el período.")

    def _render_focus_reports(mapped_name: str, raw_name: str, color: str, suffix: str, tipo_titulo: str, bucket: str, include_population: bool = False) -> None:
        # Todo lo que corresponde a este foco vive junto en una sola sección
        # (tendencia, rankings, tipos de punto, resultados por unidad y, para
        # salud pública, población alcanzada) — antes estaba repartido en
        # varias secciones separadas que mezclaban ambos focos por columnas.
        subset=r_foco[r_foco.foco_formulario.eq(mapped_name)]
        if subset.empty:
            st.info("No hay reportes para este foco con los filtros seleccionados.")
            return
        # El período de reportes real (barra de fecha) suele cubrir pocas
        # semanas, no varios meses: agrupar por mes colapsaba todo en un
        # solo punto y la "evolución" se veía vacía. Se agrupa por semana
        # cuando el rango cubierto es corto (<60 días) y por mes cuando es
        # más largo, para que la tendencia siempre tenga varios puntos.
        span_days=(subset.fecha_reporte.max()-subset.fecha_reporte.min()).days
        if span_days<60:
            period,period_label="W","Semana"
            trend=subset.assign(bucket=subset.fecha_reporte.dt.to_period(period).apply(lambda p:p.start_time.strftime("%d/%m"))).groupby("bucket").id_reporte.nunique().reset_index(name="reportes")
            trend=trend.rename(columns={"bucket":period_label})
        else:
            period,period_label="M","Mes"
            trend=subset.assign(bucket=subset.fecha_reporte.dt.to_period(period).astype(str)).groupby("bucket").id_reporte.nunique().reset_index(name="reportes")
            trend=trend.rename(columns={"bucket":period_label})
        fig=px.area(trend,x=period_label,y="reportes",markers=True,color_discrete_sequence=[color],labels={period_label:period_label,"reportes":"Reportes enviados"})
        # type="category": si un foco solo tiene reportes en 1-2 periodos,
        # Plotly interpretaba el eje como fecha continua y generaba ticks
        # con hora/segundos en vez de mostrar el periodo limpio.
        fig.update_xaxes(type="category")
        plot(fig,320,f"f02_monthly_{suffix}")
        section_figures.setdefault(bucket, []).append(fig)

        # Orden alineado con el formulario F02 real: 1) tipos de punto,
        # 2) organizaciones, 3) los bloques reales de apoyo reportado.
        # Dona + cuadro multicolor (mismo lenguaje visual que F01), no
        # barras de un solo color.
        tipo_full=active[active.foco.eq(raw_name)].groupby("tipo_punto").nombre_sitio.nunique().reset_index(name="puntos")
        # Antes contaba reportes (id_reporte.nunique() sobre "subset", sin
        # deduplicar) mientras la dona de al lado cuenta puntos distintos
        # ("active", deduplicado) — dos unidades distintas mostradas como si
        # fueran comparables, por lo que la suma de esta gráfica no
        # coincidía con el total de la dona. Se usa la misma base (puntos)
        # para que ambas gráficas sumen lo mismo.
        org_full=active[active.foco.eq(raw_name)].groupby("organizacion").nombre_sitio.nunique().reset_index(name="puntos")
        # Desglose por tipo, para apilar la barra de organizaciones con los
        # mismos colores que la dona de al lado (mismo tipo = mismo color en
        # ambas gráficas).
        org_type_full=active[active.foco.eq(raw_name)].groupby(["organizacion","tipo_punto"]).nombre_sitio.nunique().reset_index(name="puntos")
        type_palette=[color,"#548FC5","#D97732","#6B9F70","#806CA5","#91A4B4","#3568B4","#C9A63C","#7F5539"]
        type_order=sorted(tipo_full.tipo_punto.unique())
        type_color_map=dict(zip(type_order,type_palette))
        type_left,org_right=st.columns([1.6,3.4],gap="small")
        with type_left:
            st.markdown(f"#### {tipo_titulo}")
            if tipo_full.empty: st.info("No hay puntos para este foco con los filtros seleccionados.")
            else:
                fig=px.pie(tipo_full,names="tipo_punto",values="puntos",hole=.52,
                           color="tipo_punto",color_discrete_map=type_color_map,
                           labels={"tipo_punto":"Tipo de punto","puntos":"Puntos con reporte"})
                fig.update_traces(textinfo="percent",textposition="inside",marker_line_color=PAPER,marker_line_width=3)
                fig.update_layout(annotations=[dict(text=f"<b>{num(tipo_full.puntos.sum())}</b><br>puntos",x=.5,y=.5,showarrow=False,font=dict(size=15,color=INK))],
                                  legend=dict(orientation="v",yanchor="top",y=-.05,xanchor="center",x=.5,font=dict(size=11)))
                plot(fig,560,f"f02_types_{suffix}",margin=dict(l=12,r=12,t=10,b=160))
                section_figures.setdefault(bucket, []).append(fig)
        with org_right:
            st.markdown("#### Organizaciones con más puntos")
            if org_full.empty: st.info("No hay organizaciones para este foco con los filtros seleccionados.")
            else:
                # Antes era un treemap: con una organización muy dominante
                # (p. ej. 26 puntos contra 3 y 1) el área proporcional
                # dejaba a las demás como franjas ilegibles. Una barra
                # horizontal se lee bien sin importar cuánta diferencia haya
                # entre la más grande y las más chicas. Apilada por tipo de
                # punto (mismo color que la dona) para ver, dentro de cada
                # organización, qué tipos de establecimiento apoya.
                org_sorted=org_full.sort_values("puntos",ascending=False)
                top_org,rest_org=org_sorted.head(10),org_sorted.iloc[10:]
                def _org_bar(rows,key,height):
                    order=rows.organizacion.tolist()
                    rows_typed=org_type_full[org_type_full.organizacion.isin(order)].copy()
                    # Segmentos de 1-2 puntos no llevan número: a ese ancho
                    # el texto queda apretado y varios se solapan entre sí.
                    rows_typed["etiqueta"]=rows_typed.puntos.map(lambda v: str(v) if v>=3 else "")
                    fig=px.bar(rows_typed,x="puntos",y="organizacion",orientation="h",color="tipo_punto",text="etiqueta",barmode="stack",
                               color_discrete_map=type_color_map,category_orders={"organizacion":order,"tipo_punto":type_order},
                               labels={"puntos":"Puntos con reporte","organizacion":"","tipo_punto":"Tipo de punto"})
                    fig.update_traces(textposition="inside",insidetextanchor="middle",textfont=dict(color="white",size=11),constraintext="none")
                    # Sin leyenda propia: los colores ya están explicados en
                    # la leyenda de la dona de la izquierda (mismo mapeo
                    # tipo→color) y repetirla acá era redundante.
                    fig.update_layout(showlegend=False)
                    plot(fig,height,key,margin=dict(l=4,r=24,t=12,b=18))
                    section_figures.setdefault(bucket, []).append(fig)
                _org_bar(top_org,f"rank_org_{suffix}",max(360,40*len(top_org)+90))
                if not rest_org.empty:
                    with st.expander(f"Ver las {len(rest_org)} organizaciones restantes"):
                        _org_bar(rest_org,f"rank_org_rest_{suffix}",max(320,40*len(rest_org)+80))
                st.caption("El largo de cada barra representa el número de puntos distintos con reporte en este foco (no reportes enviados); los colores coinciden con el tipo de punto de la gráfica de la izquierda.")

        rank_state=subset.groupby("estado").id_reporte.nunique().nlargest(8).sort_values(ascending=False).reset_index(name="reportes")
        rank_muni=subset.groupby(["estado","municipio"]).id_reporte.nunique().nlargest(10).sort_values(ascending=False).reset_index(name="reportes")
        rank_muni["territorio"]=rank_muni.municipio+" · "+rank_muni.estado
        st_col,mu_col=st.columns(2)
        with st_col:
            st.markdown("#### Estados con más reportes")
            if rank_state.empty: st.info("No hay estados para este foco con los filtros seleccionados.")
            else:
                fig=px.bar(rank_state,x="reportes",y="estado",orientation="h",text="reportes",color_discrete_sequence=[color],
                           category_orders={"estado":rank_state.estado.tolist()},labels={"reportes":"Reportes","estado":""})
                fig.update_traces(textposition="outside"); plot(fig,max(320,44*len(rank_state)+80),f"rank_state_{suffix}")
                section_figures.setdefault(bucket, []).append(fig)
        with mu_col:
            st.markdown("#### Municipios con más reportes")
            if rank_muni.empty: st.info("No hay municipios para este foco con los filtros seleccionados.")
            else:
                fig=px.bar(rank_muni,x="reportes",y="territorio",orientation="h",text="reportes",color_discrete_sequence=[color],
                           category_orders={"territorio":rank_muni.territorio.tolist()},labels={"reportes":"Reportes","territorio":""})
                fig.update_traces(textposition="outside"); plot(fig,max(320,44*len(rank_muni)+80),f"rank_muni_{suffix}")
                section_figures.setdefault(bucket, []).append(fig)

        st.markdown("#### Distribución geográfica de los reportes")
        # Polígonos reales de municipio (límites oficiales, no puntos): la
        # intensidad del color muestra dónde se concentran los reportes,
        # igual que el ranking de municipios de arriba pero en el mapa.
        muni_geo_reports=subset.groupby(["estado","municipio"]).id_reporte.nunique().reset_index(name="reportes")
        if muni_geo_reports.empty:
            st.info("No hay reportes con estado/municipio para este foco con los filtros seleccionados.")
        else:
            muni_geo_reports["join_key"]=muni_geo_reports.estado+" | "+muni_geo_reports.municipio
            geo=load_municipio_geojson()
            # Un choropleth normal solo dibuja el polígono de los municipios
            # presentes en el DataFrame — los que no tienen reportes ni
            # aparecían, así que no se veían separados del resto del mapa
            # (mapa base sin borde). Se agregan TODOS los municipios de los
            # estados relevantes (con 0 reportes si no tienen), para que
            # cada municipio quede dibujado como su propio polígono con
            # borde, tenga o no datos.
            relevant_states=set(muni_geo_reports.estado)
            all_muni=pd.DataFrame([
                {"estado":f["properties"]["adm1_name"],"municipio":f["properties"]["adm2_name"],"join_key":f["properties"]["join_key"]}
                for f in geo["features"] if f["properties"]["adm1_name"] in relevant_states
            ]).drop_duplicates("join_key")
            full_muni=all_muni.merge(muni_geo_reports[["join_key","reportes"]],on="join_key",how="left")
            full_muni["reportes"]=full_muni.reportes.fillna(0)
            # Un color distinto por municipio (no una escala de intensidad
            # de un solo tono): así cada municipio se distingue a simple
            # vista en el mapa. Los que no tienen reportes en este foco se
            # agrupan en un gris neutro aparte para no ocupar un color de la
            # paleta sin motivo.
            muni_palette=[NAVY,RED,TEAL,YELLOW,BLUE,"#8A6FA8","#4C8C4A","#C9A63C","#E68A39","#65AAA1","#E34D40","#3568B4"]
            reporting_munis=sorted(full_muni.loc[full_muni.reportes>0,"municipio"].unique())
            muni_color_map={m:muni_palette[i%len(muni_palette)] for i,m in enumerate(reporting_munis)}
            muni_color_map["Sin reportes"]="#E4E1D6"
            full_muni["color_key"]=full_muni.municipio.where(full_muni.reportes>0,"Sin reportes")
            fig=px.choropleth_map(full_muni,geojson=geo,locations="join_key",featureidkey="properties.join_key",
                                   color="color_key",color_discrete_map=muni_color_map,
                                   hover_name="municipio",hover_data={"estado":True,"reportes":True,"join_key":False,"color_key":False},
                                   map_style="carto-positron",zoom=7.6,center={"lat":10.30,"lon":-66.98},opacity=.85,
                                   labels={"color_key":"Municipio"})
            fig.update_traces(marker_line_color="white",marker_line_width=1.2)
            plot(fig,480,f"rank_map_{suffix}",is_map=True)
            section_figures.setdefault(bucket, []).append(fig)
            st.caption("Polígonos de municipio (límites oficiales); cada municipio tiene su propio color para distinguirlo a simple vista — pase el cursor sobre un polígono para ver su número de reportes. Los municipios sin reportes en este foco se muestran en gris.")

        # Orden pedido: primero las áreas reportadas (4.1-style, "qué se
        # reportó"), y luego el tipo de apoyo institucional reportado sobre
        # esas áreas (3.11.x, "qué apoyo se dio") — antes iba al revés.
        st.markdown("### Volumen reportado por área y unidad de medida")
        if raw_name=="Salud pública":
            area_subset=res[res.foco.eq(raw_name)]
            areas=area_subset.groupby(["area","unidad"],as_index=False).total.sum()
            if areas.empty:
                st.info("No hay resultados para este foco con los filtros seleccionados.")
            else:
                # Menos áreas (6 en vez de 8) para que cada bloque quede más
                # grande y legible; antes las más chicas eran franjas casi
                # ilegibles.
                top_areas=areas.groupby("area").total.sum().nlargest(6).index; areas=areas[areas.area.isin(top_areas)]
                # Cuadro (treemap) en vez de barra apilada: la jerarquía
                # área → unidad evita sumar cifras de distinta unidad en un
                # mismo bloque (personas, casos, procedimientos, etc. quedan
                # en sub-bloques separados dentro de cada área). Se muestra
                # la unidad real siempre (antes se ocultaba "Personas" y
                # quedaba un bloque anidado con solo una rayita "-", que se
                # veía peor que repetir la palabra).
                fig=px.treemap(areas,path=["area","unidad"],values="total",color="area",
                               color_discrete_sequence=[NAVY,BLUE,TEAL,YELLOW,RED,"#8A6FA8","#4C8C4A","#C9A63C"])
                fig.update_traces(texttemplate="<b>%{label}</b><br>%{value:,.0f}",textfont_size=14)
                plot(fig,560,f"f02_areas_{suffix}",margin=dict(l=16,r=16,t=16,b=16))
                section_figures.setdefault(bucket, []).append(fig)
                st.caption("Las series separan personas, casos, procedimientos, kits, profesionales e instituciones; no deben sumarse como una misma unidad.")

            # Segunda visualización: detalle por indicador real dentro de un
            # área — cada área del F02 real (5.1, 5.2…) agrupa varios
            # indicadores específicos (5.1.1, 5.1.2…), que el cuadro de
            # arriba no puede mostrar sin amontonarse. Se elige un área y se
            # ve su desglose real por indicador (dato 100% de resultados.csv,
            # no simulado).
            st.markdown("#### Detalle por indicador dentro de un área")
            area_options=sorted(area_subset.area.unique()) if not area_subset.empty else []
            if not area_options:
                st.info("No hay áreas para mostrar el detalle.")
            else:
                selected_area=st.selectbox("Seleccione un área temática para ver el detalle",area_options,key=f"area_detail_{suffix}")
                detail=area_subset[area_subset.area.eq(selected_area)].groupby(["indicador","unidad"],as_index=False).total.sum()
                if detail.empty:
                    st.info("No hay indicadores para esta área con los filtros seleccionados.")
                else:
                    detail["etiqueta"]=detail.indicador.str.replace(r"^Número de ","",regex=True).str.slice(0,70)
                    order=detail.sort_values("total",ascending=False).etiqueta.tolist()
                    fig=px.bar(detail,x="total",y="etiqueta",color="unidad",orientation="h",text_auto=",.0f",
                               category_orders={"etiqueta":order},
                               color_discrete_sequence=[NAVY,BLUE,TEAL,YELLOW,RED,"#8A6FA8"],
                               labels={"total":"Total reportado","etiqueta":"","unidad":"Unidad"})
                    fig.update_traces(textposition="outside",cliponaxis=False)
                    fig.update_layout(legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="right",x=1))
                    plot(fig,max(360,34*len(detail)+90),f"f02_area_detail_{suffix}",margin=dict(l=4,r=54,t=44,b=18))
                    section_figures.setdefault(bucket, []).append(fig)
                    st.caption(f"Indicadores reales de F02 dentro de «{selected_area}» (resultados.csv); cada barra es un indicador específico del formulario, no un agregado del área.")
        else:
            # El histórico de reportes F02 (resultados.csv) nunca capturó la
            # pregunta 4.1 ("áreas o servicios del establecimiento que
            # recibieron apoyo") por período — solo existe a nivel de
            # registro (F01, areas.csv), no de reporte. Autorizado
            # explícitamente por la usuaria: se simula de forma
            # determinística por reporte, usando el catálogo real
            # compartido F01/F02, para representar cómo se vería este
            # bloque del formulario con datos reales.
            sim=subset[["id_reporte"]].drop_duplicates().copy()
            if sim.empty:
                st.info("No hay reportes para este foco con los filtros seleccionados.")
            else:
                area_seed=pd.util.hash_pandas_object(sim.id_reporte.astype(str),index=False).astype("uint64")
                sim["area"]=[FACILITY_AREA_CATALOG[v % len(FACILITY_AREA_CATALOG)] for v in area_seed]
                qty_seed=pd.util.hash_pandas_object((sim.id_reporte.astype(str)+"_qty"),index=False).astype("uint64")
                sim["total"]=(qty_seed % 26 + 5)
                areas=sim.groupby("area",as_index=False).total.sum().sort_values("total")
                fig=px.bar(areas,x="total",y="area",orientation="h",text_auto=".2s",color_discrete_sequence=[RED],labels={"total":"Total reportado (simulado)","area":""})
                fig.update_traces(textposition="outside"); plot(fig,max(420,32*len(areas)+120),f"f02_areas_{suffix}")
                section_figures.setdefault(bucket, []).append(fig)
                st.caption("Simulacro: el histórico de reportes F02 no capturó esta pregunta (4.1) por período — solo existe a nivel de registro (F01). Los valores se generan de forma determinística por reporte para representar cómo se vería este bloque con datos reales; no son un dato reportado.")

        if raw_name=="Establecimiento de salud":
            # Dato 100% real de F02 (resultados.csv, no F01): los únicos
            # indicadores de apoyo institucional que el formulario sí
            # reporta por período (código 3.11.x). No existe un indicador
            # equivalente para "área/servicio apoyado" (4.1) — por eso ese
            # bloque no se muestra; no se inventa un sustituto.
            st.markdown("### Tipo de apoyo reportado")
            support_code_labels={
                "3.11.1":"Despliegue de personal y atención médica directa",
                "3.11.2":"Dotación de insumos",
                "3.11.3":"Donación de medicamentos",
                "3.11.4":"Adecuaciones e infraestructura",
                "3.11.5":"Acciones en agua, saneamiento e higiene (WASH)",
            }
            support_subset=res[res.foco.eq(raw_name) & res.indicador_codigo.isin(support_code_labels)]
            support_data=support_subset.groupby("indicador_codigo").total.sum().reset_index()
            support_data["tipo_apoyo"]=support_data.indicador_codigo.map(support_code_labels)
            if support_data.empty or support_data.total.sum()==0:
                st.info("No hay apoyo institucional reportado para los filtros seleccionados.")
            else:
                fig=px.treemap(support_data,path=["tipo_apoyo"],values="total",color="total",color_continuous_scale=[[0,"#F8D8D3"],[1,RED]])
                fig.update_traces(texttemplate="<b>%{label}</b><br>%{value:.0f} instituciones/profesionales",textfont_size=15); plot(fig,480,f"f02_support_{suffix}")
                section_figures.setdefault(bucket, []).append(fig)
            st.caption("Indicadores reales de F02 sobre apoyo institucional (código 3.11.x); no existe un indicador equivalente para \"área o servicio apoyado\" (4.1) por período de reporte.")

        if include_population:
            st.markdown("### Composición por sexo, grupo de edad y discapacidad")
            gender_labels={"mujeres":"Mujeres","hombres":"Hombres","ninas":"Niñas","ninos":"Niños"}
            gender_colors={"Mujeres":RED,"Hombres":BLUE,"Niñas":YELLOW,"Niños":TEAL,"Discapacidad":"#7F5539"}
            people=res[res.foco.eq(raw_name) & res.unidad.str.lower().eq("personas") & res.tiene_desagregacion.eq(1)].copy()
            if people.empty:
                st.info("No hay personas desagregadas para este foco con los filtros seleccionados.")
            else:
                gender=people.groupby("area")[["mujeres","hombres","ninas","ninos"]].sum().reset_index().melt(id_vars="area",var_name="grupo",value_name="personas")
                gender["grupo"]=gender.grupo.map(gender_labels)
                totals_gender=gender.groupby("grupo",as_index=False).personas.sum()
                disability_total=people.discapacidad.sum()
                if disability_total>0:
                    totals_gender=pd.concat([totals_gender,pd.DataFrame([{"grupo":"Discapacidad","personas":disability_total}])],ignore_index=True)
                gp_l,gp_r=st.columns(2)
                with gp_l:
                    fig=px.pie(totals_gender,names="grupo",values="personas",hole=.6,color="grupo",color_discrete_map=gender_colors)
                    fig.update_traces(textinfo="percent+label")
                    plot(fig,420,f"gender_donut_{suffix}")
                    section_figures.setdefault(bucket, []).append(fig)
                with gp_r:
                    # Filtro en vez de selección por clic en la dona (el
                    # clic no disparaba el rerun de forma confiable): elige
                    # qué grupo mostrar en esta misma gráfica de áreas.
                    group_options=["Todos"]+totals_gender.grupo.tolist()
                    selected_group=st.selectbox("Filtrar por grupo",group_options,key=f"gender_filter_{suffix}")
                    if selected_group=="Todos":
                        top=gender.groupby("area").personas.sum().nlargest(6).index; gender_top=gender[gender.area.isin(top)]
                        fig=px.bar(gender_top,x="personas",y="area",color="grupo",orientation="h",barmode="stack",category_orders={"area":top.tolist()},color_discrete_map=gender_colors,labels={"personas":"Personas","area":"","grupo":""})
                        # Sin leyenda propia: ya está la de la dona a la
                        # izquierda con los mismos colores por grupo.
                        fig.update_layout(showlegend=False)
                        plot(fig,420,f"gender_stack_{suffix}",margin=dict(l=4,r=24,t=12,b=18))
                        section_figures.setdefault(bucket, []).append(fig)
                    elif selected_group=="Discapacidad":
                        disability=people.groupby("area").discapacidad.sum().sort_values(ascending=False).head(6).sort_values().reset_index(name="personas")
                        if disability.personas.sum()==0:
                            st.info("No hay personas con discapacidad reportadas para estos filtros.")
                        else:
                            fig=px.bar(disability,x="personas",y="area",orientation="h",text="personas",color_discrete_sequence=[gender_colors["Discapacidad"]],labels={"personas":"Personas con discapacidad","area":""}); fig.update_traces(textposition="outside")
                            plot(fig,420,f"disability_{suffix}",margin=dict(l=4,r=24,t=12,b=18))
                            section_figures.setdefault(bucket, []).append(fig)
                    else:
                        col={"Mujeres":"mujeres","Hombres":"hombres","Niñas":"ninas","Niños":"ninos"}[selected_group]
                        top_group=people.groupby("area")[col].sum().sort_values(ascending=False).head(6).sort_values().reset_index(name="personas")
                        if top_group.personas.sum()==0:
                            st.info(f"No hay personas reportadas en «{selected_group}» para estos filtros.")
                        else:
                            fig=px.bar(top_group,x="personas",y="area",orientation="h",text="personas",color_discrete_sequence=[gender_colors[selected_group]],labels={"personas":f"Personas ({selected_group.lower()})","area":""}); fig.update_traces(textposition="outside")
                            plot(fig,420,f"gender_detail_{suffix}",margin=dict(l=4,r=24,t=12,b=18))
                            section_figures.setdefault(bucket, []).append(fig)

    st.markdown('<div id="reportes-establecimientos-f02" class="section-rule"></div><div class="section-kicker">03 · Reportes de establecimientos de salud</div><h2>Evolución y alcance de los reportes de establecimientos de salud</h2>',unsafe_allow_html=True)
    _render_focus_reports("Acciones en el establecimiento de salud", "Establecimiento de salud", RED, "est", "Tipos de establecimiento de salud", "Reportes de establecimientos de salud")

    st.markdown('<div id="reportes-acciones-f02" class="section-rule"></div><div class="section-kicker">04 · Reportes de acciones de salud pública</div><h2>Evolución y alcance de los reportes de acciones de salud pública</h2>',unsafe_allow_html=True)
    _render_focus_reports("Acciones de salud pública", "Salud pública", YELLOW, "pub", "Tipos de lugar de intervención", "Reportes de acciones de salud pública", include_population=True)

    st.markdown('<div id="resultados-f02" class="section-rule"></div><div class="section-kicker">05 · Cobertura de reportes</div><h2>Organizaciones y territorios con mayor número de reportes</h2>',unsafe_allow_html=True)
    rank_org=r.groupby("organizacion").id_reporte.nunique().nlargest(10).sort_values().reset_index(name="reportes")
    rank_state=r.groupby("estado").id_reporte.nunique().nlargest(8).sort_values().reset_index(name="reportes")
    rank_muni=r.groupby("municipio").id_reporte.nunique().nlargest(10).sort_values().reset_index(name="reportes")
    ro,rs,rm=st.columns(3)
    for col,data,label,key,color in [(ro,rank_org,"Organizaciones con más reportes","rank_org",NAVY),(rs,rank_state,"Estados con más reportes","rank_state",RED),(rm,rank_muni,"Municipios con más reportes","rank_muni",TEAL)]:
        with col:
            st.markdown(f"### {label}"); category=data.columns[0]; fig=px.bar(data,x="reportes",y=category,orientation="h",text="reportes",color_discrete_sequence=[color],labels={"reportes":"Reportes",category:""}); fig.update_traces(textposition="outside"); plot(fig,430,key)
            section_figures.setdefault("Rankings de reporte", []).append(fig)
    st.markdown("### Servicios y acciones más reportadas, por foco")
    ind_est,ind_pub=st.columns(2)
    indicator_specs=[(ind_est,"Establecimiento de salud",RED,"f02_indicators_est"),(ind_pub,"Salud pública",YELLOW,"f02_indicators_pub")]
    for col,focus_value,color,key in indicator_specs:
        with col:
            st.markdown(f"#### {focus_value}")
            subset=res[res.foco.eq(focus_value)].groupby(["indicador_codigo","indicador"],as_index=False).total.sum().nlargest(8,"total").sort_values("total")
            if subset.empty:
                st.info("No hay indicadores para este foco con los filtros seleccionados.")
                continue
            subset["etiqueta"]=subset["indicador"].str.replace(r"^Número de ","",regex=True).str.slice(0,60)
            fig=px.bar(subset,x="total",y="etiqueta",orientation="h",text_auto=",.0f",color_discrete_sequence=[color],labels={"total":"Total reportado","etiqueta":""})
            fig.update_traces(textposition="outside"); plot(fig,440,key)
            section_figures.setdefault("Rankings de reporte", []).append(fig)
    st.caption("Cada barra puede combinar distintas unidades de medida (personas, casos, procedimientos, kits, etc.); compare solo indicadores con la misma unidad.")
    st.markdown("### Descargar infografía")
    st.caption("Genera la infografía general con los datos y filtros actualmente aplicados en el tablero.")
    export_items=[(name,fig) for name,figs in section_figures.items() for fig in figs]
    export_signature=(tuple((a,str(b)) for a,b,_ in cards),len(export_items))
    if st.session_state.get("f02_pdf_signature")!=export_signature:
        st.session_state.pop("f02_pdf_bytes",None)
    pdf_slot=st.empty()
    pdf_notes=[
        "El mapa incluye únicamente puntos con actividad reportada en el período seleccionado.",
        "Los reportes y resultados se presentan por separado para establecimientos de salud y acciones de salud pública; no deben sumarse como una misma categoría.",
        "Los rankings muestran frecuencia de reporte, no intensidad ni calidad de la respuesta.",
    ]
    if "f02_pdf_bytes" not in st.session_state:
        if pdf_slot.button("Descargar infografía general (PDF)", key="f02_prepare_pdf", width="stretch"):
            with st.spinner("Generando infografía…"):
                pdf,failed_charts=make_pdf(
                    "Reportes periódicos · F02",
                    [(a,str(b)) for a,b,_ in cards],
                    pdf_notes,
                    export_items,
                )
            st.session_state["f02_pdf_bytes"]=pdf
            st.session_state["f02_pdf_signature"]=export_signature
            if failed_charts:
                st.warning(f"{failed_charts} gráfica(s) no pudieron incluirse en el PDF; el resto de la infografía se generó normalmente.")
            pdf_slot.download_button("Descargar infografía general (PDF)",st.session_state["f02_pdf_bytes"],"infografia_F02.pdf","application/pdf",key="f02_download_pdf", width="stretch")
    else:
        pdf_slot.download_button("Descargar infografía general (PDF)",st.session_state["f02_pdf_bytes"],"infografia_F02.pdf","application/pdf",key="f02_download_pdf", width="stretch")

st.divider()
st.caption("Simulacro metodológico · Datos históricos sanitizados y muestras piloto · La versión productiva deberá conectarse directamente con las exportaciones de los nuevos F01 y F02.")
