"""Calcula el score de priorización para cada lead vigente.

score = score_calidad × factor_urgencia, ambos recortados a [0, 100] antes
de multiplicar y el resultado recortado de nuevo. Ver `calibracion.py` para
la justificación de por qué el tiempo se aplica multiplicativamente y el
resto de las señales de forma aditiva.

Solo se puntúan leads "maestro" (es_duplicado = 0): los duplicados dentro
de una misma empresa no compiten aparte en la cola del asesor, ya están
representados por su maestro.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from src.calibracion import BASE_SCORE_CALIDAD, bin_horas
from src.extraccion_agregada import cargar_extraccion_agregada

UMBRAL_CALIENTE = 70.0
UMBRAL_TIBIO = 40.0


def _temperatura(score: float) -> str:
    if score >= UMBRAL_CALIENTE:
        return "Caliente"
    if score >= UMBRAL_TIBIO:
        return "Tibio"
    return "Frío"


def _cargar_pesos(con: sqlite3.Connection, calibracion_id: str) -> dict:
    pesos: dict[str, dict[str, float]] = {}
    for variable, valor, puntos in con.execute(
        "SELECT variable, valor, puntos FROM calibracion_pesos WHERE calibracion_id = ?",
        (calibracion_id,),
    ):
        pesos.setdefault(variable, {})[valor] = puntos
    return pesos


def _ultima_calibracion(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT calibracion_id FROM calibracion_meta ORDER BY generada_en DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("No hay ninguna calibración: corra calibracion.calibrar() primero.")
    return row[0]


def _momento_de_referencia(con: sqlite3.Connection) -> datetime:
    """"Ahora" para efectos de urgencia = el lead más reciente del dataset.

    Los datos son sintéticos y fijos en el tiempo (ago-sep 2026). Si la
    urgencia se calculara contra el reloj real del sistema, cada día que
    pasa desde que se generaron los datos empujaría a TODOS los leads sin
    contactar al bucket ">72h" y el score colapsaría sin importar la
    corrida — el tablero dejaría de reflejar una operación "de hoy" el día
    de la sustentación. Anclar "ahora" al dato más reciente del propio
    dataset hace que la demo sea reproducible sin importar cuándo se
    ejecute el pipeline.
    """
    row = con.execute("SELECT MAX(fecha_registro) FROM leads").fetchone()
    referencia = datetime.fromisoformat(row[0])
    if referencia.tzinfo is None:
        referencia = referencia.replace(tzinfo=timezone.utc)
    return referencia


def calcular_scores(con: sqlite3.Connection, ahora: datetime | None = None) -> str:
    ahora = ahora or _momento_de_referencia(con)
    calibracion_id = _ultima_calibracion(con)
    pesos = _cargar_pesos(con, calibracion_id)

    # Fecha de referencia por grupo (maestro): la más temprana entre todos
    # los miembros, porque es el momento real en que la persona entró en
    # contacto por primera vez — la misma definición que usa el histórico
    # para horas_al_primer_contacto. Si CUALQUIER miembro del grupo ya tuvo
    # contacto efectivo, se usa el más temprano de esos también.
    grupos = con.execute(
        "SELECT lead_maestro_id, MIN(fecha_registro) AS fecha_registro_grupo, "
        "MIN(fecha_primer_contacto) AS fecha_contacto_grupo "
        "FROM leads GROUP BY lead_maestro_id"
    ).fetchall()
    fechas_grupo = {
        r["lead_maestro_id"]: (r["fecha_registro_grupo"], r["fecha_contacto_grupo"])
        for r in grupos
    }

    extraccion_por_lead = cargar_extraccion_agregada(con)

    leads = con.execute(
        "SELECT lead_id, empresa_id, canal, sku_interes FROM leads WHERE es_duplicado = 0"
    ).fetchall()

    filas_insertar = []
    for lead_row in leads:
        extra = extraccion_por_lead.get(lead_row["lead_id"], {})
        lead = {
            "lead_id": lead_row["lead_id"], "empresa_id": lead_row["empresa_id"],
            "canal": lead_row["canal"], "sku_interes": lead_row["sku_interes"],
            "forma_pago": extra.get("forma_pago"),
            "presupuesto_cop": extra.get("presupuesto_cop"),
            "pidio_cita": extra.get("pidio_cita", 0) or None,
        }
        fecha_registro_grupo, fecha_contacto_grupo = fechas_grupo.get(lead["lead_id"], (None, None))

        ya_contactado = fecha_contacto_grupo is not None
        horas_sin_contacto = None
        if not ya_contactado and fecha_registro_grupo:
            registro = datetime.fromisoformat(fecha_registro_grupo)
            if registro.tzinfo is None:
                registro = registro.replace(tzinfo=timezone.utc)
            horas_sin_contacto = max((ahora - registro).total_seconds() / 3600.0, 0.0)

        # `manifesto_cuota_inicial` equivalente a partir de lo extraído de la
        # conversación: SI si se mencionó un monto, NO si hubo conversación
        # pero sin monto, NO_INFORMA si no hay conversación asociada al lead.
        if lead["presupuesto_cop"] is not None:
            cuota_valor = "SI"
        elif lead["forma_pago"] is not None:
            cuota_valor = "NO"
        else:
            cuota_valor = "NO_INFORMA"

        # pidio_cita en el histórico solo tiene dos valores (SI/NO); sin
        # conversación asociada se asume NO, igual que si la conversación
        # existe pero no menciona ninguna cita.
        pidio_cita_valor = "SI" if lead["pidio_cita"] else "NO"
        forma_pago_valor = lead["forma_pago"] or "no_informa"

        aportes = {
            "canal": pesos.get("canal", {}).get(lead["canal"], 0.0),
            "manifesto_cuota_inicial": pesos.get("manifesto_cuota_inicial", {}).get(cuota_valor, 0.0),
            "forma_pago_declarada": pesos.get("forma_pago_declarada", {}).get(forma_pago_valor, 0.0),
            "pidio_cita": pesos.get("pidio_cita", {}).get(pidio_cita_valor, 0.0),
        }
        score_calidad = min(max(BASE_SCORE_CALIDAD + sum(aportes.values()), 0.0), 100.0)

        if ya_contactado:
            factor_urgencia = 1.0
            bucket = "ya_contactado"
        else:
            bucket = bin_horas(horas_sin_contacto)
            factor_urgencia = pesos.get("horas_al_primer_contacto_bin", {}).get(bucket, 1.0)

        score = round(min(max(score_calidad * factor_urgencia, 0.0), 100.0), 1)
        dentro_sla = int(not ya_contactado and (horas_sin_contacto or 0) <= 24)

        desglose = {
            "score_calidad": round(score_calidad, 1),
            "factor_urgencia": round(factor_urgencia, 3),
            "bucket_horas": bucket,
            "horas_sin_contacto": round(horas_sin_contacto, 1) if horas_sin_contacto is not None else None,
            "ya_contactado": ya_contactado,
            "aportes": {k: round(v, 2) for k, v in aportes.items()},
            "valores_usados": {
                "canal": lead["canal"], "manifesto_cuota_inicial": cuota_valor,
                "forma_pago_declarada": forma_pago_valor, "pidio_cita": pidio_cita_valor,
            },
            "calibracion_id": calibracion_id,
        }

        filas_insertar.append((
            lead["lead_id"], lead["empresa_id"], calibracion_id, score, _temperatura(score),
            None, round(score_calidad, 1), round(factor_urgencia, 3), horas_sin_contacto,
            dentro_sla, json.dumps(desglose, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ))

    con.executemany(
        "INSERT INTO lead_scores "
        "(lead_id, empresa_id, calibracion_id, score, temperatura, prob_cierre_estimada, "
        "score_calidad, factor_urgencia, horas_sin_contacto, dentro_sla_24h, desglose_json, "
        "calculado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        filas_insertar,
    )
    con.commit()
    return calibracion_id
