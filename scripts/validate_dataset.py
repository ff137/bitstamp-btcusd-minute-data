"""Streaming validator for Bitstamp BTC/USD one-minute OHLC CSV datasets."""

from __future__ import annotations

import argparse
import csv
import gzip
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, TextIO

EXPECTED_HEADER = "timestamp,open,high,low,close,volume"
COLUMN_NAMES = ("timestamp", "open", "high", "low", "close", "volume")
MAX_ISSUES = 20
MINUTE_SECONDS = 60


@dataclass(frozen=True)
class ValidationIssue:
    message: str
    row_number: int | None = None


@dataclass(frozen=True)
class DatasetSummary:
    path: str
    row_count: int
    first_timestamp: int | None
    last_timestamp: int | None


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    summary: DatasetSummary
    issues: tuple[ValidationIssue, ...]


def open_dataset(path: str | Path) -> TextIO:
    """Open a plain or gzip-compressed CSV for text reading."""
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8", newline="")
    return path.open(mode="r", encoding="utf-8", newline="")


def _add_issue(
    issues: list[ValidationIssue],
    *,
    row_number: int | None,
    message: str,
) -> None:
    if len(issues) < MAX_ISSUES:
        issues.append(ValidationIssue(message=message, row_number=row_number))


def _require_field(raw: str) -> str:
    if raw != raw.strip():
        raise ValueError("value must not include leading or trailing whitespace")
    if not raw:
        raise ValueError("value must be present")
    return raw


def _parse_timestamp(raw: str) -> int:
    value = _require_field(raw)
    if not value.isdigit():
        raise ValueError("timestamp must be an unsigned base-10 integer")
    timestamp = int(value)
    if timestamp % MINUTE_SECONDS != 0:
        raise ValueError("timestamp must be minute-aligned (multiple of 60)")
    return timestamp


def _parse_decimal(raw: str) -> Decimal:
    value = _require_field(raw)
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("value is not a valid decimal number") from exc
    if not number.is_finite():
        raise ValueError("value must be finite")
    return number


def validate_csv_rows(
    rows: Iterable[list[str]],
    *,
    path: str,
) -> ValidationResult:
    """Validate dataset rows from a CSV reader with O(1) row memory."""
    issues: list[ValidationIssue] = []
    row_count = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    previous_timestamp: int | None = None

    for row_number, row in enumerate(rows, start=1):
        if len(issues) >= MAX_ISSUES:
            break

        if len(row) != len(COLUMN_NAMES):
            _add_issue(
                issues,
                row_number=row_number,
                message=(
                    f"expected {len(COLUMN_NAMES)} columns "
                    f"({', '.join(COLUMN_NAMES)}), found {len(row)}"
                ),
            )
            continue

        timestamp_raw, open_raw, high_raw, low_raw, close_raw, volume_raw = row

        try:
            timestamp = _parse_timestamp(timestamp_raw)
        except ValueError as exc:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"invalid timestamp: {exc}",
            )
            continue

        if previous_timestamp is None:
            first_timestamp = timestamp
        elif timestamp == previous_timestamp:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"duplicate timestamp {timestamp}",
            )
        elif timestamp < previous_timestamp:
            _add_issue(
                issues,
                row_number=row_number,
                message=(
                    f"out-of-order timestamp {timestamp} "
                    f"(previous {previous_timestamp})"
                ),
            )
        elif timestamp != previous_timestamp + MINUTE_SECONDS:
            _add_issue(
                issues,
                row_number=row_number,
                message=(
                    f"timestamp gap: expected {previous_timestamp + MINUTE_SECONDS}, "
                    f"found {timestamp}"
                ),
            )

        try:
            open_price = _parse_decimal(open_raw)
            high_price = _parse_decimal(high_raw)
            low_price = _parse_decimal(low_raw)
            close_price = _parse_decimal(close_raw)
            volume = _parse_decimal(volume_raw)
        except ValueError as exc:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"invalid numeric value: {exc}",
            )
            continue

        if open_price <= 0:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"open must be strictly positive, found {open_raw}",
            )
        if high_price <= 0:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"high must be strictly positive, found {high_raw}",
            )
        if low_price <= 0:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"low must be strictly positive, found {low_raw}",
            )
        if close_price <= 0:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"close must be strictly positive, found {close_raw}",
            )
        if volume < 0:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"volume must be non-negative, found {volume_raw}",
            )

        if low_price > open_price:
            _add_issue(
                issues,
                row_number=row_number,
                message="low must be <= open",
            )
        if low_price > close_price:
            _add_issue(
                issues,
                row_number=row_number,
                message="low must be <= close",
            )
        if high_price < open_price:
            _add_issue(
                issues,
                row_number=row_number,
                message="high must be >= open",
            )
        if high_price < close_price:
            _add_issue(
                issues,
                row_number=row_number,
                message="high must be >= close",
            )

        row_count += 1
        last_timestamp = timestamp
        previous_timestamp = timestamp

    if row_count == 0 and not issues:
        _add_issue(issues, row_number=None, message="dataset contains no data rows")

    valid = not issues
    return ValidationResult(
        valid=valid,
        summary=DatasetSummary(
            path=path,
            row_count=row_count,
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
        ),
        issues=tuple(issues),
    )


