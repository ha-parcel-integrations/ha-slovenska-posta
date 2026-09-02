"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

The carrier-specific mapping lives in :data:`_STATUS_MAP` and
:func:`normalize_parcel`, plus the local-time parsing in
:func:`parse_local_date` and the event-to-history builder in
:func:`build_history` (both shaped around this surface's own event fields).
Everything else — generic timestamp parsing, the sort contract, the
delivered filter, the one-shot warning for unmapped statuses — is suite-wide
machinery shared with every other carrier.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-slovenska-posta/issues/new"
    "?template=unrecognised_status.yml"
)

# The official API manual's complete state set. No separate "problem" state
# exists in the manual and none has been observed live — an unmapped/future
# stateCode falls through to `unknown` plus the one-shot warning rather than
# a guessed `problem` mapping.
#
# `returning` and `returned` both collapse to canonical RETURNING — preserve
# the distinction in `raw_status` so a future split (should `returned` ever
# need its own canonical status) doesn't need a fixture recapture, just a
# mapping-table edit.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "received": ParcelStatus.REGISTERED,
    "transit": ParcelStatus.IN_TRANSIT,
    "notified": ParcelStatus.AT_PICKUP_POINT,
    "delivered": ParcelStatus.DELIVERED,
    "returning": ParcelStatus.RETURNING,
    "returned": ParcelStatus.RETURNING,
}

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Slovenská Pošta status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def map_parcel_status(code: str | None) -> ParcelStatus:
    """Map a carrier status code to a canonical :class:`ParcelStatus`.

    ``None`` (a not-yet-scanned parcel) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    if not code:
        return ParcelStatus.UNKNOWN
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return ParcelStatus.UNKNOWN


def map_event_status(code: str | None) -> ParcelStatus | None:
    """Map a history entry's status code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to unknown")
    and warn once, reusing the parcel-status one-shot set.
    """
    if not code:
        return None
    mapped = _STATUS_MAP.get(code)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(code)
    return None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


# Every event's `localDate` is Slovak local time regardless of where HA runs
# — the field name says so and the manual gives no offset. Fixed to the
# carrier's own country rather than HA's configured timezone, which is a
# fine distinction if this ever tracks a parcel from outside Slovakia's own
# timezone.
_CARRIER_TZ = ZoneInfo("Europe/Bratislava")


def parse_local_date(value: str | None) -> str | None:
    """Parse an event's ``localDate`` as Slovak local time.

    Returns an aware ISO 8601 string (so it sorts and compares like every
    other timestamp in this module), or ``None`` on a missing/unparseable
    value. A value that already carries an offset is trusted as-is.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CARRIER_TZ)
    return parsed.isoformat()


def _event_raw_status(event: dict) -> str | None:
    """Build one event's ``raw_status`` from ``stateCode`` + detail text.

    Always led by ``stateCode`` so the returning/returned distinction (Trap 2)
    survives even when ``detailDescription`` reads the same for both; the
    detail text is appended only when it adds something.
    """
    state_code = event.get("stateCode")
    detail = event.get("detailDescription") or event.get("detailCode")
    if state_code and detail and detail != state_code:
        return f"{state_code}: {detail}"
    return state_code or detail


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``timestamp`` comes from each event's own
    ``localDate``, parsed as Slovak local time (see :func:`parse_local_date`)
    — never treated as UTC. Sorted oldest → newest and capped to the most
    recent ``max_events``.
    """
    parseable: list[tuple[datetime, dict]] = []
    unparseable: list[dict] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = parse_local_date(event.get("localDate"))
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(event.get("stateCode")),
            "raw_status": _event_raw_status(event),
        }
        parsed = parse_iso(timestamp)
        if parsed is None:
            unparseable.append(entry)
        else:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    ordered = [entry for _, entry in parseable] + unparseable
    return ordered[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _latest_event(events: list | None) -> dict | None:
    """Return the event with the latest ``localDate``.

    Falls back to the last event in list order when none of them parse, and
    to ``None`` when there are no events at all — a result with an empty
    ``events[]`` (not yet trackable, or no longer retained) normalises the
    same as one with no ``events`` key.
    """
    candidates = [event for event in events or [] if isinstance(event, dict)]
    if not candidates:
        return None
    dated: list[tuple[datetime, dict]] = []
    for event in candidates:
        parsed = parse_iso(parse_local_date(event.get("localDate")))
        if parsed is not None:
            dated.append((parsed, event))
    if dated:
        dated.sort(key=lambda item: item[0])
        return dated[-1][1]
    return candidates[-1]


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    ``raw`` is one ``results[]`` entry (``{"number", "status", "events"}``) or
    the pending placeholder ``{"number": code}`` the coordinator builds for a
    code with no result yet. The **keys of the returned dict are the
    contract**: every carrier in the suite returns exactly these, in this
    order, and the aggregator and cross-carrier dashboards depend on it.

    This surface has no ETA, weight, dimensions, sender or receiver data —
    those stay ``None`` always, not just when a particular parcel lacks them
    (see ``CAPABILITIES`` in const.py). Status comes from the *latest* event's
    ``stateCode``; a parcel with no events (not yet trackable, or no longer
    retained) reports ``unknown`` — the same as an unrecognised ``stateCode``,
    deliberately, since neither is a real carrier-reported problem.

    ``pickup``/``pickup_point`` are gated on ``status is AT_PICKUP_POINT``
    (suite convention, matching GLS/DHL-NL) rather than always reading the
    latest event's ``postOffice`` — a delivered or returned parcel's last
    event still carries a ``postOffice`` (the delivering/returning office),
    which is not a place the recipient can go collect anything.
    """
    tracking_code = raw.get("number")
    events = raw.get("events") or []
    latest = _latest_event(events)
    state_code = latest.get("stateCode") if latest else None
    status = map_parcel_status(state_code)
    delivered = status is ParcelStatus.DELIVERED

    is_pickup = status is ParcelStatus.AT_PICKUP_POINT
    pickup_point = ((latest.get("postOffice") if latest else None) or {}) if is_pickup else {}

    return {
        "carrier": "Slovenská Pošta",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": _event_raw_status(latest) if latest else None,
        "delivered": delivered,
        "delivered_at": parse_local_date(latest.get("localDate"))
        if delivered and latest
        else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": is_pickup,
        "pickup_point": pickup_point.get("name") or None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
