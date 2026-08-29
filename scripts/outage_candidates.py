"""Regime-aware unexplained zero-volume candidate detection."""

import argparse
import csv
import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.provenance import (
    DEFAULT_HISTORICAL_PATH,
    DEFAULT_SIDECAR_PATH,
    DEFAULT_UPDATES_PATH,
    EXCLUSION_FLAGS,
    FLAG_SUSPECTED_OUTAGE,
    MINUTE_SECONDS,
    Interval,
    format_price_jump,
    iter_ohlcv_rows,
    merge_adjacent,
    needed_close_timestamps,
    subtract_exclusions,
)

DETECTOR_VERSION = "regime-v1"
HEURISTIC_REFERENCE = "zero_volume>=60m"
ABSOLUTE_MIN_SECONDS = 30 * 60
LIQUID_P99_CEILING_MINUTES = 30
LIQUID_STREAK_MONTHS = 6
LIQUID_PUBLISH_SECONDS = 60 * 60
DEFAULT_RESEARCH_DIR = Path("data/provenance/research")
MODEL_FILENAME = "model.json"
CANDIDATES_FILENAME = "candidates.csv"
NOTES_FILENAME = "NOTES.md"
logger = logging.getLogger(__name__)

CANDIDATE_HEADER = (
    "candidate_id",
    "start_timestamp",
    "end_timestamp",
    "duration_minutes",
    "regime",
    "detector_version",
    "rarity_rank",
    "status",
    "decision_date",
    "reviewer",
    "reference",
    "notes_path",
)

STATUS_PENDING_REVIEW = "pending_review"
STATUS_REVIEWED_UNCONFIRMED = "reviewed_unconfirmed"
STATUS_CORROBORATED = "corroborated"
STATUS_EXCLUDED = "excluded"
STATUS_THIN_UNPUBLISHED = "thin_unpublished"
STATUS_BELOW_FLOOR = "below_floor"

ALLOWED_LEDGER_STATUSES = frozenset(
    {
        STATUS_PENDING_REVIEW,
        STATUS_REVIEWED_UNCONFIRMED,
        STATUS_CORROBORATED,
        STATUS_EXCLUDED,
        STATUS_THIN_UNPUBLISHED,
        STATUS_BELOW_FLOOR,
    }
)
PUBLISHED_STATUSES = frozenset({STATUS_CORROBORATED, STATUS_REVIEWED_UNCONFIRMED})
REVIEW_FLOOR_STATUSES = frozenset(
    {
        STATUS_PENDING_REVIEW,
        STATUS_REVIEWED_UNCONFIRMED,
        STATUS_CORROBORATED,
        STATUS_EXCLUDED,
    }
)


@dataclass(frozen=True)
class DetectorParams:
    version: str = DETECTOR_VERSION
    absolute_min_seconds: int = ABSOLUTE_MIN_SECONDS
    liquid_p99_ceiling_minutes: int = LIQUID_P99_CEILING_MINUTES
    liquid_streak_months: int = LIQUID_STREAK_MONTHS
    liquid_publish_seconds: int = LIQUID_PUBLISH_SECONDS
    forced_liquid_start: int | None = None
    as_of_timestamp: int | None = None


DEFAULT_PARAMS = DetectorParams()


@dataclass(frozen=True)
class ZeroRun:
    start_timestamp: int
    end_timestamp: int
    price_jump: str = ""

    @property
    def duration_seconds(self) -> int:
        return self.end_timestamp - self.start_timestamp

    @property
    def duration_minutes(self) -> int:
        return self.duration_seconds // MINUTE_SECONDS


@dataclass(frozen=True)
class MonthlyActivity:
    month: str
    minutes: int
    trades: int
    gap_p99_minutes: float | None
    trade_share_pct: float


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    start_timestamp: int
    end_timestamp: int
    duration_minutes: int
    regime: str
    detector_version: str
    rarity_rank: int
    status: str
    decision_date: str
    reviewer: str
    reference: str
    notes_path: str

    def as_row(self) -> list[str]:
        return [
            self.candidate_id,
            str(self.start_timestamp),
            str(self.end_timestamp),
            str(self.duration_minutes),
            self.regime,
            self.detector_version,
            str(self.rarity_rank),
            self.status,
            self.decision_date,
            self.reviewer,
            self.reference,
            self.notes_path,
        ]


@dataclass(frozen=True)
class ScanResult:
    monthly: tuple[MonthlyActivity, ...]
    zero_runs: tuple[ZeroRun, ...]
    closes: dict[int, Decimal]
    liquid_start: int | None
    row_count: int
    first_timestamp: int | None
    last_timestamp: int | None
    prefix_hash_sha256: str
    as_of_timestamp: int | None


