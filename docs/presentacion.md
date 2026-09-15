# Organizador de Leads — Presentación (8 diapositivas)

*Formato markdown, listo para pasar a slides (Marp, reveal.js, Google
Slides). Cada `---` es un corte de diapositiva.*

---

## 1. El problema, en números

- +3.000 leads/mes, **< 1 de cada 10 cierra**.
- **4 de cada 10 no se tocan en las primeras 24h** — ahí se pierden.
- La conversación de WhatsApp tiene la información (modelo, cuota inicial,
  forma de pago) pero **nadie la pasa al CRM**: el asesor arranca de cero.
- Los asesores llaman **por orden de llegada**, no por probabilidad de compra.

> El histórico lo confirma con datos: tasa de cierre global = **8.95%**.

---

## 2. El hallazgo que reencuadra el problema

`historico_cierres.csv` (2.200 leads, desenlace real) muestra que la
variable más predictiva **no es un atributo del lead — es una decisión de
la empresa**:

| Horas al primer contacto | Tasa de cierre | Lift |
|---|---|---|
| ≤ 1h | 15.1% | **1.69×** |
| > 72h | 4.7% | **0.52×** |

Contactar en la primera hora **triplica** la probabilidad de cierre.
Ninguna otra señal del dataset se acerca a ese margen.

**→ El score se diseñó alrededor de esto**: `calidad × urgencia`, no solo calidad.

---

## 3. Arquitectura de punta a punta

```
5 CSV/JSON fuente → pipeline (8 etapas, 1 comando) → SQLite (17 tablas)
                                                          ↓
                                    API (aislada por empresa) → Tablero
```

- **Un solo disparo**: `python -m src.pipeline`. Programado (cron diario)
  o por evento (webhook `/admin/pipeline/run`).
- **Un solo servicio desplegado**: API interna + tablero público,
  compartiendo el mismo SQLite (`run_server.py`).
- Diagrama completo: [`docs/arquitectura.md`](arquitectura.md).

---

## 4. Datos: limpiar sin perder información

Perfilado exhaustivo *antes* de normalizar. Ejemplos:

- **4 formatos de fecha** mezclando DD/MM y MM/DD → se desambigua con el
  rango real observado en las fechas ISO del propio dataset; lo que sigue
  siendo ambiguo (3.4%) se **marca**, no se inventa.
- **Duplicados por teléfono**: de 141 grupos, **91 cruzan `empresa_id`**.
  → Esos NO se fusionan: es la misma persona en dos comercializadoras del
  grupo. Fusionarlos habría violado el aislamiento exigido. Llave de
  dedup: `(empresa_id, teléfono)`, nunca el teléfono solo.
- Cada hallazgo queda registrado con su conteo en `calidad_datos` —
  evidencia, no promesa.

---

## 5. IA: extracción + score explicable y validado

- **Extracción estructurada** de 677 conversaciones (SDK OpenAI →
  OpenRouter, modelos gratuitos para prueba): modelo, presupuesto, forma
  de pago, intención, objeción, pidió cita/cotización.
- Extractor de reglas de respaldo si la IA falla o está deshabilitada: el
  pipeline **nunca se cae** por un proveedor externo.
- **Score = reglas ponderadas, pesos derivados del lift real del
  histórico** (no inventados).
- **Validación** (como sugiere el enunciado): se reaplicó la fórmula sobre
  el propio histórico → **AUC = 0.63**, decil superior cierra **1.63×**
  más que el promedio.

---

## 6. Producto: "mis leads de hoy"

- Cola diaria por asesor, repartida por score respetando la
  `capacidad_diaria_leads` de cada uno (round-robin, nadie se queda solo
  con los fríos).
- Cada lead trae **por qué está priorizado**: horas sin contacto, señales
  de la conversación, modelo de interés.
- Tablero público en Streamlit, consumiendo la API — nunca la base de
  datos directamente.

*(demo en vivo aquí)*

---

## 7. Aislamiento por empresa — no negociable

- `empresa_id` sale **siempre del token** autenticado, nunca de un
  parámetro de la URL.
- Probado explícitamente: pedir `?empresa_id=otra-empresa` con el token
  de otra empresa se ignora.
- Verificado en 46 pruebas automatizadas (unitarias + integración), que
  corren en CI en cada push.

---

## 8. Qué haría con más tiempo

- Calibrar los umbrales Caliente/Tibio/Frío por decil, no por corte fijo.
- Matching de modelo más flexible (algunos textos parciales inequívocos
  hoy quedan sin resolver por umbral).
- Preferir el modelo mencionado en la conversación sobre el capturado por
  el canal cuando difieren.
- Recalibración periódica con el desenlace real de los leads gestionados
  (feedback loop), no solo con el histórico fijo entregado.

**Repositorio**: enlace · **URL pública**: enlace · **README**: decisiones
y supuestos completos.
