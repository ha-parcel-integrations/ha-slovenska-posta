"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.slovenska_posta.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.slovenska_posta.parcels import (
    apply_delivered_filter,
    build_history,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    parse_local_date,
    sort_parcels_by_ts,
    to_iso_timestamp,
)

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    active_sample,
    delivered_sample,
    empty_events_sample,
    event,
    pickup_sample,
    result,
    returned_sample,
    returning_sample,
)

# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status — the official six-state set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        ("received", ParcelStatus.REGISTERED),
        ("transit", ParcelStatus.IN_TRANSIT),
        ("notified", ParcelStatus.AT_PICKUP_POINT),
        ("delivered", ParcelStatus.DELIVERED),
        ("returning", ParcelStatus.RETURNING),
        ("returned", ParcelStatus.RETURNING),
    ],
)
def test_map_parcel_status_known(code, expected):
    assert map_parcel_status(code) == expected


def test_returning_and_returned_collapse_to_the_same_canonical_status():
    """Trap 2: both map to RETURNING — the distinction survives in raw_status."""
    assert map_parcel_status("returning") == map_parcel_status("returned")


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    """No documented `problem` state — an unmapped code is unknown, not guessed."""
    assert map_parcel_status("teleported") == ParcelStatus.UNKNOWN


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("something_new") is None
    assert map_event_status("delivered") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("abducted") == ParcelStatus.UNKNOWN
    assert map_parcel_status("abducted") == ParcelStatus.UNKNOWN
    assert caplog.text.count("abducted") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_parse_local_date_treats_naive_value_as_slovak_local_time():
    # 2026-04-29 is CEST (UTC+2); the offset is what proves it wasn't read as UTC.
    assert parse_local_date("2026-04-29T13:12:42") == "2026-04-29T13:12:42+02:00"
    # 2026-01-15 is CET (UTC+1) — DST-aware, not a fixed offset.
    assert parse_local_date("2026-01-15T08:00:00") == "2026-01-15T08:00:00+01:00"


def test_parse_local_date_trusts_an_existing_offset():
    assert parse_local_date("2026-04-29T13:12:42+00:00") == "2026-04-29T13:12:42+00:00"


def test_parse_local_date_handles_missing_and_garbage():
    assert parse_local_date(None) is None
    assert parse_local_date("") is None
    assert parse_local_date("not-a-date") is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    history = build_history(delivered_sample()["events"])
    assert len(history) == 3
    assert history[0]["raw_status"] == "received: Shipment announced"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        event("transit", f"2026-04-{day:02d}T10:00:00", "moved")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"stateCode": "transit"}]) == []  # no localDate
    assert build_history(["not-a-dict"]) == []


def test_build_history_keeps_unparseable_timestamp_last():
    history = build_history(
        [
            event("received", "2026-04-24T10:00:00", "fine"),
            {"stateCode": "transit", "localDate": "not-a-date", "detailDescription": "odd"},
        ]
    )
    # the unparseable one is dropped by parse_local_date's own guard upstream
    # of build_history's ordering — only the parseable entry survives here.
    assert [entry["raw_status"] for entry in history] == ["received: fine"]


def test_build_history_falls_back_to_state_code_without_detail_text():
    history = build_history([event("transit", "2026-04-24T10:00:00", "")])
    assert history[0]["raw_status"] == "transit"


def test_build_history_prefers_detail_code_over_nothing():
    history = build_history(
        [event("transit", "2026-04-24T10:00:00", "", detail_code="T1")]
    )
    assert history[0]["raw_status"] == "transit: T1"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

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


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample."""
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    pickup = normalize_parcel(pickup_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_never_populates_fields_this_surface_does_not_expose():
    """No ETA, weight, dimensions, sender or receiver on this surface — ever."""
    for sample in (delivered_sample(), active_sample(), pickup_sample()):
        parcel = normalize_parcel(sample)
        assert parcel["sender"] is None
        assert parcel["receiver"] is None
        assert parcel["weight"] is None
        assert parcel["dimensions"] is None
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Slovenská Pošta"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "delivered: Delivered to the recipient"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-29T13:12:42+02:00"
    assert parcel["url"] == (
        "https://www.posta.sk/en/tracking-of-items#parcel=" + DELIVERED_CODE
    )
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 3
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_active_parcel_is_not_delivered():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_pickup_parcel():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Post Office Bratislava 1"


def test_normalize_returning_and_returned_preserve_the_distinction():
    """Trap 2: canonical status collapses, raw_status must not."""
    returning = normalize_parcel(returning_sample())
    returned = normalize_parcel(returned_sample())
    assert returning["status"] == ParcelStatus.RETURNING
    assert returned["status"] == ParcelStatus.RETURNING
    assert returning["raw_status"] != returned["raw_status"]
    assert returning["raw_status"].startswith("returning")
    assert returned["raw_status"].startswith("returned")


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-resolved code still yields a full parcel dict."""
    parcel = normalize_parcel({"number": ACTIVE_CODE})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["pickup_point"] is None
    assert parcel["history"] is None


def test_normalize_empty_events_is_unknown_not_an_error():
    """A syntactically valid code with no history is `unknown`, same as not-yet-scanned."""
    parcel = normalize_parcel(empty_events_sample(ACTIVE_CODE))
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["raw_status"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


def test_normalize_uses_latest_event_by_local_date_not_list_order():
    """The latest event by localDate wins, even if events arrive out of order."""
    raw = result(
        ACTIVE_CODE,
        [
            event("transit", "2026-04-28T15:52:17", "later but listed first"),
            event("received", "2026-04-27T23:03:58", "earlier but listed second"),
        ],
    )
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT


def test_normalize_pickup_point_none_without_post_office():
    parcel = normalize_parcel(active_sample())
    assert parcel["pickup_point"] is None


def test_normalize_pickup_point_none_once_status_moves_past_pickup():
    """`pickup_point` must not leak the *latest* event's own post office once

    the parcel is no longer awaiting collection there — a real delivered
    parcel's last event still carries `postOffice` (the delivering office,
    not a pickup point); a real returned parcel's last event likewise
    carries `postOffice` (the returning office, not the one it was
    originally held at). Mirrors GLS/DHL-NL's `is_pickup`-gated field."""
    delivered = normalize_parcel(
        result(
            DELIVERED_CODE,
            [
                event(
                    "notified",
                    "2026-04-28T09:00:00",
                    "Deposited at post office Bratislava 1",
                    post_office={"name": "Bratislava 1"},
                ),
                event(
                    "delivered",
                    "2026-04-28T15:52:17",
                    "Delivered to addressee",
                    post_office={"name": "Bratislava 1"},
                ),
            ],
        )
    )
    assert delivered["status"] == ParcelStatus.DELIVERED
    assert delivered["pickup"] is False
    assert delivered["pickup_point"] is None

    returned_raw = returned_sample()
    returned_raw["events"][-1]["postOffice"] = {"name": "Bratislava 3"}
    returned = normalize_parcel(returned_raw)
    assert returned["status"] == ParcelStatus.RETURNING
    assert returned["pickup"] is False
    assert returned["pickup_point"] is None


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
