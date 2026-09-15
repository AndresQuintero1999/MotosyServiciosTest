"""Tablero público: "Mis leads de hoy" por asesor, aislado por empresa.

El tablero NUNCA toca la base de datos directamente: todo pasa por la API
(`src/api/main.py`), que es donde vive el filtro por empresa_id. Así el
aislamiento se garantiza en un solo lugar, sin importar cuántas
superficies (tablero, futura app móvil, integraciones) lo consuman.

El selector de "empresa" en la barra lateral es una comodidad de
demostración para esta evaluación (un tablero real de cada comercializadora
se desplegaría con su propio token fijo, no con un dropdown); se etiqueta
como tal para que quede claro que no es el patrón de producción.
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from src.config import CFG
from src.ingesta import NOMBRES_EMPRESA

API_URL = os.getenv("API_URL", CFG.api_internal_url or "http://127.0.0.1:8000")

# Paleta (ver skill de dataviz): rampa secuencial de un solo hue para la
# temperatura (magnitud ordinal Frío -> Caliente), no colores decorativos.
COLOR_FRIO = "#86b6ef"     # step 250
COLOR_TIBIO = "#2a78d6"    # step 450
COLOR_CALIENTE = "#104281"  # step 650
COLOR_SERIE = "#2a78d6"    # slot categórico 1, para series únicas (sin identidad extra)
COLOR_TEXTO_MUTED = "#898781"
COLOR_GRID = "#e1e0d9"

st.set_page_config(page_title="Organizador de Leads", layout="wide", page_icon="🏍️")


# ---------------------------------------------------------------------------
# Cliente HTTP contra la API
# ---------------------------------------------------------------------------

def _get(path: str, token: str, params: dict | None = None) -> dict | None:
    try:
        r = requests.get(
            f"{API_URL}{path}", headers={"Authorization": f"Bearer {token}"},
            params=params, timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        st.error(f"No se pudo conectar con la API ({API_URL}): {exc}")
        return None


def _post(path: str, token: str) -> dict | None:
    try:
        r = requests.post(f"{API_URL}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        st.error(f"Falló la solicitud: {exc}")
        return None


# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------

st.sidebar.title("🏍️ Organizador de Leads")
st.sidebar.caption("Motos y Servicios de Colombia S.A.S.")

tokens_por_empresa = {v: k for k, v in CFG.tokens_empresa.items()}
if not tokens_por_empresa:
    st.sidebar.error("No hay tokens de empresa configurados (API_TOKEN_EMP_01/02/03).")
    st.stop()

st.sidebar.markdown("**Selector de demostración**")
st.sidebar.caption(
    "En producción cada comercializadora accedería con su propio token fijo, "
    "no eligiendo aquí — este selector solo existe para mostrar en vivo que "
    "una empresa jamás ve los leads de otra."
)
empresa_id = st.sidebar.selectbox(
    "Empresa", options=list(tokens_por_empresa.keys()),
    format_func=lambda e: NOMBRES_EMPRESA.get(e, e),
)
token = tokens_por_empresa[empresa_id]

asesores = _get("/asesores", token) or []
opciones_asesor = {"Todos": None} | {f"{a['nombre']} ({a['asesor_id']})": a["asesor_id"] for a in asesores}
etiqueta_asesor = st.sidebar.selectbox("Asesor", options=list(opciones_asesor.keys()))
asesor_id = opciones_asesor[etiqueta_asesor]

st.sidebar.divider()
with st.sidebar.expander("⚙️ Administración"):
    admin_token = st.text_input("Token de administrador", type="password")
    if st.button("🔄 Ejecutar pipeline ahora", use_container_width=True):
        if not admin_token:
            st.warning("Ingrese el token de administrador.")
        else:
            resp = _post("/admin/pipeline/run", admin_token)
            if resp:
                st.success(resp.get("mensaje", "Corrida iniciada."))
    if admin_token:
        corridas = _get("/admin/corridas", admin_token)
        if corridas:
            st.caption("Últimas corridas")
            st.dataframe(
                pd.DataFrame(corridas)[["iniciada_en", "estado", "disparador", "duracion_s"]].head(5),
                hide_index=True, use_container_width=True,
            )


# ---------------------------------------------------------------------------
# Encabezado + KPIs
# ---------------------------------------------------------------------------

st.title(f"Mis leads de hoy — {NOMBRES_EMPRESA.get(empresa_id, empresa_id)}")

resumen = _get("/leads/hoy/resumen", token)
if not resumen or resumen.get("fecha") is None:
    st.info("Todavía no hay una cola generada. Ejecute el pipeline desde el panel de administración.")
    st.stop()

st.caption(f"Cola generada para el {resumen['fecha']}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Leads en la cola de hoy", resumen["total_leads_hoy"])
col2.metric("🔥 Calientes", resumen["por_temperatura"].get("Caliente", 0))
col3.metric("🌤️ Tibios", resumen["por_temperatura"].get("Tibio", 0))
col4.metric("⏱️ Dentro del SLA de 24h", resumen["dentro_sla_24h"])


# ---------------------------------------------------------------------------
# Gráficas
# ---------------------------------------------------------------------------

g1, g2 = st.columns([1, 2])

with g1:
    st.subheader("Por temperatura")
    orden = ["Frío", "Tibio", "Caliente"]
    colores = {"Frío": COLOR_FRIO, "Tibio": COLOR_TIBIO, "Caliente": COLOR_CALIENTE}
    valores = [resumen["por_temperatura"].get(t, 0) for t in orden]
    fig = go.Figure(go.Bar(
        x=orden, y=valores, marker_color=[colores[t] for t in orden],
        text=valores, textposition="outside",
        texttemplate="%{text}",
    ))
    fig.update_layout(
        showlegend=False, height=320, margin=dict(t=10, b=10, l=10, r=10),
        plot_bgcolor="white", paper_bgcolor="white",
        yaxis=dict(gridcolor=COLOR_GRID, zeroline=False, title=None),
        xaxis=dict(title=None),
        font=dict(color="#0b0b0b"),
    )
    st.plotly_chart(fig, use_container_width=True)

with g2:
    st.subheader("Leads por asesor")
    if resumen["por_asesor"]:
        df_asesor = pd.DataFrame(resumen["por_asesor"]).sort_values("n", ascending=True)
        fig2 = go.Figure(go.Bar(
            x=df_asesor["n"], y=df_asesor["asesor_nombre"], orientation="h",
            marker_color=COLOR_SERIE, text=df_asesor["n"], textposition="outside",
        ))
        fig2.update_layout(
            showlegend=False, height=320, margin=dict(t=10, b=10, l=10, r=10),
            plot_bgcolor="white", paper_bgcolor="white",
            xaxis=dict(gridcolor=COLOR_GRID, zeroline=False, title=None),
            yaxis=dict(title=None),
            font=dict(color="#0b0b0b"),
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.caption("Sin asignaciones todavía.")


# ---------------------------------------------------------------------------
# Cola detallada
# ---------------------------------------------------------------------------

st.subheader("Cola de gestión" + (f" — {etiqueta_asesor}" if asesor_id else ""))

datos = _get("/leads/hoy", token, params={"asesor_id": asesor_id} if asesor_id else None)
if datos and datos["leads"]:
    df = pd.DataFrame(datos["leads"])
    columnas = [
        "posicion", "asesor_nombre", "nombre_cliente", "telefono_e164", "ciudad",
        "canal", "temperatura", "score", "modelo_interes", "estado_gestion", "motivo",
    ]
    columnas = [c for c in columnas if c in df.columns]
    df_mostrar = df[columnas].rename(columns={
        "posicion": "#", "asesor_nombre": "Asesor", "nombre_cliente": "Cliente",
        "telefono_e164": "Teléfono", "ciudad": "Ciudad", "canal": "Canal",
        "temperatura": "Temp.", "score": "Score", "modelo_interes": "Modelo",
        "estado_gestion": "Estado", "motivo": "Por qué priorizarlo",
    })

    def _resaltar_temp(val):
        color = {"Caliente": COLOR_CALIENTE, "Tibio": COLOR_TIBIO, "Frío": COLOR_FRIO}.get(val, "")
        return f"color: white; background-color: {color}" if color else ""

    st.dataframe(
        df_mostrar.style.map(_resaltar_temp, subset=["Temp."]),
        hide_index=True, use_container_width=True, height=520,
    )
    st.download_button(
        "⬇️ Descargar CSV", df_mostrar.to_csv(index=False).encode("utf-8"),
        file_name=f"leads_hoy_{empresa_id}_{resumen['fecha']}.csv", mime="text/csv",
    )
else:
    st.caption("No hay leads en la cola para este filtro.")

st.divider()
st.caption(
    "Cada empresa solo ve sus propios leads: el filtro sale del token de "
    "autenticación en cada llamada a la API, nunca de un parámetro de la página."
)
