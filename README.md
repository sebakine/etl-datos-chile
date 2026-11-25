# etl-datos-chile

[![CI](https://github.com/sebakine/etl-datos-chile/actions/workflows/ci.yml/badge.svg)](https://github.com/sebakine/etl-datos-chile/actions/workflows/ci.yml)
[![ETL diario](https://github.com/sebakine/etl-datos-chile/actions/workflows/etl-diario.yml/badge.svg)](https://github.com/sebakine/etl-datos-chile/actions/workflows/etl-diario.yml)
![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791)
[![Licencia: MIT](https://img.shields.io/badge/licencia-MIT-lightgrey)](LICENSE)

Pipeline ETL que extrae datos públicos de Chile todos los días, los valida y los carga en un **modelo estrella en PostgreSQL**, listo para análisis y dashboards.

- **Sismos** de magnitud 2,5 o más en Chile y sus alrededores, desde la [API de USGS](https://earthquake.usgs.gov/fdsnws/event/1/).
- **Indicadores económicos** (dólar, euro, UF, UTM y cobre) desde [mindicador.cl](https://mindicador.cl).

La carga es **incremental e idempotente**: cada corrida pide solo lo nuevo o lo revisado, y correrla dos veces no duplica ni cambia nada. Un workflow de GitHub Actions la ejecuta cada mañana contra las APIs reales.

<img src="docs/dashboard.png" alt="Dashboard con totales, sismos por mes, mapa de epicentros y ciudades con más sismos" width="800">

*Dashboard incluido, con datos reales de sismos de enero de 2025 a noviembre de 2026.*

## Arquitectura

```mermaid
flowchart LR
    subgraph Fuentes
        U[API USGS<br/>sismos GeoJSON]
        M[API mindicador.cl<br/>series por año]
    end
    subgraph Pipeline Python
        E[Extracción<br/>httpx + reintentos]
        V[Validación<br/>pydantic]
        S[Staging<br/>COPY a tablas temporales]
    end
    subgraph PostgreSQL
        R[(raw<br/>respuestas JSON)]
        Q[(etl<br/>ejecuciones, rechazos,<br/>marcas de agua)]
        W[(warehouse<br/>modelo estrella)]
        K[(mart<br/>vistas de consumo)]
    end
    D[Dashboard<br/>Streamlit]

    U --> E
    M --> E
    E --> R
    E --> V
    V -- válidos --> S --> W --> K --> D
    V -- inválidos --> Q
```

### Modelo de datos

```mermaid
erDiagram
    dim_fecha ||--o{ fact_sismo : fecha_key
    dim_lugar ||--o{ fact_sismo : lugar_key
    dim_fecha ||--o{ fact_indicador_diario : fecha_key
    dim_indicador ||--o{ fact_indicador_diario : indicador_key

    fact_sismo {
        text sismo_id PK
        int fecha_key FK
        int lugar_key FK
        timestamptz ocurrido_en
        numeric magnitud
        numeric profundidad_km
        numeric latitud
        numeric longitud
        timestamptz actualizado_en
    }
    fact_indicador_diario {
        int indicador_key PK
        int fecha_key PK
        numeric valor
    }
    dim_fecha {
        int fecha_key PK
        date fecha
        smallint anio
        smallint mes
        text nombre_dia
        bool es_fin_de_semana
    }
    dim_lugar {
        int lugar_key PK
        text ciudad_referencia
        text pais
    }
    dim_indicador {
        int indicador_key PK
        text codigo
        text nombre
        text unidad_medida
    }
```

Vistas en el esquema `mart`: `sismos_mensuales`, `sismos_por_ciudad`, `indicadores_diarios` (formato ancho con la variación diaria del dólar calculada con `LAG`) y `resumen_diario`.

## Cómo funciona

| Etapa | Qué hace | Por qué |
| --- | --- | --- |
| Extracción | Pide los sismos en ventanas de un mes y los indicadores por año, con reintentos exponenciales ante errores de red, 429 y 5xx | Las APIs públicas fallan; USGS limita a 20.000 eventos por consulta |
| Aterrizaje | Guarda cada respuesta completa en `raw.respuestas_api` (JSONB) | Permite auditar y reprocesar sin volver a llamar a la API |
| Validación | Cada registro pasa por un modelo `pydantic` (rangos, fechas futuras, zona horaria) | Un registro malo va a `etl.rechazos` con el motivo y no detiene el pipeline |
| Carga | `COPY` a tablas temporales y luego `INSERT ... ON CONFLICT DO UPDATE ... WHERE` | Idempotente; solo actualiza si USGS revisó el sismo o si cambió un valor |
| Incremental | Marca de agua por fuente en `etl.marcas_de_agua`; USGS se consulta con `updatedafter` | No se vuelve a descargar todo el historial cada día |
| Transacción | Toda la carga de una fuente es una transacción; si falla, se revierte y la ejecución queda registrada como error | Nunca quedan datos a medias en el warehouse |
| Calidad | Controles SQL después de cada carga (`critico` o `advertencia`) | Un control crítico fallido hace que el proceso termine con error y el workflow quede en rojo |

Algunos detalles que no son obvios:

- mindicador.cl entrega las fechas en UTC (`2025-01-02T03:00:00Z`), que corresponden a la medianoche **en Chile** del 2 de enero. El pipeline convierte a `America/Santiago` antes de tomar el día; si no, todos los valores quedarían desplazados un día.
- USGS revisa magnitudes y ubicaciones después de publicar un sismo. Por eso cada corrida vuelve a pedir los últimos 90 días filtrando por fecha de actualización, y el `UPSERT` solo pisa la fila si la versión nueva es más reciente.
- El rectángulo de consulta incluye zonas fronterizas de Argentina y Bolivia; la dimensión de lugar conserva el país para poder filtrarlas.

## Uso

### Con Docker (todo en un comando)

```bash
docker compose up --build
```

Levanta PostgreSQL, corre el pipeline desde el 1 de enero de 2025 y abre el dashboard en http://localhost:8501.

### Local

Requiere Python 3.12 o superior, [uv](https://docs.astral.sh/uv/) y un PostgreSQL.

```bash
uv sync --all-extras
export DATABASE_URL=postgresql://usuario:clave@localhost:5432/etl

uv run etl-chile migrar                       # crea los esquemas y tablas
uv run etl-chile ejecutar                     # carga incremental de ambas fuentes
uv run etl-chile ejecutar --fuente sismos --desde 2020-01-01   # recarga histórica
uv run etl-chile estado                       # últimas ejecuciones y totales
uv run streamlit run dashboard/app.py         # dashboard
```

Configuración por variables de entorno (ver `.env.example`): `DATABASE_URL`, `ETL_FECHA_INICIO`, `ETL_MAGNITUD_MINIMA` y `ETL_INDICADORES`.

### Consultas de ejemplo

```sql
-- Meses con más sismos de magnitud 5 o más
SELECT mes_inicio, cantidad_m5_o_mas, magnitud_maxima
FROM mart.sismos_mensuales ORDER BY cantidad_m5_o_mas DESC LIMIT 5;

-- Días en que el dólar se movió más de 1%
SELECT fecha, dolar, dolar_variacion_pct
FROM mart.indicadores_diarios WHERE abs(dolar_variacion_pct) > 1 ORDER BY fecha DESC;

-- ¿Qué se rechazó y por qué?
SELECT fuente, motivo, count(*) FROM etl.rechazos GROUP BY 1, 2 ORDER BY 3 DESC;
```

## Ejecución diaria

El workflow [`etl-diario.yml`](.github/workflows/etl-diario.yml) corre todos los días a las 08:30 de Chile contra las APIs reales. El resumen de cada corrida (filas cargadas, controles de calidad, últimos datos) queda en la pestaña *Actions* del repositorio.

- Sin configurar nada, carga los últimos 30 días en un PostgreSQL temporal: es una prueba diaria de punta a punta.
- Para conservar el historial, crea una base gratuita (por ejemplo en Neon o Supabase) y guarda su URL como secreto `DATABASE_URL` del repositorio. Desde ahí la carga es incremental sobre esa base.

## Tests

```bash
uv run pytest                                          # unitarios
TEST_DATABASE_URL=postgresql://... uv run pytest       # + integración con PostgreSQL real
```

- **Unitarios**: interpretación de las respuestas reales de las APIs (guardadas en `tests/fixtures`), reintentos HTTP, validación y el parser de ubicaciones.
- **Integración**: corren contra PostgreSQL de verdad y verifican la carga completa e incremental, la idempotencia, la actualización de un sismo revisado, el rollback cuando una fuente falla, las vistas del mart, los controles de calidad y la CLI de punta a punta.

En CI los tests de integración usan un contenedor de PostgreSQL 16. Cobertura actual: 97%.

## Estructura

```text
src/etl_chile/
├── sources/usgs.py         extracción y parseo de sismos
├── sources/mindicador.py   extracción y parseo de indicadores
├── http.py                 cliente HTTP con reintentos
├── models.py               validación con pydantic
├── places.py               "31 km S of Coquimbo, Chile" → ciudad, país, distancia
├── load.py                 staging + upsert idempotente
├── pipeline.py             orquestación, marcas de agua y registro de ejecuciones
├── quality.py              controles de calidad
├── db.py                   conexión y migraciones versionadas
├── cli.py                  comandos migrar, ejecutar y estado
└── sql/                    001_esquema.sql, 002_marts.sql
dashboard/app.py            Streamlit
```

## Próximos pasos

Este proyecto es la base del siguiente en mi portafolio: orquestar este mismo pipeline con Airflow y mover las transformaciones a dbt.

## Licencia

[MIT](LICENSE) © Sebastián Muñoz
