"""Configuración centralizada del pipeline, leída desde variables de entorno.

Un único punto de lectura de `.env` evita que cada módulo tenga su propia
noción de dónde están los datos o cuál es el proveedor de IA activo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")


def _bool(nombre: str, defecto: bool) -> bool:
    val = os.getenv(nombre)
    if val is None:
        return defecto
    return val.strip().lower() in {"1", "true", "si", "sí", "yes"}


def _int(nombre: str, defecto: int) -> int:
    val = os.getenv(nombre)
    return int(val) if val not in (None, "") else defecto


@dataclass(frozen=True)
class ConfigIA:
    base_url: str = os.getenv("IA_BASE_URL", "https://openrouter.ai/api/v1")
    api_key: str = os.getenv("IA_API_KEY", "")
    modelo: str = os.getenv("IA_MODELO", "meta-llama/llama-3.3-70b-instruct:free")
    app_url: str = os.getenv("IA_APP_URL", "")
    app_nombre: str = os.getenv("IA_APP_NOMBRE", "Organizador de Leads")
    max_conversaciones: int = _int("IA_MAX_CONVERSACIONES", 0)
    concurrencia: int = _int("IA_CONCURRENCIA", 4)
    timeout_s: int = _int("IA_TIMEOUT_S", 60)
    max_reintentos: int = _int("IA_MAX_REINTENTOS", 3)
    habilitada: bool = _bool("IA_HABILITADA", True)


@dataclass(frozen=True)
class Config:
    dir_datasets: Path = RAIZ / os.getenv("DIR_DATASETS", "datasets-assessment-analista-ia")
    db_path: Path = RAIZ / os.getenv("DB_PATH", "data/warehouse/leads.db")
    schema_path: Path = RAIZ / "db" / "schema.sql"
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = _int("API_PORT", 8000)
    ia: ConfigIA = ConfigIA()


CFG = Config()
