"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.slovenska_posta.const import ParcelStatus
from custom_components.slovenska_posta.parcels import (
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import (
    active_sample,
    delivered_sample,
    event,
    pickup_sample,
    result,
    returned_sample,
    returning_sample,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

ZONE = ZoneInfo("Europe/Bratislava")


def _collected() -> dict:
    return result(
        "RR000000009SK",
        [
            event("received", "2026-04-27T23:03:58", "Shipment announced"),
            event(
                "notified",
                "2026-04-28T09:00:00",
                "Ready for collection",
                post_office={"name": "Post Office Bratislava 1"},
            ),
            event(
                "delivered",
                "2026-04-29T16:00:00",
                "Collected",
                post_office={"name": "Post Office Bratislava 7"},
            ),
        ],
    )


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returning": returning_sample,
    "returned": returned_sample,
    "bare": lambda: {"number": "RR000000010SK"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered_at"] == "2026-04-29T16:00:00+02:00"
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Post Office Bratislava 1"


def test_a_returning_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(returning_sample())
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Post Office Bratislava 1"


def test_a_parcel_never_at_a_pickup_point_is_not_pickup():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=ZONE)
    stop = datetime.fromisoformat(end)
    assert (stop.hour, stop.minute, stop.second, stop.microsecond) == (23, 59, 59, 0)
    assert stop.date() == parse_iso(start).astimezone(ZONE).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=ZONE)
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end)
    assert (first.hour, first.minute, first.second) == (0, 0, 0)
    assert (last.hour, last.minute, last.second, last.microsecond) == (23, 59, 59, 0)
    assert first.date() == last.date()


def test_planned_is_emptied_for_a_finished_status():
    start, end = planned_window("2099-04-29", None, zone=UTC)
    assert planned_for_status(ParcelStatus.DELIVERED, start, end) == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, start, end) == (start, end)
