"""Backtest engine — replays historical data and evaluates strategy performance.

Incluye:
- Spread y slippage simulation (configurable)
- Latency simulation: delay 100-500ms entre señal y ejecución
- Circuit breaker dual integration
- Kelly dinámico (confidence + volatility_multiplier)
- Gate evaluation: Sharpe>1.2, DD<12%, WR>52%, Expectancy>0.15R
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import logging
import random
import numpy as np
import pandas as pd

from src.strategies.base import Signal, SignalType, Strategy
from src.risk.manager import RiskManager, RiskConfig
from src.risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Record of a single backtest trade."""
    entry_time: int          # epoch
    exit_time: int           # epoch
    direction: str           # LONG or SHORT
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    pnl: float               # in USD
    pnl_pct: float           # as fraction of capital risked
    duration: int            # seconds
    win: bool
    exit_reason: str         # TP, SL, TIME


@dataclass
class BacktestResult:
    """Aggregated backtest metrics."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0  # in R multiples
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    gate_passed: bool = False
    gate_failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=== Backtest Results ===",
            f"  Total trades:    {self.total_trades}",
            f"  Win rate:        {self.win_rate:.2%}",
            f"  Total P&L:       ${self.total_pnl:.2f}",
            f"  Avg P&L/trade:   ${self.avg_pnl:.2f}",
            f"  Max drawdown:    {self.max_drawdown:.2%}",
            f"  Sharpe ratio:    {self.sharpe_ratio:.3f}",
            f"  Profit factor:   {self.profit_factor:.3f}",
            f"  Expectancy:      {self.expectancy:.3f}R",
            f"  Gate passed:     {'YES' if self.gate_passed else 'NO'}",
        ]
        if self.gate_failures:
            lines.append(f"  Gate failures:   {', '.join(self.gate_failures)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Convert result to a dictionary suitable for JSON serialization."""
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "avg_pnl": self.avg_pnl,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "trades": [
                {
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "direction": t.direction,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "stop_loss": t.stop_loss,
                    "take_profit": t.take_profit,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "duration": t.duration,
                    "win": t.win,
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
            "equity_curve": self.equity_curve,
            "gate_passed": self.gate_passed,
            "gate_failures": self.gate_failures,
        }


class BacktestEngine:
    """
    Replays historical candle data through a strategy and simulates trades.

    Includes:
    - Spread and slippage simulation
    - Walk-forward support (train/test split)
    - Risk manager integration
    - Gate evaluation (Sharpe, DD, win rate, expectancy)
    """

    def __init__(
        self,
        strategy: Strategy,
        risk_config: RiskConfig,
        spread_pips: float = 0.3,
        slippage_pips: float = 0.15,
        latency_ms_min: int = 100,
        latency_ms_max: int = 500,
        use_dynamic_kelly: bool = True,
        use_circuit_breaker: bool = True,
        candle_granularity_s: int = 60,
    ) -> None:
        """Initialize backtest engine.

        Args:
            strategy: Strategy to backtest
            risk_config: Risk configuration
            spread_pips: Spread cost per trade in pips
            slippage_pips: Base slippage in pips
            latency_ms_min: Minimum simulated latency (ms) between signal and execution
            latency_ms_max: Maximum simulated latency (ms)
            use_dynamic_kelly: Use position_size_dynamic (confidence + vol multiplier)
            use_circuit_breaker: Use dual circuit breaker (consecutive losses + DD)
            candle_granularity_s: Seconds per candle (for latency-to-candles conversion)
        """
        self.strategy = strategy
        self.risk_manager = RiskManager(risk_config)
        self.spread_pips = spread_pips
        self.slippage_pips = slippage_pips
        self.latency_ms_min = latency_ms_min
        self.latency_ms_max = latency_ms_max
        self.use_dynamic_kelly = use_dynamic_kelly
        self.use_circuit_breaker = use_circuit_breaker
        self.candle_granularity_s = candle_granularity_s
        # Dual circuit breaker (defaults: 3 consecutive losses, 5% daily DD)
        self.circuit_breaker: CircuitBreaker | None = (
            CircuitBreaker(CircuitBreakerConfig(
                consecutive_losses_threshold=3,
                daily_drawdown_threshold=risk_config.max_daily_drawdown,
            ))
            if use_circuit_breaker
            else None
        )

    def run(self, data: pd.DataFrame, initial_capital: float = 10000.0) -> BacktestResult:
        """
        Run backtest on historical data.

        Args:
            data: DataFrame with columns: epoch, open, high, low, close
            initial_capital: starting balance

        Returns:
            BacktestResult with all trades and metrics
        """
        result = BacktestResult()
        capital = initial_capital
        self.risk_manager.reset_daily(capital)

        equity = [capital]
        peak = capital
        max_dd = 0.0

        # Rolling window: start after min_channel_ticks
        min_window = 50
        if len(data) < min_window + 1:
            logger.warning("Not enough data for backtest: %d candles", len(data))
            return result

        # Track day changes for daily risk reset
        last_day = None

        for i in range(min_window, len(data) - 1):
            # Check if we entered a new day — reset daily risk
            current_epoch = int(data.iloc[i]["epoch"])
            current_day = pd.Timestamp(current_epoch, unit="s").strftime("%Y-%m-%d")
            if current_day != last_day:
                self.risk_manager.reset_daily(capital)
                last_day = current_day

            window = data.iloc[:i + 1]
            signal = self.strategy.generate_signal(window)

            if signal.type == SignalType.NO_SIGNAL:
                continue

            # Check risk — circuit breaker dual (if enabled) supersedes risk manager
            if self.circuit_breaker is not None:
                cb_can, cb_reason = self.circuit_breaker.can_trade()
                if not cb_can:
                    logger.debug("Trade skipped by circuit breaker: %s", cb_reason)
                    continue
            can, reason = self.risk_manager.can_trade()
            if not can:
                logger.debug("Trade skipped: %s", reason)
                continue

            # Calculate position size — Kelly dinámico (confidence + vol multiplier)
            win_prob = self.strategy.get_win_probability(signal)
            win_amount = abs(signal.take_profit - signal.entry_price)
            loss_amount = abs(signal.entry_price - signal.stop_loss)

            if self.use_dynamic_kelly and signal.confidence > 0:
                # Volatility multiplier from signal metadata (ATR ratio)
                metadata = signal.metadata or {}
                atr_ratio = metadata.get("atr_ratio", 1.0)
                vol_mult = 1.0 + max(0.0, (atr_ratio - 1.0))
                size = self.risk_manager.position_size_dynamic(
                    capital, win_prob, win_amount, loss_amount,
                    confidence=signal.confidence,
                    volatility_multiplier=vol_mult,
                )
            else:
                size = self.risk_manager.position_size(
                    capital, win_prob, win_amount, loss_amount
                )

            if size <= 0:
                continue

            # Simulate trade — find exit in subsequent candles
            trade = self._simulate_trade(
                signal=signal,
                data=data,
                entry_index=i,
                size=size,
                capital=capital,
            )

            if trade is None:
                continue

            # Apply spread and slippage costs
            cost = (self.spread_pips + self.slippage_pips) * 0.0001 * size
            trade.pnl -= cost
            capital += trade.pnl

            # Record trade — update risk manager + circuit breaker
            self.risk_manager.record_trade(trade.pnl, capital)
            if self.circuit_breaker is not None:
                self.circuit_breaker.update(
                    loss=trade.pnl < 0,
                    current_balance=capital,
                    starting_balance=self.risk_manager.today.starting_balance if self.risk_manager.today else capital,
                )
            result.trades.append(trade)
            equity.append(capital)

            # Track drawdown
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # Calculate metrics
        self._compute_metrics(result, equity, max_dd, initial_capital)

        logger.info(
            "Backtest complete: %d trades, P&L=%.2f, Sharpe=%.3f",
            result.total_trades,
            result.total_pnl,
            result.sharpe_ratio,
        )

        return result

    def _simulate_trade(
        self,
        signal: Signal,
        data: pd.DataFrame,
        entry_index: int,
        size: float,
        capital: float,
    ) -> Trade | None:
        """Simulate a single trade from entry to TP/SL/time exit.

        Incluye latency simulation: delay aleatorio entre señal y ejecución
        (100-500ms por defecto). Si el delay excede la granularity del candle,
        la entrada se ejecuta en la siguiente candle con slippage adverso.

        Args:
            signal: Trading signal to execute
            data: OHLCV DataFrame
            entry_index: Index of entry candle in data
            size: Position size in USD
            capital: Current account balance

        Returns:
            Completed trade or None if no exit found
        """
        # Simulate latency: delay between signal and execution (100-500ms)
        latency_ms = random.randint(self.latency_ms_min, self.latency_ms_max)
        latency_slippage_pips = (latency_ms / 1000.0) * 2.0  # ~2 pips per second of delay
        price_tick = 0.01  # minimum tick for synthetic indices

        # If latency exceeds candle granularity, entry moves to next candle
        execution_index = entry_index
        if latency_ms > self.candle_granularity_s * 1000:
            execution_index = entry_index + 1
            if execution_index >= len(data):
                return None  # no candle to execute on

        latency_slippage = latency_slippage_pips * price_tick

        entry_candle = data.iloc[execution_index]
        entry_time = int(entry_candle["epoch"])

        # Apply slippage against the trader (worse entry price)
        if signal.type == SignalType.LONG:
            entry_price = signal.entry_price + latency_slippage
        else:  # SHORT
            entry_price = signal.entry_price - latency_slippage

        # R-multiple denominator: the dollar risk per unit at entry
        sl_distance = abs(entry_price - signal.stop_loss)
        risk_amount = size * (sl_distance / entry_price) if entry_price > 0 else 0

        # Guard against zero risk (shouldn't happen with valid channels)
        if risk_amount <= 0:
            risk_amount = size

        def r_multiple(pnl_usd: float) -> float:
            """Convert dollar P&L to R-multiple of the trade's risk."""
            return pnl_usd / risk_amount if risk_amount > 0 else 0.0

        # Look ahead for exit
        max_candles = signal.duration_seconds // 60  # Assuming 1-minute candles

        for j in range(entry_index + 1, min(entry_index + max_candles + 1, len(data))):
            candle = data.iloc[j]
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])
            epoch = int(candle["epoch"])

            if signal.type == SignalType.LONG:
                # Check SL first (worst case)
                if low <= signal.stop_loss:
                    pnl = -size * (abs(entry_price - signal.stop_loss) / entry_price)
                    return Trade(
                        entry_time, epoch, "LONG", entry_price,
                        signal.stop_loss, signal.stop_loss, signal.take_profit,
                        pnl, r_multiple(pnl), epoch - entry_time, False, "SL"
                    )

                # Check TP
                if high >= signal.take_profit:
                    pnl = size * (abs(signal.take_profit - entry_price) / entry_price)
                    return Trade(
                        entry_time, epoch, "LONG", entry_price,
                        signal.take_profit, signal.stop_loss, signal.take_profit,
                        pnl, r_multiple(pnl), epoch - entry_time, True, "TP"
                    )

            elif signal.type == SignalType.SHORT:
                # Check SL first
                if high >= signal.stop_loss:
                    pnl = -size * (abs(signal.stop_loss - entry_price) / entry_price)
                    return Trade(
                        entry_time, epoch, "SHORT", entry_price,
                        signal.stop_loss, signal.stop_loss, signal.take_profit,
                        pnl, r_multiple(pnl), epoch - entry_time, False, "SL"
                    )

                # Check TP
                if low <= signal.take_profit:
                    pnl = size * (abs(entry_price - signal.take_profit) / entry_price)
                    return Trade(
                        entry_time, epoch, "SHORT", entry_price,
                        signal.take_profit, signal.stop_loss, signal.take_profit,
                        pnl, r_multiple(pnl), epoch - entry_time, True, "TP"
                    )

            # Time exit
            if epoch - entry_time >= signal.duration_seconds:
                pnl = size * ((close - entry_price) / entry_price) * (1 if signal.type == SignalType.LONG else -1)
                return Trade(
                    entry_time, epoch, signal.type.value, entry_price,
                    close, signal.stop_loss, signal.take_profit,
                    pnl, r_multiple(pnl), epoch - entry_time, pnl > 0, "TIME"
                )

        return None

    def _compute_metrics(
        self,
        result: BacktestResult,
        equity: list[float],
        max_dd: float,
        initial_capital: float,
    ) -> None:
        """Calculate all performance metrics and evaluate gates."""
        result.equity_curve = equity
        result.total_trades = len(result.trades)
        result.wins = sum(1 for t in result.trades if t.win)
        result.losses = result.total_trades - result.wins
        result.win_rate = result.wins / result.total_trades if result.total_trades > 0 else 0.0
        result.total_pnl = float(sum(t.pnl for t in result.trades))
        result.avg_pnl = result.total_pnl / result.total_trades if result.total_trades > 0 else 0.0
        result.max_drawdown = max_dd

        # Sharpe ratio (simplified — assumes risk-free = 0)
        if len(equity) > 1:
            returns = np.diff(equity) / equity[:-1]
            if np.std(returns) > 0:
                # Annualized: sqrt(trades_per_year) — approx 252 daily, ~5000 hourly
                result.sharpe_ratio = float(np.mean(returns) / np.std(returns) * np.sqrt(252))

        # Profit factor
        gross_profit = sum(t.pnl for t in result.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in result.trades if t.pnl < 0))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0

        # Expectancy in R — cast np.float64 → float for dataclass field
        wins_r = [t.pnl_pct for t in result.trades if t.win]
        losses_r = [t.pnl_pct for t in result.trades if not t.win]
        avg_win = float(np.mean(wins_r)) if wins_r else 0.0
        avg_loss = float(np.mean(losses_r)) if losses_r else 0.0
        result.expectancy = float(
            result.win_rate * avg_win - (1 - result.win_rate) * avg_loss
        )

        # Gate evaluation
        self._evaluate_gates(result)

    def _evaluate_gates(self, result: BacktestResult) -> None:
        """Evaluate if backtest passes gate criteria."""
        gates = {
            "min_sharpe": ("sharpe_ratio", 1.2, ">"),
            "max_drawdown": ("max_drawdown", 0.12, "<"),
            "min_win_rate": ("win_rate", 0.52, ">"),
            "min_expectancy": ("expectancy", 0.15, ">"),
        }

        for name, (field_name, threshold, op) in gates.items():
            value = getattr(result, field_name)
            if op == ">" and value <= threshold:
                result.gate_failures.append(f"{name}: {value:.3f} <= {threshold}")
            elif op == "<" and value >= threshold:
                result.gate_failures.append(f"{name}: {value:.3f} >= {threshold}")

        result.gate_passed = len(result.gate_failures) == 0