"""Simulacro del tablero del Clúster Salud basado en F01 y F02 propuestos.

Los CSV incluidos proceden de los tres formularios antiguos. No se presentan
como envíos de los nuevos formularios ni se enlazan artificialmente por sitio.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
INK = "#20201E"
RED = "#C43A32"
YELLOW = "#EABF43"
BLUE = "#496778"
TEAL = "#58746A"
SAND = "#CDA768"
LIGHT = "#F6F3EB"

st.set_page_config(
    page_title="Clúster Salud Venezuela | Panorama",
    page_icon="✚",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    f"""
    <style>
      .stApp {{ background:{LIGHT}; color:{INK}; }}
      .block-container {{ max-width:1380px; padding-top:2.1rem; padding-bottom:3rem; }}
      [data-testid="stSidebar"] {{ background:#EFEADF; border-right:1px solid #CFC7B7; }}
      [data-testid="stSidebar"] h3 {{ font-family:Georgia,serif; color:{INK}; }}
      .editorial-topline {{ border-top:5px solid {RED}; padding-top:.7rem;
        font:700 .7rem Arial,sans-serif; letter-spacing:.17em; text-transform:uppercase; color:{RED}; }}
      .editorial-title {{ font:700 clamp(2.1rem,3.5vw,3.45rem)/1.08 Georgia,serif;
        letter-spacing:-.035em; color:{INK}; max-width:980px; margin:.48rem 0 .45rem; }}
      .editorial-deck {{ font:1rem/1.4 Georgia,serif; max-width:900px; color:#4A4843;
        margin:0 0 .55rem; }}
      .editorial-meta {{ border-top:1px solid #BFB7A9; border-bottom:1px solid #BFB7A9;
        padding:.45rem 0; font:.72rem Arial,sans-serif; letter-spacing:.07em;
        text-transform:uppercase; color:#665F54; margin-bottom:.5rem; }}
      .editorial-note {{ border-left:3px solid {RED}; padding:.45rem .7rem;
        background:#F0E9DD; font:.8rem/1.35 Arial,sans-serif; color:#554D42; margin:.45rem 0 .7rem; }}
      .block-container h2, .block-container h3, .block-container h4 {{
        font-family:Georgia,serif; color:{INK}; letter-spacing:-.02em; }}
      .block-container h2 {{ border-top:1px solid {INK}; padding-top:.42rem; }}
      [data-testid="stMetric"] {{ background:transparent; border-top:2px solid {INK};
        border-bottom:1px solid #BFB7A9; border-radius:0; padding:.72rem .12rem .8rem; }}
      [data-testid="stMetricLabel"] {{ color:#5E584F; font:700 .75rem Arial,sans-serif; text-transform:uppercase; letter-spacing:.04em; }}
      [data-testid="stMetricValue"] {{ color:{INK}; font-family:Georgia,serif; }}
      [data-testid="stPlotlyChart"] {{ background:transparent; border:0; border-radius:0; padding:0; }}
      .stTabs [data-baseweb="tab-list"] {{ background:transparent; border-bottom:1px solid {INK}; gap:1rem; }}
      .stTabs [data-baseweb="tab"] {{ border-radius:0; padding:.65rem .1rem; color:#615B52; }}
      .stTabs [aria-selected="true"] {{ background:transparent; border-bottom:3px solid {RED}; color:{INK} !important; }}
      .stTabs [aria-selected="true"] p {{ color:{INK} !important; font-weight:700; }}
      .stDataFrame {{ border-top:1px solid {INK}; }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data
def load_data() -> dict[str, pd.DataFrame]:
    files = {
        "facility": DATA / "establecimientos" / "establecimientos.csv",
        "support": DATA / "establecimientos" / "apoyos.csv",
        "facility_areas": DATA / "establecimientos" / "areas.csv",
        "places": DATA / "cobertura" / "puntos_atencion.csv",
        "offered": DATA / "cobertura" / "acciones_ofertadas.csv",
        "reports": DATA / "acciones" / "reportes.csv",
        "results": DATA / "acciones" / "resultados.csv",
    }
    tables = {key: pd.read_csv(path, encoding="utf-8-sig") for key, path in files.items()}
    for key in ("facility", "reports", "results"):
        if "fecha_reporte" in tables[key]:
            tables[key]["fecha_reporte"] = pd.to_datetime(tables[key]["fecha_reporte"], errors="coerce")
    for key in ("facility", "places", "reports"):
        tables[key]["organizacion"] = tables[key]["organizacion"].fillna("No indicada").astype(str)
        tables[key]["estado"] = tables[key]["estado"].fillna("No indicado").astype(str)
        tables[key]["municipio"] = tables[key]["municipio"].fillna("No indicado").astype(str)
    return tables


def format_number(value: int | float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def chart(fig, height: int = 370) -> None:
    fig.update_layout(
        height=height,
        margin=dict(l=20, r=15, t=45, b=20),
        paper_bgcolor=LIGHT,
        plot_bgcolor=LIGHT,
        font=dict(family="Arial", color=INK, size=12),
        title_font=dict(family="Georgia", color=INK, size=18),
        legend_title_text="",
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#D9D1C4", zeroline=False)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def section(title: str, detail: str) -> None:
    st.subheader(title)
    st.caption(detail)


tables = load_data()
facility, places, reports, results = (
    tables["facility"], tables["places"], tables["reports"], tables["results"]
)

st.sidebar.markdown("### Explorar la respuesta")
all_states = sorted(set(facility.estado) | set(places.estado) | set(reports.estado))
state = st.sidebar.selectbox("Estado", ["Todos"] + all_states)
all_orgs = sorted(set(facility.organizacion) | set(places.organizacion) | set(reports.organizacion))
organization = st.sidebar.selectbox("Organización", ["Todas"] + all_orgs)
st.sidebar.divider()
st.sidebar.caption("Los filtros de estado y organización se aplican a los tres conjuntos históricos. El período se aplica únicamente a los reportes de acciones.")


def apply_common(df: pd.DataFrame) -> pd.DataFrame:
    if state != "Todos":
        df = df[df.estado == state]
    if organization != "Todas":
        df = df[df.organizacion == organization]
    return df.copy()


f = apply_common(facility)
p = apply_common(places)
r = apply_common(reports)
ids = set(r.id_reporte.dropna())
res = results[results.id_reporte.isin(ids)].copy()

st.markdown(
    '<div class="editorial-topline">Clúster Salud Venezuela &nbsp;/&nbsp; Panorama de la respuesta</div>'
    '<h1 class="editorial-title">La respuesta sanitaria, de un vistazo</h1>'
    '<p class="editorial-deck">Quién interviene, dónde trabaja y cómo evoluciona el reporte.</p>'
    '<div class="editorial-meta">Edición de trabajo &nbsp;·&nbsp; F01 presencia y oferta &nbsp;·&nbsp; F02 resultados &nbsp;·&nbsp; Datos históricos</div>',
    unsafe_allow_html=True,
)
st.caption("Simulacro con datos históricos de los tres formularios anteriores; no representa envíos de los nuevos F01/F02.")

tabs = st.tabs(["Panorama", "Presencia y oferta · F01", "Resultados · F02", "Calidad y alcance"])

with tabs[0]:
    st.subheader("Panorama")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Organizaciones", len(set(f.organizacion) | set(p.organizacion) | set(r.organizacion)))
    m2.metric("Apoyos registrados", len(f))
    m3.metric("Lugares de servicio", len(p))
    m4.metric("Reportes periódicos", r.id_reporte.nunique())

    a_points = f[["organizacion", "estado", "municipio", "nombre_establecimiento", "latitud", "longitud"]].rename(columns={"nombre_establecimiento": "lugar"}).copy()
    a_points["foco"] = "Establecimiento de salud"
    b_points = r[["organizacion", "estado", "municipio", "nombre_sitio", "latitud", "longitud"]].rename(columns={"nombre_sitio": "lugar"}).copy()
    b_points["foco"] = "Sitio de reporte de acciones"
    mapped = pd.concat([a_points, b_points], ignore_index=True)
    mapped["latitud"] = pd.to_numeric(mapped.latitud, errors="coerce")
    mapped["longitud"] = pd.to_numeric(mapped.longitud, errors="coerce")
    mapped = mapped.dropna(subset=["latitud", "longitud"])
    left, right = st.columns([1.3, 0.9])
    with left:
        if mapped.empty:
            st.info("No hay coordenadas disponibles para estos filtros.")
        else:
            fig = px.scatter_map(
                mapped, lat="latitud", lon="longitud", color="foco", hover_name="lugar",
                hover_data={"organizacion": True, "estado": True, "municipio": True, "latitud": False, "longitud": False},
                color_discrete_map={"Establecimiento de salud": RED, "Sitio de reporte de acciones": YELLOW},
                zoom=5.5, center={"lat": 8.6, "lon": -66}, map_style="carto-positron",
                title="Dónde se registra actividad",
            )
            outlines = [
                go.Scattermap(
                    lat=trace.lat, lon=trace.lon, mode="markers",
                    marker={"color": "#151515", "size": 14},
                    hoverinfo="skip", showlegend=False,
                )
                for trace in fig.data
            ]
            fig.update_traces(marker_size=9)
            fig = go.Figure(data=outlines + list(fig.data), layout=fig.layout)
            chart(fig, 445)
    with right:
        presence = pd.concat([f[["estado", "organizacion"]], p[["estado", "organizacion"]]], ignore_index=True)
        by_state = presence.groupby("estado").organizacion.nunique().sort_values().reset_index(name="organizaciones")
        if by_state.empty:
            st.info("Sin registros para los filtros seleccionados.")
        else:
            fig = px.bar(by_state, x="organizaciones", y="estado", orientation="h", text="organizaciones", color_discrete_sequence=[BLUE], title="Presencia por estado")
            fig.update_traces(textposition="outside", cliponaxis=False)
            fig.update_layout(xaxis_title=None, yaxis_title=None, showlegend=False)
            fig.update_xaxes(range=[0, by_state.organizaciones.max() * 1.18])
            chart(fig, 445)
    st.caption("Mapa: rojo = establecimientos (F01 anterior); amarillo = sitios de reportes (F03 anterior). No se infieren coordenadas faltantes.")

    st.markdown("#### Organizaciones con más registros")
    leader_left, leader_right = st.columns(2)
    with leader_left:
        by_facility_org = (
            f.groupby("organizacion").id_establecimiento.nunique()
            .sort_values(ascending=False).head(5).sort_values()
            .reset_index(name="registros")
        )
        if by_facility_org.empty:
            st.info("No hay establecimientos registrados para estos filtros.")
        else:
            by_facility_org["organizacion"] = by_facility_org["organizacion"].replace({
                "OIM - Organización Internacional para las Migraciones": "OIM",
            })
            fig = px.bar(
                by_facility_org, x="registros", y="organizacion", orientation="h",
                text="registros", color_discrete_sequence=[RED],
                title="Establecimientos de salud registrados",
            )
            fig.update_traces(textposition="outside", cliponaxis=False, marker_line_color="#151515", marker_line_width=1)
            fig.update_layout(xaxis_title=None, yaxis_title=None)
            fig.update_xaxes(range=[0, by_facility_org.registros.max() * 1.16])
            fig.update_yaxes(automargin=True)
            chart(fig, 340)
    with leader_right:
        by_report_org = (
            r.groupby("organizacion").id_reporte.nunique()
            .sort_values(ascending=False).head(5).sort_values()
            .reset_index(name="reportes")
        )
        if by_report_org.empty:
            st.info("No hay reportes de acciones para estos filtros.")
        else:
            fig = px.bar(
                by_report_org, x="reportes", y="organizacion", orientation="h",
                text="reportes", color_discrete_sequence=[YELLOW],
                title="Reportes periódicos de acciones",
            )
            fig.update_traces(textposition="outside", cliponaxis=False, marker_line_color="#151515", marker_line_width=1)
            fig.update_layout(xaxis_title=None, yaxis_title=None)
            fig.update_xaxes(range=[0, by_report_org.reportes.max() * 1.16])
            fig.update_yaxes(automargin=True)
            chart(fig, 340)
    monthly = r.dropna(subset=["fecha_reporte"]).copy()
    if not monthly.empty:
        monthly["mes"] = monthly.fecha_reporte.dt.to_period("M").dt.to_timestamp()
        series = monthly.groupby("mes").id_reporte.nunique().reset_index(name="reportes")
        fig = px.bar(series, x="mes", y="reportes", text="reportes", color_discrete_sequence=[INK], title="Evolución de los reportes periódicos")
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(xaxis_title=None, yaxis_title=None)
        fig.update_xaxes(dtick="M1", tickformat="%b %Y")
        chart(fig, 290)
    st.caption("Los rankings cuentan registros, no intervenciones únicas ni personas atendidas. Los nombres históricos aún requieren armonización.")

with tabs[1]:
    section("Presencia y oferta · F01", "El nuevo F01 distinguirá dos focos. Aquí se muestran sus antecedentes históricos por separado.")
    focus = st.radio("Foco de intervención", ["Establecimiento de salud", "Acciones de salud pública"], horizontal=True)
    if focus == "Establecimiento de salud":
        c1, c2, c3 = st.columns(3)
        c1.metric("Registros históricos", len(f))
        c2.metric("Organizaciones", f.organizacion.nunique())
        c3.metric("Establecimientos nombrados", f.nombre_establecimiento.nunique())
        selected = tables["support"][tables["support"].id_establecimiento.isin(f.id_establecimiento)]
        counts = selected.groupby("tipo_apoyo").id_establecimiento.nunique().sort_values().reset_index(name="registros")
        if not counts.empty:
            fig = px.bar(counts, x="registros", y="tipo_apoyo", orientation="h", color_discrete_sequence=[RED], title="Tipos de apoyo registrados")
            chart(fig)
        view = f[["organizacion", "nombre_establecimiento", "tipo_establecimiento", "estado", "municipio", "fecha_reporte"]].rename(columns={
            "organizacion": "Organización", "nombre_establecimiento": "Establecimiento", "tipo_establecimiento": "Tipo",
            "estado": "Estado", "municipio": "Municipio", "fecha_reporte": "Fecha de registro",
        })
        with st.expander("Ver registros históricos de establecimientos"):
            st.dataframe(view, hide_index=True, width="stretch")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Puntos históricos", len(p))
        c2.metric("Organizaciones", p.organizacion.nunique())
        c3.metric("Municipios", p.municipio.nunique())
        offered = tables["offered"][tables["offered"].id_servicio.isin(p.id_servicio)]
        counts = offered.groupby("servicio").id_servicio.nunique().sort_values().tail(14).reset_index(name="puntos")
        if not counts.empty:
            fig = px.bar(counts, x="puntos", y="servicio", orientation="h", text="puntos", color_discrete_sequence=[YELLOW], title="Servicios o acciones ofertadas · clasificación original")
            fig.update_traces(textposition="outside", cliponaxis=False, marker_line_color="#151515", marker_line_width=1)
            fig.update_layout(xaxis_title="Puntos registrados", yaxis_title=None)
            fig.update_xaxes(range=[0, counts.puntos.max() * 1.14])
            chart(fig, 440)
        view = p[["organizacion", "nombre_sitio", "tipo_lugar", "estado", "municipio", "desde", "hasta"]].rename(columns={
            "organizacion": "Organización", "nombre_sitio": "Lugar o sitio", "tipo_lugar": "Tipo de lugar",
            "estado": "Estado", "municipio": "Municipio", "desde": "Desde", "hasta": "Hasta",
        })
        with st.expander("Ver registros históricos de lugares"):
            st.dataframe(view, hide_index=True, width="stretch")
    st.caption("La nomenclatura antigua se conserva en estas vistas; la producción deberá aplicar los catálogos y equivalencias del nuevo F01.")

with tabs[2]:
    section("Resultados · F02", "Seleccione un indicador específico. No se suman indicadores diferentes como si fueran personas únicas.")
    available_dates = r.fecha_reporte.dropna()
    if not available_dates.empty:
        date_selection = st.date_input("Fecha del reporte histórico", value=(available_dates.min().date(), available_dates.max().date()))
        if isinstance(date_selection, tuple) and len(date_selection) == 2:
            first, last = date_selection
            r = r[r.fecha_reporte.between(pd.Timestamp(first), pd.Timestamp(last))]
            res = res[res.id_reporte.isin(r.id_reporte)]
    if res.empty:
        st.info("No hay resultados para la selección actual.")
    else:
        indicators = res[["indicador_interno", "indicador_codigo", "indicador", "unidad"]].drop_duplicates("indicador_interno")
        indicators["display"] = indicators.indicador_codigo.astype(str) + " · " + indicators.indicador.astype(str)
        selected_display = st.selectbox("Indicador", indicators.display.tolist())
        selected_id = indicators.loc[indicators.display == selected_display, "indicador_interno"].iloc[0]
        metric = res[res.indicador_interno == selected_id].copy()
        unit = str(metric.unidad.dropna().iloc[0]) if metric.unidad.notna().any() else "Valor"
        metric = metric.merge(r[["id_reporte", "organizacion", "estado", "fecha_reporte"]], on="id_reporte", how="left", suffixes=("", "_reporte"))
        for column in ("total", "mujeres", "hombres", "ninas", "ninos", "discapacidad"):
            metric[column] = pd.to_numeric(metric[column], errors="coerce").fillna(0)
        a, b, c = st.columns(3)
        a.metric(f"Valor reportado · {unit.lower()}", format_number(metric.total.sum()))
        b.metric("Reportes con este indicador", metric.id_reporte.nunique())
        c.metric("Organizaciones que reportan", metric.organizacion.nunique())
        left, right = st.columns([1.1, .9])
        with left:
            trend = metric.groupby("fecha_reporte", as_index=False).total.sum()
            fig = px.line(trend, x="fecha_reporte", y="total", markers=True, color_discrete_sequence=[RED], title="Evolución del indicador seleccionado")
            fig.update_layout(xaxis_title="Fecha de reporte", yaxis_title=unit)
            chart(fig)
        with right:
            if metric.tiene_desagregacion.fillna(0).astype(int).eq(1).any():
                demo = pd.DataFrame({"Grupo": ["Mujeres", "Hombres", "Niñas", "Niños"],
                                     "Valor": [metric.mujeres.sum(), metric.hombres.sum(), metric.ninas.sum(), metric.ninos.sum()]})
                fig = px.bar(
                    demo, x="Valor", y=["Personas reportadas"] * len(demo), color="Grupo",
                    orientation="h", text="Valor", barmode="stack",
                    color_discrete_map={"Mujeres": RED, "Hombres": BLUE, "Niñas": YELLOW, "Niños": TEAL},
                    title="Distribución por sexo y edad",
                )
                fig.update_traces(texttemplate="%{text:,.0f}", textposition="inside")
                fig.update_layout(xaxis_title=unit, yaxis_title=None, legend=dict(orientation="h", y=-0.3, x=0))
                chart(fig, 310)
                st.caption(f"Discapacidad: {format_number(metric.discapacidad.sum())}. Es un subconjunto transversal; no se agrega al total.")
            else:
                st.info("Este indicador no tiene desagregación demográfica en el registro histórico.")
        by_org = metric.groupby("organizacion", as_index=False).total.sum().sort_values("total", ascending=False)
        by_org.columns = ["Organización", f"Valor reportado ({unit})"]
        with st.expander("Ver desglose por organización"):
            st.dataframe(by_org, hide_index=True, width="stretch")
        st.caption("La suma corresponde a valores informados en distintos reportes; no representa personas únicas entre períodos.")

with tabs[3]:
    section("Calidad y alcance", "Qué podemos mostrar hoy y qué necesita la conexión definitiva F01–F02.")
    checks = pd.DataFrame([
        {"Control": "F01 · establecimientos sin coordenadas", "Resultado": int(f[["latitud", "longitud"]].isna().any(axis=1).sum()), "Lectura": "Registros históricos a revisar"},
        {"Control": "Reportes de acciones sin coordenadas", "Resultado": int(r[["latitud", "longitud"]].isna().any(axis=1).sum()), "Lectura": "Registros históricos a revisar"},
        {"Control": "F02 · reportes sin resultados tabulados", "Resultado": int(len(set(r.id_reporte) - set(res.id_reporte))), "Lectura": "Puede incluir reportes sin indicadores numéricos"},
        {"Control": "F02 · discrepancias total/desagregación", "Resultado": int(((pd.to_numeric(res.total, errors="coerce").fillna(0) - res[["mujeres", "hombres", "ninas", "ninos"]].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)).abs().gt(0) & res.tiene_desagregacion.fillna(0).astype(int).eq(1)).sum()), "Lectura": "Discapacidad no se suma al total"},
    ])
    st.dataframe(checks, hide_index=True, width="stretch")
    st.markdown("#### Para la versión conectada")
    st.markdown(
        """
        1. Leer cada intervención repetida de F01 como registro individual, asociado a una organización.
        2. Sincronizar el catálogo de organizaciones y lugares que F02 usa para identificarse.
        3. Mantener una clave estable de intervención para relacionar períodos y resultados.
        4. Aplicar las equivalencias entre categorías antiguas y los 15 bloques nuevos antes de comparar series.
        5. Mostrar recuentos de personas únicas solo si existe una regla de deduplicación verificable.
        """
    )
    st.markdown("#### Fuentes del simulacro")
    st.caption("CSV sanitizados publicados con el tablero anterior: apoyo a establecimientos (F01 antiguo), mapeo de servicios (F02 antiguo) y reporte periódico de acciones (F03 antiguo). No se usan nombres ni teléfonos de puntos focales, fotografías ni exportaciones crudas de Kobo.")
