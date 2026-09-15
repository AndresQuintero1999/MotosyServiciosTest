"""Construye la cola diaria de gestión por asesor: "mis leads de hoy".

Reglas de negocio:

  - Solo entran leads maestro (es_duplicado = 0) y no descartados
    (estado_gestion != "Descartado"). "Descartado" es un desenlace, no un
    lead pendiente de gestión.
  - La asignación respeta el punto_venta_id del lead: un asesor solo recibe
    leads de su propio punto de venta (y por lo tanto de su propia
    empresa, ya que esa relación es 1:1).
  - Dentro de cada punto de venta, los leads se reparten en round-robin por
    orden de score descendente entre los asesores activos, cada uno hasta
    su capacidad_diaria_leads. Esto evita que un asesor se quede con todos
    los leads calientes mientras otro solo recibe fríos, y respeta el tope
    operativo declarado por asesor.
  - Los leads que exceden la capacidad total del punto de venta no se
    asignan hoy; vuelven a competir por prioridad en la corrida del día
    siguiente (la urgencia solo sube, porque las horas sin contacto crecen).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from src.extraccion_agregada import cargar_extraccion_agregada


def _motivo(desglose: dict, extraccion: sqlite3.Row | None, lead: sqlite3.Row) -> str:
    partes = []
    if desglose.get("ya_contactado"):
        partes.append("Ya tuvo un primer contacto; dar seguimiento.")
    else:
        horas = desglose.get("horas_sin_contacto")
        if horas is not None:
            if horas <= 1:
                partes.append("Recién llegó (menos de 1h) — máxima ventana de conversión.")
            elif horas > 24:
                partes.append(f"Lleva {horas:.0f}h sin contacto — fuera del SLA de 24h.")
            else:
                partes.append(f"Lleva {horas:.0f}h sin contacto.")

    if extraccion is not None:
        if extraccion["presupuesto_cop"]:
            partes.append(f"Mencionó ${extraccion['presupuesto_cop']:,.0f} de cuota/presupuesto.")
        if extraccion["pidio_cita"]:
            partes.append("Pidió cita.")
        if extraccion["pidio_cotizacion"]:
            partes.append("Pidió cotización formal.")
        if extraccion["objecion_principal"] and extraccion["objecion_principal"] != "ninguna":
            partes.append(f"Objeción: {extraccion['objecion_principal']}.")

    modelo = lead["modelo_canonico"] or lead["modelo_interes_raw"]
    if modelo:
        partes.append(f"Interés: {modelo}.")

    return " ".join(partes) if partes else "Sin información adicional en conversación."


def construir_cola_diaria(con: sqlite3.Connection, corrida_id: str, fecha: date | None = None) -> None:
    fecha_str = (fecha or date.today()).isoformat()

    extraccion_por_lead = cargar_extraccion_agregada(con)

    leads = con.execute(
        """
        SELECT l.lead_id, l.empresa_id, l.punto_venta_id, l.modelo_interes_raw,
               c.modelo_canonico, s.score, s.temperatura, s.desglose_json
        FROM leads l
        JOIN lead_scores s ON s.lead_id = l.lead_id
        LEFT JOIN catalogo_motos c ON c.sku = l.sku_interes
        WHERE l.es_duplicado = 0 AND (l.estado_gestion IS NULL OR l.estado_gestion != 'Descartado')
        ORDER BY l.punto_venta_id, s.score DESC
        """
    ).fetchall()

    asesores_por_pv: dict[str, list[sqlite3.Row]] = {}
    for r in con.execute(
        "SELECT asesor_id, punto_venta_id, empresa_id, capacidad_diaria_leads "
        "FROM asesores WHERE activo = 1 ORDER BY asesor_id"
    ):
        asesores_por_pv.setdefault(r["punto_venta_id"], []).append(r)

    leads_por_pv: dict[str, list] = {}
    for lead in leads:
        leads_por_pv.setdefault(lead["punto_venta_id"], []).append(lead)

    filas_insertar = []
    pv_sin_asesores = []

    for pv_id, leads_pv in leads_por_pv.items():
        asesores = asesores_por_pv.get(pv_id, [])
        if not asesores:
            pv_sin_asesores.append(pv_id)
            continue

        capacidad_restante = {a["asesor_id"]: a["capacidad_diaria_leads"] for a in asesores}
        posicion = {a["asesor_id"]: 0 for a in asesores}
        empresa_id = asesores[0]["empresa_id"]

        idx_asesor = 0
        cola_leads = list(leads_pv)  # ya viene ordenada por score desc
        i = 0
        while i < len(cola_leads):
            asesor = asesores[idx_asesor % len(asesores)]
            idx_asesor += 1
            if capacidad_restante[asesor["asesor_id"]] <= 0:
                if all(c <= 0 for c in capacidad_restante.values()):
                    break  # capacidad total del punto de venta agotada por hoy
                continue

            lead = cola_leads[i]
            i += 1
            capacidad_restante[asesor["asesor_id"]] -= 1
            posicion[asesor["asesor_id"]] += 1

            extra = extraccion_por_lead.get(lead["lead_id"], {})
            extraccion = {
                "presupuesto_cop": extra.get("presupuesto_cop"),
                "pidio_cita": extra.get("pidio_cita"),
                "pidio_cotizacion": extra.get("pidio_cotizacion"),
                "objecion_principal": extra.get("objecion_principal"),
            }
            desglose = json.loads(lead["desglose_json"])
            motivo = _motivo(desglose, extraccion, lead)

            filas_insertar.append((
                fecha_str, lead["lead_id"], asesor["asesor_id"], empresa_id, pv_id,
                posicion[asesor["asesor_id"]], lead["score"], lead["temperatura"], motivo,
            ))

    con.executemany(
        "INSERT INTO asignaciones "
        "(fecha, lead_id, asesor_id, empresa_id, punto_venta_id, posicion, score, temperatura, motivo) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        filas_insertar,
    )

    if pv_sin_asesores:
        con.execute(
            "INSERT INTO calidad_datos (corrida_id, etapa, hallazgo, severidad, conteo, detalle) "
            "VALUES (?, 'asignacion', 'puntos_venta_sin_asesor_activo', 'error', ?, ?)",
            (corrida_id, len(pv_sin_asesores), ", ".join(pv_sin_asesores)),
        )
    con.commit()
