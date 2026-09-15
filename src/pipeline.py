"""Punto de entrada único del proceso completo: ingesta -> IA -> scoring ->
asignación. Se ejecuta con `python -m src.pipeline` (manual, cron o
webhook); no requiere ningún paso intermedio a mano.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from src.config import CFG
from src.conversaciones import cargar_conversaciones
from src.db import reconstruir_esquema
from src.ingesta import cargar_catalogo, cargar_historico, cargar_leads, cargar_organizacion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")


def ejecutar(disparador: str = "manual") -> str:
    corrida_id = str(uuid.uuid4())
    inicio = time.monotonic()
    iniciada_en = datetime.now(timezone.utc).isoformat()

    con = reconstruir_esquema(CFG.db_path, CFG.schema_path)
    con.execute(
        "INSERT INTO corridas_pipeline (corrida_id, iniciada_en, estado, disparador) "
        "VALUES (?, ?, 'en_progreso', ?)",
        (corrida_id, iniciada_en, disparador),
    )
    con.commit()

    try:
        log.info("Etapa 1/5: organización (empresas, puntos de venta, asesores)")
        cargar_organizacion(con, CFG.dir_datasets)

        log.info("Etapa 2/5: catálogo de motos")
        # Depende de puntos_venta (disponibilidad_sku la referencia).
        indice = cargar_catalogo(con, CFG.dir_datasets)

        log.info("Etapa 3/5: leads (normalización, catálogo, deduplicación)")
        cargar_leads(con, CFG.dir_datasets, indice, corrida_id)

        log.info("Etapa 4/5: histórico de cierres (insumo de calibración)")
        cargar_historico(con, CFG.dir_datasets, corrida_id)

        log.info("Etapa 5/5: conversaciones")
        cargar_conversaciones(con, CFG.dir_datasets, corrida_id)

        duracion = time.monotonic() - inicio
        con.execute(
            "UPDATE corridas_pipeline SET estado='ok', finalizada_en=?, duracion_s=? "
            "WHERE corrida_id=?",
            (datetime.now(timezone.utc).isoformat(), duracion, corrida_id),
        )
        con.commit()
        log.info("Ingesta completada en %.1fs (corrida_id=%s)", duracion, corrida_id)
    except Exception as exc:  # noqa: BLE001 - se registra y se re-lanza
        con.execute(
            "UPDATE corridas_pipeline SET estado='error', finalizada_en=?, error=? WHERE corrida_id=?",
            (datetime.now(timezone.utc).isoformat(), str(exc), corrida_id),
        )
        con.commit()
        log.exception("Ingesta fallida")
        raise
    finally:
        con.close()

    return corrida_id


if __name__ == "__main__":
    ejecutar(disparador="manual")
