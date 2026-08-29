"""Tests for the sparse provenance sidecar."""

import csv
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from scripts.outage_candidates import write_candidates
from scripts.provenance import (
    FLAG_CONFIRMED_OUTAGE,
    FLAG_SCHEDULED_MAINTENANCE,
    FLAG_SOURCE_GAP_FILLED,
    FLAG_SUSPECTED_OUTAGE,
    HEADER,
    Interval,
    detect_suspected_outages,
    merge_adjacent,
    parse_incidents,
    parse_scheduled_maintenances,
    record_fill_timestamps,
    refresh_sidecar,
    timestamps_to_intervals,
)
from scripts.update_data import fill_missing_minutes
from scripts.validate_provenance import validate_sidecar

OHLCV_HEADER = ["timestamp", "open", "high", "low", "close", "volume"]
SIXTY_MINUTES = 60
LIQUID = 0  # forced_liquid_start: treat the whole fixture as liquid
REAL_SIDECAR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "provenance"
    / "btcusd_bitstamp_1min.csv"
)


def write_ohlcv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(OHLCV_HEADER)
        for row in rows:
            writer.writerow(row)


def write_sidecar(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow(row)


def ohlcv_row(timestamp: int, close: str, volume: str) -> list[str]:
    return [str(timestamp), close, close, close, close, volume]


def contiguous_ohlcv(
    start: int,
    *,
    minutes: int,
    close: str,
    volume: str,
) -> list[list[str]]:
    return [ohlcv_row(start + offset * 60, close, volume) for offset in range(minutes)]


def sidecar_row(
    start: int,
    end: int,
    flag: str,
    reference: str,
    price_jump: str = "",
) -> list[str]:
    duration = str((end - start) // 60)
    return [str(start), str(end), duration, flag, price_jump, reference]


def parse_sidecar_interval(row: list[str]) -> Interval:
    return Interval(int(row[0]), int(row[1]), row[3], row[5], row[4])


def zero_run_with_bounds(
    start: int,
    *,
    zero_minutes: int,
    close_before: str = "100",
    close_after: str = "100",
) -> list[list[str]]:
    rows = contiguous_ohlcv(start, minutes=1, close=close_before, volume="1")
    rows.extend(
        contiguous_ohlcv(
            start + 60, minutes=zero_minutes, close=close_before, volume="0"
        )
    )
    rows.extend(
        contiguous_ohlcv(
            start + 60 + zero_minutes * 60,
            minutes=1,
            close=close_after,
            volume="1",
        )
    )
    return rows


def test_sidecar_schema_header_required(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    write_sidecar = path.write_text
    write_sidecar("start,end,flag,reference\n", encoding="utf-8")

    result = validate_sidecar(path)

    assert not result.valid
    assert any("invalid header" in issue.message for issue in result.issues)


def test_header_only_sidecar_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    write_sidecar(path, [])

    result = validate_sidecar(path)

    assert result.valid
    assert result.summary.row_count == 0


def test_allowed_flags_and_minute_alignment(tmp_path: Path) -> None:
    path = tmp_path / "ok.csv"
    write_sidecar(
        path,
        [
            sidecar_row(
                1736208000,
                1736208120,
                FLAG_SOURCE_GAP_FILLED,
                "updater:fill_missing_minutes",
                "0.00",
            ),
        ],
    )

    result = validate_sidecar(path)

    assert result.valid
    assert result.summary.row_count == 1


def test_reject_unknown_flag_and_bad_bounds(tmp_path: Path) -> None:
    path = tmp_path / "bad_flag.csv"
    write_sidecar(
        path,
        [
            sidecar_row(1736208000, 1736208000, "volume_zero", "nope"),
        ],
    )

    result = validate_sidecar(path)

    assert not result.valid
    assert any("unsupported flag" in issue.message for issue in result.issues)
    assert any("must be <" in issue.message for issue in result.issues)


def test_reject_unsorted_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "unsorted.csv"
    write_sidecar(
        path,
        [
            sidecar_row(
                1736208120, 1736208180, FLAG_CONFIRMED_OUTAGE, "https://stspg.io/b"
            ),
            sidecar_row(
                1736208000, 1736208060, FLAG_CONFIRMED_OUTAGE, "https://stspg.io/a"
            ),
        ],
    )

    result = validate_sidecar(path)

    assert not result.valid
    assert any("out-of-order" in issue.message for issue in result.issues)


def test_fill_missing_minutes_returns_synthesized_timestamps() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [1736208060, 1736208180],
            "open": [100.0, 101.0],
            "high": [100.0, 101.0],
            "low": [100.0, 101.0],
            "close": [100.0, 101.0],
            "volume": [1.0, 2.0],
        }
    )

    filled, timestamps = fill_missing_minutes(df)

    assert timestamps == [1736208120]
    assert len(filled) == 3
    missing_row = filled.loc[filled["timestamp"] == 1736208120].iloc[0]
    assert missing_row["close"] == 100.0
    assert missing_row["volume"] == 0


def test_fill_recording_merges_adjacent_minutes(tmp_path: Path) -> None:
    sidecar = tmp_path / "sidecar.csv"
    record_fill_timestamps([1736208060, 1736208120, 1736208240], sidecar)
    record_fill_timestamps([1736208120, 1736208180], sidecar)

    intervals = [
        parse_sidecar_interval(row)
        for row in csv.reader(sidecar.open(encoding="utf-8"))
        if row[0] != "start_timestamp"
    ]

    assert intervals == [
        Interval(
            1736208060,
            1736208300,
            FLAG_SOURCE_GAP_FILLED,
            "updater:fill_missing_minutes",
        ),
    ]


def test_timestamps_to_intervals_merges_adjacent() -> None:
    intervals = timestamps_to_intervals(
        [100, 160, 280],
        flag=FLAG_SOURCE_GAP_FILLED,
        reference="updater:fill_missing_minutes",
    )
    # 100 and 160 are not minute-aligned in this synthetic list; the helper
    # still groups exact +60 steps. 100->160 is +60, then 280 is a gap.
    assert [(item.start_timestamp, item.end_timestamp) for item in intervals] == [
        (100, 220),
        (280, 340),
    ]


def test_status_parse_keeps_trading_and_ignores_altcoin_only() -> None:
    payload = {
        "incidents": [
            {
                "name": "Trading halted",
                "started_at": "2026-08-05T09:14:40Z",
                "resolved_at": "2026-08-05T11:00:00Z",
                "shortlink": "https://stspg.io/trading",
                "components": [{"name": "Trading"}],
            },
            {
                "name": "ADA deposits delayed",
                "started_at": "2026-08-05T09:00:00Z",
                "resolved_at": "2026-08-05T18:00:00Z",
                "shortlink": "https://stspg.io/ada",
                "components": [{"name": "ADA"}],
            },
        ]
    }

    intervals = parse_incidents(payload, now_unix=1780000000)

    assert [item.reference for item in intervals] == ["https://stspg.io/trading"]
    assert intervals[0].flag == FLAG_CONFIRMED_OUTAGE
    assert intervals[0].start_timestamp == 1785921240
    assert intervals[0].end_timestamp == 1785927600


def test_status_parse_scheduled_maintenance_uses_window() -> None:
    payload = {
        "scheduled_maintenances": [
            {
                "name": "API maintenance",
                "scheduled_for": "2026-08-31T08:15:00Z",
                "scheduled_until": "2026-08-31T09:15:00Z",
                "shortlink": "https://stspg.io/maint",
                "components": [{"name": "REST"}, {"name": "Web"}],
            },
            {
                "name": "ADA wallet work",
                "scheduled_for": "2026-08-31T08:15:00Z",
                "scheduled_until": "2026-08-31T09:15:00Z",
                "shortlink": "https://stspg.io/ada-maint",
                "components": [{"name": "ADA"}],
            },
        ]
    }

    intervals = parse_scheduled_maintenances(payload, now_unix=1780000000)

    assert len(intervals) == 1
    assert intervals[0].flag == FLAG_SCHEDULED_MAINTENANCE
    assert intervals[0].start_timestamp == 1788164100
    assert intervals[0].end_timestamp == 1788167700


def test_sixty_minute_zero_run_is_suspected(tmp_path: Path) -> None:
    start = 1736208060
    path = tmp_path / "ohlcv.csv"
    write_ohlcv(path, zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES))

    intervals, _ = detect_suspected_outages([path], forced_liquid_start=LIQUID)

    assert len(intervals) == 1
    assert intervals[0].flag == FLAG_SUSPECTED_OUTAGE
    assert intervals[0].start_timestamp == start + 60
    assert intervals[0].end_timestamp == start + 60 + SIXTY_MINUTES * 60
    assert intervals[0].reference == "zero_volume>=60m"
    assert intervals[0].price_jump == "0.00"
    assert intervals[0].duration_minutes() == "60"


