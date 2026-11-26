"""Cliente HTTP con reintentos exponenciales para APIs públicas poco confiables."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from etl_chile import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"etl-datos-chile/{__version__} (+https://github.com/sebakine/etl-datos-chile)"


class ErrorDeLaFuente(Exception):
    """La API respondió 200, pero con un error en el cuerpo (p. ej. un 500 interno)."""


def is_upstream_unavailable(exc: BaseException) -> bool:
    """La API externa no respondió (red, 429, 5xx o error en el cuerpo): no es culpa nuestra."""
    if isinstance(exc, (httpx.TransportError, ErrorDeLaFuente)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def make_client(
    timeout: float = 60.0, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
        transport=transport,
    )


def get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    attempts: int = 5,
    max_wait: float = 30.0,
    error_en_cuerpo: Callable[[Any], str | None] | None = None,
) -> Any:
    """GET que reintenta ante errores de red, 429 y 5xx, con espera exponencial.

    ``error_en_cuerpo`` detecta respuestas 200 que en realidad traen un error;
    esas también se reintentan y, si persisten, cuentan como fuente caída.
    """

    @retry(
        retry=retry_if_exception(is_upstream_unavailable),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=min(1.0, max_wait), max=max_wait),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def _do() -> Any:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if error_en_cuerpo is not None and (mensaje := error_en_cuerpo(data)):
            raise ErrorDeLaFuente(f"{url}: {mensaje}")
        return data

    return _do()
