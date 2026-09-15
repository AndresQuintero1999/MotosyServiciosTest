"""API pública del organizador de leads.

Todo endpoint de datos exige un token de empresa (ver `api/auth.py`) y
filtra SIEMPRE por el `empresa_id` que ese token resuelve — nunca por un
parámetro que el cliente pueda manipular. Esa es la implementación real
del aislamiento por comercializadora que exige el enunciado.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.auth import admin_autenticado, empresa_autenticada
from src.config import CFG
from src.db import conectar_solo_lectura
from src.pipeline import ejecutar


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # Si el servicio arranca sin base de datos (primer deploy, disco
    # efímero), corre el pipeline una vez para que la API no responda vacía.
    if not CFG.db_path.exists():
        threading.Thread(target=lambda: ejecutar(disparador="startup"), daemon=True).start()
    yield


app = FastAPI(
    title="Organizador de Leads — Motos y Servicios de Colombia",
    description="Lista priorizada de gestión diaria por asesor, aislada por comercializadora.",
    version="1.0.0",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"],
)

_lock_pipeline = threading.Lock()


def _con() -> sqlite3.Connection:
    con = conectar_solo_lectura(CFG.db_path)
    try:
        yield con
    finally:
        con.close()


@app.get("/salud")
def salud():
    existe = CFG.db_path.exists()
    return {"estado": "ok" if existe else "sin_datos", "db_existe": existe}


@app.get("/leads/hoy")
def leads_hoy(
    empresa_id: str = Depends(empresa_autenticada),
    asesor_id: str | None = Query(default=None),
    fecha: str | None = Query(default=None, description="YYYY-MM-DD, por defecto la última corrida"),
    con: sqlite3.Connection = Depends(_con),
):
    fecha = fecha or _ultima_fecha(con)
    if fecha is None:
        return {"fecha": None, "leads": []}

    sql = "SELECT * FROM v_leads_hoy WHERE empresa_id = ? AND fecha = ?"
    params: list = [empresa_id, fecha]
    if asesor_id:
        sql += " AND asesor_id = ?"
        params.append(asesor_id)
    sql += " ORDER BY asesor_id, posicion"

    filas = [dict(r) for r in con.execute(sql, params).fetchall()]
    return {"fecha": fecha, "total": len(filas), "leads": filas}


@app.get("/leads/hoy/resumen")
def resumen_hoy(
    empresa_id: str = Depends(empresa_autenticada),
    con: sqlite3.Connection = Depends(_con),
):
    fecha = _ultima_fecha(con)
    if fecha is None:
        return {"fecha": None}

    total = con.execute(
        "SELECT COUNT(*) FROM asignaciones WHERE empresa_id=? AND fecha=?", (empresa_id, fecha)
    ).fetchone()[0]
    por_temp = con.execute(
        "SELECT temperatura, COUNT(*) FROM asignaciones WHERE empresa_id=? AND fecha=? "
        "GROUP BY temperatura", (empresa_id, fecha),
    ).fetchall()
    dentro_sla = con.execute(
        "SELECT COUNT(*) FROM v_leads_hoy WHERE empresa_id=? AND fecha=? AND dentro_sla_24h=1",
        (empresa_id, fecha),
    ).fetchone()[0]
    por_asesor = con.execute(
        "SELECT asesor_id, asesor_nombre, COUNT(*) AS n "
        "FROM v_leads_hoy WHERE empresa_id=? AND fecha=? GROUP BY asesor_id ORDER BY n DESC",
        (empresa_id, fecha),
    ).fetchall()

    return {
        "fecha": fecha,
        "total_leads_hoy": total,
        "por_temperatura": {r[0]: r[1] for r in por_temp},
        "dentro_sla_24h": dentro_sla,
        "por_asesor": [dict(r) for r in por_asesor],
    }


@app.get("/asesores")
def asesores(
    empresa_id: str = Depends(empresa_autenticada),
    con: sqlite3.Connection = Depends(_con),
):
    filas = con.execute(
        "SELECT asesor_id, nombre, punto_venta_id, capacidad_diaria_leads "
        "FROM asesores WHERE empresa_id=? AND activo=1 ORDER BY nombre",
        (empresa_id,),
    ).fetchall()
    return [dict(r) for r in filas]


@app.get("/admin/corridas")
def corridas(_: None = Depends(admin_autenticado), con: sqlite3.Connection = Depends(_con)):
    filas = con.execute(
        "SELECT corrida_id, iniciada_en, finalizada_en, estado, disparador, duracion_s, error "
        "FROM corridas_pipeline ORDER BY iniciada_en DESC LIMIT 20"
    ).fetchall()
    return [dict(r) for r in filas]


@app.get("/admin/calidad")
def calidad(_: None = Depends(admin_autenticado), con: sqlite3.Connection = Depends(_con)):
    ultima = con.execute(
        "SELECT corrida_id FROM corridas_pipeline WHERE estado='ok' ORDER BY iniciada_en DESC LIMIT 1"
    ).fetchone()
    if not ultima:
        return []
    filas = con.execute(
        "SELECT etapa, hallazgo, severidad, conteo, detalle FROM calidad_datos "
        "WHERE corrida_id=? ORDER BY etapa, severidad", (ultima[0],),
    ).fetchall()
    return [dict(r) for r in filas]


@app.post("/admin/pipeline/run", status_code=202)
def disparar_pipeline(_: None = Depends(admin_autenticado)):
    if _lock_pipeline.locked():
        raise HTTPException(status_code=409, detail="Ya hay una corrida del pipeline en curso.")

    def _correr():
        with _lock_pipeline:
            ejecutar(disparador="webhook")

    threading.Thread(target=_correr, daemon=True).start()
    return {"disparado": True, "mensaje": "Corrida iniciada en segundo plano. Consulte /admin/corridas."}


def _ultima_fecha(con: sqlite3.Connection) -> str | None:
    row = con.execute("SELECT MAX(fecha) FROM asignaciones").fetchone()
    return row[0] if row and row[0] else None
