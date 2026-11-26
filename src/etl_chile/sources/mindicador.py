"""Extracción de indicadores económicos desde https://mindicador.cl.

La API publica series por año: ``/api/{codigo}/{año}``. Cada punto trae la
fecha en UTC (por ejemplo ``2025-01-02T03:00:00.000Z``), que corresponde a la
medianoche en Chile; por eso se convierte a la zona ``America/Santiago``
antes de quedarse con el día.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from etl_chile.http import get_json
from etl_chile.models import Indicador, ValorIndicador

log = logging.getLogger(__name__)

BASE_URL = "https://mindicador.cl/api"
SANTIAGO = ZoneInfo("America/Santiago")


def _error_en_cuerpo(payload: Any) -> str | None:
    """mindicador.cl a veces responde 200 con ``{"error": ..., "message": ...}``."""
    if not isinstance(payload, dict):
        return "respuesta sin formato JSON de objeto"
    if "error" in payload:
        return str(payload.get("message") or payload["error"])
    if "codigo" not in payload:
        return "respuesta sin el campo 'codigo'"
    return None


def fetch_year(
    client: httpx.Client, codigo: str, year: int, *, max_wait: float = 30.0
) -> dict[str, Any]:
    payload: dict[str, Any] = get_json(
        client, f"{BASE_URL}/{codigo}/{year}", max_wait=max_wait, error_en_cuerpo=_error_en_cuerpo
    )
    log.info("mindicador: %s %s con %s valores", codigo, year, len(payload.get("serie", [])))
    return payload


def parse(
    payload: dict[str, Any],
) -> tuple[Indicador, list[ValorIndicador], list[tuple[dict[str, Any], str]]]:
    codigo = payload["codigo"]
    indicador = Indicador(
        codigo=codigo,
        nombre=payload.get("nombre") or codigo,
        unidad_medida=payload.get("unidad_medida") or "",
    )
    values: dict[Any, ValorIndicador] = {}
    rejected: list[tuple[dict[str, Any], str]] = []
    for point in payload.get("serie", []):
        try:
            moment = datetime.fromisoformat(point["fecha"].replace("Z", "+00:00"))
            value = ValorIndicador(
                codigo=codigo,
                fecha=moment.astimezone(SANTIAGO).date(),
                valor=Decimal(str(point["valor"])),
            )
        except (ValidationError, KeyError, ValueError, TypeError, ArithmeticError) as exc:
            rejected.append(({"codigo": codigo, **point}, f"{type(exc).__name__}: {exc}"))
            continue
        values[value.fecha] = value
    return indicador, sorted(values.values(), key=lambda v: v.fecha), rejected
