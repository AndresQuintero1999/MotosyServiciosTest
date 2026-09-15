"""Conexión y ciclo de vida del esquema SQLite.

El pipeline es idempotente por diseño: cada corrida borra el archivo de BD
y lo reconstruye desde cero a partir de los insumos fuente. No hay estado
acumulado que se pueda corromper entre corridas, lo que vuelve irrelevante
que el disco del hosting sea efímero (ver decisión en README).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def conectar(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def reconstruir_esquema(db_path: Path, schema_path: Path) -> sqlite3.Connection:
    """Reconstruye la BD desde cero en un archivo temporal y lo publica con
    un reemplazo atómico (os.replace).

    La API sirve lecturas desde `db_path` mientras el pipeline corre; si
    esta función borrara el archivo en uso directamente, una request
    concurrente podría fallar (y en Windows, un handle abierto puede
    impedir incluso borrar el archivo). Construir aparte y reemplazar al
    final dejar al archivo público siempre en un estado consistente: o la
    versión anterior completa, o la nueva completa, nunca a medias.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(db_path.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    con = conectar(tmp_path)
    con.executescript(schema_path.read_text(encoding="utf-8"))
    con.commit()
    con.close()

    os.replace(tmp_path, db_path)
    return conectar(db_path)


def conectar_solo_lectura(db_path: Path) -> sqlite3.Connection:
    """Conexión para la API/tablero: no debe poder mutar el warehouse."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con
