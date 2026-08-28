import os
import sys

import pandas as pd


def load_original_data(
    bulk_data_path: str, missing_data_path: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the original and missing datasets."""
    try:
        bulk_df = pd.read_csv(bulk_data_path)
        print(f"Loaded bulk data with {len(bulk_df)} records.")
    except Exception as e:  # noqa: BLE001
        print(f"Error loading bulk data: {e}")
        sys.exit(1)

    try:
        missing_df = pd.read_csv(missing_data_path)
        print(f"Loaded missing data with {len(missing_df)} records.")
    except Exception as e:  # noqa: BLE001
        print(f"Error loading missing data: {e}")
        sys.exit(1)

    return bulk_df, missing_df


def standardize_bulk_data(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize the bulk dataset columns."""
    df = df.rename(
        columns={
            "Timestamp": "timestamp",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def standardize_missing_data(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize the missing dataset columns."""
    df = df.rename(
        columns={
            "timestamp_unix": "timestamp",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
    )
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def merge_datasets(bulk_df: pd.DataFrame, missing_df: pd.DataFrame) -> pd.DataFrame:
    """Merge the bulk and missing datasets."""
    merged_df = pd.concat([bulk_df, missing_df], ignore_index=True)
    merged_df.drop_duplicates(subset="timestamp", inplace=True)
    merged_df.sort_values(by="timestamp", inplace=True)
    merged_df.reset_index(drop=True, inplace=True)
    return merged_df


def check_missing_timestamps(df: pd.DataFrame, interval_seconds: int = 60) -> list[int]:
    """Check for missing timestamps in the merged dataset."""
    df = df.sort_values(by="timestamp").reset_index(drop=True)

    # Convert timestamps to integers
    start = int(df["timestamp"].iloc[0])
    end = int(df["timestamp"].iloc[-1] + interval_seconds)

    expected_timestamps = list(range(start, end, interval_seconds))
    actual_timestamps = df["timestamp"].astype(int).tolist()
    missing = sorted(set(expected_timestamps) - set(actual_timestamps))

    if missing:
        print(f"Missing {len(missing)} timestamps.")
        # Optionally, print missing timestamps
        # Group consecutive timestamps into ranges
        ranges = []
        range_start = missing[0]
        prev = missing[0]

        for ts in missing[1:]:
            if ts - prev > interval_seconds:
                ranges.append((range_start, prev))
                range_start = ts
            prev = ts
        ranges.append((range_start, prev))

        # Print the ranges
        for start, end in ranges:
            print(
                f"Gap from {start} to {end} ({(end - start) // interval_seconds} minutes)"
            )
    else:
        print("No missing timestamps found.")
    return missing


def validate_data(df: pd.DataFrame) -> None:
    """Validate data integrity."""
    # Check for duplicates
    duplicates = df.duplicated(subset="timestamp").sum()
    if duplicates > 0:
        print(f"Data contains {duplicates} duplicate timestamps.")
    else:
        print("No duplicate timestamps found.")

    # Check for NaN values
    nan_values = df.isnull().sum().sum()
    if nan_values > 0:
        print(f"Data contains {nan_values} missing values.")
    else:
        print("No missing values found in data.")

    # Additional validations can be added here


def save_merged_data(df: pd.DataFrame, output_path: str) -> None:
    """Save the merged dataset to the specified path."""
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        df.to_csv(output_path, index=False)
        print(f"Merged data saved to {output_path}.")
    except Exception as e:  # noqa: BLE001
        print(f"Error saving merged data: {e}")
        sys.exit(1)


def main() -> None:
    # Define file paths
    bulk_data_path = os.path.join("data", "original", "btcusd_1-min_data.csv")
    missing_data_path = os.path.join(
        "data", "original", "missing_ohlc_data_all_gaps_as_of_1736148000.csv"
    )
    output_path = os.path.join(
        "data", "historical", "btcusd_bitstamp_1min_2012-2025.csv"
    )

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Load data
    bulk_df, missing_df = load_original_data(bulk_data_path, missing_data_path)

    # Standardize datasets
    bulk_df = standardize_bulk_data(bulk_df)
    missing_df = standardize_missing_data(missing_df)

    # Merge datasets
    merged_df = merge_datasets(bulk_df, missing_df)

    # Check for missing timestamps
    missing_timestamps = check_missing_timestamps(merged_df)

    if missing_timestamps:
        # Truncate data up to the first missing timestamp
        first_missing = missing_timestamps[0]
        merged_df = merged_df[merged_df["timestamp"] < first_missing]
        print(f"Truncated data up to first missing timestamp: {first_missing}")

    # Set timestamp column to integer type
    merged_df["timestamp"] = merged_df["timestamp"].astype(int)

    # Validate data integrity
    validate_data(merged_df)

    # Save merged data
    save_merged_data(merged_df, output_path)

    if missing_timestamps:
        print("Data preprocessing completed with truncation due to missing timestamps.")
    else:
        print("Data preprocessing completed successfully.")


if __name__ == "__main__":
    main()
