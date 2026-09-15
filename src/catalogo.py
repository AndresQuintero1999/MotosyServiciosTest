"""Resuelve texto libre de modelo de interés contra el catálogo de motos.

`modelo_interes_texto` llega como texto libre del canal de origen:
"A.K.T Dynamic R3 125", "suzuki best 125", "honda dio 110"... El catálogo
tiene 24 referencias canónicas ("AKT Dynamic R3 125", "Suzuki Best 125",
"Honda Dio 110"). Se resuelve con similitud difusa en vez de un mapeo
manual, porque el texto de entrada no está controlado por catálogo.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz, process

UMBRAL_MATCH = 70.0  # por debajo de esto se prefiere "sin match" a un falso positivo


@dataclass(frozen=True)
class ReferenciaMoto:
    sku: str
    marca: str
    linea: str
    modelo_canonico: str


def construir_indice(catalogo_rows: list[dict]) -> list[ReferenciaMoto]:
    return [
        ReferenciaMoto(
            sku=r["sku"],
            marca=r["marca"],
            linea=r["linea"],
            modelo_canonico=f"{r['marca']} {r['linea']}",
        )
        for r in catalogo_rows
    ]


def _limpiar(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = txt.lower()
    # "A.K.T" -> "akt": los puntos entre siglas rompen el match de token si
    # se dejan como separadores; se eliminan solo los puntos internos a una
    # sigla, no la puntuación general.
    txt = re.sub(r"(?<=[a-z])\.(?=[a-z])", "", txt)
    txt = re.sub(r"[^a-z0-9 ]", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def resolver_modelo(
    texto_libre: str | None, indice: list[ReferenciaMoto]
) -> tuple[str | None, float]:
    """Devuelve (sku, score 0-100). sku=None si no hay match confiable."""
    if not texto_libre or not texto_libre.strip() or not indice:
        return None, 0.0

    consulta = _limpiar(texto_libre)
    opciones = {ref.sku: _limpiar(ref.modelo_canonico) for ref in indice}

    resultado = process.extractOne(
        consulta, opciones, scorer=fuzz.token_sort_ratio
    )
    if resultado is None:
        return None, 0.0
    _valor, score, sku = resultado
    if score < UMBRAL_MATCH:
        return None, round(score, 1)
    return sku, round(score, 1)
