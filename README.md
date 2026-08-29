# Bitstamp BTC/USD 1-minute OHLC Data

[![Last update](https://img.shields.io/github/last-commit/ff137/bitstamp-btcusd-minute-data/main?path=data%2Fupdates%2Fbtcusd_bitstamp_1min_latest.csv&label=Last%20update)](./data/updates/)
![GitHub repo size](https://img.shields.io/github/repo-size/ff137/bitstamp-btcusd-minute-data)

This repository provides historical and up-to-date Bitcoin (BTC/USD) 1-minute OHLC candle data from Bitstamp.

## Bulk Historical Data

The historical dataset is saved in [data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz](data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz).

Some facts about the data:

- **Date Range:** From 1 January 2012 to 7 January 2025.
- **Number of Records:** 6,847,200
- **File Size:** Approximately 89MB zipped, 327MB unzipped.
- **Data Integrity:** Complete 60-second grid, with no duplicate timestamps or null values.

### Data Preview

Below is a preview of the first and last two rows of the bulk dataset:

| timestamp  | open     | high     | low      | close    | volume     |
| ---------- | -------- | -------- | -------- | -------- | ---------- |
| 1325376060 | 4.58     | 4.58     | 4.58     | 4.58     | 0.0        |
| 1325376120 | 4.58     | 4.58     | 4.58     | 4.58     | 0.0        |
| ...        | ...      | ...      | ...      | ...      | ...        |
| 1736207940 | 102280.0 | 102280.0 | 102280.0 | 102280.0 | 0.00755403 |
| 1736208000 | 102278.0 | 102291.0 | 102263.0 | 102263.0 | 0.52310682 |

> Note: We interpret `timestamp` as the open time of the interval in UTC, so
> each row corresponds to `[timestamp, timestamp + 60s)`. This is backed by
> Bitstamp trade and candle comparisons, but is not an official Bitstamp
> guarantee.

The historical file comes from version 706 of
[Bitcoin Historical Data](https://www.kaggle.com/datasets/mczielinski/bitcoin-historical-data)
by Zielak (mczielinski), licensed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Earlier copies
of this repository had incorrect UTC timestamps before 08:01 UTC on
13 September 2024; those rows have now been corrected. The MIT license in this
repository applies to the code, while the historical data keeps its source
license.

## Daily Updates

A daily GitHub action runs at midnight UTC to fetch the latest data and append it to a separate, daily update file.

The daily updates (since the bulk data) are saved in [data/updates/btcusd_bitstamp_1min_latest.csv](data/updates/btcusd_bitstamp_1min_latest.csv).

## Data Quality and Provenance

We use a data integrity script to guarantee a 1-minute resolution (1 row for every minute), even for missing minutes.
Missing minutes are filled by generating flat candles with zero volume.

This means that zero volume rows can be ambiguous: there could genuinely have been no trades
for that minute (low liquidity); maybe the data provider didn't submit a record for it (data quality issue); or the exchange was down (outage or maintenance).
All of these cases "look the same": a minute candle with zero volume.

That is why we also ship a [data provenance file](data/provenance/btcusd_bitstamp_1min.csv).
Official Bitstamp Statuspage incidents and maintenance (from 2023) are recorded as confirmed
outages or scheduled maintenance. Hour-long unexplained zero-volume runs from March 2013
onwards are reviewed; they appear as `suspected_outage` only after that review or after
corroborating sources. Duration alone does not publish a row. Reviewed rows without a
source are possible data-quality issues or downtime -- not proved outages. Where we found
corroborating sources, the `reference` column points at them; otherwise it is a heuristic
token.

> FYI: The data goes back to 2012, and the first months have very low liquidity. Extended
> zero-volume runs in that period are not labeled. March 2013 is the start of the review
> window, not a claim the book was already liquid. The longest published interval is the
> 108-hour halt starting 09:13 UTC on 5 January 2015, after the Bitstamp hot-wallet
> incident. Daily updates may refresh fills and official Statuspage windows; they do not
> invent new suspected rows.

The provenance file is purely for the convenience of anyone who wants to filter periods of known downtime. It is not an exhaustive list of all outages.
See [scripts/PROVENANCE.md](scripts/PROVENANCE.md) for the schema and operator notes.

## How Can I Use This Data?

The simplest way to use the data is to clone the repository:

```bash
git clone https://github.com/ff137/bitstamp-btcusd-minute-data
cd bitstamp-btcusd-minute-data
```

If you don't have git, you can also [download the repository as a zip file](https://github.com/ff137/bitstamp-btcusd-minute-data/archive/refs/heads/main.zip).
Or, just download the individual datasets:

- [data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz](https://github.com/ff137/bitstamp-btcusd-minute-data/blob/main/data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz)
- [data/updates/btcusd_bitstamp_1min_latest.csv](https://github.com/ff137/bitstamp-btcusd-minute-data/blob/main/data/updates/btcusd_bitstamp_1min_latest.csv)

### Keeping the Data Up-to-Date

Daily updates are ordinary commits. After cloning, fetch new minutes with `git pull`.

Previously (before 2026-08-28), data was kept up-to-date by overwriting the git history.
If your clone still tracks this older workflow, then run this once, and use `git pull` thereafter:

```bash
git fetch upstream
git reset --hard upstream/main
```

## Working with the Data in Python

Assuming you have [Python 3.11+](https://www.python.org/downloads/) and
[uv](https://docs.astral.sh/uv/), install the project dependencies from
the repository root:

```bash
uv sync
```

We have a [sample script](scripts/inspect_data.py) for checking the timestamp
range, duplicate timestamps, null values, and summary statistics:

```bash
uv run python -m scripts.inspect_data merged
```

Replace `merged` with `bulk` or `updated` to inspect the individual bulk or daily datasets.

### Python Template for Loading the Data

If you need a basic template for just loading the data into a single DataFrame:

```python
import pandas as pd

# Load historical and recent data
DATA_DIR = 'data'
df_hist = pd.read_csv(
    f'{DATA_DIR}/historical/btcusd_bitstamp_1min_2012-2025.csv.gz',
    compression='gzip'
)
df_recent = pd.read_csv(
    f'{DATA_DIR}/updates/btcusd_bitstamp_1min_latest.csv'
)

# Combine the datasets
df = pd.concat([df_hist, df_recent], ignore_index=True)
df.info()
```

## Want to Know More About this Repo?

> Forked from [mczielinski/kaggle-bitcoin](https://github.com/mczielinski/kaggle-bitcoin) and fixed some issues.

See [scripts/README.md](scripts/README.md) for more information on how the historical dataset was onboarded.

Daily updates are fetched from the Bitstamp API. Go to [scripts/update_data.py](scripts/update_data.py) and
[.github/workflows/update-automation.yml](.github/workflows/update-automation.yml)
if you are curious about how the data is processed and kept up-to-date.

## Support

If you need any help or have any questions, please feel free to open an issue or contact me directly.

We hope this repo makes your life easier! If it does, please give us a star! ⭐
