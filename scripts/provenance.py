"""Sparse provenance sidecar for Bitstamp BTC/USD one-minute data."""

import argparse
import csv
import gzip
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO

MINUTE_SECONDS = 60
TWELVE_HOURS_SECONDS = 12 * 60 * 60
HOURS_QUANTIZE = Decimal("0.01")
PERCENT_QUANTIZE = Decimal("0.01")

HEADER = (
    "start_timestamp",
    "end_timestamp",
    "duration_hours",
    "flag",
    "price_jump",
    "reference",
)
EXPECTED_HEADER = ",".join(HEADER)

FLAG_SOURCE_GAP_FILLED = "source_gap_filled"
FLAG_CONFIRMED_OUTAGE = "confirmed_outage"
FLAG_SCHEDULED_MAINTENANCE = "scheduled_maintenance"
FLAG_SUSPECTED_OUTAGE = "suspected_outage"
ALLOWED_FLAGS = frozenset(
    {
        FLAG_SOURCE_GAP_FILLED,
        FLAG_CONFIRMED_OUTAGE,
        FLAG_SCHEDULED_MAINTENANCE,
        FLAG_SUSPECTED_OUTAGE,
    }
)
STATUS_FLAGS = frozenset({FLAG_CONFIRMED_OUTAGE, FLAG_SCHEDULED_MAINTENANCE})
EXCLUSION_FLAGS = frozenset(
    {
        FLAG_SOURCE_GAP_FILLED,
        FLAG_CONFIRMED_OUTAGE,
        FLAG_SCHEDULED_MAINTENANCE,
    }
)

FILL_REFERENCE = "updater:fill_missing_minutes"
SUSPECTED_REFERENCE = "zero_volume>=12h"
# 2012 12h+ zero runs are thin-book quiet spells, not published as outages.
SUSPECTED_OUTAGE_MIN_START = 1356998400  # 2013-01-01 00:00:00 UTC
RELEVANT_COMPONENTS = frozenset({"trading", "rest", "websocket", "web"})

DEFAULT_SIDECAR_PATH = Path("data/provenance/btcusd_bitstamp_1min.csv")
DEFAULT_HISTORICAL_PATH = Path("data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz")
DEFAULT_UPDATES_PATH = Path("data/updates/btcusd_bitstamp_1min_latest.csv")
STATUS_API_BASE = "https://status.bitstamp.net/api/v2"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Interval:
    start_timestamp: int
    end_timestamp: int
    flag: str
    reference: str
    price_jump: str = ""

    def duration_hours(self) -> str:
        hours = Decimal(self.end_timestamp - self.start_timestamp) / Decimal(3600)
        return format(hours.quantize(HOURS_QUANTIZE))

    def as_row(self) -> list[str]:
        return [
            str(self.start_timestamp),
            str(self.end_timestamp),
            self.duration_hours(),
            self.flag,
            self.price_jump,
            self.reference,
        ]


def floor_to_minute(unix_seconds: int) -> int:
    return unix_seconds - (unix_seconds % MINUTE_SECONDS)


def ceil_to_minute(unix_seconds: int) -> int:
    remainder = unix_seconds % MINUTE_SECONDS
    if remainder == 0:
        return unix_seconds
    return unix_seconds + (MINUTE_SECONDS - remainder)


def parse_status_datetime(raw: str) -> int:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def timestamps_to_intervals(
    timestamps: Iterable[int],
    *,
    flag: str,
    reference: str,
) -> list[Interval]:
    """Merge sorted unique minute timestamps into half-open intervals."""
    unique = sorted({int(ts) for ts in timestamps})
    if not unique:
        return []

    intervals: list[Interval] = []
    start = unique[0]
    previous = unique[0]
    for timestamp in unique[1:]:
        if timestamp == previous:
            continue
        if timestamp == previous + MINUTE_SECONDS:
            previous = timestamp
            continue
        intervals.append(Interval(start, previous + MINUTE_SECONDS, flag, reference))
        start = timestamp
        previous = timestamp
    intervals.append(Interval(start, previous + MINUTE_SECONDS, flag, reference))
    return intervals


