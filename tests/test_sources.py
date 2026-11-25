from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from etl_chile.config import BoundingBox
from etl_chile.http import get_json, make_client
from etl_chile.sources import mindicador, usgs
from tests.conftest import load_fixture


class TestUsgs:
    def test_parse_valid_rejected_and_skipped(self, usgs_payload: dict[str, Any]) -> None:
        sismos, rejected = usgs.parse([usgs_payload])
        assert [s.sismo_id for s in sismos] == [
            "us7000tyyf",
            "us7000tz33",
            "us6000uc1x",
            "us6000uc5s",
        ]
        # La explosión de cantera se ignora; el evento sin magnitud se rechaza.
        assert len(rejected) == 1
        assert rejected[0][0]["id"] == "us_sinmag"

    def test_parse_fields(self, usgs_payload: dict[str, Any]) -> None:
        sismo = usgs.parse([usgs_payload])[0][0]
        assert sismo.magnitud == Decimal("4.1")
        assert sismo.latitud == Decimal("-30.2387")
        assert sismo.profundidad_km == Decimal("65.327")
        assert sismo.ocurrido_en == datetime(2026, 11, 18, 6, 45, 49, 875000, tzinfo=UTC)
        assert sismo.lugar == "31 km S of Coquimbo, Chile"

    def test_keeps_latest_version_of_duplicated_event(self, usgs_payload: dict[str, Any]) -> None:
        newer = load_fixture("usgs_sismos.json")
        newer["features"][0]["properties"]["updated"] += 1000
        newer["features"][0]["properties"]["mag"] = 4.3
        sismos, _ = usgs.parse([usgs_payload, newer])
        assert next(s for s in sismos if s.sismo_id == "us7000tyyf").magnitud == Decimal("4.3")

    def test_rejects_future_events(self, usgs_payload: dict[str, Any]) -> None:
        future = datetime.now(UTC) + timedelta(days=2)
        usgs_payload["features"][0]["properties"]["time"] = int(future.timestamp() * 1000)
        _, rejected = usgs.parse([usgs_payload])
        assert any("futuro" in reason for _, reason in rejected)

    def test_windows_cover_range_without_overlap(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 3, 15, tzinfo=UTC)
        parts = list(usgs.windows(start, end))
        assert parts[0][0] == start
        assert parts[-1][1] == end
        assert all(a[1] == b[0] for a, b in itertools.pairwise(parts))

    def test_fetch_sends_expected_params(self, usgs_payload: dict[str, Any]) -> None:
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json=usgs_payload)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            usgs.fetch(
                client,
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 1, 10, tzinfo=UTC),
                bbox=BoundingBox(),
                min_magnitude=2.5,
                updated_after=datetime(2025, 1, 5, tzinfo=UTC),
            )
        params = seen[0].params
        assert params["format"] == "geojson"
        assert params["minmagnitude"] == "2.5"
        assert params["updatedafter"] == "2025-01-05T00:00:00+00:00"
        assert params["minlatitude"] == "-56.0"


class TestMindicador:
    def test_parse_dedupes_sorts_and_uses_chile_dates(self) -> None:
        indicador, values, rejected = mindicador.parse(load_fixture("mindicador_dolar_2025.json"))
        assert indicador.nombre == "Dólar observado"
        # "2025-01-02T03:00:00Z" es la medianoche del 2 de enero en Chile, no el 1.
        assert [v.fecha for v in values] == [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
        assert values[0].valor == Decimal("992.12")
        assert len(rejected) == 2  # fecha inválida y valor negativo

    def test_fetch_year(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/uf/2025"
            return httpx.Response(200, json=load_fixture("mindicador_uf_2025.json"))

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            assert mindicador.fetch_year(client, "uf", 2025)["codigo"] == "uf"


class TestHttp:
    def test_retries_server_errors_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        with make_client(transport=httpx.MockTransport(handler)) as client:
            assert get_json(client, "https://x.test/", max_wait=0.01) == {"ok": True}
        assert calls["n"] == 3

    def test_retries_network_errors(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("caído")
            return httpx.Response(200, json=[])

        with make_client(transport=httpx.MockTransport(handler)) as client:
            assert get_json(client, "https://x.test/", max_wait=0.01) == []

    def test_does_not_retry_client_errors(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        with (
            make_client(transport=httpx.MockTransport(handler)) as client,
            pytest.raises(httpx.HTTPStatusError),
        ):
            get_json(client, "https://x.test/", max_wait=0.01)
        assert calls["n"] == 1

    def test_gives_up_after_attempts(self) -> None:
        with (
            make_client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client,
            pytest.raises(httpx.HTTPStatusError),
        ):
            get_json(client, "https://x.test/", attempts=2, max_wait=0.01)

    def test_sends_user_agent(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["user-agent"].startswith("etl-datos-chile/")
            return httpx.Response(200, json={})

        with make_client(transport=httpx.MockTransport(handler)) as client:
            get_json(client, "https://x.test/")
