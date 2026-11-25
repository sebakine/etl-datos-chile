"""Interpretación del campo ``place`` de USGS.

USGS describe la ubicación como ``"31 km S of Coquimbo, Chile"`` o
``"offshore Valparaíso, Chile"``. De ahí se obtiene la ciudad de referencia,
el país, la distancia y la dirección, que forman la dimensión de lugar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RELATIVE = re.compile(
    r"^(?P<km>\d+(?:\.\d+)?)\s*km\s+(?P<dir>[NSEW]{1,3})\s+of\s+(?P<rest>.+)$", re.IGNORECASE
)
_PREFIXES = ("offshore ", "near the coast of ", "off the coast of ", "near ")

_DIRECTIONS_ES = {
    "N": "N", "NNE": "NNE", "NE": "NE", "ENE": "ENE",
    "E": "E", "ESE": "ESE", "SE": "SE", "SSE": "SSE",
    "S": "S", "SSW": "SSO", "SW": "SO", "WSW": "OSO",
    "W": "O", "WNW": "ONO", "NW": "NO", "NNW": "NNO",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Place:
    ciudad_referencia: str
    pais: str
    distancia_km: int | None = None
    direccion: str | None = None


def parse_place(raw: str | None) -> Place:
    """Separa distancia, dirección, ciudad y país de la descripción de USGS."""
    if not raw or not raw.strip():
        return Place("Desconocido", "Desconocido")
    text = raw.strip()
    km: int | None = None
    direction: str | None = None
    match = _RELATIVE.match(text)
    if match:
        km = round(float(match["km"]))
        direction = _DIRECTIONS_ES.get(match["dir"].upper(), match["dir"].upper())
        text = match["rest"].strip()
    lowered = text.lower()
    for prefix in _PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    if "," in text:
        city, country = (part.strip() for part in text.rsplit(",", 1))
    else:
        city, country = text, text
    return Place(city or "Desconocido", country or "Desconocido", km, direction)