def merge_adjacent(intervals: Sequence[Interval]) -> list[Interval]:
    """Merge overlapping or adjacent intervals that share flag and reference."""
    if not intervals:
        return []

    ordered = sorted(
        intervals,
        key=lambda item: (
            item.flag,
            item.reference,
            item.start_timestamp,
            item.end_timestamp,
        ),
    )
    merged: list[Interval] = [ordered[0]]
    for current in ordered[1:]:
        previous = merged[-1]
        same_key = (
            current.flag == previous.flag and current.reference == previous.reference
        )
        if same_key and current.start_timestamp <= previous.end_timestamp:
            merged[-1] = Interval(
                previous.start_timestamp,
                max(previous.end_timestamp, current.end_timestamp),
                previous.flag,
                previous.reference,
                previous.price_jump
                if previous.price_jump == current.price_jump
                else "",
            )
        else:
            merged.append(current)

    merged.sort(
        key=lambda item: (
            item.start_timestamp,
            item.end_timestamp,
            item.flag,
            item.reference,
        )
    )
    return merged


def intervals_overlap(left: Interval, right: Interval) -> bool:
    return left.start_timestamp < right.end_timestamp and (
        right.start_timestamp < left.end_timestamp
    )


def subtract_exclusions(
    start: int,
    end: int,
    exclusions: Sequence[Interval],
) -> list[tuple[int, int]]:
    pieces = [(start, end)]
    for exclusion in exclusions:
        remaining: list[tuple[int, int]] = []
        for piece_start, piece_end in pieces:
            if (
                exclusion.end_timestamp <= piece_start
                or exclusion.start_timestamp >= piece_end
            ):
                remaining.append((piece_start, piece_end))
                continue
            if piece_start < exclusion.start_timestamp:
                remaining.append(
                    (piece_start, min(piece_end, exclusion.start_timestamp))
                )
            if exclusion.end_timestamp < piece_end:
                remaining.append((max(piece_start, exclusion.end_timestamp), piece_end))
        pieces = remaining
    return [(start_, end_) for start_, end_ in pieces if end_ > start_]


def format_price_jump(
    close_before: Decimal | None,
    close_after: Decimal | None,
) -> str:
    """Absolute percent change, two decimal places, or empty if unknown."""
    if close_before is None or close_after is None or close_before <= 0:
        return ""
    percent = (abs(close_after - close_before) / close_before) * Decimal(100)
    return format(percent.quantize(PERCENT_QUANTIZE))


def needed_close_timestamps(intervals: Sequence[Interval]) -> set[int]:
    needed: set[int] = set()
    for interval in intervals:
        needed.add(interval.start_timestamp - MINUTE_SECONDS)
        needed.add(interval.start_timestamp)
        needed.add(interval.end_timestamp - MINUTE_SECONDS)
        needed.add(interval.end_timestamp)
    return needed


def bounding_closes(
    interval: Interval,
    closes: dict[int, Decimal],
) -> tuple[Decimal | None, Decimal | None]:
    close_before = closes.get(interval.start_timestamp - MINUTE_SECONDS)
    if close_before is None:
        close_before = closes.get(interval.start_timestamp)
    close_after = closes.get(interval.end_timestamp)
    if close_after is None:
        close_after = closes.get(interval.end_timestamp - MINUTE_SECONDS)
    return close_before, close_after


def annotate_price_jumps(
    intervals: Sequence[Interval],
    closes: dict[int, Decimal],
) -> list[Interval]:
    annotated: list[Interval] = []
    for interval in intervals:
        close_before, close_after = bounding_closes(interval, closes)
        computed = format_price_jump(close_before, close_after)
        annotated.append(
            Interval(
                interval.start_timestamp,
                interval.end_timestamp,
                interval.flag,
                interval.reference,
                computed or interval.price_jump,
            )
        )
    return annotated


def open_ohlcv(path: str | Path) -> TextIO:
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8", newline="")
    return path.open(mode="r", encoding="utf-8", newline="")


