"""Tests for the Slovenská Pošta services (track_parcel / untrack_parcel)."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.slovenska_posta.const import (
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)

from .payloads import active_sample

NEW_CODE = "RR000000099SK"


def _batch_mock():
    """A batch mock: returns an active sample for whichever codes are asked."""

    async def _get(codes):
        return {code: active_sample(code) for code in codes}

    return AsyncMock(side_effect=_get)


async def _setup(hass, parcels: list[dict] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels or []},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.slovenska_posta.api.SlovenskaPostaApiClient.async_get_parcels",
        new=_batch_mock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_track_parcel_adds_to_options(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.slovenska_posta.api.SlovenskaPostaApiClient.async_get_parcels",
        new=_batch_mock(),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: NEW_CODE},
            blocking=True,
        )
        await hass.async_block_till_done()

    parcels = entry.options[CONF_PARCELS]
    assert parcels == [{CONF_TRACKING_CODE: NEW_CODE}]


async def test_track_parcel_trims_but_does_not_uppercase(hass):
    """The service mirrors the options flow: trim only, case preserved."""
    entry = await _setup(hass)
    with patch(
        "custom_components.slovenska_posta.api.SlovenskaPostaApiClient.async_get_parcels",
        new=_batch_mock(),
    ):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: f"  {NEW_CODE}  "},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == [{CONF_TRACKING_CODE: NEW_CODE}]


async def test_track_parcel_rejects_empty_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ""}, blocking=True
        )


async def test_track_parcel_duplicate_is_noop(hass):
    entry = await _setup(hass)
    with patch(
        "custom_components.slovenska_posta.api.SlovenskaPostaApiClient.async_get_parcels",
        new=_batch_mock(),
    ):
        for _ in range(2):
            await hass.services.async_call(
                DOMAIN,
                "track_parcel",
                {CONF_TRACKING_CODE: NEW_CODE},
                blocking=True,
            )
            await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1


async def test_untrack_parcel_removes_from_options(hass):
    entry = await _setup(hass, parcels=[{CONF_TRACKING_CODE: NEW_CODE}])
    with patch(
        "custom_components.slovenska_posta.api.SlovenskaPostaApiClient.async_get_parcels",
        new=_batch_mock(),
    ):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: NEW_CODE},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert entry.options[CONF_PARCELS] == []


async def test_untrack_unknown_code_is_noop(hass):
    entry = await _setup(hass, parcels=[{CONF_TRACKING_CODE: NEW_CODE}])
    with patch(
        "custom_components.slovenska_posta.api.SlovenskaPostaApiClient.async_get_parcels",
        new=_batch_mock(),
    ):
        await hass.services.async_call(
            DOMAIN,
            "untrack_parcel",
            {CONF_TRACKING_CODE: "RR000000077SK"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert len(entry.options[CONF_PARCELS]) == 1
