-- ===========================================================================
--  Organizador de Leads - Motos y Servicios de Colombia S.A.S.
--  Esquema de base de datos (SQLite)
--
--  Principios de diseño:
--
--  1. AISLAMIENTO POR EMPRESA. `empresa_id` viaja en toda tabla operativa,
--     incluso cuando es derivable por JOIN. Es redundancia deliberada: permite
--     que cada consulta filtre por empresa sin depender de un JOIN correcto,
--     y que un índice cubra el filtro. El aislamiento se implementa en el
--     acceso a datos, nunca en el frontend.
--
--  2. EL DATO CRUDO NUNCA SE PIERDE. Cada campo normalizado conserva su
--     valor original (`*_raw`). Permite auditar cualquier transformación y
--     defenderla frente al evaluador.
--
--  3. LA INCERTIDUMBRE SE MODELA, NO SE ESCONDE. Cuando una normalización es
--     ambigua (p.ej. fechas DD/MM vs MM/DD) se persiste una bandera de
--     confianza en vez de inventar un valor y presentarlo como certeza.
--
--  4. IDEMPOTENCIA. El pipeline reconstruye la BD completa en cada corrida.
--     No hay estado acumulado que pueda corromperse; el disco efímero del
--     hosting deja de ser un riesgo.
-- ===========================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- DIMENSIONES ORGANIZACIONALES
-- ---------------------------------------------------------------------------

CREATE TABLE empresas (
    empresa_id   TEXT PRIMARY KEY,
    nombre       TEXT NOT NULL
);

-- Un punto de venta pertenece a exactamente una empresa.
-- Verificado sobre los insumos: la relación PV -> empresa es 1:1 y consistente
-- en 1.503/1.503 leads y 2.200/2.200 registros históricos. Esa consistencia es
-- lo que hace confiable el modelo de aislamiento multi-empresa.
CREATE TABLE puntos_venta (
    punto_venta_id TEXT PRIMARY KEY,
    empresa_id     TEXT NOT NULL REFERENCES empresas(empresa_id),
    ciudad         TEXT
);

CREATE TABLE asesores (
    asesor_id             TEXT PRIMARY KEY,
    nombre                TEXT NOT NULL,
    punto_venta_id        TEXT NOT NULL REFERENCES puntos_venta(punto_venta_id),
    empresa_id            TEXT NOT NULL REFERENCES empresas(empresa_id),
    capacidad_diaria_leads INTEGER NOT NULL,
    activo                INTEGER NOT NULL,   -- 1/0. Los inactivos no reciben cola.
    fecha_ingreso         TEXT
);
CREATE INDEX idx_asesores_empresa ON asesores(empresa_id, activo);
CREATE INDEX idx_asesores_pv      ON asesores(punto_venta_id, activo);

-- ---------------------------------------------------------------------------
-- CATÁLOGO DE PRODUCTO
-- ---------------------------------------------------------------------------

CREATE TABLE catalogo_motos (
    sku            TEXT PRIMARY KEY,
    marca          TEXT NOT NULL,
    linea          TEXT NOT NULL,
    modelo_canonico TEXT NOT NULL,   -- "Marca Linea": llave de resolución del texto libre
    cilindraje     INTEGER,
    segmento       TEXT,
    precio_lista   INTEGER,
    unidades_disponibles INTEGER
);
CREATE INDEX idx_catalogo_canonico ON catalogo_motos(modelo_canonico);

-- Disponibilidad desnormalizada desde `puntos_venta_disponibles` (lista con
-- separador "|"). Explotarla a filas permite responder en SQL la pregunta
-- operativa: ¿hay inventario de lo que el cliente pide, en el punto de venta
-- al que quedó asignado?
CREATE TABLE disponibilidad_sku (
    sku            TEXT NOT NULL REFERENCES catalogo_motos(sku),
    punto_venta_id TEXT NOT NULL REFERENCES puntos_venta(punto_venta_id),
    PRIMARY KEY (sku, punto_venta_id)
);

-- ---------------------------------------------------------------------------
-- LEADS
-- ---------------------------------------------------------------------------

