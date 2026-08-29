"""Tests for regime inference, review states, and the candidate ledger."""

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.outage_candidates import (
    DETECTOR_VERSION,
    HEURISTIC_REFERENCE,
    STATUS_CORROBORATED,
    STATUS_PENDING_REVIEW,
    STATUS_REVIEWED_UNCONFIRMED,
    STATUS_THIN_UNPUBLISHED,
    Candidate,
    DetectorParams,
    MonthlyActivity,
    ZeroRun,
    classify_run,
    hash_ohlcv_prefix,
    infer_liquid_start,
    is_heuristic_reference,
    is_url_reference,
    month_start_unix,
    published_intervals_from_ledger,
    scan_ohlcv,
    write_candidates,
    write_model,
)
from scripts.provenance import FLAG_CONFIRMED_OUTAGE, FLAG_SUSPECTED_OUTAGE
from scripts.validate_ledger import validate_ledger
from tests.test_provenance import (
    sidecar_row,
    write_ohlcv,
    write_sidecar,
    zero_run_with_bounds,
)

SIXTY_MINUTES = 60


def _month(year: int, month: int, p99: float) -> MonthlyActivity:
    label = f"{year}-{month:02d}"
    return MonthlyActivity(
        month=label,
        minutes=40_000,
        trades=30_000,
        gap_p99_minutes=p99,
        trade_share_pct=75.0,
    )


def _candidate(**overrides: object) -> Candidate:
    values: dict[str, object] = {
        "candidate_id": "zv-100-160",
        "start_timestamp": 1736208000,
        "end_timestamp": 1736211600,
        "duration_minutes": 60,
        "regime": "liquid",
        "detector_version": DETECTOR_VERSION,
        "rarity_rank": 1,
        "status": STATUS_PENDING_REVIEW,
        "decision_date": "",
        "reviewer": "",
        "reference": HEURISTIC_REFERENCE,
        "notes_path": "",
    }
    values.update(overrides)
    return Candidate(**values)  # type: ignore[arg-type]


def test_liquid_start_requires_six_consecutive_months() -> None:
    monthly = [
        _month(2012, 12, 90),
        _month(2013, 1, 40),
        _month(2013, 2, 20),
        _month(2013, 3, 10),
        _month(2013, 4, 8),
        _month(2013, 5, 6),
        _month(2013, 6, 5),
        _month(2013, 7, 5),
    ]
    start = infer_liquid_start(monthly)
    assert start == month_start_unix("2013-02")


def test_liquid_start_resets_when_streak_breaks() -> None:
    monthly = [
        _month(2013, 1, 10),
        _month(2013, 2, 10),
        _month(2013, 3, 10),
        _month(2013, 4, 40),
        _month(2013, 5, 10),
        _month(2013, 6, 10),
        _month(2013, 7, 10),
        _month(2013, 8, 10),
        _month(2013, 9, 10),
        _month(2013, 10, 10),
    ]
    start = infer_liquid_start(monthly)
    assert start == month_start_unix("2013-05")


def test_forced_liquid_start_skips_inference() -> None:
    params = DetectorParams(forced_liquid_start=0)
    assert infer_liquid_start([_month(2012, 1, 900)], params) == 0


def test_classify_thin_versus_liquid_floor() -> None:
    liquid_start = int(datetime(2014, 1, 1, tzinfo=UTC).timestamp())
    thin_run = ZeroRun(liquid_start - 3600, liquid_start + 3600)
    short_run = ZeroRun(liquid_start, liquid_start + 30 * 60)
    long_run = ZeroRun(liquid_start, liquid_start + 60 * 60)
    assert classify_run(thin_run, liquid_start=liquid_start) == STATUS_THIN_UNPUBLISHED
    assert classify_run(short_run, liquid_start=liquid_start) != STATUS_PENDING_REVIEW
    assert classify_run(long_run, liquid_start=liquid_start) == STATUS_PENDING_REVIEW


def test_published_intervals_require_review() -> None:
    pending = [_candidate()]
    assert published_intervals_from_ledger(pending) == []

    reviewed = _candidate(
        status=STATUS_REVIEWED_UNCONFIRMED,
        decision_date="2026-08-29",
        reviewer="test",
        notes_path="NOTES.md",
    )
    published = published_intervals_from_ledger([reviewed])
    assert len(published) == 1
    assert published[0].flag == FLAG_SUSPECTED_OUTAGE
    assert published[0].reference == HEURISTIC_REFERENCE


def test_reference_classifiers() -> None:
    assert is_heuristic_reference("zero_volume>=60m")
    assert is_url_reference("https://blog.bitstamp.net/post/a/")
    assert is_url_reference("https://example.com/a;https://example.com/b")
    assert not is_url_reference("zero_volume>=60m")


