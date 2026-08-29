"""Tests for the pinned Kaggle historical builder."""

import csv
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.historical_source import (
    KAGGLE_HEADER,
    PUBLIC_HEADER,
    PublicationSpec,
    SourceError,
    build_historical_dataset,
    iter_publication_rows,
    load_manifest,
    publication_spec_from_manifest,
    sha256_file,
    verify_sha256,
    write_historical_gzip,
)

FIRST = 1_325_376_060
SECOND = FIRST + 60
THIRD = FIRST + 120
FOURTH = FIRST + 180


def _write_kaggle(
    path: Path,
    rows: list[list[str]],
    header: list[str] | None = None,
) -> str:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header or list(KAGGLE_HEADER))
        writer.writerows(rows)
    return sha256_file(path)


def _spec(path: Path, digest: str, **overrides: object) -> PublicationSpec:
    values: dict[str, object] = {
        "source_path": path,
        "sha256": digest,
        "header": KAGGLE_HEADER,
        "source_first_timestamp": FIRST,
        "source_last_timestamp": FOURTH,
        "source_expected_rows": 4,
        "first_timestamp": FIRST,
        "last_timestamp": SECOND,
        "expected_rows": 2,
    }
    values.update(overrides)
    return PublicationSpec(**values)  # type: ignore[arg-type]


def _standard_rows() -> list[list[str]]:
    return [
        [str(FIRST), "4.58", "4.58", "4.58", "4.58", "0.0"],
        [str(SECOND), "4.59", "4.60", "4.59", "4.60", "1.50"],
        [str(THIRD), "5.00", "5.00", "5.00", "5.00", "0.0"],
        [str(FOURTH), "5.01", "5.02", "5.00", "5.01", "0.25"],
    ]


def test_load_manifest_publication_slice() -> None:
    manifest = load_manifest()
    spec = publication_spec_from_manifest(manifest)
    assert spec.sha256 == (
        "c3ea6522e69673da38baf88755644d546363e8a96ac60f9e7dafe003c890817f"
    )
    assert spec.first_timestamp == 1_325_376_060
    assert spec.last_timestamp == 1_736_208_000
    assert spec.expected_rows == 6_847_200
    assert spec.header == KAGGLE_HEADER
    assert spec.source_expected_rows == 7_710_039
    assert (
        spec.output_sha256
        == "1be152060b39327b669cbed236eeb283191fadaf3862f76c1e974be54ceb1a20"
    )
    assert manifest["legacy_community_gaps"]["status"] == "audit_only"


