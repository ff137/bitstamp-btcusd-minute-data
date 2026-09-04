"""Write a typed Parquet from a release's csv.gz and prove equivalence.

The bitstamp-btcusd-1m-2026-08 release shipped its Parquet with string
OHLCV columns. The published asset stays as it is; this script builds a
correctly typed companion (timestamp int64, OHLCV float64) from the
release's own csv.gz. It writes to a temporary sibling path and moves
the file to the requested output only after
``verify_parquet_matches_csv`` — the same fail-closed check the monthly
build runs — has passed, so a failed run leaves nothing at the output
path (and never disturbs a file already there).
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
    """Build a typed Parquet from ``csv_path`` and return the verified row count.

    Nothing reaches ``parquet_path`` unless verification passes: the file
    is written to a temporary sibling, verified there, and renamed into
    place, so a failure leaves the output path exactly as it was.
    """
    import os

    import pyarrow.parquet as pq

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = parquet_path.with_name(parquet_path.name + ".unverified")
    batch: list[list[str]] = []
    try:
        with pq.ParquetWriter(
            staging_path, _parquet_schema(), compression="zstd"
        ) as writer:
            for row in iter_ohlcv_rows(csv_path):
                if len(row) != 6:
                    raise PublishError(f"expected 6 OHLCV columns, found {len(row)}")
                batch.append(row)
                if len(batch) >= PARQUET_BATCH_SIZE:
                    _flush_parquet_batch(writer, batch)
                    batch = []
            _flush_parquet_batch(writer, batch)
        row_count = verify_parquet_matches_csv(csv_path, staging_path)
    except BaseException:
        staging_path.unlink(missing_ok=True)
        raise
    os.replace(staging_path, parquet_path)
    return row_count


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