def test_prefix_hash_ignores_appended_later_rows(tmp_path: Path) -> None:
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    start = 1736208000
    write_ohlcv(
        first,
        [
            [str(start), "1", "1", "1", "1", "1"],
            [str(start + 60), "1", "1", "1", "1", "0"],
        ],
    )
    write_ohlcv(
        second,
        [
            [str(start), "1", "1", "1", "1", "1"],
            [str(start + 60), "1", "1", "1", "1", "0"],
            [str(start + 120), "1", "1", "1", "1", "1"],
        ],
    )
    as_of = start + 60
    left, rows_left, _, last_left = hash_ohlcv_prefix([first], as_of)
    right, rows_right, _, last_right = hash_ohlcv_prefix([second], as_of)
    assert left == right
    assert rows_left == rows_right == 2
    assert last_left == last_right == as_of

    rewritten = tmp_path / "c.csv"
    write_ohlcv(
        rewritten,
        [
            [str(start), "1", "1", "1", "9", "1"],
            [str(start + 60), "1", "1", "1", "1", "0"],
            [str(start + 120), "1", "1", "1", "1", "1"],
        ],
    )
    changed, _, _, _ = hash_ohlcv_prefix([rewritten], as_of)
    assert changed != left


def test_scan_as_of_freezes_trailing_candles(tmp_path: Path) -> None:
    path = tmp_path / "ohlcv.csv"
    start = 1736208000
    write_ohlcv(
        path,
        [[str(start + offset * 60), "1", "1", "1", "1", "1"] for offset in range(5)],
    )
    frozen = scan_ohlcv([path], params=DetectorParams(as_of_timestamp=start + 120))
    full = scan_ohlcv([path])
    assert frozen.row_count == 3
    assert frozen.last_timestamp == start + 120
    assert full.row_count == 5
    assert frozen.prefix_hash_sha256 != full.prefix_hash_sha256


def test_ledger_validator_requires_sidecar_row(tmp_path: Path) -> None:
    ledger = tmp_path / "candidates.csv"
    sidecar = tmp_path / "sidecar.csv"
    (tmp_path / "NOTES.md").write_text("evidence", encoding="utf-8")
    write_candidates(
        ledger,
        [
            _candidate(
                status=STATUS_CORROBORATED,
                decision_date="2026-08-29",
                reviewer="test",
                reference="https://blog.bitstamp.net/post/a/",
                notes_path="NOTES.md",
            )
        ],
    )
    write_sidecar(sidecar, [])
    valid, issues = validate_ledger(
        ledger_path=ledger,
        sidecar_path=sidecar,
        research_dir=tmp_path,
        ohlcv_paths=[],
    )
    assert not valid
    assert any("missing from sidecar" in issue.message for issue in issues)


def test_ledger_validator_rejects_unreviewed_sidecar_row(tmp_path: Path) -> None:
    ledger = tmp_path / "candidates.csv"
    sidecar = tmp_path / "sidecar.csv"
    write_candidates(ledger, [_candidate()])
    write_sidecar(
        sidecar,
        [
            sidecar_row(
                1736208000,
                1736211600,
                FLAG_SUSPECTED_OUTAGE,
                HEURISTIC_REFERENCE,
                "0.00",
            )
        ],
    )
    valid, issues = validate_ledger(
        ledger_path=ledger,
        sidecar_path=sidecar,
        research_dir=tmp_path,
        ohlcv_paths=[],
    )
    assert not valid
    assert any("no reviewed ledger row" in issue.message for issue in issues)


def test_ledger_validator_accepts_reviewed_unconfirmed(tmp_path: Path) -> None:
    ledger = tmp_path / "candidates.csv"
    sidecar = tmp_path / "sidecar.csv"
    (tmp_path / "NOTES.md").write_text("reviewed", encoding="utf-8")
    write_candidates(
        ledger,
        [
            _candidate(
                status=STATUS_REVIEWED_UNCONFIRMED,
                decision_date="2026-08-29",
                reviewer="test",
                notes_path="NOTES.md",
            )
        ],
    )
    write_sidecar(
        sidecar,
        [
            sidecar_row(
                1736208000,
                1736211600,
                FLAG_SUSPECTED_OUTAGE,
                HEURISTIC_REFERENCE,
                "0.00",
            )
        ],
    )
    valid, issues = validate_ledger(
        ledger_path=ledger,
        sidecar_path=sidecar,
        research_dir=tmp_path,
        ohlcv_paths=[],
    )
    assert valid, issues


