"""Range Break strategy — the ONLY synthetic index where technical analysis works.

Range Break Index oscillates between support/resistance levels and breaks out
periodically. This strategy detects the channel and trades breakouts.

REFERENCE IMPLEMENTATION WITH MULTI-FACTOR SCORING:
- Uses RangeDetector, VolumeAnalyzer, VolatilityFilter for confluence scoring
- Signal confidence = multi-factor score (0-1)
- Win probability adjusted by confidence (more granular than width-based)
- Stop Loss / Take Profit based on ATR (dynamic volatility) + channel width
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.strategies.base import Signal, SignalType, Strategy
from src.analysis.range_detector import RangeDetector
from src.analysis.volume_analyzer import VolumeAnalyzer
from src.analysis.volatility_filter import VolatilityFilter
from src.analysis.signal_scorer import SignalScorer

logger = logging.getLogger(__name__)


@dataclass
class RangeBreakConfig:
    """Configuration for Range Break strategy.

    Tuned for RB100 (Range Break 100 Index) where channel widths are
    typically 0.03%-0.10% of mid price. See skill
    'algorithmic-trading-synthetic-indices' for details.
    """
    min_channel_ticks: int = 20       # Min candles to confirm channel (20 = more signals)
    min_channel_width: float = 0.0001 # Min width as fraction of mid (0.01% — RB100 channels are tight)
    entry_buffer: float = 0.0         # No buffer for RB100 (breakout itself is the signal)
    tp_fraction: float = 1.0          # TP = full channel width (ensures b >= 1, positive Kelly)
    sl_buffer: float = 0.0            # SL at opposite channel edge (no extra buffer)
    max_duration: int = 900           # Max position duration (15 min — tighter exits)
    # Multi-factor scoring weights (can be overridden)
    atr_period: int = 14              # ATR period for dynamic SL/TP
    sl_atr_multiplier: float = 1.5    # SL = entry ± (ATR * multiplier)
    tp_atr_multiplier: float = 2.0    # TP = entry ± (ATR * multiplier)


class RangeBreakStrategy(Strategy):
    """
    Channel breakout strategy for Range Break Index with multi-factor scoring.

    Entry:
    - LONG when price closes above resistance with confirmation
    - SHORT when price closes below support with confirmation

    Channel detection:
    - Support = lowest low in last N ticks
    - Resistance = highest high in last N ticks
    - Min width filter: (resistance - support) / mid > 0.5%

    Exit:
    - TP: based on ATR multiple (default: 2.0 * ATR from entry)
    - SL: based on ATR multiple (default: 1.5 * ATR from entry)
    - Time: Max 30 minutes
    """

    def __init__(
        self,
        symbol: str = "RB100",
        config: RangeBreakConfig | None = None,
        # For dependency injection in tests
        range_detector: RangeDetector | None = None,
        volume_analyzer: VolumeAnalyzer | None = None,
        volatility_filter: VolatilityFilter | None = None,
        signal_scorer: SignalScorer | None = None,
        score_threshold: float | None = None,
    ) -> None:
        super().__init__("RangeBreak", symbol)
        self.config = config or RangeBreakConfig()
        self.range_detector = range_detector or RangeDetector()
        self.volume_analyzer = volume_analyzer or VolumeAnalyzer()
        self.volatility_filter = volatility_filter or VolatilityFilter()
        self.signal_scorer = signal_scorer or SignalScorer(
            range_detector=self.range_detector,
            volume_analyzer=self.volume_analyzer,
            volatility_filter=self.volatility_filter,
        )
        # If score_threshold is provided, override the scorer's threshold
        if score_threshold is not None:
            self.signal_scorer.entry_threshold = score_threshold
        # The strategy uses the scorer's threshold to filter signals
        self.score_threshold = self.signal_scorer.entry_threshold

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """
        Analyze candle data and detect channel breakout with multi-factor scoring.

        Args:
            data: DataFrame with columns: open, high, low, close, epoch

        Returns:
            Signal with entry/SL/TP, confidence from multi-factor score
        """
        if len(data) < self.config.min_channel_ticks:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
            )

        if len(data) < self.config.min_channel_ticks + 1:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
            )

        # --- 1. Channel detection (from candles BEFORE last) ---
        window = data.iloc[-(self.config.min_channel_ticks + 1):-1].copy()
        last_candle = data.iloc[-1]

        support = float(window["low"].min())
        resistance = float(window["high"].max())
        mid_price = (support + resistance) / 2
        channel_width = resistance - support

        # Width filter
        width_pct = channel_width / mid_price if mid_price > 0 else 0
        if width_pct < self.config.min_channel_width:
            logger.debug(
                "Channel too narrow: %.4f%% < %.4f%%",
                width_pct * 100, self.config.min_channel_width * 100
            )
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "channel_too_narrow", "width_pct": width_pct},
            )

        # --- 2. Multi-factor scoring (penetration + volume + volatility) ---
        last_close = float(last_candle["close"])
        buffer = channel_width * self.config.entry_buffer

        # LONG breakout
        if last_close > resistance + buffer:
            # Penetration depth (how far beyond resistance)
            penetration = (last_close - resistance) / channel_width if channel_width > 0 else 0
            # Direction already known: LONG
            direction = "LONG"
            entry = resistance + buffer

            # --- Multi-factor score ---
            score_breakdown = self.signal_scorer.score(data)
            confidence = score_breakdown.total_score  # 0-1

            # Filter: reject signals below score threshold
            if not score_breakdown.passes_threshold:
                logger.debug(
                    "LONG breakout rejected: score=%.3f < threshold=%.2f",
                    confidence, self.score_threshold
                )
                return Signal(
                    type=SignalType.NO_SIGNAL,
                    symbol=self.symbol,
                    entry_price=last_close, stop_loss=0, take_profit=0,
                    duration_seconds=0, confidence=confidence,
                    metadata={"reason": "score_below_threshold", "score": confidence,
                              "threshold": self.score_threshold},
                )

            logger.info(
                "LONG signal breakout: penetration=%.3f, score=%.3f, conf=%.3f",
                penetration, score_breakdown.total_score, confidence
            )

            # --- SL/TP dinámicos basados en ATR ---
            atr_value = self.volatility_filter.current_atr(data)
            atr_ratio = self.volatility_filter.atr_ratio(data)
            if atr_value > 0:
                sl_distance = atr_value * self.config.sl_atr_multiplier
                tp_distance = atr_value * self.config.tp_atr_multiplier
            else:
                # Fallback a ancho de canal si ATR falla
                sl_distance = channel_width * 0.2
                tp_distance = channel_width * 1.0
                logger.warning(
                    "ATR unavailable or zero, using channel width fallback: "
                    "sl_distance=%.5f, tp_distance=%.5f", sl_distance, tp_distance
                )

            sl = entry - sl_distance  # LONG: SL below entry
            tp = entry + tp_distance  # LONG: TP above entry

            logger.info(
                "LONG signal: entry=%.5f, SL=%.5f, TP=%.5f, "
                "conf=%.3f, atr=%.5f",
                entry, sl, tp, confidence, atr_value
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
                    "support": support,
                    "resistance": resistance,
                    "channel_width": channel_width,
                    "width_pct": width_pct,
                    "penetration": penetration,
                    "atr_value": atr_value,
                    "atr_ratio": atr_ratio,
                    "score_breakdown": score_breakdown.__dict__,
                }
            )

        # SHORT breakout
        if last_close < support - buffer:
            # Penetration depth (how far below support)
            penetration = (support - last_close) / channel_width if channel_width > 0 else 0
            direction = "SHORT"
            entry = support - buffer

            # --- Multi-factor score ---
            score_breakdown = self.signal_scorer.score(data)
            confidence = score_breakdown.total_score  # 0-1

            # Filter: reject signals below score threshold
            if not score_breakdown.passes_threshold:
                logger.debug(
                    "SHORT breakout rejected: score=%.3f < threshold=%.2f",
                    confidence, self.score_threshold
                )
                return Signal(
                    type=SignalType.NO_SIGNAL,
                    symbol=self.symbol,
                    entry_price=last_close, stop_loss=0, take_profit=0,
                    duration_seconds=0, confidence=confidence,
                    metadata={"reason": "score_below_threshold", "score": confidence,
                              "threshold": self.score_threshold},
                )

            logger.info(
                "SHORT signal breakout: penetration=%.3f, score=%.3f, conf=%.3f",
                penetration, score_breakdown.total_score, confidence
            )

            # --- SL/TP dinámicos basados en ATR ---
            atr_value = self.volatility_filter.current_atr(data)
            atr_ratio = self.volatility_filter.atr_ratio(data)
            if atr_value > 0:
                sl_distance = atr_value * self.config.sl_atr_multiplier
                tp_distance = atr_value * self.config.tp_atr_multiplier
            else:
                # Fallback a ancho de canal si ATR falla
                sl_distance = channel_width * 0.2
                tp_distance = channel_width * 1.0
                logger.warning(
                    "ATR unavailable or zero, using channel width fallback: "
                    "sl_distance=%.5f, tp_distance=%.5f", sl_distance, tp_distance
                )

            sl = entry + sl_distance  # SHORT: SL above entry
            tp = entry - tp_distance  # SHORT: TP below entry

            logger.info(
                "SHORT signal: entry=%.5f, SL=%.5f, TP=%.5f, "
                "conf=%.3f, atr=%.5f",
                entry, sl, tp, confidence, atr_value
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
                    "support": support,
                    "resistance": resistance,
                    "channel_width": channel_width,
                    "width_pct": width_pct,
                    "penetration": penetration,
                    "atr_value": atr_value,
                    "atr_ratio": atr_ratio,
                    "score_breakdown": score_breakdown.__dict__,
                }
            )

        # No breakout — price still inside channel
        return Signal(
            type=SignalType.NO_SIGNAL,
            symbol=self.symbol,
            entry_price=last_close,
            stop_loss=0, take_profit=0,
            duration_seconds=0,
            confidence=0.0,
            metadata={
                "support": support,
                "resistance": resistance,
                "channel_width": channel_width,
                "width_pct": width_pct,
                "penetration": 0.0,
                "reason": "inside_channel",
            }
        )

    def get_win_probability(self, signal: Signal) -> float:
        """
        Estimate win probability for Kelly sizing.

        For Range Break breakouts, empirical base rate is 54%.
        Adjust by multi-factor confidence score (0-1) to reflect
        confluence quality: higher confidence → slightly higher win prob.

        Formula:
            win_prob = base_prob + (confidence - 0.5) * adjustment_range
        where adjustment_range is calibrated to keep win_prob in [0.48, 0.62].

        Args:
            signal: Signal with confidence from multi-factor scorer

        Returns:
            Win probability adjusted by signal confidence
        """
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0

        # Base probability from empirical analysis (conservative)
        base_prob = 0.54

        # Adjust by confidence: map [0,1] confidence → adjustment [-0.06, +0.06]
        # confidence=0.5 → ajuste 0 (win_prob = base_prob)
        # confidence=1.0 → ajuste +0.06 (win_prob = 0.60)
        # confidence=0.0 → ajuste -0.06 (win_prob = 0.48)
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.12  # ±6% adjustment range
        prob = min(0.62, max(0.48, base_prob + adj))

        logger.debug(
            "Win probability: base=%.3f, confidence=%.3f, adj=%.3f → final=%.3f",
            base_prob, confidence, adj, prob
        )
        return round(prob, 4)