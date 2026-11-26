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

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
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
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
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
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
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


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


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

    ``pickup`` is the delivery route, not the current status: it is true once
    any event reached ``notified`` and stays true after collection, return or
    delivery. ``pickup_point`` is that ``notified`` event's own post office,
    never the newest event's (a delivered or returned parcel's last event
    carries the delivering or returning office).
    """
    tracking_code = raw.get("number")
    events = raw.get("events") or []
    latest = _latest_event(events)
    state_code = latest.get("stateCode") if latest else None
    status = map_parcel_status(state_code)
    delivered = status is ParcelStatus.DELIVERED

    pickup_events = [
        event
        for event in events
        if isinstance(event, dict)
        and _STATUS_MAP.get(event.get("stateCode")) is ParcelStatus.AT_PICKUP_POINT
    ]
    pickup = is_pickup(
        None,
        status,
        (_STATUS_MAP.get(event.get("stateCode")) for event in pickup_events),
    )
    pickup_event = _latest_event(pickup_events)
    pickup_office = ((pickup_event or {}).get("postOffice") or {}) if pickup else {}

    return {
        "carrier": "Slovenská Pošta",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "contents": None,
        "status": status,
        "raw_status": _event_raw_status(latest) if latest else None,
        "delivered": delivered,
        "delivered_at": parse_local_date(latest.get("localDate"))
        if delivered and latest
        else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": pickup,
        "pickup_point": pickup_office.get("name") or None,
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
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
