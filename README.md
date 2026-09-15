# Organizador de Leads — Motos y Servicios de Colombia S.A.S.

Assessment técnico para Analista de IA. Convierte los leads crudos de tres
canales (WhatsApp, Meta Ads, Formulario Web) en una lista priorizada de
gestión diaria por asesor, enriquecida con la información que hoy está
enterrada en las conversaciones de WhatsApp y que nadie pasa al CRM.

📄 Enunciado original: [`Assessment-Analista-IA-Enunciado.pdf`](Assessment-Analista-IA-Enunciado.pdf)
🏗️ Diagrama de arquitectura: [`docs/arquitectura.md`](docs/arquitectura.md)
🎤 Presentación: [`docs/presentacion.md`](docs/presentacion.md)

---

## 1. Qué hace

1. **Ingiere** los 5 archivos fuente sin intervención manual (`leads.csv`,
   `conversaciones.json`, `catalogo_motos.csv`, `asesores.csv`,
   `historico_cierres.csv`).
2. **Normaliza y consolida**: unifica teléfono, fecha, ciudad, canal,
   estado de gestión y modelo de interés; detecta y resuelve leads
   duplicados que llegaron por dos canales distintos.
3. **Extrae con IA** de cada conversación: modelo de interés, presupuesto
   o cuota inicial, forma de pago, intención, objeción principal, si pidió
   cita o cotización.
4. **Clasifica y prioriza** con un score explicable (`Caliente` /
   `Tibio` / `Frío`), calibrado y validado contra 2.200 leads históricos
   reales, no con pesos inventados.
5. **Persiste todo** en una base de datos relacional con modelo propio
   (17 tablas, 1 vista operativa — ver [`db/schema.sql`](db/schema.sql)).
6. **Se ejecuta de punta a punta con un solo comando**, programado
   (cron diario) o disparado por evento (webhook admin / botón manual).
7. **Publica el resultado**: una API (`/leads/hoy`) y un tablero público
   ("mis leads de hoy" por asesor).
8. **Aísla por empresa**: cada comercializadora del grupo solo ve sus
   propios leads, garantizado en la capa de autenticación de la API, no
   en el frontend.

## 2. Cómo se ejecuta

### 2.1 Local

```bash
# 1. Entorno (requiere Python 3.12; 3.13 no trae pip empaquetado en Windows)
py -3.12 -m venv .venv
.venv/Scripts/activate            # Windows
# source .venv/bin/activate       # macOS/Linux

pip install -r requirements.txt

# 2. Configuración
cp .env.example .env
# Editar .env: por defecto trae IA_HABILITADA=true apuntando a OpenRouter.
# Para correr sin llave de API (extractor de reglas de respaldo):
#   IA_HABILITADA=false

# 3. Pipeline completo — un solo disparo
python -m src.pipeline

# 4. API
uvicorn api.main:app --reload --port 8000

# 5. Tablero (en otra terminal)
streamlit run tablero/app.py

# 6. Pruebas (46, incluye integración contra la BD real)
pytest tests/ -v
```

Con el pipeline corrido, `http://localhost:8501` muestra el tablero.
Tokens de demo (ver `.env.example`): `demo-token-emp-01/02/03` (uno por
empresa) y `demo-admin-token` (panel de administración).

### 2.2 Producción (un solo servicio)

```bash
python run_server.py
```

Arranca la API en `127.0.0.1:$API_PORT` (interna, no expuesta) y el
tablero en `0.0.0.0:$PORT` (público), compartiendo el mismo disco y por lo
tanto el mismo SQLite. Si la base de datos no existe al arrancar, corre el
pipeline una vez antes de levantar los servidores. Ver
[`docs/arquitectura.md`](docs/arquitectura.md) para el porqué de este
diseño.

