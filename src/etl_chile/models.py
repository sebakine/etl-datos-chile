"""Modelos validados de los registros que entran al warehouse.

Si un registro de la API no cumple estas reglas, no se carga: queda en la
tabla ``etl.rechazos`` con el motivo, para revisarlo sin detener el pipeline.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_FUTURE_TOLERANCE = timedelta(hours=1)


class Sismo(BaseModel):
    model_config = ConfigDict(frozen=True)

    sismo_id: str = Field(min_length=1)
    ocurrido_en: datetime
    actualizado_en: datetime
    magnitud: Decimal = Field(ge=-2, le=10)
    tipo_magnitud: str | None
    profundidad_km: Decimal = Field(ge=-10, le=800)
    latitud: Decimal = Field(ge=-90, le=90)
    longitud: Decimal = Field(ge=-180, le=180)
    lugar: str | None
    estado: str | None
    alerta_tsunami: bool
    significancia: int | None
    url: str | None

    @field_validator("ocurrido_en", "actualizado_en")
    @classmethod
    def _aware_and_not_future(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("la fecha debe tener zona horaria")
        if value > datetime.now(UTC).replace(microsecond=0) + _FUTURE_TOLERANCE:
            raise ValueError("la fecha está en el futuro")
        return value


class ValorIndicador(BaseModel):
    model_config = ConfigDict(frozen=True)

    codigo: str = Field(min_length=1)
    fecha: date
    valor: Decimal = Field(gt=0)


class Indicador(BaseModel):
    model_config = ConfigDict(frozen=True)

    codigo: str
    nombre: str
    unidad_medida: str
