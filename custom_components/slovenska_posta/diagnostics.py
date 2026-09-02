"""Diagnostics support for the Slovenská Pošta parcel tracker integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SlovenskaPostaConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# The tracking code itself, the raw response body's ``number``, and the
# ``postOffice`` object's contents (name/address, when p=1 returns one on an
# event) are the fields that actually appear on this surface. Also covers
# ``detailDescription`` in case a future event ever echoes an address or
# recipient fragment in its free text, and the response body's carrier-side
# echo fields, even though this surface has no sender/receiver/name data
# today — cheap insurance if the payload grows one.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # carrier payload fields
    "number",
    "postOffice",
    "detailDescription",
    "address",
    "postalCode",
    "postal_code",
    "city",
    "street",
    "email",
    "name",
    "recipient",
    "signature",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SlovenskaPostaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Slovenská Pošta config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
