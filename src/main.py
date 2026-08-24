"""Synthetic Trader — Main entry point for trading.

Usage (from project root):
    .venv/bin/python -m src.trading.engine [--symbol SYMBOL] [--strategy STRATEGY]

This is the SINGLE entry point for all trading modes.
The old `src.main` with paper/api/backtest modes is deprecated;
use `src.trading.engine` which encompasses all functionality.
"""

from __future__ import annotations

import asyncio
import sys
import argparse
import logging
from pathlib import Path

# Ensure src is on the path
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.trading.engine import TradingEngine, EngineConfig
from src.trading.config import get_config

logger = logging.getLogger("synthetic-trader")


def main():
    parser = argparse.ArgumentParser(
        description="Synthetic Trader - Algorithmic trading bot for Deriv synthetic indices"
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Deriv synthetic index symbol (default: from config)",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Trading strategy to use (default: from config)",
    )
    parser.add_argument(
        "--executor",
        choices=["paper", "deriv"],
        default="paper",
        help="Executor type: paper (simulated) or deriv (live) (default: paper)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        default=False,
        help="Enable real-time dashboard WebSocket streaming",
    )

    args = parser.parse_args()

    # Get centralized ConfigManager
    config_mgr = get_config()

    # Build EngineConfig from ConfigManager + CLI overrides
    symbol = args.symbol or config_mgr.get_enabled_symbols()[0] if config_mgr.get_enabled_symbols() else "stpRNG"
    strategy_name = args.strategy or "step_index"
    executor_type = args.executor

    engine_config = EngineConfig(
        symbol=symbol,
        strategy_name=strategy_name,
        executor_type=executor_type,
        max_risk_per_trade=config_mgr.risk.max_risk_per_trade,
        max_daily_drawdown=config_mgr.risk.max_daily_drawdown,
        max_trades_per_day=config_mgr.risk.max_trades_per_day,
        circuit_breaker_losses=config_mgr.risk.circuit_breaker_losses,
        initial_capital=config_mgr.risk.initial_capital,
        tradable_balance_ratio=config_mgr.risk.tradable_balance_ratio,
    )

    logger.info(
        "Synthetic Trader starting: symbol=%s, strategy=%s, executor=%s",
        engine_config.symbol,
        engine_config.strategy_name,
        engine_config.executor_type,
    )

    # Create and initialize the trading engine
    engine = TradingEngine(config=engine_config)

    # Start trading
    asyncio.create_task(engine.start_trading(), name="trading_loop")

    # Keep running - engine handles its own lifecycle
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
        asyncio.create_task(engine.stop(), name="shutdown")


if __name__ == "__main__":
    main()