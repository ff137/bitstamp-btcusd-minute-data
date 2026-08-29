# Provenance sidecar (operators)

The published OHLCV files stay six columns. Operational annotations live in a
separate sparse CSV:

`data/provenance/btcusd_bitstamp_1min.csv`

Schema (half-open `[start_timestamp, end_timestamp)`, Unix seconds, minute
aligned):

```text
start_timestamp,end_timestamp,duration_hours,flag,price_jump,reference
```

`duration_hours` is `(end_timestamp - start_timestamp) / 3600`, two decimal
places. `price_jump` is the absolute percent change between the close just
before the interval and the close at/after it, also two decimal places (empty
when that bound is not in the published OHLCV yet).

Absence of a covering interval means there is no annotation, not that the
market was proved healthy.

## Flags

| Flag | Written when | `reference` |
| --- | --- | --- |
| `source_gap_filled` | The daily updater synthesizes a minute Bitstamp omitted (`fill_missing_minutes`). Going forward only. | `updater:fill_missing_minutes` |
| `confirmed_outage` | Statuspage incident overlapping Trading, REST, Websocket, or Web | Incident shortlink |
| `scheduled_maintenance` | Statuspage scheduled maintenance overlapping those components | Maintenance shortlink |
| `suspected_outage` | Contiguous `volume == 0` run of **12 hours or longer** that does not overlap the three flags above, excluding calendar year 2012 | `zero_volume>=12h` |

Do not publish `suspected_outage` for shorter zero-volume runs. Do not
publish 2012 12h+ zero runs: that quarter is a thin market (38 such stretches
between 2 January and 30 April 2012, about 650 hours), not 38 outages. Do not
reconstruct Kaggle/community historical fills as `source_gap_filled`; those
synthetic minutes are indistinguishable from true zero-volume candles in OHLCV.

Status ingest uses `https://status.bitstamp.net/api/v2/` (incidents and
scheduled-maintenances). Deposit, withdrawal, and altcoin-only components are
ignored so an ADA wallet incident does not annotate BTC/USD. The public
incidents endpoint returns the 50 most recent incidents; rows already in the
sidecar are kept when they age off that window. Component tags are
exchange-wide (a Trading halt on an alt perpetual still matches `Trading`).

Historical 12h+ zero-volume runs after 2012 can still be long synthetic fills
or genuine downtime. They are a heuristic, not official downtime.

## Commands

Refresh fills, Statuspage, and suspected outages (streams historical gzip +
updates; does not rewrite OHLCV):

```bash
uv run python scripts/provenance.py
```

Validate the sidecar:

```bash
uv run python scripts/validate_provenance.py
```

The daily workflow refreshes provenance even when OHLCV is already current,
then `git add`s both the updates CSV and this sidecar.
