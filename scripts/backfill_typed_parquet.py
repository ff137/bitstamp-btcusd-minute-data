"""Write a typed Parquet from a release's csv.gz and prove equivalence.

The bitstamp-btcusd-1m-2026-08 release shipped its Parquet with string
OHLCV columns. The published asset stays as it is; this script builds a
correctly typed companion (timestamp int64, OHLCV float64) from the
release's own csv.gz and refuses to emit it unless
``verify_parquet_matches_csv`` passes — the same fail-closed check the
monthly build runs.
"""

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.publish_monthly import (
    PARQUET_BATCH_SIZE,
    PublishError,
    _flush_parquet_batch,
    _parquet_schema,
    iter_ohlcv_rows,
    verify_parquet_matches_csv,
)


def write_typed_parquet(csv_path: Path, parquet_path: Path) -> int:
    """Build a typed Parquet from ``csv_path`` and return the verified row count."""
    import pyarrow.parquet as pq

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    batch: list[list[str]] = []
    with pq.ParquetWriter(
        parquet_path, _parquet_schema(), compression="zstd"
    ) as writer:
        for row in iter_ohlcv_rows(csv_path):
            if len(row) != 6:
                raise PublishError(f"expected 6 OHLCV columns, found {len(row)}")
            batch.append(row)
            if len(batch) >= PARQUET_BATCH_SIZE:
                _flush_parquet_batch(writer, batch)
                batch = []
        _flush_parquet_batch(writer, batch)
    return verify_parquet_matches_csv(csv_path, parquet_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a typed Parquet from a release csv.gz, fail-closed.",
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("parquet_path", type=Path)
    args = parser.parse_args(argv)
    try:
        rows = write_typed_parquet(args.csv_path, args.parquet_path)
    except PublishError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{args.parquet_path}: {rows} rows, verified against {args.csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
