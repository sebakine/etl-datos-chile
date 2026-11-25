"""Cliente HTTP con reintentos exponenciales para APIs públicas poco confiables."""

from __future__ import annotations

import logging
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


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
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
) -> Any:
    """GET que reintenta ante errores de red, 429 y 5xx, con espera exponencial."""

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=min(1.0, max_wait), max=max_wait),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def _do() -> Any:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    return _do()