def test_ledger_validator_detects_stale_nonzero_bounds(tmp_path: Path) -> None:
    ohlcv = tmp_path / "ohlcv.csv"
    ledger = tmp_path / "candidates.csv"
    sidecar = tmp_path / "sidecar.csv"
    (tmp_path / "NOTES.md").write_text("stale", encoding="utf-8")
    start = 1736208060
    write_ohlcv(ohlcv, zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES))
    stale_start = start + 60 + 4 * 3600
    stale_end = stale_start + SIXTY_MINUTES * 60
    write_candidates(
        ledger,
        [
            _candidate(
                candidate_id=f"zv-{stale_start}-{stale_end}",
                start_timestamp=stale_start,
                end_timestamp=stale_end,
                status=STATUS_REVIEWED_UNCONFIRMED,
                decision_date="2026-08-29",
                reviewer="test",
                notes_path="NOTES.md",
            )
        ],
    )
    write_sidecar(
        sidecar,
        [
            sidecar_row(
                stale_start,
                stale_end,
                FLAG_SUSPECTED_OUTAGE,
                HEURISTIC_REFERENCE,
                "0.00",
            )
        ],
    )
    valid, issues = validate_ledger(
        ledger_path=ledger,
        sidecar_path=sidecar,
        research_dir=tmp_path,
        ohlcv_paths=[ohlcv],
    )
    assert not valid
    assert any("not an exact zero-volume run" in issue.message for issue in issues)


def _write_empty_ledger_sidecar(tmp_path: Path) -> tuple[Path, Path]:
    ledger = tmp_path / "candidates.csv"
    sidecar = tmp_path / "sidecar.csv"
    write_candidates(ledger, [])
    write_sidecar(sidecar, [])
    return ledger, sidecar


def test_ledger_validator_detects_prefix_hash_mismatch(tmp_path: Path) -> None:
    ohlcv = tmp_path / "ohlcv.csv"
    start = 1736208000
    write_ohlcv(
        ohlcv,
        [
            [str(start), "1", "1", "1", "1", "1"],
            [str(start + 60), "1", "1", "1", "1", "1"],
        ],
    )
    scan = scan_ohlcv([ohlcv], params=DetectorParams(as_of_timestamp=start + 60))
    write_model(
        tmp_path / "model.json",
        scan=scan,
        params=DetectorParams(as_of_timestamp=start + 60),
        ohlcv_paths=[ohlcv],
        sensitivity={"thin": {}, "liquid": {}},
    )
    payload = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    payload["prefix_hash_sha256"] = "0" * 64
    (tmp_path / "model.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    ledger, sidecar = _write_empty_ledger_sidecar(tmp_path)
    valid, issues = validate_ledger(
        ledger_path=ledger,
        sidecar_path=sidecar,
        research_dir=tmp_path,
        ohlcv_paths=[ohlcv],
    )
    assert not valid
    assert any("prefix hash mismatch" in issue.message for issue in issues)


def test_ledger_validator_ignores_appended_updates_after_cutoff(tmp_path: Path) -> None:
    ohlcv = tmp_path / "ohlcv.csv"
    start = 1736208000
    prefix = [
        [str(start), "1", "1", "1", "1", "1"],
        [str(start + 60), "1", "1", "1", "1", "1"],
    ]
    write_ohlcv(ohlcv, prefix)
    as_of = start + 60
    scan = scan_ohlcv([ohlcv], params=DetectorParams(as_of_timestamp=as_of))
    write_model(
        tmp_path / "model.json",
        scan=scan,
        params=DetectorParams(as_of_timestamp=as_of),
        ohlcv_paths=[ohlcv],
        sensitivity={"thin": {}, "liquid": {}},
    )
    later_zero = zero_run_with_bounds(start + 120, zero_minutes=SIXTY_MINUTES)
    write_ohlcv(ohlcv, prefix + later_zero)
    ledger, sidecar = _write_empty_ledger_sidecar(tmp_path)
    valid, issues = validate_ledger(
        ledger_path=ledger,
        sidecar_path=sidecar,
        research_dir=tmp_path,
        ohlcv_paths=[ohlcv],
    )
    assert valid, issues


def test_ledger_validator_detects_stale_official_price_jump(tmp_path: Path) -> None:
    ohlcv = tmp_path / "ohlcv.csv"
    ledger = tmp_path / "candidates.csv"
    sidecar = tmp_path / "sidecar.csv"
    start = 1736208060
    write_ohlcv(
        ohlcv,
        zero_run_with_bounds(
            start,
            zero_minutes=10,
            close_after="102",
        ),
    )
    write_candidates(ledger, [])
    write_sidecar(
        sidecar,
        [
            sidecar_row(
                start + 60,
                start + 60 + 10 * 60,
                FLAG_CONFIRMED_OUTAGE,
                "https://stspg.io/stale",
                "0.00",
            )
        ],
    )
    valid, issues = validate_ledger(
        ledger_path=ledger,
        sidecar_path=sidecar,
        research_dir=tmp_path,
        ohlcv_paths=[ohlcv],
    )
    assert not valid
    assert any("price_jump mismatch" in issue.message for issue in issues)
