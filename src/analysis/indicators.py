"""Technical indicators — ATR, EMA, and related volatility calculations.

These are standalone functions (not tied to any class) so they can be
reused directly in tests, strategies, and the backtest engine without
requiring a VolatilityFilter or RangeDetector instance.

ATR (Average True Range):
    TR  = max(high - low, |high - prev_close|, |low - prev_close|)
    ATR = rolling mean of TR over `period` candles (Wilder's smoothing
         optional; default uses simple rolling mean for simplicity).

EMA (Exponential Moving Average):
    EMA_t = price_t × α + EMA_{t-1} × (1 − α)
    where α = 2 / (period + 1)
    The first value is seeded with the SMA of the first `period` prices.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_atr(
    data: pd.DataFrame,
    period: int = 14,
    smoothing: str = "sma",
) -> pd.Series:
    """Compute the Average True Range (ATR) as a pandas Series.

    Args:
        data:    DataFrame with columns ``high``, ``low``, ``close``.
        period:  Look-back window (number of candles).
        smoothing: ``"sma"`` (simple rolling mean, default) or ``"wilder"``
                   (Wilder's R-like smoothing:
                   ATR_t = (ATR_{t-1} × (n-1) + TR_t) / n).

    Returns:
        Series of ATR values aligned with ``data.index``.  The first
        ``period - 1`` entries are ``NaN`` when ``smoothing="sma"``.
        Returns an empty Series if input data is insufficient.

    Raises:
        ValueError: if required columns are missing or ``period < 1``.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    required = {"high", "low", "close"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    high = data["high"].astype(float)
    low = data["low"].astype(float)
    close = data["close"].astype(float)

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    if smoothing == "wilder":
        # Wilder's smoothing: first ATR = SMA of first `period` TR values
        atr = pd.Series(np.nan, index=data.index, dtype=float)
        if len(tr) >= period:
            first_atr = tr.iloc[:period].mean()
            atr.iloc[period - 1] = first_atr
            for i in range(period, len(tr)):
                prev = atr.iloc[i - 1]
                atr.iloc[i] = (prev * (period - 1) + tr.iloc[i]) / period
        return atr

    # Default: simple rolling mean (min_periods=period for strict ATR)
    return tr.rolling(window=period, min_periods=period).mean()


def calculate_ema(
    prices: pd.Series | pd.DataFrame,
    period: int = 20,
    column: str | None = None,
) -> pd.Series:
    """Compute the Exponential Moving Average (EMA).

    The first valid ETR value is seeded with the simple moving average
    of the first ``period`` prices, then exponentially smoothed.

    Args:
        prices:  Series of prices, or a DataFrame with a price column.
        period:  EMA look-back window (must be >= 1).
        column:  If ``prices`` is a DataFrame, the column name to use.
                  Defaults to ``"close"``.

    Returns:
        Series of EMA values.  The first ``period - 1`` entries are
        ``NaN``.

    Raises:
        ValueError: if ``period < 1`` or the column is not found.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    if isinstance(prices, pd.DataFrame):
        col = column or "close"
        if col not in prices.columns:
            raise ValueError(f"Column '{col}' not found in DataFrame")
        series = prices[col].astype(float)
    else:
        series = prices.astype(float)

    if len(series) < period:
        return pd.Series(np.nan, index=series.index, dtype=float)

    alpha = 2.0 / (period + 1)

    ema = pd.Series(np.nan, index=series.index, dtype=float)
    # Seed with SMA of first `period` values
    sma_seed = series.iloc[:period].mean()
    ema.iloc[period - 1] = sma_seed

    for i in range(period, len(series)):
        prev_ema = ema.iloc[i - 1]
        ema.iloc[i] = series.iloc[i] * alpha + prev_ema * (1.0 - alpha)

    return ema


def atr_pandas(
    data: pd.DataFrame,
    period: int = 14,
) -> pd.Series:
    """Vectorised ATR using pandas ``ewm`` (alias for convenience).

    This uses pandas' built-in exponential weighted mean on TR,
    closely matching Wilder's smoothing.

    Args:
        data:   DataFrame with ``high``, ``low``, ``close``.
        period: ATR period.

    Returns:
        EWM-smoothed ATR Series.
    """
    high = data["high"].astype(float)
    low = data["low"].astype(float)
    close = data["close"].astype(float)
    prev_close = close.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    # Wilder's smoothing ≈ ewm with alpha = 1/period
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
