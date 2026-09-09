"""Executor — executes trades based on validated signals.

Executors handle the actual order submission, monitoring, and fill management.
They subscribe to validated signals from the message bus.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.strategies.base import Signal, SignalType
from src.trading.message_bus import Event, EventType, get_message_bus
from src.trading.portfolio import Portfolio, Position
from src.trading.risk_engine import RiskEngine

logger = logging.getLogger(__name__)


@dataclass
class ExecutorConfig:
    """Configuration for an executor."""

    symbol: str
    strategy_name: str
    max_position_duration: int = 900  # seconds
    default_stake: Decimal = Decimal("1.0")


class BaseExecutor(ABC):
    """Abstract base class for all executors."""

    def __init__(
        self,
        config: ExecutorConfig,
        portfolio: Portfolio,
        risk_engine: RiskEngine,
        message_bus=None,
    ):
        self.config = config
        self.portfolio = portfolio
        self.risk_engine = risk_engine
        self.bus = message_bus or get_message_bus()
        self._running = False
        self._subscribed = False
        self.client = None  # DerivClient for live trading
        self._executor_id = f"{config.strategy_name}_{config.symbol}"

    @property
    def executor_id(self) -> str:
        """Unique identifier for this executor."""
        return self._executor_id

    @abstractmethod
    async def execute_order(self, signal: Signal) -> dict[str, Any]:
        """Execute an order based on the signal. Returns order result."""
        pass

    async def submit_order(self, order_payload) -> dict[str, Any]:
        """Submit an order from request handler (for Req/Rep)."""
        signal = Signal(
            type=SignalType.LONG if order_payload.side == "BUY" else SignalType.SHORT,
            symbol=order_payload.symbol,
            entry_price=order_payload.entry_price or 0,
            stop_loss=order_payload.stop_loss or 0,
            take_profit=order_payload.take_profit or 0,
            duration_seconds=order_payload.duration or self.config.max_position_duration,
            confidence=1.0,
            metadata={},
        )
        return await self.execute_order(signal)

    async def on_signal_validated(self, event: Event) -> None:
        """Handler for validated signals."""
        if not self._running:
            return

        signal_data = event.payload.get("signal", {})
        signal = Signal(
            type=SignalType(signal_data.get("type", "NO_SIGNAL")),
            symbol=signal_data.get("symbol", self.config.symbol),
            entry_price=signal_data.get("entry_price", 0),
            stop_loss=signal_data.get("stop_loss", 0),
            take_profit=signal_data.get("take_profit", 0),
            duration_seconds=signal_data.get("duration_seconds", self.config.max_position_duration),
            confidence=signal_data.get("confidence", 0.0),
            metadata=signal_data.get("metadata", {}),
        )

        if signal.type == SignalType.NO_SIGNAL:
            return

        logger.info(
            "%s received validated signal: %s @ %.4f (SL: %.4f, TP: %.4f)",
            self.config.strategy_name,
            signal.type.value,
            signal.entry_price,
            signal.stop_loss,
            signal.take_profit,
        )

        # Execute the order
        result = await self.execute_order(signal)

        # Publish result
        fill_event = Event(
            type=EventType.ORDER_FILLED if result.get("success") else EventType.ORDER_REJECTED,
            payload={
                "signal": signal_data,
                "result": result,
                "executor": self.config.strategy_name,
                "symbol": self.config.symbol,
            },
            source=self.config.strategy_name,
            correlation_id=event.correlation_id,
        )
        await self.bus.publish(fill_event)

    async def subscribe(self) -> None:
        """Subscribe to validated signal events."""
        if not self._subscribed:
            self.bus.subscribe(EventType.ORDER_VALIDATED, self.on_signal_validated)
            self._subscribed = True

    async def start(self) -> None:
        """Start the executor."""
        self._running = True
        await self.subscribe()
        logger.info("Executor %s started for %s", self.config.strategy_name, self.config.symbol)

    async def stop(self) -> None:
        """Stop the executor."""
        self._running = False
        logger.info("Executor %s stopped", self.config.strategy_name)


class DerivExecutor(BaseExecutor):
    """Executor for Deriv synthetic indices via WebSocket API."""

    def __init__(
        self,
        config: ExecutorConfig,
        portfolio: Portfolio,
        risk_engine: RiskEngine,
        deriv_client=None,
        message_bus=None,
    ):
        super().__init__(config, portfolio, risk_engine, message_bus)
        self.client = deriv_client
        self._open_contracts: dict[str, dict] = {}  # contract_id -> {signal, contract}

    async def execute_order(self, signal: Signal) -> dict[str, Any]:
        """Execute a trade on Deriv."""
        if self.client is None:
            return {
                "success": False,
                "error": "Deriv client not configured",
                "signal_type": signal.type.value,
            }

        # Validate with risk engine
        validation = self.risk_engine.validate_order(
            side="BUY" if signal.type == SignalType.LONG else "SELL",
            symbol=signal.symbol,
            entry_price=Decimal(str(signal.entry_price)),
            stop_loss=Decimal(str(signal.stop_loss)),
            stake=Decimal(str(self.config.default_stake)),
            current_balance=Decimal(str(self.portfolio.cash())),
        )

        if not validation.passed:
            return {
                "success": False,
                "error": validation.rejection_reason,
                "signal_type": signal.type.value,
                "risk_checks": [c.message for c in validation.checks],
            }

        # Submit order to Deriv
        try:
            contract_type = "CALL" if signal.type == SignalType.LONG else "PUT"
            proposal = await self.client.get_proposal(
                contract_type=contract_type,
                symbol=signal.symbol,
                stake=float(self.config.default_stake),
                duration=signal.duration_seconds,
                currency="USD",
            )

            if "error" in proposal:
                return {
                    "success": False,
                    "error": proposal["error"],
                    "signal_type": signal.type.value,
                }

            # Buy the contract
            buy_result = await self.client.buy(proposal["id"])

            if "error" in buy_result:
                return {
                    "success": False,
                    "error": buy_result["error"],
                    "signal_type": signal.type.value,
                }

            contract_id = buy_result.get("contract_id", "")
            self._open_contracts[contract_id] = {
                "signal": signal,
                "contract": buy_result,
                "entry_time": datetime.now(UTC),
            }

            # Add to portfolio
            position = Position(
                symbol=signal.symbol,
                side=signal.type.value,
                entry_price=Decimal(str(signal.entry_price)),
                current_price=Decimal(str(signal.entry_price)),
                quantity=Decimal(str(self.config.default_stake)),
                stop_loss=Decimal(str(signal.stop_loss)),
                take_profit=Decimal(str(signal.take_profit)),
                entry_time=datetime.now(UTC),
                strategy_name=self.config.strategy_name,
                contract_id=contract_id,
            )
            self.portfolio.add_position(position)

            # Publish position opened event
            event = Event(
                type=EventType.POSITION_OPENED,
                payload={
                    "contract_id": contract_id,
                    "symbol": signal.symbol,
                    "side": signal.type.value,
                    "entry_price": signal.entry_price,
                    "stake": float(self.config.default_stake),
                    "strategy": self.config.strategy_name,
                },
                source=self.config.strategy_name,
            )
            await self.bus.publish(event)

            return {
                "success": True,
                "contract_id": contract_id,
                "signal_type": signal.type.value,
                "entry_price": signal.entry_price,
                "stake": float(self.config.default_stake),
            }

        except Exception as e:
            logger.exception("Order execution failed: %s", e)
            return {
                "success": False,
                "error": str(e),
                "signal_type": signal.type.value,
            }

    async def monitor_positions(self) -> None:
        """Monitor open positions for TP/SL/expiry."""
        while self._running:
            try:
                for contract_id, pos_data in list(self._open_contracts.items()):
                    if self.client:
                        status = await self.client.get_contract_status(contract_id)

                        if status.get("is_sold") or status.get("is_expired"):
                            await self._handle_fill(contract_id, pos_data, status)

                await asyncio.sleep(5)  # Check every 5 seconds

            except Exception as e:
                logger.exception("Position monitoring error: %s", e)
                await asyncio.sleep(10)

    async def _handle_fill(
        self,
        contract_id: str,
        pos_data: dict,
        status: dict,
    ) -> None:
        """Handle a filled/expired contract."""
        signal = pos_data["signal"]
        exit_price = Decimal(str(status.get("exit_spot", signal.entry_price)))
        pnl = Decimal(str(status.get("profit", 0)))
        is_win = pnl > 0

        # Remove from portfolio
        position = self.portfolio.remove_position(
            contract_id=contract_id,
            exit_price=exit_price,
            exit_reason=status.get("exit_reason", "FILLED"),
        )

        if position:
            # Record in risk engine
            self.risk_engine.record_fill(
                pnl=pnl,
                is_win=is_win,
                new_balance=self.portfolio.cash(),
            )

            # Publish position closed event
            event = Event(
                type=EventType.POSITION_CLOSED,
                payload={
                    "contract_id": contract_id,
                    "symbol": signal.symbol,
                    "side": signal.type.value,
                    "entry_price": float(signal.entry_price),
                    "exit_price": float(exit_price),
                    "pnl": float(pnl),
                    "is_win": is_win,
                    "exit_reason": status.get("exit_reason", "FILLED"),
                    "strategy": self.config.strategy_name,
                },
                source=self.config.strategy_name,
            )
            await self.bus.publish(event)

        # Clean up
        del self._open_contracts[contract_id]


class PaperExecutor(BaseExecutor):
    """Executor for paper trading (simulation)."""

    def __init__(
        self,
        config: ExecutorConfig,
        portfolio: Portfolio,
        risk_engine: RiskEngine,
        message_bus=None,
    ):
        super().__init__(config, portfolio, risk_engine, message_bus)
        self._paper_contract_id = 0

    async def execute_order(self, signal: Signal) -> dict[str, Any]:
        """Execute a simulated paper trade."""
        # Validate with risk engine
        validation = self.risk_engine.validate_order(
            side="BUY" if signal.type == SignalType.LONG else "SELL",
            symbol=signal.symbol,
            entry_price=Decimal(str(signal.entry_price)),
            stop_loss=Decimal(str(signal.stop_loss)),
            stake=Decimal(str(self.config.default_stake)),
            current_balance=Decimal(str(self.portfolio.cash())),
        )

        if not validation.passed:
            return {
                "success": False,
                "error": validation.rejection_reason,
                "signal_type": signal.type.value,
                "risk_checks": [c.message for c in validation.checks],
            }

        # Create paper contract
        self._paper_contract_id += 1
        contract_id = f"PAPER_{self._paper_contract_id}"

        # Add to portfolio immediately (paper trades fill instantly)
        position = Position(
            symbol=signal.symbol,
            side=signal.type.value,
            entry_price=Decimal(str(signal.entry_price)),
            current_price=Decimal(str(signal.entry_price)),
            quantity=Decimal(str(self.config.default_stake)),
            stop_loss=Decimal(str(signal.stop_loss)),
            take_profit=Decimal(str(signal.take_profit)),
            entry_time=datetime.now(UTC),
            strategy_name=self.config.strategy_name,
            contract_id=contract_id,
        )
        self.portfolio.add_position(position)

        # Schedule monitoring for TP/SL/expiry
        asyncio.create_task(self._monitor_paper_position(contract_id, signal))

        return {
            "success": True,
            "contract_id": contract_id,
            "signal_type": signal.type.value,
            "entry_price": signal.entry_price,
            "stake": float(self.config.default_stake),
            "mode": "paper",
        }

    async def _monitor_paper_position(self, contract_id: str, signal: Signal) -> None:
        """Monitor paper position for TP/SL/time exit."""
        max_duration = signal.duration_seconds
        check_interval = 5  # seconds
        checks = 0

        while checks * check_interval < max_duration and self._running:
            await asyncio.sleep(check_interval)
            checks += 1

            # In real paper trading, we'd get current price from market data
            # For simulation, we simulate a random walk or check against live data
            # This is a placeholder - in production, connect to live price feed

        # Time exit
        if contract_id in [p.contract_id for p in self.portfolio.get_positions()]:
            position = self.portfolio.get_position(contract_id)
            if position:
                exit_price = position.current_price
                pnl = position.unrealized_pnl
                is_win = pnl > 0

                self.portfolio.remove_position(
                    contract_id=contract_id,
                    exit_price=exit_price,
                    exit_reason="TIME",
                )

                self.risk_engine.record_fill(
                    pnl=pnl,
                    is_win=is_win,
                    new_balance=self.portfolio.cash(),
                )

                # Publish position closed
                event = Event(
                    type=EventType.POSITION_CLOSED,
                    payload={
                        "contract_id": contract_id,
                        "symbol": signal.symbol,
                        "side": signal.type.value,
                        "entry_price": float(signal.entry_price),
                        "exit_price": float(exit_price),
                        "pnl": float(pnl),
                        "is_win": is_win,
                        "exit_reason": "TIME",
                        "strategy": self.config.strategy_name,
                    },
                    source=self.config.strategy_name,
                )
                await self.bus.publish(event)


# Executor factory
def create_executor(
    executor_type: str,
    symbol: str,
    strategy_name: str,
    portfolio: Portfolio,
    risk_engine: RiskEngine,
    message_bus=None,
    **kwargs,
) -> BaseExecutor:
    """Factory function to create executors."""

    config = ExecutorConfig(
        symbol=symbol,
        strategy_name=strategy_name,
        max_position_duration=kwargs.get("max_duration", 900),
        default_stake=Decimal(str(kwargs.get("default_stake", 1.0))),
    )

    executors = {
        "deriv": DerivExecutor,
        "paper": PaperExecutor,
    }

    executor_class = executors.get(executor_type.lower(), PaperExecutor)
    return executor_class(config, portfolio, risk_engine, message_bus=message_bus)
