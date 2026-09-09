"""Controller — base class and concrete implementations for signal generation.

Controllers decide WHAT to trade based on market data.
They publish signals to the message bus for executors to act upon.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.strategies.base import Signal, SignalType, Strategy
from src.trading.message_bus import Event, EventType, get_message_bus

logger = logging.getLogger(__name__)


@dataclass
class ControllerConfig:
    """Configuration for a controller."""

    symbol: str
    strategy_name: str
    score_threshold: float = 0.35
    max_concurrent_signals: int = 1
    cooldown_seconds: float = 10.0


class BaseController(ABC):
    """Abstract base class for all controllers."""

    def __init__(
        self,
        config: ControllerConfig,
        strategy: Strategy,
        message_bus: Any | None = None,
    ):
        self.config = config
        self.strategy = strategy
        self.bus = message_bus or get_message_bus()
        self._running = False
        self._last_signal_time: float = 0.0
        self._active_signals: int = 0
        self._controller_id = f"{strategy.__class__.__name__}_{config.symbol}"

    @property
    def controller_id(self) -> str:
        """Unique identifier for this controller."""
        return self._controller_id

    @property
    def symbol(self) -> str:
        return self.config.symbol

    @property
    def strategy_name(self) -> str:
        return self.config.strategy_name

    @abstractmethod
    async def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Generate a signal from market data. Must be implemented by subclass."""
        pass

    def _check_cooldown(self) -> bool:
        """Check if cooldown period has passed."""
        import time
        elapsed = time.time() - self._last_signal_time
        return elapsed >= self.config.cooldown_seconds

    def _update_cooldown(self) -> None:
        """Update last signal time."""
        import time
        self._last_signal_time = time.time()

    def can_generate(self) -> bool:
        """Check if controller can generate a new signal."""
        return (
            self._running
            and self._active_signals < self.config.max_concurrent_signals
            and self._check_cooldown()
        )

    async def on_candle_closed(self, data: pd.DataFrame) -> Signal | None:
        """Called when a new candle closes. Generates and publishes signal if conditions met."""
        if not self.can_generate():
            return None

        signal = await self.generate_signal(data)

        if signal.type != SignalType.NO_SIGNAL:
            # Publish signal to message bus
            event = Event(
                type=EventType.SIGNAL_GENERATED,
                payload={
                    "signal": {
                        "type": signal.type.value,
                        "symbol": signal.symbol,
                        "entry_price": signal.entry_price,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "duration_seconds": signal.duration_seconds,
                        "confidence": signal.confidence,
                        "metadata": signal.metadata,
                    },
                    "controller": self.strategy_name,
                    "symbol": self.symbol,
                },
                source=self.strategy_name,
            )
            await self.bus.publish(event)

            self._active_signals += 1
            self._update_cooldown()
            logger.info(
                "%s generated signal: %s @ %.4f (conf=%.3f)",
                self.strategy_name,
                signal.type.value,
                signal.entry_price,
                signal.confidence,
            )

        return signal

    async def on_signal_filled(self, signal_data: dict) -> None:
        """Called when a signal's order is filled."""
        self._active_signals = max(0, self._active_signals - 1)

    async def start(self) -> None:
        """Start the controller."""
        self._running = True
        logger.info("Controller %s started for %s", self.strategy_name, self.symbol)

    async def stop(self) -> None:
        """Stop the controller."""
        self._running = False
        logger.info("Controller %s stopped", self.strategy_name)


class VolatilityController(BaseController):
    """Controller for VolatilityStrategyV2 — mean-reversion with regime detection."""

    def __init__(
        self,
        config: ControllerConfig,
        strategy,
        message_bus=None,
    ):
        super().__init__(config, strategy, message_bus)
        self._regime_cache: str | None = None

    async def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Generate signal using VolatilityStrategyV2."""
        if len(data) < 50:  # Minimum for vol_ratio calculation
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0,
                stop_loss=0,
                take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={"reason": "insufficient_data"},
            )

        # Generate signal via strategy
        signal = self.strategy.generate_signal(data)

        # Add controller metadata
        regime_attr = getattr(self.strategy, "_regime", None)
        if regime_attr is not None:
            try:
                self._regime_cache = regime_attr.value
                if signal.metadata is None:
                    signal.metadata = {}
                signal.metadata["regime"] = self._regime_cache
            except AttributeError:
                pass

        return signal


class StepIndexController(BaseController):
    """Controller for StepIndexStrategy — trend following + mean reversion on step indices."""

    async def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Generate signal using StepIndexStrategy."""
        if len(data) < 20:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0,
                stop_loss=0,
                take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={"reason": "insufficient_data"},
            )

        signal = self.strategy.generate_signal(data)
        return signal


