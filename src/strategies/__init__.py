"""Trading strategies package."""
from src.strategies.base import Signal, SignalType, Strategy
from src.strategies.gems import GemsConfig, GemsStrategy
from src.strategies.pair_trading import PairTradingStrategy, cointegration_test
from src.strategies.range_break import RangeBreakConfig, RangeBreakStrategy
from src.strategies.volatility import VolatilityConfig, VolatilityStrategy

__all__ = [
    "Signal",
    "SignalType",
    "Strategy",
    "RangeBreakConfig",
    "RangeBreakStrategy",
    "VolatilityConfig",
    "VolatilityStrategy",
    "GemsConfig",
    "GemsStrategy",
    "PairTradingStrategy",
    "cointegration_test",
]
