"""Adaptador del formulario NUEVO (F01/F02) al contrato que consume el tablero.

El tablero nació como simulacro: toma los datos históricos (formularios viejos
de cobertura, instituciones y reporte periódico) y les da la *estructura* de
F01/F02. Este módulo hace lo contrario y lo definitivo: lee los datos REALES
del F01/F02 desde Postgres y los entrega con la misma forma que espera
`prepare()`, para no tener que reescribir el tablero.

Fuentes (ver db/schema/012_etiquetas_y_vistas.sql del repositorio del pipeline):

    app.v_f01_intervenciones    una fila por intervención registrada
    app.v_f02_reportes          una fila por reporte periódico
    app.v_f02_resultados        una fila por indicador reportado
    app.catalog_indicadores_f02 catálogo de los 60 indicadores

Dos diferencias de fondo con el histórico, que este módulo resuelve:

1. Los multi-select vienen como ARRAY de Postgres en la misma fila, no como
   tablas hijas. Acá se "explotan" a las tablas que el tablero espera
   (áreas por establecimiento, apoyos, acciones ofertadas).

2. El formulario nuevo SÍ captura campos que el histórico no tenía y que
   `prepare()` simula por hash (`tipo_organizacion`, `tipo_punto`) o infiere
   del nombre del sitio (`foco`). Acá se entregan los valores reales y se
   marca el DataFrame con el atributo `fuente_real` para que `prepare()`
   sepa que no debe simular encima. Simular sobre un dato real sería
   reemplazar lo que la organización efectivamente declaró.
"""
from __future__ import annotations

import decimal

import pandas as pd

# El catálogo nuevo no trae `unidad` y el tablero la usa para separar
# personas de casos, procedimientos, etc. (sumarlas entre sí no tendría
# sentido). El enunciado de cada indicador sí la dice: "Número de personas
# alcanzadas...", "Número de casos derivados". Se deduce de ahí, con el mismo
# vocabulario de cinco unidades que traía el catálogo viejo.
#
# Es una deducción, no un dato declarado: lo correcto a futuro es que
# `catalog_indicadores_f02` tenga su propia columna `unidad`.
_UNIDAD_POR_PALABRA = (
    ("persona", "Personas"),
    ("caso", "Casos"),
    ("procedimiento", "Procedimientos"),
    ("profesional", "Profesionales"),
    ("institucion", "Instituciones"),
    ("institución", "Instituciones"),
)


def unidad_de_indicador(etiqueta: object) -> str:
    texto = str(etiqueta or "").lower()
    for palabra, unidad in _UNIDAD_POR_PALABRA:
        if palabra in texto:
            return unidad
    return "Personas"


# El F01/F02 nuevo nombra el foco con etiquetas largas ("Acciones en el
# establecimiento de salud") y el tablero usa las cortas ("Establecimiento de
# salud"), que son las de su filtro lateral y sus paletas de color. Se traduce
# desde el CÓDIGO del formulario, no desde la etiqueta: el código es estable y
# la etiqueta puede reescribirse sin avisar.
_FOCO_POR_CODIGO = {
    "facility_actions": "Establecimiento de salud",
    "public_health_actions": "Salud pública",
}


def foco_corto(codigo: object, etiqueta: object = "") -> str:
    valor = _FOCO_POR_CODIGO.get(str(codigo or "").strip())
    if valor:
        return valor
    texto = str(etiqueta or "").lower()
    if "establecimiento" in texto:
        return "Establecimiento de salud"
    if "pública" in texto or "publica" in texto:
        return "Salud pública"
    return "No indicado"


def _numerico(serie: pd.Series) -> pd.Series:
    """Postgres devuelve `numeric` como Decimal; el tablero espera float."""
    if serie.dtype == object:
        muestra = serie.dropna()
        if not muestra.empty and isinstance(muestra.iloc[0], decimal.Decimal):
            return pd.to_numeric(serie, errors="coerce")
    return serie


def _explotar(frame: pd.DataFrame, id_col: str, array_col: str, destino: str) -> pd.DataFrame:
    """Convierte una columna ARRAY en una tabla hija (una fila por elemento)."""
    if array_col not in frame.columns:
        return pd.DataFrame(columns=[id_col, destino])
    sub = frame[[id_col, array_col]].copy()
    sub[array_col] = sub[array_col].map(lambda v: list(v) if isinstance(v, (list, tuple)) else [])
    sub = sub.explode(array_col).dropna(subset=[array_col])
    sub = sub[sub[array_col].astype(str).str.strip().ne("")]
    return sub.rename(columns={array_col: destino}).reset_index(drop=True)


