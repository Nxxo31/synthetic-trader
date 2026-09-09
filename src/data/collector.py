"""Data collector for Deriv historical and live market data."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.connection.deriv_client import DerivClient

logger = logging.getLogger(__name__)


class DataCollector:
    """
    Collects historical ticks and candles from Deriv API.

    Stores data in Parquet format for efficient backtesting.
    """

    def __init__(self, client: DerivClient, data_dir: str = "data"):
        self.client = client
        self.data_dir = Path(data_dir)
        self.ticks_dir = self.data_dir / "ticks"
        self.candles_dir = self.data_dir / "candles"
        self.ticks_dir.mkdir(parents=True, exist_ok=True)
        self.candles_dir.mkdir(parents=True, exist_ok=True)

    async def download_candles(
        self,
        symbol: str,
        count: int = 5000,
        granularity: int = 60,
        batch_size: int = 1000,
    ) -> pd.DataFrame:
        """
        Download historical candles in batches.

        Args:
            symbol: Deriv symbol (e.g. "R_100", "RDBR100")
            count: total candles to download
            granularity: seconds per candle (60=1min, 300=5min, 3600=1h)
            batch_size: candles per API call (max 1000 per Deriv limits)

        Returns:
            DataFrame with columns: epoch, open, high, low, close, volume
        """
        all_candles: list[dict] = []
        remaining = count
        end_timestamp = "latest"

        logger.info("Downloading %d candles for %s (granularity=%ds)",
                     count, symbol, granularity)

        while remaining > 0:
            batch_count = min(batch_size, remaining)
            response = await self.client.ticks_history(
                symbol=symbol,
                count=batch_count,
                style="candles",
                granularity=granularity,
                end=end_timestamp if end_timestamp != "latest" else "latest",
            )

            candles = response.get("candles", [])
            if not candles:
                break

            all_candles.extend(candles)
            remaining -= len(candles)

            # Set end to oldest candle epoch for next batch
            if candles and end_timestamp == "latest":
                oldest_epoch = candles[0]["epoch"]
                end_timestamp = str(int(oldest_epoch) - 1)

            logger.info("Downloaded %d/%d candles for %s",
                         len(all_candles), count, symbol)

            # Rate limit: 1 request per second for safety
            await asyncio.sleep(1)

        df = pd.DataFrame(all_candles)
        if df.empty:
            logger.warning("No candles received for %s", symbol)
            return df

        df["epoch"] = pd.to_numeric(df["epoch"])
        df = df.sort_values("epoch").reset_index(drop=True)
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s")

        # Save to Parquet
        output_file = self.candles_dir / f"{symbol}_candles_{granularity}s.parquet"
        table = pa.Table.from_pandas(df)
        pq.write_table(table, output_file)
        logger.info("Saved %d candles to %s", len(df), output_file)

        return df

    async def download_ticks(self, symbol: str, count: int = 5000) -> pd.DataFrame:
        """
        Download historical tick data.

        Returns:
            DataFrame with columns: epoch, quote, datetime
        """
        logger.info("Downloading %d ticks for %s", count, symbol)
        response = await self.client.ticks_history(
            symbol=symbol,
            count=count,
            style="ticks",
        )

        ticks = response.get("history", {})
        if not ticks:
            return pd.DataFrame()

        df = pd.DataFrame({
            "epoch": pd.to_numeric(ticks.get("times", [])),
            "quote": pd.to_numeric(ticks.get("prices", [])),
        })
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s")
        df = df.sort_values("epoch").reset_index(drop=True)

        output_file = self.ticks_dir / f"{symbol}_ticks.parquet"
        table = pa.Table.from_pandas(df)
        pq.write_table(table, output_file)
        logger.info("Saved %d ticks to %s", len(df), output_file)

        return df

    def load_candles(self, symbol: str, granularity: int = 60) -> pd.DataFrame:
        """Load previously downloaded candles from Parquet."""
        path = self.candles_dir / f"{symbol}_candles_{granularity}s.parquet"
        if not path.exists():
            raise FileNotFoundError(f"No candle data at {path}")
        df = pq.read_table(path).to_pandas()
        return df

    def load_ticks(self, symbol: str) -> pd.DataFrame:
        """Load previously downloaded ticks from Parquet."""
        path = self.ticks_dir / f"{symbol}_ticks.parquet"
        if not path.exists():
            raise FileNotFoundError(f"No tick data at {path}")
        df = pq.read_table(path).to_pandas()
        return df
