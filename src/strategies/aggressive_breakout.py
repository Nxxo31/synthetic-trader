"""
Aggressive Breakout strategy — modified Range Break with lower thresholds
for higher trade frequency while maintaining risk controls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from src.analysis.range_detector import RangeDetector
from src.analysis.signal_scorer import SignalScorer
from src.analysis.volatility_filter import VolatilityFilter
from src.analysis.volume_analyzer import VolumeAnalyzer
from src.strategies.base import Signal, SignalType, Strategy

logger = logging.getLogger(__name__)


@dataclass
class AggressiveBreakoutConfig:
    """Configuration for Aggressive Breakout strategy.

    Tuned for higher frequency trading on RB100 while maintaining
    risk controls through dynamic position sizing.
    """
    # Channel detection - more sensitive
    min_channel_ticks: int = 10          # Reduced from 20 for faster signals
    min_channel_width: float = 0.00005   # Reduced from 0.0001 for tighter channels
    entry_buffer: float = 0.0            # No buffer for breakout itself
    tp_fraction: float = 1.0             # TP = full channel width
    sl_buffer: float = 0.0               # SL at opposite channel edge
    max_duration: int = 600              # Reduced to 10 min for faster turnover

    # Multi-factor scoring - more permissive
    atr_period: int = 14
    sl_atr_multiplier: float = 1.0       # Tighter stops
    tp_atr_multiplier: float = 1.5       # Tighter targets
    entry_threshold: float = 0.4         # Reduced from 0.6 for more signals


class AggressiveBreakoutStrategy(Strategy):
    """
    Aggressive Channel breakout strategy for Range Break Index.
    
    Generates more signals by lowering thresholds while maintaining
    risk controls through ATR-based stops and Kelly sizing.
    """

    def __init__(
        self,
        symbol: str = "RB100",
        config: AggressiveBreakoutConfig | None = None,
        range_detector: RangeDetector | None = None,
        volume_analyzer: VolumeAnalyzer | None = None,
        volatility_filter: VolatilityFilter | None = None,
        signal_scorer: SignalScorer | None = None,
    ) -> None:
        super().__init__("AggressiveBreakout", symbol)
        self.config = config or AggressiveBreakoutConfig()
        self.range_detector = range_detector or RangeDetector()
        self.volume_analyzer = volume_analyzer or VolumeAnalyzer()
        self.volatility_filter = volatility_filter or VolatilityFilter()
        self.signal_scorer = signal_scorer or SignalScorer(
            range_detector=self.range_detector,
            volume_analyzer=self.volume_analyzer,
            volatility_filter=self.volatility_filter,
            entry_threshold=self.config.entry_threshold,
        )

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Analyze candle data and detect channel breakout with aggressive scoring."""
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

        # --- 2. Multi-factor scoring ---
        last_close = float(last_candle["close"])
        buffer = channel_width * self.config.entry_buffer

        # LONG breakout
        if last_close > resistance + buffer:
            penetration = (last_close - resistance) / channel_width if channel_width > 0 else 0
            direction = "LONG"
            entry = resistance + buffer

            # --- Multi-factor score ---
            score_breakdown = self.signal_scorer.score(data)
            confidence = score_breakdown.total_score  # 0-1

            # Filter: reject signals below score threshold
            if not score_breakdown.passes_threshold:
                logger.debug(
                    "LONG breakout rejected: score=%.3f < threshold=%.2f",
                    confidence, self.config.entry_threshold
                )
                return Signal(
                    type=SignalType.NO_SIGNAL,
                    symbol=self.symbol,
                    entry_price=last_close, stop_loss=0, take_profit=0,
                    duration_seconds=0, confidence=confidence,
                    metadata={"reason": "score_below_threshold", "score": confidence,
                              "threshold": self.config.entry_threshold},
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
                sl_distance = channel_width * 0.15  # Tighter fallback
                tp_distance = channel_width * 0.8
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
                    confidence, self.config.entry_threshold
                )
                return Signal(
                    type=SignalType.NO_SIGNAL,
                    symbol=self.symbol,
                    entry_price=last_close, stop_loss=0, take_profit=0,
                    duration_seconds=0, confidence=confidence,
                    metadata={"reason": "score_below_threshold", "score": confidence,
                              "threshold": self.config.entry_threshold},
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
                sl_distance = channel_width * 0.15  # Tighter fallback
                tp_distance = channel_width * 0.8
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
        """Estimate win probability for Kelly sizing."""
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0

        # Base probability from empirical analysis (conservative)
        base_prob = 0.52  # Slightly lower base for more signals

        # Adjust by confidence: map [0,1] confidence → adjustment [-0.04, +0.04]
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.08  # ±4% adjustment range
        prob = min(0.58, max(0.46, base_prob + adj))

        logger.debug(
            "Win probability: base=%.3f, confidence=%.3f, adj=%.3f → final=%.3f",
            base_prob, confidence, adj, prob
        )
        return round(prob, 4)
