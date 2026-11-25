"""Dashboard de los marts del warehouse.

Ejecutar con:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
from typing import Any

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://etl:etl@localhost:5432/etl")

st.set_page_config(page_title="Datos de Chile", layout="wide")


@st.cache_data(ttl=600)
def query(sql: str) -> pd.DataFrame:
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(sql)
        columns = [c.name for c in cur.description or []]
        rows: list[Any] = cur.fetchall()
    return pd.DataFrame(rows, columns=columns)


st.title("Sismos e indicadores económicos de Chile")
st.caption("Datos de USGS y mindicador.cl, cargados por el pipeline etl-datos-chile.")

estado = query(
    """SELECT fuente, max(fin) AS ultima_carga FROM etl.ejecuciones
       WHERE estado = 'ok' GROUP BY fuente"""
)
totales = query(
    """SELECT count(*) AS sismos,
              count(*) FILTER (WHERE magnitud >= 5) AS sismos_m5,
              max(magnitud) AS magnitud_maxima
       FROM warehouse.fact_sismo"""
).iloc[0]
dolar = query(
    "SELECT fecha, dolar FROM mart.indicadores_diarios WHERE dolar IS NOT NULL ORDER BY fecha"
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Sismos registrados", f"{int(totales.sismos):,}".replace(",", "."))
c2.metric("De magnitud 5 o más", int(totales.sismos_m5))
c3.metric("Magnitud máxima", f"{totales.magnitud_maxima or 0:.1f}")
if not dolar.empty:
    last = dolar.iloc[-1]
    c4.metric(
        "Dólar observado",
        f"${float(last.dolar):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
    )

st.subheader("Sismos por mes")
mensual = query(
    """SELECT to_char(mes_inicio, 'YYYY-MM') AS mes, cantidad, cantidad_m5_o_mas
       FROM mart.sismos_mensuales ORDER BY mes_inicio"""
)
if not mensual.empty:
    st.altair_chart(
        alt.Chart(mensual)
        .mark_bar()
        .encode(
            x=alt.X("mes:O", title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("cantidad:Q", title="Sismos de magnitud 2,5 o más"),
            tooltip=[
                alt.Tooltip("mes:O", title="Mes"),
                alt.Tooltip("cantidad:Q", title="Sismos"),
                alt.Tooltip("cantidad_m5_o_mas:Q", title="Magnitud 5 o más"),
            ],
        ),
        width="stretch",
    )

left, right = st.columns([3, 2])
with left:
    st.subheader("Dónde ocurrieron")
    puntos = query(
        """SELECT latitud::float AS lat, longitud::float AS lon, magnitud::float AS magnitud
           FROM warehouse.fact_sismo"""
    )
    if not puntos.empty:
        st.map(puntos, latitude="lat", longitude="lon", size="magnitud", zoom=3)
with right:
    st.subheader("Ciudades de referencia con más sismos")
    ciudades = query(
        """SELECT ciudad_referencia AS ciudad, pais, cantidad, magnitud_maxima
           FROM mart.sismos_por_ciudad ORDER BY cantidad DESC LIMIT 12"""
    )
    st.dataframe(ciudades, hide_index=True, width="stretch")

if not dolar.empty:
    st.subheader("Dólar observado")
    st.altair_chart(
        alt.Chart(dolar)
        .mark_line()
        .encode(
            x=alt.X("fecha:T", title=None),
            y=alt.Y("dolar:Q", title="CLP", scale=alt.Scale(zero=False)),
        ),
        width="stretch",
    )

with st.expander("Estado del pipeline"):
    st.dataframe(estado, hide_index=True)
