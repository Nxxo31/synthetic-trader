from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure src is on the path
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.trading.config import get_config
from src.trading.engine import EngineConfig, TradingEngine

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

    # Run the trading engine
    asyncio.run(run_engine(engine, args.watch))


async def run_engine(engine: TradingEngine, watch: bool):
    """Run the trading engine with proper async lifecycle."""
    # Initialize the engine (starts message bus, loads warmup data)
    await engine.initialize()

    # Start trading
    await engine.start_trading()

    # Keep running - engine handles its own lifecycle
    try:
        # Keep the event loop alive
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down...")
        await engine.stop()


if __name__ == "__main__":
    main()
