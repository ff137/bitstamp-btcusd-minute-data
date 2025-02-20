import logging
import os
from datetime import datetime, timezone
from typing import List, Tuple

import pandas as pd
import requests

# Configuration
CURRENCY_PAIR = "btcusd"
BULK_DATA_PATH = "data/historical/btcusd_bitstamp_1min_2012-2025.csv"
DAILY_DATA_PATH = "data/updates/btcusd_bitstamp_1min_latest.csv"
COLUMN_NAMES = ["timestamp", "open", "high", "low", "close", "volume"]

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Add console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Add handler to logger
logger.addHandler(console_handler)


# Function to fetch data from Bitstamp API
def fetch_bitstamp_data(
    currency_pair: str,
    start_timestamp: int,
    end_timestamp: int,
    step: int = 60,
    limit: int = 1000,
) -> List[dict]:
    url = f"https://www.bitstamp.net/api/v2/ohlc/{currency_pair}/"
    params = {
        "step": step,  # 60 seconds (1-minute interval)
        "start": start_timestamp,
        "end": end_timestamp,
        "limit": limit,  # Fetch 1000 data points max per request
    }
    try:
        logger.debug(f"Fetching data from {url} with params: {params}")
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        return response.json().get("data", {}).get("ohlc", [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data: {e}")
        return []


# Ensure at least one dataset exists
def ensure_data() -> None:
    if not os.path.exists(DAILY_DATA_PATH):
        if not os.path.exists(BULK_DATA_PATH):
            logger.error(
                f"Neither bulk dataset ({BULK_DATA_PATH}) nor daily dataset ({DAILY_DATA_PATH}) found.\n"
                f"Please ensure data is unzipped and present at {BULK_DATA_PATH} for first run."
            )
            exit(1)
        else:
            logger.info(
                f"Daily dataset ({DAILY_DATA_PATH}) not found. Assuming this is first run."
            )


# Check for missing data since the last update
def check_missing_intervals(df: pd.DataFrame) -> Tuple[int, int]:
    last_timestamp = int(df["timestamp"].max())
    logger.debug(f"Last timestamp: {last_timestamp}")

    # Round current timestamp down to the nearest minute
    current_timestamp = int(
        datetime.now(timezone.utc).replace(second=0, microsecond=0).timestamp()
    )
    # We subtract 60 seconds to avoid fetching the current minute, which is subject to change
    current_timestamp -= 60

    if last_timestamp >= current_timestamp:
        logger.info(
            f"Data is already up to date (last_timestamp: {last_timestamp}, current_timestamp: {current_timestamp})"
        )
        return None

    # Return a single interval from the last timestamp to the current timestamp
    return last_timestamp + 60, current_timestamp


# Fetch and append missing data
def fetch_and_append_missing_data(
    currency_pair: str, missing_interval: Tuple[int, int], daily_df: pd.DataFrame
) -> pd.DataFrame:
    all_new_data = []
    start_timestamp, end_timestamp = missing_interval
    logger.info(f"Missing data: from {start_timestamp} to {end_timestamp}")

    while start_timestamp < end_timestamp:
        # Calculate the number of minutes remaining
        remaining_minutes = (end_timestamp - start_timestamp) // 60
        logger.debug(f"Remaining minutes to fetch: {remaining_minutes}")

        # Because Bitstamp prioritises limit over start/end:
        # Set the limit to the minimum of 1000 or the remaining minutes
        limit = min(1000, remaining_minutes)

        if limit <= 0:
            logger.error(f"Limit is {limit}, breaking to prevent bad request")
            break

        # Calculate window end - ensure `limit` records are fetched
        window_end = min(start_timestamp + ((limit - 1) * 60), end_timestamp)

        logger.info(f"Fetching {limit} rows from {start_timestamp} to {window_end}")
        new_data = fetch_bitstamp_data(
            currency_pair, start_timestamp, window_end, limit=limit
        )

        if new_data:
            logger.debug(
                f"Retrieved {len(new_data)} records for interval {start_timestamp} to {window_end}"
            )
            df_new = pd.DataFrame(new_data)

            df_new["timestamp"] = pd.to_numeric(df_new["timestamp"], errors="coerce")
            logger.debug(
                f"Timestamp range: {df_new['timestamp'].min()} to {df_new['timestamp'].max()}"
            )

            df_new.columns = COLUMN_NAMES
            logger.debug(f"Sample data:\n{df_new.head()}\n")

            all_new_data.append(df_new)

            # Move to the next chunk using the last timestamp from the data
            last_timestamp = int(df_new["timestamp"].max())
            start_timestamp = last_timestamp + 60
            logger.debug(f"Next chunk will start at timestamp {start_timestamp}")
        else:
            logger.warning(
                f"No data retrieved for interval {start_timestamp} to {window_end}"
            )
            break

    if all_new_data:
        logger.debug(f"Merging {len(all_new_data)} intervals of new data")
        updated_daily_df = pd.concat([daily_df] + all_new_data, ignore_index=True)
        initial_rows = len(updated_daily_df)

        # Log duplicate timestamps
        duplicate_timestamps = updated_daily_df[
            updated_daily_df.duplicated(subset="timestamp", keep=False)
        ]
        if not duplicate_timestamps.empty:
            logger.info(
                f"Duplicate timestamps detected: {duplicate_timestamps['timestamp'].tolist()}"
            )

        updated_daily_df.drop_duplicates(subset="timestamp", inplace=True)
        duplicates_removed = initial_rows - len(updated_daily_df)
        if duplicates_removed > 0:
            logger.info(f"Removed {duplicates_removed} duplicate timestamps")
        updated_daily_df.sort_values(by="timestamp", ascending=True, inplace=True)
        logger.debug(f"Final dataset contains {len(updated_daily_df)} records")
        return updated_daily_df
    else:
        logger.info("No new data found to append")
        return daily_df


# Validate data integrity
def validate_data_integrity(df: pd.DataFrame) -> pd.DataFrame:
    # Remove duplicates
    initial_rows = len(df)
    df.drop_duplicates(subset="timestamp", inplace=True)
    duplicates_removed = initial_rows - len(df)
    if duplicates_removed > 0:
        logger.debug(f"Removed {duplicates_removed} duplicate timestamps")

    # Check for missing minutes
    df.sort_values(by="timestamp", ascending=True, inplace=True)
    df.reset_index(drop=True, inplace=True)
    expected_range = pd.date_range(
        start=pd.to_datetime(df["timestamp"].min(), unit="s"),
        end=pd.to_datetime(df["timestamp"].max(), unit="s"),
        freq="min",
    )
    missing_minutes = expected_range.difference(
        pd.to_datetime(df["timestamp"], unit="s")
    )
    if not missing_minutes.empty:
        missing_timestamps = missing_minutes.astype(int) // 10**9  # Convert to Unix
        logger.warning(f"Missing minutes detected: {missing_timestamps.tolist()}")
    else:
        logger.info("No missing minutes detected")

    # Check for nulls
    if df.isnull().values.any():
        logger.warning("Null values detected in the dataset")
    else:
        logger.info("No null values detected")

    return df


# Fill missing minutes using forward fill strategy
def fill_missing_minutes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop_duplicates(subset="timestamp", keep="last").copy()
    df.set_index("timestamp", inplace=True)

    # Create a complete range of timestamps
    full_range = (
        pd.date_range(
            start=pd.to_datetime(df.index.min(), unit="s"),
            end=pd.to_datetime(df.index.max(), unit="s"),
            freq="min",
        ).astype(int)
        // 10**9
    )  # Convert to seconds

    # Reindex the DataFrame to include all timestamps
    df = df.reindex(full_range)

    # Forward fill the close values
    df["close"] = df["close"].ffill()

    # Fill open, high, low with the previous close value
    df["open"] = df["open"].fillna(df["close"])
    df["high"] = df["high"].fillna(df["close"])
    df["low"] = df["low"].fillna(df["close"])

    # Fill volume with zero
    df["volume"] = df["volume"].fillna(0)

    # Reset index to have timestamp as a column again
    df.reset_index(inplace=True)
    df.rename(columns={"index": "timestamp"}, inplace=True)

    return df


# Main execution
if __name__ == "__main__":
    # Ensure data exists
    ensure_data()

    # Load datasets
    if os.path.exists(DAILY_DATA_PATH):
        df = pd.read_csv(DAILY_DATA_PATH)
        logger.info(f"Loaded daily dataset with {len(df)} records")
        daily_df = df
    else:
        df = pd.read_csv(BULK_DATA_PATH)
        logger.info(f"Loaded bulk dataset with {len(df)} records")
        daily_df = pd.DataFrame(columns=COLUMN_NAMES)  # Empty df for first run

    # Check for missing intervals
    missing_interval = check_missing_intervals(df)

    if missing_interval:
        # Fetch and append missing data
        updated_daily_df = fetch_and_append_missing_data(
            CURRENCY_PAIR, missing_interval, daily_df
        )

        # Fill missing minutes
        updated_daily_df = fill_missing_minutes(updated_daily_df)

        # Validate data integrity
        updated_daily_df = validate_data_integrity(updated_daily_df)

        # Save the updated daily dataset
        os.makedirs(os.path.dirname(DAILY_DATA_PATH), exist_ok=True)
        updated_daily_df.to_csv(DAILY_DATA_PATH, index=False)
        logger.info(
            f"Successfully saved {len(updated_daily_df)} records to {DAILY_DATA_PATH}"
        )
    else:
        logger.info("No missing data to fetch")
