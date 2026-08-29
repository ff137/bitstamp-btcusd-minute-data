"""Validate the provenance candidate ledger against OHLCV and the sidecar."""

import argparse
import csv
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.outage_candidates import (
    ALLOWED_LEDGER_STATUSES,
    CANDIDATE_HEADER,
    CANDIDATES_FILENAME,
    DEFAULT_RESEARCH_DIR,
    DETECTOR_VERSION,
    MODEL_FILENAME,
    PUBLISHED_STATUSES,
    STATUS_CORROBORATED,
    STATUS_PENDING_REVIEW,
    STATUS_REVIEWED_UNCONFIRMED,
    DetectorParams,
    build_candidates,
    hash_ohlcv_prefix,
    is_heuristic_reference,
    is_url_reference,
    read_candidates,
    scan_ohlcv,
)
from scripts.provenance import (
    DEFAULT_HISTORICAL_PATH,
    DEFAULT_SIDECAR_PATH,
    DEFAULT_UPDATES_PATH,
    EXCLUSION_FLAGS,
    FLAG_SUSPECTED_OUTAGE,
    bounding_closes,
    collect_closes,
    format_price_jump,
    iter_ohlcv_rows,
    needed_close_timestamps,
    read_sidecar,
)
from scripts.validate_provenance import (
    MAX_ISSUES,
    ValidationIssue,
    _add_issue,
    format_issue,
    validate_sidecar,
)


