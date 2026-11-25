"""Orquestación de cada fuente: extraer, validar, cargar y registrar la ejecución."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg

from etl_chile import load
from etl_chile.config import Settings
from etl_chile.sources import mindicador, usgs

log = logging.getLogger(__name__)

FUENTE_SISMOS = "usgs_sismos"
FUENTE_INDICADORES = "mindicador"


@dataclass(slots=True)
class RunSummary:
    fuente: str
    ejecucion_id: int
    extraidos: int = 0
    resultado: load.LoadResult = field(default_factory=load.LoadResult)
    rechazados: int = 0
    estado: str = "ok"
    error: str | None = None


def get_watermark(conn: psycopg.Connection, fuente: str) -> datetime | None:
    row = conn.execute(
        "SELECT valor FROM etl.marcas_de_agua WHERE fuente = %s", (fuente,)
    ).fetchone()
    return row[0] if row else None


def set_watermark(conn: psycopg.Connection, fuente: str, value: datetime) -> None:
    conn.execute(
        """
        INSERT INTO etl.marcas_de_agua (fuente, valor) VALUES (%s, %s)
        ON CONFLICT (fuente) DO UPDATE
            SET valor = greatest(etl.marcas_de_agua.valor, excluded.valor), actualizado_en = now()
        """,
        (fuente, value),
    )


def _start_run(conn: psycopg.Connection, fuente: str) -> int:
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO etl.ejecuciones (fuente) VALUES (%s) RETURNING id", (fuente,)
        ).fetchone()
    if row is None:  # pragma: no cover - RETURNING siempre devuelve la fila
        raise RuntimeError("no se pudo registrar la ejecución")
    return int(row[0])


def _finish_run(conn: psycopg.Connection, summary: RunSummary) -> None:
    with conn.transaction():
        conn.execute(
            """
            UPDATE etl.ejecuciones SET fin = now(), estado = %s, extraidos = %s, insertados = %s,
                   actualizados = %s, rechazados = %s, error = %s
            WHERE id = %s
            """,
            (
                summary.estado, summary.extraidos, summary.resultado.inserted,
                summary.resultado.updated, summary.rechazados, summary.error, summary.ejecucion_id,
            ),
        )  # fmt: skip


def _tracked(
    conn: psycopg.Connection, fuente: str, body: Callable[[RunSummary], None]
) -> RunSummary:
    """Ejecuta ``body`` dentro de una transacción y deja registro en etl.ejecuciones.

    Si algo falla, la transacción de datos se revierte completa (nada a medias
    en el warehouse) y la ejecución queda marcada como error con el motivo.
    """
    summary = RunSummary(fuente, _start_run(conn, fuente))
    try:
        with conn.transaction():
            body(summary)
    except Exception as exc:
        summary.estado = "error"
        summary.error = f"{type(exc).__name__}: {exc}"
        log.exception("falló la carga de %s", fuente)
    _finish_run(conn, summary)
    return summary


def run_sismos(
    conn: psycopg.Connection,
    client: httpx.Client,
    settings: Settings,
    *,
    desde: date | None = None,
    now: datetime | None = None,
) -> RunSummary:
    """Carga sismos. Sin ``desde`` es incremental según la marca de agua."""
    now = now or datetime.now(UTC)

    def body(summary: RunSummary) -> None:
        watermark = get_watermark(conn, FUENTE_SISMOS)
        updated_after = None
        if desde is not None:
            start = usgs.date_to_utc(desde)
        elif watermark is None:
            start = usgs.date_to_utc(settings.start_date)
        else:
            start = max(
                usgs.date_to_utc(settings.start_date),
                watermark - timedelta(days=settings.usgs_lookback_days),
            )
            updated_after = watermark - timedelta(hours=settings.usgs_overlap_hours)
        payloads = usgs.fetch(
            client,
            start=start,
            end=now,
            bbox=settings.bbox,
            min_magnitude=settings.min_magnitude,
            updated_after=updated_after,
        )
        for payload in payloads:
            load.save_raw(conn, FUENTE_SISMOS, payload.get("metadata", {}).get("url", ""), payload)
        sismos, rejected = usgs.parse(payloads)
        summary.extraidos = len(sismos) + len(rejected)
        summary.rechazados = len(rejected)
        load.save_rejections(conn, summary.ejecucion_id, FUENTE_SISMOS, rejected)
        summary.resultado = load.load_sismos(conn, sismos)
        if sismos:
            set_watermark(conn, FUENTE_SISMOS, max(s.actualizado_en for s in sismos))

    return _tracked(conn, FUENTE_SISMOS, body)


def run_indicadores(
    conn: psycopg.Connection,
    client: httpx.Client,
    settings: Settings,
    *,
    desde: date | None = None,
    today: date | None = None,
) -> RunSummary:
    """Carga indicadores por año. Sin ``desde`` parte desde el año de la marca de agua."""
    today = today or datetime.now(mindicador.SANTIAGO).date()

    def body(summary: RunSummary) -> None:
        watermark = get_watermark(conn, FUENTE_INDICADORES)
        if desde is not None:
            first_year = desde.year
        elif watermark is not None:
            first_year = watermark.astimezone(mindicador.SANTIAGO).year
        else:
            first_year = settings.start_date.year
        latest: date | None = None
        for codigo in settings.indicators:
            for year in range(first_year, today.year + 1):
                payload = mindicador.fetch_year(client, codigo, year)
                load.save_raw(conn, FUENTE_INDICADORES, f"{codigo}/{year}", payload)
                indicador, values, rejected = mindicador.parse(payload)
                if desde is not None:
                    values = [v for v in values if v.fecha >= desde]
                summary.extraidos += len(values) + len(rejected)
                summary.rechazados += len(rejected)
                load.save_rejections(conn, summary.ejecucion_id, FUENTE_INDICADORES, rejected)
                summary.resultado += load.load_indicador(conn, indicador, values)
                if values:
                    latest = max(latest or values[-1].fecha, values[-1].fecha)
        if latest is not None:
            set_watermark(
                conn,
                FUENTE_INDICADORES,
                datetime(latest.year, latest.month, latest.day, tzinfo=mindicador.SANTIAGO),
            )

    return _tracked(conn, FUENTE_INDICADORES, body)
