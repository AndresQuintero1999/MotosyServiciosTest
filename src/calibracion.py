"""Calibra los pesos del score de priorización contra historico_cierres.csv.

Enfoque elegido: reglas ponderadas, pero los pesos NO se inventan — se
derivan del lift real observado en 2.021 leads históricos gestionados
(se excluyen 179 "Sin gestión": tienen numero_contactos=0 por definición,
así que incluirlos infla artificialmente cualquier señal).

lift(variable=valor) = tasa_cierre(variable=valor) / tasa_cierre_global

Dos usos distintos del lift, según lo que mide la variable:

  - Atributos del LEAD (canal, cuota inicial, forma de pago, pidió cita):
    se combinan ADITIVAMENTE en `score_calidad`. Cada uno aporta
    puntos = (lift - 1) * PESO_BASE sobre una base de 50, así que un lift
    de 1.0 (sin efecto) no mueve el score y un lift de 1.22 suma ~5.5 pts.

  - horas_al_primer_contacto (variable de TIEMPO, no del lead): es la
    señal más predictiva de todo el dataset por un margen amplio
    (contactar en la primera hora cierra 3.2x más que después de 72h) y
    depende de una decisión de la empresa, no del cliente. Se usa
    MULTIPLICATIVAMENTE como `factor_urgencia`, aplicado directamente
    como el lift de su bucket: así el score final cae en el mismo orden
    de magnitud en que cae la probabilidad real de cierre al posponer el
    contacto.

La validación (¿el score separa a los que cierran de los que no?) se hace
reaplicando la misma fórmula sobre el propio histórico y midiendo AUC y el
lift del decil superior — la sugerencia explícita del enunciado.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

import pandas as pd

PESO_BASE = 25.0
BASE_SCORE_CALIDAD = 50.0

VARIABLES_ADITIVAS = ["canal", "manifesto_cuota_inicial", "forma_pago_declarada", "pidio_cita"]

_BINS_HORAS = [-0.01, 1, 4, 8, 24, 48, 72, 1e9]
_ETIQUETAS_HORAS = ["<=1h", "1-4h", "4-8h", "8-24h", "24-48h", "48-72h", ">72h"]


def bin_horas(horas: float | None) -> str | None:
    if horas is None:
        return None
    for i, limite in enumerate(_BINS_HORAS[1:]):
        if horas <= limite:
            return _ETIQUETAS_HORAS[i]
    return _ETIQUETAS_HORAS[-1]


def _cargar_historico_gestionado(con: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT * FROM historico_cierres WHERE gestionado = 1", con
    )
    df["cerrado"] = (df.desenlace == "Cerrado").astype(int)
    df["pidio_cita_txt"] = df.pidio_cita.map({1: "SI", 0: "NO"})
    df["horas_bin"] = df.horas_al_primer_contacto.map(bin_horas)
    return df


def _lift_por_variable(df: pd.DataFrame, columna: str, tasa_global: float) -> pd.DataFrame:
    g = df.groupby(columna).agg(n=("cerrado", "size"), tasa_cierre=("cerrado", "mean"))
    g["lift"] = g.tasa_cierre / tasa_global
    return g.reset_index().rename(columns={columna: "valor"})


def _auc(scores: pd.Series, etiquetas: pd.Series) -> float:
    """AUC manual (Mann-Whitney), sin depender de scikit-learn."""
    rangos = scores.rank(method="average")
    n_pos = int(etiquetas.sum())
    n_neg = len(etiquetas) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    suma_rangos_pos = rangos[etiquetas == 1].sum()
    return (suma_rangos_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def calibrar(con: sqlite3.Connection) -> str:
    calibracion_id = str(uuid.uuid4())
    df = _cargar_historico_gestionado(con)
    tasa_global = df.cerrado.mean()

    pesos_por_variable: dict[str, dict[str, float]] = {}

    mapa_columnas = {
        "canal": "canal",
        "manifesto_cuota_inicial": "manifesto_cuota_inicial",
        "forma_pago_declarada": "forma_pago_declarada",
        "pidio_cita": "pidio_cita_txt",
    }
    for variable, columna in mapa_columnas.items():
        tabla = _lift_por_variable(df, columna, tasa_global)
        pesos_por_variable[variable] = {}
        for _, fila in tabla.iterrows():
            puntos = round((fila.lift - 1) * PESO_BASE, 2)
            pesos_por_variable[variable][fila.valor] = puntos
            con.execute(
                "INSERT INTO calibracion_pesos "
                "(calibracion_id, variable, valor, n_historico, tasa_cierre, lift, puntos) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (calibracion_id, variable, fila.valor, int(fila.n), round(fila.tasa_cierre, 4),
                 round(fila.lift, 3), puntos),
            )

    tabla_horas = _lift_por_variable(df, "horas_bin", tasa_global)
    pesos_horas: dict[str, float] = {}
    for _, fila in tabla_horas.iterrows():
        lift = round(fila.lift, 3)
        pesos_horas[fila.valor] = lift
        con.execute(
            "INSERT INTO calibracion_pesos "
            "(calibracion_id, variable, valor, n_historico, tasa_cierre, lift, puntos) "
            "VALUES (?, 'horas_al_primer_contacto_bin', ?, ?, ?, ?, ?)",
            (calibracion_id, fila.valor, int(fila.n), round(fila.tasa_cierre, 4), lift, lift),
        )

    # ---- Validación: reaplicar la fórmula sobre el propio histórico ----
    def _score_fila(row) -> float:
        calidad = BASE_SCORE_CALIDAD
        calidad += pesos_por_variable["canal"].get(row["canal"], 0.0)
        calidad += pesos_por_variable["manifesto_cuota_inicial"].get(row["manifesto_cuota_inicial"], 0.0)
        calidad += pesos_por_variable["forma_pago_declarada"].get(row["forma_pago_declarada"], 0.0)
        calidad += pesos_por_variable["pidio_cita"].get(row["pidio_cita_txt"], 0.0)
        calidad = min(max(calidad, 0.0), 100.0)
        urgencia = pesos_horas.get(row["horas_bin"], 1.0)
        return min(max(calidad * urgencia, 0.0), 100.0)

    df["score_simulado"] = df.apply(_score_fila, axis=1)
    auc = _auc(df.score_simulado, df.cerrado)

    df["decil"] = pd.qcut(df.score_simulado.rank(method="first"), 10, labels=False)
    tasa_decil_superior = df[df.decil == 9].cerrado.mean()
    lift_decil_superior = tasa_decil_superior / tasa_global

    con.execute(
        "INSERT INTO calibracion_meta "
        "(calibracion_id, generada_en, n_historico_usado, tasa_cierre_global, auc, "
        "lift_decil_superior, notas) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            calibracion_id,
            datetime.now(timezone.utc).isoformat(),
            len(df),
            round(tasa_global, 4),
            round(auc, 4),
            round(lift_decil_superior, 3),
            f"PESO_BASE={PESO_BASE}. Variables aditivas: {VARIABLES_ADITIVAS}. "
            "horas_al_primer_contacto se aplica como multiplicador (factor_urgencia), "
            "no aditivo, por ser la señal mas predictiva del dataset (lift 1.69 en <=1h "
            "vs 0.52 en >72h) y depender de una decision de la empresa, no del lead.",
        ),
    )
    con.commit()
    return calibracion_id
