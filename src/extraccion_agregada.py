"""Consolida extraccion_ia a nivel de lead.

25 leads tienen más de una conversación asociada (conversacion_id
duplicados en conversaciones.json). Cualquier consulta que una `leads` con
`extraccion_ia` por `lead_id` sin pasar por esta consolidación produce una
fila por conversación en vez de una por lead — rompe la PK de lead_scores
y duplica leads en la cola de asignaciones (ambos bugs aparecieron durante
el desarrollo y se corrigieron centralizando la lógica acá).

Regla de combinación: los campos binarios/numéricos se agregan con
MAX/OR (basta con que una conversación lo haya mencionado); los campos
categóricos se toman de la conversación con mayor `confianza` declarada.
"""
from __future__ import annotations

import sqlite3


def cargar_extraccion_agregada(con: sqlite3.Connection) -> dict[str, dict]:
    agregada: dict[str, dict] = {}
    for e in con.execute(
        "SELECT lead_id, forma_pago, presupuesto_cop, pidio_cita, pidio_cotizacion, "
        "objecion_principal, intencion, modelo_interes, sku_interes, confianza "
        "FROM extraccion_ia WHERE lead_id IS NOT NULL ORDER BY confianza DESC"
    ):
        acc = agregada.setdefault(e["lead_id"], {
            "forma_pago": None, "objecion_principal": None, "intencion": None,
            "modelo_interes": None, "sku_interes": None,
            "presupuesto_cop": None, "pidio_cita": 0, "pidio_cotizacion": 0,
        })
        # La primera vez que se ve este lead_id es la de mayor confianza,
        # por el ORDER BY: los campos categóricos se fijan una sola vez.
        for campo in ("forma_pago", "objecion_principal", "intencion", "modelo_interes", "sku_interes"):
            if acc[campo] is None:
                acc[campo] = e[campo]
        if e["presupuesto_cop"] is not None:
            acc["presupuesto_cop"] = max(acc["presupuesto_cop"] or 0, e["presupuesto_cop"])
        acc["pidio_cita"] = max(acc["pidio_cita"], e["pidio_cita"] or 0)
        acc["pidio_cotizacion"] = max(acc["pidio_cotizacion"], e["pidio_cotizacion"] or 0)

    return agregada
