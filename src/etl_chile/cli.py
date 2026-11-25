"""CLI del pipeline: ``etl-chile migrar | ejecutar | estado``."""

from __future__ import annotations

import logging
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from etl_chile import __version__, db, pipeline, quality
from etl_chile.config import Settings
from etl_chile.http import make_client

app = typer.Typer(
    help="Pipeline ETL de sismos (USGS) e indicadores económicos (mindicador.cl) hacia PostgreSQL.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


class Fuente(StrEnum):
    TODAS = "todas"
    SISMOS = "sismos"
    INDICADORES = "indicadores"


def _version(value: bool) -> None:
    if value:
        console.print(f"etl-chile {__version__}")
        raise typer.Exit


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Más detalle en los logs.")
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Muestra la versión."),
    ] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=Console(stderr=True), show_path=False)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.command()
def migrar() -> None:
    """Crea o actualiza las tablas del warehouse."""
    with db.connect(Settings().database_url) as conn:
        applied = db.migrate(conn)
    console.print(
        f"[green]✓[/] {len(applied)} migraciones aplicadas"
        if applied
        else "Sin migraciones pendientes"
    )


@app.command()
def ejecutar(
    fuente: Annotated[Fuente, typer.Option(help="Qué fuente cargar.")] = Fuente.TODAS,
    desde: Annotated[
        datetime | None,
        typer.Option(
            formats=["%Y-%m-%d"],
            help="Recarga desde esta fecha (AAAA-MM-DD) en vez de ser incremental.",
        ),
    ] = None,
) -> None:
    """Extrae, valida y carga los datos; luego corre los controles de calidad."""
    settings = Settings()
    since: date | None = desde.date() if desde else None
    summaries = []
    with db.connect(settings.database_url) as conn, make_client(settings.http_timeout) as client:
        db.migrate(conn)
        if fuente in (Fuente.TODAS, Fuente.SISMOS):
            summaries.append(pipeline.run_sismos(conn, client, settings, desde=since))
        if fuente in (Fuente.TODAS, Fuente.INDICADORES):
            summaries.append(pipeline.run_indicadores(conn, client, settings, desde=since))
        with conn.transaction():
            results = quality.run_checks(conn)

    table = Table(title="Ejecución", title_justify="left")
    for col in ("Fuente", "Estado", "Extraídos", "Insertados", "Actualizados", "Rechazados"):
        table.add_column(col, justify="left" if col in {"Fuente", "Estado"} else "right")
    for s in summaries:
        estado = "[green]ok[/]" if s.estado == "ok" else f"[red]error[/] {s.error}"
        table.add_row(
            s.fuente, estado, str(s.extraidos), str(s.resultado.inserted),
            str(s.resultado.updated), str(s.rechazados),
        )  # fmt: skip
    console.print(table)
    _print_checks(results)

    failed_critical = [r for r in results if not r.ok and r.check.severidad == "critico"]
    if any(s.estado != "ok" for s in summaries) or failed_critical:
        raise typer.Exit(1)


def _print_checks(results: list[quality.CheckResult]) -> None:
    table = Table(title="Controles de calidad", title_justify="left")
    table.add_column("Control")
    table.add_column("Severidad")
    table.add_column("Resultado")
    for r in results:
        mark = (
            "[green]ok[/]"
            if r.ok
            else ("[red]" if r.check.severidad == "critico" else "[yellow]")
            + f"{r.problemas} problemas[/]"
        )
        table.add_row(r.check.nombre, r.check.severidad, mark)
    console.print(table)


@app.command()
def estado() -> None:
    """Muestra las últimas ejecuciones y cuántos datos hay en el warehouse."""
    with db.connect(Settings().database_url) as conn:
        runs = conn.execute(
            """SELECT id, fuente, inicio, estado, extraidos, insertados, actualizados, rechazados
               FROM etl.ejecuciones ORDER BY id DESC LIMIT 10"""
        ).fetchall()
        counts = conn.execute(
            """SELECT (SELECT count(*) FROM warehouse.fact_sismo),
                      (SELECT max(ocurrido_en) FROM warehouse.fact_sismo),
                      (SELECT count(*) FROM warehouse.fact_indicador_diario),
                      (SELECT max(fecha) FROM mart.indicadores_diarios)"""
        ).fetchone()
    table = Table(title="Últimas ejecuciones", title_justify="left")
    for col in ("ID", "Fuente", "Inicio", "Estado", "Extraídos", "Ins.", "Act.", "Rech."):
        table.add_column(col)
    for row in runs:
        table.add_row(*[f"{v:%Y-%m-%d %H:%M}" if isinstance(v, datetime) else str(v) for v in row])
    console.print(table)
    if counts:
        console.print(
            f"Sismos: {counts[0]} (último {counts[1]:%Y-%m-%d %H:%M} UTC) · "
            f"Valores de indicadores: {counts[2]} (último {counts[3]})"
            if counts[1]
            else "El warehouse está vacío. Corre `etl-chile ejecutar`."
        )


if __name__ == "__main__":  # pragma: no cover
    app()