def _texto(serie: pd.Series, relleno: str = "No indicado") -> pd.Series:
    return serie.fillna(relleno).astype(str).replace("", relleno)


def leer(dsn: str) -> dict[str, pd.DataFrame]:
    """Devuelve las 7 tablas del contrato del tablero, desde el F01/F02 real."""
    import psycopg

    crudo: dict[str, pd.DataFrame] = {}
    with psycopg.connect(dsn, prepare_threshold=None) as conn, conn.cursor() as cur:
        for clave, sql in {
            "f01": "select * from app.v_f01_intervenciones order by submission_time",
            "f02": "select * from app.v_f02_reportes order by submission_time",
            "res": "select * from app.v_f02_resultados",
        }.items():
            cur.execute(sql)
            crudo[clave] = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])

    f01, f02, res = crudo["f01"], crudo["f02"], crudo["res"]
    for frame in (f01, f02, res):
        for col in frame.columns:
            frame[col] = _numerico(frame[col])
        # Foco en el vocabulario corto del tablero, desde el código del formulario.
        if "intervention_focus" in frame.columns:
            frame["foco"] = [foco_corto(c, e) for c, e in zip(frame["intervention_focus"], frame.get("foco", ""))]

    return {
        "facilities": _facilities(f01),
        "facility_areas": _explotar(
            f01[f01.foco.eq("Establecimiento de salud")], "intervention_id",
            "servicios_establecimiento", "area_servicio",
        ).rename(columns={"intervention_id": "id_establecimiento"}),
        "supports": _explotar(
            f01[f01.foco.eq("Establecimiento de salud")], "intervention_id",
            "tipos_apoyo", "tipo_apoyo",
        ).rename(columns={"intervention_id": "id_establecimiento"}),
        "places": _places(f01),
        "offered": _explotar(f01, "intervention_id", "areas_intervencion", "servicio")
        .rename(columns={"intervention_id": "id_servicio"}),
        "reports": _reports(f02),
        "results": _results(res),
    }


def _facilities(f01: pd.DataFrame) -> pd.DataFrame:
    """Establecimientos de salud apoyados (bloque de foco 'establecimiento')."""
    est = f01[f01.foco.eq("Establecimiento de salud")].copy()
    salida = pd.DataFrame({
        "id_establecimiento": est.intervention_id,
        "fecha_reporte": pd.to_datetime(est.submission_time, errors="coerce").dt.tz_localize(None),
        "organizacion": _texto(est.organization_label),
        "estado": _texto(est.estado_etiqueta),
        "municipio": _texto(est.municipio_etiqueta),
        "latitud": pd.to_numeric(est.latitud, errors="coerce"),
        "longitud": pd.to_numeric(est.longitud, errors="coerce"),
        "nombre_establecimiento": _texto(est.target_label, "Sin nombre"),
        "tipo_establecimiento": _texto(est.facility_type_etiqueta, "No indicado"),
    }).reset_index(drop=True)
    # El formulario nuevo declara el tipo: no hay que clasificarlo por el nombre.
    salida.attrs["fuente_real"] = True
    return salida


