"""Simulacro ejecutivo del tablero del Clúster Salud para los nuevos F01 y F02.

Los datos históricos se usan únicamente para ensayar la estructura. Las fechas del
calendario piloto son simuladas y están identificadas como tales en la interfaz.
"""

from __future__ import annotations

import base64
import html as html_lib
import io
import json
import math
import re
import sys
import textwrap
import unicodedata
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

# The desktop preview keeps its document libraries in a shared runtime.  Add
# that location only after the data stack is loaded so its NumPy build cannot
# shadow the application's own installation.
_shared_packages = Path(r"C:\Users\claud\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages")
if _shared_packages.exists():
    sys.path.append(str(_shared_packages))

from report import build_report, bar_chart, composition_bar


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
# Paleta de marca única (provista por la usuaria, uso exclusivo en todo el
# tablero): Azul oscuro, Azul principal, Azul cielo, Azul claro, Naranja,
# Gris. Para series con más de 5-6 categorías se generan tintes/sombras de
# estos mismos 6 tonos (helpers tint/shade) en vez de introducir colores
# nuevos, para no salirse de la paleta.
INK, PAPER = "#20252B", "#FFFFFF"  # neutros tipográficos (texto/fondo de página), no forman parte de la paleta de datos


def _blend(hex_a: str, hex_b: str, t: float) -> str:
    a = tuple(int(hex_a[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(hex_b[i:i + 2], 16) for i in (1, 3, 5))
    m = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return "#{:02X}{:02X}{:02X}".format(*m)


def tint(hex_color: str, t: float = 0.35) -> str:
    return _blend(hex_color, "#FFFFFF", t)


def shade(hex_color: str, t: float = 0.35) -> str:
    return _blend(hex_color, "#000000", t)


def value_gradient_colors(values, stops: list) -> list:
    # Reemplaza a color_continuous_scale para treemaps: Plotly/Kaleido
    # dibuja un rectángulo de fondo oscuro (no configurable via root_color,
    # marker_line ni paper/plot_bgcolor) cuando un treemap usa `color=`
    # numérico con coloraxis. Precalculando aquí el color exacto de cada
    # bloque y pasándolo como color_discrete_map se logra el mismo
    # degradado sin activar ese renderizado con fondo oscuro.
    vmin, vmax = min(values), max(values)
    span = (vmax - vmin) or 1
    out = []
    for v in values:
        t = (v - vmin) / span
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]; p1, c1 = stops[i + 1]
            if t <= p1 or i == len(stops) - 2:
                local_t = 0 if p1 == p0 else min(max((t - p0) / (p1 - p0), 0), 1)
                out.append(_blend(c0, c1, local_t))
                break
    return out


# Los 6 tonos exactos que dio la usuaria, suavizados un 15% (mezclados hacia
# blanco) a pedido suyo para que no se vean tan "brillantes" sobre el fondo
# blanco de la hoja — equivale visualmente a un 15% de transparencia sobre
# ese fondo blanco.
_BRAND_NAVY, _BRAND_BLUE, _BRAND_SKY, _BRAND_PALE, _BRAND_ORANGE, _BRAND_GRAY = "#0B3C5D", "#0067A5", "#2D9CDB", "#EAF4FA", "#F26A21", "#8FA4B3"
NAVY, BLUE, SKY, PALE, ORANGE, GRAY = (tint(c, .07) for c in (_BRAND_NAVY, _BRAND_BLUE, _BRAND_SKY, _BRAND_PALE, _BRAND_ORANGE, _BRAND_GRAY))
# Alias históricos que quedan mapeados dentro de la paleta de 6 colores:
RED, YELLOW, TEAL, MUTED = ORANGE, SKY, SKY, GRAY
# Rojo real (no el alias RED=ORANGE) para "Mujeres" en gráficas de sexo/edad
# — a pedido explícito, distinto del naranja de marca usado en focos.
MUJERES_RED = tint("#D6483F", .07)
FOCUS_COLORS = {"Establecimiento de salud": ORANGE, "Salud pública": SKY}


# Paleta categórica extendida: los 5 tonos base (máxima distinción) seguidos
# de tintes/sombras de los mismos, para gráficas con varias categorías sin
# salir de la paleta de marca.
PALETTE_EXT = [
    NAVY, ORANGE, SKY, GRAY, BLUE,
    shade(NAVY, .35), tint(ORANGE, .35), tint(SKY, .35), shade(GRAY, .3), tint(BLUE, .35),
    shade(ORANGE, .35), tint(NAVY, .35), shade(SKY, .35), tint(GRAY, .35),
]
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
    "Otro",
]
# Catálogo real de la pregunta 4.2 del F02 ("Seleccione el tipo o los tipos
# de apoyo proporcionados durante este período") — tomado directamente del
# formulario vigente (captura de pantalla provista por la usuaria), no del
# histórico de apoyos.csv: ese histórico solo trae 5 de estas 12 categorías
# (las que alguna organización ya registró alguna vez), pero el simulacro de
# esta pregunta a nivel de reporte periódico debe poder mostrar cualquiera
# de las 12 opciones reales del formulario, no solo el subconjunto que ya
# tiene datos de registro.
FACILITY_SUPPORT_TYPE_CATALOG = [
    "Acciones en agua, saneamiento e higiene (WASH)",
    "Acompañamiento técnico",
    "Adecuaciones e infraestructura",
    "Despliegue de personal y atención médica directa",
    "Dotación de equipos médicos y biomédicos",
    "Dotación de insumos",
    "Donación de medicamentos",
    "Gestión de residuos de establecimientos de salud",
    "Salud Mental y Apoyo Psicosocial (SMAPS)",
    "Sistemas de información y vigilancia epidemiológica",
    "Soporte logístico, energía y cadena de frío",
    "Otro (especifique)",
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
def load_estado_geojson() -> dict:
    """Límites estadales reales de Venezuela, mismo origen que load_municipio_geojson().
    "join_key" = nombre del estado (ya es único, no hace falta componerlo)."""
    with open(DATA / "geografia" / "limites_estadales.geojson", encoding="utf-8") as f:
        geo = json.load(f)
    for feature in geo["features"]:
        feature["properties"]["join_key"] = feature["properties"]["adm1_name"]
    return geo


def geojson_lat_lon_bounds(geo: dict, join_keys: set) -> tuple[float, float, float, float] | None:
    """Caja delimitadora (lat_lo, lat_hi, lon_lo, lon_hi) de los polígonos cuyo
    join_key está en join_keys — para encuadrar el mapa según los datos
    realmente presentes (p. ej. un solo estado costero chico como La Guaira)
    en vez de un centro/zoom fijo pensado para el país completo."""
    lats: list[float] = []; lons: list[float] = []

    def _walk(coords):
        if isinstance(coords[0], (int, float)):
            lons.append(coords[0]); lats.append(coords[1])
        else:
            for c in coords:
                _walk(c)

    for feature in geo["features"]:
        if feature["properties"]["join_key"] in join_keys:
            _walk(feature["geometry"]["coordinates"])
    if not lats:
        return None
    return min(lats), max(lats), min(lons), max(lons)


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
    # Verificado contra el XLSForm real y vigente del F02
    # (F02_lista_organizaciones_completa_20260915.xlsx, hoja "survey"): la
    # sección 5 del formulario va de 5.1 a 5.15 y NO existe un bloque 5.11 —
    # sin embargo el histórico de resultados.csv sí trae un bloque "3.11.x"
    # ("profesionales de salud apoyados", "instituciones dotadas con
    # insumos", etc.) que no corresponde a ninguna pregunta real del
    # formulario actual. También hay dos códigos sueltos dentro de 5.8
    # Inmunización (3.8.2 "instituciones apoyadas en inmunización" y 3.8.3
    # "personas del talento humano apoyadas en inmunización") que tampoco
    # existen ahí — el bloque real 5.8 tiene un solo indicador (5.8.1). Se
    # excluyen estos códigos huérfanos para que ninguna gráfica los
    # presente como si fueran un indicador real del F02 vigente.
    _non_form_codes = {"3.8.2", "3.8.3"}
    results = results[~results["indicador_codigo"].isin(_non_form_codes) & ~results["indicador_codigo"].str.startswith("3.11.")]
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
    # "Hasta" es obligatorio en el F01 vigente; en el CSV histórico un punto
    # (dato piloto antiguo) no lo declaró. Se simula una fecha de cierre
    # determinística (60–180 días después del inicio) en vez de extenderlo
    # silenciosamente hasta fin de año, para no inflar artificialmente su
    # vigencia en el calendario.
    missing_hasta = vigencia_real["hasta"].isna()
    simulated_span = vigencia_real.loc[missing_hasta, "id_servicio"].astype(str).map(lambda s: 60 + (hash(s) % 121))
    vigencia_real.loc[missing_hasta, "hasta"] = vigencia_real.loc[missing_hasta, "desde"] + pd.to_timedelta(simulated_span, unit="D")
    vigencia_real["hasta_efectiva"] = vigencia_real["hasta"].clip(upper=year_end)

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


def support_ranking_table(ranking: pd.DataFrame, *, max_height: int = 700) -> None:
    """Tabla legible con barras rojas; evita el color fijo del ProgressColumn de Streamlit."""
    frame = ranking[["Organización", "Establecimiento", "Tipología", "Apoyos recibidos"]].copy()
    maximum = max(1, int(frame["Apoyos recibidos"].max()))
    rows = []
    for row in frame.itertuples(index=False, name=None):
        org, establishment, typology, supports = row
        width = max(0, min(100, int(round(int(supports) / maximum * 100))))
        rows.append(
            "<tr>"
            f"<td>{html_lib.escape(str(org))}</td>"
            f"<td>{html_lib.escape(str(establishment))}</td>"
            f"<td>{html_lib.escape(str(typology))}</td>"
            "<td><div class='support-meter'>"
            f"<span style='width:{width}%'></span><b>{int(supports)}</b>"
            "</div></td></tr>"
        )
    height = min(max_height, 45 * len(frame) + 48)
    st.markdown(
        f"""
        <div class="support-table-wrap" style="max-height:{height}px">
          <table class="support-table">
            <thead><tr><th>Organización</th><th>Establecimiento</th><th>Tipología</th><th>Apoyos recibidos</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def complete_dashboard_export() -> None:
    """Abre la impresión del navegador para guardar toda la vista activa como PDF."""
    with st.container(key="print_export"):
        components.html(
            f"""
            <style>
              html,body{{margin:0;background:transparent;font-family:Arial,sans-serif}}
              button{{width:100%;border:0;border-radius:8px;padding:12px 16px;background:{RED};color:white;
                      font-size:15px;font-weight:700;cursor:pointer}}
              button:hover{{background:{shade(RED,.12)}}}
            </style>
            <button onclick="window.parent.focus(); window.parent.print();">Descargar tablero completo como PDF</button>
            """,
            height=48,
        )
        st.caption("En la ventana de impresión seleccione «Guardar como PDF». Se exportará toda la vista activa con los filtros actuales.")


def add_map_marker_outline(fig: go.Figure, frame: pd.DataFrame, size_col: str) -> None:
    # px.scatter_map / go.Scattermap no admiten marker.line (el círculo de
    # MapLibre no soporta borde): se simula un contorno fino agregando una
    # capa negra debajo, con el mismo sizeref/sizemode/sizemin que ya calculó
    # Plotly Express, apenas más grande en área (~1.2x → ~9% más de radio)
    # para que se vea como un anillo delgado y no un halo grueso.
    if fig.data and frame.size:
        ref_marker = fig.data[0].marker
        fig.add_trace(go.Scattermap(
            lat=frame["latitud"], lon=frame["longitud"],
            marker=dict(size=frame[size_col] * 1.2, sizeref=ref_marker.sizeref, sizemode=ref_marker.sizemode,
                        sizemin=ref_marker.sizemin, color="black", opacity=0.9),
            hoverinfo="skip", showlegend=False,
        ))
        fig.data = (fig.data[-1],) + fig.data[:-1]


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
    fig.update_xaxes(gridcolor=tint(GRAY,.7), zeroline=False, automargin=True, tickfont=dict(size=12))
    fig.update_yaxes(gridcolor=tint(GRAY,.7), zeroline=False, automargin=True, tickfont=dict(size=13))
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
    return "<br>".join(textwrap.wrap(name, width=21, break_long_words=False))


def set_org_tile_labels(fig, min_size: int = 12, max_size: int = 18) -> None:
    # Mismo criterio que set_value_tile_labels: escala continua por raíz
    # cuadrada (no saltos discretos) acotada entre un mínimo y un máximo, así
    # ningún bloque queda con letras gigantes ni microscópicas. "name" ya
    # viene con saltos de línea de tile_name(), así que no se envuelve de
    # nuevo aquí.
    values=list(fig.data[0].values)
    vmin,vmax=min(values),max(values)
    span=(vmax-vmin) or 1
    labels=[]
    for name,value in zip(fig.data[0].labels,values):
        frac=((value-vmin)/span)**0.5
        size=round(min_size+frac*(max_size-min_size))
        labels.append(f'<span style="font-size:{size}px">{name}<br>{value:g}</span>')
    fig.update_traces(text=labels,texttemplate="%{text}",textfont_size=max_size,textposition="middle center",root_color="white")


def set_value_tile_labels(fig, unit: str, min_size: int = 13, max_size: int = 18, wrap_width: int = 15) -> None:
    # Tamaño de letra proporcional al valor (raíz cuadrada, como el área del
    # bloque), acotado entre un mínimo y un máximo (rango angosto a
    # propósito: nada de letras gigantes en bloques grandes ni miscroscópicas
    # en los chicos — look ejecutivo, no "de escuela"). Los nombres largos se
    # envuelven en varias líneas ANTES de calcular/mostrar el texto (en vez
    # de dejar que Plotly los encoja para que quepan en una sola línea): así
    # dos bloques del mismo tamaño (mismo valor) siempre quedan con el mismo
    # tamaño de letra, sin importar si el nombre es corto o largo.
    values=list(fig.data[0].values)
    vmin,vmax=min(values),max(values)
    span=(vmax-vmin) or 1
    labels=[]
    for name,value in zip(fig.data[0].labels,values):
        frac=((value-vmin)/span)**0.5
        size=round(min_size+frac*(max_size-min_size))
        wrapped_name="<br>".join(textwrap.wrap(str(name),width=wrap_width,break_long_words=False)) or str(name)
        value_line=f"{value:,.0f} {unit}".strip()
        labels.append(f'<span style="font-size:{size}px">{wrapped_name}<br>{value_line}</span>')
    fig.update_traces(text=labels,texttemplate="%{text}",textfont_size=max_size,textposition="middle center",
                       marker_line_color="white",marker_line_width=2,root_color="white")


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


d = prepare()
fac, reports, results, points = d["facilities"], d["reports_prepared"], d["results_joined"], d["points"]

st.markdown(f"""
<style>
  .stApp{{background:{PAPER};color:{INK}}}.block-container{{max-width:1620px;padding-top:1rem;padding-bottom:3.5rem;padding-left:2rem;padding-right:2rem}}
  [data-testid="stSidebar"]{{background:{tint(GRAY,.88)};border-right:1px solid {tint(GRAY,.7)}}}
  .logo-crop{{max-width:1660px;margin:0 auto 20px;padding:16px 22px;background:white;border:1px solid {tint(GRAY,.78)};border-radius:14px;box-shadow:0 3px 12px {NAVY}12}}
  .logo-crop img{{width:100%;height:auto;display:block;filter:contrast(1.02);}}
  .logo-strip-extra{{max-width:1660px;margin:0 auto 24px;padding:14px 22px;background:white;border:1px solid {tint(GRAY,.78)};border-radius:14px;box-shadow:0 3px 12px {NAVY}12;display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:16px 28px}}
  .logo-strip-extra img{{height:44px;width:auto;max-width:200px;object-fit:contain;filter:contrast(1.02)}}
  .hero{{background:linear-gradient(120deg,{NAVY},{BLUE});color:white;border-radius:16px;padding:16px 26px;margin:0 0 18px;box-shadow:0 8px 18px {NAVY}22}}
  .hero b{{font-size:.68rem;letter-spacing:.15em}}.hero h1{{font-size:1.7rem;margin:.28rem 0 .18rem;color:white}}.hero p{{font-size:.88rem;margin:0;color:{PALE}}}
  .hero small{{display:block;margin-top:.22rem;color:{tint(SKY,.72)};font-size:.76rem}}
  .module-head{{border-left:6px solid {ORANGE};background:white;padding:14px 18px;border-radius:8px;margin:18px 0}}
  .module-head h2{{margin:0;color:{NAVY};font-size:1.55rem}}.module-head p{{margin:4px 0 0;color:{MUTED}}}
  .section-band{{display:flex;align-items:center;flex-wrap:wrap;gap:.35rem .65rem;background:{PALE};border:1px solid {tint(SKY,.7)};border-left:5px solid {NAVY};color:{NAVY};border-radius:9px;padding:.6rem .85rem;margin:.9rem 0 .65rem;font-weight:800;font-size:.92rem;letter-spacing:.02em}}
  .section-band span{{color:{MUTED};font-weight:500;font-style:italic;font-size:.88rem;letter-spacing:0}}
  .metric-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:14px;margin:16px 0 26px}}
  .metric{{background:white;border:1px solid {tint(GRAY,.75)};border-top:4px solid {BLUE};padding:14px 16px;min-height:106px;text-align:center;display:flex;flex-direction:column}}
  .metric span{{display:flex;align-items:center;justify-content:center;min-height:46px;text-transform:uppercase;font-size:.68rem;letter-spacing:.08em;color:{MUTED};font-weight:700;line-height:1.35}}
  .metric strong{{display:block;font-size:2rem;color:{NAVY};margin:.2rem 0;flex:1}}.metric small{{color:{MUTED}}}
  .pilot{{background:{tint(ORANGE,.88)};border:1px solid {tint(ORANGE,.45)};border-radius:8px;padding:10px 13px;color:{shade(ORANGE,.55)};font-size:.85rem}}
  .excel-dl{{display:inline-flex;align-items:center;gap:6px;text-decoration:none;color:{NAVY};background:white;border:1px solid {tint(GRAY,.68)};border-radius:8px;padding:5px 11px;font-weight:700;font-size:.83rem;line-height:1.6}}
  .excel-dl:hover{{border-color:{BLUE};color:{BLUE}}}
  .excel-dl svg{{flex:none}}
  .index-label{{color:{MUTED};font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;margin:10px 0 8px}}
  .index{{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 20px}}
  .index a{{text-decoration:none;color:{NAVY};background:white;border:1px solid {tint(GRAY,.68)};border-radius:999px;padding:7px 12px;font-weight:700;font-size:.82rem}}
  .index a::before{{content:"↓ ";color:{BLUE}}}
  .ext-links{{display:flex;gap:8px;flex-wrap:wrap;margin:2px 0 20px}}
  .ext-links a{{text-decoration:none;color:{NAVY};background:white;border:1px solid {tint(GRAY,.68)};border-radius:999px;padding:7px 12px;font-weight:700;font-size:.82rem}}
  .ext-links a::after{{content:" ↗";color:{BLUE}}}
  .sidebar-nav{{display:flex;gap:8px;margin:4px 0 16px}}
  .sidebar-nav a{{flex:1;text-decoration:none;text-align:center;color:{NAVY};background:white;border:1px solid {tint(GRAY,.68)};border-radius:8px;padding:8px 6px;font-weight:700;font-size:.78rem}}
  .sidebar-nav a:hover{{border-color:{BLUE};color:{BLUE}}}
  .sidebar-update{{display:flex;flex-direction:column;gap:8px;margin:4px 0 16px}}
  .sidebar-update a{{text-decoration:none;text-align:center;color:white;background:{BLUE};border-radius:8px;padding:9px 6px;font-weight:700;font-size:.8rem}}
  .sidebar-update a::after{{content:" ↗";}}
  .sidebar-update a:hover{{background:{NAVY}}}
  .st-key-calendar_nav_btn button{{background:{tint(ORANGE,.08)}!important;color:white!important;border:none!important;font-weight:700!important}}
  .st-key-calendar_nav_btn button:hover{{background:{ORANGE}!important;color:white!important}}
  .st-key-calendar_nav_btn button p{{color:white!important}}
  .section-kicker{{color:{RED};text-transform:uppercase;letter-spacing:.11em;font-size:.7rem;font-weight:800;margin-top:22px}}
  .section-rule{{border-top:1px solid {tint(GRAY,.55)};margin:24px 0 8px}}
  [data-testid="stRadio"] label p{{color:{INK}!important;font-weight:700}}
  h2,h3{{color:{NAVY}}}
  .block-container h2{{font-size:1.9rem!important;line-height:1.23;font-weight:650}}
  .block-container h3{{font-size:1.45rem!important;line-height:1.28;font-weight:650}}
  .block-container h4{{font-size:1.16rem!important;line-height:1.3;font-weight:600}}
  .block-container .module-head h2{{font-size:1.55rem!important}}
  .stTabs [data-baseweb="tab-list"]{{gap:1.2rem;border-bottom:1px solid {GRAY}}}
  .stTabs [aria-selected="true"]{{border-bottom:4px solid {RED}}}
  .support-table-wrap{{overflow:auto;border:1px solid {tint(GRAY,.72)};border-radius:10px;background:white}}
  .support-table{{width:100%;border-collapse:collapse;font-size:.88rem}}
  .support-table th{{position:sticky;top:0;z-index:1;background:{tint(GRAY,.9)};color:{MUTED};text-align:left;padding:11px 12px;border-bottom:1px solid {tint(GRAY,.68)};font-weight:600}}
  .support-table td{{padding:10px 12px;border-bottom:1px solid {tint(GRAY,.78)};vertical-align:middle}}
  .support-table th:nth-child(1),.support-table td:nth-child(1){{width:16%}}
  .support-table th:nth-child(2),.support-table td:nth-child(2){{width:34%}}
  .support-table th:nth-child(3),.support-table td:nth-child(3){{width:28%}}
  .support-table th:nth-child(4),.support-table td:nth-child(4){{width:22%}}
  .support-meter{{display:grid;grid-template-columns:1fr 32px;gap:9px;align-items:center;position:relative}}
  .support-meter::before{{content:"";grid-column:1;grid-row:1;height:10px;background:{tint(GRAY,.82)};border-radius:999px}}
  .support-meter span{{grid-column:1;grid-row:1;height:10px;background:{RED};border-radius:999px;min-width:0}}
  .support-meter b{{grid-column:2;font-weight:500;color:{INK};text-align:right}}
  @media print{{
    [data-testid="stSidebar"],header,[data-testid="stToolbar"],.stRadio,.stDownloadButton,.st-key-calendar_nav_btn,.st-key-print_export{{display:none!important}}
    .block-container{{max-width:none!important;padding:0!important}}
    .hero{{box-shadow:none}}
    [data-testid="stPlotlyChart"],.support-table-wrap,.metric-row,[data-testid="stDataFrame"]{{break-inside:avoid;page-break-inside:avoid}}
    .support-table-wrap{{max-height:none!important;overflow:visible!important}}
    .print-break{{break-before:page}}
  }}
  @media(max-width:900px){{.metric-row{{grid-template-columns:repeat(2,1fr)}}}}