def validate_ledger(
    *,
    ledger_path: str | Path,
    sidecar_path: str | Path,
    model_path: str | Path | None = None,
    ohlcv_paths: list[str | Path] | None = None,
    research_dir: str | Path | None = None,
) -> tuple[bool, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    ledger_path = Path(ledger_path)
    sidecar_path = Path(sidecar_path)
    resolved_research = (
        Path(research_dir) if research_dir is not None else ledger_path.parent
    )
    resolved_model = (
        Path(model_path)
        if model_path is not None
        else resolved_research / MODEL_FILENAME
    )
    if ohlcv_paths is None:
        paths: list[str | Path] = [DEFAULT_HISTORICAL_PATH, DEFAULT_UPDATES_PATH]
    else:
        paths = ohlcv_paths
    available = [path for path in paths if Path(path).exists()]

    if not ledger_path.exists():
        _add_issue(issues, row_number=None, message=f"ledger is missing: {ledger_path}")
        return False, issues

    try:
        with ledger_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
    except OSError as exc:
        _add_issue(issues, row_number=None, message=f"cannot read ledger: {exc}")
        return False, issues

    if header != list(CANDIDATE_HEADER):
        _add_issue(
            issues,
            row_number=None,
            message=(f"invalid ledger header: expected {','.join(CANDIDATE_HEADER)!r}"),
        )
        return False, issues

    try:
        candidates = read_candidates(ledger_path)
    except ValueError as exc:
        _add_issue(issues, row_number=None, message=str(exc))
        return False, issues

    seen_ids: set[str] = set()
    seen_bounds: set[tuple[int, int]] = set()
    previous_key: tuple[int, int] | None = None
    published_bounds: set[tuple[int, int]] = set()
    published_by_bounds: dict[tuple[int, int], object] = {}

    for index, item in enumerate(candidates, start=2):
        if item.candidate_id in seen_ids:
            _add_issue(
                issues,
                row_number=index,
                message=f"duplicate candidate_id {item.candidate_id}",
            )
        seen_ids.add(item.candidate_id)
        bounds = (item.start_timestamp, item.end_timestamp)
        if bounds in seen_bounds:
            _add_issue(
                issues,
                row_number=index,
                message=f"duplicate interval {bounds[0]},{bounds[1]}",
            )
        seen_bounds.add(bounds)
        if previous_key is not None and bounds < previous_key:
            _add_issue(
                issues,
                row_number=index,
                message="out-of-order candidate interval",
            )
        previous_key = bounds
        if item.start_timestamp >= item.end_timestamp:
            _add_issue(
                issues,
                row_number=index,
                message="start_timestamp must be < end_timestamp",
            )
        expected_minutes = (item.end_timestamp - item.start_timestamp) // 60
        if item.duration_minutes != expected_minutes:
            _add_issue(
                issues,
                row_number=index,
                message=(
                    f"duration_minutes {item.duration_minutes} does not match "
                    f"{expected_minutes}"
                ),
            )
        if item.status not in ALLOWED_LEDGER_STATUSES:
            _add_issue(
                issues,
                row_number=index,
                message=f"unsupported status {item.status!r}",
            )
        if item.status == STATUS_CORROBORATED:
            if not is_url_reference(item.reference):
                _add_issue(
                    issues,
                    row_number=index,
                    message="corroborated candidate must have URL reference(s)",
                )
            if not item.reviewer or not item.decision_date or not item.notes_path:
                _add_issue(
                    issues,
                    row_number=index,
                    message="corroborated candidate needs reviewer, date, and notes",
                )
        if item.status == STATUS_REVIEWED_UNCONFIRMED:
            if not is_heuristic_reference(item.reference):
                _add_issue(
                    issues,
                    row_number=index,
                    message="reviewed_unconfirmed must use a zero_volume>= reference",
                )
            if not item.reviewer or not item.decision_date or not item.notes_path:
                _add_issue(
                    issues,
                    row_number=index,
                    message="reviewed_unconfirmed needs reviewer, date, and notes",
                )
        if item.status == STATUS_PENDING_REVIEW and (
            item.reviewer or item.decision_date or item.notes_path
        ):
            _add_issue(
                issues,
                row_number=index,
                message="pending_review must not carry a completed review",
            )
        if item.status in PUBLISHED_STATUSES:
            published_bounds.add(bounds)
            published_by_bounds[bounds] = item

    sidecar_result = validate_sidecar(sidecar_path)
    if not sidecar_result.valid:
        issues.extend(sidecar_result.issues)
        return False, issues

    sidecar_intervals = read_sidecar(sidecar_path)
    sidecar_suspected = [
        item for item in sidecar_intervals if item.flag == FLAG_SUSPECTED_OUTAGE
    ]
    sidecar_bounds = {
        (item.start_timestamp, item.end_timestamp) for item in sidecar_suspected
    }

    for start, end in sorted(published_bounds - sidecar_bounds):
        _add_issue(
            issues,
            row_number=None,
            message=f"published ledger candidate missing from sidecar: {start},{end}",
        )
    for start, end in sorted(sidecar_bounds - published_bounds):
        _add_issue(
            issues,
            row_number=None,
            message=f"sidecar suspected_outage has no reviewed ledger row: {start},{end}",
        )

    if resolved_model.is_file() and available:
        model = json.loads(resolved_model.read_text(encoding="utf-8"))
        as_of = int(model["as_of_timestamp"])
        expected_hash = str(model["prefix_hash_sha256"])
        actual_hash, prefix_rows, first_ts, last_ts = hash_ohlcv_prefix(
            available, as_of
        )
        if actual_hash != expected_hash:
            _add_issue(
                issues,
                row_number=None,
                message=(
                    f"prefix hash mismatch: expected {expected_hash}, found {actual_hash}"
                ),
            )
        if prefix_rows != int(model["row_count"]):
            _add_issue(
                issues,
                row_number=None,
                message=(
                    f"prefix row count mismatch: expected {model['row_count']}, "
                    f"found {prefix_rows}"
                ),
            )
        if first_ts != int(model["first_timestamp"]) or last_ts != int(
            model["last_timestamp"]
        ):
            _add_issue(
                issues,
                row_number=None,
                message="prefix first/last timestamp does not match model.json",
            )
        if str(model.get("detector_version")) != DETECTOR_VERSION:
            _add_issue(
                issues,
                row_number=None,
                message=f"unexpected detector_version {model.get('detector_version')!r}",
            )

        params = DetectorParams(
            as_of_timestamp=as_of,
            liquid_publish_seconds=int(model["liquid_publish_seconds"]),
            liquid_p99_ceiling_minutes=int(model["liquid_p99_ceiling_minutes"]),
            liquid_streak_months=int(model["liquid_streak_months"]),
        )
        exclusions = [
            item for item in sidecar_intervals if item.flag in EXCLUSION_FLAGS
        ]
        scan = scan_ohlcv(available, params=params)
        regenerated = {
            (item.start_timestamp, item.end_timestamp): item
            for item in build_candidates(scan, exclusions)
        }
        ledger_bounds = {
            (item.start_timestamp, item.end_timestamp) for item in candidates
        }
        extra = sorted(ledger_bounds - set(regenerated))
        missing = sorted(set(regenerated) - ledger_bounds)
        for start, end in extra[:5]:
            _add_issue(
                issues,
                row_number=None,
                message=f"ledger interval is not in the frozen prefix scan: {start},{end}",
            )
        for start, end in missing[:5]:
            _add_issue(
                issues,
                row_number=None,
                message=f"detected prefix run missing from ledger: {start},{end}",
            )
        if scan.liquid_start != model.get("liquid_start_timestamp"):
            _add_issue(
                issues,
                row_number=None,
                message="liquid_start_timestamp does not match a prefix rescan",
            )

    if available:
        closes = collect_closes(available, needed_close_timestamps(sidecar_intervals))
        for interval in sidecar_intervals:
            expected_jump = format_price_jump(*bounding_closes(interval, closes))
            if interval.price_jump != expected_jump:
                _add_issue(
                    issues,
                    row_number=None,
                    message=(
                        f"price_jump mismatch for {interval.start_timestamp},"
                        f"{interval.end_timestamp}: expected {expected_jump!r}, "
                        f"found {interval.price_jump!r}"
                    ),
                )
        expected_minutes = {
            (item.start_timestamp, item.end_timestamp): (
                item.end_timestamp - item.start_timestamp
            )
            // 60
            for item in sidecar_suspected
        }
        zero_counts = {key: 0 for key in expected_minutes}
        nonzero_counts = {key: 0 for key in expected_minutes}
        if expected_minutes:
            windows = list(expected_minutes)
            for timestamp, _open, _high, _low, _close, volume, _raw in iter_ohlcv_rows(
                available
            ):
                for start, end in windows:
                    if start <= timestamp < end:
                        if volume == 0:
                            zero_counts[start, end] += 1
                        else:
                            nonzero_counts[start, end] += 1
            for start, end in expected_minutes:
                zeros = zero_counts[start, end]
                nonzeros = nonzero_counts[start, end]
                missing = expected_minutes[start, end] - zeros - nonzeros
                if nonzeros or missing or zeros == 0:
                    _add_issue(
                        issues,
                        row_number=None,
                        message=(
                            f"suspected_outage {start},{end} is not an exact "
                            f"zero-volume run (zeros={zeros} nonzero={nonzeros} "
                            f"missing={missing})"
                        ),
                    )

    notes_root = resolved_research
    for item in candidates:
        if item.status not in PUBLISHED_STATUSES:
            continue
        notes_path = notes_root / item.notes_path if item.notes_path else None
        if notes_path is None or not notes_path.is_file():
            _add_issue(
                issues,
                row_number=None,
                message=f"{item.candidate_id}: notes file missing ({item.notes_path})",
            )

    return not issues, issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the provenance candidate ledger against candles and the sidecar.",
    )
    parser.add_argument(
        "--ledger",
        default=str(DEFAULT_RESEARCH_DIR / CANDIDATES_FILENAME),
    )
    parser.add_argument("--sidecar", default=str(DEFAULT_SIDECAR_PATH))
    parser.add_argument("--model", default=str(DEFAULT_RESEARCH_DIR / MODEL_FILENAME))
    parser.add_argument("--historical", default=str(DEFAULT_HISTORICAL_PATH))
    parser.add_argument("--updates", default=str(DEFAULT_UPDATES_PATH))
    args = parser.parse_args(argv)

    valid, issues = validate_ledger(
        ledger_path=args.ledger,
        sidecar_path=args.sidecar,
        model_path=args.model,
        ohlcv_paths=[args.historical, args.updates],
    )
    if not valid:
        print("ledger validation failed:", file=sys.stderr)
        for issue in issues:
            print(f"  {format_issue(issue)}", file=sys.stderr)
        if len(issues) >= MAX_ISSUES:
            print(f"  (only first {MAX_ISSUES} issues shown)", file=sys.stderr)
        return 1

    print(f"{args.ledger}: consistent with {args.sidecar} and frozen OHLCV prefix")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
