# Provenance sidecar

Extended periods of zero-volume minutes are recorded in a sparse CSV, separate from the main datasets:

[`data/provenance/btcusd_bitstamp_1min.csv`](../data/provenance/btcusd_bitstamp_1min.csv)

```text
start_timestamp, end_timestamp, duration_minutes, flag, price_jump, reference
```

Intervals are `[start_timestamp, end_timestamp)`, minute aligned.
`duration_minutes` is the integer `(end_timestamp - start_timestamp) / 60`.
`price_jump` is the absolute percent change between the close just before the interval and the close at/after it.

## Flags

Official Bitstamp incidents (from 2023) are recorded as confirmed outages or scheduled maintenance.
All other periods of 1h+ of flat candles was also reviewed, and where corroborating evidence was found,
those are labelled as a suspected outage.

> NB: The data goes back to 2012, and the first months have very low liquidity. The extended
> zero-volume runs in that period are not labeled. March 2013 is when liquidity is identified to be good enough
> that a 1h zero-volume run gets flagged as a candidate for suspected outage.

After March 2013, there are no 4h+ periods of downtime that do _not_ correspond with a flagged incident.

The longest published interval is a 108-hour halt starting 09:13 UTC on 5 January 2015, after the Bitstamp hot-wallet
incident.

There are some 1h+ periods of downtime that we cannot be sure what the reason is, so we do not flag those.
Therefore, the list is not exhaustive, and is just intended to be convenient for those that want to filter
periods of known downtime. If all periods of downtime matter for you, then you are free to check for yourself and filter more rigorously.

## Schema

| Flag | Written when | `reference` |
| --- | --- | --- |
| `suspected_outage` | Continuous zero-volume run of **60 minutes or longer** that has been corroborated or explicitly reviewed | `zero_volume>=60m` after review, or source URL(s) when corroborated |
| `confirmed_outage` | Statuspage incident overlapping Trading, REST, Websocket, or Web | Incident shortlink |
| `scheduled_maintenance` | Statuspage scheduled maintenance overlapping those components | Maintenance shortlink |
| `source_gap_filled` | The daily updater synthesizes a minute Bitstamp omitted (`fill_missing_minutes`) | `updater:fill_missing_minutes` |

`reviewed_unconfirmed` means unexplained silence, not proved downtime.
`corroborated` rows have qualifying URL evidence. Both require a reviewer,
decision date, and a note in [`NOTES.md`](../data/provenance/research/NOTES.md).

The sufficient-liquidity review floor starts 2013-03-01 (first month of a six-month
streak where the inter-trade-gap p99 is at most 30 minutes, frozen in
`data/provenance/research/model.json`). That date is the start of the review
window. Earlier thin-book runs, including 2012, are not published.

Status ingest uses `https://status.bitstamp.net/api/v2/`. Deposit, withdrawal,
and altcoin-only components are ignored. The public incidents endpoint returns
the 50 most recent incidents; rows already in the sidecar are kept when they
age off that window.

## Commands

Refresh fills, Statuspage, and price jumps:

```bash
uv run python scripts/provenance.py
```

Regenerate the candidate ledger and frozen model (local investigation; not
part of the daily Action):

```bash
uv run python scripts/outage_candidates.py --apply-decisions
```

Validate the sidecar and ledger against current candles:

```bash
uv run python scripts/validate_provenance.py
uv run python scripts/validate_ledger.py
```

The daily workflow refreshes provenance even when OHLCV is already current,
then `git add`s the updates CSV and this sidecar. It must not mutate
`data/provenance/research/`.