</style>
""", unsafe_allow_html=True)

st.markdown('<div id="top"></div>', unsafe_allow_html=True)
logo = img_data(ROOT / "logos_formularios_hd.png")
if logo:
    st.markdown(f'<div class="logo-crop"><img src="data:image/png;base64,{logo}" alt="Logotipos de las organizaciones participantes"></div>', unsafe_allow_html=True)
# Socios nuevos: cualquier archivo de imagen que se coloque en esta carpeta
# aparece aquí automáticamente en el próximo reinicio, sin tocar código ni
# el banner compuesto de arriba.
_logos_dir = ROOT / "logos_socios"
_mime_by_ext = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}
_extra_logo_files = sorted(p for p in _logos_dir.glob("*") if p.suffix.lower() in _mime_by_ext) if _logos_dir.exists() else []
_extra_logo_imgs = [f'<img src="data:{_mime_by_ext[p.suffix.lower()]};base64,{img_data(p)}" alt="{p.stem}">' for p in _extra_logo_files if img_data(p)]
if _extra_logo_imgs:
    st.markdown(f'<div class="logo-strip-extra">{"".join(_extra_logo_imgs)}</div>', unsafe_allow_html=True)
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
st.markdown(f'<div class="hero"><b>OPS/OMS · CLÚSTER DE SALUD · VENEZUELA</b><h1>Tablero de la Respuesta en Salud del terremoto en Venezuela (M7.2 y M7.5)</h1><p>Presencia operativa, programación de actividades y resultados reportados</p><small>Fecha de consulta: {hero_today_label} · Periodo de reportes: {date_floor.strftime("%d/%m/%Y")} – {date_ceiling.strftime("%d/%m/%Y")}</small></div>', unsafe_allow_html=True)

RADIO_MODULE_OPTIONS=["Registro de organizaciones e intervenciones", "Reportes periódicos"]
CALENDAR_MODULE="Calendario de brigadas"
if "active_module" not in st.session_state:
    st.session_state.active_module = RADIO_MODULE_OPTIONS[1] if st.query_params.get("vista")=="reportes" else RADIO_MODULE_OPTIONS[0]
if "main_view_radio" not in st.session_state:
    st.session_state.main_view_radio = st.session_state.active_module if st.session_state.active_module in RADIO_MODULE_OPTIONS else RADIO_MODULE_OPTIONS[0]

def _go_to_calendar_module() -> None:
    st.session_state.active_module = CALENDAR_MODULE
    # Desmarcar el radio principal. Si quedara seleccionado "Registro",
    # volver a hacer clic sobre la misma opción no dispararía on_change y la
    # aplicación permanecería atrapada en el calendario.
    st.session_state.main_view_radio = None
    st.query_params.pop("vista", None)

def _sync_module_from_radio() -> None:
    selected = st.session_state.main_view_radio
    if selected in RADIO_MODULE_OPTIONS:
        st.session_state.active_module = selected
        if selected == RADIO_MODULE_OPTIONS[1]:
            st.query_params["vista"] = "reportes"
        else:
            st.query_params.pop("vista", None)

nav_radio_col, nav_calendar_col = st.columns([5, 1.7])
with nav_radio_col:
    st.radio("Vista principal", RADIO_MODULE_OPTIONS, horizontal=True, label_visibility="collapsed", key="main_view_radio", on_change=_sync_module_from_radio)
with nav_calendar_col:
    with st.container(key="calendar_nav_btn"):
        st.button("📅 Calendario de brigadas", on_click=_go_to_calendar_module, key="calendar_module_btn", width="stretch")
module = st.session_state.active_module

states = sorted(set(points["estado"].dropna()) | set(reports["estado"].dropna()))
municipios = sorted(set(points["municipio"].dropna()) | set(reports["municipio"].dropna()))
orgs = sorted(set(points["organizacion"].dropna()) | set(reports["organizacion"].dropna()))
st.sidebar.markdown("### Filtros")
# Orden: primero la jerarquía geográfica (Estado → Municipio), luego
# Organización y Foco (Organización antes que Foco, a pedido), y por
# último el rango de fecha.
state = st.sidebar.selectbox("Estado", ["Todos"] + states)
municipio_filter = st.sidebar.selectbox("Municipio", ["Todos"] + municipios)
org = st.sidebar.selectbox("Organización", ["Todas"] + orgs)
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
nav_buttons = '<div class="sidebar-nav"><a href="#top">Inicio</a></div>'
st.sidebar.markdown(nav_buttons, unsafe_allow_html=True)
update_buttons = (
    '<div class="sidebar-update">'
    '<a href="https://ee.kobotoolbox.org/x/gjyvWedn" target="_blank" rel="noopener">Actualizar datos F01</a>'
    '<a href="https://ee.kobotoolbox.org/x/pQqj0V1d" target="_blank" rel="noopener">Actualizar datos F02</a>'
    '</div>'
)
st.sidebar.markdown(update_buttons, unsafe_allow_html=True)

def filt(frame: pd.DataFrame, has_focus: bool = False) -> pd.DataFrame:
    out=frame.copy()
    if state != "Todos" and "estado" in out: out=out[out["estado"].eq(state)]
    if municipio_filter != "Todos" and "municipio" in out: out=out[out["municipio"].eq(municipio_filter)]
    if org != "Todas" and "organizacion" in out: out=out[out["organizacion"].eq(org)]
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
    f01_report_dates=pd.concat([pd.to_datetime(d["facilities"].fecha_reporte,errors="coerce"),pd.to_datetime(d["reports_prepared"].fecha_reporte,errors="coerce")]).dropna()
    first_f01=f01_report_dates.min().strftime("%d/%m/%Y"); last_f01=f01_report_dates.max().strftime("%d/%m/%Y")
    section_band("SOCIOS Y APOYOS DEL CLÚSTER SALUD", "¿Quiénes son los socios, qué ofrecen, a qué establecimientos de salud apoyan y qué acciones de salud pública realizan?")
    st.markdown('<div class="index-label">Ir a la sección</div><div class="index"><a href="#mapa-f01">01 · Mapa</a><a href="#socios-f01">02 · Socios</a><a href="#territorio-f01">03 · Alcance territorial</a><a href="#apoyos-establecimientos-f01">04 · Apoyo a establecimientos de salud</a><a href="#acciones-publicas-f01">05 · Acciones de salud pública</a><a href="#capacidad-f01">06 · Capacidad operativa</a><a href="#inversion-f01">07 · Inversión</a></div>',unsafe_allow_html=True)
    professional_total=pd.to_numeric(staffing[all_staff_cols].stack(),errors="coerce").sum()
    parish_count=p.loc[p.parroquia.ne("No reportada"),"parroquia"].nunique()
    cards=[
        ("Estados",num(p.estado.nunique()),""),
        ("Municipios",num(p.municipio.nunique()),""),
        ("Parroquias",num(parish_count),""),
        ("Socios",num(p.organizacion.nunique()),""),
        ("Puntos de intervención",num(p.id_punto.nunique()),""),
        ("Total profesionales reportados",num(professional_total),""),
        ("Inversión registrada",f'{num(p.inversion_usd.sum())}<br><span style="font-size:.5em;color:{MUTED};">USD</span>',""),
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
        add_map_marker_outline(fig, mapped, "inversion_usd")
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

    st.markdown(f'<div id="socios-f01" class="section-kicker">02 · Socios</div><h2>Organizaciones y su alcance · {num(p.organizacion.nunique())} socios · {num(p.id_punto.nunique())} puntos</h2>',unsafe_allow_html=True)
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
    fig.update_layout(showlegend=False,annotations=[dict(text=f"<b>{num(point_distribution.puntos.sum())}</b><br>intervenciones",x=.5,y=.5,showarrow=False,font=dict(size=15,color=INK))])
    plot(fig,410,"f01_focus_distribution")
    section_figures.setdefault("Presencia de socios", []).append(fig)
    st.caption("El gráfico muestra cómo se distribuye el total de puntos registrados en F01 entre los dos focos de intervención.")

    st.markdown("### Composición de socios")
    comp=active_places.groupby("tipo_organizacion").organizacion.nunique().reset_index(name="socios").sort_values("socios")
    # Ancho completo y etiquetas "outside": con 6 categorías (algunas con un
    # solo socio) el donut a media columna recortaba los nombres más largos.
    fig=px.pie(comp,names="tipo_organizacion",values="socios",hole=.5,color_discrete_sequence=PALETTE_EXT[:6])
    fig.update_traces(textinfo="label+percent",textposition="outside",textfont=dict(size=13))
    fig.update_layout(showlegend=False)
    plot(fig,520,"partner_composition",margin=dict(l=110,r=110,t=52,b=30))
    section_figures.setdefault("Presencia de socios", []).append(fig)

    st.markdown("#### Modalidad de implementación")
    impl=active_places.groupby("modalidad_implementacion").organizacion.nunique().reset_index(name="socios").sort_values("socios")
    fig=px.bar(impl,x="socios",y="modalidad_implementacion",orientation="h",text="socios",color_discrete_sequence=[BLUE],labels={"socios":"Socios","modalidad_implementacion":""}); fig.update_traces(textposition="outside"); plot(fig,320,"implementation_mode")
    section_figures.setdefault("Presencia de socios", []).append(fig)

    st.markdown("#### Fuentes de apoyo declaradas por los socios")
    donor=filt(d["donor_sources"].copy())
    donor["estado_fuente"]=donor["donantes"].map(lambda x:"Fuente identificada" if x != "Sin fuente reportada" else "Sin fuente reportada")
    source_status=donor.groupby("estado_fuente").id_servicio.nunique().reset_index(name="puntos")
    named=donor[donor.donantes.ne("Sin fuente reportada")][["id_servicio","donantes"]].copy()
    named_sources=named.groupby("donantes").id_servicio.nunique().nlargest(10).sort_values().reset_index(name="puntos")
    with st.container(border=True):
        dl,dr=st.columns([.72,1.28])
        with dl:
            st.markdown("##### Disponibilidad de información")
            fig=px.pie(source_status,names="estado_fuente",values="puntos",hole=.62,color="estado_fuente",color_discrete_map={"Fuente identificada":BLUE,"Sin fuente reportada":GRAY})
            fig.update_traces(textinfo="percent+value",textposition="inside"); plot(fig,470,"donor_status")
            section_figures.setdefault("Presencia de socios", []).append(fig)
        with dr:
            st.markdown("##### Fuentes mencionadas con mayor frecuencia")
            if named_sources.empty: st.info("No se identificaron fuentes para los filtros seleccionados.")
            else:
                fig=px.bar(named_sources,x="puntos",y="donantes",orientation="h",text="puntos",color_discrete_sequence=[NAVY],labels={"puntos":"Puntos respaldados","donantes":""})
                fig.update_traces(textposition="outside"); plot(fig,470,"donor_sources")
                section_figures.setdefault("Presencia de socios", []).append(fig)
    st.caption("La visualización cuenta puntos en los que una organización declaró una fuente de apoyo. No representa montos ni atribuye financiamiento a un servicio específico.")

    st.markdown('<div id="territorio-f01" class="section-kicker">03 · Alcance territorial</div><h2>Presencia territorial de socios</h2>',unsafe_allow_html=True)
    territory=active_places.copy(); territory["parroquia"]=territory.parroquia.fillna("No reportada")
    top_states=territory.groupby("estado").organizacion.nunique().nlargest(8).sort_values().reset_index(name="socios")
    top_munis=territory.groupby(["estado","municipio"]).organizacion.nunique().nlargest(10).sort_values().reset_index(name="socios"); top_munis["territorio"]=top_munis.municipio+" ("+top_munis.estado+")"
    # Un solo gráfico con selector (antes eran dos gráficos fijos lado a
    # lado): el usuario elige el nivel territorial y la misma gráfica se
    # redibuja; municipio tiene más categorías, así que la altura crece con
    # la cantidad de barras para que ninguna quede apretada.
    territory_view=st.radio("Ver por",["Estado","Municipio"],index=1,horizontal=True,key="territory_view_f01")
    if territory_view=="Estado":
        st.markdown("#### Estados con mayor presencia")
        # Color por intensidad (no un solo tono plano): la barra más larga ya
        # lo dice, pero el degradado refuerza a simple vista dónde hay más
        # presencia, sobre todo al exportar la gráfica sola al PDF.
        fig=px.bar(top_states,x="socios",y="estado",orientation="h",text="socios",color="socios",color_continuous_scale=[[0,tint(NAVY,.82)],[1,NAVY]],labels={"socios":"Socios activos","estado":""})
        fig.update_traces(textposition="outside"); fig.update_layout(coloraxis_showscale=False); plot(fig,max(390,40*len(top_states)+90),"top_states_f01")
        section_figures.setdefault("Presencia de socios", []).append(fig)
    else:
        st.markdown("#### Municipios con mayor presencia")
        fig=px.bar(top_munis,x="socios",y="territorio",orientation="h",text="socios",color="socios",color_continuous_scale=[[0,tint(SKY,.75)],[1,SKY]],labels={"socios":"Socios activos","territorio":""})
        fig.update_traces(textposition="outside"); fig.update_layout(coloraxis_showscale=False); plot(fig,max(390,40*len(top_munis)+90),"top_munis_f01")
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
    st.markdown('<div id="apoyos-establecimientos-f01" class="section-kicker">04 · Apoyos a establecimientos de salud</div><h2>Servicios y apoyos que las organizaciones brindan</h2>',unsafe_allow_html=True)
    facility_points_all=p[p.foco.eq("Establecimiento de salud")]
    facility_type_filter=st.selectbox("Filtrar por tipo de establecimiento de salud",["Todos"]+sorted(facility_points_all.tipo_punto.dropna().unique()),key="facility_type_filter")
    facility_points=facility_points_all[facility_points_all.tipo_punto.eq(facility_type_filter)] if facility_type_filter!="Todos" else facility_points_all
    facility_types=facility_points.groupby("tipo_punto").id_punto.nunique().reset_index(name="puntos")
    facility_orgs=facility_points.groupby("organizacion").id_punto.nunique().reset_index(name="puntos")

    # La dona ocupa menos ancho para que el cuadro de organizaciones (lo
    # más importante de esta comparación) tenga más espacio.
    with st.container(border=True):
        facility_left,facility_right=st.columns([1.6,3.4],gap="small")
        with facility_left:
            st.markdown("#### Tipo de establecimiento de salud")
            if facility_types.empty: st.info("No hay establecimientos para los filtros seleccionados.")
            else:
                fig=px.pie(facility_types,names="tipo_punto",values="puntos",hole=.52,
                           color_discrete_sequence=PALETTE_EXT[:7],
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
                               color_discrete_sequence=PALETTE_EXT[:9],
                               labels={"organizacion":"Organización","puntos":"Establecimientos"})
                fig.update_traces(hovertemplate="%{customdata[0]}<br>%{value} establecimientos<extra></extra>",
                                  marker_line_color=PAPER,marker_line_width=3)
                set_org_tile_labels(fig)
                fig.update_layout(showlegend=False)
                plot(fig,700,"facility_orgs_treemap",margin=dict(l=4,r=4,t=10,b=12))
                section_figures.setdefault("Presencia de socios", []).append(fig)
                st.caption("El tamaño de cada bloque representa el número de establecimientos apoyados.")

    # F01 tiene dos preguntas reales distintas para este foco (5.1 áreas que
    # reciben apoyo, 5.2 tipo de apoyo brindado, en el mismo orden que el
    # formulario), mostradas una al lado de la otra para compararlas.
    fs=filt(d["facility_services_joined"])
    if facility_type_filter!="Todos": fs=fs[fs.tipo_punto.eq(facility_type_filter)]
    ft=fs.servicio.value_counts().head(10).reset_index(); ft.columns=["servicio","puntos"]; ft["foco"]="Establecimiento de salud"
    fsup=filt(d["facility_supports_joined"])
    if facility_type_filter!="Todos": fsup=fsup[fsup.tipo_punto.eq(facility_type_filter)]
    ftsup=fsup.tipo_apoyo.value_counts().head(10).reset_index(); ftsup.columns=["tipo_apoyo","puntos"]
    with st.container(border=True):
        services_col,supports_col=st.columns(2,gap="small")
        with services_col:
            st.markdown("##### Áreas o servicios que reciben apoyo")
            if ft.empty: st.info("No hay servicios registrados para los filtros seleccionados.")
            else:
                ft["_tilecolor"]=value_gradient_colors(ft.puntos,[[0,tint(ORANGE,.82)],[1,ORANGE]])
                fig=px.treemap(ft,path=["servicio"],values="puntos",color="servicio",color_discrete_map=dict(zip(ft.servicio,ft._tilecolor)))
                set_value_tile_labels(fig,"puntos"); plot(fig,560,"facility_services_tree",margin=dict(l=6,r=6,t=10,b=6))
                section_figures.setdefault("Paquetes de apoyo", []).append(fig)
                st.caption("El tamaño de cada bloque representa los puntos que reciben apoyo en esa área.")
        with supports_col:
            st.markdown("##### Tipo de apoyo proporcionado")
            if ftsup.empty: st.info("No hay tipos de apoyo registrados para los filtros seleccionados.")
            else:
                ftsup["_tilecolor"]=value_gradient_colors(ftsup.puntos,[[0,tint(ORANGE,.82)],[1,ORANGE]])
                fig=px.treemap(ftsup,path=["tipo_apoyo"],values="puntos",color="tipo_apoyo",color_discrete_map=dict(zip(ftsup.tipo_apoyo,ftsup._tilecolor)))
                set_value_tile_labels(fig,"puntos"); plot(fig,560,"facility_supports_tree",margin=dict(l=6,r=6,t=10,b=6))
                section_figures.setdefault("Paquetes de apoyo", []).append(fig)
                st.caption("El tamaño de cada bloque representa los puntos que recibieron ese tipo de apoyo.")

    # Los cuadros de arriba solo muestran totales por área/tipo de apoyo, sin
    # nombres. Aquí se lista cada establecimiento del universo filtrado con
    # su cantidad total de apoyos recibidos (sumando áreas + tipos de apoyo
    # combinados), de mayor a menor, para ver de un vistazo quién recibe más
    # apoyo y quién recibe menos (incluyendo quienes no reciben ninguno). Se
    # muestra como tabla (Organización / Establecimiento / barra) en vez de
    # un gráfico de barras de Plotly, porque un eje Y de texto largo
    # ("Establecimiento — Organización") no se puede alinear en columnas
    # separadas — una tabla sí lo permite.
    st.markdown("##### Establecimientos por nivel de apoyo recibido")
    rank_f1,rank_f2,rank_f3=st.columns(3)
    with rank_f1:
        rank_area=st.selectbox("Filtrar por área o servicio",["Todas"]+sorted(fs.servicio.dropna().unique()),key="rank_filter_area")
    with rank_f2:
        rank_support=st.selectbox("Filtrar por tipo de apoyo",["Todos"]+sorted(fsup.tipo_apoyo.dropna().unique()),key="rank_filter_support")
    with rank_f3:
        rank_tipologia=st.selectbox("Filtrar por tipología",["Todas"]+sorted(facility_points.tipo_punto.dropna().unique()),key="rank_filter_tipologia")
    combined_support=pd.concat([
        fs[["organizacion","lugar"]],
        fsup[["organizacion","lugar"]],
    ],ignore_index=True)
    support_counts=combined_support.groupby(["organizacion","lugar"]).size().reset_index(name="apoyos")
    ranking=facility_points[["organizacion","lugar","tipo_punto"]].drop_duplicates().merge(support_counts,on=["organizacion","lugar"],how="left")
    ranking["apoyos"]=ranking["apoyos"].fillna(0).astype(int)
    if rank_tipologia!="Todas":
        ranking=ranking[ranking.tipo_punto.eq(rank_tipologia)]
    if rank_area!="Todas":
        area_keys=set(map(tuple,fs[fs.servicio.eq(rank_area)][["organizacion","lugar"]].drop_duplicates().itertuples(index=False,name=None)))
        ranking=ranking[ranking.apply(lambda row:(row.organizacion,row.lugar) in area_keys,axis=1)]
    if rank_support!="Todos":
        support_keys=set(map(tuple,fsup[fsup.tipo_apoyo.eq(rank_support)][["organizacion","lugar"]].drop_duplicates().itertuples(index=False,name=None)))
        ranking=ranking[ranking.apply(lambda row:(row.organizacion,row.lugar) in support_keys,axis=1)]
    if ranking.empty:
        st.info("Ningún establecimiento cumple con los filtros seleccionados.")
    else:
        ranking=ranking.sort_values("apoyos",ascending=False).rename(columns={"organizacion":"Organización","lugar":"Establecimiento","tipo_punto":"Tipología","apoyos":"Apoyos recibidos"})
        support_ranking_table(ranking)
        n_zero=int((ranking["Apoyos recibidos"]==0).sum())
        st.caption(f"Cada fila suma las áreas y los tipos de apoyo recibidos por ese establecimiento (no montos). {n_zero} establecimiento(s) de la selección actual no registran ningún apoyo.")

    # Vocabulario alineado al F01 real: la organización "realiza acciones de
    # salud pública" en ciertas "áreas temáticas" (pregunta sobre
    # intervention_areas) — tampoco usa "oferta".
    st.markdown('<div id="acciones-publicas-f01" class="section-kicker">05 · Acciones de salud pública</div><h2>Áreas de acción y cobertura de salud pública</h2>',unsafe_allow_html=True)
    public_points_for_charts=p[p.foco.eq("Salud pública")]
    public_types=public_points_for_charts.groupby("tipo_punto").id_punto.nunique().reset_index(name="puntos")
    public_orgs=public_points_for_charts.groupby("organizacion").id_punto.nunique().reset_index(name="puntos")
    with st.container(border=True):
        public_left,public_right=st.columns([1.6,3.4],gap="small")
        with public_left:
            st.markdown("#### Tipo de lugar de intervención")
            if public_types.empty: st.info("No hay lugares de intervención para los filtros seleccionados.")
            else:
                # Misma dona multicolor que "Tipo de establecimiento de salud",
                # en vez de barra de un solo color, para que ambos focos se
                # lean con el mismo lenguaje visual.
                fig=px.pie(public_types,names="tipo_punto",values="puntos",hole=.52,
                           color_discrete_sequence=PALETTE_EXT[:9],
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
                               color_discrete_sequence=PALETTE_EXT[:9],
                               labels={"organizacion":"Organización","puntos":"Lugares"})
                fig.update_traces(hovertemplate="%{customdata[0]}<br>%{value} lugares<extra></extra>",
                                  marker_line_color=PAPER,marker_line_width=3)
                set_org_tile_labels(fig)
                fig.update_layout(showlegend=False)
                plot(fig,700,"public_orgs_treemap",margin=dict(l=4,r=4,t=10,b=12))
                section_figures.setdefault("Presencia de socios", []).append(fig)
                st.caption("El tamaño de cada bloque representa el número de lugares de intervención.")

    po=filt(d["offered_joined"]); pt=po.servicio.value_counts().head(10).reset_index(); pt.columns=["servicio","puntos"]; pt["foco"]="Salud pública"
    st.markdown("#### Áreas temáticas de acciones de salud pública")
    if pt.empty: st.info("No hay acciones registradas para los filtros seleccionados.")
    else:
        pt["_tilecolor"]=value_gradient_colors(pt.puntos,[[0,tint(SKY,.82)],[1,SKY]])
        fig=px.treemap(pt,path=["servicio"],values="puntos",color="servicio",color_discrete_map=dict(zip(pt.servicio,pt._tilecolor)))
        set_value_tile_labels(fig,"puntos"); plot(fig,540,"public_actions_tree")
        section_figures.setdefault("Paquetes de apoyo", []).append(fig)

    st.markdown('<div id="capacidad-f01" class="section-kicker">06 · Capacidad operativa</div><h2>Modalidad de atención y personal disponible</h2>',unsafe_allow_html=True)
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
        totals["_tilecolor"]=value_gradient_colors(totals.personas,[[0,tint(NAVY,.88)],[.55,tint(NAVY,.42)],[1,NAVY]])
        fig=px.treemap(totals,path=["perfil"],values="personas",color="perfil",color_discrete_map=dict(zip(totals.perfil,totals._tilecolor)))
        set_value_tile_labels(fig,"personas"); plot(fig,590,"staffing_tree")
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
            fig=px.imshow(matrix,text_auto=True,color_continuous_scale=[[0,tint(NAVY,.92)],[1,NAVY]],labels=dict(x="Día",y="Horario",color="Puntos"),aspect="auto",zmin=0,zmax=max(1,matrix.values.max()))
            fig.update_xaxes(side="top"); fig.update_traces(textfont_size=14)
            plot(fig,340,"schedule_heatmap")
            section_figures.setdefault("Personal", []).append(fig)
            st.caption(f"Con base en los {len(schedule_scope)} puntos que reportaron días y horarios de atención en el F01 (bloque «Días y horas de atención»); el resto de los puntos de la selección actual no tiene este dato registrado.")

    st.markdown(f'<div id="inversion-f01" class="section-kicker">07 · Recursos movilizados</div><h2>Inversión registrada · USD {num(p.inversion_usd.sum())}</h2>',unsafe_allow_html=True)
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
        fig.update_xaxes(type="log",showgrid=True,gridcolor=tint(GRAY,.7),tickfont=dict(size=12))
        fig.update_yaxes(showgrid=False,tickfont=dict(size=13,color=INK),ticksuffix="  ")
        plot(fig,max(320,54+42*len(order)),"f01_muni_investment",margin=dict(l=16,r=64,t=28,b=18))
        section_figures.setdefault("Inversión", []).append(fig)

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
    st.markdown("#### Vista completa")
    complete_dashboard_export()
    st.markdown("#### Resumen ejecutivo")
    report_palette={"navy":NAVY,"muted":MUTED,"ink":INK,"blue":BLUE,"border":tint(GRAY,.75)}
    top_states=p.groupby("estado").id_punto.nunique().nlargest(12).sort_values()
    top_munis=p.groupby("municipio").id_punto.nunique().nlargest(12).sort_values()
    top_orgs=p.groupby("organizacion").id_punto.nunique().nlargest(12).sort_values()
    top_muni_inv=ranked.head(12).sort_values()
    top_services=fs["servicio"].value_counts().nlargest(12).sort_values()
    top_supports=fsup["tipo_apoyo"].value_counts().nlargest(12).sort_values()
    report_staff=pd.to_numeric(staffing[all_staff_cols].stack(),errors="coerce").unstack().sum().sort_values(ascending=False).head(12).sort_values()
    report_staff.index=report_staff.index.str.replace("personal_","",regex=False).str.replace("_"," ").str.title()
    top_donors=donor.groupby("donantes").id_servicio.nunique().nlargest(12).sort_values()
    report_bytes=build_report(
        title="Tablero de la Respuesta en Salud del terremoto en Venezuela",
        subtitle="Socios y apoyos del Clúster Salud — Registro de organizaciones e intervenciones",
        scope_text="Universo vigente según los filtros de estado, municipio, organización, foco y rango de fecha activos.",
        as_of_text=f"Generado el {pd.Timestamp.today().strftime('%d/%m/%Y')}",
        kpis=[
            ("Estados",num(p.estado.nunique())),
            ("Municipios",num(p.municipio.nunique())),
            ("Socios",num(p.organizacion.nunique())),
            ("Puntos de intervención",num(p.id_punto.nunique())),
            ("Profesionales",num(professional_total)),
            ("Inversión (USD)",num(p.inversion_usd.sum())),
        ],
        sections=[
            {
                "title": "Panorama territorial",
                "rows": [[
                    ("Top estados por puntos de intervención",bar_chart(top_states.index.tolist(),top_states.tolist(),BLUE,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=190,width=350,label_width=110),12.7),
                    ("Top municipios por puntos de intervención",bar_chart(top_munis.index.tolist(),top_munis.tolist(),BLUE,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=190,width=350,label_width=120),12.7),
                ]],
                "caption": "Cuenta puntos de intervención distintos (F01) dentro del alcance de los filtros activos.",
            },
            {
                "title": "Socios y distribución por foco",
                "rows": [[
                    ("Top socios por puntos de intervención",bar_chart(top_orgs.index.tolist(),top_orgs.tolist(),NAVY,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=190,width=350,label_width=140),12.7),
                    ("Distribución por foco de intervención",composition_bar(point_distribution.foco_formulario.tolist(),point_distribution.puntos.tolist(),[RED,YELLOW],MUTED,width=350,height=115),12.7),
                ]],
                "caption": "El foco distingue acciones en el establecimiento de salud de acciones de salud pública.",
            },
            {
                "title": "Servicios y tipos de apoyo registrados",
                "rows": [[
                    ("Áreas o servicios que reciben apoyo",bar_chart(top_services.index.tolist(),top_services.tolist(),RED,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=220,width=350,label_width=145),12.7),
                    ("Tipos de apoyo proporcionado",bar_chart(top_supports.index.tolist(),top_supports.tolist(),ORANGE,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=220,width=350,label_width=145),12.7),
                ]],
                "caption": "Cuenta puntos registrados en F01 que declaran cada servicio o tipo de apoyo.",
            },
            {
                "title": "Capacidad operativa",
                "rows": [[
                    ("Personal disponible por perfil",bar_chart(report_staff.index.tolist(),report_staff.tolist(),RED,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=230,width=735,label_width=170),26.0),
                ]],
                "caption": "Suma del personal disponible reportado en los puntos incluidos por los filtros activos.",
            },
            {
                "title": "Inversión registrada por municipio",
                "rows": [[
                    ("Top municipios por inversión (USD)",bar_chart(top_muni_inv.index.tolist(),top_muni_inv.tolist(),ORANGE,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=190,width=735,label_width=150),26.0),
                ]],
                "caption": "Montos ilustrativos del simulacro (no representan cifras reales de financiamiento).",
            },
            {
                "title": "Fuentes que respaldan la oferta",
                "rows": [[
                    ("Puntos respaldados por fuente",bar_chart(top_donors.index.tolist(),top_donors.tolist(),TEAL,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=230,width=735,label_width=180),26.0),
                ]],
                "caption": "Cuenta vínculos fuente–punto reportados; no representa montos ni atribuye financiamiento a un servicio específico.",
            },
        ],
        palette=report_palette,
    )
    _, dl_col, _ = st.columns([1.0,1.15,1.0])
    with dl_col:
        st.download_button("Descargar resumen PDF",data=report_bytes,file_name=f"Resumen_F01_{pd.Timestamp.today().strftime('%Y%m%d')}.pdf",mime="application/pdf",width="stretch")
elif module.startswith("Reportes"):
    section_figures: dict[str, list[go.Figure]] = {}
    r=filt(reports,True); ids=set(r.id_reporte); res=results[results.id_reporte.isin(ids)].copy(); active=r.sort_values("fecha_reporte").drop_duplicates(["organizacion","nombre_sitio"],keep="last")
    section_band("REPORTE DE ACCIONES", "¿Qué acciones reportan los socios, dónde se realizan y qué resultados registran?")
    st.markdown('<div class="index-label">Ir a la sección</div><div class="index"><a href="#mapa-f02">01 · Mapa</a><a href="#reportantes-f02">02 · Organizaciones que reportaron</a><a href="#focos-f02">03 · Resumen de los Reportes</a><a href="#reportes-establecimientos-f02">04 · Reportes de establecimientos de salud</a><a href="#reportes-acciones-f02">05 · Reportes de acciones de salud pública</a></div>',unsafe_allow_html=True)
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
        add_map_marker_outline(fig, mapped, "reportes_punto")
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

    st.markdown('<div id="reportantes-f02" class="section-rule"></div><div class="section-kicker">02 · Organizaciones que reportaron</div>',unsafe_allow_html=True)
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

    st.markdown('<div id="focos-f02" class="section-rule"></div><div class="section-kicker">03 · Distribución por foco</div><h2>Reportes y puntos activos por foco de intervención</h2>',unsafe_allow_html=True)
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
        type_palette=[color]+[c for c in PALETTE_EXT if c!=color][:8]
        type_order=sorted(tipo_full.tipo_punto.unique())
        type_color_map=dict(zip(type_order,type_palette))
        with st.container(border=True):
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
        # Un color por municipio, calculado sobre TODOS los municipios con
        # reportes de este foco (no solo el top 10 de la barra), para que
        # el mismo municipio tenga siempre el mismo color acá y en el mapa
        # de abajo, esté o no entre los primeros 10. Mismo criterio para
        # estado.
        territory_palette=PALETTE_EXT
        all_reporting_munis=sorted(subset.municipio.dropna().unique())
        muni_color_map={m:territory_palette[i%len(territory_palette)] for i,m in enumerate(all_reporting_munis)}
        all_reporting_states=sorted(subset.estado.dropna().unique())
        state_color_map={s:territory_palette[i%len(territory_palette)] for i,s in enumerate(all_reporting_states)}

        # Un solo gráfico con selector Estado/Municipio (antes eran dos
        # gráficos fijos lado a lado) — mismo patrón que "Alcance
        # territorial" de F01. El mapa de abajo usa el mismo selector para
        # que sus polígonos (estadales o municipales) cambien junto con la
        # gráfica en vez de quedar siempre a nivel municipio.
        territory_view=st.radio("Ver por",["Estado","Municipio"],index=1,horizontal=True,key=f"territory_view_{suffix}")
        if territory_view=="Estado":
            st.markdown("#### Estados con más reportes")
            if rank_state.empty: st.info("No hay estados para este foco con los filtros seleccionados.")
            else:
                fig=px.bar(rank_state,x="reportes",y="estado",orientation="h",text="reportes",color="estado",
                           color_discrete_map=state_color_map,
                           category_orders={"estado":rank_state.estado.tolist()},labels={"reportes":"Reportes","estado":""})
                fig.update_traces(textposition="outside")
                fig.update_layout(showlegend=False)
                plot(fig,max(320,44*len(rank_state)+80),f"rank_state_{suffix}")
                section_figures.setdefault(bucket, []).append(fig)
        else:
            st.markdown("#### Municipios con más reportes")
            if rank_muni.empty: st.info("No hay municipios para este foco con los filtros seleccionados.")
            else:
                fig=px.bar(rank_muni,x="reportes",y="territorio",orientation="h",text="reportes",color="municipio",
                           color_discrete_map=muni_color_map,
                           category_orders={"territorio":rank_muni.territorio.tolist()},labels={"reportes":"Reportes","territorio":"","municipio":"Municipio"})
                fig.update_traces(textposition="outside")
                # Sin leyenda propia: el color de cada municipio ya se
                # explica en el mapa de abajo (mismo mapeo municipio→color).
                fig.update_layout(showlegend=False)
                plot(fig,max(320,44*len(rank_muni)+80),f"rank_muni_{suffix}")
                section_figures.setdefault(bucket, []).append(fig)

        st.markdown("#### Distribución geográfica de los reportes")
        if territory_view=="Estado":
            # Polígonos estadales (límites oficiales): mismo criterio que la
            # vista municipal de abajo, pero un nivel más arriba.
            state_geo_reports=subset.groupby("estado").id_reporte.nunique().reset_index(name="reportes")
            if state_geo_reports.empty:
                st.info("No hay reportes con estado para este foco con los filtros seleccionados.")
            else:
                state_geo_reports["join_key"]=state_geo_reports.estado
                geo=load_estado_geojson()
                all_states_geo=pd.DataFrame([
                    {"estado":f["properties"]["adm1_name"],"join_key":f["properties"]["join_key"]}
                    for f in geo["features"]
                ]).drop_duplicates("join_key")
                full_state=all_states_geo.merge(state_geo_reports[["join_key","reportes"]],on="join_key",how="left")
                full_state["reportes"]=full_state.reportes.fillna(0)
                map_color_map={**state_color_map,"Sin reportes":tint(GRAY,.55)}
                full_state["color_key"]=full_state.estado.where(full_state.reportes>0,"Sin reportes")
                fig=px.choropleth_map(full_state,geojson=geo,locations="join_key",featureidkey="properties.join_key",
                                       color="color_key",color_discrete_map=map_color_map,
                                       hover_name="estado",hover_data={"reportes":True,"join_key":False,"color_key":False},
                                       map_style="carto-positron",center={"lat":8.0,"lon":-66.0},opacity=.85,
                                       labels={"color_key":"Estado"})
                fig.update_traces(marker_line_color="white",marker_line_width=1.2)
                # Encuadre dinámico según los estados con datos (no un
                # zoom/centro fijo para todo el país): un estado costero
                # chico como La Guaira, solo, quedaba casi invisible con el
                # encuadre fijo anterior.
                reported_states=set(state_geo_reports.loc[state_geo_reports.reportes>0,"estado"]) or set(state_geo_reports.estado)
                bounds=geojson_lat_lon_bounds(geo,reported_states)
                if bounds:
                    lat_lo,lat_hi,lon_lo,lon_hi=bounds
                    lat_pad=max(0.15,(lat_hi-lat_lo)*0.35); lon_pad=max(0.15,(lon_hi-lon_lo)*0.35)
                    fig.update_layout(map=dict(bounds=dict(
                        west=max(-180,lon_lo-lon_pad), east=min(180,lon_hi+lon_pad),
                        south=max(-90,lat_lo-lat_pad), north=min(90,lat_hi+lat_pad),
                    )))
                plot(fig,480,f"rank_map_{suffix}",is_map=True)
                section_figures.setdefault(bucket, []).append(fig)
                st.caption("Polígonos de estado (límites oficiales); cada estado tiene su propio color para distinguirlo a simple vista — pase el cursor sobre un polígono para ver su número de reportes. Los estados sin reportes en este foco se muestran en gris.")
        else:
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
                # Mismo mapeo municipio→color que "Municipios con más reportes"
                # de arriba (muni_color_map), para que el color de un municipio
                # sea idéntico en la barra y en el mapa. Los que no tienen
                # reportes se agrupan en un gris neutro aparte.
                map_color_map={**muni_color_map,"Sin reportes":tint(GRAY,.55)}
                full_muni["color_key"]=full_muni.municipio.where(full_muni.reportes>0,"Sin reportes")
                fig=px.choropleth_map(full_muni,geojson=geo,locations="join_key",featureidkey="properties.join_key",
                                       color="color_key",color_discrete_map=map_color_map,
                                       hover_name="municipio",hover_data={"estado":True,"reportes":True,"join_key":False,"color_key":False},
                                       map_style="carto-positron",zoom=7.6,center={"lat":10.30,"lon":-66.98},opacity=.85,
                                       labels={"color_key":"Municipio"})
                fig.update_traces(marker_line_color="white",marker_line_width=1.2)
                plot(fig,480,f"rank_map_{suffix}",is_map=True)
                section_figures.setdefault(bucket, []).append(fig)
                st.caption("Polígonos de municipio (límites oficiales); cada municipio tiene su propio color para distinguirlo a simple vista — pase el cursor sobre un polígono para ver su número de reportes. Los municipios sin reportes en este foco se muestran en gris.")
            st.caption("Polígonos de municipio (límites oficiales); cada municipio tiene su propio color para distinguirlo a simple vista — pase el cursor sobre un polígono para ver su número de reportes. Los municipios sin reportes en este foco se muestran en gris.")

        # Orden pedido: primero las áreas reportadas (4.1-style, "qué se
        # reportó"), y luego el tipo de apoyo institucional reportado sobre
        # esas áreas (3.11.x, "qué apoyo se dio") — antes iba al revés.
        # Título distinto según el foco: "Salud pública" muestra volumen real
        # con unidad de medida (resultados.csv); "Establecimiento de salud"
        # muestra el simulacro de la pregunta 4.1 real del formulario, con
        # el texto exacto de esa pregunta en vez del título genérico de
        # "unidad de medida" (que no aplica a esta pregunta).
        st.markdown("### " + ("Volumen reportado por área y unidad de medida" if raw_name=="Salud pública" else "Áreas o servicios del establecimiento de salud que recibieron apoyo"))
        if raw_name=="Salud pública":
            area_subset=res[res.foco.eq(raw_name)]
            areas_by_unit=area_subset.groupby(["area","unidad"],as_index=False).total.sum()
            if areas_by_unit.empty:
                st.info("No hay resultados para este foco con los filtros seleccionados.")
            else:
                # Menos áreas (6 en vez de 8) para que cada bloque quede más
                # grande y legible; antes las más chicas eran franjas casi
                # ilegibles.
                top_areas=areas_by_unit.groupby("area").total.sum().nlargest(6).index
                # Un solo nivel (estilo OEC): un bloque por área. La jerarquía
                # área→unidad se descartó porque Plotly reserva una franja de
                # encabezado propia para cada área-padre y la repite dentro
                # del bloque hijo cuando solo hay una unidad (nombre
                # duplicado), y las unidades minoritarias quedaban en franjas
                # demasiado angostas para su texto (se salía del cuadro). El
                # total por área ya suma sus unidades (se aclara en el
                # caption que no deben interpretarse como la misma unidad).
                areas=areas_by_unit[areas_by_unit.area.isin(top_areas)].groupby("area",as_index=False).total.sum()
                fig=px.treemap(areas,path=["area"],values="total",color="area",
                               color_discrete_sequence=PALETTE_EXT[:8])
                vals=list(fig.data[0].values)
                vmin,vmax=min(vals),max(vals); span=(vmax-vmin) or 1
                grand_total=sum(vals) or 1
                min_size,max_size=13,20
                texts=[]
                for name,val in zip(fig.data[0].labels,vals):
                    pct=val/grand_total*100
                    size=round(min_size+((val-vmin)/span)**0.5*(max_size-min_size))
                    wrapped_name="<br>".join(textwrap.wrap(str(name),width=16,break_long_words=False)) or str(name)
                    texts.append(f'<span style="font-size:{size}px">{wrapped_name}<br>{val:,.0f}<br>{pct:.1f}%</span>')
                fig.update_traces(text=texts,texttemplate="%{text}",textfont_size=max_size,textposition="middle center",
                                   marker_line_color="white",marker_line_width=2,root_color="white")
                plot(fig,480,f"f02_areas_{suffix}",margin=dict(l=16,r=16,t=16,b=16))
                section_figures.setdefault(bucket, []).append(fig)
                st.caption("Cada bloque muestra su total y el porcentaje del total general. Unidad de medida por serie: personas, casos, procedimientos, kits, profesionales o instituciones (no se repite en cada bloque; no deben sumarse entre sí como si fueran la misma unidad).")

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
                detail=area_subset[area_subset.area.eq(selected_area)].groupby(["indicador","unidad"],as_index=False).agg(
                    total=("total","sum"),mujeres=("mujeres","sum"),hombres=("hombres","sum"),
                    ninas=("ninas","sum"),ninos=("ninos","sum"),
                )
                if detail.empty:
                    st.info("No hay indicadores para esta área con los filtros seleccionados.")
                else:
                    detail["etiqueta"]=detail.indicador.str.replace(r"^Número de ","",regex=True).str.slice(0,70)
                    order=detail.sort_values("total",ascending=False).etiqueta.tolist()
                    # Barras apiladas por sexo/edad cuando el indicador es de
                    # unidad "personas" y trae desagregación real; el resto
                    # (sin desagregar, o en otra unidad — casos,
                    # procedimientos, kits...) se muestra como un solo
                    # segmento con la etiqueta de su unidad real, para no
                    # inventar un desglose por género que el dato no tiene.
                    is_personas=detail.unidad.str.lower().eq("personas")
                    gender_cols=["mujeres","hombres","ninas","ninos"]
                    detail.loc[~is_personas,gender_cols]=0
                    resto=(detail.total-detail[gender_cols].sum(axis=1)).clip(lower=0)
                    detail["resto"]=resto.where(is_personas,detail.total)
                    detail["resto_etiqueta"]=detail.unidad.str.capitalize().where(~is_personas,"Sin desagregar por sexo/edad")
                    rows=[]
                    for _,row in detail.iterrows():
                        for col,label in zip(gender_cols,["Mujeres","Hombres","Niñas","Niños"]):
                            if row[col]>0:
                                rows.append({"etiqueta":row.etiqueta,"grupo":label,"total":row[col]})
                        if row.resto>0:
                            rows.append({"etiqueta":row.etiqueta,"grupo":row.resto_etiqueta,"total":row.resto})
                    melted=pd.DataFrame(rows)
                    # Segmentos muy angostos (barra chica) no muestran el
                    # número adentro — a ese ancho el texto queda cortado o
                    # superpuesto con el del segmento vecino; mejor omitirlo
                    # que mostrarlo ilegible (el valor real sigue disponible
                    # al pasar el cursor).
                    chart_max=melted.groupby("etiqueta").total.sum().max() or 1
                    melted["texto"]=melted.total.map(lambda v: f"{v:,.0f}" if v/chart_max>=0.025 else "")
                    base_colors={"Mujeres":MUJERES_RED,"Hombres":BLUE,"Niñas":tint(MUJERES_RED,.45),"Niños":SKY}
                    extra_groups=sorted(set(melted.grupo)-set(base_colors))
                    extra_palette=[GRAY,tint(NAVY,.55),shade(GRAY,.2),tint(GRAY,.55)]
                    detail_colors={**base_colors,**{g:extra_palette[i%len(extra_palette)] for i,g in enumerate(extra_groups)}}
                    group_order=["Mujeres","Hombres","Niñas","Niños"]+extra_groups
                    fig=px.bar(melted,x="total",y="etiqueta",color="grupo",orientation="h",text="texto",barmode="stack",
                               category_orders={"etiqueta":order,"grupo":group_order},
                               color_discrete_map=detail_colors,
                               hover_data={"total":True,"texto":False},
                               labels={"total":"Total reportado","etiqueta":"","grupo":""})
                    fig.update_traces(textposition="inside",insidetextanchor="middle",textfont=dict(color="white",size=11))
                    fig.update_layout(legend=dict(orientation="h",yanchor="bottom",y=1.02,xanchor="right",x=1))
                    plot(fig,max(360,34*len(detail)+90),f"f02_area_detail_{suffix}",margin=dict(l=4,r=54,t=44,b=18))
                    section_figures.setdefault(bucket, []).append(fig)
                    st.caption(f"Indicadores reales de F02 dentro de «{selected_area}» (resultados.csv). Los indicadores de personas se dividen por sexo/edad cuando el formulario trae esa desagregación; los que no la traen, o están en otra unidad (casos, procedimientos, kits…), se muestran como un solo segmento con su unidad real.")
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
            # El bloque "3.11.x" de resultados.csv que antes se mostraba
            # aquí NO corresponde a ninguna pregunta real del formulario
            # F02 vigente (verificado contra el XLSForm: la sección 5 va de
            # 5.1 a 5.15, no existe un 5.11) — se retiró ese dato huérfano.
            # Igual que el bloque de áreas (4.1) de arriba, el histórico de
            # reportes periódicos nunca capturó "tipo de apoyo
            # proporcionado" (4.2) por período — solo existe a nivel de
            # registro (F01, apoyos.csv). Autorizado explícitamente por la
            # usuaria: se simula de forma determinística por reporte, con
            # el catálogo real de las 12 opciones de la pregunta 4.2 del
            # formulario vigente (FACILITY_SUPPORT_TYPE_CATALOG, tomado del
            # XLSForm — no del histórico de apoyos.csv, que solo cubre 5 de
            # esas 12 opciones), para representar cómo se vería este bloque
            # con datos reales, en vez de dejar la sección vacía.
            st.markdown("### Tipo de apoyo reportado")
            support_catalog=FACILITY_SUPPORT_TYPE_CATALOG
            sim_sup=subset[["id_reporte"]].drop_duplicates().copy()
            if sim_sup.empty or not support_catalog:
                st.info("No hay reportes para este foco con los filtros seleccionados.")
            else:
                sup_seed=pd.util.hash_pandas_object((sim_sup.id_reporte.astype(str)+"_sup"),index=False).astype("uint64")
                sim_sup["tipo_apoyo"]=[support_catalog[v % len(support_catalog)] for v in sup_seed]
                qty_seed=pd.util.hash_pandas_object((sim_sup.id_reporte.astype(str)+"_supqty"),index=False).astype("uint64")
                sim_sup["total"]=(qty_seed % 26 + 5)
                sup_totals=sim_sup.groupby("tipo_apoyo",as_index=False).total.sum().sort_values("total")
                # Mismo estilo OEC (cuadro único por categoría) que "Volumen
                # reportado por área y unidad de medida": un bloque por tipo
                # de apoyo, con su nombre, total y porcentaje del total
                # general, en vez de una barra horizontal.
                fig=px.treemap(sup_totals,path=["tipo_apoyo"],values="total",color="tipo_apoyo",
                               color_discrete_sequence=PALETTE_EXT[:8])
                vals=list(fig.data[0].values)
                vmin,vmax=min(vals),max(vals); span=(vmax-vmin) or 1
                grand_total=sum(vals) or 1
                min_size,max_size=13,20
                texts=[]
                for name,val in zip(fig.data[0].labels,vals):
                    pct=val/grand_total*100
                    size=round(min_size+((val-vmin)/span)**0.5*(max_size-min_size))
                    wrapped_name="<br>".join(textwrap.wrap(str(name),width=16,break_long_words=False)) or str(name)
                    texts.append(f'<span style="font-size:{size}px">{wrapped_name}<br>{val:,.0f}<br>{pct:.1f}%</span>')
                fig.update_traces(text=texts,texttemplate="%{text}",textfont_size=max_size,textposition="middle center",
                                   marker_line_color="white",marker_line_width=2,root_color="white")
                plot(fig,480,f"f02_support_{suffix}",margin=dict(l=16,r=16,t=16,b=16))
                section_figures.setdefault(bucket, []).append(fig)
                st.caption("Simulacro: el histórico de reportes F02 no capturó esta pregunta (4.2) por período — solo existe a nivel de registro (F01, apoyos.csv). Los valores se generan de forma determinística por reporte, usando el catálogo real de tipos de apoyo, para representar cómo se vería este bloque con datos reales; no son un dato reportado.")

            # Tabla de ranking por establecimiento, con datos REALES (no
            # simulados): reutiliza las mismas fuentes reales de F01
            # (areas.csv vía facility_services_joined, apoyos.csv vía
            # facility_supports_joined — bloques 5.1 y 5.2, registro, no
            # por período) que ya alimentan la tabla equivalente de F01,
            # acotadas a los establecimientos que sí presentaron reporte
            # periódico (F02) dentro del alcance de filtros actual. Mismo
            # esquema de 3 filtros que en F01 (área, tipo de apoyo,
            # tipología).
            st.markdown("### Establecimientos por nivel de apoyo recibido")
            fs_f02=d["facility_services_joined"]
            fsup_f02=d["facility_supports_joined"]
            reported_establishments=subset[["organizacion","nombre_sitio","tipo_punto"]].drop_duplicates().rename(columns={"nombre_sitio":"lugar"})
            rank_f1,rank_f2,rank_f3=st.columns(3)
            with rank_f1:
                rank_area=st.selectbox("Filtrar por área o servicio",["Todas"]+sorted(fs_f02.servicio.dropna().unique()),key=f"rank_filter_area_{suffix}")
            with rank_f2:
                rank_support=st.selectbox("Filtrar por tipo de apoyo",["Todos"]+sorted(fsup_f02.tipo_apoyo.dropna().unique()),key=f"rank_filter_support_{suffix}")
            with rank_f3:
                rank_tipologia=st.selectbox("Filtrar por tipología",["Todas"]+sorted(reported_establishments.tipo_punto.dropna().unique()),key=f"rank_filter_tipologia_{suffix}")
            combined_support=pd.concat([fs_f02[["organizacion","lugar"]],fsup_f02[["organizacion","lugar"]]],ignore_index=True)
            support_counts=combined_support.groupby(["organizacion","lugar"]).size().reset_index(name="apoyos")
            ranking=reported_establishments.merge(support_counts,on=["organizacion","lugar"],how="left")
            ranking["apoyos"]=ranking["apoyos"].fillna(0).astype(int)
            if rank_tipologia!="Todas":
                ranking=ranking[ranking.tipo_punto.eq(rank_tipologia)]
            if rank_area!="Todas":
                area_keys=set(map(tuple,fs_f02[fs_f02.servicio.eq(rank_area)][["organizacion","lugar"]].drop_duplicates().itertuples(index=False,name=None)))
                ranking=ranking[ranking.apply(lambda row:(row.organizacion,row.lugar) in area_keys,axis=1)]
            if rank_support!="Todos":
                support_keys=set(map(tuple,fsup_f02[fsup_f02.tipo_apoyo.eq(rank_support)][["organizacion","lugar"]].drop_duplicates().itertuples(index=False,name=None)))
                ranking=ranking[ranking.apply(lambda row:(row.organizacion,row.lugar) in support_keys,axis=1)]
            if ranking.empty:
                st.info("Ningún establecimiento con reporte cumple con los filtros seleccionados.")
            else:
                ranking=ranking.sort_values("apoyos",ascending=False).rename(columns={"organizacion":"Organización","lugar":"Establecimiento","tipo_punto":"Tipología","apoyos":"Apoyos recibidos"})
                support_ranking_table(ranking)
                st.caption("Apoyos reales registrados en F01 (áreas y tipos de apoyo, bloques 5.1/5.2) para los establecimientos que presentaron reporte periódico (F02) dentro del alcance de los filtros activos. Dato real de registro, no simulado.")

        if include_population:
            st.markdown("### Composición de las acciones realizadas por género")
            gender_labels={"mujeres":"Mujeres","hombres":"Hombres","ninas":"Niñas","ninos":"Niños"}
            gender_colors={"Mujeres":MUJERES_RED,"Hombres":BLUE,"Niñas":tint(MUJERES_RED,.45),"Niños":SKY}
            people=res[res.foco.eq(raw_name) & res.unidad.str.lower().eq("personas") & res.tiene_desagregacion.eq(1)].copy()
            if people.empty:
                st.info("No hay personas desagregadas para este foco con los filtros seleccionados.")
            else:
                gender=people.groupby("area")[["mujeres","hombres","ninas","ninos"]].sum().reset_index().melt(id_vars="area",var_name="grupo",value_name="personas")
                gender["grupo"]=gender.grupo.map(gender_labels)
                # La dona solo suma sexo/edad (mujeres, hombres, niñas,
                # niños): son categorías mutuamente excluyentes que sí suman
                # el 100% de las personas atendidas. "Discapacidad" NO se
                # agrega aquí — es una característica que puede aplicar a
                # personas YA contadas en cualquiera de esos 4 grupos, así
                # que sumarla como una quinta porción duplicaría personas en
                # el total. Se muestra aparte, como cifra independiente.
                totals_gender=gender.groupby("grupo",as_index=False).personas.sum()
                disability_total=people.discapacidad.sum()
                gp_l,gp_r=st.columns(2)
                with gp_l:
                    fig=px.pie(totals_gender,names="grupo",values="personas",hole=.6,color="grupo",color_discrete_map=gender_colors)
                    fig.update_traces(textinfo="percent+label")
                    plot(fig,420,f"gender_donut_{suffix}")
                    section_figures.setdefault(bucket, []).append(fig)
                    if disability_total>0:
                        st.caption(f"Adicionalmente, {num(disability_total)} de estas personas (ya incluidas arriba en su grupo de sexo/edad) tienen discapacidad — no se suma aparte para no duplicar el total.")
                with gp_r:
                    # Filtro en vez de selección por clic en la dona (el
                    # clic no disparaba el rerun de forma confiable): elige
                    # qué grupo mostrar en esta misma gráfica de áreas.
                    # "Discapacidad" no es una opción aquí: no es un grupo de
                    # sexo/edad, es una característica aparte (ya aclarada en
                    # el caption de la dona) — mezclarla en este mismo filtro
                    # la haría ver como un grupo más, cuando no lo es.
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
                    else:
                        col={"Mujeres":"mujeres","Hombres":"hombres","Niñas":"ninas","Niños":"ninos"}[selected_group]
                        top_group=people.groupby("area")[col].sum().sort_values(ascending=False).head(6).sort_values().reset_index(name="personas")
                        if top_group.personas.sum()==0:
                            st.info(f"No hay personas reportadas en «{selected_group}» para estos filtros.")
                        else:
                            fig=px.bar(top_group,x="personas",y="area",orientation="h",text="personas",color_discrete_sequence=[gender_colors[selected_group]],labels={"personas":f"Personas ({selected_group.lower()})","area":""}); fig.update_traces(textposition="outside")
                            plot(fig,420,f"gender_detail_{suffix}",margin=dict(l=4,r=24,t=12,b=18))
                            section_figures.setdefault(bucket, []).append(fig)

    st.markdown('<div id="reportes-establecimientos-f02" class="section-rule"></div><div class="section-kicker">04 · Reportes de establecimientos de salud</div><h2>Evolución y alcance de los reportes de establecimientos de salud</h2>',unsafe_allow_html=True)
    _render_focus_reports("Acciones en el establecimiento de salud", "Establecimiento de salud", RED, "est", "Tipos de establecimiento de salud", "Reportes de establecimientos de salud")

    st.markdown('<div id="reportes-acciones-f02" class="section-rule"></div><div class="section-kicker">05 · Reportes de acciones de salud pública</div><h2>Evolución y alcance de los reportes de acciones de salud pública</h2>',unsafe_allow_html=True)
    _render_focus_reports("Acciones de salud pública", "Salud pública", YELLOW, "pub", "Tipos de lugar de intervención", "Reportes de acciones de salud pública", include_population=True)

    st.markdown("### Descargar infografía")
    st.markdown("#### Vista completa")
    complete_dashboard_export()
    st.markdown("#### Resumen ejecutivo")
    report_palette={"navy":NAVY,"muted":MUTED,"ink":INK,"blue":BLUE,"border":tint(GRAY,.75)}
    top_orgs_f02=org_points_by_focus.groupby("organizacion").puntos.sum().nlargest(12).sort_values()
    area_volume=res.groupby("area",as_index=False).total.sum().nlargest(12,"total").sort_values("total")
    top_states_f02=r.groupby("estado").id_reporte.nunique().nlargest(12).sort_values()
    top_munis_f02=r.groupby("municipio").id_reporte.nunique().nlargest(12).sort_values()
    reports_by_focus=focus_summary.set_index("foco_formulario")["reportes"].sort_values()
    totals_by_unit=res.groupby("unidad").total.sum().nlargest(12).sort_values()
    population_totals=res[["mujeres","hombres","ninas","ninos"]].sum().rename({"mujeres":"Mujeres","hombres":"Hombres","ninas":"Niñas","ninos":"Niños"})
    disability_by_area=res.groupby("area").discapacidad.sum().nlargest(12).sort_values()
    report_bytes=build_report(
        title="Tablero de la Respuesta en Salud del terremoto en Venezuela",
        subtitle="Reporte de acciones — Reportes periódicos (F02)",
        scope_text="Universo de reportes vigente según los filtros de estado, municipio, organización, foco y rango de fecha activos.",
        as_of_text=f"Generado el {pd.Timestamp.today().strftime('%d/%m/%Y')}",
        kpis=[(c[0],c[1]) for c in cards],
        sections=[
            {
                "title": "Organizaciones y distribución por foco",
                "rows": [[
                    ("Top organizaciones por puntos con reporte",bar_chart(top_orgs_f02.index.tolist(),top_orgs_f02.tolist(),NAVY,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=190,width=350,label_width=140),12.7),
                    ("Distribución por foco de intervención",composition_bar(focus_points.foco_formulario.tolist(),focus_points.puntos.tolist(),[RED,YELLOW],MUTED,width=350,height=115),12.7),
                ]],
                "caption": "Cuenta puntos distintos (no reportes); una organización con varios puntos aparece una sola vez.",
            },
            {
                "title": "Volumen reportado por área temática",
                "rows": [[
                    ("Top áreas por total reportado",bar_chart(area_volume.area.tolist(),area_volume.total.tolist(),ORANGE,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=190,width=735,label_width=180),26.0),
                ]],
                "caption": "Suma de indicadores por área (personas, casos, procedimientos, kits, profesionales o instituciones); no deben sumarse entre sí como si fueran la misma unidad.",
            },
            {
                "title": "Distribución territorial de los reportes",
                "rows": [[
                    ("Estados con más reportes",bar_chart(top_states_f02.index.tolist(),top_states_f02.tolist(),RED,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=210,width=350,label_width=115),12.7),
                    ("Municipios con más reportes",bar_chart(top_munis_f02.index.tolist(),top_munis_f02.tolist(),ORANGE,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=210,width=350,label_width=125),12.7),
                ]],
                "caption": "Cuenta formularios F02 recibidos dentro del alcance de los filtros activos.",
            },
            {
                "title": "Composición de los resultados reportados",
                "rows": [[
                    ("Reportes periódicos por foco",bar_chart(reports_by_focus.index.tolist(),reports_by_focus.tolist(),RED,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=180,width=350,label_width=145),12.7),
                    ("Resultados por unidad de medida",bar_chart(totals_by_unit.index.tolist(),totals_by_unit.tolist(),BLUE,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=180,width=350,label_width=125),12.7),
                ]],
                "caption": "Las unidades se presentan separadas; no deben sumarse como si representaran una misma medida.",
            },
            {
                "title": "Población reportada",
                "rows": [[
                    ("Composición por sexo y grupo de edad",composition_bar(population_totals.index.tolist(),population_totals.tolist(),[RED,BLUE,tint(RED,.45),SKY],MUTED,width=350,height=115),12.7),
                    ("Personas con discapacidad por acción",bar_chart(disability_by_area.index.tolist(),disability_by_area.tolist(),TEAL,MUTED,tint(GRAY,.75),tint(GRAY,.85),height=210,width=350,label_width=145),12.7),
                ]],
                "caption": "Las cifras son sumas de indicadores y no representan personas únicas. La discapacidad es un subconjunto transversal.",
            },
        ],
        palette=report_palette,
    )
    _, dl_col, _ = st.columns([1.0,1.15,1.0])
    with dl_col:
        st.download_button("Descargar resumen PDF",data=report_bytes,file_name=f"Resumen_F02_{pd.Timestamp.today().strftime('%Y%m%d')}.pdf",mime="application/pdf",width="stretch")
else:
    section_figures: dict[str, list[go.Figure]] = {}
    section_band("CALENDARIO DE BRIGADAS", "Aquí se registran y planifican todas las acciones de salud pública.")
    cal=filt(d["calendar"],False); cal=cal[cal.foco.eq("Salud pública")]
    st.markdown('<h2>Calendario de brigadas de salud pública · {}</h2>'.format(CALENDAR_YEAR),unsafe_allow_html=True)
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
                              fillcolor=tint(GRAY,.75), opacity=0.5, line_width=0, layer="below")
            # Degradado de 4 tonos (en vez de 2 planos) para dar más
            # profundidad visual, y "grout lines" color papel más gruesas
            # entre celdas para que la cuadrícula se vea como una tarjeta,
            # no como bloques pegados.
            fig.add_trace(go.Scatter(
                x=cell_xs, y=cell_ys, mode="markers",
                marker=dict(symbol="square", size=cell_px, color=cell_colors, cmin=0, cmax=cmax,
                            colorscale=[[0, tint(ORANGE, .9)], [0.35, tint(ORANGE, .55)], [0.7, tint(ORANGE, .15)], [1, shade(ORANGE, .15)]], line=dict(width=4, color=PAPER),
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
                                          textfont=dict(size=10, color="white"), customdata=cds,
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

        def _calendar_export_button(scope_df: pd.DataFrame, label: str) -> None:
            export_cols=[c for c in ["fecha","organizacion","estado","municipio","parroquia","lugar","actividad","jornada"] if c in scope_df.columns]
            export_df=scope_df[export_cols].sort_values("fecha").copy()
            export_df["fecha"]=pd.to_datetime(export_df["fecha"]).dt.strftime("%d/%m/%Y")
            export_df=export_df.rename(columns={"fecha":"Fecha","organizacion":"Organización","estado":"Estado","municipio":"Municipio","parroquia":"Parroquia","lugar":"Lugar","actividad":"Actividad","jornada":"Jornada"})
            excel_buffer=io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                export_df.to_excel(writer, index=False, sheet_name="Actividades")
            excel_b64=base64.b64encode(excel_buffer.getvalue()).decode("ascii")
            _, right_col=st.columns([3,2])
            with right_col:
                st.markdown(
                    '<div style="text-align:right">'
                    f'<a class="excel-dl" href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{excel_b64}" download="{label}.xlsx">'
                    '<svg width="15" height="15" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
                    '<rect width="16" height="16" rx="3" fill="#1D6F42"/>'
                    '<path d="M4.5 4.5L11.5 11.5M11.5 4.5L4.5 11.5" stroke="white" stroke-width="1.6" stroke-linecap="round"/>'
                    '</svg>Descargar el calendario</a></div>',
                    unsafe_allow_html=True,
                )

        calendar_view=st.radio("Vista del calendario",["Anual","Mensual","Semanal","Diario"],horizontal=True,key="calendar_view_mode",index=1)
        selected_date=None
        selection_points=[]

        if calendar_view=="Anual":
            _calendar_export_button(cal_f, f"calendario_brigadas_{CALENDAR_YEAR}_anual")
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
            _calendar_export_button(month_rows, f"calendario_brigadas_{picked_month.strftime('%Y_%m')}")
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
            _calendar_export_button(week_rows, f"calendario_brigadas_semana_{picked_week.start_time.strftime('%Y%m%d')}")
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
            _calendar_export_button(cal_f[cal_f.fecha.eq(selected_date)], f"calendario_brigadas_{selected_date.strftime('%Y_%m_%d')}")

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

        st.markdown("### Descargar calendario como infografía")
        complete_dashboard_export()

