# Cambios

## [Sin publicar]

### Cambiado

- Cada indicador se carga en su propio savepoint: si mindicador.cl falla para uno, los demás se guardan igual.
- Nuevo estado `parcial` en `etl.ejecuciones` (migración 003); en GitHub Actions se muestra como advertencia sin fallar la corrida.
- Si una fuente falla por completo, el resumen ya no informa filas que se revirtieron.

### Agregado

- Control `dolar_desactualizado` (advertencia) cuando el dólar lleva más de 5 días sin datos.

## [0.1.0] - 2026-11-25

### Agregado

- Extracción de sismos (USGS) e indicadores económicos (mindicador.cl) con reintentos.
- Validación con pydantic y cuarentena de registros inválidos en `etl.rechazos`.
- Modelo estrella en PostgreSQL con carga incremental e idempotente.
- Vistas de consumo en el esquema `mart` y controles de calidad después de cada carga.
- CLI `etl-chile` con los comandos `migrar`, `ejecutar` y `estado`.
- Dashboard en Streamlit, Docker Compose y ejecución diaria con GitHub Actions.