def test_shorter_than_sixty_minute_zero_run_is_ignored(tmp_path: Path) -> None:
    start = 1736208060
    path = tmp_path / "ohlcv.csv"
    write_ohlcv(path, zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES - 1))

    intervals, _ = detect_suspected_outages([path], forced_liquid_start=LIQUID)

    assert intervals == []


def test_four_hour_zero_run_is_published(tmp_path: Path) -> None:
    start = 1736208060
    path = tmp_path / "ohlcv.csv"
    write_ohlcv(path, zero_run_with_bounds(start, zero_minutes=4 * 60))

    intervals, _ = detect_suspected_outages([path], forced_liquid_start=LIQUID)

    assert len(intervals) == 1
    assert intervals[0].duration_minutes() == "240"


def test_2012_sixty_minute_zero_run_is_not_published(tmp_path: Path) -> None:
    start = 1325376060  # 2012-01-01 00:01 UTC, historical first timestamp
    path = tmp_path / "ohlcv.csv"
    write_ohlcv(path, zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES))

    intervals, _ = detect_suspected_outages([path])

    assert intervals == []


def test_suspected_excludes_overlap_leaving_short_remainder(tmp_path: Path) -> None:
    start = 1736208060
    path = tmp_path / "ohlcv.csv"
    write_ohlcv(path, zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES))
    exclusion = Interval(
        start + 60,
        start + 60 + 30 * 60,
        FLAG_CONFIRMED_OUTAGE,
        "https://stspg.io/overlap",
    )

    intervals, _ = detect_suspected_outages(
        [path], [exclusion], forced_liquid_start=LIQUID
    )

    assert intervals == []


