import os
import sys

import pandas as pd


def load_data(data_path: str) -> pd.DataFrame:
    """Load the dataset from the given path."""
    try:
        df = pd.read_csv(data_path)
        print(f"Loaded data with {len(df)} records from {data_path}.")
        return df
    except Exception as e:
        print(f"Error loading data from {data_path}: {e}")
        sys.exit(1)


def get_timestamp_range(df: pd.DataFrame) -> None:
    """Get the timestamp range in epoch and human-readable format."""
    min_timestamp = df["timestamp"].min()
    max_timestamp = df["timestamp"].max()
    min_datetime = pd.to_datetime(min_timestamp, unit="s", utc=True)
    max_datetime = pd.to_datetime(max_timestamp, unit="s", utc=True)
    print("Timestamp Range:")
    print(f"  From: {min_timestamp} ({min_datetime})")
    print(f"  To:   {max_timestamp} ({max_datetime})")


def print_data_schema(df: pd.DataFrame) -> None:
    """Print the data schema."""
    print("\nData Schema:")
    print(df.dtypes)


def check_missing_values(df: pd.DataFrame) -> None:
    """Check and print missing values per column."""
    missing = df.isnull().sum()
    print("\nMissing Values per Column:")
    print(missing)


def check_duplicates(df: pd.DataFrame) -> None:
    """Check and print the number of duplicate timestamps."""
    duplicates = df.duplicated(subset="timestamp").sum()
    if duplicates > 0:
        print(f"\nNumber of Duplicate Timestamps: {duplicates}")
    else:
        print("\nNo duplicate timestamps found.")


def print_descriptive_statistics(df: pd.DataFrame) -> None:
    """Print descriptive statistics of the dataset."""
    print("\nDescriptive Statistics:")
    print(df.describe())


def print_sample_rows(df: pd.DataFrame) -> None:
    """Print first and last few rows of the dataset."""
    print("\nFirst 5 rows:")
    print(df.head())

    print("\nMost recent 5 rows:")
    print(df.tail())


def main() -> None:
    # Determine which dataset to inspect based on command-line argument
    data_type = sys.argv[1] if len(sys.argv) > 1 else "merged"

    # Define paths to the datasets
    data_paths = {
        "bulk": os.path.join("data", "historical", "btcusd_bitstamp_1min_2012-2025.csv"),
        "updated": os.path.join("data", "updates", "btcusd_bitstamp_1min_latest.csv")
    }

    # Load and merge datasets if 'merged' is selected
    if data_type == "merged":
        bulk_df = load_data(data_paths["bulk"])
        updated_df = load_data(data_paths["updated"])
        df = pd.concat([bulk_df, updated_df])
        print(f"Merged data with {len(df)} records.")
    else:
        # Get the path for the selected dataset
        data_path = data_paths.get(data_type)
        if not data_path:
            print(f"Invalid data type specified: {data_type}. Choose from 'bulk', 'updated', or 'merged'.")
            sys.exit(1)
        # Load the selected data
        df = load_data(data_path)

    # Get and print the timestamp range
    get_timestamp_range(df)

    # Print the data schema
    print_data_schema(df)

    # Check for missing values
    check_missing_values(df)

    # Check for duplicate timestamps
    check_duplicates(df)

    # Print descriptive statistics
    print_descriptive_statistics(df)

    # Print sample rows
    print_sample_rows(df)


if __name__ == "__main__":
    main()
