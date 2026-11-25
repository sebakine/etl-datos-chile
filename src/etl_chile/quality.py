"""Controles de calidad que corren después de cada carga.

Un control ``critico`` que falla marca la ejecución como error (y el CLI
termina con código distinto de cero). Uno de ``advertencia`` solo se informa.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import psycopg

Severity = Literal["critico", "advertencia"]


@dataclass(frozen=True, slots=True)
class Check:
    nombre: str
    severidad: Severity
    sql: str  # devuelve una fila por problema encontrado
    descripcion: str


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: Check
    problemas: int
    ejemplos: list[tuple[object, ...]]

    @property
    def ok(self) -> bool:
        return self.problemas == 0


CHECKS: tuple[Check, ...] = (
    Check(
        "sismos_sin_fecha_en_dimension",
        "critico",
        """SELECT s.sismo_id FROM warehouse.fact_sismo s
           LEFT JOIN warehouse.dim_fecha f USING (fecha_key) WHERE f.fecha IS NULL""",
        "Todo sismo debe apuntar a una fecha existente.",
    ),
    Check(
        "sismos_en_el_futuro",
        "critico",
        """SELECT sismo_id, ocurrido_en FROM warehouse.fact_sismo
           WHERE ocurrido_en > now() + interval '1 hour'""",
        "No puede haber sismos con fecha futura.",
    ),
    Check(
        "sismos_fuera_de_chile",
        "advertencia",
        """SELECT sismo_id, latitud, longitud FROM warehouse.fact_sismo
           WHERE latitud NOT BETWEEN -56 AND -17.5 OR longitud NOT BETWEEN -76 AND -66""",
        "Las coordenadas deberían estar dentro del rectángulo de consulta.",
    ),
    Check(
        "dolar_variacion_extrema",
        "advertencia",
        """SELECT fecha, dolar, dolar_variacion_pct FROM mart.indicadores_diarios
           WHERE abs(dolar_variacion_pct) > 5""",
        "Una variación diaria del dólar sobre 5% suele ser un error de la fuente.",
    ),
    Check(
        "uf_fuera_de_rango",
        "critico",
        "SELECT fecha, uf FROM mart.indicadores_diarios WHERE uf NOT BETWEEN 10000 AND 100000",
        "La UF debe estar en un rango plausible.",
    ),
    Check(
        "dolar_desactualizado",
        "advertencia",
        """SELECT 'dolar', max(fecha) FROM mart.indicadores_diarios WHERE dolar IS NOT NULL
           HAVING max(fecha) < current_date - 5""",
        "El dólar se publica cada día hábil; más de 5 días sin valor indica un problema.",
    ),
    Check(
        "datos_desactualizados",
        "advertencia",
        """SELECT 'sismos', max(ocurrido_en) FROM warehouse.fact_sismo
           HAVING max(ocurrido_en) < now() - interval '7 days'""",
        "Chile tiene sismos de magnitud 2.5 o más casi a diario; 7 días sin datos es sospechoso.",
    ),
)


def run_checks(conn: psycopg.Connection, checks: tuple[Check, ...] = CHECKS) -> list[CheckResult]:
    results = []
    for check in checks:
        rows = conn.execute(check.sql).fetchall()
        results.append(CheckResult(check, len(rows), [tuple(r) for r in rows[:5]]))
    return results
