"""Tests for scripts/publish_monthly.py."""

import csv
import gzip
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.provenance import (
    FLAG_CONFIRMED_OUTAGE,
    FLAG_SCHEDULED_MAINTENANCE,
    FLAG_SOURCE_GAP_FILLED,
    FLAG_SUSPECTED_OUTAGE,
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
    format_btc_volume,
    format_usd_volume,
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


def test_volume_thousand_separators() -> None:
    assert format_btc_volume(Decimal("52676.10918656")) == "52,676.1"
    assert format_usd_volume(Decimal(3750512974)) == "$3,750,512,974"
    assert format_usd_volume(Decimal("705.5")) == "$706"


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
    assert "No zero-volume candles this month." not in result.notes
    assert "1 zero-volume interval, 1 minute total." in result.notes
    assert format_utc_range(start + 4 * 60, start + 5 * 60) in result.notes
    assert "Source gap fills" not in result.notes
    assert "updater:fill_missing_minutes" not in result.notes


def build_six_minute_snapshot(tmp_path: Path) -> Path:
    start = 1_577_836_800
    month, historical_rows, updates_rows = six_minute_month(start)
    historical, updates = write_pair(tmp_path, historical_rows, updates_rows)
    sidecar = tmp_path / "sidecar.csv"
    write_sidecar(sidecar, [])
    intro = tmp_path / "intro.md"
    intro.write_text("x\n", encoding="utf-8")
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
    return tmp_path / "out"


def test_parquet_ohlcv_columns_are_typed(tmp_path: Path) -> None:
    """The Parquet asset carries typed numerics, not the CSV's decimal text."""
    start = 1_577_836_800
    month = MonthWindow(2020, 1, start, start + 6 * 60)
    historical_rows = [
        [str(start), "7160.03", "7161.5", "7159.9", "7160.5", "0.06"],
        [str(start + 60), "7160.5", "7162.1", "7160.0", "7161.0", "1.5"],
        [str(start + 120), "7161.0", "7161.0", "7160.1", "7160.1", "0.75"],
    ]
    updates_rows = [
        [str(start + 180), "7160.1", "7163.3", "7160.1", "7163.0", "2.25"],
        [str(start + 240), "7163.0", "7163.0", "7162.2", "7162.5", "0.1"],
        [str(start + 300), "7162.5", "7164.0", "7162.5", "7163.9", "3"],
    ]
    historical, updates = write_pair(tmp_path, historical_rows, updates_rows)
    sidecar = tmp_path / "sidecar.csv"
    write_sidecar(sidecar, [])
    intro = tmp_path / "intro.md"
    intro.write_text("x\n", encoding="utf-8")

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
    table = pq.read_table(tmp_path / "out" / "btcusd_bitstamp_1min.parquet")
    expected_schema = pa.schema(
        [("timestamp", pa.int64())]
        + [(name, pa.float64()) for name in OHLCV_HEADER[1:]]
    )
    assert table.schema.equals(expected_schema), str(table.schema)
    all_rows = historical_rows + updates_rows
    assert table.column("timestamp").to_pylist() == [int(row[0]) for row in all_rows]
    for index, name in enumerate(OHLCV_HEADER[1:], start=1):
        expected_values = [float(row[index]) for row in all_rows]
        assert table.column(name).to_pylist() == expected_values, name


def test_release_validation_pins_parquet_to_csv(tmp_path: Path) -> None:
    """The post-build check accepts the built pair and refuses a doctored one."""
    from scripts.publish_monthly import verify_parquet_matches_csv

    output_dir = build_six_minute_snapshot(tmp_path)
    csv_path = output_dir / "btcusd_bitstamp_1min.csv.gz"
    parquet_path = output_dir / "btcusd_bitstamp_1min.parquet"

    assert verify_parquet_matches_csv(csv_path, parquet_path) == 6

    table = pq.read_table(parquet_path)
    doctored_close = table.column("close").to_pylist()
    doctored_close[3] += 0.5
    doctored = table.set_column(
        table.schema.get_field_index("close"),
        "close",
        pa.array(doctored_close, type=pa.float64()),
    )
    pq.write_table(doctored, parquet_path)
    with pytest.raises(PublishError, match="row 3 does not match"):
        verify_parquet_matches_csv(csv_path, parquet_path)

    pq.write_table(table.slice(0, 5), parquet_path)
    with pytest.raises(PublishError, match="more rows than"):
        verify_parquet_matches_csv(csv_path, parquet_path)


def test_release_validation_refuses_string_columns(tmp_path: Path) -> None:
    """A Parquet with string OHLCV columns must be refused outright."""
    from scripts.publish_monthly import verify_parquet_matches_csv

    output_dir = build_six_minute_snapshot(tmp_path)
    csv_path = output_dir / "btcusd_bitstamp_1min.csv.gz"
    parquet_path = output_dir / "btcusd_bitstamp_1min.parquet"

    table = pq.read_table(parquet_path)
    strung = pa.table(
        {
            "timestamp": table.column("timestamp"),
            **{
                name: pa.array(
                    [format(value, "g") for value in table.column(name).to_pylist()],
                    type=pa.string(),
                )
                for name in OHLCV_HEADER[1:]
            },
        }
    )
    pq.write_table(strung, parquet_path)
    with pytest.raises(PublishError, match="schema does not match"):
        verify_parquet_matches_csv(csv_path, parquet_path)


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
            Interval(
                start,
                start + 60,
                FLAG_SOURCE_GAP_FILLED,
                "updater:fill_missing_minutes",
                "0",
            ),
        ],
        intro_text=None,
    )
    assert "# January 2020" in notes
    assert (
        "Full Bitstamp BTC/USD 1-minute history from January 2012 through January 2020."
    ) in notes
    assert "## Price summary for January 2020" in notes
    assert "- Volume: 7.0 BTC / $706" in notes
    assert "Volume (USD)" not in notes
    assert "Candles:" not in notes
    assert "## Exchange status" in notes
    assert "(sidecar)" not in notes
    assert "No zero-volume candles this month." in notes
    assert "Confirmed outages" in notes
    assert f"- {format_utc_range(start, start + 60)}; https://stspg.io/a" in notes
    assert "price_jump" not in notes
    assert "Change: +1.00%" in notes
    assert "Suspected outages" not in notes
    assert "Scheduled maintenance" not in notes
    assert "Source gap fills" not in notes
    assert "updater:fill_missing_minutes" not in notes


