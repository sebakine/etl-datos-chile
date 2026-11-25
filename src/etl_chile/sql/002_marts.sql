-- Vistas de consumo. Se recrean completas en cada migración que las cambie.

CREATE OR REPLACE VIEW mart.sismos_mensuales AS
SELECT
    f.anio,
    f.mes,
    make_date(f.anio, f.mes, 1)                                   AS mes_inicio,
    count(*)                                                      AS cantidad,
    count(*) FILTER (WHERE s.magnitud >= 5)                       AS cantidad_m5_o_mas,
    max(s.magnitud)                                               AS magnitud_maxima,
    round(avg(s.magnitud), 2)                                     AS magnitud_promedio,
    round(avg(s.profundidad_km), 1)                               AS profundidad_promedio_km
FROM warehouse.fact_sismo s
JOIN warehouse.dim_fecha f USING (fecha_key)
GROUP BY f.anio, f.mes;

CREATE OR REPLACE VIEW mart.sismos_por_ciudad AS
SELECT
    l.ciudad_referencia,
    l.pais,
    count(*)                  AS cantidad,
    max(s.magnitud)           AS magnitud_maxima,
    round(avg(s.magnitud), 2) AS magnitud_promedio,
    max(s.ocurrido_en)        AS ultimo_sismo
FROM warehouse.fact_sismo s
JOIN warehouse.dim_lugar l USING (lugar_key)
GROUP BY l.ciudad_referencia, l.pais;

-- Indicadores en formato ancho, con variación diaria calculada con LAG.
-- Los indicadores mensuales (UTM) se arrastran al resto de los días del mes.
CREATE OR REPLACE VIEW mart.indicadores_diarios AS
WITH ancho AS (
    SELECT
        f.fecha,
        max(v.valor) FILTER (WHERE i.codigo = 'dolar')       AS dolar,
        max(v.valor) FILTER (WHERE i.codigo = 'euro')        AS euro,
        max(v.valor) FILTER (WHERE i.codigo = 'uf')          AS uf,
        max(v.valor) FILTER (WHERE i.codigo = 'utm')         AS utm,
        max(v.valor) FILTER (WHERE i.codigo = 'libra_cobre') AS libra_cobre
    FROM warehouse.fact_indicador_diario v
    JOIN warehouse.dim_indicador i USING (indicador_key)
    JOIN warehouse.dim_fecha f USING (fecha_key)
    GROUP BY f.fecha
)
SELECT
    fecha,
    dolar,
    round(100 * (dolar / lag(dolar) OVER w - 1), 3) AS dolar_variacion_pct,
    euro,
    uf,
    coalesce(utm, (SELECT v2.valor
                   FROM warehouse.fact_indicador_diario v2
                   JOIN warehouse.dim_indicador i2 USING (indicador_key)
                   JOIN warehouse.dim_fecha f2 USING (fecha_key)
                   WHERE i2.codigo = 'utm' AND f2.fecha <= ancho.fecha
                   ORDER BY f2.fecha DESC LIMIT 1))   AS utm,
    libra_cobre
FROM ancho
WINDOW w AS (ORDER BY fecha);

-- Un día por fila, con sismos e indicadores juntos: la tabla que lee el dashboard.
CREATE OR REPLACE VIEW mart.resumen_diario AS
SELECT
    f.fecha,
    f.nombre_dia,
    f.es_fin_de_semana,
    count(s.sismo_id)                AS sismos,
    max(s.magnitud)                  AS magnitud_maxima,
    i.dolar,
    i.dolar_variacion_pct,
    i.uf
FROM warehouse.dim_fecha f
LEFT JOIN warehouse.fact_sismo s USING (fecha_key)
LEFT JOIN mart.indicadores_diarios i ON i.fecha = f.fecha
WHERE f.fecha <= current_date
GROUP BY f.fecha, f.nombre_dia, f.es_fin_de_semana, i.dolar, i.dolar_variacion_pct, i.uf;