**Despliegue en Render**: `render.yaml` (blueprint) ya define el servicio
completo. En Render → *New* → *Blueprint*, apuntar a este repositorio y
completar en el panel las variables marcadas `sync: false`
(`IA_API_KEY`, `ADMIN_TOKEN`, `API_TOKEN_EMP_01/02/03`) — nunca se
versionan en el repo.

### 2.3 Automatización programada/por evento

`.github/workflows/pipeline-cron.yml` llama diariamente (y bajo demanda
con el botón *Run workflow*) al webhook `POST /admin/pipeline/run` del
servicio ya desplegado. Requiere los secretos de repositorio `APP_URL` y
`ADMIN_TOKEN` (configurar después del primer despliegue, en *Settings →
Secrets and variables → Actions*).

`.github/workflows/ci.yml` corre en cada push/PR: reconstruye el
warehouse con el extractor de reglas (sin costo ni llaves) y corre las 46
pruebas contra datos reales.

## 3. Arquitectura

Ver [`docs/arquitectura.md`](docs/arquitectura.md) (diagrama Mermaid +
tabla de decisiones). Resumen: `datasets → pipeline (8 etapas) → SQLite →
API (aislada por empresa) → tablero Streamlit`, todo detrás de un único
comando y un único servicio desplegado.

## 4. Datos: lo que se encontró y cómo se resolvió

El perfilado de los 5 archivos (antes de escribir una sola línea de
normalización) encontró:

| Hallazgo | Magnitud | Resolución |
|---|---|---|
| Formatos de fecha mezclados | 4 formatos; `NN/NN/AAAA` mezcla DD/MM y MM/DD | Las fechas ISO no ambiguas del propio dataset acotan el rango real a meses {8, 9}; se usa esa evidencia para desambiguar. Solo 51/1503 (3.4%) quedan genuinamente ambiguas (ambos componentes ∈ {8,9}) y se marcan con `fecha_registro_ambigua=1` en vez de forzar una certeza falsa. |
| Formatos de teléfono | 7 variantes | Normalización a E.164 (`+57XXXXXXXXXX`). 1.502/1.503 normalizables. |
| **Duplicados cross-empresa** | De 141 grupos que comparten teléfono, **91 cruzan `empresa_id`** | **No se fusionan.** `punto_venta_id → empresa_id` es 1:1 y consistente en el 100% de los 3.703 registros (leads + histórico): esos 91 grupos son la misma persona cotizando en dos comercializadoras del grupo, no duplicados. Fusionarlos habría filtrado el cliente de una empresa hacia otra — justo lo que la condición de aislamiento prohíbe. La llave de dedup es `(empresa_id, telefono_e164)`. |
| Duplicados reales (misma empresa) | 49 grupos, 98 leads → 49 personas | Se consolidan; el maestro es el registro con `fecha_registro` más antigua (coincide con la definición de `horas_al_primer_contacto` del histórico). |
| Variantes de ciudad/canal/estado | 38 / 9 / 10 variantes | Normalizadas a 11 / 3 / 6 valores canónicos. |
| Conversaciones fuera de canal WhatsApp | 289/677 (43%) pertenecen a leads con canal Meta Ads o Formulario Web | Se cargan todas: filtrar por canal habría perdido casi la mitad del material de enriquecimiento. |
| Conversaciones huérfanas | 12/677 referencian un `lead_id` inexistente | Se preservan (marcadas `huerfana=1`) para auditoría, no se descartan en silencio. |
| Conversaciones duplicadas | 25 `conversacion_id` repetidos, mismo `lead_id` | Se cargan ambas (id sufijado); se consolidan a nivel de lead en `extraccion_agregada.py`. |
| Filas exactamente duplicadas | 2 en `leads.csv` | Se descartan (`drop_duplicates`). |
| Fecha inválida | `2026-08-33` (día 33 inexistente) | Se detecta y se descarta sin tumbar el pipeline. |

Cada uno de estos hallazgos queda registrado con su conteo en la tabla
`calidad_datos` de cada corrida (expuesta en `/admin/calidad`) — la
evidencia de que se detectaron y resolvieron, no de que se asumieron en
silencio.

