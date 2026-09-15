"""Carga de los 5 insumos fuente al warehouse, con normalización y dedup.

Cada función registra en `calidad_datos` lo que encontró y corrigió, para
que las inconsistencias queden documentadas en la propia base de datos en
vez de resueltas en silencio.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.catalogo import ReferenciaMoto, construir_indice, resolver_modelo
from src.dedup import LeadParaDedup, agrupar_duplicados
from src.normalizacion import (
    normalizar_canal,
    normalizar_ciudad,
    normalizar_estado_gestion,
    normalizar_fecha,
    normalizar_nombre,
    normalizar_telefono,
)

# Nombres de display de cada comercializadora. Se infieren de la geografía
# real de sus puntos de venta (verificado por cruce leads x ciudad): EMP-01
# opera en Antioquia, EMP-02 en la Costa Atlántica, EMP-03 en Bogotá — las
# tres regiones que menciona el enunciado. La lógica de negocio nunca
# depende de este nombre, solo de empresa_id; es puramente cosmético.
NOMBRES_EMPRESA = {
    "EMP-01": "Motos y Motores del Norte S.A.S.",
    "EMP-02": "Comercializadora Caribe S.A.S.",
    "EMP-03": "Comercializadora Capital S.A.S.",
}


def _leer_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _registrar_calidad(
    con: sqlite3.Connection, corrida_id: str, etapa: str, hallazgo: str,
    severidad: str, conteo: int, detalle: str = "",
) -> None:
    con.execute(
        "INSERT INTO calidad_datos (corrida_id, etapa, hallazgo, severidad, conteo, detalle) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (corrida_id, etapa, hallazgo, severidad, conteo, detalle),
    )


def cargar_catalogo(con: sqlite3.Connection, dir_datasets: Path) -> list[ReferenciaMoto]:
    df = _leer_csv(dir_datasets / "catalogo_motos.csv")
    filas = df.to_dict("records")
    for r in filas:
        con.execute(
            "INSERT INTO catalogo_motos "
            "(sku, marca, linea, modelo_canonico, cilindraje, segmento, precio_lista, unidades_disponibles) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["sku"], r["marca"], r["linea"], f"{r['marca']} {r['linea']}",
                int(r["cilindraje"]) if r["cilindraje"] else None,
                r["segmento"] or None,
                int(r["precio_lista"]) if r["precio_lista"] else None,
                int(r["unidades_disponibles"]) if r["unidades_disponibles"] else None,
            ),
        )
        for pv in (r.get("puntos_venta_disponibles") or "").split("|"):
            pv = pv.strip()
            if pv:
                con.execute(
                    "INSERT OR IGNORE INTO disponibilidad_sku (sku, punto_venta_id) VALUES (?, ?)",
                    (r["sku"], pv),
                )
    con.commit()
    return construir_indice(filas)


def cargar_organizacion(con: sqlite3.Connection, dir_datasets: Path) -> None:
    """Carga empresas, puntos_venta y asesores.

    La relación punto_venta -> empresa se deriva de asesores.csv, que la
    declara explícitamente y de forma 1:1 (verificado: 15 PV, cada uno
    mapeado a una sola empresa en el 100% de las filas de leads.csv y
    historico_cierres.csv).
    """
    ase = _leer_csv(dir_datasets / "asesores.csv")
    leads_df = _leer_csv(dir_datasets / "leads.csv")

    for empresa_id in sorted(ase.empresa_id.unique()):
        nombre = NOMBRES_EMPRESA.get(empresa_id, empresa_id)
        con.execute(
            "INSERT INTO empresas (empresa_id, nombre) VALUES (?, ?)", (empresa_id, nombre)
        )

    ciudad_por_pv = (
        leads_df.assign(ciudad_n=leads_df.ciudad.map(normalizar_ciudad))
        .groupby("punto_venta_id")
        .ciudad_n.agg(lambda s: Counter(s.dropna()).most_common(1)[0][0] if s.notna().any() else None)
    )

    pv_empresa = ase.drop_duplicates("punto_venta_id").set_index("punto_venta_id").empresa_id
    for pv_id, empresa_id in pv_empresa.items():
        con.execute(
            "INSERT INTO puntos_venta (punto_venta_id, empresa_id, ciudad) VALUES (?, ?, ?)",
            (pv_id, empresa_id, ciudad_por_pv.get(pv_id)),
        )

    for r in ase.to_dict("records"):
        con.execute(
            "INSERT INTO asesores "
            "(asesor_id, nombre, punto_venta_id, empresa_id, capacidad_diaria_leads, activo, fecha_ingreso) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                r["asesor_id"], r["nombre"], r["punto_venta_id"], r["empresa_id"],
                int(r["capacidad_diaria_leads"]),
                1 if r["activo"].strip().upper() == "SI" else 0,
                r["fecha_ingreso"] or None,
            ),
        )
    con.commit()


def cargar_leads(
    con: sqlite3.Connection, dir_datasets: Path, indice_catalogo: list[ReferenciaMoto],
    corrida_id: str,
) -> pd.DataFrame:
    df = _leer_csv(dir_datasets / "leads.csv")

    n_antes = len(df)
    df = df.drop_duplicates()
    n_dup_exactos = n_antes - len(df)
    if n_dup_exactos:
        _registrar_calidad(
            con, corrida_id, "leads", "filas_exactamente_duplicadas", "aviso",
            n_dup_exactos, "Filas idénticas repetidas en leads.csv; se conservó una sola copia.",
        )

    ahora = datetime.now(timezone.utc).isoformat()
    registros_dedup: list[LeadParaDedup] = []
    filas_norm = []

    fechas_invalidas = 0
    fechas_ambiguas = 0
    telefonos_invalidos = 0
    sin_match_modelo = 0

    for r in df.to_dict("records"):
        fr = normalizar_fecha(r["fecha_registro"])
        fpc = normalizar_fecha(r["fecha_primer_contacto"])
        tel_e164, tel_valido = normalizar_telefono(r["telefono"])
        ciudad_n = normalizar_ciudad(r["ciudad"])
        canal_n = normalizar_canal(r["canal"])
        estado_n = normalizar_estado_gestion(r["estado_gestion"])
        nombre_n = normalizar_nombre(r["nombre_cliente"])
        sku, score = resolver_modelo(r["modelo_interes_texto"], indice_catalogo)

        if fr.iso is None and r["fecha_registro"].strip():
            fechas_invalidas += 1
        if fr.ambigua:
            fechas_ambiguas += 1
        if not tel_valido:
            telefonos_invalidos += 1
        if sku is None and r["modelo_interes_texto"].strip():
            sin_match_modelo += 1

        fila = dict(
            lead_id=r["lead_id"],
            empresa_id=r["empresa_id"],
            punto_venta_id=r["punto_venta_id"],
            nombre_cliente=nombre_n,
            nombre_raw=r["nombre_cliente"],
            telefono_e164=tel_e164,
            telefono_raw=r["telefono"],
            telefono_valido=int(tel_valido),
            email=(r["email"].strip().lower() or None),
            ciudad=ciudad_n,
            ciudad_raw=r["ciudad"] or None,
            canal=canal_n,
            canal_raw=r["canal"] or None,
            estado_gestion=estado_n,
            estado_gestion_raw=r["estado_gestion"] or None,
            campania=(r["campania"].strip() or None),
            fecha_registro=fr.iso,
            fecha_registro_raw=r["fecha_registro"] or None,
            fecha_registro_ambigua=int(fr.ambigua),
            fecha_primer_contacto=fpc.iso,
            fecha_primer_contacto_raw=r["fecha_primer_contacto"] or None,
            modelo_interes_raw=r["modelo_interes_texto"] or None,
            sku_interes=sku,
            modelo_match_score=score,
            ingerido_en=ahora,
        )
        filas_norm.append(fila)
        registros_dedup.append(
            LeadParaDedup(
                lead_id=r["lead_id"], empresa_id=r["empresa_id"],
                telefono_e164=tel_e164, nombre=nombre_n,
                fecha_registro=fr.iso, fecha_primer_contacto=fpc.iso,
            )
        )

    grupos = agrupar_duplicados(registros_dedup)
    # grupos es lead_id -> GrupoDedup, y cada miembro de un mismo grupo
    # apunta al mismo objeto: se deduplica por identidad antes de contar
    # grupos (si no, un grupo de 2 miembros se contaría dos veces).
    grupos_unicos = {id(g): g for g in grupos.values()}.values()
    grupos_con_duplicados = [g for g in grupos_unicos if len(g.miembros) > 1]
    n_duplicados = len(grupos_con_duplicados)
    leads_en_grupo_duplicado = sum(len(g.miembros) for g in grupos_con_duplicados)

    # lead_maestro_id es una FK auto-referenciada dentro de la misma tabla.
    # Si un duplicado precede a su maestro en el orden del CSV, insertarlos
    # en una sola pasada rompe la restricción. Se inserta primero sin esa
    # columna (todas las filas ya existen) y se resuelve en una segunda
    # pasada con UPDATE, cuando cualquier lead_id referenciado es válido.
    maestros_y_motivos = []
    for fila in filas_norm:
        grupo = grupos[fila["lead_id"]]
        fila["es_duplicado"] = int(fila["lead_id"] != grupo.lead_maestro_id)
        maestros_y_motivos.append((grupo.lead_maestro_id, grupo.motivo, fila["lead_id"]))
        fila["lead_maestro_id"] = None
        fila["motivo_dedup"] = None

    cols = list(filas_norm[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    con.executemany(
        f"INSERT INTO leads ({', '.join(cols)}) VALUES ({placeholders})",
        [tuple(f[c] for c in cols) for f in filas_norm],
    )
    con.executemany(
        "UPDATE leads SET lead_maestro_id = ?, motivo_dedup = ? WHERE lead_id = ?",
        maestros_y_motivos,
    )

    _registrar_calidad(con, corrida_id, "leads", "telefonos_no_normalizables", "aviso", telefonos_invalidos)
    _registrar_calidad(con, corrida_id, "leads", "fechas_registro_invalidas", "error", fechas_invalidas,
                        "p.ej. dia 33 inexistente")
    _registrar_calidad(con, corrida_id, "leads", "fechas_registro_ambiguas_dd_mm_vs_mm_dd", "aviso", fechas_ambiguas,
                        "Se asumio convencion DD/MM y se marco fecha_registro_ambigua=1")
    _registrar_calidad(con, corrida_id, "leads", "modelo_interes_sin_match_en_catalogo", "aviso", sin_match_modelo)
    _registrar_calidad(
        con, corrida_id, "leads", "grupos_duplicados_misma_empresa", "info", n_duplicados,
        f"{leads_en_grupo_duplicado} leads consolidados en {n_duplicados} personas reales "
        "(telefono + empresa); no incluye coincidencias de telefono cross-empresa, que se "
        "preservan como leads independientes por aislamiento organizacional.",
    )
    con.commit()
    return pd.DataFrame(filas_norm)


def cargar_historico(con: sqlite3.Connection, dir_datasets: Path, corrida_id: str) -> None:
    df = _leer_csv(dir_datasets / "historico_cierres.csv")
    n_sin_gestion = 0
    for r in df.to_dict("records"):
        gestionado = 0 if r["desenlace"].strip() == "Sin gestión" else 1
        n_sin_gestion += 1 - gestionado
        con.execute(
            "INSERT INTO historico_cierres "
            "(lead_id, fecha_registro, canal, empresa_id, punto_venta_id, modelo_cotizado, "
            "precio_lista, horas_al_primer_contacto, numero_contactos, manifesto_cuota_inicial, "
            "forma_pago_declarada, pidio_cita, desenlace, gestionado) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["lead_id"], r["fecha_registro"], r["canal"], r["empresa_id"], r["punto_venta_id"],
                r["modelo_cotizado"],
                int(r["precio_lista"]) if r["precio_lista"] else None,
                float(r["horas_al_primer_contacto"]) if r["horas_al_primer_contacto"] else None,
                int(r["numero_contactos"]) if r["numero_contactos"] else None,
                r["manifesto_cuota_inicial"] or None,
                r["forma_pago_declarada"] or None,
                1 if r["pidio_cita"].strip().upper() == "SI" else 0,
                r["desenlace"],
                gestionado,
            ),
        )
    _registrar_calidad(
        con, corrida_id, "historico", "registros_sin_gestion_excluidos_de_calibracion", "info",
        n_sin_gestion, "numero_contactos=0 por definicion; incluirlos infla artificialmente los lifts.",
    )
    con.commit()
