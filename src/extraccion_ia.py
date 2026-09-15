"""Extrae información estructurada de las conversaciones de WhatsApp.

Usa el SDK de OpenAI apuntando a un `base_url` configurable (OpenRouter por
defecto, con modelos gratuitos para pruebas; OpenAI directo cambiando solo
variables de entorno — ver `.env.example`).

Diseño defensivo, porque el proveedor puede fallar o el modelo gratuito
puede devolver texto mal formado:

  - Reintentos con backoff (tenacity) ante errores transitorios de red/API.
  - Si el JSON no parsea tras los reintentos, la fila queda con `error`
    poblado y el resto de la extracción continúa — una conversación mala
    no tumba la corrida completa.
  - Caché por `hash_texto`: una conversación ya extraída no se vuelve a
    pagar en la siguiente corrida si su texto no cambió.
  - `IA_HABILITADA=false` permite correr todo el pipeline sin llamar a
    ningún LLM, usando un extractor de reglas de respaldo — útil para
    demos sin llave de API o para no depender de la disponibilidad de un
    modelo gratuito el día de la sustentación.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from src.catalogo import ReferenciaMoto, resolver_modelo
from src.config import ConfigIA

log = logging.getLogger("extraccion_ia")

FORMAS_PAGO = {"contado", "credito", "no_informa"}
INTENCIONES = {"compra_inmediata", "cotizando", "comparando", "curioseando"}
OBJECIONES = {"precio", "credito_reportado", "comparando", "tiempo", "ninguna", "otra"}

PROMPT_SISTEMA = """Eres un analista que lee conversaciones de WhatsApp entre un \
cliente y un asesor comercial de una comercializadora de motos en Colombia, y \
extrae información estructurada para priorizar el seguimiento comercial.

Responde EXCLUSIVAMENTE con un objeto JSON (sin texto adicional, sin markdown) \
con estas claves exactas:

- "modelo_interes": string o null. El modelo de moto que el cliente menciona \
  querer, tal como lo escribió (ej: "Pulsar RS 200", "la Dio").
- "presupuesto_cop": entero o null. Cuota inicial o presupuesto en pesos \
  colombianos que el cliente menciona tener disponible. Si dice "tengo 2 \
  palos" son 2.000.000. Si no menciona ningún monto propio, usa null.
- "forma_pago": uno de "contado", "credito", "no_informa".
- "intencion": una de "compra_inmediata" (quiere comprar ya/pide cerrar), \
  "cotizando" (pide precio o cotización formal), "comparando" (dice estar \
  mirando otras opciones/marcas), "curioseando" (solo pregunta sin señal de \
  avance).
- "objecion_principal": una de "precio" (le parece caro), "credito_reportado" \
  (menciona reportes en centrales de riesgo), "comparando" (compara con la \
  competencia), "tiempo" (no tiene afán, solo mirando), "ninguna" (no hay \
  objeción clara), "otra".
- "pidio_cita": true/false. El cliente pidió agendar una visita o llamada.
- "pidio_cotizacion": true/false. El cliente pidió que le envíen la \
  cotización formal.
- "confianza": número entre 0 y 1, tu confianza en esta extracción.

