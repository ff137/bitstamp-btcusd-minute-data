"""Pinned Kaggle source contract and streaming historical bulk builder."""

import csv
import gzip
import hashlib
import io
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

MINUTE_SECONDS = 60
PUBLIC_HEADER = ("timestamp", "open", "high", "low", "close", "volume")
KAGGLE_HEADER = ("Timestamp", "Open", "High", "Low", "Close", "Volume")
HASH_CHUNK_SIZE = 1024 * 1024
GZIP_COMPRESSLEVEL = 9
GZIP_MTIME = 0

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "data" / "original" / "source-manifest.json"
DEFAULT_SOURCE_CSV = REPO_ROOT / "data" / "original" / "btcusd_1-min_data.csv"
DEFAULT_OUTPUT_PATH = (
    REPO_ROOT / "data" / "historical" / "btcusd_bitstamp_1min_2012-2025.csv.gz"
)


class SourceError(ValueError):
    """Pinned source or publication slice is not acceptable."""


@dataclass(frozen=True)
class PublicationSpec:
    source_path: Path
    sha256: str
    header: tuple[str, ...]
    source_first_timestamp: int
    source_last_timestamp: int
    source_expected_rows: int
    first_timestamp: int
    last_timestamp: int
    expected_rows: int
    output_sha256: str | None = None


def load_manifest(path: Path | None = None) -> dict:
    manifest_path = path or DEFAULT_MANIFEST_PATH
    with manifest_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def publication_spec_from_manifest(
    manifest: dict,
    *,
    source_path: Path | None = None,
) -> PublicationSpec:
    kaggle = manifest["kaggle"]
    publication = manifest["publication"]
    spec = PublicationSpec(
        source_path=source_path or DEFAULT_SOURCE_CSV,
        sha256=str(kaggle["sha256"]),
        header=tuple(kaggle["header"]),
        source_first_timestamp=int(kaggle["first_timestamp"]),
        source_last_timestamp=int(kaggle["last_timestamp"]),
        source_expected_rows=int(kaggle["row_count"]),
        first_timestamp=int(publication["first_timestamp"]),
        last_timestamp=int(publication["last_timestamp"]),
        expected_rows=int(publication["row_count"]),
        output_sha256=(str(publication["sha256"]) if "sha256" in publication else None),
    )
    if spec.header != KAGGLE_HEADER:
        raise SourceError(
            f"manifest source header must be {list(KAGGLE_HEADER)!r}, "
            f"found {list(spec.header)!r}"
        )
    if tuple(publication["columns"]) != PUBLIC_HEADER:
        raise SourceError(
            f"manifest publication columns must be {list(PUBLIC_HEADER)!r}"
        )
    _validate_extent(
        "source",
        spec.source_first_timestamp,
        spec.source_last_timestamp,
        spec.source_expected_rows,
    )
    _validate_extent(
        "publication",
        spec.first_timestamp,
        spec.last_timestamp,
        spec.expected_rows,
    )
    if spec.first_timestamp != spec.source_first_timestamp:
        raise SourceError("publication must start at the first source timestamp")
    if spec.last_timestamp > spec.source_last_timestamp:
        raise SourceError("publication must end within the source range")
    return spec


