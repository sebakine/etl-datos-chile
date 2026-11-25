# Cambios

## [0.1.0] - 2026-11-25

### Agregado

- Extracción de sismos (USGS) e indicadores económicos (mindicador.cl) con reintentos.
- Validación con pydantic y cuarentena de registros inválidos en `etl.rechazos`.
- Modelo estrella en PostgreSQL con carga incremental e idempotente.
- Vistas de consumo en el esquema `mart` y controles de calidad después de cada carga.
- CLI `etl-chile` con los comandos `migrar`, `ejecutar` y `estado`.
- Dashboard en Streamlit, Docker Compose y ejecución diaria con GitHub Actions.
