"""Trading Engine — orchestrates the complete trading pipeline.

Coordinates:
- TypedMessageBus: type-safe event backbone with Pub/Sub + Req/Rep
- Controllers: signal generation (decision)
- RiskEngine: validation (guardrails)
- Executors: order execution (action)
- Portfolio: position & equity aggregation (state)
- ConfigManager: centralized configuration
- Clock: deterministic time (backtest) or real-time (live)

Follows Controller-Executor pattern (inspired by Hummingbot V2 + NautilusTrader).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd

from src.connection.deriv_client import DerivClient, DerivConfig
from src.trading.clock import Clock
from src.trading.message_bus_typed import (
    Request,
    Response,
    Topics,
    get_typed_message_bus,
    shutdown_typed_message_bus,
)

logger = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    """Master configuration for the trading engine."""

    # Symbol & strategy
    symbol: str = "R_100"
    strategy_name: str = "volatility_v2"
    executor_type: str = "paper"  # "paper" or "deriv"

    # Risk
    max_risk_per_trade: Decimal = field(default_factory=lambda: Decimal("0.03"))
    max_daily_drawdown: Decimal = field(default_factory=lambda: Decimal("0.05"))
    max_trades_per_day: int = 10
    circuit_breaker_losses: int = 3

    # Portfolio
    initial_capital: Decimal = field(default_factory=lambda: Decimal("10000.0"))
    tradable_balance_ratio: Decimal = field(default_factory=lambda: Decimal("0.99"))
    max_total_exposure: Decimal = field(default_factory=lambda: Decimal("1.0"))
    max_single_symbol_exposure: Decimal = field(default_factory=lambda: Decimal("0.5"))

    # Controller
    score_threshold: float = 0.35
    cooldown_seconds: float = 10.0
    max_concurrent_signals: int = 1

    # Executor
    default_stake: Decimal = field(default_factory=lambda: Decimal("1.0"))
    max_position_duration: int = 900

    # Deriv connection
    deriv_app_id: str = ""
    deriv_token: str = ""

    # Runtime
    warmup_candles: int = 500


class TradingEngine:
    """Trading Engine — orchestrates the complete trading pipeline.

    Coordinates:
    - TypedMessageBus: type-safe event backbone with Pub/Sub + Req/Rep
    - Controllers: signal generation (decision)
    - RiskEngine: validation (guardrails)
    - Executors: order execution (action)
    - Portfolio: position & equity aggregation (state)
    """

    def __init__(self, config: EngineConfig) -> None:
        self.config = config

        # Typed message bus (initialized in initialize())
        self.bus = get_typed_message_bus()

        # Deriv client (for real trading)
        self.deriv_client: DerivClient | None = None
        if config.executor_type == "deriv" and config.deriv_app_id:
            self.deriv_client = DerivClient(DerivConfig(
                api_base_url="https://api.derivws.com",
                app_id=config.deriv_app_id,
                api_token=config.deriv_token,
                demo_account="",
                real_account="",
                is_demo=True,
                otp_lifetime=120,
                ws_demo_path="/trading/v1/options/ws/demo",
                ws_real_path="/trading/v1/options/ws/real",
                ws_public_path="/trading/v1/options/ws/public",
                otp_path="/trading/v1/options/accounts",
            ))

        # Market data
        self._candles: pd.DataFrame = pd.DataFrame()
        self._last_candle_epoch: int = 0
        self._tick_buffer: list[dict[str, Any]] = []

        # State
        self._trading_task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._dashboard_push_task: asyncio.Task[None] | None = None
        self._running = False

        # Register request handlers
        self._register_request_handlers()

    def _register_request_handlers(self) -> None:
        """Register request handlers for Req/Rep operations."""
        self.bus.register_request_handler(Topics.ORDER_SUBMIT, self._handle_order_submit)
        self.bus.register_request_handler(Topics.ORDER_CANCEL, self._handle_order_cancel)
        self.bus.register_request_handler(Topics.POSITION_CLOSE, self._handle_position_close)
        self.bus.register_request_handler(Topics.ACCOUNT_INFO, self._handle_account_info)

    async def _handle_order_submit(self, req: Request) -> Response:
        """Handle order submission request."""
        try:
            # Validate through risk engine
            order_payload = req.payload
            validation = self.risk_engine.validate_order(
                side=order_payload.side,
                symbol=order_payload.symbol,
                entry_price=Decimal(str(order_payload.entry_price or 0)),
                stop_loss=Decimal(str(order_payload.stop_loss or 0)),
                stake=Decimal(str(order_payload.stake)),
                current_balance=Decimal(str(self.portfolio.cash())),
            )

            if not validation.passed:
                return Response(
                    request_id=req.request_id,
                    success=False,
                    error=validation.rejection_reason,
                )

            # Submit to executor
            if hasattr(self.executor, "submit_order"):
                result = await self.executor.submit_order(order_payload)
                return Response(
                    request_id=req.request_id,
                    success=True,
                    payload=result,
                )
            else:
                return Response(
                    request_id=req.request_id,
                    success=False,
                    error="Executor does not support order submission",
                )
        except Exception as e:
            logger.exception("Order submit failed: %s", e)
            return Response(
                request_id=req.request_id,
                success=False,
                error=str(e),
            )

    async def _handle_order_cancel(self, req: Request) -> Response:
        """Handle order cancellation request."""
        try:
            # TODO: Implement order cancellation
            return Response(
                request_id=req.request_id,
                success=False,
                error="Not implemented",
            )
        except Exception as e:
            return Response(
                request_id=req.request_id,
                success=False,
                error=str(e),
            )

    async def _handle_position_close(self, req: Request) -> Response:
        """Handle position close request."""
        try:
            # TODO: Implement position close
            return Response(
                request_id=req.request_id,
                success=False,
                error="Not implemented",
            )
        except Exception as e:
            return Response(
                request_id=req.request_id,
                success=False,
                error=str(e),
            )

    async def _handle_account_info(self, req: Request) -> Response:
        """Handle account info request."""
        try:
            return Response(
                request_id=req.request_id,
                success=True,
                payload=self.portfolio.get_summary(),
            )
        except Exception as e:
            return Response(
                request_id=req.request_id,
                success=False,
                error=str(e),
            )

    async def _load_warmup_candles(self) -> None:
        """Load historical candles for warmup period."""
        from src.data.collector import DataCollector
        
        # Use public endpoint for market data (no auth needed)
        if self.deriv_client is None:
            # Create a temporary client for data collection
            config = DerivConfig(
                api_base_url="https://api.derivws.com",
                app_id="",  # Not needed for public data
                api_token="",  # Not needed for public data
                demo_account="",
                real_account="",
                is_demo=True,
                otp_lifetime=120,
                ws_demo_path="/trading/v1/options/ws/demo",
                ws_real_path="/trading/v1/options/ws/real",
                ws_public_path="/trading/v1/options/ws/public",
                otp_path="/trading/v1/options/accounts",
            )
            temp_client = DerivClient(config)
            await temp_client.connect_public()
            collector = DataCollector(temp_client)
        else:
            collector = DataCollector(self.deriv_client)
        
        # Download warmup candles
        symbol = self.config.symbol
        granularity = 60  # 1-minute candles as default
        count = self.config.warmup_candles
        
        self._candles = await collector.download_candles(
            symbol=symbol,
            count=count,
            granularity=granularity,
        )
        
        self._last_candle_epoch = (
            int(self._candles.iloc[-1]["epoch"]) 
            if len(self._candles) > 0 
            else 0
        )
        
        logger.info(
            "Loaded %d warmup candles for %s (last epoch: %d)",
            len(self._candles),
            symbol,
            self._last_candle_epoch,
        )

    async def initialize(self) -> None:
        """Initialize the trading engine."""
        # Initialize Deriv client if needed
        if (
            self.deriv_client is None
            and self.config.executor_type == "deriv"
            and self.config.deriv_app_id
        ):
            try:
                deriv_config = DerivConfig.from_yaml()
            except Exception:
                deriv_config = DerivConfig(
                    api_base_url="https://api.derivws.com",
                    app_id=self.config.deriv_app_id,
                    api_token=self.config.deriv_token,
                    demo_account="",
                    real_account="",
                    is_demo=True,
                    otp_lifetime=120,
                    ws_demo_path="/trading/v1/options/ws/demo",
                    ws_real_path="/trading/v1/options/ws/real",
                    ws_public_path="/trading/v1/options/ws/public",
                    otp_path="/trading/v1/options/accounts",
                )
            self.deriv_client = DerivClient(deriv_config)

            if hasattr(self.executor, "client"):
                self.executor.client = self.deriv_client

        # Load warmup candles
        await self._load_warmup_candles()

        logger.info(
            "TradingEngine initialized: %s / %s",
            self.config.strategy_name,
            self.config.symbol,
        )

    async def start_trading(self) -> None:
        """Start trading."""
        if self._running:
            logger.warning("Trading engine is already running")
            return

        self._running = True
        logger.info("Starting trading engine...")

        # Start controller
        await self.controller.start()

        # Start executor
        await self.executor.start()

        # Start main trading loop
        self._trading_task = asyncio.create_task(self._trading_loop())

        # Start monitoring task
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        # Start dashboard push task if watch enabled
        if hasattr(self, 'watch') and self.watch:
            self._dashboard_push_task = asyncio.create_task(self._dashboard_push_loop())

        logger.info("Trading engine started")

    async def stop(self) -> None:
        """Stop the trading engine gracefully."""
        if not self._running:
            return

        logger.info("Stopping trading engine...")
        self._running = False

        # Cancel tasks
        for task in [self._trading_task, self._monitor_task, self._dashboard_push_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop components
        await self.controller.stop()
        await self.executor.stop()

        # Close Deriv client
        if self.deriv_client:
            await self.deriv_client.disconnect()

        # Shutdown typed message bus
        await shutdown_typed_message_bus()

        logger.info(
            "TradingEngine stopped: %s / %s",
            self.config.strategy_name,
            self.config.symbol,
        )

    def get_status(self) -> dict:
        """Get complete engine status for dashboard/API."""
        return {
            "engine": {
                "running": self._running,
                "symbol": self.config.symbol,
                "strategy": self.config.strategy_name,
                "executor_type": self.config.executor_type,
                "clock_type": self.clock.clock_type.value,
            },
            "portfolio": self.portfolio.get_summary(),
            "risk": self.risk_engine.get_status(),
            "controller": {
                "active_signals": (
                    self.controller._active_signals
                    if hasattr(self.controller, "_active_signals")
                    else 0
                ),
            },
        }

    # Properties for components (lazy initialized)
    @property
    def controller(self):
        if not hasattr(self, '_controller'):
            from src.trading.controllers import create_controller
            from src.trading.strategy_factory import create_strategy
            
            strategy = create_strategy(
                name=self.config.strategy_name,
                symbol=self.config.symbol
            )
            self._controller = create_controller(
                strategy_name=self.config.strategy_name,
                symbol=self.config.symbol,
                strategy=strategy,
                message_bus=self.bus
            )
        return self._controller

    @property
    def risk_engine(self):
        if not hasattr(self, '_risk_engine'):
            from src.trading.risk_engine import RiskEngine, RiskConfig
            risk_config = RiskConfig(
                max_risk_per_trade=self.config.max_risk_per_trade,
                max_daily_drawdown=self.config.max_daily_drawdown,
                max_trades_per_day=self.config.max_trades_per_day,
                circuit_breaker_losses=self.config.circuit_breaker_losses,
                kelly_fraction=Decimal("0.25"),
                initial_capital=self.config.initial_capital,
                tradable_balance_ratio=self.config.tradable_balance_ratio,
                max_open_positions=5,
            )
            self._risk_engine = RiskEngine(config=risk_config)
        return self._risk_engine

    @property
    def executor(self):
        if not hasattr(self, '_executor'):
            from src.trading.executors import create_executor
            from src.trading.portfolio import Portfolio, PortfolioConfig
            
            portfolio_config = PortfolioConfig(
                initial_capital=self.config.initial_capital,
                tradable_balance_ratio=self.config.tradable_balance_ratio,
                max_total_exposure=self.config.max_total_exposure,
                max_single_symbol_exposure=self.config.max_single_symbol_exposure,
            )
            portfolio = Portfolio(config=portfolio_config)
            self._executor = create_executor(
                executor_type=self.config.executor_type,
                symbol=self.config.symbol,
                strategy_name=self.config.strategy_name,
                portfolio=portfolio,
                risk_engine=self.risk_engine,
                message_bus=self.bus,
                deriv_client=self.deriv_client,
                default_stake=self.config.default_stake,
                max_position_duration=self.config.max_position_duration
            )
        return self._executor

    @property
    def portfolio(self):
        if not hasattr(self, '_portfolio'):
            from src.trading.portfolio import Portfolio, PortfolioConfig
            portfolio_config = PortfolioConfig(
                initial_capital=self.config.initial_capital,
                tradable_balance_ratio=self.config.tradable_balance_ratio,
                max_total_exposure=self.config.max_total_exposure,
                max_single_symbol_exposure=self.config.max_single_symbol_exposure,
            )
            self._portfolio = Portfolio(config=portfolio_config)
        return self._portfolio

    @property
    def clock(self):
        if not hasattr(self, '_clock'):
            from src.trading.clock import LiveClock
            self._clock = LiveClock()
        return self._clock

    async def _trading_loop(self) -> None:
        """Main trading loop."""
        logger.info("Trading loop started")
        try:
            while self._running:
                # Main trading logic happens via event-driven architecture
                # This loop just keeps the engine alive
                await asyncio.sleep(1)
        except Exception as e:
            logger.exception("Error in trading loop: %s", e)
        finally:
            logger.info("Trading loop stopped")

    async def _monitor_loop(self) -> None:
        """Monitor loop for health checks and metrics."""
        logger.info("Monitor loop started")
        try:
            while self._running:
                # Update metrics, check health, etc.
                await asyncio.sleep(5)
        except Exception as e:
            logger.exception("Error in monitor loop: %s", e)
        finally:
            logger.info("Monitor loop stopped")

    async def _dashboard_push_loop(self) -> None:
        """Dashboard push loop for WebSocket streaming."""
        logger.info("Dashboard push loop started")
        try:
            while self._running:
                # Push updates to dashboard WebSocket clients
                await asyncio.sleep(1)
        except Exception as e:
            logger.exception("Error in dashboard push loop: %s", e)
        finally:
            logger.info("Dashboard push loop stopped")


async def create_and_run_engine(
    symbol: str = "R_100",
    strategy_name: str = "volatility_v2",
    executor_type: str = "paper",
    clock: Clock | None = None,
    **kwargs,
) -> TradingEngine:
    """Factory to create and start a trading engine."""
    config = EngineConfig(
        symbol=symbol,
        strategy_name=strategy_name,
        executor_type=executor_type,
        **kwargs,
    )
    engine = TradingEngine(config=config, clock=clock)
    await engine.initialize()
    await engine.start_trading()
    return engine


if __name__ == "__main__":
    # Quick test
    import asyncio

    async def test():
        # Use TestClock for fast testing
        from src.trading.clock import TestClock

        test_clock = TestClock()

        engine = await create_and_run_engine(
            symbol="R_100",
            strategy_name="volatility_v2",
            executor_type="paper",
            clock=test_clock,
        )
        print("Engine started")
        await asyncio.sleep(2)
        print("Status:", engine.get_status())
        await engine.stop()

    asyncio.run(test())