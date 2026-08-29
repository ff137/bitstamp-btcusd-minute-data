"""Bounded Bitstamp and local-lineage checks for the pinned Kaggle snapshot."""

import argparse
import calendar
import csv
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from scripts.historical_source import (
    DEFAULT_SOURCE_CSV,
    KAGGLE_HEADER,
    MINUTE_SECONDS,
    SourceError,
    iter_publication_rows,
    load_manifest,
    publication_spec_from_manifest,
    verify_sha256,
    verify_source_shape,
)

BITSTAMP_OHLC_URL = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
MAX_API_REQUESTS = 40
API_LIMIT = 1000
API_SLEEP_SECONDS = 0.2


@dataclass(frozen=True)
class Window:
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class Mismatch:
    window: str
    timestamp: int
    detail: str


class RequestBudget:
    def __init__(self, maximum: int = MAX_API_REQUESTS) -> None:
        self.maximum = maximum
        self.used = 0

    def consume(self) -> None:
        if self.used >= self.maximum:
            raise SourceError(f"API request budget exceeded ({self.maximum} requests)")
        self.used += 1


def _unix(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())


def _nth_sunday(year: int, month: int, n: int) -> int:
    sundays = [
        day
        for day in calendar.Calendar().itermonthdays(year, month)
        if day and date(year, month, day).weekday() == calendar.SUNDAY
    ]
    return sundays[n - 1]


def _dst_window(year: int, month: int, n: int, name: str) -> Window:
    day = _nth_sunday(year, month, n)
    start = _unix(year, month, day, 5)
    end = _unix(year, month, day, 8, 59)
    return Window(name=name, start=start, end=end)


def verification_windows() -> tuple[Window, ...]:
    windows = [
        Window("early-2012", _unix(2012, 1, 1, 0, 1), _unix(2012, 1, 1, 10, 1)),
        Window("winter-2013", _unix(2013, 1, 15, 12), _unix(2013, 1, 15, 13, 59)),
        Window("summer-2013", _unix(2013, 7, 15, 12), _unix(2013, 7, 15, 13, 59)),
        Window("outage-2020-04", _unix(2020, 4, 7, 19), _unix(2020, 4, 8, 1)),
        Window("gap-2024-06-01", _unix(2024, 6, 1, 0, 40), _unix(2024, 6, 1, 8)),
        Window(
            "boundary-2024-09-13", _unix(2024, 9, 13, 6), _unix(2024, 9, 13, 10, 59)
        ),
        Window("seam-2025-01-07", _unix(2025, 1, 6, 23), _unix(2025, 1, 7, 1)),
    ]
    for year in range(2012, 2025):
        windows.append(_dst_window(year, 3, 2, f"spring-dst-{year}"))
        windows.append(_dst_window(year, 11, 1, f"fall-dst-{year}"))
    return tuple(windows)


def needed_timestamps(windows: Iterable[Window]) -> set[int]:
    needed: set[int] = set()
    for window in windows:
        needed.update(range(window.start, window.end + MINUTE_SECONDS, MINUTE_SECONDS))
    return needed


def _as_decimals(values: Iterable[str]) -> tuple[Decimal, ...]:
    return tuple(Decimal(value) for value in values)


def collect_source_rows(
    source_path: Path,
    timestamps: set[int],
) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    with source_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != list(KAGGLE_HEADER):
            raise SourceError(
                f"unexpected Kaggle header: expected {list(KAGGLE_HEADER)!r}, "
                f"found {header!r}"
            )
        for row_number, row in enumerate(reader, start=2):
            if len(row) != 6:
                raise SourceError(f"row {row_number}: expected 6 columns")
            try:
                timestamp = int(row[0])
            except ValueError as exc:
                raise SourceError(
                    f"row {row_number}: invalid timestamp {row[0]!r}"
                ) from exc
            if timestamp in timestamps:
                if timestamp in found:
                    raise SourceError(
                        f"row {row_number}: duplicate timestamp {timestamp}"
                    )
                found[timestamp] = row[:6]
            if len(found) == len(timestamps) and timestamp >= max(timestamps):
                break
    return found


def fetch_bitstamp_window(
    start: int,
    end: int,
    budget: RequestBudget,
    *,
    session: requests.Session,
) -> dict[int, list[str]]:
    rows: dict[int, list[str]] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + (API_LIMIT - 1) * MINUTE_SECONDS)
        budget.consume()
        response = session.get(
            BITSTAMP_OHLC_URL,
            params={
                "step": MINUTE_SECONDS,
                "start": cursor,
                "end": chunk_end,
                "limit": API_LIMIT,
            },
            timeout=60,
        )
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise SourceError("Bitstamp response must be a JSON object")
        data = document.get("data")
        if not isinstance(data, dict):
            raise SourceError("Bitstamp response is missing the data object")
        payload = data.get("ohlc")
        if not isinstance(payload, list):
            raise SourceError("Bitstamp response is missing the OHLC list")
        for item in payload:
            if not isinstance(item, dict):
                raise SourceError("Bitstamp OHLC rows must be JSON objects")
            timestamp = int(item["timestamp"])
            if start <= timestamp <= end:
                if timestamp in rows:
                    raise SourceError(
                        f"Bitstamp returned duplicate timestamp {timestamp}"
                    )
                rows[timestamp] = [
                    str(item["timestamp"]),
                    str(item["open"]),
                    str(item["high"]),
                    str(item["low"]),
                    str(item["close"]),
                    str(item["volume"]),
                ]
        cursor = chunk_end + MINUTE_SECONDS
        if cursor <= end:
            time.sleep(API_SLEEP_SECONDS)
    return rows