def test_suspected_keeps_non_overlapping_remainder(tmp_path: Path) -> None:
    start = 1736208060
    path = tmp_path / "ohlcv.csv"
    write_ohlcv(path, zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES + 30))
    exclusion = Interval(
        start + 60,
        start + 60 + 30 * 60,
        FLAG_CONFIRMED_OUTAGE,
        "https://stspg.io/overlap",
    )

    intervals, _ = detect_suspected_outages(
        [path], [exclusion], forced_liquid_start=LIQUID
    )

    assert len(intervals) == 1
    assert intervals[0].start_timestamp == start + 60 + 30 * 60
    assert intervals[0].end_timestamp == start + 60 + (SIXTY_MINUTES + 30) * 60


def test_price_jump_is_a_column_not_reference(tmp_path: Path) -> None:
    start = 1736208060
    path = tmp_path / "ohlcv.csv"
    write_ohlcv(
        path,
        zero_run_with_bounds(
            start,
            zero_minutes=SIXTY_MINUTES,
            close_after="102",
        ),
    )

    intervals, _ = detect_suspected_outages([path], forced_liquid_start=LIQUID)

    assert intervals[0].reference == "zero_volume>=60m"
    assert intervals[0].price_jump == "2.00"


def test_refresh_is_idempotent(tmp_path: Path) -> None:
    start = 1736208060
    rows = zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES)
    ohlcv = tmp_path / "ohlcv.csv"
    sidecar = tmp_path / "sidecar.csv"
    write_ohlcv(ohlcv, rows)
    status = [
        Interval(
            start + 60 + SIXTY_MINUTES * 60,
            start + 60 + (SIXTY_MINUTES + 1) * 60,
            FLAG_CONFIRMED_OUTAGE,
            "https://stspg.io/once",
        )
    ]

    ledger = tmp_path / "candidates.csv"
    write_candidates(ledger, [])
    first = refresh_sidecar(
        sidecar_path=sidecar,
        ohlcv_paths=[ohlcv],
        status_intervals=status,
        fetch_status=False,
        ledger_path=ledger,
        forced_liquid_start=LIQUID,
    )
    second = refresh_sidecar(
        sidecar_path=sidecar,
        ohlcv_paths=[ohlcv],
        status_intervals=status,
        fetch_status=False,
        ledger_path=ledger,
        forced_liquid_start=LIQUID,
    )

    assert first == second
    suspected = [item for item in first if item.flag == FLAG_SUSPECTED_OUTAGE]
    confirmed = [item for item in first if item.flag == FLAG_CONFIRMED_OUTAGE]
    assert suspected == []
    assert confirmed[0].start_timestamp == status[0].start_timestamp
    assert confirmed[0].end_timestamp == status[0].end_timestamp
    assert confirmed[0].reference == status[0].reference


