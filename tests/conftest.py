from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from etl_chile import db
from etl_chile.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def usgs_payload() -> dict[str, Any]:
    return load_fixture("usgs_sismos.json")


Handler = Callable[[httpx.Request], httpx.Response]


def api_handler(usgs: dict[str, Any] | None = None) -> Handler:
    """Simula USGS y mindicador.cl con respuestas reales guardadas en fixtures."""
    usgs = usgs if usgs is not None else load_fixture("usgs_sismos.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "earthquake.usgs.gov":
            return httpx.Response(200, json=usgs)
        if request.url.host == "mindicador.cl":
            _, _, codigo, year = request.url.path.split("/")
            fixture = FIXTURES / f"mindicador_{codigo}_{year}.json"
            if fixture.exists():
                return httpx.Response(200, json=load_fixture(fixture.name))
            return httpx.Response(
                200,
                json={"codigo": codigo, "nombre": codigo, "unidad_medida": "Pesos", "serie": []},
            )
        return httpx.Response(404)

    return handler


@pytest.fixture
def mock_client() -> Iterator[httpx.Client]:
    with httpx.Client(transport=httpx.MockTransport(api_handler())) as client:
        yield client


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=os.environ.get("TEST_DATABASE_URL", ""), indicators=("dolar", "uf")
    )


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    """Base de datos limpia y migrada. Necesita TEST_DATABASE_URL."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("define TEST_DATABASE_URL para correr los tests de integración")
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(
            "DROP SCHEMA IF EXISTS raw, etl, warehouse, mart CASCADE;"
            "DROP TABLE IF EXISTS public.migraciones_aplicadas"
        )
    with db.connect(url) as connection:
        db.migrate(connection)
        yield connection


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los reintentos no esperan de verdad en los tests."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)
