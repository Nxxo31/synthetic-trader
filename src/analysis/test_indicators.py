"""Unit tests for analysis/indicators.py — ATR and EMA calculations.

Tests verify REAL mathematical behaviour with known datasets, not tautologies.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.indicators import calculate_atr, calculate_ema, atr_pandas


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------


def _make_flat_ohlc(n: int = 30, price: float = 100.0) -> pd.DataFrame:
    """Create a flat OHLC dataset where all candles are identical.

    ATR of a flat dataset must be exactly 0.
    """
    idx = pd.date_range("2025-01-01", periods=n, freq="1min")
    return pd.DataFrame(
        {
            "open": price,
            "high": price,
            "low": price,
            "close": price,
        },
        index=idx,
    )


def _make_trending_ohlc(n: int = 40, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """Create an OHLC dataset where close increases by `step` each candle.

    high = close + 0.5, low = close - 0.5 → constant range.
    """
    closes = [start + i * step for i in range(n)]
    idx = pd.date_range("2025-01-01", periods=n, freq="1min")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
#  ATR tests
# ---------------------------------------------------------------------------


class TestATRCalculation:
    """Tests for calculate_atr()."""

    def test_atr_flat_dataset_is_zero(self):
        """Flat OHLC (high == low == close) → TR = 0 → ATR = 0."""
        data = _make_flat_ohlc(50)
        atr = calculate_atr(data, period=14)
        # The first 13 values are NaN (min_periods=14), rest are 0
        assert atr.iloc[:13].isna().all(), "First period-1 values should be NaN"
        assert atr.iloc[13:].sum() == 0, "ATR of flat data should be 0"

    def test_atr_constant_range_known_value(self):
        """If every candle has range = 1.0 and no gaps, ATR = 1.0."""
        n = 30
        closes = [100.0 + i for i in range(n)]  # rising, no gaps
        idx = pd.date_range("2025-01-01", periods=n, freq="1min")
        data = pd.DataFrame(
            {
                "high": [c + 0.5 for c in closes],
                "low": [c - 0.5 for c in closes],
                "close": closes,
            },
            index=idx,
        )
        # Each candle: high - low = 1.0
        # |high - prev_close| = |c+0.5 - c_prev| = |c+0.5 - (c-1)| = 1.5
        # |low - prev_close|  = |c-0.5 - c_prev| = |c-0.5 - (c-1)| = 0.5
        # TR = max(1.0, 1.5, 0.5) = 1.5
        atr = calculate_atr(data, period=14)
        assert not atr.iloc[13:].isna().any(), "ATR should be defined from index 13"
        # For the last candle, TR = max(1.0, 1.5, 0.5) = 1.5
        # But ATR is the average of TR over 14 candles, and TR is constant at 1.5
        # (after the first candle where prev_close exists)
        last_atr = atr.iloc[-1]
        assert abs(last_atr - 1.5) < 0.001, f"Expected ATR ≈ 1.5, got {last_atr}"

    def test_atr_period_length(self):
        """ATR with period N should have first N-1 values as NaN."""
        data = _make_trending_ohlc(50)
        period = 14
        atr = calculate_atr(data, period=period)
        assert atr.iloc[:period - 1].isna().all()
        assert not atr.iloc[period - 1:].isna().any()

    def test_atr_volatile_vs_calm(self):
        """Dataset with larger ranges must have higher ATR than calm dataset."""
        calm = _make_trending_ohlc(50, step=0.1)    # range = 1.0
        volatile = _make_trending_ohlc(50, step=0.1)
        # Make volatile dataset have 5x larger candle ranges
        volatile["high"] = volatile["high"] - 0.5 + 5.0
        volatile["low"] = volatile["low"] - 0.5

        atr_calm = calculate_atr(calm, period=14).iloc[-1]
        atr_volatile = calculate_atr(volatile, period=14).iloc[-1]
        assert atr_volatile > atr_calm, (
            f"Volatile ATR ({atr_volatile}) should exceed calm ATR ({atr_calm})"
        )
        assert atr_volatile > 4.0, f"Volatile ATR should be > 4.0, got {atr_volatile}"

    def test_atr_wilder_smoothing(self):
        """Wilder's ATR should produce a value close to SMA ATR for constant data."""
        data = _make_trending_ohlc(50)
        period = 14

        atr_sma = calculate_atr(data, period=period, smoothing="sma")
        atr_wilder = calculate_atr(data, period=period, smoothing="wilder")

        # Both should converge for later values (same numeric series)
        # At the end, Wilder and SMA should be close (both ≈ 1.5 for this dataset)
        # Wilder changes slower due to momentum, so values differ slightly
        assert not np.isnan(atr_wilder.iloc[-1])
        assert abs(atr_wilder.iloc[-1] - atr_sma.iloc[-1]) < 1.0, (
            f"Wilder ({atr_wilder.iloc[-1]}) should be close to SMA ({atr_sma.iloc[-1]})"
        )

    def test_atr_missing_columns_raises(self):
        """Should raise ValueError if high/low/close missing."""
        bad_data = pd.DataFrame({"open": [1, 2], "close": [1, 2]})
        with pytest.raises(ValueError, match="Missing required columns"):
            calculate_atr(bad_data, period=14)

    def test_atr_invalid_period_raises(self):
        """period < 1 should raise."""
        data = _make_flat_ohlc(10)
        with pytest.raises(ValueError, match="period must be >= 1"):
            calculate_atr(data, period=0)

    def test_atr_pandas_ewm_matches_manual_wilder(self):
        """atr_pandas (ewm-based) should be in same ballpark as manual Wilder ATR."""
        data = _make_trending_ohlc(50)
        period = 14
        atr_wilder = calculate_atr(data, period=period, smoothing="wilder").iloc[-1]
        atr_ewm = atr_pandas(data, period=period).iloc[-1]
        # Both use alpha = 1/period, so should be very close
        assert abs(atr_wilder - atr_ewm) < 0.5, (
            f"Wilder ({atr_wilder}) ≈ EWM ({atr_ewm})"
        )