def test_merge_adjacent_same_flag_and_reference() -> None:
    merged = merge_adjacent(
        [
            Interval(100, 160, FLAG_SOURCE_GAP_FILLED, "updater:fill_missing_minutes"),
            Interval(160, 220, FLAG_SOURCE_GAP_FILLED, "updater:fill_missing_minutes"),
            Interval(300, 360, FLAG_CONFIRMED_OUTAGE, "https://stspg.io/a"),
        ]
    )
    assert merged[0] == Interval(
        100,
        220,
        FLAG_SOURCE_GAP_FILLED,
        "updater:fill_missing_minutes",
    )
    assert merged[1].reference == "https://stspg.io/a"


def test_cli_validate_success(tmp_path: Path) -> None:
    path = tmp_path / "sidecar.csv"
    write_sidecar(
        path,
        [
            sidecar_row(
                1736208000,
                1736208120,
                FLAG_SOURCE_GAP_FILLED,
                "updater:fill_missing_minutes",
                "0.00",
            ),
        ],
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_provenance.py",
            "--sidecar",
            str(path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "1 intervals" in completed.stdout


def test_cli_refresh_from_local_status(tmp_path: Path) -> None:
    ohlcv = tmp_path / "ohlcv.csv"
    sidecar = tmp_path / "sidecar.csv"
    incidents = tmp_path / "incidents.json"
    maintenances = tmp_path / "maintenances.json"
    write_ohlcv(ohlcv, contiguous_ohlcv(1736208060, minutes=3, close="100", volume="1"))
    ledger = tmp_path / "candidates.csv"
    write_candidates(ledger, [])
    incidents.write_text(
        json.dumps(
            {
                "incidents": [
                    {
                        "started_at": "2026-08-05T09:14:00Z",
                        "resolved_at": "2026-08-05T10:14:00Z",
                        "shortlink": "https://stspg.io/cli",
                        "components": [{"name": "Trading"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    maintenances.write_text(
        json.dumps({"scheduled_maintenances": []}),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/provenance.py",
            "--sidecar",
            str(sidecar),
            "--historical",
            str(ohlcv),
            "--updates",
            str(ohlcv),
            "--incidents-json",
            str(incidents),
            "--maintenances-json",
            str(maintenances),
            "--ledger",
            str(ledger),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    result = validate_sidecar(sidecar)
    assert result.valid
    assert result.summary.row_count == 1


@pytest.mark.skipif(not REAL_SIDECAR.exists(), reason="sidecar not generated yet")
def test_committed_sidecar_validates() -> None:
    result = validate_sidecar(REAL_SIDECAR)
    assert result.valid
    assert result.summary.row_count >= 0


def test_refresh_publishes_only_reviewed_ledger_rows(tmp_path: Path) -> None:
    from scripts.outage_candidates import (
        HEURISTIC_REFERENCE,
        STATUS_CORROBORATED,
        STATUS_REVIEWED_UNCONFIRMED,
        Candidate,
        write_candidates,
    )

    start = 1736208060
    first = zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES)
    second_start = start + (SIXTY_MINUTES + 5) * 60
    second = zero_run_with_bounds(second_start, zero_minutes=SIXTY_MINUTES)
    ohlcv_rows = first[:-1] + second
    ohlcv = tmp_path / "ohlcv.csv"
    sidecar = tmp_path / "sidecar.csv"
    ledger = tmp_path / "candidates.csv"
    write_ohlcv(ohlcv, ohlcv_rows)
    zero_start = start + 60
    zero_end = start + 60 + SIXTY_MINUTES * 60
    write_candidates(
        ledger,
        [
            Candidate(
                candidate_id="zv-test",
                start_timestamp=zero_start,
                end_timestamp=zero_end,
                duration_minutes=60,
                regime="liquid",
                detector_version="regime-v1",
                rarity_rank=1,
                status=STATUS_CORROBORATED,
                decision_date="2026-08-29",
                reviewer="test",
                reference="https://blog.bitstamp.net/post/example/",
                notes_path="NOTES.md",
            )
        ],
    )

    intervals = refresh_sidecar(
        sidecar_path=sidecar,
        ohlcv_paths=[ohlcv],
        status_intervals=[],
        fetch_status=False,
        ledger_path=ledger,
    )
    suspected = [item for item in intervals if item.flag == FLAG_SUSPECTED_OUTAGE]
    assert len(suspected) == 1
    assert suspected[0].start_timestamp == zero_start
    assert suspected[0].reference == "https://blog.bitstamp.net/post/example/"

    other_start = second_start + 60
    other_end = other_start + SIXTY_MINUTES * 60
    write_candidates(
        ledger,
        [
            Candidate(
                candidate_id="zv-test",
                start_timestamp=zero_start,
                end_timestamp=zero_end,
                duration_minutes=60,
                regime="liquid",
                detector_version="regime-v1",
                rarity_rank=1,
                status=STATUS_CORROBORATED,
                decision_date="2026-08-29",
                reviewer="test",
                reference="https://blog.bitstamp.net/post/example/",
                notes_path="NOTES.md",
            ),
            Candidate(
                candidate_id="zv-other",
                start_timestamp=other_start,
                end_timestamp=other_end,
                duration_minutes=60,
                regime="liquid",
                detector_version="regime-v1",
                rarity_rank=2,
                status=STATUS_REVIEWED_UNCONFIRMED,
                decision_date="2026-08-29",
                reviewer="test",
                reference=HEURISTIC_REFERENCE,
                notes_path="NOTES.md",
            ),
        ],
    )
    again = refresh_sidecar(
        sidecar_path=sidecar,
        ohlcv_paths=[ohlcv],
        status_intervals=[],
        fetch_status=False,
        ledger_path=ledger,
    )
    suspected_again = [item for item in again if item.flag == FLAG_SUSPECTED_OUTAGE]
    assert [item.start_timestamp for item in suspected_again] == [
        zero_start,
        other_start,
    ]
    assert suspected_again[0].reference.startswith("https://")
    assert suspected_again[1].reference == HEURISTIC_REFERENCE


def test_refresh_does_not_publish_unreviewed_duration_runs(tmp_path: Path) -> None:
    from scripts.outage_candidates import (
        HEURISTIC_REFERENCE,
        STATUS_REVIEWED_UNCONFIRMED,
        Candidate,
        write_candidates,
    )

    start = 1736208060
    reviewed = zero_run_with_bounds(start, zero_minutes=SIXTY_MINUTES)
    extra_start = start + (SIXTY_MINUTES + 5) * 60
    extra = zero_run_with_bounds(extra_start, zero_minutes=SIXTY_MINUTES)
    ohlcv = tmp_path / "ohlcv.csv"
    sidecar = tmp_path / "sidecar.csv"
    ledger = tmp_path / "candidates.csv"
    write_ohlcv(ohlcv, reviewed[:-1] + extra)
    zero_start = start + 60
    zero_end = start + 60 + SIXTY_MINUTES * 60
    write_candidates(
        ledger,
        [
            Candidate(
                candidate_id="zv-reviewed",
                start_timestamp=zero_start,
                end_timestamp=zero_end,
                duration_minutes=60,
                regime="liquid",
                detector_version="regime-v1",
                rarity_rank=1,
                status=STATUS_REVIEWED_UNCONFIRMED,
                decision_date="2026-08-29",
                reviewer="test",
                reference=HEURISTIC_REFERENCE,
                notes_path="NOTES.md",
            )
        ],
    )

    intervals = refresh_sidecar(
        sidecar_path=sidecar,
        ohlcv_paths=[ohlcv],
        status_intervals=[],
        fetch_status=False,
        ledger_path=ledger,
    )
    suspected = [item for item in intervals if item.flag == FLAG_SUSPECTED_OUTAGE]
    assert len(suspected) == 1
    assert suspected[0].start_timestamp == zero_start
    assert extra_start + 60 not in {item.start_timestamp for item in suspected}
