"""Tests for scripts/validate_dataset.py."""

import csv
import gzip
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_dataset import (
    COLUMN_NAMES,
    MAX_ISSUES,
    DatasetSummary,
    expected_summary_issues,
    validate_csv_rows,
    validate_dataset,
    validate_historical_and_updates,
    validate_seam,
    validate_sha256,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HISTORICAL_TAIL = FIXTURES / "historical_tail.csv"
UPDATES_HEAD = FIXTURES / "updates_head.csv"
REAL_UPDATES = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "updates"
    / "btcusd_bitstamp_1min_latest.csv"
)


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(row)


def test_valid_fixtures_pass() -> None:
    historical = validate_dataset(HISTORICAL_TAIL)
    updates = validate_dataset(UPDATES_HEAD)

    assert historical.valid
    assert updates.valid
    assert historical.summary.row_count == 5
    assert historical.summary.last_timestamp == 1736208000
    assert updates.summary.first_timestamp == 1736208060


def test_valid_seam_between_fixtures() -> None:
    historical, updates, seam_issues = validate_historical_and_updates(
        HISTORICAL_TAIL,
        UPDATES_HEAD,
    )
    assert historical.valid
    assert updates.valid
    assert seam_issues == ()


def test_real_updates_passes_with_fixture_tail() -> None:
    historical, updates, seam_issues = validate_historical_and_updates(
        HISTORICAL_TAIL,
        REAL_UPDATES,
    )
    assert historical.valid
    assert updates.valid
    assert seam_issues == ()
    assert updates.summary.row_count > 0


def test_exact_schema_header_required(tmp_path: Path) -> None:
    path = tmp_path / "bad_header.csv"
    write_csv(path, [["time", "o", "h", "l", "c", "v"]])

    result = validate_dataset(path)

    assert not result.valid
    assert any("invalid header" in issue.message for issue in result.issues)


def test_malformed_row_column_count(tmp_path: Path) -> None:
    path = tmp_path / "short_row.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208060", "1.0", "1.0", "1.0"],
        ],
    )

    result = validate_dataset(path)

    assert not result.valid
    assert any("expected 6 columns" in issue.message for issue in result.issues)


def test_missing_value_rejected(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208060", "", "1.0", "1.0", "1.0", "0.0"],
        ],
    )

    result = validate_dataset(path)

    assert not result.valid
    assert any("invalid numeric value" in issue.message for issue in result.issues)


def test_extra_column_rejected(tmp_path: Path) -> None:
    path = tmp_path / "extra.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208060", "1.0", "1.0", "1.0", "1.0", "0.0", "extra"],
        ],
    )

    result = validate_dataset(path)

    assert not result.valid
    assert any("expected 6 columns" in issue.message for issue in result.issues)


def test_non_finite_values_rejected(tmp_path: Path) -> None:
    for bad_value in ("NaN", "nan", "inf", "-inf", "Infinity"):
        path = tmp_path / f"nonfinite_{bad_value}.csv"
        write_csv(
            path,
            [
                list(COLUMN_NAMES),
                ["1736208060", bad_value, "1.0", "1.0", "1.0", "0.0"],
            ],
        )
        result = validate_dataset(path)
        assert not result.valid
        assert any("invalid numeric value" in issue.message for issue in result.issues)


def test_decimal_parsing_accepts_exact_values(tmp_path: Path) -> None:
    path = tmp_path / "decimal.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208060", "102228.0", "102228.0", "102228.0", "102228.0", "0"],
        ],
    )

    result = validate_dataset(path)

    assert result.valid


def test_non_positive_ohlc_rejected(tmp_path: Path) -> None:
    cases = [
        ("open", ["1736208060", "0", "1.0", "1.0", "1.0", "0.0"]),
        ("high", ["1736208060", "1.0", "-1.0", "1.0", "1.0", "0.0"]),
        ("low", ["1736208060", "1.0", "1.0", "0.0", "1.0", "0.0"]),
        ("close", ["1736208060", "1.0", "1.0", "1.0", "-0.1", "0.0"]),
    ]
    for label, row in cases:
        path = tmp_path / f"bad_{label}.csv"
        write_csv(path, [list(COLUMN_NAMES), row])
        result = validate_dataset(path)
        assert not result.valid
        assert any(
            f"{label} must be strictly positive" in issue.message
            for issue in result.issues
        )


