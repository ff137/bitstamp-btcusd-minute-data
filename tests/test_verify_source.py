"""Tests for bounded source verification helpers."""

import csv
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.historical_source import (
    KAGGLE_HEADER,
    PUBLIC_HEADER,
    SourceError,
    sha256_file,
)
from scripts.verify_source import (
    MAX_API_REQUESTS,
    RequestBudget,
    Window,
    compare_window,
    fetch_bitstamp_window,
    needed_timestamps,
    run_verification,
    verification_windows,
    verify_community_subset,
)


def test_verification_windows_stay_under_api_budget() -> None:
    windows = verification_windows()
    requests_needed = 0
    for window in windows:
        minutes = (window.end - window.start) // 60 + 1
        requests_needed += (minutes + 999) // 1000
    assert requests_needed < MAX_API_REQUESTS
    assert requests_needed == len(windows)
    names = [window.name for window in windows]
    assert "early-2012" in names
    assert "boundary-2024-09-13" in names
    assert "spring-dst-2012" in names
    assert "fall-dst-2024" in names
    assert "seam-2025-01-07" in names


def test_compare_window_detects_mismatch() -> None:
    window = Window("sample", 60, 120)
    source = {
        60: ["60", "1.0", "1.0", "1.0", "1.0", "0.0"],
        120: ["120", "2.0", "2.0", "2.0", "2.0", "0.5"],
    }
    api = {
        60: ["60", "1.00", "1.00", "1.00", "1.00", "0.00000000"],
        120: ["120", "2.0", "2.0", "2.0", "9.0", "0.5"],
    }
    mismatches = compare_window(window, source, api)
    assert len(mismatches) == 1
    assert mismatches[0].timestamp == 120


def test_fetch_bitstamp_window_respects_budget() -> None:
    session = Mock()
    session.get.return_value.json.return_value = {
        "data": {
            "ohlc": [
                {
                    "timestamp": "60",
                    "open": "1",
                    "high": "1",
                    "low": "1",
                    "close": "1",
                    "volume": "0",
                }
            ]
        }
    }
    session.get.return_value.raise_for_status = Mock()
    budget = RequestBudget(maximum=1)
    rows = fetch_bitstamp_window(60, 60, budget, session=session)
    assert rows[60][4] == "1"
    with pytest.raises(SourceError, match="request budget exceeded"):
        fetch_bitstamp_window(60, 60, budget, session=session)


def test_fetch_bitstamp_window_rejects_malformed_payload() -> None:
    session = Mock()
    session.get.return_value.json.return_value = []
    session.get.return_value.raise_for_status = Mock()
    with pytest.raises(SourceError, match="JSON object"):
        fetch_bitstamp_window(60, 60, RequestBudget(), session=session)


def test_community_subset_and_api_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "btcusd_1-min_data.csv"
    rows = [
        ["1325376060", "4.58", "4.58", "4.58", "4.58", "0.0"],
        ["1325376120", "4.59", "4.59", "4.59", "4.59", "0.1"],
    ]
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(KAGGLE_HEADER)
        writer.writerows(rows)
    community = tmp_path / "missing_ohlc_data_all_gaps_as_of_1736148000.csv"
    with community.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "timestamp_unix",
                "timestamp_utc",
                "timestamp_ny",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )
        writer.writerow(
            ["1325376120", "x", "y", "4.59", "4.59", "4.59", "4.59", "0.10"]
        )
    matched, total = verify_community_subset(community, source)
    assert (matched, total) == (1, 1)

    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "kaggle": {
                    "sha256": sha256_file(source),
                    "header": list(KAGGLE_HEADER),
                    "first_timestamp": 1325376060,
                    "last_timestamp": 1325376120,
                    "row_count": 2,
                },
                "publication": {
                    "first_timestamp": 1325376060,
                    "last_timestamp": 1325376120,
                    "row_count": 2,
                    "columns": list(PUBLIC_HEADER),
                },
                "legacy_community_gaps": {
                    "filename": community.name,
                    "sha256": sha256_file(community),
                    "row_count": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    assert (
        run_verification(
            manifest_path=manifest,
            source_path=source,
            skip_api=True,
        )
        == 0
    )

    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {
        "data": {
            "ohlc": [
                {
                    "timestamp": row[0],
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": row[5],
                }
                for row in rows
            ]
        }
    }
    session = Mock()
    session.get.return_value = response
    monkeypatch.setattr(
        "scripts.verify_source.verification_windows",
        lambda: (Window("fixture", 1325376060, 1325376120),),
    )
    assert (
        run_verification(
            manifest_path=manifest,
            source_path=source,
            session=session,
        )
        == 0
    )


def test_needed_timestamps_covers_inclusive_end() -> None:
    window = Window("tiny", 60, 180)
    assert needed_timestamps([window]) == {60, 120, 180}
