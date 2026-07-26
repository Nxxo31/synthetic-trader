"""Risk manager — enforces position sizing, circuit breakers, and daily limits."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """Risk management configuration — NON-NEGOTIABLE."""
    max_risk_per_trade: float = 0.015      # 1.5% of capital
    max_daily_drawdown: float = 0.05       # 5% daily loss limit
    max_trades_per_day: int = 8            # Max 8 trades per day
    circuit_breaker_losses: int = 5        # Halt after 5 consecutive losses
    kelly_fraction: float = 0.25           # Quarter-Kelly
    initial_capital: float = 10000.0


@dataclass
class DailyStats:
    """Tracks trading stats for the current day."""
    date: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    starting_balance: float = 0.0
    current_balance: float = 0.0
    consecutive_losses: int = 0
    halted: bool = False
    halt_reason: str = ""


class RiskManager:
    """
    Enforces risk rules on every trade.

    Rules (NON-NEGOTIABLE):
    1. Max 1.5% risk per trade
    2. Max 5% daily drawdown
    3. Max 8 trades per day
    4. Circuit breaker: halt after 5 consecutive losses
    5. Quarter-Kelly position sizing
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self.today: DailyStats | None = None
        self._is_halted = False
        self._halt_reason = ""

    def reset_daily(self, balance: float) -> None:
        """Reset daily stats. Call at start of each trading day."""
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        self.today = DailyStats(
            date=today_str,
            starting_balance=balance,
            current_balance=balance,
        )
        self._is_halted = False
        self._halt_reason = ""
        logger.info("Daily risk reset. Balance: %.2f USD", balance)

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed under current risk rules."""
        if self._is_halted:
            return False, f"HALTED: {self._halt_reason}"

        if self.today is None:
            return False, "Daily stats not initialized. Call reset_daily() first."

        if self.today.trades >= self.config.max_trades_per_day:
            return False, f"Max trades per day reached ({self.config.max_trades_per_day})"

        drawdown = (self.today.starting_balance - self.today.current_balance) / self.today.starting_balance
        if drawdown >= self.config.max_daily_drawdown:
            self._halt(f"Daily drawdown limit reached: {drawdown:.2%}")
            return False, self._halt_reason

        if self.today.consecutive_losses >= self.config.circuit_breaker_losses:
            self._halt(f"Circuit breaker: {self.today.consecutive_losses} consecutive losses")
            return False, self._halt_reason

        return True, "OK"

    def position_size(
        self,
        capital: float,
        win_probability: float,
        win_amount: float,
        loss_amount: float,
    ) -> float:
        """
        Calculate position size using Kelly Criterion (quarter-Kelly).

        Kelly = (p * b - q) / b
        where:
            p = win probability
            q = loss probability = 1 - p
            b = win/loss ratio = win_amount / loss_amount

        Quarter-Kelly = Kelly * 0.25 (reduces variance)
        Capped at max_risk_per_trade * capital.

        Args:
            capital: current account balance
            win_probability: estimated probability of winning
            win_amount: expected gain if trade wins
            loss_amount: expected loss if trade loses

        Returns:
            Position size in USD
        """
        p = win_probability
        q = 1.0 - p
        b = win_amount / loss_amount if loss_amount > 0 else 0

        kelly = (p * b - q) / b if b > 0 else 0
        quarter_kelly = kelly * self.config.kelly_fraction

        # Cap at max_risk_per_trade
        max_risk = self.config.max_risk_per_trade * capital
        size = min(quarter_kelly * capital, max_risk)

        # Negative Kelly = no edge, don't trade
        if size <= 0:
            logger.warning("Kelly <= 0 (p=%.3f, b=%.3f). No edge detected.", p, b)
            return 0.0

        logger.info(
            "Position size: %.2f USD (Kelly=%.4f, QK=%.4f, cap=%.2f)",
            size, kelly, quarter_kelly, max_risk,
        )
        return round(size, 2)

    def record_trade(self, pnl: float, new_balance: float) -> None:
        """Record a completed trade."""
        if self.today is None:
            logger.error("record_trade called before reset_daily")
            return

        self.today.trades += 1
        self.today.pnl += pnl
        self.today.current_balance = new_balance

        if pnl >= 0:
            self.today.wins += 1
            self.today.consecutive_losses = 0
        else:
            self.today.losses += 1
            self.today.consecutive_losses += 1

        logger.info(
            "Trade recorded: P&L=%.2f, Total=%d trades, W/L=%d/%d, Balance=%.2f",
            pnl, self.today.trades, self.today.wins, self.today.losses, new_balance,
        )

        # Check circuit breaker after recording
        can, reason = self.can_trade()
        if not can and "HALTED" in reason:
            logger.warning("Circuit breaker triggered: %s", reason)

    def daily_report(self) -> dict:
        """Generate daily risk report."""
        if self.today is None:
            return {"error": "No daily stats"}

        drawdown = (self.today.starting_balance - self.today.current_balance) / self.today.starting_balance
        win_rate = self.today.wins / self.today.trades if self.today.trades > 0 else 0

        return {
            "date": self.today.date,
            "trades": self.today.trades,
            "wins": self.today.wins,
            "losses": self.today.losses,
            "win_rate": round(win_rate, 4),
            "pnl": round(self.today.pnl, 2),
            "drawdown": round(drawdown, 4),
            "consecutive_losses": self.today.consecutive_losses,
            "halted": self._is_halted,
            "halt_reason": self._halt_reason,
            "starting_balance": self.today.starting_balance,
            "current_balance": self.today.current_balance,
        }

    def _halt(self, reason: str) -> None:
        """Halt trading."""
        self._is_halted = True
        self._halt_reason = reason
        if self.today:
            self.today.halted = True
            self.today.halt_reason = reason
        logger.critical("TRADING HALTED: %s", reason)