def test_negative_volume_rejected(tmp_path: Path) -> None:
    path = tmp_path / "negative_volume.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208060", "1.0", "1.0", "1.0", "1.0", "-0.01"],
        ],
    )

    result = validate_dataset(path)

    assert not result.valid
    assert any(
        "volume must be non-negative" in issue.message for issue in result.issues
    )


def test_ohlc_envelope_violations(tmp_path: Path) -> None:
    cases = [
        ("low > open", ["1736208060", "1.0", "2.0", "1.5", "1.0", "0.0"]),
        ("low > close", ["1736208060", "1.0", "2.0", "1.0", "0.5", "0.0"]),
        ("high < open", ["1736208060", "2.0", "1.5", "1.0", "1.0", "0.0"]),
        ("high < close", ["1736208060", "1.0", "1.5", "1.0", "2.0", "0.0"]),
    ]
    for label, row in cases:
        path = tmp_path / f"envelope_{label.replace(' ', '_')}.csv"
        write_csv(path, [list(COLUMN_NAMES), row])
        result = validate_dataset(path)
        assert not result.valid, label


def test_timestamp_must_be_unsigned_integer(tmp_path: Path) -> None:
    for bad_timestamp in ("1736208060.0", "-60", "1e3", " 1736208060", "abc"):
        path = tmp_path / f"bad_ts_{bad_timestamp.replace(' ', '_')}.csv"
        write_csv(
            path,
            [
                list(COLUMN_NAMES),
                [bad_timestamp, "1.0", "1.0", "1.0", "1.0", "0.0"],
            ],
        )
        result = validate_dataset(path)
        assert not result.valid
        assert any("invalid timestamp" in issue.message for issue in result.issues)


def test_timestamp_must_be_minute_aligned(tmp_path: Path) -> None:
    path = tmp_path / "unaligned.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208061", "1.0", "1.0", "1.0", "1.0", "0.0"],
        ],
    )

    result = validate_dataset(path)

    assert not result.valid
    assert any("minute-aligned" in issue.message for issue in result.issues)


def test_duplicate_timestamp_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208060", "1.0", "1.0", "1.0", "1.0", "0.0"],
            ["1736208060", "1.0", "1.0", "1.0", "1.0", "0.0"],
        ],
    )

    result = validate_dataset(path)

    assert not result.valid
    assert any("duplicate timestamp" in issue.message for issue in result.issues)


def test_out_of_order_timestamp_rejected(tmp_path: Path) -> None:
    path = tmp_path / "out_of_order.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208120", "1.0", "1.0", "1.0", "1.0", "0.0"],
            ["1736208060", "1.0", "1.0", "1.0", "1.0", "0.0"],
        ],
    )

    result = validate_dataset(path)

    assert not result.valid
    assert any("out-of-order timestamp" in issue.message for issue in result.issues)


def test_timestamp_gap_rejected(tmp_path: Path) -> None:
    path = tmp_path / "gap.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208060", "1.0", "1.0", "1.0", "1.0", "0.0"],
            ["1736208180", "1.0", "1.0", "1.0", "1.0", "0.0"],
        ],
    )

    result = validate_dataset(path)

    assert not result.valid
    assert any("timestamp gap" in issue.message for issue in result.issues)


def test_empty_file_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")

    result = validate_dataset(path)

    assert not result.valid
    assert any("empty" in issue.message for issue in result.issues)


def test_header_only_rejected(tmp_path: Path) -> None:
    path = tmp_path / "header_only.csv"
    write_csv(path, [list(COLUMN_NAMES)])

    result = validate_dataset(path)

    assert not result.valid
    assert any("no data rows" in issue.message for issue in result.issues)


