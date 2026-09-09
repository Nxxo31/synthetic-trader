"""Portfolio — aggregates positions, equity, and exposure across instruments.

Centralized view of all open positions, mark-to-market, and total equity.
Supports multi-instrument, multi-strategy exposure tracking.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents a single open position."""

    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: Decimal
    current_price: Decimal
    quantity: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    entry_time: datetime
    strategy_name: str
    contract_id: str = ""
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")

    def update_price(self, price: Decimal) -> None:
        """Update current price and recalculate unrealized P&L."""
        self.current_price = price
        if self.side == "LONG":
            self.unrealized_pnl = (price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - price) * self.quantity

    def is_stop_loss_hit(self, price: Decimal) -> bool:
        """Check if stop loss is hit."""
        if self.side == "LONG":
            return price <= self.stop_loss
        else:
            return price >= self.stop_loss

    def is_take_profit_hit(self, price: Decimal) -> bool:
        """Check if take profit is hit."""
        if self.side == "LONG":
            return price >= self.take_profit
        else:
            return price <= self.take_profit

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": float(self.entry_price),
            "current_price": float(self.current_price),
            "quantity": float(self.quantity),
            "stop_loss": float(self.stop_loss),
            "take_profit": float(self.take_profit),
            "entry_time": self.entry_time.isoformat(),
            "strategy_name": self.strategy_name,
            "contract_id": self.contract_id,
            "unrealized_pnl": float(self.unrealized_pnl),
            "realized_pnl": float(self.realized_pnl),
        }


@dataclass
class PortfolioConfig:
    """Portfolio configuration."""

    initial_capital: Decimal = Decimal("10000.0")
    tradable_balance_ratio: Decimal = Decimal("0.99")
    max_total_exposure: Decimal = Decimal("1.0")  # 100% of capital
    max_single_symbol_exposure: Decimal = Decimal("0.5")  # 50% in one symbol


