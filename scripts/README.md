# How to Onboard the Historical Bulk Data

## Original Data

We use version 706 of the original Kaggle dataset:
<https://www.kaggle.com/datasets/mczielinski/bitcoin-historical-data>

Save this in `data/original/btcusd_1-min_data.csv`:

```sh
mkdir -p data/original
curl -L "https://www.kaggle.com/api/v1/datasets/download/mczielinski/bitcoin-historical-data?datasetVersionNumber=706" \
  | funzip > data/original/btcusd_1-min_data.csv
```

Check that you have the same file:

```sh
sha256sum data/original/btcusd_1-min_data.csv
# expected: c3ea6522e69673da38baf88755644d546363e8a96ac60f9e7dafe003c890817f
```

We pin the version and checksum because a newer Kaggle version may contain
different data. The full details are saved in
[data/original/source-manifest.json](../data/original/source-manifest.json).

## Missing Data

Older versions of the Kaggle file had gaps. A collection of those missing rows
was shared here:
<https://github.com/mczielinski/kaggle-bitcoin/issues/2#issuecomment-2577927918>

Version 706 already contains every row from that file, so it is no longer
merged into the historical dataset.

## Checking the Source

This checks the downloaded file and compares a small selection of candles with
the Bitstamp API:

```bash
uv run python scripts/verify_source.py
```

The comparison covers the DST changes from 2012 through 2024, the old
timestamp boundary, and a few known gaps and outages. It does not download the
full history from Bitstamp.

## Processing the Bulk Data

```bash
uv run python scripts/preprocess_bulk_data.py
```

The script reads the Kaggle file one row at a time and copies the data through
`2025-01-07 00:00 UTC`. It changes the column names to lowercase, but does not
shift timestamps, merge the old gap file, or fill any rows.

The result is saved directly in:
`data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz`

## After Processing

Check the result and its connection to the daily updates:

```sh
uv run python scripts/validate_dataset.py \
  --historical-tail data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz \
  --updates data/updates/btcusd_bitstamp_1min_latest.csv \
  --expected-first 1325376060 \
  --expected-last 1736208000 \
  --expected-rows 6847200 \
  --expected-sha256 1be152060b39327b669cbed236eeb283191fadaf3862f76c1e974be54ceb1a20
```

You can inspect it in more detail with:

```bash
uv run python scripts/inspect_data.py bulk
```

Now you're ready to run the update script:

```bash
uv run python scripts/update_data.py
```

This saves Bitstamp data since the bulk historical dataset was last updated in
a separate file at `data/updates/btcusd_bitstamp_1min_latest.csv`.
If Bitstamp leaves out a minute, the current update script keeps the grid
complete with a flat candle at the previous close and zero volume.

Going-forward gap fills and official Statuspage windows are recorded in
`data/provenance/btcusd_bitstamp_1min.csv`. Hour-long unexplained zero-volume
runs in the liquid-regime review window are candidates only; they are
published as `suspected_outage` after corroboration or explicit review, not
by duration alone. See [PROVENANCE.md](PROVENANCE.md).

## Want to Know More?

See [.github/workflows/update-automation.yml](../.github/workflows/update-automation.yml)
for how the data is kept up to date.