def test_flat_zero_volume_candle_accepted(tmp_path: Path) -> None:
    path = tmp_path / "flat_zero_volume.csv"
    write_csv(
        path,
        [
            list(COLUMN_NAMES),
            ["1736208060", "100.0", "100.0", "100.0", "100.0", "0"],
        ],
    )

    result = validate_dataset(path)

    assert result.valid


def test_gzip_csv_supported(tmp_path: Path) -> None:
    path = tmp_path / "valid.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMN_NAMES)
        writer.writerow(["1736208060", "100.0", "100.0", "100.0", "100.0", "0"])

    result = validate_dataset(path)

    assert result.valid


def test_seam_mismatch_detected() -> None:
    issues = validate_seam(
        DatasetSummary("historical", 1, 1736208000, 1736208000),
        DatasetSummary("updates", 1, 1736208180, 1736208180),
    )

    assert issues
    assert any("seam mismatch" in issue.message for issue in issues)


def test_issue_cap_enforced() -> None:
    rows = [list(COLUMN_NAMES)]
    timestamp = 1736208060
    for _ in range(MAX_ISSUES + 5):
        rows.append([str(timestamp), "0", "1.0", "1.0", "1.0", "0.0"])
        timestamp += 60

    result = validate_csv_rows(rows[1:], path="synthetic")

    assert not result.valid
    assert len(result.issues) == MAX_ISSUES


def test_cli_success(capsys: pytest.CaptureFixture[str]) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_dataset.py",
            "--historical-tail",
            str(HISTORICAL_TAIL),
            "--updates",
            str(UPDATES_HEAD),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "seam ok" in completed.stdout
    assert completed.stderr == ""


def test_cli_failure_reports_to_stderr(tmp_path: Path) -> None:
    bad_updates = tmp_path / "bad_updates.csv"
    write_csv(
        bad_updates,
        [
            list(COLUMN_NAMES),
            ["1736208180", "1.0", "1.0", "1.0", "1.0", "0.0"],
        ],
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_dataset.py",
            "--historical-tail",
            str(HISTORICAL_TAIL),
            "--updates",
            str(bad_updates),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "seam validation failed" in completed.stderr
    assert "seam mismatch" in completed.stderr


def test_expected_summary_issues_detect_extent_mismatch() -> None:
    summary = DatasetSummary("historical", 5, 1736207760, 1736208000)
    issues = expected_summary_issues(
        summary,
        expected_first=1325376060,
        expected_last=1736208000,
        expected_rows=6847200,
    )
    messages = [issue.message for issue in issues]
    assert any("expected 6847200 historical rows" in message for message in messages)
    assert any("expected first timestamp" in message for message in messages)


def test_cli_expected_summary_flags() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_dataset.py",
            "--historical-tail",
            str(HISTORICAL_TAIL),
            "--updates",
            str(UPDATES_HEAD),
            "--expected-first",
            "1736207760",
            "--expected-last",
            "1736208000",
            "--expected-rows",
            "5",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_validate_sha256() -> None:
    expected = hashlib.sha256(HISTORICAL_TAIL.read_bytes()).hexdigest()
    assert validate_sha256(HISTORICAL_TAIL, expected) is None
    issue = validate_sha256(HISTORICAL_TAIL, "0" * 64)
    assert issue is not None
    assert "SHA-256 mismatch" in issue.message


def test_cli_expected_checksum_failure() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_dataset.py",
            "--historical-tail",
            str(HISTORICAL_TAIL),
            "--updates",
            str(UPDATES_HEAD),
            "--expected-sha256",
            "0" * 64,
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "historical checksum mismatch" in completed.stderr


def test_cli_file_validation_failure(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.csv"
    write_csv(
        bad_file,
        [
            list(COLUMN_NAMES),
            ["1736208060", "0", "1.0", "1.0", "1.0", "0.0"],
        ],
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_dataset.py",
            "--historical-tail",
            str(bad_file),
            "--updates",
            str(UPDATES_HEAD),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "validation failed" in completed.stderr
