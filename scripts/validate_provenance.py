"""Fail-closed validator for the sparse provenance sidecar."""

import argparse
import csv
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.provenance import ALLOWED_FLAGS, EXPECTED_HEADER, HEADER, MINUTE_SECONDS

MAX_ISSUES = 20


@dataclass(frozen=True)
class ValidationIssue:
    message: str
    row_number: int | None = None


@dataclass(frozen=True)
class SidecarSummary:
    path: str
    row_count: int


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    summary: SidecarSummary
    issues: tuple[ValidationIssue, ...]


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


def _parse_decimal(raw: str, *, field: str) -> Decimal:
    value = _require_field(raw)
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} is not a valid decimal number") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return number


def _expected_duration_minutes(start_timestamp: int, end_timestamp: int) -> str:
    return str((end_timestamp - start_timestamp) // MINUTE_SECONDS)


def _parse_timestamp(raw: str) -> int:
    value = _require_field(raw)
    if not value.isdigit():
        raise ValueError("timestamp must be an unsigned base-10 integer")
    timestamp = int(value)
    if timestamp % MINUTE_SECONDS != 0:
        raise ValueError("timestamp must be minute-aligned (multiple of 60)")
    return timestamp


def validate_sidecar_rows(
    rows: Iterable[list[str]],
    *,
    path: str,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    row_count = 0
    previous_key: tuple[int, int, str, str] | None = None

    for row_number, row in enumerate(rows, start=2):
        if len(issues) >= MAX_ISSUES:
            break

        if len(row) != len(HEADER):
            _add_issue(
                issues,
                row_number=row_number,
                message=(
                    f"expected {len(HEADER)} columns "
                    f"({', '.join(HEADER)}), found {len(row)}"
                ),
            )
            continue

        start_raw, end_raw, duration_raw, flag_raw, price_jump_raw, reference_raw = row

        try:
            start_timestamp = _parse_timestamp(start_raw)
            end_timestamp = _parse_timestamp(end_raw)
        except ValueError as exc:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"invalid timestamp: {exc}",
            )
            continue

        if start_timestamp >= end_timestamp:
            _add_issue(
                issues,
                row_number=row_number,
                message=(
                    f"start_timestamp {start_timestamp} must be < "
                    f"end_timestamp {end_timestamp}"
                ),
            )

        expected_duration = _expected_duration_minutes(start_timestamp, end_timestamp)
        if duration_raw != expected_duration:
            _add_issue(
                issues,
                row_number=row_number,
                message=(
                    f"duration_minutes {duration_raw!r} does not match "
                    f"{expected_duration!r}"
                ),
            )

        if price_jump_raw != "":
            try:
                jump = _parse_decimal(price_jump_raw, field="price_jump")
            except ValueError as exc:
                _add_issue(
                    issues,
                    row_number=row_number,
                    message=f"invalid price_jump: {exc}",
                )
                continue
            if jump < 0:
                _add_issue(
                    issues,
                    row_number=row_number,
                    message=f"price_jump must be non-negative, found {price_jump_raw}",
                )

        try:
            flag = _require_field(flag_raw)
        except ValueError as exc:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"invalid flag: {exc}",
            )
            continue

        if flag not in ALLOWED_FLAGS:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"unsupported flag {flag!r}",
            )

        try:
            reference = _require_field(reference_raw)
        except ValueError as exc:
            _add_issue(
                issues,
                row_number=row_number,
                message=f"invalid reference: {exc}",
            )
            continue

        key = (start_timestamp, end_timestamp, flag, reference)
        if previous_key is not None:
            if key == previous_key:
                _add_issue(
                    issues,
                    row_number=row_number,
                    message="duplicate interval",
                )
            elif key < previous_key:
                _add_issue(
                    issues,
                    row_number=row_number,
                    message=(
                        "out-of-order interval "
                        f"{start_timestamp},{end_timestamp},{flag}"
                    ),
                )
        previous_key = key
        row_count += 1

    valid = not issues
    return ValidationResult(
        valid=valid,
        summary=SidecarSummary(path=path, row_count=row_count),
        issues=tuple(issues),
    )


def validate_sidecar(path: str | Path) -> ValidationResult:
    path = Path(path)
    issues: list[ValidationIssue] = []

    try:
        with path.open(mode="r", encoding="utf-8", newline="") as handle:
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
                    summary=SidecarSummary(path=str(path), row_count=0),
                    issues=tuple(issues),
                )

            if header != list(HEADER):
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
                    summary=SidecarSummary(path=str(path), row_count=0),
                    issues=tuple(issues),
                )

            return validate_sidecar_rows(reader, path=str(path))
    except OSError as exc:
        _add_issue(issues, row_number=None, message=f"cannot read file: {exc}")
        return ValidationResult(
            valid=False,
            summary=SidecarSummary(path=str(path), row_count=0),
            issues=tuple(issues),
        )


def format_issue(issue: ValidationIssue) -> str:
    if issue.row_number is None:
        return issue.message
    return f"row {issue.row_number}: {issue.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Bitstamp BTC/USD sparse provenance sidecar.",
    )
    parser.add_argument(
        "--sidecar",
        default="data/provenance/btcusd_bitstamp_1min.csv",
        help="Path to the provenance CSV.",
    )
    args = parser.parse_args(argv)

    result = validate_sidecar(args.sidecar)
    if not result.valid:
        print("provenance validation failed:", file=sys.stderr)
        for issue in result.issues:
            print(f"  {format_issue(issue)}", file=sys.stderr)
        if len(result.issues) >= MAX_ISSUES:
            print(
                f"  (only first {MAX_ISSUES} issues shown)",
                file=sys.stderr,
            )
        return 1

    print(f"{result.summary.path}: {result.summary.row_count} intervals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
