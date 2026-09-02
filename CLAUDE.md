# Working in this repository

Home Assistant custom integration for **Slovenská Pošta** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **If this carrier can reach `ParcelStatus.AT_PICKUP_POINT` from a real raw
  status/code**, it needs an `awaiting_pickup` sensor — see *Parcel contract*
  in `CONVENTIONS.md`. Say "pickup point", not "ServicePoint"/"parcel
  shop"/"locker", for the generic concept. `ha-dhl-nl`, `ha-dpd`, `ha-gls`,
  `ha-inpost` are reference implementations; `slovenska_posta` here reaches
  `AT_PICKUP_POINT` via the carrier's own `notified` state and has the
  `awaiting_pickup` sensor.

## Carrier-specific notes

**API mechanics live in `carrier-research/slovenska-posta/` (private research
repo)** — the endpoint, auth (none), the batch envelope shape, the full
official status vocabulary and its `ParcelStatus` mapping, and the timestamp
format. The research doc's own `### Endpoint & auth` / `### Payload` /
`### Status vocabulary` sections are the source of truth; `api/tracking.md`
is a short supplementary quick-reference, not a second copy to keep in sync.
Not duplicated here; this section is integration-level decisions only.

The tracking surface is a single public, keyless, batchable JSON endpoint —
one `q=` call carries every tracked code, comma-separated, up to 100 per
request (chunked above that). No account, no per-parcel HTTP round trip.

- **Batched fetch, not per-code.** `api.py`'s `async_get_parcels` and
  `coordinator.py`'s `_async_update_data` deliberately diverge from the
  per-code-gather pattern most account-less carriers in this suite use: one
  batched request per poll, matched back to tracked codes by each result's
  own `number` field (never by array position — nothing guarantees the
  response preserves request order). A network/API failure fails the *whole*
  poll (falls back to cache, same as any carrier), not one parcel at a time —
  there's only one HTTP call to fail.
- **`invalid_format` is not "not found".** The carrier's own per-code
  `status` field distinguishes a malformed tracking code (`invalid_format`)
  from a syntactically valid one with no history yet (`ok` + empty
  `events[]`). `api.py` drops `invalid_format` entries before they reach the
  coordinator — one must never become a normalised parcel with
  `status: unknown`. The options flow additionally runs a **live check**
  against the batch endpoint for newly added codes only (not already-tracked
  ones, and skipped outright if the carrier is unreachable) so a bad code is
  rejected as a form error at entry time rather than silently sitting there
  showing `unknown` forever.
- **No separate `problem` state.** The manual's state set (`received`,
  `transit`, `notified`, `delivered`, `returning`, `returned`) has no
  exception/problem entry and none has been observed live. An unmapped
  future code falls to `unknown` plus the one-shot warning — do not guess a
  `problem` mapping from a `detailCode` nobody has confirmed.
- **`returning`/`returned` collapse to one canonical status.** Both map to
  `ParcelStatus.RETURNING`; `raw_status` always leads with the carrier's own
  `stateCode` (never just the detail text) specifically so this distinction
  survives if a future split is ever needed.
- **This surface has no ETA, weight, dimensions, sender or receiver.**
  `planned_from`/`planned_to`/`weight`/`dimensions`/`sender`/`receiver` are
  `None` on every parcel, not just some — `const.py`'s `CAPABILITIES` is
  `{"pickup_point", "url", "history"}` accordingly. The suite-wide
  `next_delivery` sensor, **Deliveries** calendar and
  `delivery_time_changed` event still exist (same entity set every carrier
  gets) but stay empty/silent here.
- **Tracking-code format is intentionally unvalidated locally.** The manual
  doesn't document a code shape, so `config_flow.py`'s `valid_tracking_code`
  is a generic 8–20 character length cap plus a control-character reject —
  not a guessed prefix/checksum rule. The live check above is what actually
  enforces the real shape, deferring to the carrier instead of a local guess.
- **`AT_PICKUP_POINT` is reachable** (`notified` → post-office hold), so this
  carrier has the suite's standard `awaiting_pickup` sensor.
- **`pickup_point` is gated on `status is AT_PICKUP_POINT`, not just "does
  the latest event carry a `postOffice`"** — matching GLS/DHL-NL. A
  delivered or returned parcel's last event still carries its own
  `postOffice` (the delivering/returning office), which is not a place the
  recipient can go collect anything; only the `notified` state's post office
  is a real pickup point.
- **Timestamps are Slovak local time, not UTC.** Every event's `localDate` is
  naive and carries no offset; `parcels.py`'s `parse_local_date` attaches
  `Europe/Bratislava` (DST-aware) rather than treating it as UTC like the
  suite's generic `to_iso_timestamp` would.
- **`url` is the current public tracker's own English deep link**
  (`https://www.posta.sk/en/tracking-of-items#parcel=<code>`), confirmed
  live — not the legacy `tandt.posta.sk/zasielky/<code>` path, which still
  redirects there but is one hop slower for no benefit now the current path
  is known.
- **Never call `https://api.posta.sk/private/web/track`.** It currently
  answers unauthenticated too, but is undocumented, carries a different
  `{parcels[]}` schema (epoch-millisecond timestamps, bilingual description
  objects) and could change or disappear without notice. `/tracking` is the
  only supported contract.

## Options and reloads

For code-based carriers, the options flow starts with exactly `Pakketten` and
`Instellingen`. `Pakketten` is one editable multi-code list; `Instellingen` is
a flat form. Changes apply without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  calls `async_request_refresh()`, so added/removed parcel sensors appear
  immediately (this is also the resume path after polling has fully
  suspended — see "Dynamic polling" below).
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Dynamic polling

There is no user-facing polling interval — this is a deliberate suite-wide
choice, not a gap. `coordinator.py` recomputes `update_interval` at the end of
every refresh:

- **Quiet window:** no polling 00:00–06:00 local time, except two daily
  anchors (~00:00 and ~06:00) for overnight / end-of-day catch-up.
- **Tiers while polling:** *hot* (15 min) when a tracked, not-yet-delivered
  parcel is `out_for_delivery` within an hour of its `planned_from` (or has no
  `planned_from` at all); *mid* (45 min) for anything else still in flight —
  `problem`/`returning` included, deliberately not hot. Account-based carriers
  never fully stop even with nothing hot or in transit: the mid-tier poll is
  also how a new shipment gets discovered.
- **Full stop (account-less carriers only):** `update_interval = None` when
  nothing is tracked or every tracked parcel is delivered. Resumes the moment
  a parcel is added back, via the options-flow refresh above.
- **Stagger:** a small, stable per-install offset (hash of the config entry
  id) is added to every computed interval so installs don't all hit an anchor
  or tier boundary at the same second.
- **429 backoff:** a 429 anywhere in a poll raises `UpdateFailed` with
  `retry_after` — the carrier's own `Retry-After` header if present, otherwise
  an exponential backoff tracked per-coordinator. `api.py`'s
  `…ApiError.status_code` / `.retry_after` carry this from the HTTP layer.

A carrier that genuinely throttles or soft-bans traffic harder than the 429
backoff handles is a documented, local divergence from this in that one
repo's own `CLAUDE.md` — not a generator flag.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. This carrier's fetch is one batched call, not a per-parcel
gather (see *Carrier-specific notes*) — `aiohttp.ClientError`/`SlovenskaPostaApiError`
are caught **around that one call** in `_async_update_data`, and a failure falls
back to cached data per tracked code rather than failing every parcel
individually. Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.slovenska_posta
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