def _places(f01: pd.DataFrame) -> pd.DataFrame:
    """Puntos de intervención, con personal, disponibilidad y modalidades."""
    # El F01 nuevo trae 14 perfiles; el tablero nombra sus columnas
    # `personal_*`. Solo se mapean los que existen en ambos lados.
    perfiles = {
        "staff_general_doctor": "personal_medico",
        "staff_pediatrician": "personal_pediatria",
        "staff_obgyn": "personal_ginecologia",
        "staff_psychiatrist": "personal_psiquiatra",
        "staff_nurse": "personal_enfermero",
        "staff_nurse_assistant": "personal_auxiliar_enfermeria",
        "staff_psychologist": "personal_psicologo",
        "staff_nutritionist": "personal_nutricionista_dietista",
        "staff_dentist": "personal_odontologos",
        "staff_rehabilitation": "personal_fisioterapeutas",
        "staff_pharmacy": "personal_regente_farmacia",
        "staff_social_worker": "personal_trabajador_social",
        "staff_admin_logistics": "personal_administrativos",
        "staff_other": "personal_otros",
    }
    salida = pd.DataFrame({
        "id_servicio": f01.intervention_id,
        "organizacion": _texto(f01.organization_label),
        "estado": _texto(f01.estado_etiqueta),
        "municipio": _texto(f01.municipio_etiqueta),
        "parroquia": _texto(f01.parroquia_etiqueta, "No reportada"),
        "nombre_sitio": _texto(f01.target_label, "Sin nombre"),
        "tipo_lugar": _texto(f01.intervention_place_type_etiqueta, "No indicado"),
        "desde": pd.to_datetime(f01.intervention_start, errors="coerce"),
        "hasta": pd.to_datetime(f01.intervention_end, errors="coerce"),
        # Datos DECLARADOS en el formulario nuevo, no simulados por hash.
        "tipo_organizacion": _texto(f01.org_type_etiqueta),
        "modalidad_implementacion": f01.modalidad_implementacion.map(_unir),
        "donantes": f01.donantes.map(_unir),
        "dias_horas_aplica": f01.disponibilidad_aplica.map(
            lambda v: "Aplica" if str(v).strip().lower() in {"si", "sí", "yes", "true", "1", "aplica"} else "No aplica"
        ),
    }).reset_index(drop=True)
    for origen, destino in perfiles.items():
        salida[destino] = pd.to_numeric(f01[origen], errors="coerce").fillna(0).astype(int) if origen in f01 else 0
    salida.attrs["fuente_real"] = True
    return salida


def _unir(valor: object) -> str:
    if isinstance(valor, (list, tuple)):
        limpio = [str(v).strip() for v in valor if str(v).strip()]
        return ", ".join(limpio) if limpio else "No indicado"
    return str(valor).strip() or "No indicado"


def _reports(f02: pd.DataFrame) -> pd.DataFrame:
    salida = pd.DataFrame({
        "id_reporte": f02.report_uuid,
        "fecha_reporte": pd.to_datetime(f02.period_end.fillna(f02.submission_time), errors="coerce"),
        "organizacion": _texto(f02.organization_label),
        "estado": _texto(f02.estado_etiqueta),
        "municipio": _texto(f02.municipio_etiqueta),
        "parroquia": _texto(f02.parroquia_etiqueta, "No reportada"),
        "nombre_sitio": _texto(f02.target_label, "Sin nombre"),
        "latitud": pd.to_numeric(f02.latitud, errors="coerce"),
        "longitud": pd.to_numeric(f02.longitud, errors="coerce"),
        # El foco viene DECLARADO; no hay que adivinarlo por el nombre del sitio.
        "foco": _texto(f02.foco),
        "tipo_punto": _texto(f02.tipo_lugar_etiqueta, "No indicado"),
    }).reset_index(drop=True)
    salida.attrs["fuente_real"] = True
    return salida


def _results(res: pd.DataFrame) -> pd.DataFrame:
    numericas = ["total", "mujeres", "hombres", "ninas", "ninos", "discapacidad"]
    salida = pd.DataFrame({
        "id_reporte": res.report_uuid,
        "fecha_reporte": pd.to_datetime(res.period_end, errors="coerce"),
        "indicador_interno": _texto(res.numero, ""),
        "indicador_codigo": _texto(res.indicador_codigo, ""),
        "indicador": _texto(res.indicador, ""),
        "unidad": res.indicador.map(unidad_de_indicador),
        # El tablero agrupa los resultados por "área temática": en el F02
        # nuevo eso es la sección del formulario (5.1 SMAPS, 5.2 ENT, ...).
        "area": _texto(res.seccion_etiqueta, "Sin sección"),
        "foco": _texto(res.foco),
    }).reset_index(drop=True)
    for col in numericas:
        salida[col] = pd.to_numeric(res[col], errors="coerce").fillna(0).values
    # El histórico traía esta bandera; acá se deduce de si hay desagregación.
    salida["tiene_desagregacion"] = (
        salida[["mujeres", "hombres", "ninas", "ninos"]].sum(axis=1) > 0
    ).astype(int)
    salida.attrs["fuente_real"] = True
    return salida
