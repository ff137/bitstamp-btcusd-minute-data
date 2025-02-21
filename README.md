# Bitstamp BTC/USD 1-minute OHLC Data

This repository provides historical and up-to-date Bitcoin (BTC/USD) 1-minute OHLC candle data from Bitstamp.

## Bulk Historical Data

The historical dataset is saved in [data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz](data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz).

Some facts about the data:

- **Date Range:** From 1 January 2012 to 7 January 2025.
- **Number of Records:** 6,846,600
- **File Size:** Approximately 90MB zipped, 326MB unzipped.
- **Data Integrity:** No missing minutes, no duplicates, and no null values.

### Data Preview

Below is a preview of the first and last two rows of the bulk dataset:

| timestamp  | open     | high     | low      | close    | volume   |
| ---------- | -------- | -------- | -------- | -------- | -------- |
| 1325412060 | 4.58     | 4.58     | 4.58     | 4.58     | 0.0      |
| 1325412120 | 4.58     | 4.58     | 4.58     | 4.58     | 0.0      |
| ...        | ...      | ...      | ...      | ...      | ...      |
| 1736207940 | 102280.0 | 102280.0 | 102280.0 | 102280.0 | 0.007554 |
| 1736208000 | 102278.0 | 102291.0 | 102263.0 | 102263.0 | 0.523107 |

## Daily Updates

A daily GitHub action runs at midnight UTC to fetch the latest data and append it to a separate, daily update file.

The daily updates (since the bulk data) are saved in [data/updates/btcusd_bitstamp_1min_latest.csv](data/updates/btcusd_bitstamp_1min_latest.csv).

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

Some time passes and you want to fetch the new daily updates. Perform a force pull:

```bash
git fetch upstream
git reset --hard upstream/main
```

This is needed instead of `git pull`, because the daily update file gets overwritten to keep the git history clean.

## Working with the Data in Python

Assuming you have [Python installed](https://www.python.org/downloads/release/python-3129/),
you can install Poetry and the project dependencies:

```bash
python -m venv venv  # Create a new virtual environment
source venv/bin/activate  # On Windows use `venv\Scripts\activate`

pip install poetry  # Install Poetry
poetry install  # Install the project dependencies
```

We have a [sample script](scripts/inspect_data.py) for you to inspect the data integrity
(validate that there are no missing minutes, no duplicates, no nulls, etc):

```bash
python -m scripts.inspect_data merged
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

See [scripts/README.md](scripts/README.md) for more information on how this data was onboarded.

Go to [scripts/update_data.py](scripts/update_data.py) and
[.github/workflows/update-automation.yml](.github/workflows/update-automation.yml)
if you are curious about how the data is processed and kept up-to-date.

## Support

If you need any help or have any questions, please feel free to open an issue or contact me directly.

We hope this repo makes your life easier! If it does, please give us a star! ⭐
