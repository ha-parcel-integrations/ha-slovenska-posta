"""Tests for Slovenská Pošta diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.slovenska_posta.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "RR000000001SK"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 45
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=45)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "RR000000001SK",
            "sender": None,
            "receiver": None,
            "status": "at_pickup_point",
            "raw": {
                "number": "RR000000001SK",
                "status": "ok",
                "events": [
                    {
                        "stateCode": "notified",
                        "localDate": "2026-04-29T09:00:00",
                        "detailCode": "P1",
                        "detailDescription": "Ready for collection",
                        "postOffice": {
                            "name": "Post Office Bratislava 1",
                            "city": "Bratislava",
                        },
                    }
                ],
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    assert result["polling"] == {
        "tier_minutes": 45,
        "update_interval_seconds": 2700.0,
        "suspended": False,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["number"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["events"][0]["postOffice"] == "**REDACTED**"
    assert (
        result["incoming"][0]["raw"]["events"][0]["detailDescription"]
        == "**REDACTED**"
    )
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "at_pickup_point"
    assert result["incoming"][0]["raw"]["events"][0]["stateCode"] == "notified"
    assert result["incoming"][0]["raw"]["events"][0]["detailCode"] == "P1"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
