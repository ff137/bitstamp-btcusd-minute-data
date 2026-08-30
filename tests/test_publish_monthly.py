"""Tests for scripts/publish_monthly.py."""

from __future__ import annotations

import csv
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.provenance import (
    FLAG_CONFIRMED_OUTAGE,
    FLAG_SOURCE_GAP_FILLED,
    Interval,
    write_sidecar,
)
from scripts.provenance import (
    HEADER as SIDECAR_HEADER,
)
from scripts.publish_monthly import (
    FIRST_RELEASE_TAG,
    MANIFEST_ASSET,
    MonthWindow,
    PublishError,
    build_snapshot,
    canonical_manifest_json,
    clip_sidecar,
    format_utc_range,
    is_utc_first_of_month,
    load_intro,
    month_window,
    parse_year_month,
    previous_utc_month,
    render_notes,
    run_publish,
    sha256_file,
)
from scripts.validate_dataset import COLUMN_NAMES

OHLCV_HEADER = list(COLUMN_NAMES)


def write_ohlcv(path: Path, rows: list[list[str]], *, gzip_file: bool = False) -> None:
    if gzip_file:
        with gzip.open(path, mode="wt", encoding="utf-8", newline="") as handle:
            _write_ohlcv_rows(handle, rows)
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        _write_ohlcv_rows(handle, rows)


def _write_ohlcv_rows(handle, rows: list[list[str]]) -> None:
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(OHLCV_HEADER)
    for row in rows:
        writer.writerow(row)


def candle(timestamp: int, close: str, volume: str = "1") -> list[str]:
    return [str(timestamp), close, close, close, close, volume]


def six_minute_month(
    start: int,
) -> tuple[MonthWindow, list[list[str]], list[list[str]]]:
    month = MonthWindow(2020, 1, start, start + 6 * 60)
    historical = [candle(start + offset * 60, "100") for offset in range(3)]
    updates = [
        candle(start + (3 + offset) * 60, "110", "0" if offset == 1 else "2")
        for offset in range(3)
    ]
    return month, historical, updates


def write_pair(
    tmp_path: Path,
    historical_rows: list[list[str]],
    updates_rows: list[list[str]],
) -> tuple[Path, Path]:
    historical = tmp_path / "historical.csv.gz"
    updates = tmp_path / "updates.csv"
    write_ohlcv(historical, historical_rows, gzip_file=True)
    write_ohlcv(updates, updates_rows)
    return historical, updates


def test_month_window_cutoff() -> None:
    august = month_window(2026, 8)
    assert august.tag == "bitstamp-btcusd-1m-2026-08"
    assert august.start_timestamp == int(datetime(2026, 8, 1, tzinfo=UTC).timestamp())
    assert august.end_timestamp == int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
    assert august.last_minute == august.end_timestamp - 60
    assert august.expected_candles == 31 * 1440


def test_previous_month_and_first_day() -> None:
    now = datetime(2026, 9, 1, 0, 5, tzinfo=UTC)
    assert is_utc_first_of_month(now)
    assert previous_utc_month(now).tag == "bitstamp-btcusd-1m-2026-08"
    assert not is_utc_first_of_month(datetime(2026, 9, 2, tzinfo=UTC))


def test_parse_year_month_rejects_garbage() -> None:
    with pytest.raises(PublishError):
        parse_year_month("2026/08")


