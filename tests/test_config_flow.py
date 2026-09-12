"""Tests for the Slovenská Pošta config and options flow."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.slovenska_posta.api import SlovenskaPostaApiError
from custom_components.slovenska_posta.config_flow import (
    normalize_tracking_code,
    valid_tracking_code,
)
from custom_components.slovenska_posta.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)

CODE = "RR000000001SK"


def test_normalize_tracking_code_trims_only():
    """Case and internal characters are preserved — the format isn't documented."""
    assert normalize_tracking_code(f"  {CODE}  ") == CODE
    assert normalize_tracking_code("rr000000001sk") == "rr000000001sk"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


def test_valid_tracking_code_accepts_any_non_empty_code():
    assert valid_tracking_code(CODE)
    assert valid_tracking_code("ABC")
    assert not valid_tracking_code("")
    assert not valid_tracking_code("bad\x00code")  # control character


async def test_user_flow_creates_hub_without_input(hass):
    """No account, no postcode — the entry is created straight away."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Slovenská Pošta"
    assert result["options"][CONF_PARCELS] == []


async def test_second_hub_rejected(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort"
    # single_config_entry in the manifest aborts before the flow runs.
    assert result["reason"] == "single_instance_allowed"


def _hub(parcels: list[dict], *, client=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )
    # No full integration setup in these tests — runtime_data is set directly
    # so the live tracking-code check in async_step_parcels has something to
    # call (or, when omitted, correctly finds nothing and skips the check).
    entry.runtime_data = SimpleNamespace(client=client) if client else None
    return entry


def _settings_input(
    *,
    history=False,
    filter_type="days",
    amount=7,
) -> dict:
    """Build the settings-form submission."""
    return {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: amount,
        CONF_INCLUDE_HISTORY: history,
    }


async def _open_options_step(hass, entry, step_id: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [CODE]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: CODE}]


async def test_options_add_parcel_trims_whitespace(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [f"  {CODE}  "]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: CODE}]


async def test_options_add_invalid_tracking_code(hass):
    """A control character is the only thing still rejected locally."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["bad\x00code"]}
    )
    assert result["errors"]["base"] == "invalid_tracking_code"


async def test_options_de_duplicates_exact_tracking_codes(hass):
    entry = _hub([{CONF_TRACKING_CODE: CODE}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [CODE, CODE]}
    )
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: CODE}]


async def test_options_remove_parcel(hass):
    other = "RR000000002SK"
    entry = _hub([{CONF_TRACKING_CODE: CODE}, {CONF_TRACKING_CODE: other}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [other]}
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {other}


async def test_options_can_clear_the_tracked_code_list(hass):
    entry = _hub([{CONF_TRACKING_CODE: CODE}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_changes_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _settings_input(
            history=True,
            filter_type="parcels",
            amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5


# ---------------------------------------------------------------------------
# live validation of newly added codes — invalid_format must surface as a
# form error, not a registered parcel that shows "unknown"
# ---------------------------------------------------------------------------


def _client(mapping: dict) -> AsyncMock:
    client = AsyncMock()
    client.async_get_parcels.side_effect = lambda codes: {
        code: mapping[code] for code in codes if code in mapping
    }
    return client


async def test_options_add_code_the_carrier_confirms_is_accepted(hass):
    entry = _hub([], client=_client({CODE: {"number": CODE, "status": "ok"}}))
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [CODE]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: CODE}]


async def test_options_add_code_the_carrier_rejects_is_a_form_error(hass):
    """invalid_format never becomes a tracked parcel — it's a form error."""
    entry = _hub([], client=_client({}))  # carrier dropped it -> invalid_format
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [CODE]}
    )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_tracking_code"


async def test_options_live_check_only_applies_to_new_codes(hass):
    """Resubmitting an already-tracked code doesn't re-run the live check."""
    client = AsyncMock()
    client.async_get_parcels.side_effect = AssertionError(
        "should not be called for an already-tracked code"
    )
    entry = _hub([{CONF_TRACKING_CODE: CODE}], client=client)
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [CODE]}
    )
    assert result["type"] == "create_entry"


async def test_options_live_check_outage_does_not_block_adding(hass):
    """A carrier outage during the live check must not block parcel management."""
    for exc in (SlovenskaPostaApiError("HTTP 500"), aiohttp.ClientError("boom")):
        client = AsyncMock()
        client.async_get_parcels.side_effect = exc
        entry = _hub([], client=client)
        entry.add_to_hass(hass)
        result = await _open_options_step(hass, entry, "parcels")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"tracking_codes": [CODE]}
        )
        assert result["type"] == "create_entry"


async def test_options_skips_live_check_when_no_client_available(hass):
    """No runtime_data (entry never fully set up) -> local check only."""
    entry = _hub([])  # client=None
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": [CODE]}
    )
    assert result["type"] == "create_entry"
