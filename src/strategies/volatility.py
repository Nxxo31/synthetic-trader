"""Volatility strategy — ATR-band mean-reversion for synthetic indices.

Unlike the Range Break strategy (which trades breakouts), the Volatility
strategy uses **ATR bands** (Bollinger-like) to trade mean reversion:

    upper_band = EMA + k × ATR
    lower_band = EMA - k × ATR

Entry signals:
    LONG  when close pushes below the lower band (oversold → expect bounce)
    SHORT when close pushes above the upper band (overbought → expect reversion)

The signal score uses a combination of:
    - **Band deviation**: how far the price has penetrated past the band
      (deeper penetration → higher conviction reversion signal).
    - **ATR expansion/contraction**: volatile regimes (high ATR ratio)
      produce stronger mean-reversion edges.

Implements the same interface as RangeBreakStrategy:
    generate_signal(data) → Signal  (with confidence = multi-factor score)
    get_win_probability(signal) → float
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.strategies.base import Signal, SignalType, Strategy
from src.analysis.indicators import calculate_atr, calculate_ema
from src.analysis.volatility_filter import VolatilityFilter

logger = logging.getLogger(__name__)


@dataclass
class VolatilityConfig:
    """Configuration for the Volatility (ATR-band) strategy.

    Attributes:
        atr_period:        ATR look-back window (default 14).
        ema_period:        EMA period for the band midline (default 20).
        band_multiplier:   Number of ATRs above/below EMA for the bands
                           (default 2.0, typical for Bollinger-like width).
        min_candles:       Minimum number of candles required before
                           generating a signal (avoids noise on warmup).
        max_duration:      Maximum position hold time in seconds.
        sl_atr_multiplier: Stop-loss distance in ATR multiples.
        tp_atr_multiplier: Take-profit distance in ATR multiples.
        score_threshold:   Minimum composite score to emit a signal.
    """
    atr_period: int = 14
    ema_period: int = 20
    band_multiplier: float = 2.0
    min_candles: int = 30
    max_duration: int = 600
    sl_atr_multiplier: float = 1.5
    tp_atr_multiplier: float = 2.0
    score_threshold: float = 0.20


class VolatilityStrategy(Strategy):
    """ATR-band mean-reversion strategy.

    Generates LONG signals when price falls below the lower ATR band
    and SHORT signals when price rises above the upper ATR band, betting
    on reversion toward the EMA midline.
    """

    def __init__(
        self,
        symbol: str = "R_100",
        config: VolatilityConfig | None = None,
        volatility_filter: VolatilityFilter | None = None,
    ) -> None:
        super().__init__("Volatility", symbol)
        self.config = config or VolatilityConfig()
        self.volatility_filter = volatility_filter or VolatilityFilter(
            atr_period=self.config.atr_period
        )

    # ------------------------------------------------------------------ #
    #  Core signal generation                                             #
    # ------------------------------------------------------------------ #

    def _compute_bands(self, data: pd.DataFrame) -> tuple[float, float, float, float]:
        """Compute EMA midline and ATR bands.

        Returns (midline, upper_band, lower_band, atr_value).
        All values are ``NaN`` if insufficient data.
        """
        close = data["close"].astype(float)
        ema = calculate_ema(close, period=self.config.ema_period)
        atr = calculate_atr(data, period=self.config.atr_period)

        if ema.isna().iloc[-1] or atr.isna().iloc[-1]:
            return (np.nan, np.nan, np.nan, np.nan)

        mid = float(ema.iloc[-1])           # type: ignore[union-attr]
        atr_val = float(atr.iloc[-1])       # type: ignore[union-attr]
        upper = mid + self.config.band_multiplier * atr_val
        lower = mid - self.config.band_multiplier * atr_val
        return (mid, upper, lower, atr_val)

    def _band_deviation_score(self, close: float, mid: float, atr_val: float) -> tuple[float, float]:
        """Calculate deviation of close from the band, normalised by ATR.

        Returns (direction_score, distance_in_atr).
        A larger distance from the midline (in ATR units) → stronger signal.

        - distance > 1.5 ATR  → score >= 0.15 (strong)
        - distance 0.5–1.5    → proportional [0, 0.15]
        - distance < 0.5 ATR   → 0 (price not far enough)
        """
        if atr_val <= 0:
            return (0.0, 0.0)

        distance_atr = abs(close - mid) / atr_val

        # Map distance (in ATR units) → score in [0, 0.25]
        # Band is at `band_multiplier` ATR. When price is exactly at the band,
        # distance ≈ band_multiplier (e.g. 2.0). We start scoring at distance >= 0.5.
        if distance_atr <= 0.5:
            score = 0.0
        elif distance_atr >= 1.5:
            # Saturate at 0.25 beyond 1.5 ATR
            score = 0.25
        else:
            # Linear: 0.5 → 0, 1.5 → 0.25
            score = (distance_atr - 0.5) * 0.25

        return (round(min(0.25, score), 4), distance_atr)

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Analyse candle data and produce an ATR-band mean-reversion signal.

        Args:
            data: DataFrame with columns ``high``, ``low``, ``close``.

        Returns:
            Signal with entry, SL, TP, and confidence (multi-factor score).
            ``SignalType.NO_SIGNAL`` when no band penetration or insufficient data.
        """
        if len(data) < self.config.min_candles:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
            )

        mid, upper, lower, atr_val = self._compute_bands(data)

        if np.isnan(mid) or np.isnan(atr_val) or atr_val <= 0:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "insufficient_data_for_bands"},
            )

        last_close = float(data["close"].iloc[-1])

        # Volatility score from VolatilityFilter (ATR ratio expansion)
        atr_score = float(self.volatility_filter.volatility_score(data))

        if last_close < lower:
            # Price below the lower band → LONG (expect bounce back to EMA)
            dev_score, dist_atr = self._band_deviation_score(last_close, mid, atr_val)
            score = round(dev_score + atr_score, 4)
            score = min(1.0, max(0.0, score))

            if score < self.config.score_threshold:
                return Signal(
                    type=SignalType.NO_SIGNAL,
                    symbol=self.symbol,
                    entry_price=last_close,
                    stop_loss=0, take_profit=0,
                    duration_seconds=0,
                    confidence=score,
                    metadata={"reason": "score_below_threshold", "score": score},
                )

            sl_distance = atr_val * self.config.sl_atr_multiplier
            tp_distance = atr_val * self.config.tp_atr_multiplier
            entry = last_close
            sl = entry - sl_distance
            tp = entry + tp_distance

            logger.info(
                "Volatility LONG: close=%.5f below lower=%.5f (ATR=%.5f, score=%.3f)",
                last_close, lower, atr_val, score,
            )
            return Signal(
                type=SignalType.LONG,
                symbol=self.symbol,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                duration_seconds=self.config.max_duration,
                confidence=score,
                metadata={
                    "strategy": "volatility",
                    "upper_band": upper,
                    "lower_band": lower,
                    "ema_midline": mid,
                    "atr_value": atr_val,
                    "distance_atr": dist_atr,
                    "deviation_score": dev_score,
                    "atr_score": atr_score,
                },
            )

        if last_close > upper:
            # Price above the upper band → SHORT (expect reversion to EMA)
            dev_score, dist_atr = self._band_deviation_score(last_close, mid, atr_val)
            score = round(dev_score + atr_score, 4)
            score = min(1.0, max(0.0, score))

            if score < self.config.score_threshold:
                return Signal(
                    type=SignalType.NO_SIGNAL,
                    symbol=self.symbol,
                    entry_price=last_close,
                    stop_loss=0, take_profit=0,
                    duration_seconds=0,
                    confidence=score,
                    metadata={"reason": "score_below_threshold", "score": score},
                )

            sl_distance = atr_val * self.config.sl_atr_multiplier
            tp_distance = atr_val * self.config.tp_atr_multiplier
            entry = last_close
            sl = entry + sl_distance   # SHORT: SL above
            tp = entry - tp_distance   # SHORT: TP below

            logger.info(
                "Volatility SHORT: close=%.5f above upper=%.5f (ATR=%.5f, score=%.3f)",
                last_close, upper, atr_val, score,
            )
            return Signal(
                type=SignalType.SHORT,
                symbol=self.symbol,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                duration_seconds=self.config.max_duration,
                confidence=score,
                metadata={
                    "strategy": "volatility",
                    "upper_band": upper,
                    "lower_band": lower,
                    "ema_midline": mid,
                    "atr_value": atr_val,
                    "distance_atr": dist_atr,
                    "deviation_score": dev_score,
                    "atr_score": atr_score,
                },
            )

        # Price within bands — no signal
        return Signal(
            type=SignalType.NO_SIGNAL,
            symbol=self.symbol,
            entry_price=last_close,
            stop_loss=0, take_profit=0,
            duration_seconds=0,
            confidence=0.0,
            metadata={
                "upper_band": upper,
                "lower_band": lower,
                "ema_midline": mid,
                "atr_value": atr_val,
                "reason": "within_bands",
            },
        )

    def get_win_probability(self, signal: Signal) -> float:
        """Estimate win probability for Kelly sizing.

        Mean-reversion strategies have a higher base win rate than
        breakout strategies (price tends to revert more often than not),
        but lower payoff per trade.

        Base rate: 58%, adjusted ±6% by confidence.
        """
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0

        base_prob = 0.58
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.12
        prob = min(0.66, max(0.50, base_prob + adj))

        return round(prob, 4)
