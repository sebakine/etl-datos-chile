"""Tests contra PostgreSQL real. En CI corren con un contenedor de servicio."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest
from typer.testing import CliRunner

from etl_chile import cli, db, pipeline, quality
from etl_chile.config import Settings
from tests.conftest import api_handler, load_fixture

pytestmark = pytest.mark.integracion

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


def one(conn: psycopg.Connection, sql: str) -> Any:
    row = conn.execute(sql).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return row[0]


def client_for(usgs: dict[str, Any] | None = None) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(api_handler(usgs)))


def test_migrate_is_idempotent(conn: psycopg.Connection) -> None:
    assert db.migrate(conn) == []
    assert one(conn, "SELECT count(*) FROM public.migraciones_aplicadas") == len(
        db.migration_files()
    )


def test_sismos_full_then_incremental(conn: psycopg.Connection, settings: Settings) -> None:
    first = pipeline.run_sismos(conn, client_for(), settings, desde=date(2026, 11, 1), now=NOW)
    assert first.estado == "ok", first.error
    assert (first.resultado.inserted, first.resultado.updated, first.rechazados) == (4, 0, 1)
    assert one(conn, "SELECT count(*) FROM warehouse.fact_sismo") == 4
    assert one(conn, "SELECT count(*) FROM etl.rechazos") == 1
    assert one(conn, "SELECT count(*) FROM raw.respuestas_api") == 1

    # Misma respuesta otra vez: nada cambia (idempotencia).
    again = pipeline.run_sismos(conn, client_for(), settings, now=NOW)
    assert (again.resultado.inserted, again.resultado.updated) == (0, 0)

    # USGS revisa la magnitud de un sismo: se actualiza solo ese.
    revised = load_fixture("usgs_sismos.json")
    revised["features"][0]["properties"]["mag"] = 4.4
    revised["features"][0]["properties"]["updated"] += 60_000
    third = pipeline.run_sismos(conn, client_for(revised), settings, now=NOW)
    assert (third.resultado.inserted, third.resultado.updated) == (0, 1)
    assert one(
        conn, "SELECT magnitud FROM warehouse.fact_sismo WHERE sismo_id = 'us7000tyyf'"
    ) == Decimal("4.40")


def test_star_schema_and_marts(conn: psycopg.Connection, settings: Settings) -> None:
    pipeline.run_sismos(conn, client_for(), settings, desde=date(2026, 11, 1), now=NOW)
    pipeline.run_indicadores(
        conn, client_for(), settings, desde=date(2025, 1, 1), today=date(2025, 1, 10)
    )

    row = conn.execute(
        """SELECT l.ciudad_referencia, l.pais, s.distancia_km, s.direccion, f.nombre_dia
           FROM warehouse.fact_sismo s
           JOIN warehouse.dim_lugar l USING (lugar_key)
           JOIN warehouse.dim_fecha f USING (fecha_key)
           WHERE s.sismo_id = 'us7000tyyf'"""
    ).fetchone()
    # 06:45 UTC del 18 de noviembre es la madrugada del mismo día en Chile (miércoles).
    assert row == ("Coquimbo", "Chile", 31, "S", "miércoles")

    assert one(conn, "SELECT count(*) FROM mart.sismos_por_ciudad WHERE pais = 'Argentina'") == 1
    assert one(conn, "SELECT sum(cantidad) FROM mart.sismos_mensuales") == 4
    dolar = conn.execute(
        "SELECT fecha, dolar, dolar_variacion_pct, uf FROM mart.indicadores_diarios ORDER BY fecha"
    ).fetchall()
    assert dolar[0] == (date(2025, 1, 2), Decimal("992.1200"), None, Decimal("38416.6900"))
    assert dolar[1][2] == Decimal("0.437")  # (996.46 / 992.12 - 1) * 100 = 0.4374
    assert one(conn, "SELECT count(*) FROM mart.resumen_diario WHERE sismos > 0") >= 1


def test_indicadores_incremental(conn: psycopg.Connection, settings: Settings) -> None:
    first = pipeline.run_indicadores(
        conn, client_for(), settings, today=date(2025, 1, 10), desde=date(2025, 1, 1)
    )
    assert first.estado == "ok", first.error
    assert (first.resultado.inserted, first.rechazados) == (5, 2)
    second = pipeline.run_indicadores(conn, client_for(), settings, today=date(2025, 1, 10))
    assert (second.resultado.inserted, second.resultado.updated) == (0, 0)
    assert one(
        conn, "SELECT valor::date FROM etl.marcas_de_agua WHERE fuente = 'mindicador'"
    ) == date(2025, 1, 6)


def test_one_failing_indicator_does_not_lose_the_others(
    conn: psycopg.Connection, settings: Settings
) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/uf/2025"):
            return httpx.Response(404)
        return api_handler()(request)

    with httpx.Client(transport=httpx.MockTransport(broken)) as client:
        summary = pipeline.run_indicadores(
            conn, client, settings, desde=date(2025, 1, 1), today=date(2025, 1, 10)
        )
    assert summary.estado == "parcial"
    assert "uf" in (summary.error or "")
    assert summary.resultado.inserted == 3  # el dólar sí quedó cargado
    assert one(conn, "SELECT count(*) FROM warehouse.fact_indicador_diario") == 3
    # La marca de agua no avanza, para reintentar la UF en la próxima corrida.
    assert one(conn, "SELECT count(*) FROM etl.marcas_de_agua WHERE fuente = 'mindicador'") == 0
    assert one(conn, "SELECT estado FROM etl.ejecuciones ORDER BY id DESC LIMIT 1") == "parcial"


def test_failed_source_rolls_back_and_is_logged(
    conn: psycopg.Connection, settings: Settings
) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400))) as client:
        sismos = pipeline.run_sismos(conn, client, settings, desde=date(2026, 11, 1), now=NOW)
        indicadores = pipeline.run_indicadores(
            conn, client, settings, desde=date(2025, 1, 1), today=date(2025, 1, 10)
        )
    assert sismos.estado == indicadores.estado == "error"
    assert sismos.resultado.inserted == 0
    assert one(conn, "SELECT count(*) FROM warehouse.fact_sismo") == 0
    assert one(conn, "SELECT count(*) FROM raw.respuestas_api") == 0
    assert one(conn, "SELECT count(*) FROM etl.ejecuciones WHERE estado = 'error'") == 2


def test_quality_checks(conn: psycopg.Connection, settings: Settings) -> None:
    pipeline.run_sismos(conn, client_for(), settings, desde=date(2026, 11, 1), now=NOW)
    with conn.transaction():
        results = {r.check.nombre: r for r in quality.run_checks(conn)}
    assert results["sismos_en_el_futuro"].ok
    assert results["sismos_sin_fecha_en_dimension"].ok
    conn.execute("UPDATE warehouse.fact_sismo SET latitud = 10 WHERE sismo_id = 'us7000tyyf'")
    results = {r.check.nombre: r for r in quality.run_checks(conn)}
    assert results["sismos_fuera_de_chile"].problemas == 1
    conn.rollback()


def test_cli_end_to_end(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ETL_INDICADORES", "dolar,uf")
    monkeypatch.setattr(cli, "make_client", lambda timeout: client_for())
    runner = CliRunner()
    assert runner.invoke(cli.app, ["migrar"]).exit_code == 0
    result = runner.invoke(cli.app, ["ejecutar", "--desde", "2025-01-01"])
    # El fixture de sismos es de noviembre de 2026; el control de frescura puede advertir,
    # pero ningún control crítico debe fallar.
    assert result.exit_code == 0, result.output
    assert "usgs_sismos" in result.output
    status = runner.invoke(cli.app, ["estado"])
    assert status.exit_code == 0, status.output
    assert "Sismos: 4" in status.output
    assert "etl-chile" in runner.invoke(cli.app, ["--version"]).output


def test_cli_partial_load_warns_on_github(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/uf/"):
            return httpx.Response(404)
        return api_handler()(request)

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ETL_INDICADORES", "dolar,uf")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(
        cli, "make_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(broken))
    )
    result = CliRunner().invoke(
        cli.app, ["ejecutar", "--fuente", "indicadores", "--desde", "2025-01-01"]
    )
    assert result.exit_code == 0, result.output
    assert "::warning title=Carga parcial de mindicador::" in result.output


def test_cli_fails_when_a_source_fails(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setattr(
        cli,
        "make_client",
        lambda timeout: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400))),
    )
    result = CliRunner().invoke(cli.app, ["ejecutar", "--fuente", "sismos"])
    assert result.exit_code == 1
