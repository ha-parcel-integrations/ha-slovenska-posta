"""Tests for the Slovenská Pošta API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.slovenska_posta.api import (
    BATCH_CHUNK_SIZE,
    SlovenskaPostaApiClient,
    SlovenskaPostaApiError,
)

CODE_A = "RR000000001SK"
CODE_B = "RR000000002SK"


def _session_returning(status: int, body: object = None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = {}
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


# ---------------------------------------------------------------------------
# async_get_parcels — happy path
# ---------------------------------------------------------------------------


async def test_get_parcels_returns_one_entry_per_code():
    session = _session_returning(
        200,
        {
            "status": "ok",
            "results": [
                {"number": CODE_A, "status": "ok", "events": []},
                {"number": CODE_B, "status": "ok", "events": []},
            ],
        },
    )
    client = SlovenskaPostaApiClient(session)

    result = await client.async_get_parcels([CODE_A, CODE_B])

    assert result[CODE_A]["number"] == CODE_A
    assert result[CODE_B]["number"] == CODE_B
    # every requested code ends up comma-joined in the one URL
    url = session.get.call_args[0][0]
    assert CODE_A in url and CODE_B in url


async def test_get_parcels_empty_input_short_circuits():
    client = SlovenskaPostaApiClient(MagicMock())
    assert await client.async_get_parcels([]) == {}


async def test_get_parcels_matches_by_number_not_array_position():
    """Trap 4: match results[] entries by their own `number`, never by index."""
    session = _session_returning(
        200,
        {
            "status": "ok",
            "results": [
                # reversed order relative to the request
                {"number": CODE_B, "status": "ok", "events": []},
                {"number": CODE_A, "status": "ok", "events": []},
            ],
        },
    )
    client = SlovenskaPostaApiClient(session)

    result = await client.async_get_parcels([CODE_A, CODE_B])

    assert result[CODE_A]["number"] == CODE_A
    assert result[CODE_B]["number"] == CODE_B


async def test_get_parcels_drops_invalid_format_entries(caplog):
    """A result with status: invalid_format must never reach the caller."""
    session = _session_returning(
        200,
        {
            "status": "ok",
            "results": [
                {"number": "not-a-code!!", "status": "invalid_format"},
                {"number": CODE_A, "status": "ok", "events": []},
            ],
        },
    )
    client = SlovenskaPostaApiClient(session)

    result = await client.async_get_parcels(["not-a-code!!", CODE_A])

    assert "not-a-code!!" not in result
    assert CODE_A in result
    assert "invalid tracking code format" in caplog.text


async def test_get_parcels_chunks_at_the_documented_cap():
    codes = [f"RR{i:09d}SK" for i in range(BATCH_CHUNK_SIZE + 5)]
    calls = []

    def _get(url, *args, **kwargs):
        calls.append(url)
        requested = url.split("q=")[1].split("&")[0].split(",")
        body = {
            "status": "ok",
            "results": [{"number": c, "status": "ok", "events": []} for c in requested],
        }
        response = AsyncMock()
        response.status = 200
        response.headers = {}
        response.json = AsyncMock(return_value=body)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=response)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    session = MagicMock()
    session.get = MagicMock(side_effect=_get)
    client = SlovenskaPostaApiClient(session)

    result = await client.async_get_parcels(codes)

    assert len(calls) == 2  # BATCH_CHUNK_SIZE + 5 -> two chunks
    assert len(result) == len(codes)


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


async def test_get_parcels_raises_on_error_status():
    client = SlovenskaPostaApiClient(_session_returning(500, {}))
    with pytest.raises(SlovenskaPostaApiError):
        await client.async_get_parcels([CODE_A])


async def test_get_parcels_raises_on_unparseable_body():
    client = SlovenskaPostaApiClient(_session_returning(200, "not json"))
    with pytest.raises(SlovenskaPostaApiError):
        await client.async_get_parcels([CODE_A])


async def test_get_parcels_raises_on_non_object_body():
    client = SlovenskaPostaApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(SlovenskaPostaApiError):
        await client.async_get_parcels([CODE_A])


async def test_get_parcels_raises_on_missing_results_key():
    client = SlovenskaPostaApiClient(_session_returning(200, {"status": "ok"}))
    with pytest.raises(SlovenskaPostaApiError):
        await client.async_get_parcels([CODE_A])


async def test_get_parcels_ignores_non_dict_and_numberless_entries():
    session = _session_returning(
        200,
        {
            "status": "ok",
            "results": ["not-a-dict", {"status": "ok", "events": []}],
        },
    )
    client = SlovenskaPostaApiClient(session)
    result = await client.async_get_parcels([CODE_A])
    assert result == {}


async def test_get_parcels_raises_on_429_with_retry_after_header():
    response = AsyncMock()
    response.status = 429
    response.headers = {"Retry-After": "42"}
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    client = SlovenskaPostaApiClient(session)

    with pytest.raises(SlovenskaPostaApiError) as err:
        await client.async_get_parcels([CODE_A])
    assert err.value.status_code == 429
    assert err.value.retry_after == 42


async def test_get_parcels_429_with_http_date_retry_after_is_none():
    response = AsyncMock()
    response.status = 429
    response.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    client = SlovenskaPostaApiClient(session)

    with pytest.raises(SlovenskaPostaApiError) as err:
        await client.async_get_parcels([CODE_A])
    assert err.value.retry_after is None


async def test_get_parcels_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = SlovenskaPostaApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcels([CODE_A])