def iter_ohlcv_candles(
    paths: Sequence[str | Path],
) -> Iterable[tuple[int, Decimal, Decimal]]:
    """Yield `(timestamp, close, volume)` from one or more OHLCV CSV files."""
    for path in paths:
        with open_ohlcv(path) as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header != [
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]:
                raise ValueError(
                    f"{path}: expected OHLCV header timestamp,open,high,low,close,volume"
                )
            for row_number, row in enumerate(reader, start=2):
                if len(row) != 6:
                    raise ValueError(
                        f"{path}:{row_number}: expected 6 columns, found {len(row)}"
                    )
                try:
                    timestamp = int(row[0])
                    close = Decimal(row[4])
                    volume = Decimal(row[5])
                except (InvalidOperation, ValueError) as exc:
                    raise ValueError(
                        f"{path}:{row_number}: invalid numeric value"
                    ) from exc
                yield timestamp, close, volume


def detect_suspected_outages(
    paths: Sequence[str | Path],
    exclusions: Sequence[Interval] | None = None,
    *,
    min_duration_seconds: int = TWELVE_HOURS_SECONDS,
    extra_needed_timestamps: Iterable[int] | None = None,
) -> tuple[list[Interval], dict[int, Decimal]]:
    """Emit suspected_outage intervals for contiguous 12h+ zero-volume runs."""
    exclusion_windows = [
        item for item in (exclusions or []) if item.flag in EXCLUSION_FLAGS
    ]
    needed = set(extra_needed_timestamps or [])
    closes: dict[int, Decimal] = {}
    suspected: list[Interval] = []
    close_before: Decimal | None = None
    run_start: int | None = None
    run_end: int | None = None

    def flush_run(close_after: Decimal | None) -> None:
        nonlocal run_start, run_end
        if run_start is None or run_end is None:
            return
        if run_end - run_start < min_duration_seconds:
            run_start = None
            run_end = None
            return
        pieces = subtract_exclusions(run_start, run_end, exclusion_windows)
        for piece_start, piece_end in pieces:
            if piece_end - piece_start < min_duration_seconds:
                continue
            if piece_start < SUSPECTED_OUTAGE_MIN_START:
                continue
            suspected.append(
                Interval(
                    piece_start,
                    piece_end,
                    FLAG_SUSPECTED_OUTAGE,
                    SUSPECTED_REFERENCE,
                    format_price_jump(close_before, close_after),
                )
            )
        run_start = None
        run_end = None

    for timestamp, close, volume in iter_ohlcv_candles(paths):
        if timestamp in needed:
            closes[timestamp] = close
        if volume == 0:
            if run_start is None:
                run_start = timestamp
                if close_before is not None:
                    closes[timestamp - MINUTE_SECONDS] = close_before
            run_end = timestamp + MINUTE_SECONDS
            continue
        if run_start is not None:
            closes[timestamp] = close
        flush_run(close)
        close_before = close

    flush_run(None)
    return merge_adjacent(suspected), closes


