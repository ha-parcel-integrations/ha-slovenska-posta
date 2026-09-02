"""Sample Slovenská Pošta ``/tracking`` API payloads shared by the test modules.

Shapes match the live-confirmed envelope — ``{"status": "ok", "results": [...]}``
with one entry per requested code, each carrying its own ``number``,
``status`` (``"ok"`` or ``"invalid_format"``) and, for an ``"ok"`` entry,
``events``. Field names (``stateCode``, ``detailCode``, ``detailDescription``,
``localDate``, ``postOffice``) match the official manual; the illustrative
text values are not the two real, recipient-authorised responses used during
research — those are deliberately not retained.

Keep samples in one module rather than inline in each test — when the
payload shape turns out to be different from what was assumed, there is then
exactly one place to fix.
"""
from __future__ import annotations

ACTIVE_CODE = "RR000000001SK"
DELIVERED_CODE = "RR000000002SK"
PICKUP_CODE = "RR000000003SK"
RETURNING_CODE = "RR000000004SK"
RETURNED_CODE = "RR000000005SK"


def event(
    state_code: str,
    local_date: str,
    detail_description: str = "",
    *,
    detail_code: str | None = None,
    post_office: dict | None = None,
) -> dict:
    """One entry of the carrier's own event timeline."""
    entry: dict = {
        "stateCode": state_code,
        "localDate": local_date,
        "detailCode": detail_code,
        "detailDescription": detail_description,
    }
    if post_office is not None:
        entry["postOffice"] = post_office
    return entry


def result(number: str, events: list[dict], *, status: str = "ok") -> dict:
    """One ``results[]`` entry, as returned by the batch endpoint."""
    return {"number": number, "status": status, "events": events}


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative tracking response for a delivered parcel."""
    return result(
        code,
        [
            event("received", "2026-04-27T23:03:58", "Shipment announced"),
            event("transit", "2026-04-28T15:52:17", "At the sorting facility"),
            event("delivered", "2026-04-29T13:12:42", "Delivered to the recipient"),
        ],
    )


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel still in transit, no terminal event yet."""
    return result(
        code,
        [
            event("received", "2026-04-27T23:03:58", "Shipment announced"),
            event("transit", "2026-04-28T15:52:17", "At the sorting facility"),
        ],
    )


def pickup_sample(code: str = PICKUP_CODE) -> dict:
    """A parcel waiting for collection at a post office."""
    return result(
        code,
        [
            event("received", "2026-04-27T23:03:58", "Shipment announced"),
            event("transit", "2026-04-28T15:52:17", "At the sorting facility"),
            event(
                "notified",
                "2026-04-29T09:00:00",
                "Ready for collection",
                post_office={"name": "Post Office Bratislava 1"},
            ),
        ],
    )


def returning_sample(code: str = RETURNING_CODE) -> dict:
    """A parcel not collected in time, now on its way back to the sender."""
    return result(
        code,
        [
            event("received", "2026-04-27T23:03:58", "Shipment announced"),
            event("transit", "2026-04-28T15:52:17", "At the sorting facility"),
            event(
                "notified",
                "2026-04-29T09:00:00",
                "Ready for collection",
                post_office={"name": "Post Office Bratislava 1"},
            ),
            event(
                "returning", "2026-05-06T08:00:00", "Undelivered, returning to sender"
            ),
        ],
    )


def returned_sample(code: str = RETURNED_CODE) -> dict:
    """A parcel that completed its return to the sender.

    Shares every event with :func:`returning_sample` up to the terminal one —
    the two carrier states are meant to look alike except for the very last
    event, since collapsing both to canonical RETURNING is the point (see
    CLAUDE.md's status-map trap).
    """
    sample = returning_sample(code)
    sample["events"].append(
        event("returned", "2026-05-08T11:00:00", "Returned to sender")
    )
    return sample


def invalid_format_result(number: str) -> dict:
    """A batch-response entry for a tracking code the carrier rejected."""
    return {"number": number, "status": "invalid_format"}


def empty_events_sample(code: str) -> dict:
    """A syntactically valid code with no history — not yet trackable, or no
    longer retained. Normalises the same as an unrecognised status."""
    return result(code, [])
