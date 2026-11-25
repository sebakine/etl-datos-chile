-- Capas del warehouse:
--   raw        respuestas de las APIs tal como llegaron (auditoría y reproceso)
--   etl        control del pipeline: marcas de agua, ejecuciones y rechazos
--   warehouse  modelo estrella: dimensiones y hechos
--   mart       vistas listas para consumir en dashboards

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS etl;
CREATE SCHEMA IF NOT EXISTS warehouse;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE TABLE raw.respuestas_api (
    id           bigserial PRIMARY KEY,
    fuente       text        NOT NULL,
    solicitud    text        NOT NULL,
    payload      jsonb       NOT NULL,
    ingerido_en  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON raw.respuestas_api (fuente, ingerido_en);

CREATE TABLE etl.marcas_de_agua (
    fuente         text PRIMARY KEY,
    valor          timestamptz NOT NULL,
    actualizado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE etl.ejecuciones (
    id            bigserial PRIMARY KEY,
    fuente        text        NOT NULL,
    inicio        timestamptz NOT NULL DEFAULT now(),
    fin           timestamptz,
    estado        text        NOT NULL DEFAULT 'en_curso'
                  CHECK (estado IN ('en_curso', 'ok', 'error')),
    extraidos     integer     NOT NULL DEFAULT 0,
    insertados    integer     NOT NULL DEFAULT 0,
    actualizados  integer     NOT NULL DEFAULT 0,
    rechazados    integer     NOT NULL DEFAULT 0,
    error         text
);

CREATE TABLE etl.rechazos (
    id            bigserial PRIMARY KEY,
    ejecucion_id  bigint      NOT NULL REFERENCES etl.ejecuciones (id),
    fuente        text        NOT NULL,
    registro      jsonb       NOT NULL,
    motivo        text        NOT NULL,
    creado_en     timestamptz NOT NULL DEFAULT now()
);

-- Dimensiones ---------------------------------------------------------------

CREATE TABLE warehouse.dim_fecha (
    fecha_key         integer PRIMARY KEY,          -- AAAAMMDD
    fecha             date     NOT NULL UNIQUE,
    anio              smallint NOT NULL,
    trimestre         smallint NOT NULL,
    mes               smallint NOT NULL,
    nombre_mes        text     NOT NULL,
    dia               smallint NOT NULL,
    dia_semana        smallint NOT NULL,            -- 1 = lunes
    nombre_dia        text     NOT NULL,
    es_fin_de_semana  boolean  NOT NULL
);

CREATE TABLE warehouse.dim_lugar (
    lugar_key          serial PRIMARY KEY,
    ciudad_referencia  text NOT NULL,
    pais               text NOT NULL,
    UNIQUE (ciudad_referencia, pais)
);

CREATE TABLE warehouse.dim_indicador (
    indicador_key  serial PRIMARY KEY,
    codigo         text NOT NULL UNIQUE,
    nombre         text NOT NULL,
    unidad_medida  text NOT NULL
);

-- Hechos --------------------------------------------------------------------

CREATE TABLE warehouse.fact_sismo (
    sismo_id         text PRIMARY KEY,
    fecha_key        integer       NOT NULL REFERENCES warehouse.dim_fecha (fecha_key),
    lugar_key        integer       NOT NULL REFERENCES warehouse.dim_lugar (lugar_key),
    ocurrido_en      timestamptz   NOT NULL,
    magnitud         numeric(4, 2) NOT NULL,
    tipo_magnitud    text,
    profundidad_km   numeric(7, 2) NOT NULL,
    latitud          numeric(8, 4) NOT NULL,
    longitud         numeric(8, 4) NOT NULL,
    distancia_km     integer,
    direccion        text,
    estado           text,
    alerta_tsunami   boolean       NOT NULL,
    significancia    integer,
    url              text,
    actualizado_en   timestamptz   NOT NULL,
    cargado_en       timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX ON warehouse.fact_sismo (fecha_key);
CREATE INDEX ON warehouse.fact_sismo (lugar_key);

CREATE TABLE warehouse.fact_indicador_diario (
    indicador_key  integer        NOT NULL REFERENCES warehouse.dim_indicador (indicador_key),
    fecha_key      integer        NOT NULL REFERENCES warehouse.dim_fecha (fecha_key),
    valor          numeric(14, 4) NOT NULL CHECK (valor > 0),
    cargado_en     timestamptz    NOT NULL DEFAULT now(),
    PRIMARY KEY (indicador_key, fecha_key)
);
CREATE INDEX ON warehouse.fact_indicador_diario (fecha_key);

-- Llena dim_fecha para un rango; idempotente.
CREATE FUNCTION warehouse.asegurar_fechas(desde date, hasta date) RETURNS void
LANGUAGE sql AS $$
    INSERT INTO warehouse.dim_fecha
    SELECT
        to_char(d, 'YYYYMMDD')::integer,
        d,
        extract(year FROM d),
        extract(quarter FROM d),
        extract(month FROM d),
        (ARRAY['enero','febrero','marzo','abril','mayo','junio','julio','agosto',
               'septiembre','octubre','noviembre','diciembre'])[extract(month FROM d)],
        extract(day FROM d),
        extract(isodow FROM d),
        (ARRAY['lunes','martes','miércoles','jueves','viernes','sábado','domingo'])[extract(isodow FROM d)],
        extract(isodow FROM d) IN (6, 7)
    FROM generate_series(desde, hasta, interval '1 day') AS g(d)
    ON CONFLICT (fecha_key) DO NOTHING;
$$;
