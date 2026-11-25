"""Conexión a PostgreSQL y migraciones SQL versionadas."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources

import psycopg

log = logging.getLogger(__name__)


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=False) as conn:
        yield conn


def migration_files() -> list[tuple[str, str]]:
    """Migraciones del paquete, ordenadas por nombre (``001_...``, ``002_...``)."""
    folder = resources.files("etl_chile") / "sql"
    files = sorted(
        (f.name, f.read_text(encoding="utf-8")) for f in folder.iterdir() if f.name.endswith(".sql")
    )
    return files


def migrate(conn: psycopg.Connection) -> list[str]:
    """Aplica las migraciones pendientes, cada una en su propia transacción."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.migraciones_aplicadas ("
            " nombre text PRIMARY KEY, aplicada_en timestamptz NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT nombre FROM public.migraciones_aplicadas")}
    applied = []
    for name, sql in migration_files():
        if name in done:
            continue
        with conn.transaction():
            conn.execute(sql)  # SQL de archivos propios del paquete
            conn.execute("INSERT INTO public.migraciones_aplicadas (nombre) VALUES (%s)", (name,))
        log.info("migración aplicada: %s", name)
        applied.append(name)
    return applied
