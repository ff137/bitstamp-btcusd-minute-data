"""Tests for scripts/update_data.py."""

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts.update_data import (
    COLUMN_NAMES,
    fetch_and_append_missing_data,
    fill_missing_minutes,
)

EXISTING_TS = 1_700_000_000
NEW_TS = EXISTING_TS + 60


def _daily_row(timestamp: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [timestamp],
            "open": [77590.0],
            "high": [77590.0],
            "low": [77590.0],
            "close": [77590.0],
            "volume": [0.0185672],
        }
    )


def test_appended_rows_drop_api_trailing_zeros(tmp_path: Path) -> None:
    daily_df = _daily_row(EXISTING_TS)
    api_rows = [
        {
            "timestamp": str(NEW_TS),
            "open": "77590.00",
            "high": "77600.00",
            "low": "77580.00",
            "close": "77595.00",
            "volume": "0.01856720",
        }
    ]

    with patch("scripts.update_data.fetch_bitstamp_data", return_value=api_rows):
        updated = fetch_and_append_missing_data(
            "btcusd", (NEW_TS, NEW_TS + 60), daily_df
        )
    updated, _filled = fill_missing_minutes(updated)

    csv_path = tmp_path / "btcusd_bitstamp_1min_latest.csv"
    updated.to_csv(csv_path, index=False)
    text = csv_path.read_text(encoding="utf-8")

    assert list(updated.columns) == COLUMN_NAMES
    assert "77590.00" not in text
    assert "77600.00" not in text
    assert "77580.00" not in text
    assert "77595.00" not in text
    assert "0.01856720" not in text
    assert "77590.0,77590.0,77590.0,77590.0,0.0185672" in text
    assert "77590.0,77600.0,77580.0,77595.0,0.0185672" in text