## 5. El score: cómo se prioriza y por qué

**Hallazgo que reencuadra el problema**: la variable más predictiva del
cierre en `historico_cierres.csv` no es un atributo del lead, es
`horas_al_primer_contacto` — una decisión de la empresa.

| Horas al primer contacto | Tasa de cierre | Lift |
|---|---|---|
| ≤ 1h | 15.1% | **1.69×** |
| 1–4h | 11.6% | 1.29× |
| 4–8h | 7.6% | 0.85× |
| 8–24h | 7.9% | 0.88× |
| 24–48h | 7.7% | 0.86× |
| 48–72h | 6.5% | 0.72× |
| > 72h | 4.7% | **0.52×** |

Contactar en la primera hora triplica la probabilidad de cierre frente a
esperar más de 72h. Ningún otro atributo del dataset se acerca a ese
margen (`pidió_cita` 1.22×, `manifestó_cuota_inicial` 1.22×; el canal es
casi plano, 0.90–1.06×). Por eso el score se descompone en dos factores
con roles distintos (`src/calibracion.py`, `src/scoring.py`):

```
score = score_calidad (aditivo, atributos del lead) × factor_urgencia (multiplicativo, horas sin contacto)
```

Los pesos **no se inventaron**: se derivaron del *lift* real
(`tasa_cierre_segmento / tasa_cierre_global`) sobre 2.021 leads históricos
*gestionados* (se excluyen 179 "Sin gestión": tienen
`numero_contactos = 0` por definición y contaminarían cualquier señal —
son precisamente el caso de negocio del proyecto, no un dato de
entrenamiento válido).

