"""Tests for scripts/backfill_typed_parquet.py."""

import csv
import gzip
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.backfill_typed_parquet import main, write_typed_parquet
from scripts.validate_dataset import COLUMN_NAMES

ROWS = [
    ["1577836800", "13345.35117537", "13346.1", "13345.0", "13345.42906843", "0.06"],
    ["1577836860", "13345.42906843", "13345.5", "13344.9", "13345.1", "5853.85216588"],
]


def write_csv_gz(path: Path) -> None:
    with gzip.open(path, mode="wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMN_NAMES)
        writer.writerows(ROWS)


def test_write_typed_parquet_builds_and_verifies(tmp_path: Path) -> None:
    csv_path = tmp_path / "release.csv.gz"
    write_csv_gz(csv_path)
    parquet_path = tmp_path / "typed.parquet"

    assert write_typed_parquet(csv_path, parquet_path) == 2

    table = pq.read_table(parquet_path)
    assert table.schema.field("timestamp").type == pa.int64()
    for name in COLUMN_NAMES[1:]:
        assert table.schema.field(name).type == pa.float64(), name
    assert table.column("timestamp").to_pylist() == [int(row[0]) for row in ROWS]
    assert table.column("close").to_pylist() == [float(row[4]) for row in ROWS]
    assert table.column("volume").to_pylist() == [float(row[5]) for row in ROWS]


def test_main_reports_the_verified_count_and_fails_closed(
    tmp_path: Path, capsys
) -> None:
    csv_path = tmp_path / "release.csv.gz"
    write_csv_gz(csv_path)
    parquet_path = tmp_path / "typed.parquet"

    assert main([str(csv_path), str(parquet_path)]) == 0
    assert "2 rows" in capsys.readouterr().out

    bad = tmp_path / "bad.csv.gz"
    with gzip.open(bad, mode="wt", encoding="utf-8", newline="") as handle:
        handle.write("not,a,valid,header\n")
    assert main([str(bad), str(tmp_path / "bad.parquet")]) == 1