def compare_window(
    window: Window,
    source_rows: dict[int, list[str]],
    api_rows: dict[int, list[str]],
) -> list[Mismatch]:
    mismatches: list[Mismatch] = []
    for timestamp in range(window.start, window.end + MINUTE_SECONDS, MINUTE_SECONDS):
        source = source_rows.get(timestamp)
        api = api_rows.get(timestamp)
        if source is None:
            mismatches.append(
                Mismatch(window.name, timestamp, "missing from Kaggle source")
            )
            continue
        if api is None:
            mismatches.append(
                Mismatch(window.name, timestamp, "missing from Bitstamp API")
            )
            continue
        try:
            if _as_decimals(source[1:]) != _as_decimals(api[1:]):
                mismatches.append(
                    Mismatch(
                        window.name,
                        timestamp,
                        f"OHLCV mismatch source={source[1:]} api={api[1:]}",
                    )
                )
        except InvalidOperation:
            mismatches.append(
                Mismatch(window.name, timestamp, "non-decimal OHLCV value")
            )
    return mismatches


def verify_community_subset(
    community_path: Path,
    source_path: Path,
) -> tuple[int, int]:
    community_rows: dict[int, list[str]] = {}
    with community_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header or "timestamp_unix" not in header:
            raise SourceError(f"unexpected community header: {header!r}")
        for row_number, row in enumerate(reader, start=2):
            if len(row) != 8:
                raise SourceError(
                    f"community row {row_number}: expected 8 columns, found {len(row)}"
                )
            timestamp = int(row[0])
            if timestamp in community_rows:
                raise SourceError(
                    f"community row {row_number}: duplicate timestamp {timestamp}"
                )
            community_rows[timestamp] = [
                row[0],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
            ]

    source_rows = collect_source_rows(source_path, set(community_rows))
    matched = 0
    for timestamp, community in community_rows.items():
        source = source_rows.get(timestamp)
        if source is None:
            continue
        if _as_decimals(source[1:]) == _as_decimals(community[1:]):
            matched += 1
    return matched, len(community_rows)


def run_verification(
    *,
    manifest_path: Path | None = None,
    source_path: Path | None = None,
    skip_api: bool = False,
    session: requests.Session | None = None,
) -> int:
    manifest = load_manifest(manifest_path)
    spec = publication_spec_from_manifest(
        manifest,
        source_path=source_path or DEFAULT_SOURCE_CSV,
    )
    if not spec.source_path.is_file():
        raise SourceError(f"source CSV not found: {spec.source_path}")

    digest = verify_sha256(spec.source_path, spec.sha256)
    print(f"source hash ok: {digest}")

    source_rows = verify_source_shape(spec.source_path, spec)
    print(
        "complete source grid ok: "
        f"{source_rows} rows "
        f"{spec.source_first_timestamp}..{spec.source_last_timestamp}"
    )

    emitted = sum(1 for _ in iter_publication_rows(spec.source_path, spec))
    print(
        "publication slice ok: "
        f"{emitted} rows {spec.first_timestamp}..{spec.last_timestamp}"
    )

    community_name = manifest["legacy_community_gaps"]["filename"]
    community_path = spec.source_path.parent / community_name
    if community_path.is_file():
        verify_sha256(
            community_path,
            str(manifest["legacy_community_gaps"]["sha256"]),
        )
        expected_community = int(manifest["legacy_community_gaps"]["row_count"])
        matched, total = verify_community_subset(community_path, spec.source_path)
        print(f"community subset: {matched}/{total} exact numeric matches")
        if total != expected_community or matched != total:
            raise SourceError(
                "community gap file is not an exact subset of the pinned Kaggle source"
            )
    else:
        print(f"community subset: skipped ({community_path} not present)")

    if skip_api:
        print("api comparison skipped")
        return 0

    windows = verification_windows()
    timestamps = needed_timestamps(windows)
    source_rows = collect_source_rows(spec.source_path, timestamps)
    budget = RequestBudget()
    owned_session = session is None
    session = session or requests.Session()
    mismatches: list[Mismatch] = []
    compared = 0
    try:
        for index, window in enumerate(windows):
            api_rows = fetch_bitstamp_window(
                window.start, window.end, budget, session=session
            )
            window_mismatches = compare_window(window, source_rows, api_rows)
            window_minutes = (window.end - window.start) // MINUTE_SECONDS + 1
            compared += window_minutes
            status = (
                "ok"
                if not window_mismatches
                else f"{len(window_mismatches)} mismatches"
            )
            print(
                f"api {window.name}: {window_minutes} minutes, {status} "
                f"(requests {budget.used}/{budget.maximum})"
            )
            mismatches.extend(window_mismatches)
            if index + 1 < len(windows):
                time.sleep(API_SLEEP_SECONDS)
    finally:
        if owned_session:
            session.close()

    print(f"api compared minutes: {compared}")
    print(f"api requests used: {budget.used}")
    if mismatches:
        for item in mismatches[:20]:
            when = datetime.fromtimestamp(item.timestamp, UTC).isoformat()
            print(
                f"  mismatch {item.window} {item.timestamp} ({when}): {item.detail}",
                file=sys.stderr,
            )
        if len(mismatches) > 20:
            print(f"  ({len(mismatches) - 20} more mismatches)", file=sys.stderr)
        raise SourceError(f"{len(mismatches)} Bitstamp API mismatches")

    print("api comparison ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the pinned Kaggle snapshot against Bitstamp and local lineage."
    )
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="Only check hash, publication slice, and optional community subset.",
    )
    args = parser.parse_args(argv)
    try:
        return run_verification(
            manifest_path=args.manifest,
            source_path=args.source,
            skip_api=args.skip_api,
        )
    except (
        OSError,
        SourceError,
        requests.RequestException,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Source verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
