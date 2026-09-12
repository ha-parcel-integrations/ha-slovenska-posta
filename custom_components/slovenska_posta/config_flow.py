"""Config flow for the Slovenská Pošta parcel tracker integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import SlovenskaPostaApiError
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# The carrier's tracking-code format is not documented by the manual and was
# not established from the two live samples (redacted, not retained). Real
# format enforcement happens live: newly added codes are checked against the
# API itself in async_step_parcels below, which is what actually knows the
# shape. Locally we only strip control characters — garbage bytes, not a
# format-shape gate — never reject on length or charset.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def normalize_tracking_code(value: str) -> str:
    """Return the tracking code trimmed of surrounding whitespace only.

    Deliberately does **not** uppercase or strip internal characters — the
    format isn't documented, so guessing a canonical shape risks mangling a
    code the carrier would otherwise accept as-is.
    """
    return (value or "").strip()


def valid_tracking_code(value: str) -> bool:
    """Whether ``value`` is a plausible tracking code, format unknown.

    Accepts any non-empty code once control characters are rejected — the
    carrier's real formats vary too much and aren't fully documented to gate
    on shape client-side. The carrier's own answer (via a live lookup) is the
    actual authority.
    """
    return bool(value) and not _CONTROL_CHAR_RE.search(value)


def _current_parcels(entry: ConfigEntry) -> list[dict[str, str]]:
    """Return a mutable copy of the tracked parcels list."""
    return [dict(item) for item in entry.options.get(CONF_PARCELS, [])]


def _clean_tracking_codes(values: list[str] | None) -> list[str]:
    """Normalise, drop blanks, and de-duplicate tracking codes."""
    codes: list[str] = []
    for value in values or []:
        code = normalize_tracking_code(value)
        if code and code not in codes:
            codes.append(code)
    return codes


class SlovenskaPostaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI-driven configuration flow for the Slovenská Pošta integration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SlovenskaPostaOptionsFlowHandler:
        """Return the options flow handler."""
        return SlovenskaPostaOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the Slovenská Pošta hub — single instance, no input needed.

        Tracking is keyed on the tracking code alone (no account, no postal
        code — the ``/tracking`` endpoint takes only ``q=``), so there is
        nothing to ask at setup: the entry is created straight away and
        parcels are added afterwards via the options flow, the
        ``slovenska_posta.track_parcel`` service or a dashboard button.
        ``single_config_entry`` in the manifest enforces one hub.
        """
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Slovenská Pošta",
            data={},
            options={
                CONF_PARCELS: [],
                CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
            },
        )


class SlovenskaPostaOptionsFlowHandler(OptionsFlow):
    """Manage tracked parcels separately from integration settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer parcel management separately from integration settings."""
        return self.async_show_menu(
            step_id="init", menu_options=["parcels", "settings"]
        )

    async def async_step_parcels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the complete tracked-code list.

        Newly added codes get one extra check beyond the generic length cap:
        a live lookup against the carrier's own batch endpoint. A code the
        carrier answers ``status: "invalid_format"`` for is dropped by
        :meth:`SlovenskaPostaApiClient.async_get_parcels` before it ever reaches
        here, so it is missing from the result — that absence is what turns
        into the same ``invalid_tracking_code`` form error, never a tracked
        parcel that silently shows ``unknown``. If the live check itself
        can't run (the carrier is unreachable), codes are accepted on the
        local check alone rather than blocking parcel management on an
        outage — the next poll still resolves them.
        """
        current_codes = [
            p[CONF_TRACKING_CODE] for p in _current_parcels(self.config_entry)
        ]
        errors: dict[str, str] = {}
        if user_input is not None:
            codes = _clean_tracking_codes(user_input.get("tracking_codes"))
            if any(not valid_tracking_code(code) for code in codes):
                errors["base"] = "invalid_tracking_code"
            else:
                new_codes = [code for code in codes if code not in current_codes]
                if new_codes and not await self._async_new_codes_valid(new_codes):
                    errors["base"] = "invalid_tracking_code"
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_PARCELS: [{CONF_TRACKING_CODE: code} for code in codes],
                        CONF_DELIVERED_FILTER_TYPE: self.config_entry.options.get(
                            CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                        ),
                        CONF_DELIVERED_FILTER_AMOUNT: self.config_entry.options.get(
                            CONF_DELIVERED_FILTER_AMOUNT,
                            DEFAULT_DELIVERED_FILTER_AMOUNT,
                        ),
                        CONF_INCLUDE_HISTORY: self.config_entry.options.get(
                            CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                        ),
                    },
                )
        schema = vol.Schema(
            {
                vol.Optional("tracking_codes"): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id="parcels",
            data_schema=self.add_suggested_values_to_schema(
                schema, {"tracking_codes": current_codes}
            ),
            errors=errors,
        )

    async def _async_new_codes_valid(self, new_codes: list[str]) -> bool:
        """Whether the carrier's live batch endpoint accepts every new code.

        ``True`` when every code comes back (i.e. none was rejected as
        ``invalid_format``) — and also when the lookup itself fails, so a
        transient outage doesn't block adding a parcel the user can already
        see is correctly typed.
        """
        client = getattr(self.config_entry.runtime_data, "client", None)
        if client is None:
            return True
        try:
            results = await client.async_get_parcels(new_codes)
        except (SlovenskaPostaApiError, aiohttp.ClientError) as err:
            _LOGGER.debug(
                "Slovenská Pošta live tracking-code check failed, accepting on "
                "the local check alone: %s",
                err,
            )
            return True
        return all(code in results for code in new_codes)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and handle the non-parcel integration settings."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_PARCELS: _current_parcels(self.config_entry),
                    CONF_DELIVERED_FILTER_TYPE: user_input[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        user_input[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(user_input[CONF_INCLUDE_HISTORY]),
                },
            )
        current = self.config_entry.options
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_DELIVERED_FILTER_TYPE,
                default=current.get(
                    CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["days", "parcels"],
                    translation_key=CONF_DELIVERED_FILTER_TYPE,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_DELIVERED_FILTER_AMOUNT,
                default=current.get(
                    CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_INCLUDE_HISTORY,
                default=current.get(CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY),
            ): selector.BooleanSelector(),
        }
        return self.async_show_form(step_id="settings", data_schema=vol.Schema(schema))
