"""Build and optionally publish a monthly Bitstamp BTC/USD snapshot release."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import logging
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.provenance import (
    ALLOWED_FLAGS,
    FLAG_CONFIRMED_OUTAGE,
    FLAG_SCHEDULED_MAINTENANCE,
    FLAG_SOURCE_GAP_FILLED,
    FLAG_SUSPECTED_OUTAGE,
    Interval,
    read_sidecar,
)
from scripts.provenance import (
    HEADER as SIDECAR_HEADER,
)
from scripts.validate_dataset import (
    COLUMN_NAMES,
    MINUTE_SECONDS,
    format_issue,
    open_dataset,
    validate_historical_and_updates,
)

TAG_PREFIX = "bitstamp-btcusd-1m-"
FIRST_RELEASE_TAG = "bitstamp-btcusd-1m-2026-08"
CSV_ASSET = "btcusd_bitstamp_1min.csv.gz"
PARQUET_ASSET = "btcusd_bitstamp_1min.parquet"
PROVENANCE_ASSET = "btcusd_bitstamp_1min_provenance.csv"
MANIFEST_ASSET = "manifest.json"
NOTES_ASSET = "release_notes.md"
DEFAULT_INTRO_PATH = Path("scripts/first_release_intro.md")
DEFAULT_HISTORICAL_PATH = Path("data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz")
DEFAULT_UPDATES_PATH = Path("data/updates/btcusd_bitstamp_1min_latest.csv")
DEFAULT_SIDECAR_PATH = Path("data/provenance/btcusd_bitstamp_1min.csv")
PARQUET_BATCH_SIZE = 50_000
HASH_CHUNK_SIZE = 1024 * 1024
FLAG_NOTE_ORDER = (
    FLAG_CONFIRMED_OUTAGE,
    FLAG_SCHEDULED_MAINTENANCE,
    FLAG_SUSPECTED_OUTAGE,
    FLAG_SOURCE_GAP_FILLED,
)
FLAG_HEADINGS = {
    FLAG_CONFIRMED_OUTAGE: "Confirmed outages",
    FLAG_SCHEDULED_MAINTENANCE: "Scheduled maintenance",
    FLAG_SUSPECTED_OUTAGE: "Suspected outages",
    FLAG_SOURCE_GAP_FILLED: "Source gap fills",
}

logger = logging.getLogger(__name__)


class PublishError(Exception):
    """Fail-closed snapshot error."""


@dataclass(frozen=True)
class MonthWindow:
    year: int
    month: int
    start_timestamp: int
    end_timestamp: int

    @property
    def last_minute(self) -> int:
        return self.end_timestamp - MINUTE_SECONDS

    @property
    def tag(self) -> str:
        return f"{TAG_PREFIX}{self.year:04d}-{self.month:02d}"

    @property
    def expected_candles(self) -> int:
        return (self.end_timestamp - self.start_timestamp) // MINUTE_SECONDS

    @property
    def label(self) -> str:
        return datetime(self.year, self.month, 1, tzinfo=UTC).strftime("%B %Y")


@dataclass
class ZeroRun:
    start_timestamp: int
    end_timestamp: int


@dataclass
class MonthStats:
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    first_open: str | None = None
    last_close: str | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume_sum: Decimal = Decimal(0)
    candle_count: int = 0
    zero_runs: list[ZeroRun] = field(default_factory=list)
    _zero_start: int | None = field(default=None, repr=False)
    _zero_end: int | None = field(default=None, repr=False)

    def add_row(
        self,
        timestamp: int,
        open_raw: str,
        high_raw: str,
        low_raw: str,
        close_raw: str,
        volume_raw: str,
    ) -> None:
        high = Decimal(high_raw)
        low = Decimal(low_raw)
        volume = Decimal(volume_raw)
        if self.first_timestamp is None:
            self.first_timestamp = timestamp
            self.first_open = open_raw
        self.last_timestamp = timestamp
        self.last_close = close_raw
        self.high = high if self.high is None else max(self.high, high)
        self.low = low if self.low is None else min(self.low, low)
        self.volume_sum += volume
        self.candle_count += 1
        if volume == 0:
            if self._zero_start is None:
                self._zero_start = timestamp
            self._zero_end = timestamp + MINUTE_SECONDS
            return
        self._flush_zero_run()

    def finish(self) -> None:
        self._flush_zero_run()

    def _flush_zero_run(self) -> None:
        if self._zero_start is None or self._zero_end is None:
            return
        self.zero_runs.append(ZeroRun(self._zero_start, self._zero_end))
        self._zero_start = None
        self._zero_end = None


@dataclass(frozen=True)
class SnapshotResult:
    tag: str
    output_dir: Path
    row_count: int
    first_timestamp: int
    last_timestamp: int
    notes: str
    manifest: dict[str, object]
    skipped: bool = False
    skip_reason: str | None = None


def parse_year_month(raw: str) -> tuple[int, int]:
    parts = raw.strip().split("-")
    if len(parts) != 2:
        raise PublishError(f"year-month must be YYYY-MM, found {raw!r}")
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError as exc:
        raise PublishError(f"year-month must be YYYY-MM, found {raw!r}") from exc
    if month < 1 or month > 12 or year < 2012:
        raise PublishError(f"year-month out of range: {raw}")
    return year, month


def month_window(year: int, month: int) -> MonthWindow:
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return MonthWindow(
        year=year,
        month=month,
        start_timestamp=int(start.timestamp()),
        end_timestamp=int(end.timestamp()),
    )


def previous_utc_month(now: datetime) -> MonthWindow:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    if now.month == 1:
        return month_window(now.year - 1, 12)
    return month_window(now.year, now.month - 1)


def is_utc_first_of_month(now: datetime) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    return now.day == 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_manifest_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def git_revision(repo: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip()


def github_release_exists(tag: str) -> bool:
    completed = subprocess.run(
        ["gh", "release", "view", tag],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def format_utc_range(start_timestamp: int, end_timestamp: int) -> str:
    start = datetime.fromtimestamp(start_timestamp, UTC)
    last = datetime.fromtimestamp(end_timestamp - MINUTE_SECONDS, UTC)
    minutes = (end_timestamp - start_timestamp) // MINUTE_SECONDS
    if start.date() == last.date():
        span = f"{start:%Y-%m-%d %H:%M}–{last:%H:%M} UTC"
    else:
        span = f"{start:%Y-%m-%d %H:%M} UTC – {last:%Y-%m-%d %H:%M} UTC"
    return f"{span} ({minutes}m)"


def intervals_overlap(
    start: int,
    end: int,
    window_start: int,
    window_end: int,
) -> bool:
    return start < window_end and end > window_start


def clip_interval(
    interval: Interval,
    window_start: int,
    window_end: int,
) -> Interval | None:
    if not intervals_overlap(
        interval.start_timestamp,
        interval.end_timestamp,
        window_start,
        window_end,
    ):
        return None
    start = max(interval.start_timestamp, window_start)
    end = min(interval.end_timestamp, window_end)
    if end <= start:
        return None
    return Interval(
        start,
        end,
        interval.flag,
        interval.reference,
        interval.price_jump,
    )


def clip_sidecar(
    intervals: Sequence[Interval],
    window_start: int,
    window_end: int,
) -> list[Interval]:
    clipped: list[Interval] = []
    for interval in intervals:
        piece = clip_interval(interval, window_start, window_end)
        if piece is not None:
            clipped.append(piece)
    return clipped


def write_clipped_sidecar(path: Path, intervals: Sequence[Interval]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        intervals,
        key=lambda item: (
            item.start_timestamp,
            item.end_timestamp,
            item.flag,
            item.reference,
        ),
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(SIDECAR_HEADER)
        for interval in ordered:
            writer.writerow(interval.as_row())


def iter_ohlcv_rows(path: Path) -> Iterator[list[str]]:
    with open_dataset(path) as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != list(COLUMN_NAMES):
            raise PublishError(
                f"{path}: expected OHLCV header {','.join(COLUMN_NAMES)}"
            )
        yield from reader


def _require_validated_join(
    historical_path: Path,
    updates_path: Path,
) -> tuple[int, int]:
    historical, updates, seam_issues = validate_historical_and_updates(
        historical_path,
        updates_path,
    )
    issues = []
    if not historical.valid:
        issues.extend(historical.issues)
    if not updates.valid:
        issues.extend(updates.issues)
    issues.extend(seam_issues)
    if issues:
        detail = "; ".join(format_issue(issue) for issue in issues[:20])
        raise PublishError(f"OHLCV validation failed: {detail}")
    if historical.summary.first_timestamp is None:
        raise PublishError("historical dataset has no rows")
    if updates.summary.last_timestamp is None:
        raise PublishError("updates dataset has no rows")
    return historical.summary.first_timestamp, updates.summary.last_timestamp


def stream_joined_rows(
    historical_path: Path,
    updates_path: Path,
    cutoff: int,
) -> Iterator[list[str]]:
    for path in (historical_path, updates_path):
        for row in iter_ohlcv_rows(path):
            timestamp = int(row[0])
            if timestamp > cutoff:
                return
            yield row


def _parquet_schema():
    import pyarrow as pa

    return pa.schema(
        [
            ("timestamp", pa.int64()),
            ("open", pa.string()),
            ("high", pa.string()),
            ("low", pa.string()),
            ("close", pa.string()),
            ("volume", pa.string()),
        ]
    )


def _flush_parquet_batch(writer, rows: list[list[str]]) -> None:
    import pyarrow as pa

    if not rows:
        return
    arrays = [
        pa.array([int(row[0]) for row in rows], type=pa.int64()),
        pa.array([row[1] for row in rows], type=pa.string()),
        pa.array([row[2] for row in rows], type=pa.string()),
        pa.array([row[3] for row in rows], type=pa.string()),
        pa.array([row[4] for row in rows], type=pa.string()),
        pa.array([row[5] for row in rows], type=pa.string()),
    ]
    writer.write_table(pa.Table.from_arrays(arrays, schema=_parquet_schema()))


def write_join_assets(
    rows: Iterable[list[str]],
    *,
    csv_path: Path,
    parquet_path: Path,
    month: MonthWindow,
) -> tuple[int, int, int, MonthStats]:
    import pyarrow.parquet as pq

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    stats = MonthStats()
    row_count = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    batch: list[list[str]] = []

    with (
        gzip.open(csv_path, mode="wt", encoding="utf-8", newline="") as gzip_handle,
        pq.ParquetWriter(parquet_path, _parquet_schema(), compression="zstd") as writer,
    ):
        csv_writer = csv.writer(gzip_handle, lineterminator="\n")
        csv_writer.writerow(COLUMN_NAMES)
        for row in rows:
            if len(row) != 6:
                raise PublishError(f"expected 6 OHLCV columns, found {len(row)}")
            timestamp = int(row[0])
            csv_writer.writerow(row)
            batch.append(row)
            if len(batch) >= PARQUET_BATCH_SIZE:
                _flush_parquet_batch(writer, batch)
                batch = []
            if first_timestamp is None:
                first_timestamp = timestamp
            last_timestamp = timestamp
            row_count += 1
            if month.start_timestamp <= timestamp < month.end_timestamp:
                stats.add_row(timestamp, row[1], row[2], row[3], row[4], row[5])
        _flush_parquet_batch(writer, batch)

    stats.finish()
    if first_timestamp is None or last_timestamp is None:
        raise PublishError("joined snapshot contains no rows")
    if last_timestamp != month.last_minute:
        raise PublishError(
            f"joined last timestamp {last_timestamp} != month cutoff {month.last_minute}"
        )
    if stats.candle_count != month.expected_candles:
        raise PublishError(
            f"month candle count {stats.candle_count} != "
            f"expected {month.expected_candles}"
        )
    return row_count, first_timestamp, last_timestamp, stats


def percent_change(first_open: str, last_close: str) -> Decimal:
    start = Decimal(first_open)
    if start == 0:
        raise PublishError("first open must be non-zero")
    return ((Decimal(last_close) - start) / start * Decimal(100)).quantize(
        Decimal("0.01")
    )


def render_notes(
    *,
    month: MonthWindow,
    stats: MonthStats,
    sidecar: Sequence[Interval],
    intro_text: str | None,
) -> str:
    if (
        stats.first_open is None
        or stats.last_close is None
        or stats.high is None
        or stats.low is None
    ):
        raise PublishError("month slice is missing OHLC for notes")

    change = percent_change(stats.first_open, stats.last_close)
    change_text = f"+{change}%" if change >= 0 else f"{change}%"
    month_sidecar = clip_sidecar(
        sidecar,
        month.start_timestamp,
        month.end_timestamp,
    )
    by_flag: dict[str, list[Interval]] = {flag: [] for flag in FLAG_NOTE_ORDER}
    for interval in month_sidecar:
        if interval.flag in by_flag:
            by_flag[interval.flag].append(interval)

    lines: list[str] = []
    if intro_text:
        lines.append(intro_text.rstrip())
        lines.append("")
    lines.append(f"# {month.tag}")
    lines.append("")
    lines.append(
        f"Full-history Bitstamp BTC/USD 1-minute snapshot through "
        f"{datetime.fromtimestamp(month.last_minute, UTC):%Y-%m-%d %H:%M} UTC."
    )
    lines.append("")
    lines.append(f"## Price ({month.label}, UTC)")
    lines.append("")
    lines.append(f"- Open: {stats.first_open}")
    lines.append(f"- Close: {stats.last_close}")
    lines.append(f"- High: {stats.high}")
    lines.append(f"- Low: {stats.low}")
    lines.append(f"- Change: {change_text}")
    lines.append(f"- Volume: {stats.volume_sum.normalize()} BTC")
    lines.append(f"- Candles: {stats.candle_count}")
    lines.append("")
    lines.append("## Exchange status (sidecar)")
    lines.append("")
    for flag in FLAG_NOTE_ORDER:
        lines.append(f"### {FLAG_HEADINGS[flag]}")
        lines.append("")
        rows = by_flag[flag]
        if not rows:
            lines.append("None this month.")
            lines.append("")
            continue
        for interval in rows:
            jump = interval.price_jump or "0"
            lines.append(
                f"- {format_utc_range(interval.start_timestamp, interval.end_timestamp)}"
                f"; price_jump={jump}; {interval.reference}"
            )
        lines.append("")
    lines.append("## Zero-volume runs (data quality)")
    lines.append("")
    if not stats.zero_runs:
        lines.append("No zero-volume minutes in this month.")
        lines.append("")
    else:
        total_minutes = sum(
            (run.end_timestamp - run.start_timestamp) // MINUTE_SECONDS
            for run in stats.zero_runs
        )
        lines.append(
            f"{len(stats.zero_runs)} contiguous run(s), {total_minutes} minute(s)."
        )
        lines.append("")
        for run in stats.zero_runs:
            lines.append(
                f"- {format_utc_range(run.start_timestamp, run.end_timestamp)}"
            )
        lines.append("")
    return "\n".join(lines)


def build_manifest(
    *,
    tag: str,
    as_of: str,
    row_count: int,
    first_timestamp: int,
    last_timestamp: int,
    generation_revision: str,
    output_dir: Path,
) -> dict[str, object]:
    assets: dict[str, dict[str, object]] = {}
    for name in (CSV_ASSET, PARQUET_ASSET, PROVENANCE_ASSET):
        path = output_dir / name
        assets[name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return {
        "as_of": as_of,
        "assets": assets,
        "first_timestamp": first_timestamp,
        "generation_revision": generation_revision,
        "last_timestamp": last_timestamp,
        "row_count": row_count,
        "schema_version": 1,
        "tag": tag,
    }


def load_intro(path: Path, tag: str) -> str | None:
    if tag != FIRST_RELEASE_TAG:
        return None
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise PublishError(f"{path} is empty")
    return text


def create_github_release(
    *,
    tag: str,
    notes_path: Path,
    output_dir: Path,
) -> None:
    title = f"Bitstamp BTC/USD 1-minute · {tag.removeprefix(TAG_PREFIX)}"
    files = [
        output_dir / CSV_ASSET,
        output_dir / PARQUET_ASSET,
        output_dir / PROVENANCE_ASSET,
        output_dir / MANIFEST_ASSET,
    ]
    command = [
        "gh",
        "release",
        "create",
        tag,
        "--title",
        title,
        "--notes-file",
        str(notes_path),
        *[str(path) for path in files],
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise PublishError(
            f"gh release create failed: {completed.stderr.strip() or completed.stdout}"
        )


def build_snapshot(
    *,
    month: MonthWindow,
    historical_path: Path,
    updates_path: Path,
    sidecar_path: Path,
    intro_path: Path,
    output_dir: Path,
    as_of: str,
    generation_revision: str,
) -> SnapshotResult:
    dataset_start, updates_last = _require_validated_join(
        historical_path,
        updates_path,
    )
    if updates_last < month.last_minute:
        raise PublishError(
            f"updates last timestamp {updates_last} is before month cutoff "
            f"{month.last_minute}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / CSV_ASSET
    parquet_path = output_dir / PARQUET_ASSET
    provenance_path = output_dir / PROVENANCE_ASSET
    notes_path = output_dir / NOTES_ASSET
    manifest_path = output_dir / MANIFEST_ASSET

    rows = stream_joined_rows(historical_path, updates_path, month.last_minute)
    row_count, first_timestamp, last_timestamp, stats = write_join_assets(
        rows,
        csv_path=csv_path,
        parquet_path=parquet_path,
        month=month,
    )
    if first_timestamp != dataset_start:
        raise PublishError(
            f"joined first timestamp {first_timestamp} != historical start {dataset_start}"
        )

    sidecar = read_sidecar(sidecar_path)
    for interval in sidecar:
        if interval.flag not in ALLOWED_FLAGS:
            raise PublishError(f"unsupported sidecar flag {interval.flag}")
    clipped = clip_sidecar(sidecar, first_timestamp, month.end_timestamp)
    write_clipped_sidecar(provenance_path, clipped)

    intro = load_intro(intro_path, month.tag)
    notes = render_notes(
        month=month,
        stats=stats,
        sidecar=sidecar,
        intro_text=intro,
    )
    notes_path.write_text(notes, encoding="utf-8")

    manifest = build_manifest(
        tag=month.tag,
        as_of=as_of,
        row_count=row_count,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        generation_revision=generation_revision,
        output_dir=output_dir,
    )
    manifest_path.write_text(canonical_manifest_json(manifest), encoding="utf-8")
    return SnapshotResult(
        tag=month.tag,
        output_dir=output_dir,
        row_count=row_count,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        notes=notes,
        manifest=manifest,
    )


def _skip(reason: str, month: MonthWindow | None = None) -> SnapshotResult:
    logger.info(reason)
    return SnapshotResult(
        tag=month.tag if month is not None else "",
        output_dir=Path("."),
        row_count=0,
        first_timestamp=0,
        last_timestamp=0,
        notes="",
        manifest={},
        skipped=True,
        skip_reason=reason,
    )


def run_publish(
    *,
    year_month: str | None,
    if_due: bool,
    publish: bool,
    now: datetime,
    historical_path: Path,
    updates_path: Path,
    sidecar_path: Path,
    intro_path: Path,
    output_dir: Path,
    generation_revision: str | None = None,
) -> SnapshotResult:
    if year_month:
        year, month = parse_year_month(year_month)
        window = month_window(year, month)
        if if_due and not is_utc_first_of_month(now):
            return _skip(
                "Not the first UTC day of the month; skipping monthly snapshot.",
                window,
            )
        if if_due and previous_utc_month(now).tag != window.tag:
            return _skip(
                f"{window.tag} is not the just-completed UTC month; skipping.",
                window,
            )
    elif if_due:
        if not is_utc_first_of_month(now):
            return _skip(
                "Not the first UTC day of the month; skipping monthly snapshot."
            )
        window = previous_utc_month(now)
    else:
        raise PublishError("--year-month is required unless --if-due is set")

    if publish and github_release_exists(window.tag):
        if if_due:
            return _skip(
                f"GitHub Release {window.tag} already exists; skipping.",
                window,
            )
        raise PublishError(f"GitHub Release {window.tag} already exists")

    revision = generation_revision or git_revision()
    as_of = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = build_snapshot(
        month=window,
        historical_path=historical_path,
        updates_path=updates_path,
        sidecar_path=sidecar_path,
        intro_path=intro_path,
        output_dir=output_dir,
        as_of=as_of,
        generation_revision=revision,
    )
    identity = sha256_file(output_dir / MANIFEST_ASSET)
    logger.info(
        "Built %s: %s rows, manifest sha256=%s",
        result.tag,
        result.row_count,
        identity,
    )
    if publish:
        create_github_release(
            tag=result.tag,
            notes_path=output_dir / NOTES_ASSET,
            output_dir=output_dir,
        )
        logger.info("Published GitHub Release %s", result.tag)
    return result


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
        description="Build (and optionally publish) a monthly Bitstamp snapshot.",
    )
    parser.add_argument("--year-month", help="UTC month to snapshot (YYYY-MM).")
    parser.add_argument(
        "--if-due",
        action="store_true",
        help="On the 1st UTC day, snapshot the previous month; otherwise skip.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Create the GitHub Release after a successful build.",
    )
    parser.add_argument(
        "--historical",
        default=str(DEFAULT_HISTORICAL_PATH),
    )
    parser.add_argument(
        "--updates",
        default=str(DEFAULT_UPDATES_PATH),
    )
    parser.add_argument(
        "--sidecar",
        default=str(DEFAULT_SIDECAR_PATH),
    )
    parser.add_argument(
        "--intro",
        default=str(DEFAULT_INTRO_PATH),
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/monthly-snapshot",
    )
    parser.add_argument(
        "--now",
        help="Override current UTC time (ISO-8601) for tests and dry runs.",
    )
    args = parser.parse_args(argv)

    if args.now:
        raw = args.now
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        now = datetime.fromisoformat(raw)
    else:
        now = datetime.now(UTC)

    try:
        run_publish(
            year_month=args.year_month,
            if_due=args.if_due,
            publish=args.publish,
            now=now,
            historical_path=Path(args.historical),
            updates_path=Path(args.updates),
            sidecar_path=Path(args.sidecar),
            intro_path=Path(args.intro),
            output_dir=Path(args.output_dir),
        )
    except PublishError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