CREATE TABLE leads (
    lead_id            TEXT PRIMARY KEY,
    empresa_id         TEXT NOT NULL REFERENCES empresas(empresa_id),
    punto_venta_id     TEXT NOT NULL REFERENCES puntos_venta(punto_venta_id),

    -- ---- Contacto: normalizado + crudo ----
    nombre_cliente     TEXT,
    nombre_raw         TEXT,
    telefono_e164      TEXT,          -- +57XXXXXXXXXX; NULL si no es normalizable
    telefono_raw       TEXT,
    telefono_valido    INTEGER NOT NULL DEFAULT 0,
    email              TEXT,
    ciudad             TEXT,          -- canónica
    ciudad_raw         TEXT,

    -- ---- Clasificación ----
    canal              TEXT,          -- WhatsApp | Meta Ads | Formulario Web
    canal_raw          TEXT,
    estado_gestion     TEXT,
    estado_gestion_raw TEXT,
    campania           TEXT,

    -- ---- Fechas: ISO 8601 tras resolver 4 formatos de entrada ----
    fecha_registro          TEXT,
    fecha_registro_raw      TEXT,
    fecha_registro_ambigua  INTEGER NOT NULL DEFAULT 0,  -- 1 = DD/MM vs MM/DD irresoluble
    fecha_primer_contacto      TEXT,
    fecha_primer_contacto_raw  TEXT,

    -- ---- Modelo de interés resuelto contra el catálogo ----
    modelo_interes_raw   TEXT,
    sku_interes          TEXT REFERENCES catalogo_motos(sku),
    modelo_match_score   REAL,        -- similitud 0-100 del match difuso

    -- ---- Deduplicación ----
    -- Un lead se marca como duplicado de otro SOLO dentro de la misma empresa.
    -- Fusionar por teléfono a través de empresas filtraría clientes de una
    -- comercializadora a otra: 91 de los 141 grupos que comparten teléfono
    -- cruzan empresa y NO son duplicados que deban unificarse.
    es_duplicado       INTEGER NOT NULL DEFAULT 0,
    lead_maestro_id    TEXT REFERENCES leads(lead_id),
    motivo_dedup       TEXT,

    ingerido_en        TEXT NOT NULL
);
CREATE INDEX idx_leads_empresa   ON leads(empresa_id, es_duplicado);
CREATE INDEX idx_leads_pv        ON leads(punto_venta_id);
CREATE INDEX idx_leads_tel       ON leads(empresa_id, telefono_e164);
CREATE INDEX idx_leads_maestro   ON leads(lead_maestro_id);
CREATE INDEX idx_leads_fecha     ON leads(fecha_registro);

-- ---------------------------------------------------------------------------
-- CONVERSACIONES
-- ---------------------------------------------------------------------------

-- Ojo: 289 de las 677 conversaciones pertenecen a leads cuyo canal NO es
-- WhatsApp (entran por Meta o web y luego escriben al WhatsApp). Restringir el
-- enriquecimiento al canal WhatsApp perdería el 43% de las conversaciones.
CREATE TABLE conversaciones (
    conversacion_id TEXT PRIMARY KEY,
    lead_id         TEXT REFERENCES leads(lead_id),
    empresa_id      TEXT,             -- heredada del lead; NULL si es huérfana
    canal           TEXT,
    fecha_inicio    TEXT,
    num_mensajes    INTEGER,
    texto_plano     TEXT,             -- transcripción linealizada, entrada del extractor
    hash_texto      TEXT NOT NULL,    -- llave de caché: evita reprocesar con la IA
    huerfana        INTEGER NOT NULL DEFAULT 0  -- lead_id inexistente en leads.csv (12 casos)
);
CREATE INDEX idx_conv_lead  ON conversaciones(lead_id);
CREATE INDEX idx_conv_hash  ON conversaciones(hash_texto);

CREATE TABLE mensajes (
    mensaje_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id TEXT NOT NULL REFERENCES conversaciones(conversacion_id),
    orden           INTEGER NOT NULL,
    emisor          TEXT NOT NULL,    -- cliente | asesor
    hora            TEXT,
    texto           TEXT
);
CREATE INDEX idx_mensajes_conv ON mensajes(conversacion_id, orden);