def test_notes_zero_runs_annotate_status_overlap() -> None:
    start = 1_577_836_800
    month = MonthWindow(2020, 1, start, start + 4 * 60)
    from scripts.publish_monthly import MonthStats

    stats = MonthStats()
    stats.add_row(start, "100", "100", "100", "100", "1")
    stats.add_row(start + 60, "100", "100", "100", "100", "0")
    stats.add_row(start + 120, "100", "100", "100", "100", "0")
    stats.add_row(start + 180, "100", "100", "100", "100", "1")
    stats.finish()
    notes = render_notes(
        month=month,
        stats=stats,
        sidecar=[
            Interval(
                start + 60,
                start + 180,
                FLAG_CONFIRMED_OUTAGE,
                "https://stspg.io/a",
                "0.10",
            ),
            Interval(
                start + 60,
                start + 120,
                FLAG_SUSPECTED_OUTAGE,
                "zero_volume>=60m",
                "0.01",
            ),
        ],
        intro_text=None,
    )
    assert "## Zero-volume candle report" in notes
    assert "1 zero-volume interval, 2 minutes total." in notes
    assert f"- {format_utc_range(start + 60, start + 180)} -- confirmed outage" in notes
    assert "Suspected outages" in notes
    assert "zero_volume>=60m" in notes
    assert "price_jump" not in notes
    assert "scheduled maintenance" not in notes


def test_notes_zero_run_scheduled_maintenance_note() -> None:
    start = 1_577_836_800
    month = MonthWindow(2020, 1, start, start + 3 * 60)
    from scripts.publish_monthly import MonthStats

    stats = MonthStats()
    stats.add_row(start, "100", "100", "100", "100", "0")
    stats.add_row(start + 60, "100", "100", "100", "100", "1")
    stats.add_row(start + 120, "100", "100", "100", "100", "1")
    stats.finish()
    notes = render_notes(
        month=month,
        stats=stats,
        sidecar=[
            Interval(
                start,
                start + 60,
                FLAG_SCHEDULED_MAINTENANCE,
                "https://stspg.io/m",
                "0",
            ),
        ],
        intro_text=None,
    )
    assert "1 zero-volume interval, 1 minute total." in notes
    assert f"- {format_utc_range(start, start + 60)} -- scheduled maintenance" in notes
    assert "### Scheduled maintenance" in notes
    assert f"- {format_utc_range(start, start + 60)}; https://stspg.io/m" in notes
    assert "price_jump" not in notes


def test_notes_omit_maintenance_without_zero_overlap() -> None:
    start = 1_577_836_800
    month = MonthWindow(2020, 1, start, start + 3 * 60)
    from scripts.publish_monthly import MonthStats

    stats = MonthStats()
    stats.add_row(start, "100", "100", "100", "100", "1")
    stats.add_row(start + 60, "100", "100", "100", "100", "1")
    stats.add_row(start + 120, "100", "100", "100", "100", "1")
    stats.finish()
    notes = render_notes(
        month=month,
        stats=stats,
        sidecar=[
            Interval(
                start,
                start + 60,
                FLAG_SCHEDULED_MAINTENANCE,
                "https://stspg.io/m",
                "0.50",
            ),
        ],
        intro_text=None,
    )
    assert "Scheduled maintenance" not in notes
    assert "https://stspg.io/m" not in notes
    assert "No zero-volume candles this month." in notes
    assert "None this month." in notes


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
    assert "# August 2026" in result.notes
    assert (
        "Full Bitstamp BTC/USD 1-minute history from January 2012 through August 2026."
    ) in result.notes
    assert result.tag == FIRST_RELEASE_TAG