# ---------------------------------------------------------------------------
#  EMA tests
# ---------------------------------------------------------------------------


class TestEMACalculation:
    """Tests for calculate_ema()."""

    def test_ema_constant_series_equals_constant(self):
        """EMA of a constant series must equal that constant."""
        prices = pd.Series([50.0] * 30)
        ema = calculate_ema(prices, period=10)
        assert not ema.iloc[9:].isna().any()
        assert abs(ema.iloc[-1] - 50.0) < 1e-10

    def test_ema_seeded_with_sma(self):
        """First valid EMA value must equal the SMA of the first `period` prices."""
        prices = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
        period = 3
        ema = calculate_ema(prices, period=period)
        # First valid value at index 2 = SMA([10, 20, 30]) = 20
        assert not np.isnan(ema.iloc[period - 1])
        expected_sma = prices.iloc[:period].mean()
        assert abs(ema.iloc[period - 1] - expected_sma) < 1e-10

    def test_ema_formula_correctness(self):
        """Second EMA value must follow EMA = price×α + prev_EMA×(1-α)."""
        prices = pd.Series([10.0, 20.0, 30.0, 40.0])
        period = 2
        alpha = 2.0 / (period + 1)  # = 2/3

        ema = calculate_ema(prices, period=period)
        # Seed at index 1 = SMA([10, 20]) = 15
        seed = prices.iloc[:2].mean()
        assert abs(ema.iloc[1] - seed) < 1e-10

        # At index 2: EMA = 30 × (2/3) + 15 × (1/3) = 20 + 5 = 25
        expected = prices.iloc[2] * alpha + seed * (1 - alpha)
        assert abs(ema.iloc[2] - expected) < 1e-10

    def test_ema_lags_price(self):
        """EMA should lag behind a sharply rising price (not equal last price)."""
        prices = pd.Series([10.0] * 10 + [100.0] * 10)
        ema = calculate_ema(prices, period=5)
        last_ema = ema.iloc[-1]
        # EMA after 10 candles at 100 should be approaching 100 but still < 100
        assert last_ema < 100.0, "EMA should lag below the new price level"
        assert last_ema > 10.0, "EMA should have moved up from the old level"

    def test_ema_responds_faster_with_smaller_period(self):
        """Shorter EMA period should react faster to price change."""
        prices = pd.Series([10.0] * 20 + [50.0] * 20)
        ema_short = calculate_ema(prices, period=3).iloc[-1]
        ema_long = calculate_ema(prices, period=15).iloc[-1]
        # Shorter period reacts faster → closer to 50
        assert ema_short > ema_long, (
            f"EMA(3)={ema_short} should be > EMA(15)={ema_long} after price jump"
        )

    def test_ema_from_dataframe(self):
        """calculate_ema should accept a DataFrame and use the close column."""
        idx = pd.date_range("2025-01-01", periods=10, freq="1min")
        df = pd.DataFrame({"close": [10.0] * 10, "open": [9.0] * 10}, index=idx)
        ema_df = calculate_ema(df, period=5)
        ema_series = calculate_ema(df["close"], period=5)
        assert abs(ema_df.iloc[-1] - ema_series.iloc[-1]) < 1e-10

    def test_ema_invalid_period_raises(self):
        with pytest.raises(ValueError, match="period must be >= 1"):
            calculate_ema(pd.Series([1, 2, 3]), period=0)
