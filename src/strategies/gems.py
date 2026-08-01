"""Gems strategy — spike mean-reversion for Boom/Crash synthetic indices.

Deriv's Boom and Crash indices (BOOM1000, CRASH1000, etc.) produce rare,
violent price spikes: Boom indices spike *up* sharply then revert; Crash
indices spike *down* sharply then revert. These spikes are statistical
outliers in the return distribution and are the defining characteristic
of these instruments.

The Gems strategy is a **gem hunting** approach:

1. Compute the z-score of the latest candle's return (or bar-range) relative
   to a rolling window of recent returns.
2. When |z-score| exceeds a configurable threshold (default 3.0 — a
   >3σ event), flag the candle as a spike ("gem").
3. Enter in the **opposite** direction of the spike — betting on
   mean-reversion back toward the rolling mean.

    Boom spike (price shoots up, z >> +3)  → SHORT (expect reversion down)
    Crash spike (price drops, z << -3)     → LONG  (expect reversion up)

Exit parameters:
    - SL tight: 0.5 × ATR (spikes are fast; tight stop limits damage if the
      spike continues instead of reverting).
    - TP proportional: 1.5 × ATR (favourable reward:risk = 3:1, capturing
      a portion of the reversion move).
    - Duration cap: configurable max hold (default 300s — gems are
      short-lived events).

Confidence is derived from the magnitude of the z-score: larger deviations
are rarer and higher-conviction, capped at 1.0.

Win probability base rate is 0.52 — spikes are inherently unpredictable
and mean-reversion is not guaranteed (a spike can mark the start of a
trend shift rather than a temporary dislocation), so the base rate stays
modest and is only lightly adjusted by confidence.

Implements the same interface as RangeBreakStrategy / VolatilityStrategy:
    generate_signal(data) → Signal  (with confidence from z-score)
    get_win_probability(signal) → float
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.strategies.base import Signal, SignalType, Strategy
from src.analysis.indicators import calculate_atr
from src.analysis.volatility_filter import VolatilityFilter

logger = logging.getLogger(__name__)


@dataclass
class GemsConfig:
    """Configuration for the Gems (spike mean-reversion) strategy.

    Attributes:
        z_threshold:      Minimum |z-score| to flag a spike (default 3.0,
                          i.e. a >3σ outlier). Lower → more signals but
                          more false positives.
        lookback:         Rolling window (number of candles) used to compute
                          the mean and std dev of returns for z-scoring.
        min_candles:      Minimum candles required before signalling
                          (must be >= lookback for a stable std estimate).
        max_duration:      Maximum position hold time in seconds (gems are
                          short-lived; default 300s = 5 minutes).
        sl_atr_multiplier: Stop-loss distance in ATR multiples (tight:
                          default 0.5).
        tp_atr_multiplier: Take-profit distance in ATR multiples
                          (default 1.5, giving 3:1 R:R).
        score_threshold:   Minimum confidence to emit a signal (reject
                          marginal spikes).
    """
    z_threshold: float = 3.0
    lookback: int = 50
    min_candles: int = 55
    max_duration: int = 300
    sl_atr_multiplier: float = 0.5
    tp_atr_multiplier: float = 1.5
    score_threshold: float = 0.30


class GemsStrategy(Strategy):
    """Spike mean-reversion strategy for Boom/Crash synthetic indices.

    Detects statistical outliers (z-score > threshold) in candle returns
    and enters in the opposite direction, betting on reversion toward the
    rolling mean. This is the ``gem hunting`` approach: rare violent spikes
    tend to partially revert, providing a short-lived mean-reversion edge.

    Entry:
        Boom spike  (close up sharply, z > +threshold) → SHORT
        Crash spike (close down sharply, z < -threshold) → LONG

    Exit:
        SL = 0.5 × ATR (tight — spikes are fast and dangerous)
        TP = 1.5 × ATR (proportional, 3:1 reward-to-risk)

    Confidence maps from the z-score magnitude: higher |z| → higher
    conviction, capped at 1.0.
    """

    def __init__(
        self,
        symbol: str = "BOOM1000",
        config: GemsConfig | None = None,
        volatility_filter: VolatilityFilter | None = None,
    ) -> None:
        super().__init__("Gems", symbol)
        self.config = config or GemsConfig()
        self.volatility_filter = volatility_filter or VolatilityFilter(
            atr_period=14
        )

    # ------------------------------------------------------------------ #
    #  Core helpers                                                       #
    # ------------------------------------------------------------------ #

    def _compute_z_score(self, data: pd.DataFrame) -> tuple[float, float, float]:
        """Compute the z-score of the latest candle's return.

        Uses the price return (close-to-close change) normalised by the
        rolling std dev of returns over ``lookback`` candles.

        Returns:
            (z_score, mean_return, std_return). All ``NaN`` if insufficient
            data or the std dev is zero (degenerate / flat window).
        """
        close = data["close"].astype(float)

        # Period returns (close-to-close)
        returns = close.diff()

        if len(returns) < self.config.lookback + 1:
            return (np.nan, np.nan, np.nan)

        window = returns.iloc[-(self.config.lookback + 1):-1]
        mean_ret = float(window.mean())
        std_ret = float(window.std())

        if np.isnan(std_ret) or std_ret <= 0:
            return (np.nan, mean_ret, std_ret)

        latest_ret = float(returns.iloc[-1])
        z = (latest_ret - mean_ret) / std_ret

        return (z, mean_ret, std_ret)

    def _confidence_from_zscore(self, z: float) -> float:
        """Map z-score magnitude to a confidence in [0, 1].

        Linear scaling from the threshold up to 2× the threshold:
            |z| == threshold → 0.30 (marginal gem)
            |z| == 2× threshold → 1.00 (extreme gem)
        Values beyond 2× the threshold saturate at 1.0.
        """
        threshold = self.config.z_threshold
        abs_z = abs(z)

        if abs_z < threshold:
            return 0.0

        # Linear ramp: threshold → 0.30, 2×threshold → 1.0
        upper = 2.0 * threshold
        if abs_z >= upper:
            return 1.0

        # Map [threshold, 2×threshold] → [0.30, 1.0]
        frac = (abs_z - threshold) / (upper - threshold)
        return round(0.30 + frac * 0.70, 4)

    # ------------------------------------------------------------------ #
    #  Strategy interface                                                 #
    # ------------------------------------------------------------------ #

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Analyse candle data and detect spike gems for mean-reversion.

        Args:
            data: DataFrame with columns ``high``, ``low``, ``close``.

        Returns:
            Signal with entry, SL, TP, and confidence derived from the
            z-score magnitude. ``SignalType.NO_SIGNAL`` when no spike
            detected or insufficient data.
        """
        if len(data) < self.config.min_candles:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
            )

        z, mean_ret, std_ret = self._compute_z_score(data)

        if np.isnan(z):
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "insufficient_data_for_zscore"},
            )

        last_close = float(data["close"].iloc[-1])
        atr_val = self.volatility_filter.current_atr(data)

        # --- Spike detection ---
        if abs(z) < self.config.z_threshold:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=last_close,
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={
                    "z_score": z,
                    "mean_return": mean_ret,
                    "std_return": std_ret,
                    "reason": "below_z_threshold",
                },
            )

        # We have a spike → compute confidence
        confidence = self._confidence_from_zscore(z)

        if confidence < self.config.score_threshold:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=last_close,
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=confidence,
                metadata={
                    "z_score": z,
                    "reason": "confidence_below_threshold",
                    "score": confidence,
                },
            )

        # --- SL/TP from ATR (tight for spikes) ---
        if atr_val <= 0:
            atr_series = calculate_atr(data, period=14)
            if not atr_series.empty:
                atr_val = float(atr_series.iloc[-1])
                if np.isnan(atr_val):
                    atr_val = 0.0

        if atr_val <= 0:
            logger.warning(
                "Gems: ATR unavailable, cannot compute SL/TP — skipping signal"
            )
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=last_close,
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=confidence,
                metadata={"reason": "atr_unavailable", "z_score": z},
            )

        sl_distance = atr_val * self.config.sl_atr_multiplier   # 0.5 ATR
        tp_distance = atr_val * self.config.tp_atr_multiplier   # 1.5 ATR

        # --- Direction: enter OPPOSITE to the spike (mean-reversion) ---
        if z > 0:
            # Boom spike: price shot up → expect reversion down → SHORT
            entry = last_close
            sl = entry + sl_distance   # SHORT: SL above
            tp = entry - tp_distance   # SHORT: TP below

            logger.info(
                "Gems SHORT (boom spike): z=%.2f, close=%.5f, ATR=%.5f, "
                "conf=%.3f, SL=%.5f, TP=%.5f",
                z, last_close, atr_val, confidence, sl, tp,
            )
            return Signal(
                type=SignalType.SHORT,
                symbol=self.symbol,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                duration_seconds=self.config.max_duration,
                confidence=confidence,
                metadata={
                    "strategy": "gems",
                    "spike_type": "boom",
                    "z_score": z,
                    "mean_return": mean_ret,
                    "std_return": std_ret,
                    "atr_value": atr_val,
                    "sl_atr_mult": self.config.sl_atr_multiplier,
                    "tp_atr_mult": self.config.tp_atr_multiplier,
                },
            )

        # z < 0: Crash spike — price dropped → expect reversion up → LONG
        entry = last_close
        sl = entry - sl_distance   # LONG: SL below
        tp = entry + tp_distance   # LONG: TP above

        logger.info(
            "Gems LONG (crash spike): z=%.2f, close=%.5f, ATR=%.5f, "
            "conf=%.3f, SL=%.5f, TP=%.5f",
            z, last_close, atr_val, confidence, sl, tp,
        )
        return Signal(
            type=SignalType.LONG,
            symbol=self.symbol,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            duration_seconds=self.config.max_duration,
            confidence=confidence,
            metadata={
                "strategy": "gems",
                "spike_type": "crash",
                "z_score": z,
                "mean_return": mean_ret,
                "std_return": std_ret,
                "atr_value": atr_val,
                "sl_atr_mult": self.config.sl_atr_multiplier,
                "tp_atr_mult": self.config.tp_atr_multiplier,
            },
        )

    def get_win_probability(self, signal: Signal) -> float:
        """Estimate win probability for Kelly sizing.

        Spikes are inherently unpredictable — a violent move can be the
        start of a regime shift rather than a temporary dislocation.
        The base win rate is therefore modest (0.52) and only lightly
        adjusted by confidence.

        Formula:
            win_prob = base_prob + (confidence - 0.5) * adjustment_range
        where adjustment_range is calibrated to keep win_prob in
        [0.46, 0.58].

        Args:
            signal: Signal with confidence from z-score mapping.

        Returns:
            Win probability adjusted by signal confidence.
        """
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0

        base_prob = 0.52
        confidence = max(0.0, min(1.0, signal.confidence))
        # ±6% adjustment range — modest, reflecting spike unpredictability
        adj = (confidence - 0.5) * 0.12
        prob = min(0.58, max(0.46, base_prob + adj))

        return round(prob, 4)