**Validación** (siguiendo la sugerencia explícita del enunciado: "el
histórico es la única fuente que le permite validar con datos si su
lógica realmente separa a los que cierran de los que no"): se reaplicó la
misma fórmula sobre el propio histórico. Resultado: **AUC = 0.63**, y el
**decil superior de score cierra 1.63× más** que el promedio. No es un
modelo perfecto — 5 señales débiles sobre un dataset sintético no dan para
más — pero la separación es real y medible, guardada en
`calibracion_meta` en cada corrida, no supuesta.

## 6. Decisiones y supuestos

| Decisión | Justificación |
|---|---|
| SQLite como motor relacional | Ver [`docs/arquitectura.md`](docs/arquitectura.md) — pipeline idempotente, disco efímero deja de ser un riesgo. |
| SDK de OpenAI apuntando a OpenRouter | Permite modelos gratuitos para pruebas y cambiar de proveedor solo con variables de entorno. |
| Extractor de reglas como respaldo | El pipeline nunca se cae por una API de IA caída o sin llave configurada. |
| Reglas ponderadas calibradas (no un modelo entrenado desde cero) | El enunciado no lo exige; sí exige criterio y validación — cumplidos con lift real y AUC medido. |
| `(empresa_id, telefono_e164)` como llave de dedup, nunca solo el teléfono | Evita fusionar clientes de dos comercializadoras distintas (ver hallazgo de datos, sección 4). |
| "Ahora" para el cálculo de urgencia = fecha más reciente del propio dataset, no el reloj real del sistema | Los datos son sintéticos y fijos (ago-sep 2026); anclar al reloj real habría hecho que el tablero se viera "vencido" cualquier día que se ejecutara la demo. |
| Los 15 puntos de venta pertenecen al grupo completo (3 empresas × 5 PV), no a una sola comercializadora | El enunciado dice que *Motos y Motores del Norte* tiene 15 PV en Antioquia/Bogotá/Costa, pero los datos muestran exactamente esa distribución repartida 1:1 entre las 3 empresas (`EMP-01`=Antioquia, `EMP-02`=Costa, `EMP-03`=Bogotá — confirmado cruzando ciudad × empresa). Se interpretó como los 15 PV del grupo completo. |
| Nombres de display de las empresas (`Motos y Motores del Norte S.A.S.`, etc.) | Los datos solo traen `empresa_id`; los nombres se infirieron de la geografía real de cada una y son puramente cosméticos — ninguna lógica de negocio depende de ellos, solo del `empresa_id`. |
| Umbrales de temperatura (Caliente ≥ 70, Tibio ≥ 40) | Valor de referencia razonable dado el rango de score observado (0–100); no se calibraron por decil contra el histórico por límite de tiempo (ver sección 7). |

## 7. Qué haría con más tiempo

- **Calibrar los umbrales de temperatura por decil del histórico** en vez
  de un corte fijo (70/40), igual que se hizo con los pesos del score.
- **Resolución de modelo más flexible**: el matching difuso actual
  (`src/catalogo.py`) rechaza correctamente texto ambiguo ("Honda" solo),
  pero también rechaza texto parcial inequívoco como "Best 125" (única
  línea con ese nombre en el catálogo) por quedar justo bajo el umbral de
  similitud. Un candidato adicional que compare contra `linea` sola, no
  solo `marca + linea`, resolvería esos casos sin abrir la puerta a falsos
  positivos.
- **Preferir el modelo mencionado en la conversación sobre el capturado
  por el canal** cuando ambos existen y difieren (hoy conviven sin
  reconciliar; la conversación suele ser la señal más reciente y
  específica).
- **Real-time**: hoy la "urgencia" se recalcula solo cuando corre el
  pipeline. Un disparador por evento genuino (webhook de un nuevo mensaje
  de WhatsApp) permitiría re-priorizar sin esperar a la corrida
  programada.
- **Multiempresa en el selector del tablero**: hoy es un dropdown de
  demostración explícitamente etiquetado como tal; en producción cada
  comercializadora tendría su propio subdominio/token fijo, no una
  elección visible en la página.
- **Feedback loop**: cuando un lead real se cierra o se pierde, alimentar
  ese desenlace de vuelta a `historico_cierres` para recalibrar
  periódicamente, en vez de calibrar una sola vez contra el histórico
  fijo entregado.
- **Rate limiting / cuota** en `POST /admin/pipeline/run` para que el
  webhook no se pueda disparar en bucle por error.

## 8. Estructura del repositorio

```
db/schema.sql          Esquema versionado (17 tablas, 1 vista)
src/
  config.py             Configuración desde .env
  normalizacion.py       Teléfono, fecha, ciudad, canal, estado (funciones puras)
  catalogo.py             Resolución de modelo de interés → SKU (fuzzy match)
  dedup.py                 Agrupación de leads duplicados
  db.py                     Conexión + reconstrucción idempotente del esquema
  ingesta.py                 Carga de leads/catálogo/asesores/histórico
  conversaciones.py           Carga de conversaciones.json
  extraccion_ia.py             LLM (OpenAI SDK/OpenRouter) + extractor de reglas
  extraccion_agregada.py        Consolida extracción a nivel de lead
  calibracion.py                 Deriva y valida los pesos del score
  scoring.py                      score = calidad × urgencia
  asignacion.py                    Cola diaria por asesor (round-robin por capacidad)
  pipeline.py                       Orquestador — python -m src.pipeline
api/
  auth.py                Aislamiento por empresa (token → empresa_id)
  main.py                 Endpoints públicos y de administración
tablero/app.py          Streamlit — "mis leads de hoy"
tests/                  46 pruebas (unitarias + integración contra la API real)
.github/workflows/      CI + disparo programado/por evento
run_server.py           Lanzador de despliegue (API interna + tablero público)
render.yaml, Procfile   Configuración de despliegue
datasets-assessment-analista-ia/   Insumos fuente (versionados, son sintéticos)
```

## 9. Variables de entorno

Ver [`.env.example`](.env.example), con comentarios en cada línea.
Ninguna credencial real está versionada en este repositorio.
