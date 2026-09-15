"""Conexión y ciclo de vida del esquema SQLite.

El pipeline es idempotente por diseño: cada corrida borra el archivo de BD
y lo reconstruye desde cero a partir de los insumos fuente. No hay estado
acumulado que se pueda corromper entre corridas, lo que vuelve irrelevante
que el disco del hosting sea efímero (ver decisión en README).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def conectar(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def reconstruir_esquema(db_path: Path, schema_path: Path) -> sqlite3.Connection:
    """Borra la BD existente (si la hay) y la recrea desde schema.sql."""
    if db_path.exists():
        db_path.unlink()
    con = conectar(db_path)
    con.executescript(schema_path.read_text(encoding="utf-8"))
    con.commit()
    return con


def conectar_solo_lectura(db_path: Path) -> sqlite3.Connection:
    """Conexión para la API/tablero: no debe poder mutar el warehouse."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con
