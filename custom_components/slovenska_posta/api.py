"""Slovenská Pošta public tracking API client.

The single documented surface: ``GET /tracking?q=<code[,code...]>&l=en&p=1``,
keyless, batching up to :data:`BATCH_CHUNK_SIZE` tracking codes per call (the
manual's own cap). The envelope is ``{"status": "ok", "results": [...]}`` —
one entry per requested code, each carrying its own ``number``, ``status``
(``"ok"`` or ``"invalid_format"``) and, for an ``"ok"`` entry, ``events``.

:meth:`SlovenskaPostaApiClient.async_get_parcels` returns ``{number: result}``
built from ``results[]`` and matched by each entry's own ``number`` — never
by request-array position, since the manual doesn't promise the response
preserves request order. A ``status: "invalid_format"`` entry is dropped
before it reaches the caller: it means the carrier itself rejected that code
as malformed, not "not found" — it must never turn into a ``ParcelStatus``.

Never call ``https://api.posta.sk/private/web/track`` — it currently answers
unauthenticated too, but is undocumented, carries a different ``{parcels[]}``
schema and is not the carrier-supported contract; nothing in this module
should ever reference it.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)

# The official manual's own cap on tracking codes per ``q=`` request.
BATCH_CHUNK_SIZE = 100


class SlovenskaPostaApiError(Exception):
    """Raised when a Slovenská Pošta API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Slovenská Pošta API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class SlovenskaPostaApiClient:
    """Client for the public, keyless Slovenská Pošta ``/tracking`` endpoint."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcels(
        self, tracking_codes: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Fetch every tracking code, batched per the documented ``q=`` cap.

        Raises :class:`SlovenskaPostaApiError` (status code and, for a 429,
        ``retry_after`` set) or lets ``aiohttp.ClientError`` propagate — both
        untouched, so the coordinator's own backoff/cache-fallback handles
        them. A code the carrier rejects as malformed (``status:
        "invalid_format"``) is silently dropped from the returned mapping —
        it is never "not found", it's a different signal that must not
        become a parcel.
        """
        if not tracking_codes:
            return {}
        merged: dict[str, dict[str, Any]] = {}
        for start in range(0, len(tracking_codes), BATCH_CHUNK_SIZE):
            chunk = tracking_codes[start : start + BATCH_CHUNK_SIZE]
            for result in await self._async_get_batch(chunk):
                if not isinstance(result, dict):
                    continue
                number = result.get("number")
                if not number:
                    continue
                if result.get("status") == "invalid_format":
                    _LOGGER.warning(
                        "Slovenská Pošta rejected %s as an invalid tracking "
                        "code format",
                        number,
                    )
                    continue
                merged[number] = result
        return merged

    async def _async_get_batch(self, codes: list[str]) -> list[Any]:
        """Fetch one ``q=`` batch (up to :data:`BATCH_CHUNK_SIZE` codes)."""
        url = TRACKING_API_URL.format(
            codes=",".join(quote(code, safe="") for code in codes)
        )
        async with self._session.get(url) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise SlovenskaPostaApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise SlovenskaPostaApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: consumer endpoints routinely serve JSON as
                # text/plain, and aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                raise SlovenskaPostaApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise SlovenskaPostaApiError("unexpected body (not a JSON object)")
        results = payload.get("results")
        if not isinstance(results, list):
            raise SlovenskaPostaApiError("unexpected body (no results[])")
        return results
