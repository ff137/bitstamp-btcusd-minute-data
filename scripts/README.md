# How to Onboard the Historical Bulk Data

## Original Data

We download the original data from Kaggle:
<https://www.kaggle.com/datasets/mczielinski/bitcoin-historical-data?resource=download&select=btcusd_1-min_data.csv>

Save this in `data/original/btcusd_1-min_data.csv`.

> NB: At time of writing, there is a spurious false record in the very last row of the dataset.
> Manually delete this, or use `sed -i '$ d' btcusd_1-min_data.csv` to remove it.

## Missing Data

The original dataset has missing data, and a collection of gaps has graciously been shared:
<https://github.com/mczielinski/kaggle-bitcoin/issues/2#issuecomment-2577927918>

Extract and save this in `data/original/missing_ohlc_data_all_gaps_as_of_1736148000.csv`.

## Processing the Merged, Bulk Data

1. Follow the above download and save steps
2. Run `python scripts/preprocess_bulk_data.py` to merge the gap data into the bulk data

The script will print out the missing timestamps (known issue), and truncate the data up to the first missing timestamp.

Data integrity is validated (no duplicate timestamps, no missing values),
and finally saved in `data/historical/btcusd_bitstamp_1min_2012-2025.csv`

## After Processing

Inspect the data using `python scripts/inspect_bulk_data.py`.

Zip before uploading to github:

```bash
gzip -k data/historical/btcusd_bitstamp_1min_2012-2025.csv
```

Now you're ready to run the update script:

```bash
python scripts/update_data.py
```

This will save Bitstamp data since the bulk data was last updated in a separate file,
located in `data/updates/btcusd_bitstamp_1min_latest.csv`.