def test_join_cutoff_and_parquet(tmp_path: Path) -> None:
    start = 1_577_836_800
    month, historical_rows, updates_rows = six_minute_month(start)
    historical, updates = write_pair(tmp_path, historical_rows, updates_rows)
    sidecar = tmp_path / "sidecar.csv"
    write_sidecar(
        sidecar,
        [
            Interval(
                start + 60,
                start + 180,
                FLAG_CONFIRMED_OUTAGE,
                "https://stspg.io/a",
                "0.10",
            ),
            Interval(
                start + 1000,
                start + 1120,
                FLAG_SOURCE_GAP_FILLED,
                "updater:fill_missing_minutes",
                "0",
            ),
        ],
    )
    intro = tmp_path / "intro.md"
    intro.write_text("INTRO_SHOULD_NOT_APPEAR\n", encoding="utf-8")

    result = build_snapshot(
        month=month,
        historical_path=historical,
        updates_path=updates,
        sidecar_path=sidecar,
        intro_path=intro,
        output_dir=tmp_path / "out",
        as_of="2026-09-01T00:10:00Z",
        generation_revision="deadbeef",
    )

    assert result.row_count == 6
    assert result.first_timestamp == start
    assert result.last_timestamp == start + 5 * 60
    table = pq.read_table(tmp_path / "out" / "btcusd_bitstamp_1min.parquet")
    assert table.column("timestamp").to_pylist() == [start + i * 60 for i in range(6)]
    assert table.schema.field("timestamp").type == pa.int64()
    with gzip.open(
        tmp_path / "out" / "btcusd_bitstamp_1min.csv.gz", "rt", encoding="utf-8"
    ) as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == OHLCV_HEADER
    assert rows[-1][0] == str(start + 5 * 60)

    provenance_rows = list(
        csv.reader(
            (tmp_path / "out" / "btcusd_bitstamp_1min_provenance.csv").open(
                encoding="utf-8"
            )
        )
    )
    assert provenance_rows[0] == list(SIDECAR_HEADER)
    assert len(provenance_rows) == 2
    assert provenance_rows[1][3] == FLAG_CONFIRMED_OUTAGE
    assert "INTRO_SHOULD_NOT_APPEAR" not in result.notes
    assert "No zero-volume minutes" not in result.notes
    assert format_utc_range(start + 4 * 60, start + 5 * 60) in result.notes


def test_short_tail_fails(tmp_path: Path) -> None:
    start = 1_577_836_800
    month, historical_rows, updates_rows = six_minute_month(start)
    historical, updates = write_pair(tmp_path, historical_rows, updates_rows[:-1])
    sidecar = tmp_path / "sidecar.csv"
    write_sidecar(sidecar, [])
    intro = tmp_path / "intro.md"
    intro.write_text("x\n", encoding="utf-8")

    with pytest.raises(PublishError, match="before month cutoff"):
        build_snapshot(
            month=month,
            historical_path=historical,
            updates_path=updates,
            sidecar_path=sidecar,
            intro_path=intro,
            output_dir=tmp_path / "out",
            as_of="2026-09-01T00:10:00Z",
            generation_revision="deadbeef",
        )


def test_seam_failure(tmp_path: Path) -> None:
    start = 1_577_836_800
    month, historical_rows, updates_rows = six_minute_month(start)
    updates_rows[0][0] = str(start + 5 * 60)
    historical, updates = write_pair(tmp_path, historical_rows, updates_rows)
    sidecar = tmp_path / "sidecar.csv"
    write_sidecar(sidecar, [])
    intro = tmp_path / "intro.md"
    intro.write_text("x\n", encoding="utf-8")

    with pytest.raises(PublishError, match="OHLCV validation failed"):
        build_snapshot(
            month=month,
            historical_path=historical,
            updates_path=updates,
            sidecar_path=sidecar,
            intro_path=intro,
            output_dir=tmp_path / "out",
            as_of="2026-09-01T00:10:00Z",
            generation_revision="deadbeef",
        )


def test_notes_empty_zeros_and_sidecar_groups() -> None:
    start = 1_577_836_800
    month = MonthWindow(2020, 1, start, start + 2 * 60)
    from scripts.publish_monthly import MonthStats

    stats = MonthStats()
    stats.add_row(start, "100", "101", "99", "100.5", "3")
    stats.add_row(start + 60, "100.5", "102", "100", "101", "4")
    stats.finish()
    notes = render_notes(
        month=month,
        stats=stats,
        sidecar=[
            Interval(
                start, start + 60, FLAG_CONFIRMED_OUTAGE, "https://stspg.io/a", "1.25"
            ),
        ],
        intro_text=None,
    )
    assert "No zero-volume minutes in this month." in notes
    assert "Confirmed outages" in notes
    assert "https://stspg.io/a" in notes
    assert "None this month." in notes
    assert "Change: +1.00%" in notes


