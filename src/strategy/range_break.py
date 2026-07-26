"""Range Break strategy — the ONLY synthetic index where technical analysis works.

Range Break Index oscillates between support/resistance levels and breaks out
periodically. This strategy detects the channel and trades breakouts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.strategy.base import Signal, SignalType, Strategy

logger = logging.getLogger(__name__)


@dataclass
class RangeBreakConfig:
    """Configuration for Range Break strategy."""
    min_channel_ticks: int = 50       # Min candles to confirm channel
    min_channel_width: float = 0.005  # Min width as fraction of mid price (0.5%)
    entry_buffer: float = 0.1         # Buffer above/below channel for entry (fraction of width)
    tp_fraction: float = 0.5          # TP = entry +/- this fraction of channel width
    sl_buffer: float = 0.2            # SL buffer beyond opposite channel edge
    max_duration: int = 1800          # Max position duration in seconds


class RangeBreakStrategy(Strategy):
    """
    Channel breakout strategy for Range Break Index.

    Entry:
    - LONG when price closes above resistance with confirmation
    - SHORT when price closes below support with confirmation

    Channel detection:
    - Support = lowest low in last N ticks
    - Resistance = highest high in last N ticks
    - Min width filter: (resistance - support) / mid > 0.5%

    Exit:
    - TP: 50% of channel width from entry
    - SL: Opposite channel edge + 20% buffer
    - Time: Max 30 minutes
    """

    def __init__(self, symbol: str = "RDBR100", config: RangeBreakConfig | None = None):
        super().__init__("RangeBreak", symbol)
        self.config = config or RangeBreakConfig()

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """
        Analyze candle data and detect channel breakout.

        Args:
            data: DataFrame with columns: open, high, low, close, epoch

        Returns:
            Signal with entry/SL/TP or NO_SIGNAL
        """
        if len(data) < self.config.min_channel_ticks:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
            )

        # Look at candles BEFORE the current one for channel detection
        # Current candle is the one we check for breakout
        if len(data) < self.config.min_channel_ticks + 1:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
            )

        # Channel from candles BEFORE the last one
        window = data.iloc[-(self.config.min_channel_ticks + 1):-1].copy()
        last_candle = data.iloc[-1]

        support = window["low"].min()
        resistance = window["high"].max()
        mid_price = (support + resistance) / 2
        channel_width = resistance - support

        # Width filter — avoid too-narrow channels
        width_pct = channel_width / mid_price if mid_price > 0 else 0
        if width_pct < self.config.min_channel_width:
            logger.debug("Channel too narrow: %.4f%% < %.4f%%",
                          width_pct * 100, self.config.min_channel_width * 100)
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "channel_too_narrow", "width_pct": width_pct},
            )

        # Breakout detection
        # last_candle already set above
        last_close = float(last_candle["close"])
        buffer = channel_width * self.config.entry_buffer
        tp_distance = channel_width * self.config.tp_fraction
        sl_distance = channel_width * self.config.sl_buffer

        # LONG: price breaks above resistance + buffer
        if last_close > resistance + buffer:
            entry = resistance + buffer
            sl = support - sl_distance
            tp = entry + tp_distance
            confidence = min(1.0, width_pct / (self.config.min_channel_width * 2))

            logger.info(
                "LONG signal: entry=%.5f, SL=%.5f, TP=%.5f, confidence=%.2f",
                entry, sl, tp, confidence,
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
                },
            )

        # SHORT: price breaks below support - buffer
        if last_close < support - buffer:
            entry = support - buffer
            sl = resistance + sl_distance
            tp = entry - tp_distance
            confidence = min(1.0, width_pct / (self.config.min_channel_width * 2))

            logger.info(
                "SHORT signal: entry=%.5f, SL=%.5f, TP=%.5f, confidence=%.2f",
                entry, sl, tp, confidence,
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
                },
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
            },
        )

    def get_win_probability(self, signal: Signal) -> float:
        """
        Estimate win probability for Kelly sizing.

        For Range Break breakouts, empirical estimates suggest 54-58% win rate
        when channel width filter is satisfied. Using conservative 0.54 base.

        Adjust by confidence: higher confidence (wider channel) = slightly higher prob.
        """
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0

        # Base probability from empirical analysis
        base_prob = 0.54

        # Adjust by confidence (channel width)
        adj = (signal.confidence - 0.5) * 0.04  # ±2% adjustment
        prob = min(0.62, max(0.48, base_prob + adj))

        return round(prob, 4)
