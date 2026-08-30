# Monthly snapshot releases (operators)

After each completed UTC month, a GitHub Release tagged
`bitstamp-btcusd-1m-YYYY-MM` holds a full-history join through that month-end.

The daily workflow (`update-automation.yml`) runs `scripts/publish_monthly.py --if-due --publish` after a successful append/validate. On the 1st UTC day, if the updates file already contains the last minute of the previous month, it publishes. On other days it skips. If that tag already exists, the daily job skips instead of failing.

Retry or dry-run from Actions: **Monthly snapshot release** (`workflow_dispatch`) with `year_month` (for example `2026-08`). Leave `publish` unchecked to write `artifacts/monthly-snapshot/` only.

```bash
uv run python scripts/publish_monthly.py --year-month 2026-08 --output-dir artifacts/monthly-snapshot
```

Assets: `btcusd_bitstamp_1min.csv.gz`, `btcusd_bitstamp_1min.parquet` (integer `timestamp`, string OHLC/volume), `btcusd_bitstamp_1min_provenance.csv` (sidecar clipped to the snapshot), `manifest.json`. Snapshot identity is the SHA-256 of the canonical manifest file.

The first tag `bitstamp-btcusd-1m-2026-08` prepends `scripts/first_release_intro.md`. Later months do not.

Do not overwrite an existing tag. Do not rewrite daily `data/` files. Root README documentation is human-owned.