def _validate_extent(name: str, first: int, last: int, rows: int) -> None:
    if first % MINUTE_SECONDS or last % MINUTE_SECONDS or last < first:
        raise SourceError(f"invalid {name} timestamp range {first}..{last}")
    expected_rows = (last - first) // MINUTE_SECONDS + 1
    if rows != expected_rows:
        raise SourceError(
            f"{name} row count {rows} does not match its timestamp range "
            f"({expected_rows})"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> str:
    actual = sha256_file(path)
    if actual != expected:
        raise SourceError(
            f"SHA-256 mismatch for {path}: expected {expected}, found {actual}"
        )
    return actual


def _parse_minute_timestamp(raw: str, *, row_number: int) -> int:
    if raw != raw.strip() or not raw.isdigit():
        raise SourceError(
            f"row {row_number}: timestamp must be an unsigned base-10 integer, "
            f"found {raw!r}"
        )
    timestamp = int(raw)
    if timestamp % MINUTE_SECONDS != 0:
        raise SourceError(
            f"row {row_number}: timestamp {timestamp} is not minute-aligned"
        )
    return timestamp


def _validate_ohlcv(row: Sequence[str], *, row_number: int) -> None:
    try:
        open_price, high_price, low_price, close_price, volume = (
            Decimal(value) for value in row[1:]
        )
    except (InvalidOperation, ValueError) as exc:
        raise SourceError(f"row {row_number}: invalid OHLCV number") from exc

    values = (open_price, high_price, low_price, close_price, volume)
    if not all(value.is_finite() for value in values):
        raise SourceError(f"row {row_number}: OHLCV values must be finite")
    if min(open_price, high_price, low_price, close_price) <= 0:
        raise SourceError(f"row {row_number}: OHLC prices must be positive")
    if volume < 0:
        raise SourceError(f"row {row_number}: volume must be non-negative")
    if low_price > min(open_price, close_price):
        raise SourceError(f"row {row_number}: low is above open or close")
    if high_price < max(open_price, close_price):
        raise SourceError(f"row {row_number}: high is below open or close")


def verify_source_shape(source_path: Path, spec: PublicationSpec) -> int:
    """Validate the complete pinned CSV's range and minute continuity."""
    with source_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != list(spec.header):
            raise SourceError(
                f"invalid source header: expected {list(spec.header)!r}, "
                f"found {header!r}"
            )

        first_timestamp: int | None = None
        previous_timestamp: int | None = None
        row_count = 0
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(PUBLIC_HEADER):
                raise SourceError(
                    f"row {row_number}: expected {len(PUBLIC_HEADER)} columns, "
                    f"found {len(row)}"
                )
            if any(field.strip() == "" for field in row):
                raise SourceError(f"row {row_number}: empty field")
            timestamp = _parse_minute_timestamp(row[0], row_number=row_number)
            if previous_timestamp is None:
                first_timestamp = timestamp
            elif timestamp != previous_timestamp + MINUTE_SECONDS:
                raise SourceError(
                    f"row {row_number}: expected timestamp "
                    f"{previous_timestamp + MINUTE_SECONDS}, found {timestamp}"
                )
            previous_timestamp = timestamp
            row_count += 1

    if first_timestamp != spec.source_first_timestamp:
        raise SourceError(
            f"expected source first timestamp {spec.source_first_timestamp}, "
            f"found {first_timestamp}"
        )
    if previous_timestamp != spec.source_last_timestamp:
        raise SourceError(
            f"expected source last timestamp {spec.source_last_timestamp}, "
            f"found {previous_timestamp}"
        )
    if row_count != spec.source_expected_rows:
        raise SourceError(
            f"expected {spec.source_expected_rows} source rows, found {row_count}"
        )
    return row_count


def iter_publication_rows(
    source_path: Path,
    spec: PublicationSpec,
) -> Iterator[list[str]]:
    """Yield public six-column rows for the pinned publication slice."""
    with source_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise SourceError(f"{source_path} is empty") from exc

        if header != list(spec.header):
            raise SourceError(
                f"invalid source header: expected {list(spec.header)!r}, "
                f"found {header!r}"
            )

        previous_timestamp: int | None = None
        emitted = 0
        reached_end = False

        for row_number, row in enumerate(reader, start=2):
            if reached_end:
                break
            if len(row) != len(PUBLIC_HEADER):
                raise SourceError(
                    f"row {row_number}: expected {len(PUBLIC_HEADER)} columns, "
                    f"found {len(row)}"
                )
            timestamp = _parse_minute_timestamp(row[0], row_number=row_number)
            if any(field.strip() == "" for field in row):
                raise SourceError(f"row {row_number}: empty field")
            _validate_ohlcv(row, row_number=row_number)

            if timestamp < spec.first_timestamp:
                raise SourceError(
                    f"row {row_number}: timestamp {timestamp} precedes "
                    f"publication start {spec.first_timestamp}"
                )
            if timestamp > spec.last_timestamp:
                raise SourceError(
                    "publication slice ended before last_timestamp "
                    f"{spec.last_timestamp} (next source timestamp {timestamp})"
                )

            if previous_timestamp is None:
                if timestamp != spec.first_timestamp:
                    raise SourceError(
                        f"row {row_number}: expected first timestamp "
                        f"{spec.first_timestamp}, found {timestamp}"
                    )
            elif timestamp == previous_timestamp:
                raise SourceError(f"row {row_number}: duplicate timestamp {timestamp}")
            elif timestamp != previous_timestamp + MINUTE_SECONDS:
                raise SourceError(
                    f"row {row_number}: timestamp gap: expected "
                    f"{previous_timestamp + MINUTE_SECONDS}, found {timestamp}"
                )

            yield [row[0], row[1], row[2], row[3], row[4], row[5]]
            emitted += 1
            previous_timestamp = timestamp
            if timestamp == spec.last_timestamp:
                try:
                    following = next(reader)
                except StopIteration:
                    reached_end = True
                    break
                following_number = row_number + 1
                if len(following) != len(PUBLIC_HEADER):
                    raise SourceError(
                        f"row {following_number}: expected {len(PUBLIC_HEADER)} columns, "
                        f"found {len(following)}"
                    )
                following_timestamp = _parse_minute_timestamp(
                    following[0], row_number=following_number
                )
                if following_timestamp == spec.last_timestamp:
                    raise SourceError(
                        f"row {following_number}: duplicate timestamp "
                        f"{following_timestamp}"
                    )
                if following_timestamp < spec.last_timestamp:
                    raise SourceError(
                        f"row {following_number}: out-of-order timestamp "
                        f"{following_timestamp}"
                    )
                reached_end = True
                break

    if previous_timestamp is None:
        raise SourceError(f"{source_path} contains no data rows")
    if previous_timestamp != spec.last_timestamp:
        raise SourceError(
            f"source ended at {previous_timestamp}, expected publication "
            f"last_timestamp {spec.last_timestamp}"
        )
    if emitted != spec.expected_rows:
        raise SourceError(
            f"expected {spec.expected_rows} publication rows, emitted {emitted}"
        )


def write_historical_gzip(
    rows: Iterable[Sequence[str]],
    output_path: Path,
    *,
    compresslevel: int = GZIP_COMPRESSLEVEL,
) -> int:
    """Write public CSV rows to a deterministic gzip file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    count = 0
    try:
        with (
            tmp_path.open("wb") as raw,
            gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                mtime=GZIP_MTIME,
                compresslevel=compresslevel,
            ) as gz,
            io.TextIOWrapper(
                gz, encoding="utf-8", newline="", write_through=True
            ) as text,
        ):
            writer = csv.writer(text, lineterminator="\n")
            writer.writerow(PUBLIC_HEADER)
            for row in rows:
                writer.writerow(row)
                count += 1
        tmp_path.replace(output_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return count


def build_historical_dataset(
    spec: PublicationSpec,
    output_path: Path,
    *,
    verify_hash: bool = True,
) -> int:
    """Verify the pinned source and write the publication gzip."""
    if not spec.source_path.is_file():
        raise SourceError(f"source CSV not found: {spec.source_path}")
    if verify_hash:
        verify_sha256(spec.source_path, spec.sha256)
    verify_source_shape(spec.source_path, spec)

    candidate_path = output_path.with_name(output_path.name + ".candidate")
    try:
        row_count = write_historical_gzip(
            iter_publication_rows(spec.source_path, spec), candidate_path
        )
        if spec.output_sha256 is not None:
            verify_sha256(candidate_path, spec.output_sha256)
        candidate_path.replace(output_path)
    except Exception:
        if candidate_path.exists():
            candidate_path.unlink()
        raise
    return row_count
