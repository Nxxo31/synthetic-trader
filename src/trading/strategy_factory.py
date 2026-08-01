"""Strategy factory — registry-based strategy instantiation.

Provides a central registry that maps strategy **names** (strings) to
their implementing **classes**, so callers can request a strategy by
name without importing the concrete class.  This is the recommended
extension point: adding a new strategy only requires registering it in
the ``STRATEGY_REGISTRY`` dict (or calling :func:`register_strategy``).

Registered strategies:
    - ``"breakout"`` / ``"range_break"`` → :class:`RangeBreakStrategy`
    - ``"volatility"``                 → :class:`VolatilityStrategy`
    - ``"confluence"``                 → :class:`ConfluenceStrategy`
    - ``"gems"``                       → :class:`GemsStrategy`

Usage::

    from src.trading.strategy_factory import create_strategy

    strategy = create_strategy("volatility", symbol="R_100")
    signal = strategy.generate_signal(candle_df)
"""
from __future__ import annotations

import logging
from typing import Callable

from src.strategies.base import Signal, SignalType, Strategy
from src.strategies.range_break import RangeBreakConfig, RangeBreakStrategy
from src.strategies.volatility import VolatilityConfig, VolatilityStrategy
from src.strategies.gems import GemsConfig, GemsStrategy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Confluence strategy — combines Breakout + Volatility for confluence entry
# ---------------------------------------------------------------------------


class ConfluenceStrategy(Strategy):
    """Confluence strategy — requires both breakout AND volatility agreement.

    A signal is only emitted when BOTH sub-strategies agree on direction:
    - Breakout says LONG and Volatility says LONG → strong LONG
    - Breakout says SHORT and Volatility says SHORT → strong SHORT
    - Otherwise → NO_SIGNAL (no confluence)

    The confidence score is the **product** of the two sub-strategies'
    confidences, ensuring only high-conviction dual-confirmation signals
    pass (higher bar than either strategy alone).
    """

    def __init__(
        self,
        symbol: str = "RB100",
        breakout: RangeBreakStrategy | None = None,
        volatility: VolatilityStrategy | None = None,
    ) -> None:
        super().__init__("Confluence", symbol)
        self.breakout = breakout or RangeBreakStrategy(symbol=symbol)
        self.volatility = volatility or VolatilityStrategy(symbol=symbol)

    def generate_signal(self, data) -> Signal:  # type: ignore[override]
        """Generate a signal only when both strategies agree."""
        if len(data) < 30:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
            )

        sig_b = self.breakout.generate_signal(data)
        sig_v = self.volatility.generate_signal(data)

        if sig_b.type == SignalType.NO_SIGNAL or sig_v.type == SignalType.NO_SIGNAL:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=sig_b.entry_price or sig_v.entry_price,
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={
                    "breakout_type": sig_b.type.value,
                    "volatility_type": sig_v.type.value,
                    "reason": "no_confluence",
                },
            )

        if sig_b.type != sig_v.type:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=sig_b.entry_price,
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={
                    "breakout_type": sig_b.type.value,
                    "volatility_type": sig_v.type.value,
                    "reason": "directional_disagreement",
                },
            )

        # Both agree — combine confidence (product for stricter entry)
        combined_conf = sig_b.confidence * sig_v.confidence
        entry = sig_b.entry_price
        sl = sig_b.stop_loss
        tp = sig_b.take_profit

        logger.info(
            "Confluence %s: breakout_conf=%.3f × vol_conf=%.3f = %.3f",
            sig_b.type.value, sig_b.confidence, sig_v.confidence, combined_conf,
        )

        return Signal(
            type=sig_b.type,
            symbol=self.symbol,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            duration_seconds=sig_b.duration_seconds,
            confidence=combined_conf,
            metadata={
                "breakout_confidence": sig_b.confidence,
                "volatility_confidence": sig_v.confidence,
                "confluence": True,
            },
        )

    def get_win_probability(self, signal: Signal) -> float:
        """Confluence signals are higher quality → slight bump in base rate."""
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0
        base_prob = 0.60  # Higher base than individual strategies
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.10
        prob = min(0.68, max(0.52, base_prob + adj))
        return round(prob, 4)


# ---------------------------------------------------------------------------
#  Registry & factory
# ---------------------------------------------------------------------------

# Type alias for strategy constructor
StrategyConstructor = Callable[..., Strategy]

# Master registry: canonical name → class
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "breakout": RangeBreakStrategy,
    "range_break": RangeBreakStrategy,
    "volatility": VolatilityStrategy,
    "confluence": ConfluenceStrategy,
    "gems": GemsStrategy,
}

# Default config class per strategy type
_CONFIG_REGISTRY: dict[str, type] = {
    "breakout": RangeBreakConfig,
    "range_break": RangeBreakConfig,
    "volatility": VolatilityConfig,
    "confluence": type(None),  # Confluence uses sub-strategy defaults
    "gems": GemsConfig,
}


def register_strategy(name: str, cls: type[Strategy]) -> None:
    """Register a new strategy under a given name.

    Allows external code to add strategies without modifying this module.

    Args:
        name: Canonical name (case-insensitive on lookup).
        cls:  Strategy subclass.
    """
    STRATEGY_REGISTRY[name.lower()] = cls
    logger.info("Registered strategy '%s' → %s", name, cls.__name__)


def create_strategy(
    name: str,
    symbol: str = "RB100",
    **kwargs,
) -> Strategy:
    """Factory: create a strategy instance by name.

    Args:
        name:    Strategy name (case-insensitive). Must be in the registry.
                 Accepts "breakout", "range_break", "volatility", "confluence", "gems".
        symbol:  Trading symbol to pass to the strategy.
        **kwargs: Extra arguments forwarded to the strategy constructor.

    Returns:
        An instantiated Strategy subclass.

    Raises:
        ValueError: if the strategy name is not registered.
    """
    key = name.lower().strip()
    if key not in STRATEGY_REGISTRY:
        available = ", ".join(sorted(STRATEGY_REGISTRY.keys()))
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {available}"
        )

    cls = STRATEGY_REGISTRY[key]
    logger.debug("Creating strategy '%s' → %s (symbol=%s)", name, cls.__name__, symbol)

    # ConfluenceStrategy needs sub-strategy instances, not raw config
    if key == "confluence":
        return cls(symbol=symbol, **kwargs)

    return cls(symbol=symbol, **kwargs)


def list_strategies() -> list[str]:
    """Return canonical strategy names (one per distinct class, no aliases)."""
    seen_classes: set[int] = set()
    result: list[str] = []
    for name, cls in STRATEGY_REGISTRY.items():
        if id(cls) not in seen_classes:
            result.append(name)
            seen_classes.add(id(cls))
    return result


def available_strategies() -> dict[str, str]:
    """Return mapping of strategy names to their class names."""
    seen_classes: set[str] = set()
    mapping: dict[str, str] = {}
    for name, cls in STRATEGY_REGISTRY.items():
        cls_name = cls.__name__
        if cls_name not in seen_classes:
            mapping[name] = cls_name
            seen_classes.add(cls_name)
    return mapping
