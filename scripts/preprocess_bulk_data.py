"""Build the published historical gzip from the pinned Kaggle snapshot."""

import argparse
import sys
from pathlib import Path

from scripts.historical_source import (
    DEFAULT_OUTPUT_PATH,
    DEFAULT_SOURCE_CSV,
    SourceError,
    build_historical_dataset,
    load_manifest,
    publication_spec_from_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stream the pinned Kaggle BTC/USD snapshot into the published "
            "historical gzip. No community merge, timezone shift, or fill."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to source-manifest.json (default: data/original/source-manifest.json).",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to the pinned Kaggle CSV (default: data/original/btcusd_1-min_data.csv).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to write the historical gzip.",
    )
    parser.add_argument(
        "--skip-hash",
        action="store_true",
        help="Skip SHA-256 verification (tests only).",
    )
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        spec = publication_spec_from_manifest(
            manifest,
            source_path=args.source or DEFAULT_SOURCE_CSV,
        )
        row_count = build_historical_dataset(
            spec,
            args.output,
            verify_hash=not args.skip_hash,
        )
    except (OSError, SourceError, KeyError, TypeError, ValueError) as exc:
        print(f"Error building historical dataset: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {row_count} rows to {args.output} "
        f"({spec.first_timestamp}..{spec.last_timestamp})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