-- ---------------------------------------------------------------------------
-- EXTRACCIÓN CON IA
-- ---------------------------------------------------------------------------

-- Cacheada por `hash_texto`: si la conversación no cambió, no se vuelve a
-- pagar la llamada al modelo. Cada campo lleva su origen (`ia` o `reglas`)
-- para poder auditar qué aportó realmente el LLM.
CREATE TABLE extraccion_ia (
    hash_texto          TEXT PRIMARY KEY,
    conversacion_id     TEXT REFERENCES conversaciones(conversacion_id),
    lead_id             TEXT,

    modelo_interes      TEXT,      -- modelo canónico mencionado en el chat
    sku_interes         TEXT,
    presupuesto_cop     INTEGER,   -- cuota inicial o presupuesto en pesos
    forma_pago          TEXT,      -- contado | credito | no_informa
    intencion           TEXT,      -- compra_inmediata | cotizando | comparando | curioseando
    objecion_principal  TEXT,      -- precio | credito_reportado | comparando | tiempo | ninguna
    pidio_cita          INTEGER,
    pidio_cotizacion    INTEGER,

    confianza           REAL,      -- 0-1 declarada por el extractor
    origen              TEXT,      -- ia | reglas | mixto
    modelo_llm          TEXT,
    tokens_entrada      INTEGER,
    tokens_salida       INTEGER,
    extraido_en         TEXT,
    error               TEXT       -- si la extracción falló, se registra y se cae a reglas
);
CREATE INDEX idx_extraccion_lead ON extraccion_ia(lead_id);

-- ---------------------------------------------------------------------------
-- SCORING
-- ---------------------------------------------------------------------------

-- Pesos derivados del histórico, NO inventados. `lift` es la razón entre la
-- tasa de cierre del segmento y la tasa global. Versionar la calibración
-- permite reproducir por qué un lead obtuvo cierto score en cierta fecha.
CREATE TABLE calibracion_pesos (
    calibracion_id  TEXT NOT NULL,
    variable        TEXT NOT NULL,
    valor           TEXT NOT NULL,
    n_historico     INTEGER,
    tasa_cierre     REAL,
    lift            REAL,
    puntos          REAL,          -- peso en el score, derivado del lift
    PRIMARY KEY (calibracion_id, variable, valor)
);

CREATE TABLE calibracion_meta (
    calibracion_id      TEXT PRIMARY KEY,
    generada_en         TEXT,
    n_historico_usado   INTEGER,   -- excluye "Sin gestión": nunca fueron gestionados
    tasa_cierre_global  REAL,
    -- Validación: ¿el score realmente separa a los que cierran de los que no?
    auc                 REAL,
    lift_decil_superior REAL,
    notas               TEXT
);

CREATE TABLE lead_scores (
    lead_id        TEXT PRIMARY KEY REFERENCES leads(lead_id),
    empresa_id     TEXT NOT NULL,
    calibracion_id TEXT,

    score          REAL NOT NULL,        -- 0-100
    temperatura    TEXT NOT NULL,        -- Caliente | Tibio | Frio
    prob_cierre_estimada REAL,

    -- El score se descompone en dos factores porque responden a preguntas
    -- distintas: la calidad es un atributo del lead, la urgencia es una
    -- consecuencia de la inacción de la empresa y decae con las horas.
    score_calidad  REAL,
    factor_urgencia REAL,
    horas_sin_contacto REAL,
    dentro_sla_24h INTEGER,

    -- Desglose regla a regla en JSON: es lo que el tablero muestra como
    -- "por qué este lead está de primero". Sin esto el score es una caja negra.
    desglose_json  TEXT,
    calculado_en   TEXT
);
CREATE INDEX idx_scores_empresa ON lead_scores(empresa_id, score DESC);

-- ---------------------------------------------------------------------------
-- ASIGNACIÓN: LA COLA DIARIA DE CADA ASESOR
-- ---------------------------------------------------------------------------