class RangeBreakController(BaseController):
    """Controller for RangeBreakStrategy — channel breakout detection."""

    async def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Generate signal using RangeBreakStrategy."""
        if len(data) < 30:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0,
                stop_loss=0,
                take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={"reason": "insufficient_data"},
            )

        signal = self.strategy.generate_signal(data)
        return signal


class DriftBoomCrashController(BaseController):
    """Controller for DriftBoomCrashStrategy — drift detection on boom/crash indices."""

    async def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Generate signal using DriftBoomCrashStrategy."""
        if len(data) < 20:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0,
                stop_loss=0,
                take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={"reason": "insufficient_data"},
            )

        signal = self.strategy.generate_signal(data)
        return signal


class ConfluenceController(BaseController):
    """Controller for ConfluenceStrategy — dual confirmation (breakout + volatility)."""

    async def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Generate signal using ConfluenceStrategy."""
        if len(data) < 30:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0,
                stop_loss=0,
                take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={"reason": "insufficient_data"},
            )

        signal = self.strategy.generate_signal(data)
        return signal


class SyntheticBotController(BaseController):
    """Minimal controller for synthetic trading bot — 8-phase dev cycle implementation.

    This is a minimal controller that follows the project patterns:
    - Inherits from BaseController
    - Implements generate_signal with basic logic
    - Publishes signals to the message bus
    - Supports cooldown and concurrency control via BaseController
    """

    async def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Generate a minimal synthetic trading signal.

        This controller provides a basic signal generation approach:
        - Uses minimal data validation (requires at least 10 candles)
        - Generates a LONG signal with conservative parameters
        - Sets confidence based on data quality
        - Follows the project's Signal dataclass structure
        """
        if len(data) < 10:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0,
                stop_loss=0,
                take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={"reason": "insufficient_data"},
            )

        # Use the last close price as entry price
        entry_price = float(data["close"].iloc[-1])

        # Minimal trend detection: compare first close vs last close
        first_close = float(data["close"].iloc[0])
        last_close = entry_price
        price_change_pct = (last_close - first_close) / first_close if first_close != 0 else 0.0

        # Generate signal based on price direction
        if price_change_pct > 0.001:  # > 0.1% upward move
            signal_type = SignalType.LONG
            confidence = min(abs(price_change_pct) * 100, 1.0)
            stop_loss = entry_price * 0.99  # 1% stop loss
            take_profit = entry_price * 1.02  # 2% take profit
            duration_seconds = 3600  # 1 hour default
        elif price_change_pct < -0.001:  # > 0.1% downward move
            signal_type = SignalType.SHORT
            confidence = min(abs(price_change_pct) * 100, 1.0)
            stop_loss = entry_price * 1.01  # 1% stop loss for short
            take_profit = entry_price * 0.98  # 2% take profit for short
            duration_seconds = 3600  # 1 hour default
        else:
            signal_type = SignalType.NO_SIGNAL
            confidence = 0.0
            stop_loss = 0
            take_profit = 0
            duration_seconds = 0

        signal = Signal(
            type=signal_type,
            symbol=self.symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            duration_seconds=duration_seconds,
            confidence=confidence,
            metadata={
                "reason": "synthetic_bot_minimal",
                "price_change_pct": price_change_pct,
                "controller": "SyntheticBotController",
            },
        )

        return signal


# Controller factory
def create_controller(
    strategy_name: str,
    symbol: str,
    strategy,
    score_threshold: float = 0.35,
    message_bus=None,
) -> BaseController:
    """Factory function to create the appropriate controller for a strategy."""

    config = ControllerConfig(
        symbol=symbol,
        strategy_name=strategy_name,
        score_threshold=score_threshold,
    )

    controllers = {
        "volatility_v2": VolatilityController,
        "volatility": VolatilityController,
        "step_index": StepIndexController,
        "breakout": RangeBreakController,
        "range_break": RangeBreakController,
        "drift_boom_crash": DriftBoomCrashController,
        "confluence": ConfluenceController,
        "aggressive_breakout": RangeBreakController,
    }

    controller_class = controllers.get(strategy_name.lower(), BaseController)
    return controller_class(config, strategy, message_bus)