Si la conversación no da información para un campo, usa null o el valor \
neutral indicado ("no_informa", "ninguna", false). No inventes datos."""


def _construir_cliente(cfg: ConfigIA) -> OpenAI:
    headers = {}
    if cfg.app_url:
        headers["HTTP-Referer"] = cfg.app_url
    if cfg.app_nombre:
        headers["X-Title"] = cfg.app_nombre
    return OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, default_headers=headers or None)


def _validar_payload(payload: dict) -> dict:
    def _en(valor, permitidos, defecto):
        return valor if valor in permitidos else defecto

    return {
        "modelo_interes": (payload.get("modelo_interes") or None),
        "presupuesto_cop": _coaccionar_entero(payload.get("presupuesto_cop")),
        "forma_pago": _en(payload.get("forma_pago"), FORMAS_PAGO, "no_informa"),
        "intencion": _en(payload.get("intencion"), INTENCIONES, "curioseando"),
        "objecion_principal": _en(payload.get("objecion_principal"), OBJECIONES, "ninguna"),
        "pidio_cita": bool(payload.get("pidio_cita", False)),
        "pidio_cotizacion": bool(payload.get("pidio_cotizacion", False)),
        "confianza": min(max(float(payload.get("confianza", 0.5) or 0.5), 0.0), 1.0),
    }


def _coaccionar_entero(valor) -> int | None:
    if valor is None or valor == "":
        return None
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
def _llamar_llm(cliente: OpenAI, cfg: ConfigIA, texto: str) -> dict:
    resp = cliente.chat.completions.create(
        model=cfg.modelo,
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": texto},
        ],
        temperature=0,
        timeout=cfg.timeout_s,
    )
    contenido = resp.choices[0].message.content or "{}"
    contenido = re.sub(r"^```(json)?|```$", "", contenido.strip(), flags=re.MULTILINE).strip()
    payload = json.loads(contenido)
    tokens_in = getattr(resp.usage, "prompt_tokens", None)
    tokens_out = getattr(resp.usage, "completion_tokens", None)
    return {**_validar_payload(payload), "_tokens_in": tokens_in, "_tokens_out": tokens_out}


# ---------------------------------------------------------------------------
# Extractor de reglas: respaldo cuando IA_HABILITADA=false o cuando el LLM
# falla tras los reintentos. Cubre solo señales explícitas y de bajo riesgo
# de falso positivo; no reemplaza los matices que sí captura el LLM
# (intención, objeción), que quedan en su valor neutral.
#
# Señal crítica a evitar: el asesor casi siempre cita el precio de lista
# ("La Bajaj Dominar 400 está en $24.900.000...") y pregunta "¿de contado o
# financiada?" en su primer mensaje. Un regex que escanee la conversación
# completa confunde el precio del asesor con el presupuesto del cliente, y
# su pregunta con la respuesta. Por eso el monto y la forma de pago se
# buscan SOLO en las líneas del cliente.
# ---------------------------------------------------------------------------

_RE_MONTO = re.compile(r"(\d{1,3}(?:[.,]\d{3}){1,3})|(\d+)\s*(palos|millones|mill)", re.IGNORECASE)
_RE_LINEA = re.compile(r"^\[[^\]]*\]\s*(cliente|asesor):\s*(.*)$", re.IGNORECASE)
_AFIRMATIVOS = ("listo", "si", "sí", "dale", "claro", "porfa", "envie", "envíe", "mandela", "mándela")


def _separar_por_emisor(texto: str) -> list[tuple[str, str]]:
    lineas = []
    for renglon in texto.splitlines():
        m = _RE_LINEA.match(renglon.strip())
        if m:
            lineas.append((m.group(1).lower(), m.group(2).lower()))
    return lineas


def _extraer_monto(t: str) -> int | None:
    m = _RE_MONTO.search(t)
    if not m:
        return None
    if m.group(1):
        digitos = re.sub(r"[.,]", "", m.group(1))
        return int(digitos) if digitos.isdigit() else None
    if m.group(2):
        return int(m.group(2)) * 1_000_000
    return None


def _extraer_reglas(texto: str) -> dict:
    lineas = _separar_por_emisor(texto)
    texto_cliente = " ".join(t for emisor, t in lineas if emisor == "cliente") or texto.lower()

    presupuesto = _extraer_monto(texto_cliente)

    forma_pago = "no_informa"
    if "contado" in texto_cliente:
        forma_pago = "contado"
    elif "credito" in texto_cliente or "crédito" in texto_cliente or "financia" in texto_cliente:
        forma_pago = "credito"

    pidio_cita = any(p in texto_cliente for p in ["agendar", "visitar", "cita", "pasar por"])

    # pidio_cotizacion: el cliente lo pide explícitamente, o el asesor lo
    # ofrece y el cliente responde con un afirmativo poco después.
    pidio_cotizacion = any(p in texto_cliente for p in ["cotiza", "cotización", "cotizacion"])
    if not pidio_cotizacion:
        for i, (emisor, t) in enumerate(lineas):
            if emisor == "asesor" and "cotiza" in t:
                siguientes = lineas[i + 1 : i + 3]
                if any(
                    e == "cliente" and any(a in t2 for a in _AFIRMATIVOS)
                    for e, t2 in siguientes
                ):
                    pidio_cotizacion = True
                    break

    intencion = "curioseando"
    if any(p in texto_cliente for p in ["quiero comprarla", "la compro", "cerremos", "como hago para llevarla"]):
        intencion = "compra_inmediata"
    elif pidio_cotizacion or "precio" in texto_cliente:
        intencion = "cotizando"
    elif any(p in texto_cliente for p in ["comparando", "otra marca", "otra opcion", "otra opción"]):
        intencion = "comparando"

    objecion = "ninguna"
    if "reporte" in texto_cliente or "central" in texto_cliente or "datacredito" in texto_cliente:
        objecion = "credito_reportado"
    elif "caro" in texto_cliente or "costoso" in texto_cliente:
        objecion = "precio"
    elif "otra marca" in texto_cliente or "comparando" in texto_cliente:
        objecion = "comparando"
    elif "solo mirando" in texto_cliente or "mirando precios" in texto_cliente:
        objecion = "tiempo"

    return {
        "modelo_interes": None,
        "presupuesto_cop": presupuesto,
        "forma_pago": forma_pago,
        "intencion": intencion,
        "objecion_principal": objecion,
        "pidio_cita": pidio_cita,
        "pidio_cotizacion": pidio_cotizacion,
        "confianza": 0.4,
        "_tokens_in": None,
        "_tokens_out": None,
    }


def extraer_todas(
    con: sqlite3.Connection, cfg: ConfigIA, corrida_id: str,
    indice_catalogo: list[ReferenciaMoto],
) -> None:
    ya_cacheadas = {
        r[0] for r in con.execute("SELECT hash_texto FROM extraccion_ia").fetchall()
    }
    pendientes = con.execute(
        "SELECT hash_texto, conversacion_id, lead_id, texto_plano FROM conversaciones "
        "WHERE huerfana = 0"
    ).fetchall()
    pendientes = [p for p in pendientes if p[0] not in ya_cacheadas]

    if cfg.max_conversaciones:
        pendientes = pendientes[: cfg.max_conversaciones]

    log.info("Conversaciones a extraer: %d (cacheadas: %d)", len(pendientes), len(ya_cacheadas))
    if not pendientes:
        return

    n_ia, n_reglas, n_error = 0, 0, 0
    cliente = _construir_cliente(cfg) if cfg.habilitada else None

    def _procesar(fila):
        hash_texto, conv_id, lead_id, texto = fila
        if not cfg.habilitada:
            return hash_texto, conv_id, lead_id, _extraer_reglas(texto), "reglas", None
        try:
            resultado = _llamar_llm(cliente, cfg, texto)
            return hash_texto, conv_id, lead_id, resultado, "ia", None
        except Exception as exc:  # noqa: BLE001
            log.warning("Fallo LLM para %s tras reintentos, uso reglas: %s", conv_id, exc)
            return hash_texto, conv_id, lead_id, _extraer_reglas(texto), "mixto", str(exc)

    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrencia)) as pool:
        futuros = [pool.submit(_procesar, p) for p in pendientes]
        for fut in as_completed(futuros):
            hash_texto, conv_id, lead_id, r, origen, error = fut.result()
            if origen == "ia":
                n_ia += 1
            elif origen == "reglas":
                n_reglas += 1
            else:
                n_error += 1

            sku, _score = resolver_modelo(r["modelo_interes"], indice_catalogo)
            con.execute(
                "INSERT OR REPLACE INTO extraccion_ia "
                "(hash_texto, conversacion_id, lead_id, modelo_interes, sku_interes, "
                "presupuesto_cop, forma_pago, intencion, objecion_principal, pidio_cita, "
                "pidio_cotizacion, confianza, origen, modelo_llm, tokens_entrada, "
                "tokens_salida, extraido_en, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hash_texto, conv_id, lead_id, r["modelo_interes"], sku,
                    r["presupuesto_cop"], r["forma_pago"], r["intencion"], r["objecion_principal"],
                    int(r["pidio_cita"]), int(r["pidio_cotizacion"]), r["confianza"], origen,
                    cfg.modelo if origen != "reglas" else None,
                    r.get("_tokens_in"), r.get("_tokens_out"),
                    datetime.now(timezone.utc).isoformat(), error,
                ),
            )

    con.execute(
        "INSERT INTO calidad_datos (corrida_id, etapa, hallazgo, severidad, conteo, detalle) "
        "VALUES (?, 'extraccion_ia', 'extraidas_via_llm', 'info', ?, '')", (corrida_id, n_ia),
    )
    con.execute(
        "INSERT INTO calidad_datos (corrida_id, etapa, hallazgo, severidad, conteo, detalle) "
        "VALUES (?, 'extraccion_ia', 'extraidas_via_reglas_fallback', 'aviso', ?, "
        "'IA deshabilitada o fallo del LLM tras reintentos')", (corrida_id, n_reglas + n_error),
    )
    con.commit()
    log.info("Extracción: %d via LLM, %d via reglas, %d con error", n_ia, n_reglas, n_error)
