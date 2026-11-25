"""Extracción de sismos desde la API FDSN de USGS.

Documentación: https://earthquake.usgs.gov/fdsnws/event/1/
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import ValidationError

from etl_chile.config import BoundingBox
from etl_chile.http import get_json
from etl_chile.models import Sismo

log = logging.getLogger(__name__)

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
#: USGS rechaza consultas de más de 20.000 eventos; con ventanas de un mes
#: sobre Chile y magnitud 2.5 o más quedamos muy por debajo.
WINDOW = timedelta(days=31)


def _ms_to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def windows(
    start: datetime, end: datetime, size: timedelta = WINDOW
) -> Iterator[tuple[datetime, datetime]]:
    """Divide un rango en ventanas consecutivas, sin solaparse."""
    cursor = start
    while cursor < end:
        upper = min(cursor + size, end)
        yield cursor, upper
        cursor = upper


def fetch(
    client: httpx.Client,
    *,
    start: datetime,
    end: datetime,
    bbox: BoundingBox,
    min_magnitude: float,
    updated_after: datetime | None = None,
) -> list[dict[str, Any]]:
    """Devuelve las respuestas GeoJSON crudas de cada ventana de tiempo."""
    responses = []
    for lower, upper in windows(start, end):
        params: dict[str, Any] = {
            "format": "geojson",
            "starttime": lower.isoformat(timespec="seconds"),
            "endtime": upper.isoformat(timespec="seconds"),
            "minlatitude": bbox.min_lat,
            "maxlatitude": bbox.max_lat,
            "minlongitude": bbox.min_lon,
            "maxlongitude": bbox.max_lon,
            "minmagnitude": min_magnitude,
            "orderby": "time-asc",
        }
        if updated_after is not None:
            params["updatedafter"] = updated_after.isoformat(timespec="seconds")
        payload = get_json(client, URL, params)
        log.info(
            "usgs: %s eventos entre %s y %s",
            len(payload.get("features", [])),
            lower.date(),
            upper.date(),
        )
        responses.append(payload)
    return responses


def parse_feature(feature: dict[str, Any]) -> Sismo:
    """Convierte un ``Feature`` GeoJSON en un :class:`Sismo` validado."""
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None, None]
    lon, lat, depth = [*coords, None, None, None][:3]
    return Sismo(
        sismo_id=feature.get("id") or "",
        ocurrido_en=_ms_to_datetime(props["time"]),
        actualizado_en=_ms_to_datetime(props.get("updated") or props["time"]),
        magnitud=Decimal(str(props["mag"])),
        tipo_magnitud=props.get("magType"),
        profundidad_km=Decimal(str(depth)),
        latitud=Decimal(str(lat)),
        longitud=Decimal(str(lon)),
        lugar=props.get("place"),
        estado=props.get("status"),
        alerta_tsunami=bool(props.get("tsunami")),
        significancia=props.get("sig"),
        url=props.get("url"),
    )


def parse(payloads: list[dict[str, Any]]) -> tuple[list[Sismo], list[tuple[dict[str, Any], str]]]:
    """Valida todos los eventos. Devuelve (válidos, rechazados con su motivo).

    Si un evento aparece en dos ventanas, se queda la versión más reciente.
    """
    valid: dict[str, Sismo] = {}
    rejected: list[tuple[dict[str, Any], str]] = []
    for payload in payloads:
        for feature in payload.get("features", []):
            if (feature.get("properties") or {}).get("type", "earthquake") != "earthquake":
                continue  # explosiones de minería, canteras, etc.
            try:
                sismo = parse_feature(feature)
            except (ValidationError, KeyError, TypeError, ArithmeticError) as exc:
                rejected.append((feature, _short_error(exc)))
                continue
            current = valid.get(sismo.sismo_id)
            if current is None or sismo.actualizado_en > current.actualizado_en:
                valid[sismo.sismo_id] = sismo
    return list(valid.values()), rejected


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
    return f"{type(exc).__name__}: {exc}"


def date_to_utc(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)