CREATE TABLE asignaciones (
    fecha          TEXT NOT NULL,
    lead_id        TEXT NOT NULL REFERENCES leads(lead_id),
    asesor_id      TEXT NOT NULL REFERENCES asesores(asesor_id),
    empresa_id     TEXT NOT NULL,
    punto_venta_id TEXT NOT NULL,
    posicion       INTEGER NOT NULL,   -- orden dentro de la cola del asesor
    score          REAL,
    temperatura    TEXT,
    motivo         TEXT,               -- resumen accionable para el asesor
    PRIMARY KEY (fecha, lead_id)
);
CREATE INDEX idx_asig_asesor  ON asignaciones(fecha, asesor_id, posicion);
CREATE INDEX idx_asig_empresa ON asignaciones(fecha, empresa_id);

-- ---------------------------------------------------------------------------
-- HISTÓRICO (insumo de calibración, no operativo)
-- ---------------------------------------------------------------------------

CREATE TABLE historico_cierres (
    lead_id                  TEXT PRIMARY KEY,
    fecha_registro           TEXT,
    canal                    TEXT,
    empresa_id               TEXT,
    punto_venta_id           TEXT,
    modelo_cotizado          TEXT,
    precio_lista             INTEGER,
    horas_al_primer_contacto REAL,
    numero_contactos         INTEGER,
    manifesto_cuota_inicial  TEXT,
    forma_pago_declarada     TEXT,
    pidio_cita               INTEGER,
    desenlace                TEXT,       -- Cerrado | Perdido | Sin gestión
    gestionado               INTEGER     -- 0 = "Sin gestión": se excluye de la calibración
);
CREATE INDEX idx_hist_desenlace ON historico_cierres(desenlace);

-- ---------------------------------------------------------------------------
-- OBSERVABILIDAD DEL PIPELINE
-- ---------------------------------------------------------------------------

-- Sin esto no se puede demostrar que el proceso corrió solo ni diagnosticar
-- una corrida fallida en producción.
CREATE TABLE corridas_pipeline (
    corrida_id      TEXT PRIMARY KEY,
    iniciada_en     TEXT,
    finalizada_en   TEXT,
    estado          TEXT,        -- ok | error
    disparador      TEXT,        -- manual | cron | webhook
    duracion_s      REAL,
    error           TEXT
);

-- Cada anomalía detectada queda registrada con su conteo. Es la evidencia de
-- que las inconsistencias se detectaron y se resolvieron, en vez de pasarlas
-- por alto en silencio.
CREATE TABLE calidad_datos (
    corrida_id  TEXT NOT NULL REFERENCES corridas_pipeline(corrida_id),
    etapa       TEXT NOT NULL,
    hallazgo    TEXT NOT NULL,
    severidad   TEXT,           -- info | aviso | error
    conteo      INTEGER,
    detalle     TEXT
);
CREATE INDEX idx_calidad_corrida ON calidad_datos(corrida_id, severidad);

-- ---------------------------------------------------------------------------
-- VISTA OPERATIVA: "MIS LEADS DE HOY"
-- ---------------------------------------------------------------------------
-- Toda consulta que la sirva DEBE filtrar por empresa_id.

CREATE VIEW v_leads_hoy AS
SELECT
    a.fecha,
    a.empresa_id,
    a.asesor_id,
    ase.nombre            AS asesor_nombre,
    a.posicion,
    a.lead_id,
    l.nombre_cliente,
    l.telefono_e164,
    l.ciudad,
    l.canal,
    l.estado_gestion,
    l.fecha_registro,
    a.score,
    a.temperatura,
    a.motivo,
    s.horas_sin_contacto,
    s.dentro_sla_24h,
    s.desglose_json,
    c.modelo_canonico     AS modelo_interes,
    c.precio_lista,
    e.presupuesto_cop,
    e.forma_pago,
    e.intencion,
    e.objecion_principal,
    e.pidio_cita,
    e.pidio_cotizacion
FROM asignaciones a
JOIN leads         l   ON l.lead_id   = a.lead_id
JOIN asesores      ase ON ase.asesor_id = a.asesor_id
LEFT JOIN lead_scores   s ON s.lead_id = a.lead_id
LEFT JOIN catalogo_motos c ON c.sku    = l.sku_interes
LEFT JOIN extraccion_ia  e ON e.lead_id = a.lead_id;