class Portfolio:
    """Aggregates positions and tracks equity across all strategies."""

    def __init__(self, config: PortfolioConfig):
        self.config = config
        self._positions: dict[str, Position] = {}  # contract_id -> Position
        self._positions_by_symbol: dict[str, list[str]] = defaultdict(list)  # symbol -> [contract_ids]
        self._positions_by_strategy: dict[str, list[str]] = defaultdict(list)  # strategy -> [contract_ids]
        self._cash: Decimal = config.initial_capital
        self._starting_cash: Decimal = config.initial_capital
        self._total_realized_pnl: Decimal = Decimal("0")
        self._trade_history: list[dict] = []

    def add_position(self, position: Position) -> None:
        """Add a new open position."""
        self._positions[position.contract_id] = position
        self._positions_by_symbol[position.symbol].append(position.contract_id)
        self._positions_by_strategy[position.strategy_name].append(position.contract_id)
        logger.info(
            "Position added: %s %s @ %.4f (SL: %.4f, TP: %.4f) qty=%.2f",
            position.side,
            position.symbol,
            position.entry_price,
            position.stop_loss,
            position.take_profit,
            position.quantity,
        )

    def remove_position(self, contract_id: str, exit_price: Decimal, exit_reason: str) -> Position | None:
        """Close a position and move to history."""
        position = self._positions.pop(contract_id, None)
        if not position:
            return None

        # Calculate final P&L
        if position.side == "LONG":
            realized = (exit_price - position.entry_price) * position.quantity
        else:
            realized = (position.entry_price - exit_price) * position.quantity

        position.realized_pnl = realized
        position.unrealized_pnl = Decimal("0")
        position.current_price = exit_price

        # Remove from indices
        if contract_id in self._positions_by_symbol[position.symbol]:
            self._positions_by_symbol[position.symbol].remove(contract_id)
        if contract_id in self._positions_by_strategy[position.strategy_name]:
            self._positions_by_strategy[position.strategy_name].remove(contract_id)

        # Update cash and totals
        self._cash += realized
        self._total_realized_pnl += realized

        # Record in history
        self._trade_history.append({
            "contract_id": contract_id,
            "symbol": position.symbol,
            "side": position.side,
            "entry_price": float(position.entry_price),
            "exit_price": float(exit_price),
            "quantity": float(position.quantity),
            "realized_pnl": float(realized),
            "entry_time": position.entry_time.isoformat(),
            "exit_time": datetime.now(UTC).isoformat(),
            "strategy_name": position.strategy_name,
            "exit_reason": exit_reason,
        })

        logger.info(
            "Position closed: %s %s @ %.4f → %.4f P&L=%.2f (%s)",
            position.side,
            position.symbol,
            position.entry_price,
            exit_price,
            realized,
            exit_reason,
        )
        return position

    def update_price(self, symbol: str, price: Decimal) -> None:
        """Update mark price for all positions in a symbol."""
        for contract_id in self._positions_by_symbol.get(symbol, []):
            position = self._positions.get(contract_id)
            if position:
                position.update_price(price)

    def get_position(self, contract_id: str) -> Position | None:
        """Get a position by contract ID."""
        return self._positions.get(contract_id)

    def get_positions(self, symbol: str | None = None, strategy: str | None = None) -> list[Position]:
        """Get all open positions, optionally filtered."""
        if symbol:
            return [self._positions[cid] for cid in self._positions_by_symbol.get(symbol, []) if cid in self._positions]
        if strategy:
            return [self._positions[cid] for cid in self._positions_by_strategy.get(strategy, []) if cid in self._positions]
        return list(self._positions.values())

    def get_open_positions_count(self) -> int:
        """Total number of open positions."""
        return len(self._positions)

    def get_positions_count_by_symbol(self, symbol: str) -> int:
        """Count open positions for a symbol."""
        return len([cid for cid in self._positions_by_symbol.get(symbol, []) if cid in self._positions])

    def get_positions_count_by_strategy(self, strategy: str) -> int:
        """Count open positions for a strategy."""
        return len([cid for cid in self._positions_by_strategy.get(strategy, []) if cid in self._positions])

    def equity(self) -> Decimal:
        """Total equity = cash + unrealized P&L."""
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return self._cash + unrealized

    def cash(self) -> Decimal:
        """Available cash."""
        return self._cash

    def starting_cash(self) -> Decimal:
        """Initial capital at session start."""
        return self._starting_cash

    def total_realized_pnl(self) -> Decimal:
        """Total realized P&L."""
        return self._total_realized_pnl

    def total_unrealized_pnl(self) -> Decimal:
        """Total unrealized P&L."""
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return unrealized if unrealized != 0 else Decimal("0")  # type: ignore[return-value]

    def total_pnl(self) -> Decimal:
        """Total P&L (realized + unrealized)."""
        return self._total_realized_pnl + self.total_unrealized_pnl()

    def exposure_per_symbol(self) -> dict[str, Decimal]:
        """Current exposure (notional value) per symbol."""
        exposure = {}
        for symbol, contract_ids in self._positions_by_symbol.items():
            total = Decimal("0")
            for cid in contract_ids:
                pos = self._positions.get(cid)
                if pos:
                    total += pos.current_price * pos.quantity
            if total > 0:
                exposure[symbol] = total
        return exposure

    def exposure_per_strategy(self) -> dict[str, Decimal]:
        """Current exposure per strategy."""
        exposure = {}
        for strategy, contract_ids in self._positions_by_strategy.items():
            total = Decimal("0")
            for cid in contract_ids:
                pos = self._positions.get(cid)
                if pos:
                    total += pos.current_price * pos.quantity
            if total > 0:
                exposure[strategy] = total
        return exposure

    def total_exposure(self) -> Decimal:
        """Total notional exposure across all positions."""
        return sum(self.exposure_per_symbol().values())

    def available_capital(self) -> Decimal:
        """Capital available for new trades (respecting tradable_balance_ratio)."""
        equity_val = self.equity()
        max_tradable = equity_val * self.config.tradable_balance_ratio
        used = self.total_exposure()
        available = max_tradable - used
        return max(Decimal("0"), available)  # type: ignore[return-value]

    def can_open_position(
        self,
        symbol: str,
        strategy: str,
        notional: Decimal,
    ) -> tuple[bool, str]:
        """Check if a new position can be opened."""
        # Check total exposure limit
        current_exposure = self.total_exposure()
        if current_exposure + notional > self.equity() * self.config.max_total_exposure:
            return False, f"Total exposure limit would be exceeded: {current_exposure + notional:.2f} > {self.equity() * self.config.max_total_exposure:.2f}"

        # Check single symbol exposure
        symbol_exposure = self.exposure_per_symbol().get(symbol, Decimal("0"))
        if symbol_exposure + notional > self.equity() * self.config.max_single_symbol_exposure:
            return False, f"Single symbol exposure limit for {symbol}: {symbol_exposure + notional:.2f} > {self.equity() * self.config.max_single_symbol_exposure:.2f}"

        # Check available capital
        if notional > self.available_capital():
            return False, f"Insufficient available capital: {notional:.2f} > {self.available_capital():.2f}"

        return True, "OK"

    def get_dynamic_stake(
        self,
        symbol: str,
        strategy: str,
        max_open_trades: int = 5,
    ) -> Decimal:
        """Calculate dynamic stake per Freqtrade pattern: balance / available_slots."""
        equity_val = self.equity()
        open_trades = self.get_open_positions_count()
        available_slots = max_open_trades - open_trades

        if available_slots <= 0:
            return Decimal("0")

        stake = (equity_val * self.config.tradable_balance_ratio) / available_slots
        return stake

    def get_summary(self) -> dict:
        """Get complete portfolio summary for dashboard/API."""
        equity_val = self.equity()
        exposure_by_symbol = self.exposure_per_symbol()
        exposure_by_strategy = self.exposure_per_strategy()

        return {
            "equity": float(equity_val),
            "cash": float(self._cash),
            "starting_cash": float(self._starting_cash),
            "total_realized_pnl": float(self._total_realized_pnl),
            "total_unrealized_pnl": float(self.total_unrealized_pnl()),
            "total_pnl": float(self.total_pnl()),
            "total_pnl_pct": float((self.total_pnl() / self._starting_cash) * 100) if self._starting_cash > 0 else 0,
            "open_positions": self.get_open_positions_count(),
            "positions": [p.to_dict() for p in self._positions.values()],
            "exposure_by_symbol": {k: float(v) for k, v in exposure_by_symbol.items()},
            "exposure_by_strategy": {k: float(v) for k, v in exposure_by_strategy.items()},
            "total_exposure": float(self.total_exposure()),
            "available_capital": float(self.available_capital()),
            "trade_count": len(self._trade_history),
        }

    def get_equity_curve_data(self) -> pd.DataFrame:
        """Generate equity curve DataFrame from trade history."""
        if not self._trade_history:
            return pd.DataFrame(columns=["timestamp", "equity", "realized_pnl"])

        df = pd.DataFrame(self._trade_history)
        df["timestamp"] = pd.to_datetime(df["exit_time"])
        df = df.sort_values("timestamp")

        # Cumulative equity
        df["cumulative_pnl"] = df["realized_pnl"].cumsum()
        df["equity"] = float(self._starting_cash) + df["cumulative_pnl"]

        result = df[["timestamp", "equity", "realized_pnl"]].copy()
        return result  # type: ignore[return-value]


# Global portfolio instance
_global_portfolio: Portfolio | None = None


def get_portfolio(config: PortfolioConfig | None = None) -> Portfolio:
    """Get or create the global portfolio instance."""
    global _global_portfolio
    if _global_portfolio is None:
        _global_portfolio = Portfolio(config or PortfolioConfig())
    return _global_portfolio


def reset_portfolio(config: PortfolioConfig | None = None) -> Portfolio:
    """Reset the global portfolio instance."""
    global _global_portfolio
    _global_portfolio = Portfolio(config or PortfolioConfig())
    return _global_portfolio
