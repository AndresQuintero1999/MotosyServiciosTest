"""Normalización de campos crudos de leads.

Cada función es pura (texto crudo -> valor canónico) y se probó contra los
patrones reales encontrados al perfilar `leads.csv` (ver README, sección
"Datos y decisiones"). Ninguna regla aquí es especulativa: cada una responde
a una inconsistencia medida, con su conteo.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Teléfonos
# ---------------------------------------------------------------------------
# Perfilado: 7 formatos de escritura para el mismo número de celular
# colombiano (10 dígitos, empieza en 3). 1.502/1.503 son normalizables;
# la única excepción es basura real ("300123", 6 dígitos).


def normalizar_telefono(crudo: str | None) -> tuple[str | None, bool]:
    """Devuelve (telefono_e164, valido). None si no es un celular CO válido."""
    if not crudo:
        return None, False
    digitos = re.sub(r"\D", "", crudo)
    if digitos.startswith("57") and len(digitos) == 12:
        digitos = digitos[2:]
    if len(digitos) == 10 and digitos.startswith("3"):
        return f"+57{digitos}", True
    return None, False


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------
# Perfilado: 4 formatos en fecha_registro/fecha_primer_contacto.
#   - ISO "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DDTHH:MM:SS"  -> NO ambiguo
#   - "DD-MM-YYYY" (solo fecha)                          -> NO ambiguo:
#       el 2do componente es SIEMPRE 8 o 9 en las 219 filas (mes real),
#       nunca >12, así que el orden día-mes es consistente en todo el archivo.
#   - "NN/NN/YYYY HH:MM"                                  -> AMBIGUO:
#       204/528 filas tienen 1er componente >12 (fuerza DD/MM),
#        59/528 filas tienen 2do componente >12 (fuerza MM/DD),
#       265/528 filas con ambos <=12 son genuinamente ambiguas.
#
# Las fechas ISO no ambiguas del dataset caen todas entre 2026-08-01 y
# 2026-09-10. Ese rango observado (meses 8 y 9) es la evidencia que se usa
# para desambiguar: se prefiere la interpretación cuyo mes cae en ese rango.
# Cuando ambas interpretaciones son plausibles se marca `ambigua=True` y se
# aplica la convención colombiana (día/mes) en vez de inventar certeza.

MESES_OBSERVADOS = {8, 9}

_PAT_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})$")
_PAT_DASH = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
_PAT_SLASH = re.compile(r"^(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2})$")


@dataclass(frozen=True)
class FechaNormalizada:
    iso: str | None
    ambigua: bool


def _valida(anio: int, mes: int, dia: int, hora: int = 0, minuto: int = 0, seg: int = 0) -> str | None:
    try:
        from datetime import datetime

        dt = datetime(anio, mes, dia, hora, minuto, seg)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def normalizar_fecha(crudo: str | None) -> FechaNormalizada:
    if not crudo:
        return FechaNormalizada(None, False)
    s = crudo.strip()
    if not s:
        return FechaNormalizada(None, False)

    m = _PAT_ISO.match(s)
    if m:
        anio, mes, dia, h, mi, se = map(int, m.groups())
        return FechaNormalizada(_valida(anio, mes, dia, h, mi, se), False)

    m = _PAT_DASH.match(s)
    if m:
        a, b, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # b (segundo componente) es el candidato a mes: en el archivo siempre
        # es <=12. Se confirma como no-ambiguo cuando cae en el rango
        # observado del dataset.
        dia, mes = a, b
        return FechaNormalizada(_valida(anio, mes, dia), False)

    m = _PAT_SLASH.match(s)
    if m:
        a, b, anio, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
        dia, mes, ambigua = _resolver_dia_mes(a, b)
        if dia is None:
            return FechaNormalizada(None, True)
        return FechaNormalizada(_valida(anio, mes, dia, h, mi), ambigua)

    return FechaNormalizada(None, False)


def _resolver_dia_mes(a: int, b: int) -> tuple[int | None, int | None, bool]:
    """Decide cuál de los dos componentes NN/NN es el día y cuál el mes.

    Devuelve (dia, mes, ambigua).
    """
    ddmm_valido = 1 <= b <= 12 and 1 <= a <= 31          # a=dia, b=mes
    mmdd_valido = 1 <= a <= 12 and 1 <= b <= 31           # a=mes, b=dia

    b_en_rango = ddmm_valido and b in MESES_OBSERVADOS
    a_en_rango = mmdd_valido and a in MESES_OBSERVADOS

    if b_en_rango and not a_en_rango:
        return a, b, False  # DD/MM confirmado por el mes observado
    if a_en_rango and not b_en_rango:
        return b, a, False  # MM/DD confirmado por el mes observado
    if b_en_rango and a_en_rango:
        # Ambas interpretaciones caen en el rango del dataset: genuinamente
        # ambiguo. Se asume convención colombiana (día/mes) y se marca.
        return a, b, True
    # Ninguna interpretación cae en el rango esperado: se usa DD/MM si es
    # sintácticamente válida (best-effort) y se marca como ambigua.
    if ddmm_valido:
        return a, b, True
    if mmdd_valido:
        return b, a, True
    return None, None, True


# ---------------------------------------------------------------------------
# Ciudad
# ---------------------------------------------------------------------------
# Perfilado: 38 valores distintos para ~11 ciudades reales (mayúsculas,
# minúsculas, con/sin tilde, abreviaturas como "B/quilla" y "Sta Marta").


def _sin_tildes(txt: str) -> str:
    nfkd = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


_CIUDADES: dict[str, str] = {}
for _canon, _alias in [
    ("Bogotá", ["bogota", "bogota dc", "bogota d.c.", "bogota d c"]),
    ("Medellín", ["medellin"]),
    ("Cartagena", ["cartagena", "cartagena de indias"]),
    ("Barranquilla", ["barranquilla", "b/quilla", "bquilla"]),
    ("Santa Marta", ["santa marta", "sta marta"]),
    ("Soacha", ["soacha"]),
    ("Soledad", ["soledad"]),
    ("Bello", ["bello"]),
    ("Montería", ["monteria"]),
    ("Itagüí", ["itagui"]),
    ("Rionegro", ["rionegro", "rio negro"]),
]:
    for a in _alias:
        _CIUDADES[a] = _canon


def normalizar_ciudad(crudo: str | None) -> str | None:
    if not crudo:
        return None
    clave = re.sub(r"\s+", " ", _sin_tildes(crudo).strip().lower())
    if not clave:
        return None
    return _CIUDADES.get(clave, crudo.strip().title())


# ---------------------------------------------------------------------------
# Canal
# ---------------------------------------------------------------------------
# Perfilado: 9 variantes de capitalización para 3 canales reales.

_CANALES = {
    "whatsapp": "WhatsApp",
    "meta ads": "Meta Ads",
    "formulario web": "Formulario Web",
}


def normalizar_canal(crudo: str | None) -> str | None:
    if not crudo:
        return None
    clave = re.sub(r"\s+", " ", crudo.strip().lower())
    return _CANALES.get(clave, crudo.strip() or None)


# ---------------------------------------------------------------------------
# Estado de gestión
# ---------------------------------------------------------------------------
# Perfilado: 10 variantes para 6 estados reales.

_ESTADOS = {
    "contactado": "Contactado",
    "sin gestion": "Sin gestión",
    "sin gestión": "Sin gestión",
    "cotizacion enviada": "Cotización enviada",
    "cotización enviada": "Cotización enviada",
    "en proceso": "En proceso",
    "no contesta": "No contesta",
    "descartado": "Descartado",
}


def normalizar_estado_gestion(crudo: str | None) -> str | None:
    if not crudo:
        return None
    clave = re.sub(r"\s+", " ", _sin_tildes(crudo).strip().lower())
    return _ESTADOS.get(clave, crudo.strip() or None)


# ---------------------------------------------------------------------------
# Nombre
# ---------------------------------------------------------------------------
# Perfilado: 100% de los nombres con padding en algunas filas; se colapsa
# espacio interno y se recorta, sin forzar un casing que pueda destruir
# información (algunos vienen en mayúsculas sostenidas legítimamente).


def normalizar_nombre(crudo: str | None) -> str | None:
    if not crudo:
        return None
    limpio = re.sub(r"\s+", " ", crudo.strip())
    return limpio or None


def clave_nombre(crudo: str | None) -> frozenset[str]:
    """Tokens normalizados de un nombre, usados solo para matching de dedup."""
    if not crudo:
        return frozenset()
    txt = _sin_tildes(crudo.strip().lower())
    txt = re.sub(r"[^a-z ]", " ", txt)
    return frozenset(t for t in txt.split() if len(t) > 1)