def test_hash_rejection(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    digest = _write_kaggle(source, _standard_rows())
    spec = _spec(source, "0" * 64)
    with pytest.raises(SourceError, match="SHA-256 mismatch"):
        build_historical_dataset(spec, tmp_path / "out.csv.gz")
    verify_sha256(source, digest)


def test_header_and_string_preservation(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    digest = _write_kaggle(source, _standard_rows())
    output = tmp_path / "historical.csv.gz"
    spec = _spec(source, digest)
    assert build_historical_dataset(spec, output) == 2

    with gzip.open(output, mode="rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == list(PUBLIC_HEADER)
    assert rows[1] == _standard_rows()[0]
    assert rows[2] == _standard_rows()[1]
    assert len(rows) == 3


def test_cutoff_excludes_trailing_source_rows(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    digest = _write_kaggle(source, _standard_rows())
    spec = _spec(source, digest)
    rows = list(iter_publication_rows(source, spec))
    assert rows == _standard_rows()[:2]


def test_wrong_first_timestamp(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = _standard_rows()
    rows[0][0] = str(FIRST + 60)
    digest = _write_kaggle(source, rows)
    spec = _spec(
        source,
        digest,
        first_timestamp=FIRST,
        last_timestamp=SECOND + 60,
        expected_rows=2,
    )
    with pytest.raises(SourceError, match="expected first timestamp"):
        list(iter_publication_rows(source, spec))


def test_gap_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = [_standard_rows()[0], _standard_rows()[2]]
    digest = _write_kaggle(source, rows)
    spec = _spec(source, digest, last_timestamp=THIRD, expected_rows=2)
    with pytest.raises(SourceError, match="timestamp gap"):
        list(iter_publication_rows(source, spec))


def test_gap_after_publication_cutoff_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = [_standard_rows()[0], _standard_rows()[1], _standard_rows()[3]]
    digest = _write_kaggle(source, rows)
    spec = _spec(
        source,
        digest,
        source_last_timestamp=FOURTH,
        source_expected_rows=3,
    )
    with pytest.raises(SourceError, match="expected timestamp"):
        build_historical_dataset(spec, tmp_path / "out.csv.gz")


def test_duplicate_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = [_standard_rows()[0], _standard_rows()[1], _standard_rows()[1]]
    digest = _write_kaggle(source, rows)
    spec = _spec(source, digest)
    with pytest.raises(SourceError, match="duplicate timestamp"):
        list(iter_publication_rows(source, spec))


def test_invalid_numeric_field_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = [[str(FIRST), "", "4.58", "4.58", "4.58", "0.0"]]
    digest = _write_kaggle(source, rows)
    spec = _spec(source, digest, last_timestamp=FIRST, expected_rows=1)
    with pytest.raises(SourceError, match="empty field"):
        list(iter_publication_rows(source, spec))


def test_non_finite_numeric_field_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = [[str(FIRST), "NaN", "4.58", "4.58", "4.58", "0.0"]]
    digest = _write_kaggle(source, rows)
    spec = _spec(source, digest, last_timestamp=FIRST, expected_rows=1)
    with pytest.raises(SourceError, match="must be finite"):
        list(iter_publication_rows(source, spec))


def test_broken_ohlc_envelope_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = [[str(FIRST), "4.58", "4.00", "4.58", "4.58", "0.0"]]
    digest = _write_kaggle(source, rows)
    spec = _spec(source, digest, last_timestamp=FIRST, expected_rows=1)
    with pytest.raises(SourceError, match="high is below"):
        list(iter_publication_rows(source, spec))


def test_truncated_before_cutoff(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    digest = _write_kaggle(source, [_standard_rows()[0]])
    spec = _spec(source, digest)
    with pytest.raises(SourceError, match="source ended"):
        list(iter_publication_rows(source, spec))


def test_wrong_header_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    digest = _write_kaggle(
        source,
        _standard_rows(),
        header=["timestamp", "open", "high", "low", "close", "volume"],
    )
    spec = _spec(source, digest)
    with pytest.raises(SourceError, match="invalid source header"):
        list(iter_publication_rows(source, spec))


def test_row_before_publication_start_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    rows = [[str(FIRST - 60), "4.58", "4.58", "4.58", "4.58", "0.0"], *_standard_rows()]
    digest = _write_kaggle(source, rows)
    spec = _spec(source, digest)
    with pytest.raises(SourceError, match="precedes publication start"):
        list(iter_publication_rows(source, spec))


def test_deterministic_gzip(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    digest = _write_kaggle(source, _standard_rows())
    spec = _spec(source, digest)
    first = tmp_path / "a.csv.gz"
    second = tmp_path / "b.csv.gz"
    build_historical_dataset(spec, first)
    build_historical_dataset(spec, second)
    first_bytes = first.read_bytes()
    second_bytes = second.read_bytes()
    assert first_bytes == second_bytes
    assert first_bytes[4:8] == b"\x00\x00\x00\x00"
    assert (
        hashlib.sha256(first_bytes).hexdigest()
        == hashlib.sha256(second_bytes).hexdigest()
    )


def test_output_hash_mismatch_preserves_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    digest = _write_kaggle(source, _standard_rows())
    output = tmp_path / "historical.csv.gz"
    output.write_bytes(b"existing artifact")
    spec = _spec(source, digest, output_sha256="0" * 64)

    with pytest.raises(SourceError, match="SHA-256 mismatch"):
        build_historical_dataset(spec, output)

    assert output.read_bytes() == b"existing artifact"
    assert not output.with_name(output.name + ".candidate").exists()


def test_write_historical_gzip_round_trip(tmp_path: Path) -> None:
    output = tmp_path / "out.csv.gz"
    count = write_historical_gzip([_standard_rows()[0]], output)
    assert count == 1
    with gzip.open(output, mode="rt", encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == list(PUBLIC_HEADER)


def test_preprocess_cli(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    digest = _write_kaggle(source, _standard_rows())
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "kaggle": {
                    "sha256": digest,
                    "header": list(KAGGLE_HEADER),
                    "first_timestamp": FIRST,
                    "last_timestamp": FOURTH,
                    "row_count": 4,
                },
                "publication": {
                    "first_timestamp": FIRST,
                    "last_timestamp": SECOND,
                    "row_count": 2,
                    "columns": list(PUBLIC_HEADER),
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "historical.csv.gz"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/preprocess_bulk_data.py",
            "--manifest",
            str(manifest),
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.is_file()
    assert "Wrote 2 rows" in completed.stdout
