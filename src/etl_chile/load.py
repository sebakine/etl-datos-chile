"""Carga al warehouse con tablas de staging y ``INSERT ... ON CONFLICT``.

Cada carga es idempotente: correrla dos veces con los mismos datos no
duplica filas ni cambia nada. Se usa ``RETURNING (xmax = 0)`` para distinguir
filas insertadas de filas actualizadas sin consultas extra.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from etl_chile.models import Indicador, Sismo, ValorIndicador
from etl_chile.places import parse_place

SANTIAGO = ZoneInfo("America/Santiago")


@dataclass(frozen=True, slots=True)
class LoadResult:
    inserted: int = 0
    updated: int = 0

    def __add__(self, other: LoadResult) -> LoadResult:
        return LoadResult(self.inserted + other.inserted, self.updated + other.updated)


def _count(rows: Iterable[tuple[bool]]) -> LoadResult:
    flags = [r[0] for r in rows]
    return LoadResult(inserted=sum(flags), updated=len(flags) - sum(flags))


def save_raw(conn: psycopg.Connection, fuente: str, solicitud: str, payload: Any) -> None:
    conn.execute(
        "INSERT INTO raw.respuestas_api (fuente, solicitud, payload) VALUES (%s, %s, %s)",
        (fuente, solicitud, Jsonb(payload)),
    )


def save_rejections(
    conn: psycopg.Connection,
    run_id: int,
    fuente: str,
    rejected: Sequence[tuple[dict[str, Any], str]],
) -> None:
    if not rejected:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO etl.rechazos (ejecucion_id, fuente, registro, motivo)"
            " VALUES (%s, %s, %s, %s)",
            [(run_id, fuente, Jsonb(record), reason) for record, reason in rejected],
        )


def load_sismos(conn: psycopg.Connection, sismos: Sequence[Sismo]) -> LoadResult:
    if not sismos:
        return LoadResult()
    local_dates = [s.ocurrido_en.astimezone(SANTIAGO).date() for s in sismos]
    conn.execute("SELECT warehouse.asegurar_fechas(%s, %s)", (min(local_dates), max(local_dates)))
    conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS stg_sismo (
            sismo_id text, fecha date, ocurrido_en timestamptz, actualizado_en timestamptz,
            magnitud numeric, tipo_magnitud text, profundidad_km numeric, latitud numeric,
            longitud numeric, ciudad text, pais text, distancia_km integer, direccion text,
            estado text, alerta_tsunami boolean, significancia integer, url text
        ) ON COMMIT DROP
        """
    )
    conn.execute("TRUNCATE stg_sismo")
    with conn.cursor() as cur, cur.copy("COPY stg_sismo FROM STDIN") as copy:
        for sismo, local_date in zip(sismos, local_dates, strict=True):
            place = parse_place(sismo.lugar)
            copy.write_row(
                (
                    sismo.sismo_id, local_date, sismo.ocurrido_en, sismo.actualizado_en,
                    sismo.magnitud, sismo.tipo_magnitud, sismo.profundidad_km, sismo.latitud,
                    sismo.longitud, place.ciudad_referencia, place.pais, place.distancia_km,
                    place.direccion, sismo.estado, sismo.alerta_tsunami, sismo.significancia,
                    sismo.url,
                )
            )  # fmt: skip
    conn.execute(
        """
        INSERT INTO warehouse.dim_lugar (ciudad_referencia, pais)
        SELECT DISTINCT ciudad, pais FROM stg_sismo
        ON CONFLICT (ciudad_referencia, pais) DO NOTHING
        """
    )
    rows = conn.execute(
        """
        INSERT INTO warehouse.fact_sismo AS f (
            sismo_id, fecha_key, lugar_key, ocurrido_en, magnitud, tipo_magnitud,
            profundidad_km, latitud, longitud, distancia_km, direccion, estado,
            alerta_tsunami, significancia, url, actualizado_en
        )
        SELECT s.sismo_id, to_char(s.fecha, 'YYYYMMDD')::integer, l.lugar_key, s.ocurrido_en,
               s.magnitud, s.tipo_magnitud, s.profundidad_km, s.latitud, s.longitud,
               s.distancia_km, s.direccion, s.estado, s.alerta_tsunami, s.significancia,
               s.url, s.actualizado_en
        FROM stg_sismo s
        JOIN warehouse.dim_lugar l ON l.ciudad_referencia = s.ciudad AND l.pais = s.pais
        ON CONFLICT (sismo_id) DO UPDATE SET
            fecha_key = excluded.fecha_key,
            lugar_key = excluded.lugar_key,
            ocurrido_en = excluded.ocurrido_en,
            magnitud = excluded.magnitud,
            tipo_magnitud = excluded.tipo_magnitud,
            profundidad_km = excluded.profundidad_km,
            latitud = excluded.latitud,
            longitud = excluded.longitud,
            distancia_km = excluded.distancia_km,
            direccion = excluded.direccion,
            estado = excluded.estado,
            alerta_tsunami = excluded.alerta_tsunami,
            significancia = excluded.significancia,
            url = excluded.url,
            actualizado_en = excluded.actualizado_en,
            cargado_en = now()
        WHERE f.actualizado_en < excluded.actualizado_en
        RETURNING (xmax = 0)
        """
    ).fetchall()
    return _count(rows)


def load_indicador(
    conn: psycopg.Connection, indicador: Indicador, values: Sequence[ValorIndicador]
) -> LoadResult:
    row = conn.execute(
        """
        INSERT INTO warehouse.dim_indicador (codigo, nombre, unidad_medida)
        VALUES (%s, %s, %s)
        ON CONFLICT (codigo) DO UPDATE
            SET nombre = excluded.nombre, unidad_medida = excluded.unidad_medida
        RETURNING indicador_key
        """,
        (indicador.codigo, indicador.nombre, indicador.unidad_medida),
    ).fetchone()
    if row is None:  # pragma: no cover - RETURNING siempre devuelve la fila
        raise RuntimeError("no se pudo registrar el indicador")
    key = row[0]
    if not values:
        return LoadResult()
    conn.execute("SELECT warehouse.asegurar_fechas(%s, %s)", (values[0].fecha, values[-1].fecha))
    # Una corrida carga varios indicadores en la misma transacción: la tabla de
    # staging se crea una vez y se vacía antes de cada indicador.
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS stg_indicador (fecha date, valor numeric) ON COMMIT DROP"
    )
    conn.execute("TRUNCATE stg_indicador")
    with conn.cursor() as cur, cur.copy("COPY stg_indicador FROM STDIN") as copy:
        for value in values:
            copy.write_row((value.fecha, value.valor))
    rows = conn.execute(
        """
        INSERT INTO warehouse.fact_indicador_diario AS f (indicador_key, fecha_key, valor)
        SELECT %s, to_char(fecha, 'YYYYMMDD')::integer, valor FROM stg_indicador
        ON CONFLICT (indicador_key, fecha_key) DO UPDATE
            SET valor = excluded.valor, cargado_en = now()
        WHERE f.valor IS DISTINCT FROM excluded.valor
        RETURNING (xmax = 0)
        """,
        (key,),
    ).fetchall()
    return _count(rows)