def test_intro_only_for_first_tag(tmp_path: Path) -> None:
    intro = tmp_path / "intro.md"
    intro.write_text(
        "## Publisher changes (first snapshot)\n\nHello.\n", encoding="utf-8"
    )
    assert load_intro(intro, FIRST_RELEASE_TAG) is not None
    assert load_intro(intro, "bitstamp-btcusd-1m-2026-09") is None


def test_if_due_skips_when_not_first_day(tmp_path: Path) -> None:
    start = 1_577_836_800
    _month, historical_rows, updates_rows = six_minute_month(start)
    historical, updates = write_pair(tmp_path, historical_rows, updates_rows)
    sidecar = tmp_path / "sidecar.csv"
    write_sidecar(sidecar, [])
    intro = tmp_path / "intro.md"
    intro.write_text("hello\n", encoding="utf-8")

    result = run_publish(
        year_month=None,
        if_due=True,
        publish=False,
        now=datetime(2026, 9, 2, tzinfo=UTC),
        historical_path=historical,
        updates_path=updates,
        sidecar_path=sidecar,
        intro_path=intro,
        output_dir=tmp_path / "out",
        generation_revision="deadbeef",
    )
    assert result.skipped
    assert result.skip_reason is not None
    assert "first UTC day" in result.skip_reason


def test_manifest_identity_is_hash_of_file(tmp_path: Path) -> None:
    start = 1_577_836_800
    month, historical_rows, updates_rows = six_minute_month(start)
    historical, updates = write_pair(tmp_path, historical_rows, updates_rows)
    sidecar = tmp_path / "sidecar.csv"
    write_sidecar(sidecar, [])
    intro = tmp_path / "intro.md"
    intro.write_text("hello\n", encoding="utf-8")

    result = build_snapshot(
        month=month,
        historical_path=historical,
        updates_path=updates,
        sidecar_path=sidecar,
        intro_path=intro,
        output_dir=tmp_path / "out",
        as_of="2026-09-01T00:10:00Z",
        generation_revision="deadbeef",
    )
    manifest_path = tmp_path / "out" / MANIFEST_ASSET
    written = manifest_path.read_text(encoding="utf-8")
    assert written == canonical_manifest_json(result.manifest)
    assert "identity" not in result.manifest
    payload = json.loads(written)
    assert payload["schema_version"] == 1
    assert payload["tag"] == month.tag
    assert payload["generation_revision"] == "deadbeef"
    assert "sha256" in payload["assets"]["btcusd_bitstamp_1min.csv.gz"]
    assert sha256_file(manifest_path) == sha256_file(manifest_path)


def test_clip_sidecar_clips_end_to_window() -> None:
    start = 100
    intervals = [
        Interval(60, 240, FLAG_CONFIRMED_OUTAGE, "https://stspg.io/a", "0"),
    ]
    clipped = clip_sidecar(intervals, start, 180)
    assert clipped == [
        Interval(100, 180, FLAG_CONFIRMED_OUTAGE, "https://stspg.io/a", "0"),
    ]


def test_first_release_intro_prepended(tmp_path: Path) -> None:
    start = 1_577_836_800
    month = MonthWindow(2026, 8, start, start + 6 * 60)
    _template, historical_rows, updates_rows = six_minute_month(start)
    historical, updates = write_pair(tmp_path, historical_rows, updates_rows)
    sidecar = tmp_path / "sidecar.csv"
    write_sidecar(sidecar, [])
    intro = tmp_path / "intro.md"
    intro.write_text(
        "## Publisher changes (first snapshot)\n\nOnce-off intro.\n", encoding="utf-8"
    )

    result = build_snapshot(
        month=month,
        historical_path=historical,
        updates_path=updates,
        sidecar_path=sidecar,
        intro_path=intro,
        output_dir=tmp_path / "out",
        as_of="2026-09-01T00:10:00Z",
        generation_revision="deadbeef",
    )
    assert result.notes.startswith("## Publisher changes (first snapshot)")
    assert "Once-off intro." in result.notes
    assert result.tag == FIRST_RELEASE_TAG