def validate_dataset(path: str | Path) -> ValidationResult:
    """Validate a CSV dataset file, including optional gzip compression."""
    path = Path(path)
    issues: list[ValidationIssue] = []

    try:
        with open_dataset(path) as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                _add_issue(
                    issues,
                    row_number=None,
                    message="file is empty (missing header)",
                )
                return ValidationResult(
                    valid=False,
                    summary=DatasetSummary(
                        path=str(path),
                        row_count=0,
                        first_timestamp=None,
                        last_timestamp=None,
                    ),
                    issues=tuple(issues),
                )

            if header != list(COLUMN_NAMES):
                _add_issue(
                    issues,
                    row_number=None,
                    message=(
                        f"invalid header: expected {EXPECTED_HEADER!r}, "
                        f"found {','.join(header)!r}"
                    ),
                )
                return ValidationResult(
                    valid=False,
                    summary=DatasetSummary(
                        path=str(path),
                        row_count=0,
                        first_timestamp=None,
                        last_timestamp=None,
                    ),
                    issues=tuple(issues),
                )

            result = validate_csv_rows(reader, path=str(path))
    except OSError as exc:
        _add_issue(issues, row_number=None, message=f"cannot read file: {exc}")
        return ValidationResult(
            valid=False,
            summary=DatasetSummary(
                path=str(path),
                row_count=0,
                first_timestamp=None,
                last_timestamp=None,
            ),
            issues=tuple(issues),
        )

    return result


def validate_seam(
    historical: DatasetSummary,
    updates: DatasetSummary,
) -> tuple[ValidationIssue, ...]:
    """Validate that updates begin exactly one minute after the historical tail."""
    issues: list[ValidationIssue] = []

    if historical.last_timestamp is None:
        _add_issue(
            issues,
            row_number=None,
            message="historical tail has no rows for seam validation",
        )
    if updates.first_timestamp is None:
        _add_issue(
            issues,
            row_number=None,
            message="updates file has no rows for seam validation",
        )

    if (
        historical.last_timestamp is not None
        and updates.first_timestamp is not None
        and updates.first_timestamp != historical.last_timestamp + MINUTE_SECONDS
    ):
        _add_issue(
            issues,
            row_number=None,
            message=(
                "seam mismatch: expected updates to start at "
                f"{historical.last_timestamp + MINUTE_SECONDS}, "
                f"found {updates.first_timestamp}"
            ),
        )

    return tuple(issues)


def format_issue(issue: ValidationIssue) -> str:
    if issue.row_number is None:
        return issue.message
    return f"row {issue.row_number}: {issue.message}"


def format_summary(summary: DatasetSummary) -> str:
    return (
        f"{summary.path}: {summary.row_count} rows, "
        f"timestamps {summary.first_timestamp}..{summary.last_timestamp}"
    )


def validate_historical_and_updates(
    historical_tail_path: str | Path,
    updates_path: str | Path,
) -> tuple[ValidationResult, ValidationResult, tuple[ValidationIssue, ...]]:
    """Validate both datasets and their declared seam."""
    historical_result = validate_dataset(historical_tail_path)
    updates_result = validate_dataset(updates_path)
    seam_issues = validate_seam(historical_result.summary, updates_result.summary)
    return historical_result, updates_result, seam_issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Bitstamp BTC/USD one-minute OHLC CSV datasets.",
    )
    parser.add_argument(
        "--historical-tail",
        required=True,
        help="Path to the historical tail CSV (fixture or sampled bulk end).",
    )
    parser.add_argument(
        "--updates",
        required=True,
        help="Path to the updates CSV.",
    )
    args = parser.parse_args(argv)

    historical_result, updates_result, seam_issues = validate_historical_and_updates(
        args.historical_tail,
        args.updates,
    )

    exit_code = 0
    for label, result in (
        ("historical tail", historical_result),
        ("updates", updates_result),
    ):
        if not result.valid:
            exit_code = 1
            print(f"{label} validation failed:", file=sys.stderr)
            for issue in result.issues:
                print(f"  {format_issue(issue)}", file=sys.stderr)
            if len(result.issues) >= MAX_ISSUES:
                print(
                    f"  (only first {MAX_ISSUES} issues shown)",
                    file=sys.stderr,
                )

    if seam_issues:
        exit_code = 1
        print("seam validation failed:", file=sys.stderr)
        for issue in seam_issues:
            print(f"  {format_issue(issue)}", file=sys.stderr)

    if exit_code == 0:
        print(format_summary(historical_result.summary))
        print(format_summary(updates_result.summary))
        print(
            "seam ok: "
            f"{historical_result.summary.last_timestamp} + {MINUTE_SECONDS} "
            f"== {updates_result.summary.first_timestamp}"
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
