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
- **Data Integrity:** No duplicates and no null values.

### Data Preview

Below is a preview of the first and last two rows of the bulk dataset:

| timestamp  | open     | high     | low      | close    | volume     |
| ---------- | -------- | -------- | -------- | -------- | ---------- |
| 1325376060 | 4.58     | 4.58     | 4.58     | 4.58     | 0.0        |
| 1325376120 | 4.58     | 4.58     | 4.58     | 4.58     | 0.0        |
| ...        | ...      | ...      | ...      | ...      | ...        |
| 1736207940 | 102280.0 | 102280.0 | 102280.0 | 102280.0 | 0.00755403 |
| 1736208000 | 102278.0 | 102291.0 | 102263.0 | 102263.0 | 0.52310682 |

> `timestamp` is the UTC open time. Each row corresponds to `[timestamp, timestamp + 60s)`.

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
This represents a best-effort attempt at categorising periods of extended zero-volume candles.
It is purely for the convenience of anyone who wants to filter periods of known downtime,
or if you just want to see the extent of flat, zero-volume candles in the dataset.

Quick takeaways:

- The dataset goes back to 2012, when liquidity was very low. We don't try classify these quiet periods.
- 2013 is when liquidity starts being good enough that a 30-minute period of zero-volume candles is rare.
- After March 2013, there are no 4h+ periods of zero-volume candles that does _not_ correspond with a flagged incident. So the data provenance file is good enough to explain all 4h+ outages.
- The longest period of downtime is 108 hours starting 5 January 2015, after a Bitstamp hot-wallet
incident. Apart from that, there were two times that the exchange was not trading for ~1 day.

See [scripts/PROVENANCE.md](scripts/PROVENANCE.md) for more details and notes.

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

We have a [sample script](scripts/inspect_data.py) for you to inspect the data integrity
(validate that there are no missing minutes, no duplicates, no nulls, etc):

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

## Data Licensing

The code in this repository is MIT-licensed (see [LICENSE](LICENSE)); that
file covers code only, not the data. The data terms are recorded in full in
[DATA_LICENSE](DATA_LICENSE):

- The bulk history (2012-01-01 through 2025-01-07) is derived from the Kaggle dataset
  [Bitcoin Historical Data](https://www.kaggle.com/datasets/mczielinski/bitcoin-historical-data)
  by Zielak (mczielinski), dataset version 706, and is
  **CC BY-SA 4.0** with required attribution:
  "Zielak (mczielinski), Bitcoin Historical Data, Kaggle".
- The daily updates (rows from 2025-01-07 onward) are
  collected from the public Bitstamp API by this repository and are
  **CC BY 4.0** with required attribution (suggested form):
  "Bitstamp BTC/USD minute data, github.com/ff137/bitstamp-btcusd-minute-data".
- Combined artifacts, such as the monthly full-history releases, contain
  the Kaggle-derived portion and are therefore **CC BY-SA 4.0**.

The machine-readable acquisition record lives in
[data/original/source-manifest.json](data/original/source-manifest.json).

## Support

If you need any help or have any questions, please feel free to open an issue or contact me directly.

We hope this repo makes your life easier! If it does, please give us a star! ⭐
