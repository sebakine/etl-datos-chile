"""Configuración leída desde variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date

DEFAULT_INDICATORS = ("dolar", "euro", "uf", "utm", "libra_cobre")


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Rectángulo geográfico que cubre Chile continental y su costa."""

    min_lat: float = -56.0
    max_lat: float = -17.5
    min_lon: float = -76.0
    max_lon: float = -66.0


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL", "postgresql://etl:etl@localhost:5432/etl"
        )
    )
    start_date: date = field(
        default_factory=lambda: date.fromisoformat(os.environ.get("ETL_FECHA_INICIO", "2024-01-01"))
    )
    min_magnitude: float = field(
        default_factory=lambda: float(os.environ.get("ETL_MAGNITUD_MINIMA", "2.5"))
    )
    indicators: tuple[str, ...] = field(
        default_factory=lambda: _env_list("ETL_INDICADORES", DEFAULT_INDICATORS)
    )
    bbox: BoundingBox = field(default_factory=BoundingBox)
    http_timeout: float = field(
        default_factory=lambda: float(os.environ.get("ETL_HTTP_TIMEOUT", "60"))
    )
    #: Días hacia atrás que se vuelven a pedir en cada corrida incremental, para
    #: recoger sismos que USGS revisó después de publicarlos.
    usgs_lookback_days: int = 90
    #: Margen de solapamiento sobre la marca de agua, por si hay relojes desfasados.
    usgs_overlap_hours: int = 6