def _component_names(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for component in payload.get("components") or []:
        name = component.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    for update in payload.get("incident_updates") or []:
        for component in update.get("affected_components") or []:
            name = component.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def is_market_relevant(payload: dict[str, Any]) -> bool:
    names = {name.casefold() for name in _component_names(payload)}
    return bool(names & RELEVANT_COMPONENTS)


def _interval_from_status_event(
    payload: dict[str, Any],
    *,
    flag: str,
    start_raw: str | None,
    end_raw: str | None,
    now_unix: int,
) -> Interval | None:
    if not start_raw:
        return None
    start = floor_to_minute(parse_status_datetime(start_raw))
    if end_raw:
        end = ceil_to_minute(parse_status_datetime(end_raw))
    else:
        end = floor_to_minute(now_unix)
    if end <= start:
        return None
    reference = payload.get("shortlink") or payload.get("id")
    if not isinstance(reference, str) or not reference.strip():
        return None
    return Interval(start, end, flag, reference.strip())


def parse_incidents(
    payload: dict[str, Any],
    *,
    now_unix: int | None = None,
) -> list[Interval]:
    now = now_unix if now_unix is not None else int(datetime.now(UTC).timestamp())
    intervals: list[Interval] = []
    for incident in payload.get("incidents") or []:
        if not is_market_relevant(incident):
            continue
        interval = _interval_from_status_event(
            incident,
            flag=FLAG_CONFIRMED_OUTAGE,
            start_raw=incident.get("started_at"),
            end_raw=incident.get("resolved_at"),
            now_unix=now,
        )
        if interval is not None:
            intervals.append(interval)
    return intervals


def parse_scheduled_maintenances(
    payload: dict[str, Any],
    *,
    now_unix: int | None = None,
) -> list[Interval]:
    now = now_unix if now_unix is not None else int(datetime.now(UTC).timestamp())
    intervals: list[Interval] = []
    for maintenance in payload.get("scheduled_maintenances") or []:
        if not is_market_relevant(maintenance):
            continue
        interval = _interval_from_status_event(
            maintenance,
            flag=FLAG_SCHEDULED_MAINTENANCE,
            start_raw=maintenance.get("scheduled_for") or maintenance.get("started_at"),
            end_raw=maintenance.get("scheduled_until")
            or maintenance.get("resolved_at"),
            now_unix=now,
        )
        if interval is not None:
            intervals.append(interval)
    return intervals


def fetch_json(url: str, *, timeout: int = 60) -> dict[str, Any]:
    import requests

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError(f"{url}: expected a JSON object")
    return payload


def fetch_status_intervals(
    *,
    incidents_url: str | None = None,
    maintenances_url: str | None = None,
    now_unix: int | None = None,
) -> list[Interval]:
    incidents_payload = fetch_json(incidents_url or f"{STATUS_API_BASE}/incidents.json")
    maintenances_payload = fetch_json(
        maintenances_url or f"{STATUS_API_BASE}/scheduled-maintenances.json"
    )
    return merge_adjacent(
        parse_incidents(incidents_payload, now_unix=now_unix)
        + parse_scheduled_maintenances(maintenances_payload, now_unix=now_unix)
    )


def read_sidecar(path: str | Path) -> list[Interval]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(mode="r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != list(HEADER):
            raise ValueError(f"{path}: invalid header: expected {EXPECTED_HEADER!r}")
        intervals: list[Interval] = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(HEADER):
                raise ValueError(
                    f"{path}: expected {len(HEADER)} columns, found {len(row)}"
                )
            intervals.append(
                Interval(
                    int(row[0]),
                    int(row[1]),
                    row[3],
                    row[5],
                    row[4],
                )
            )
    return intervals


def write_sidecar(path: str | Path, intervals: Sequence[Interval]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = merge_adjacent(intervals)
    with path.open(mode="w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(HEADER)
        for interval in ordered:
            writer.writerow(interval.as_row())


def record_fill_timestamps(
    timestamps: Iterable[int],
    sidecar_path: str | Path = DEFAULT_SIDECAR_PATH,
) -> list[Interval]:
    """Record going-forward synthesized minutes as source_gap_filled intervals."""
    new_intervals = timestamps_to_intervals(
        timestamps,
        flag=FLAG_SOURCE_GAP_FILLED,
        reference=FILL_REFERENCE,
    )
    if not new_intervals:
        return read_sidecar(sidecar_path) if Path(sidecar_path).exists() else []

    existing = read_sidecar(sidecar_path)
    merged = merge_adjacent([*existing, *new_intervals])
    write_sidecar(sidecar_path, merged)
    logger.info(
        "Recorded %s source_gap_filled interval(s) into %s",
        len(new_intervals),
        sidecar_path,
    )
    return merged


def _reference_tokens(reference: str) -> set[str]:
    return {part.strip() for part in reference.split(";") if part.strip()}


def replace_status_intervals(
    existing: Sequence[Interval],
    fetched: Sequence[Interval],
) -> list[Interval]:
    fetched_refs: set[str] = set()
    for interval in fetched:
        fetched_refs.update(_reference_tokens(interval.reference))

    retained = [
        interval
        for interval in existing
        if interval.flag in STATUS_FLAGS
        and not (_reference_tokens(interval.reference) & fetched_refs)
    ]
    return merge_adjacent([*retained, *fetched])


def refresh_sidecar(
    *,
    sidecar_path: str | Path = DEFAULT_SIDECAR_PATH,
    ohlcv_paths: Sequence[str | Path] | None = None,
    status_intervals: Sequence[Interval] | None = None,
    fetch_status: bool = True,
) -> list[Interval]:
    """Rebuild suspected rows and merge status/fills into the sidecar."""
    try:
        existing = read_sidecar(sidecar_path)
    except ValueError:
        logger.warning("Sidecar schema mismatch; rebuilding %s", sidecar_path)
        existing = []
    fills = [
        interval for interval in existing if interval.flag == FLAG_SOURCE_GAP_FILLED
    ]
    existing_status = [
        interval for interval in existing if interval.flag in STATUS_FLAGS
    ]

    if status_intervals is not None:
        fetched = list(status_intervals)
    elif fetch_status:
        fetched = fetch_status_intervals()
    else:
        fetched = []

    status = replace_status_intervals(existing_status, fetched)
    exclusions = [*fills, *status]

    suspected: list[Interval] = []
    closes: dict[int, Decimal] = {}
    if ohlcv_paths:
        available = [path for path in ohlcv_paths if Path(path).exists()]
        if available:
            suspected, closes = detect_suspected_outages(
                available,
                exclusions,
                extra_needed_timestamps=needed_close_timestamps(exclusions),
            )

    merged = merge_adjacent([*fills, *status, *suspected])
    if closes:
        merged = annotate_price_jumps(merged, closes)
    write_sidecar(sidecar_path, merged)
    return merged


def summarize_intervals(intervals: Sequence[Interval]) -> dict[str, int]:
    counts = {flag: 0 for flag in sorted(ALLOWED_FLAGS)}
    for interval in intervals:
        counts[interval.flag] = counts.get(interval.flag, 0) + 1
    return counts


def _configure_logging() -> None:
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(
        description="Refresh the Bitstamp BTC/USD sparse provenance sidecar.",
    )
    parser.add_argument(
        "--sidecar",
        default=str(DEFAULT_SIDECAR_PATH),
        help="Path to the provenance CSV.",
    )
    parser.add_argument(
        "--historical",
        default=str(DEFAULT_HISTORICAL_PATH),
        help="Path to historical OHLCV (plain CSV or .gz).",
    )
    parser.add_argument(
        "--updates",
        default=str(DEFAULT_UPDATES_PATH),
        help="Path to the updates OHLCV CSV.",
    )
    parser.add_argument(
        "--skip-status",
        action="store_true",
        help="Do not fetch Statuspage; keep existing status intervals.",
    )
    parser.add_argument(
        "--incidents-json",
        help="Optional local incidents.json instead of the live API.",
    )
    parser.add_argument(
        "--maintenances-json",
        help="Optional local scheduled-maintenances.json instead of the live API.",
    )
    args = parser.parse_args(argv)

    status_intervals: list[Interval] | None
    if args.skip_status:
        status_intervals = None
        fetch_status = False
    elif args.incidents_json or args.maintenances_json:
        now = int(datetime.now(UTC).timestamp())
        incidents_payload: dict[str, Any] = {"incidents": []}
        maintenances_payload: dict[str, Any] = {"scheduled_maintenances": []}
        if args.incidents_json:
            import json

            incidents_payload = json.loads(
                Path(args.incidents_json).read_text(encoding="utf-8")
            )
        if args.maintenances_json:
            import json

            maintenances_payload = json.loads(
                Path(args.maintenances_json).read_text(encoding="utf-8")
            )
        status_intervals = merge_adjacent(
            parse_incidents(incidents_payload, now_unix=now)
            + parse_scheduled_maintenances(maintenances_payload, now_unix=now)
        )
        fetch_status = False
    else:
        status_intervals = None
        fetch_status = True

    ohlcv_paths = [args.historical, args.updates]
    intervals = refresh_sidecar(
        sidecar_path=args.sidecar,
        ohlcv_paths=ohlcv_paths,
        status_intervals=status_intervals,
        fetch_status=fetch_status,
    )
    counts = summarize_intervals(intervals)
    logger.info(
        "Wrote %s intervals to %s "
        "(source_gap_filled=%s confirmed_outage=%s "
        "scheduled_maintenance=%s suspected_outage=%s)",
        len(intervals),
        args.sidecar,
        counts.get(FLAG_SOURCE_GAP_FILLED, 0),
        counts.get(FLAG_CONFIRMED_OUTAGE, 0),
        counts.get(FLAG_SCHEDULED_MAINTENANCE, 0),
        counts.get(FLAG_SUSPECTED_OUTAGE, 0),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