def month_key(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m")


def month_start_unix(month: str) -> int:
    return int(datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC).timestamp())


def candidate_id_for(start_timestamp: int, end_timestamp: int) -> str:
    return f"zv-{start_timestamp}-{end_timestamp}"


def is_heuristic_reference(reference: str) -> bool:
    return reference.startswith("zero_volume>=")


def is_url_reference(reference: str) -> bool:
    tokens = [part.strip() for part in reference.split(";") if part.strip()]
    return bool(tokens) and all(
        token.startswith(("https://", "http://")) for token in tokens
    )


def _percentile(values: list[int], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (q / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def infer_liquid_start(
    monthly: Sequence[MonthlyActivity],
    params: DetectorParams = DEFAULT_PARAMS,
) -> int | None:
    if params.forced_liquid_start is not None:
        return params.forced_liquid_start
    streak = 0
    streak_start: str | None = None
    for row in monthly:
        eligible = (
            row.gap_p99_minutes is not None
            and row.gap_p99_minutes <= params.liquid_p99_ceiling_minutes
        )
        if eligible:
            if streak == 0:
                streak_start = row.month
            streak += 1
            if streak >= params.liquid_streak_months and streak_start is not None:
                return month_start_unix(streak_start)
        else:
            streak = 0
            streak_start = None
    return None


def _update_prefix_hash(digest, fields: Sequence[str]) -> None:
    digest.update(",".join(fields).encode("utf-8"))
    digest.update(b"\n")


def scan_ohlcv(
    paths: Sequence[str | Path],
    *,
    extra_needed_timestamps: Iterable[int] | None = None,
    params: DetectorParams = DEFAULT_PARAMS,
) -> ScanResult:
    needed = set(extra_needed_timestamps or [])
    closes: dict[int, Decimal] = {}
    monthly_minutes: dict[str, int] = defaultdict(int)
    monthly_trades: dict[str, int] = defaultdict(int)
    monthly_gaps: dict[str, list[int]] = defaultdict(list)
    zero_runs: list[ZeroRun] = []
    close_before: Decimal | None = None
    last_trade: int | None = None
    run_start: int | None = None
    run_end: int | None = None
    row_count = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    digest = hashlib.sha256()

    def flush_run(close_after: Decimal | None) -> None:
        nonlocal run_start, run_end
        if run_start is None or run_end is None:
            return
        duration = run_end - run_start
        if duration >= params.absolute_min_seconds:
            zero_runs.append(
                ZeroRun(
                    run_start,
                    run_end,
                    format_price_jump(close_before, close_after),
                )
            )
        run_start = None
        run_end = None

    for timestamp, open_, high, low, close, volume, raw_fields in iter_ohlcv_rows(
        paths
    ):
        if params.as_of_timestamp is not None and timestamp > params.as_of_timestamp:
            break
        _update_prefix_hash(digest, raw_fields)
        row_count += 1
        if first_timestamp is None:
            first_timestamp = timestamp
        last_timestamp = timestamp
        month = month_key(timestamp)
        monthly_minutes[month] += 1
        if timestamp in needed:
            closes[timestamp] = close
        if volume == 0:
            if run_start is None:
                run_start = timestamp
                if close_before is not None:
                    closes[timestamp - MINUTE_SECONDS] = close_before
            run_end = timestamp + MINUTE_SECONDS
            continue
        monthly_trades[month] += 1
        if last_trade is not None:
            gap_minutes = (timestamp - last_trade) // MINUTE_SECONDS
            monthly_gaps[month].append(gap_minutes)
        last_trade = timestamp
        if run_start is not None:
            closes[timestamp] = close
        flush_run(close)
        close_before = close

    flush_run(None)

    monthly = tuple(
        MonthlyActivity(
            month=month,
            minutes=monthly_minutes[month],
            trades=monthly_trades[month],
            gap_p99_minutes=(
                round(_percentile(monthly_gaps[month], 99), 2)
                if monthly_gaps[month]
                else None
            ),
            trade_share_pct=round(
                100.0 * monthly_trades[month] / monthly_minutes[month], 3
            )
            if monthly_minutes[month]
            else 0.0,
        )
        for month in sorted(monthly_minutes)
    )
    liquid_start = infer_liquid_start(monthly, params)
    as_of = params.as_of_timestamp
    if as_of is None:
        as_of = last_timestamp
    return ScanResult(
        monthly=monthly,
        zero_runs=tuple(zero_runs),
        closes=closes,
        liquid_start=liquid_start,
        row_count=row_count,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        prefix_hash_sha256=digest.hexdigest(),
        as_of_timestamp=as_of,
    )


def hash_ohlcv_prefix(
    paths: Sequence[str | Path],
    as_of_timestamp: int,
) -> tuple[str, int, int | None, int | None]:
    """SHA-256 of canonical CSV rows with timestamp <= as_of_timestamp."""
    digest = hashlib.sha256()
    row_count = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    for timestamp, _open, _high, _low, _close, _volume, raw_fields in iter_ohlcv_rows(
        paths
    ):
        if timestamp > as_of_timestamp:
            break
        _update_prefix_hash(digest, raw_fields)
        row_count += 1
        if first_timestamp is None:
            first_timestamp = timestamp
        last_timestamp = timestamp
    return digest.hexdigest(), row_count, first_timestamp, last_timestamp


def classify_run(
    run: ZeroRun,
    *,
    liquid_start: int | None,
    params: DetectorParams = DEFAULT_PARAMS,
) -> str:
    if liquid_start is None or run.start_timestamp < liquid_start:
        return STATUS_THIN_UNPUBLISHED
    if run.duration_seconds < params.liquid_publish_seconds:
        return STATUS_BELOW_FLOOR
    return STATUS_PENDING_REVIEW


def split_run_around_exclusions(
    run: ZeroRun,
    exclusions: Sequence[Interval],
) -> list[ZeroRun]:
    windows = [item for item in exclusions if item.flag in EXCLUSION_FLAGS]
    pieces = subtract_exclusions(run.start_timestamp, run.end_timestamp, windows)
    return [
        ZeroRun(piece_start, piece_end, run.price_jump)
        for piece_start, piece_end in pieces
        if piece_end > piece_start
    ]


def detect_suspected_outages(
    paths: Sequence[str | Path],
    exclusions: Sequence[Interval] | None = None,
    *,
    extra_needed_timestamps: Iterable[int] | None = None,
    params: DetectorParams = DEFAULT_PARAMS,
) -> tuple[list[Interval], dict[int, Decimal], ScanResult]:
    """Emit liquid-regime review-floor zero-volume intervals."""
    exclusion_list = list(exclusions or [])
    extra = set(extra_needed_timestamps or [])
    extra.update(needed_close_timestamps(exclusion_list))
    scan = scan_ohlcv(paths, extra_needed_timestamps=extra, params=params)
    suspected: list[Interval] = []
    for run in scan.zero_runs:
        for piece in split_run_around_exclusions(run, exclusion_list):
            if (
                classify_run(piece, liquid_start=scan.liquid_start, params=params)
                != STATUS_PENDING_REVIEW
            ):
                continue
            suspected.append(
                Interval(
                    piece.start_timestamp,
                    piece.end_timestamp,
                    FLAG_SUSPECTED_OUTAGE,
                    HEURISTIC_REFERENCE,
                    piece.price_jump,
                )
            )
    return merge_adjacent(suspected), scan.closes, scan


def published_intervals_from_ledger(ledger: Sequence[Candidate]) -> list[Interval]:
    """Public suspected rows: corroborated or explicitly reviewed only."""
    intervals: list[Interval] = []
    for item in ledger:
        if item.status not in PUBLISHED_STATUSES:
            continue
        if not item.reviewer or not item.decision_date or not item.notes_path:
            raise ValueError(
                f"{item.candidate_id}: published candidates need reviewer, "
                "decision_date, and notes_path"
            )
        if item.status == STATUS_CORROBORATED and not is_url_reference(item.reference):
            raise ValueError(
                f"{item.candidate_id}: corroborated row needs URL evidence"
            )
        if item.status == STATUS_REVIEWED_UNCONFIRMED and not is_heuristic_reference(
            item.reference
        ):
            raise ValueError(
                f"{item.candidate_id}: reviewed_unconfirmed must use {HEURISTIC_REFERENCE}"
            )
        intervals.append(
            Interval(
                item.start_timestamp,
                item.end_timestamp,
                FLAG_SUSPECTED_OUTAGE,
                item.reference,
            )
        )
    return merge_adjacent(intervals)


def _with_status(item: Candidate, **overrides: object) -> Candidate:
    values = {
        "candidate_id": item.candidate_id,
        "start_timestamp": item.start_timestamp,
        "end_timestamp": item.end_timestamp,
        "duration_minutes": item.duration_minutes,
        "regime": item.regime,
        "detector_version": item.detector_version,
        "rarity_rank": item.rarity_rank,
        "status": item.status,
        "decision_date": item.decision_date,
        "reviewer": item.reviewer,
        "reference": item.reference,
        "notes_path": item.notes_path,
    }
    values.update(overrides)
    return Candidate(**values)  # type: ignore[arg-type]


def build_candidates(
    scan: ScanResult,
    exclusions: Sequence[Interval] | None = None,
    *,
    params: DetectorParams = DEFAULT_PARAMS,
    existing_ledger: Sequence[Candidate] = (),
    decision_date: str = "",
) -> list[Candidate]:
    previous = {
        (item.start_timestamp, item.end_timestamp): item for item in existing_ledger
    }
    exclusion_list = list(exclusions or [])
    rows: list[Candidate] = []
    for run in scan.zero_runs:
        pieces = split_run_around_exclusions(run, exclusion_list)
        wholly_excluded = not pieces
        targets = (
            [ZeroRun(run.start_timestamp, run.end_timestamp, run.price_jump)]
            if wholly_excluded
            else pieces
        )
        for piece in targets:
            status = (
                STATUS_EXCLUDED
                if wholly_excluded
                else classify_run(piece, liquid_start=scan.liquid_start, params=params)
            )
            prior = previous.get((piece.start_timestamp, piece.end_timestamp))
            reference = HEURISTIC_REFERENCE if status in REVIEW_FLOOR_STATUSES else ""
            reviewer = ""
            notes_path = ""
            date = ""
            if prior is not None:
                reviewer = prior.reviewer
                notes_path = prior.notes_path
                date = prior.decision_date
                if prior.status in PUBLISHED_STATUSES or (
                    prior.status == STATUS_EXCLUDED and prior.reviewer
                ):
                    status = prior.status
                    reference = prior.reference or reference
                elif prior.reference and not is_heuristic_reference(prior.reference):
                    reference = prior.reference
            if status == STATUS_PENDING_REVIEW and not reference:
                reference = HEURISTIC_REFERENCE
            rows.append(
                Candidate(
                    candidate_id=candidate_id_for(
                        piece.start_timestamp, piece.end_timestamp
                    ),
                    start_timestamp=piece.start_timestamp,
                    end_timestamp=piece.end_timestamp,
                    duration_minutes=piece.duration_minutes,
                    regime="liquid"
                    if scan.liquid_start is not None
                    and piece.start_timestamp >= scan.liquid_start
                    else "thin",
                    detector_version=params.version,
                    rarity_rank=0,
                    status=status,
                    decision_date=date or (decision_date if reviewer else ""),
                    reviewer=reviewer,
                    reference=reference,
                    notes_path=notes_path,
                )
            )

    published = [item for item in rows if item.status in REVIEW_FLOOR_STATUSES]
    published.sort(key=lambda item: (-item.duration_minutes, item.start_timestamp))
    ranks = {
        (item.start_timestamp, item.end_timestamp): rank
        for rank, item in enumerate(published, start=1)
    }
    ranked: list[Candidate] = []
    for item in rows:
        rank = ranks.get((item.start_timestamp, item.end_timestamp), 0)
        ranked.append(_with_status(item, rarity_rank=rank))
    ranked.sort(key=lambda item: (item.start_timestamp, item.end_timestamp))
    return ranked


def read_candidates(path: str | Path) -> list[Candidate]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != list(CANDIDATE_HEADER):
            raise ValueError(f"{path}: invalid candidate ledger header")
        rows: list[Candidate] = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(CANDIDATE_HEADER):
                raise ValueError(f"{path}: expected {len(CANDIDATE_HEADER)} columns")
            rows.append(
                Candidate(
                    candidate_id=row[0],
                    start_timestamp=int(row[1]),
                    end_timestamp=int(row[2]),
                    duration_minutes=int(row[3]),
                    regime=row[4],
                    detector_version=row[5],
                    rarity_rank=int(row[6]) if row[6] else 0,
                    status=row[7],
                    decision_date=row[8],
                    reviewer=row[9],
                    reference=row[10],
                    notes_path=row[11],
                )
            )
    return rows


def review_floor_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Liquid-regime ≥60m rows committed to the research ledger."""
    return [item for item in candidates if item.status in REVIEW_FLOOR_STATUSES]


def write_candidates(path: str | Path, candidates: Sequence[Candidate]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CANDIDATE_HEADER)
        for item in candidates:
            writer.writerow(item.as_row())


def sensitivity_counts(
    scan: ScanResult,
    exclusions: Sequence[Interval] | None = None,
) -> dict[str, dict[str, int]]:
    floors_minutes = (30, 60, 120, 240, 720)
    counts = {
        "thin": {str(floor): 0 for floor in floors_minutes},
        "liquid": {str(floor): 0 for floor in floors_minutes},
    }
    for run in scan.zero_runs:
        for piece in split_run_around_exclusions(run, exclusions or []):
            bucket = (
                "liquid"
                if scan.liquid_start is not None
                and piece.start_timestamp >= scan.liquid_start
                else "thin"
            )
            minutes = piece.duration_minutes
            for floor in floors_minutes:
                if minutes >= floor:
                    counts[bucket][str(floor)] += 1
    return counts


def write_model(
    path: str | Path,
    *,
    scan: ScanResult,
    params: DetectorParams,
    ohlcv_paths: Sequence[str | Path],
    sensitivity: dict[str, dict[str, int]],
) -> None:
    payload = {
        "detector_version": params.version,
        "absolute_min_seconds": params.absolute_min_seconds,
        "liquid_p99_ceiling_minutes": params.liquid_p99_ceiling_minutes,
        "liquid_streak_months": params.liquid_streak_months,
        "liquid_publish_seconds": params.liquid_publish_seconds,
        "liquid_start_timestamp": scan.liquid_start,
        "liquid_start_iso": (
            datetime.fromtimestamp(scan.liquid_start, UTC).isoformat()
            if scan.liquid_start is not None
            else None
        ),
        "as_of_timestamp": scan.as_of_timestamp,
        "as_of_iso": (
            datetime.fromtimestamp(scan.as_of_timestamp, UTC).isoformat()
            if scan.as_of_timestamp is not None
            else None
        ),
        "prefix_hash_sha256": scan.prefix_hash_sha256,
        "ohlcv_paths": [str(path) for path in ohlcv_paths],
        "row_count": scan.row_count,
        "first_timestamp": scan.first_timestamp,
        "last_timestamp": scan.last_timestamp,
        "zero_runs_ge_30m": len(scan.zero_runs),
        "sensitivity_counts": sensitivity,
        "heuristic_reference": HEURISTIC_REFERENCE,
        "publication_rule": (
            "Duration selects review candidates. Public suspected_outage rows "
            "require corroboration or explicit human review."
        ),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_model(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
        description="Generate regime-aware unexplained zero-volume candidates.",
    )
    parser.add_argument("--historical", default=str(DEFAULT_HISTORICAL_PATH))
    parser.add_argument("--updates", default=str(DEFAULT_UPDATES_PATH))
    parser.add_argument("--sidecar", default=str(DEFAULT_SIDECAR_PATH))
    parser.add_argument("--research-dir", default=str(DEFAULT_RESEARCH_DIR))
    parser.add_argument(
        "--as-of",
        type=int,
        default=None,
        help="Freeze the snapshot at this Unix timestamp (inclusive).",
    )
    args = parser.parse_args(argv)

    from scripts.provenance import read_sidecar

    ohlcv_paths = [args.historical, args.updates]
    available = [path for path in ohlcv_paths if Path(path).exists()]
    exclusions = [
        item for item in read_sidecar(args.sidecar) if item.flag in EXCLUSION_FLAGS
    ]
    params = DetectorParams(as_of_timestamp=args.as_of)
    extra = needed_close_timestamps(exclusions)
    scan = scan_ohlcv(available, extra_needed_timestamps=extra, params=params)
    research_dir = Path(args.research_dir)
    existing = read_candidates(research_dir / CANDIDATES_FILENAME)
    candidates = build_candidates(scan, exclusions, existing_ledger=existing)
    committed = [
        _with_status(item, notes_path=NOTES_FILENAME)
        if item.status in PUBLISHED_STATUSES
        else item
        for item in review_floor_candidates(candidates)
    ]
    write_candidates(research_dir / CANDIDATES_FILENAME, committed)
    write_model(
        research_dir / MODEL_FILENAME,
        scan=scan,
        params=params,
        ohlcv_paths=available,
        sensitivity=sensitivity_counts(scan, exclusions),
    )
    logger.info(
        "as_of=%s liquid_start=%s prefix=%s committed=%s pending=%s "
        "reviewed_unconfirmed=%s corroborated=%s excluded=%s",
        scan.as_of_timestamp,
        scan.liquid_start,
        scan.prefix_hash_sha256,
        len(committed),
        sum(1 for item in committed if item.status == STATUS_PENDING_REVIEW),
        sum(1 for item in committed if item.status == STATUS_REVIEWED_UNCONFIRMED),
        sum(1 for item in committed if item.status == STATUS_CORROBORATED),
        sum(1 for item in committed if item.status == STATUS_EXCLUDED),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
