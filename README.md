# Slovenská Pošta Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-slovenska-posta.svg)](https://github.com/ha-parcel-integrations/ha-slovenska-posta/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Slovenská Pošta](https://www.posta.sk/en/tracking-of-items) (Slovakia) parcels. No account is needed — you enter the tracking code yourself, just like on the Slovenská Pošta website.

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Slovenská Pošta parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `at_pickup_point` / `delivered` / `returning` / …), the carrier's own status text and a tracking deep-link
- Summary sensors: incoming parcels, awaiting pickup, recently delivered parcels
- `slovenska_posta.track_parcel` / `slovenska_posta.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.12 or newer
- A Slovenská Pošta parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-slovenska-posta` as an **Integration**.
3. Install **Slovenská Pošta** and restart Home Assistant.

### Manual

Copy `custom_components/slovenska_posta` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Slovenská Pošta**. There is nothing to fill in: the hub is created immediately (Slovenská Pošta tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`slovenska_posta.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule (quiet overnight window, faster when a parcel is out
for delivery, stopped entirely once nothing is left to track) with nothing to
configure. See [CLAUDE.md](CLAUDE.md) for the details.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Slovenská Pošta → ⋮ → Delete**. Nothing is stored on Slovenská Pošta's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.slovenska_posta_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.slovenska_posta_awaiting_pickup` | Number of parcels currently waiting for collection at a post office |
| `sensor.slovenska_posta_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.slovenska_posta_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.slovenska_posta_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.slovenska_posta_last_successful_update` | Diagnostic: when Slovenská Pošta was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family. Slovenská Pošta's own manual documents six states, which this integration maps onto:

| Status | Meaning |
|---|---|
| `registered` | Announced / received by Slovenská Pošta |
| `in_transit` | In the sorting network |
| `at_pickup_point` | Waiting for you at a post office |
| `delivered` | Delivered |
| `returning` | Undelivered and going back to the sender (covers both the carrier's own "returning" and "returned" states) |
| `unknown` | Not yet scanned, no longer retained, or a status we have not mapped yet |

`out_for_delivery` and `problem` are part of the shared enum but this carrier's documented state set never produces them.

The carrier's own status text is always available as `raw_status`. Slovenská Pošta's public tracking surface exposes no expected delivery window, weight or package dimensions, so those fields — and the entities that depend on them (`next_delivery` sensor, **Deliveries** calendar, `delivery_time_changed` event) — are always empty for this carrier; they're still present because every carrier in the suite gets the same entity set.

## Events

The integration fires these on the event bus (also available as device triggers on the Slovenská Pošta device):

| Event | When |
|---|---|
| `slovenska_posta_parcel_registered` | A new parcel appears in the active list |
| `slovenska_posta_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `slovenska_posta_parcel_delivered` | A parcel is delivered |
| `slovenska_posta_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `slovenska_posta.track_parcel` | `tracking_code` | Start tracking a parcel |
| `slovenska_posta.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.slovenska_posta: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Slovenská Pošta has not scanned it yet, or no longer retains its history. It will pick up automatically once scanned.
- **Adding a parcel fails with "invalid tracking code"** — the carrier's own tracking lookup rejected the code as malformed. Double-check it against the shipping confirmation or missed-delivery card.
- **A status logs "Unrecognised Slovenská Pošta status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-slovenska-posta/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the same public tracking endpoint as the Slovenská Pošta consumer website. It is not affiliated with, endorsed by, or supported by Slovenská Pošta.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
