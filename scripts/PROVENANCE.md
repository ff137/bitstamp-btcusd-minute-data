# Provenance sidecar

Unexplained zero-volume minutes are recorded in a sparse CSV, separate from
the six-column OHLCV files:

[`data/provenance/btcusd_bitstamp_1min.csv`](../data/provenance/btcusd_bitstamp_1min.csv)

The schema is as follows:

```text
start_timestamp, end_timestamp, duration_hours, flag, price_jump, reference
```

Intervals are half-open `[start_timestamp, end_timestamp)`, Unix seconds,
minute aligned. `duration_hours` is `(end_timestamp - start_timestamp) / 3600`,
two decimal places. `price_jump` is the absolute percent change between the
close just before the interval and the close at/after it, two decimal places
(empty when that bound is not in the published OHLCV yet).

Absence of a covering interval means there is no annotation, not that the
market was proved healthy. A `volume == 0` candle can be genuine no-trade
minutes, an omitted provider row, a synthetic fill, or downtime — OHLCV
cannot tell them apart.

## Flags

| Flag | Written when | `reference` |
| --- | --- | --- |
| `suspected_outage` | A liquid-regime `volume == 0` run of **60 minutes or longer** that has been corroborated or explicitly reviewed. Duration alone never publishes this flag. | `zero_volume>=60m` after review, or source URL(s) when corroborated |
| `confirmed_outage` | Statuspage incident overlapping Trading, REST, Websocket, or Web | Incident shortlink |
| `scheduled_maintenance` | Statuspage scheduled maintenance overlapping those components | Maintenance shortlink |
| `source_gap_filled` | The daily updater synthesizes a minute Bitstamp omitted (`fill_missing_minutes`) | `updater:fill_missing_minutes` |

`reviewed_unconfirmed` suspected rows are unexplained silence / possible
data-quality issues, not proved outages. `corroborated` rows have qualifying
URL evidence. Both require a reviewer, decision date, and note in the
research ledger.

The liquid-regime *review* floor starts at the first month of a six-month
streak whose inter-trade-gap p99 is at most 30 minutes (frozen as 2013-03-01
in `data/provenance/research/model.json`). That date is the start of the
review window, not a claim the book was already liquid. Earlier thin-book
runs, including 2012, are not published. See
[PROVENANCE_RESEARCH.md](PROVENANCE_RESEARCH.md).

Status ingest uses `https://status.bitstamp.net/api/v2/` (incidents and
scheduled-maintenances). Deposit, withdrawal, and altcoin-only components are
ignored so an ADA wallet incident does not annotate BTC/USD. The public
incidents endpoint returns the 50 most recent incidents; rows already in the
sidecar are kept when they age off that window.

Candidate discovery is manual. The frozen research snapshot is the prefix of
OHLCV through `model.json`'s `as_of_timestamp`. Appending later daily candles
does not change that identity; rewriting a candle inside the prefix does.

## Commands

Refresh fills, Statuspage, and price jumps. Suspected rows come only from
reviewed ledger states (`corroborated`, `reviewed_unconfirmed`). This does
not discover new duration-only rows and does not rewrite OHLCV or the
research snapshot:

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
