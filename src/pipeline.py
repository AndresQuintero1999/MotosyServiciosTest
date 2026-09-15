"""Punto de entrada único del proceso completo: ingesta -> IA -> scoring ->
asignación. Se ejecuta con `python -m src.pipeline` (manual, cron o
webhook); no requiere ningún paso intermedio a mano.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from src.calibracion import calibrar
from src.config import CFG
from src.conversaciones import cargar_conversaciones
from src.db import reconstruir_esquema
from src.extraccion_ia import extraer_todas
from src.ingesta import cargar_catalogo, cargar_historico, cargar_leads, cargar_organizacion
from src.asignacion import construir_cola_diaria
from src.scoring import calcular_scores

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
        log.info("Etapa 1/8: organización (empresas, puntos de venta, asesores)")
        cargar_organizacion(con, CFG.dir_datasets)

        log.info("Etapa 2/8: catálogo de motos")
        # Depende de puntos_venta (disponibilidad_sku la referencia).
        indice = cargar_catalogo(con, CFG.dir_datasets)

        log.info("Etapa 3/8: leads (normalización, catálogo, deduplicación)")
        cargar_leads(con, CFG.dir_datasets, indice, corrida_id)

        log.info("Etapa 4/8: histórico de cierres (insumo de calibración)")
        cargar_historico(con, CFG.dir_datasets, corrida_id)

        log.info("Etapa 5/8: conversaciones")
        cargar_conversaciones(con, CFG.dir_datasets, corrida_id)

        log.info("Etapa 6/8: extracción con IA sobre las conversaciones")
        extraer_todas(con, CFG.ia, corrida_id, indice)

        log.info("Etapa 7/8: calibración del score contra el histórico")
        calibrar(con)

        log.info("Etapa 8/8: cálculo de scores y cola diaria por asesor")
        calcular_scores(con)
        construir_cola_diaria(con, corrida_id)

        duracion = time.monotonic() - inicio
        con.execute(
            "UPDATE corridas_pipeline SET estado='ok', finalizada_en=?, duracion_s=? "
            "WHERE corrida_id=?",
            (datetime.now(timezone.utc).isoformat(), duracion, corrida_id),
        )
        con.commit()
        log.info("Pipeline completo en %.1fs (corrida_id=%s)", duracion, corrida_id)
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
