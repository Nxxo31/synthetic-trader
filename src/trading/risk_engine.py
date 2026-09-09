"""Risk Engine — central validation for all orders.

All orders must pass through RiskEngine before execution.
Implements PROJECT.md risk rules:
- 2% max risk per trade (configurable)
- 5% max daily drawdown
- 10 trades max per day
- Circuit breaker after consecutive losses
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)


class RiskCheckResult(str, Enum):
    """Result of a risk check."""

    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


@dataclass
class RiskCheck:
    """Result of a single risk validation."""

    check_name: str
    result: RiskCheckResult
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class RiskValidationResult:
    """Aggregated result of all risk checks."""

    passed: bool
    checks: list[RiskCheck]
    rejection_reason: str | None = None

    def add_check(self, check: RiskCheck) -> None:
        self.checks.append(check)
        if check.result == RiskCheckResult.FAIL:
            self.passed = False
            if self.rejection_reason is None:
                self.rejection_reason = check.message


@dataclass
class RiskConfig:
    """Risk configuration parameters."""

    max_risk_per_trade: Decimal = Decimal("0.02")  # 2% of capital
    max_daily_drawdown: Decimal = Decimal("0.05")  # 5% daily loss limit
    max_trades_per_day: int = 10
    circuit_breaker_losses: int = 5  # Halt after N consecutive losses
    kelly_fraction: Decimal = Decimal("0.25")  # Quarter-Kelly
    initial_capital: Decimal = Decimal("10000.0")
    tradable_balance_ratio: Decimal = Decimal("0.99")  # 1% reserved for fees
    max_open_positions: int = 5  # Max concurrent positions


@dataclass
class DailyStats:
    """Tracks trading stats for the current day."""

    date: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: Decimal = Decimal("0")
    starting_balance: Decimal = Decimal("0")
    current_balance: Decimal = Decimal("0")
    consecutive_losses: int = 0
    max_drawdown_today: Decimal = Decimal("0")


class RiskEngine:
    """Central risk validation engine.

    All orders pass through here before being submitted to the broker.
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self.today: DailyStats | None = None
        self._is_halted = False
        self._halt_reason = ""
        self._total_consecutive_losses = 0

    def reset_daily(self, balance: Decimal) -> None:
        """Reset daily stats. Call at start of each trading day."""
        today_str = datetime.now(UTC).strftime("%Y-%m-%d")
        self.today = DailyStats(
            date=today_str,
            starting_balance=balance,
            current_balance=balance,
        )
        self._is_halted = False
        self._halt_reason = ""
        self._total_consecutive_losses = 0
        logger.info("Daily risk reset. Balance: %.2f", balance)

    def can_trade(self) -> RiskValidationResult:
        """Check if trading is allowed under current risk rules."""
        result = RiskValidationResult(passed=True, checks=[])

        if self._is_halted:
            result.add_check(RiskCheck(
                check_name="halted",
                result=RiskCheckResult.FAIL,
                message=f"HALTED: {self._halt_reason}",
            ))
            return result

        if self.today is None:
            result.add_check(RiskCheck(
                check_name="daily_stats_not_initialized",
                result=RiskCheckResult.FAIL,
                message="Daily stats not initialized. Call reset_daily() first.",
            ))
            return result

        # Check max trades per day
        if self.today.trades >= self.config.max_trades_per_day:
            result.add_check(RiskCheck(
                check_name="max_trades_per_day",
                result=RiskCheckResult.FAIL,
                message=f"Max trades per day reached ({self.config.max_trades_per_day})",
                details={"trades_today": self.today.trades, "limit": self.config.max_trades_per_day},
            ))

        # Check daily drawdown
        if self.today.starting_balance > 0:
            drawdown = (self.today.starting_balance - self.today.current_balance) / self.today.starting_balance
            if drawdown >= self.config.max_daily_drawdown:
                self._halt_trading(f"Daily drawdown limit exceeded: {drawdown:.2%}")
                result.add_check(RiskCheck(
                    check_name="daily_drawdown",
                    result=RiskCheckResult.FAIL,
                    message=f"Daily drawdown limit exceeded: {drawdown:.2%}",
                    details={"drawdown": float(drawdown), "limit": float(self.config.max_daily_drawdown)},
                ))

        # Check circuit breaker
        if self._total_consecutive_losses >= self.config.circuit_breaker_losses:
            self._halt_trading(f"Circuit breaker: {self._total_consecutive_losses} consecutive losses")
            result.add_check(RiskCheck(
                check_name="circuit_breaker",
                result=RiskCheckResult.FAIL,
                message=f"Circuit breaker triggered: {self._total_consecutive_losses} consecutive losses",
                details={"consecutive_losses": self._total_consecutive_losses, "threshold": self.config.circuit_breaker_losses},
            ))

        return result

    def validate_order(
        self,
        side: str,  # "BUY" or "SELL"
        symbol: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        stake: Decimal,
        current_balance: Decimal,
    ) -> RiskValidationResult:
        """Validate a proposed order against all risk rules."""
        result = self.can_trade()

        if not result.passed:
            return result

        # Check risk per trade
        if entry_price > 0 and stop_loss > 0:
            risk_amount = abs(entry_price - stop_loss) * stake / entry_price
            max_risk = current_balance * self.config.max_risk_per_trade
            if risk_amount > max_risk:
                result.add_check(RiskCheck(
                    check_name="risk_per_trade",
                    result=RiskCheckResult.FAIL,
                    message=f"Risk per trade exceeded: {risk_amount:.2f} > {max_risk:.2f} (max {float(self.config.max_risk_per_trade)*100:.1f}%)",
                    details={
                        "risk_amount": float(risk_amount),
                        "max_allowed": float(max_risk),
                        "entry_price": float(entry_price),
                        "stop_loss": float(stop_loss),
                        "stake": float(stake),
                    },
                ))

        # Check stake size
        max_stake = current_balance * self.config.tradable_balance_ratio
        if stake > max_stake:
            result.add_check(RiskCheck(
                check_name="stake_too_large",
                result=RiskCheckResult.FAIL,
                message=f"Stake exceeds tradable balance: {stake:.2f} > {max_stake:.2f}",
                details={"stake": float(stake), "max_allowed": float(max_stake)},
            ))

        # Check stake is not too small (minimum practical size)
        min_stake = Decimal("0.35")  # Deriv minimum
        if stake < min_stake:
            result.add_check(RiskCheck(
                check_name="stake_too_small",
                result=RiskCheckResult.WARNING,
                message=f"Stake below minimum practical size: {stake:.2f} < {min_stake}",
                details={"stake": float(stake), "min_recommended": float(min_stake)},
            ))

        return result

    def record_fill(
        self,
        pnl: Decimal,
        is_win: bool,
        new_balance: Decimal,
    ) -> None:
        """Record a filled trade and update daily stats."""
        if self.today is None:
            return

        self.today.trades += 1
        self.today.pnl += pnl
        self.today.current_balance = new_balance

        if is_win:
            self.today.wins += 1
            self._total_consecutive_losses = 0
        else:
            self.today.losses += 1
            self._total_consecutive_losses += 1

        # Update max drawdown
        if self.today.starting_balance > 0:
            drawdown = (self.today.starting_balance - self.today.current_balance) / self.today.starting_balance
            if drawdown > self.today.max_drawdown_today:
                self.today.max_drawdown_today = drawdown

        logger.info(
            "Trade recorded: %s P&L=%.2f Balance=%.2f Trades=%d DD=%.2f%%",
            "WIN" if is_win else "LOSS",
            pnl,
            new_balance,
            self.today.trades,
            float(self.today.max_drawdown_today * 100),
        )

    def _halt_trading(self, reason: str) -> None:
        """Halt all trading."""
        self._is_halted = True
        self._halt_reason = reason
        logger.warning("TRADING HALTED: %s", reason)

    def get_status(self) -> dict:
        """Get current risk engine status."""
        return {
            "halted": self._is_halted,
            "halt_reason": self._halt_reason,
            "daily_stats": {
                "date": self.today.date if self.today else None,
                "trades": self.today.trades if self.today else 0,
                "wins": self.today.wins if self.today else 0,
                "losses": self.today.losses if self.today else 0,
                "pnl": float(self.today.pnl) if self.today else 0,
                "starting_balance": float(self.today.starting_balance) if self.today else 0,
                "current_balance": float(self.today.current_balance) if self.today else 0,
                "consecutive_losses": self.today.consecutive_losses if self.today else 0,
                "max_drawdown_today": float(self.today.max_drawdown_today) if self.today else 0,
            },
            "total_consecutive_losses": self._total_consecutive_losses,
            "config": {
                "max_risk_per_trade": float(self.config.max_risk_per_trade),
                "max_daily_drawdown": float(self.config.max_daily_drawdown),
                "max_trades_per_day": self.config.max_trades_per_day,
                "circuit_breaker_losses": self.config.circuit_breaker_losses,
            },
        }

    def position_size_dynamic(
        self,
        capital: float,
        win_probability: float,
        win_amount: float,
        loss_amount: float,
        confidence: float = 1.0,
        volatility_multiplier: float = 1.0,
    ) -> float:
        """Calculate dynamic position size using Kelly criterion with adjustments.

        Args:
            capital: Capital base for sizing (typically daily surplus)
            win_probability: Probability of winning (0-1)
            win_amount: Expected win amount per unit stake
            loss_amount: Expected loss amount per unit stake
            confidence: Signal confidence score (0-1)
            volatility_multiplier: Volatility adjustment factor (>=1.0 reduces size)

        Returns:
            Position size in USD, or 0 if no edge
        """
        if capital <= 0:
            return 0.0

        # Basic Kelly: f = p - q/b where b = win/loss
        if loss_amount <= 0:
            return 0.0

        b = win_amount / loss_amount
        p = win_probability
        q = 1 - p

        # Kelly fraction
        kelly = p - q / b
        if kelly <= 0:
            return 0.0

        # Apply quarter-Kelly and confidence
        adjusted_kelly = kelly * float(self.config.kelly_fraction) * confidence

        # Apply volatility multiplier (reduces size in high vol)
        adjusted_kelly = adjusted_kelly / max(1.0, volatility_multiplier)

        # Calculate stake
        stake = capital * adjusted_kelly

        # Cap at max risk per trade
        max_risk = capital * float(self.config.max_risk_per_trade)
        if stake > max_risk:
            stake = max_risk

        # Minimum stake
        min_stake = 0.35  # Deriv minimum
        if stake < min_stake:
            return 0.0

        return round(stake, 2)


# Convenience function for quick validation
def quick_validate(
    side: str,
    symbol: str,
    entry_price: float,
    stop_loss: float,
    stake: float,
    balance: float,
    config: RiskConfig | None = None,
) -> RiskValidationResult:
    """Quick one-off validation without engine state."""
    engine = RiskEngine(config or RiskConfig())
    engine.reset_daily(Decimal(str(balance)))
    return engine.validate_order(
        side=side,
        symbol=symbol,
        entry_price=Decimal(str(entry_price)),
        stop_loss=Decimal(str(stop_loss)),
        stake=Decimal(str(stake)),
        current_balance=Decimal(str(balance)),
    )
