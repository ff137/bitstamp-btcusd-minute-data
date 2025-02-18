# Bitstamp BTC/USD 1-minute OHLC Data

> Forked from [mczielinski/kaggle-bitcoin](https://github.com/mczielinski/kaggle-bitcoin)

## Project Overview

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

The daily updates (since the bulk data) are saved in [data/recent/btcusd_bitstamp_1min_latest.csv](data/recent/btcusd_bitstamp_1min_latest.csv).

## Want to Know More?

See [scripts/README.md](scripts/README.md) for more information on how this data was onboarded, and go to
[.github/workflows/update-automation.yml](.github/workflows/update-automation.yml) and
[scripts/update_data.py](scripts/update_data.py) if you are curious about how the data is processed and kept up-to-date.

We hope this repo makes your life easier! If it does, please give us a star! ⭐
