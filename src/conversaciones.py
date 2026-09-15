"""Carga de conversaciones.json a las tablas conversaciones/mensajes.

Dos hallazgos del perfilado que este módulo maneja explícitamente:

1. 289 de las 677 conversaciones pertenecen a leads cuyo canal declarado en
   leads.csv NO es WhatsApp (Meta Ads o Formulario Web): el lead entra por
   un formulario y la persona luego escribe por WhatsApp. Se cargan todas,
   sin filtrar por canal, porque descartar esas perdería el 43% del
   material que alimenta la extracción con IA.

2. 12 conversaciones referencian un lead_id que no existe en leads.csv
   (huérfanas) y 25 conversacion_id están duplicados. Se cargan igual pero
   marcadas, para que la extracción no falle en silencio ni se pierda
   evidencia de la inconsistencia.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def _linealizar(mensajes: list[dict]) -> str:
    """Convierte la lista de mensajes en el texto plano que ve el extractor
    de IA: "[hora] emisor: texto" por línea, en orden de llegada."""
    return "\n".join(f"[{m['hora']}] {m['emisor']}: {m['texto']}" for m in mensajes)


def cargar_conversaciones(
    con: sqlite3.Connection, dir_datasets: Path, corrida_id: str
) -> None:
    data = json.loads((dir_datasets / "conversaciones.json").read_text(encoding="utf-8"))

    lead_ids_existentes = {
        r[0] for r in con.execute("SELECT lead_id FROM leads").fetchall()
    }
    lead_a_empresa = dict(con.execute("SELECT lead_id, empresa_id FROM leads").fetchall())

    vistas: set[str] = set()
    n_duplicadas = 0
    n_huerfanas = 0

    # 12 conversaciones referencian un lead_id que no existe en leads.csv.
    # Es inconsistencia real del dataset que se preserva para auditoría (no
    # se descarta la conversación), así que se relaja la FK solo para esta
    # carga en vez de perder el dato o inventar un lead_id válido.
    con.execute("PRAGMA foreign_keys = OFF")

    for conv in data:
        conv_id = conv["conversacion_id"]
        if conv_id in vistas:
            n_duplicadas += 1
            conv_id = f"{conv_id}__DUP{n_duplicadas}"
        vistas.add(conv_id)

        lead_id = conv["lead_id"]
        huerfana = lead_id not in lead_ids_existentes
        if huerfana:
            n_huerfanas += 1

        texto = _linealizar(conv["mensajes"])
        hash_texto = hashlib.sha256(texto.encode("utf-8")).hexdigest()

        con.execute(
            "INSERT INTO conversaciones "
            "(conversacion_id, lead_id, empresa_id, canal, fecha_inicio, num_mensajes, "
            "texto_plano, hash_texto, huerfana) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conv_id, lead_id, lead_a_empresa.get(lead_id), conv.get("canal"),
                conv.get("fecha_inicio"), len(conv["mensajes"]), texto, hash_texto,
                int(huerfana),
            ),
        )
        for i, m in enumerate(conv["mensajes"]):
            con.execute(
                "INSERT INTO mensajes (conversacion_id, orden, emisor, hora, texto) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, i, m["emisor"], m.get("hora"), m["texto"]),
            )

    con.execute(
        "INSERT INTO calidad_datos (corrida_id, etapa, hallazgo, severidad, conteo, detalle) "
        "VALUES (?, 'conversaciones', 'conversacion_id_duplicados', 'aviso', ?, "
        "'Se sufijo el id para no perder mensajes; no afecta la extraccion.')",
        (corrida_id, n_duplicadas),
    )
    con.execute(
        "INSERT INTO calidad_datos (corrida_id, etapa, hallazgo, severidad, conteo, detalle) "
        "VALUES (?, 'conversaciones', 'conversaciones_huerfanas_lead_inexistente', 'aviso', ?, "
        "'lead_id referenciado no existe en leads.csv; se cargan igual pero no alimentan ningun score.')",
        (corrida_id, n_huerfanas),
    )
    con.commit()
    con.execute("PRAGMA foreign_keys = ON")
