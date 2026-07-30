"""Paper trading engine — executes trades on Deriv demo account in real-time.

Phase 3 of the demo-to-live pipeline:
    1. Connect to Deriv demo (PAT + OTP flow)
    2. Download historical candles for channel detection
    3. Subscribe to live ticks
    4. On each new candle, generate signal via RangeBreakStrategy
    5. If signal passes threshold → proposal → buy → monitor → sell
    6. Apply risk rules (Kelly dynamic, circuit breaker dual)
    7. Log every trade and generate daily report

Gate criteria for Phase 3:
    - 30+ trades minimum
    - Profitable over full period
    - No single day > -5% loss
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.analysis.recommender import Recommendation, RecommendationEngine
from src.connection.deriv_client import DerivClient
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.manager import RiskConfig, RiskManager
from src.strategies.base import Signal, SignalType
from src.strategies.range_break import RangeBreakConfig, RangeBreakStrategy

logger = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    """Record of a single paper trade executed on demo."""
    entry_time: datetime
    exit_time: datetime | None
    direction: str          # LONG (CALL) or SHORT (PUT)
    symbol: str
    entry_price: float
    exit_price: float
    stake_usd: float
    pnl_usd: float
    contract_id: str | int
    signal_score: float
    exit_reason: str        # TP, SL, TIME, MANUAL
    win: bool


@dataclass
class PaperTradingReport:
    """Aggregated paper trading session report."""
    started_at: datetime
    ended_at: datetime | None
    symbol: str
    initial_balance: float
    final_balance: float
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    max_drawdown: float
    max_daily_loss: float
    trades: list[PaperTrade] = field(default_factory=list)
    gate_passed: bool = False
    gate_failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=== Paper Trading Report ===",
            f"  Symbol:          {self.symbol}",
            f"  Started:         {self.started_at}",
            f"  Ended:           {self.ended_at or '—'}",
            f"  Initial balance: ${self.initial_balance:.2f}",
            f"  Final balance:   ${self.final_balance:.2f}",
            f"  Total trades:    {self.total_trades}",
            f"  Wins/Losses:     {self.wins}/{self.losses}",
            f"  Win rate:        {self.win_rate:.2%}",
            f"  Total P&L:       ${self.total_pnl:.2f}",
            f"  Max drawdown:    {self.max_drawdown:.2%}",
            f"  Max daily loss:  {self.max_daily_loss:.2%}",
            f"  Gate passed:     {'YES' if self.gate_passed else 'NO'}",
        ]
        if self.gate_failures:
            lines.append(f"  Gate failures:   {', '.join(self.gate_failures)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "symbol": self.symbol,
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "max_drawdown": self.max_drawdown,
            "max_daily_loss": self.max_daily_loss,
            "trades": [
                {
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "direction": t.direction,
                    "symbol": t.symbol,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "stake_usd": t.stake_usd,
                    "pnl_usd": t.pnl_usd,
                    "signal_score": t.signal_score,
                    "exit_reason": t.exit_reason,
                    "win": t.win,
                }
                for t in self.trades
            ],
            "gate_passed": self.gate_passed,
            "gate_failures": self.gate_failures,
        }


class PaperTrader:
    """Real-time paper trading engine for Deriv demo account.

    Flow:
        1. Connect + download warmup candles
        2. Subscribe to live ticks
        3. Build candles from ticks (1-min granularity)
        4. On candle close → generate_signal()
        5. If signal passes → proposal → buy → track → auto-sell on TP/SL/time
        6. Apply circuit breaker + Kelly dynamic sizing
        7. Generate daily report
    """

    def __init__(
        self,
        client: DerivClient,
        symbol: str = "R_100",
        strategy: RangeBreakStrategy | None = None,
        risk_config: RiskConfig | None = None,
        candle_granularity: int = 60,
        max_trades: int = 30,
        max_session_hours: float = 8.0,
    ) -> None:
        self.client = client
        self.symbol = symbol
        self.strategy = strategy or RangeBreakStrategy(
            symbol=symbol, config=RangeBreakConfig()
        )
        self.risk_config = risk_config or RiskConfig()
        self.risk_manager = RiskManager(self.risk_config)
        self.circuit_breaker = CircuitBreaker()
        self.recommender = RecommendationEngine(self.risk_config)
        self.candle_granularity = candle_granularity
        self.max_trades = max_trades
        self.max_session_hours = max_session_hours

        # State
        self.candles: pd.DataFrame = pd.DataFrame()
        self.open_trade: PaperTrade | None = None
        self.trades: list[PaperTrade] = []
        self.initial_balance: float = 0.0
        self.current_balance: float = 0.0
        self.peak_balance: float = 0.0
        self.max_drawdown: float = 0.0
        self.starting_day_balance: float = 0.0
        self.max_daily_loss: float = 0.0
        self._running = False

    async def run(self) -> PaperTradingReport:
        """Start paper trading session.

        Downloads warmup data, subscribes to ticks, and runs the trading loop
        until max_trades or max_session_hours is reached.
        """
        started_at = datetime.now(timezone.utc)
        logger.info("Paper trading session started at %s", started_at)

        # Step 1: Get balance (use one-shot, not subscribe)
        bal_resp = await self.client._send({"balance": 1, "subscribe": 0})
        self.initial_balance = float(
            bal_resp.get("balance", {}).get("balance", 10000)
        )
        self.current_balance = self.initial_balance
        self.peak_balance = self.initial_balance
        self.starting_day_balance = self.initial_balance
        self.risk_manager.reset_daily(self.initial_balance)
        logger.info("Initial balance: $%.2f", self.initial_balance)

        # Step 2: Download warmup candles (100+ for channel detection)
        from src.data.collector import DataCollector
        collector = DataCollector(self.client)
        self.candles = await collector.download_candles(
            symbol=self.symbol, count=200, granularity=self.candle_granularity
        )
        logger.info("Warmup: %d candles loaded", len(self.candles))

        # Step 3: Trading loop
        self._running = True
        last_candle_time = 0
        ticks_buffer: list[float] = []

        while self._running:
            # Check stop conditions
            elapsed_hours = (datetime.now(timezone.utc) - started_at).total_seconds() / 3600
            if len(self.trades) >= self.max_trades:
                logger.info("Max trades reached (%d). Stopping.", self.max_trades)
                break
            if elapsed_hours >= self.max_session_hours:
                logger.info("Max session hours reached (%.1f). Stopping.", elapsed_hours)
                break

            # Check circuit breaker
            cb_can, cb_reason = self._check_circuit_breaker()
            if not cb_can:
                logger.warning("Circuit breaker active: %s. Waiting 60s.", cb_reason)
                await asyncio.sleep(60)
                continue

            # Step 4: Check for new candle → generate signal
            new_candle = await self._get_latest_candle()
            if new_candle is not None:
                # Append new candle to buffer
                self.candles = pd.concat(
                    [self.candles, pd.DataFrame([new_candle])], ignore_index=True
                )
                # Trim to last 500 candles to avoid memory growth
                if len(self.candles) > 500:
                    self.candles = self.candles.iloc[-500:]

                # Generate signal on candle close
                signal = self.strategy.generate_signal(self.candles)

                if signal.type != SignalType.NO_SIGNAL and not self.open_trade:
                    # Solo abrir un trade si no hay uno en curso
                    await self._execute_signal(signal)
                elif signal.type != SignalType.NO_SIGNAL and self.open_trade:
                    logger.debug("Signal generated but trade already open — skipping")

            # Check open trade for exit
            if self.open_trade:
                await self._check_open_trade_exit()

            # Update balance periodically
            await self._update_balance()

            # Wait for next tick (poll every 5 seconds)
            await asyncio.sleep(5)

        # Close any remaining open trade
        if self.open_trade:
            await self._close_trade(self.open_trade, "MANUAL", 0.0)

        ended_at = datetime.now(timezone.utc)
        report = self._build_report(started_at, ended_at)
        self._save_report(report)
        return report

    def _check_circuit_breaker(self) -> tuple[bool, str]:
        """Check if circuit breaker allows trading."""
        can_cb, reason_cb = self.circuit_breaker.can_trade()
        can_rm, reason_rm = self.risk_manager.can_trade()
        if not can_cb:
            return False, reason_cb
        if not can_rm:
            return False, reason_rm
        return True, "OK"

    async def _get_latest_candle(self) -> dict | None:
        """Get the latest closed candle from Deriv.

        Returns a dict with epoch, open, high, low, close or None if no new candle.
        """
        try:
            resp = await self.client.ticks_history(
                self.symbol, count=1, style="candles",
                granularity=self.candle_granularity
            )
            candles_data = resp.get("candles", [])
            if not candles_data:
                return None

            c = candles_data[-1]
            epoch = int(c["epoch"])
            # Only return if it's a new candle we haven't seen
            if len(self.candles) > 0:
                last_epoch = int(self.candles.iloc[-1]["epoch"])
                if epoch <= last_epoch:
                    return None

            return {
                "epoch": epoch,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "datetime": datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error("Error getting latest candle: %s", e)
            return None

    async def _execute_signal(self, signal: Signal) -> None:
        """Execute a trading signal: proposal → buy → track."""
        # Get win probability from strategy
        win_prob = self.strategy.get_win_probability(signal)

        # Calculate position size (use dynamic Kelly)
        win_amount = abs(signal.take_profit - signal.entry_price)
        loss_amount = abs(signal.entry_price - signal.stop_loss)

        # Get confidence from signal metadata
        confidence = signal.confidence
        vol_multiplier = 1.0  # Could calculate from ATR ratio

        size = self.risk_manager.position_size_dynamic(
            capital=self.current_balance,
            win_probability=win_prob,
            win_amount=win_amount,
            loss_amount=loss_amount,
            confidence=confidence,
            volatility_multiplier=vol_multiplier,
        )

        if size <= 0:
            logger.warning("Kelly says no trade (size=0). Skipping signal.")
            return

        # Generate recommendation for logging
        rec = self.recommender.generate_recommendation(
            signal=signal,
            score=confidence,
            stake_usd=size,
            circuit_breaker=self.circuit_breaker,
            volatility_multiplier=vol_multiplier,
        )
        logger.info("RECOMMENDATION: %s", rec.to_string())

        # Build proposal params for Deriv
        contract_type = "CALL" if signal.type == SignalType.LONG else "PUT"
        duration_seconds = signal.duration_seconds

        proposal_params = {
            "amount": size,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": duration_seconds,
            "duration_unit": "s",
            "underlying_symbol": self.symbol,
        }

        try:
            # Step 1: Get proposal
            prop_resp = await self.client.proposal(proposal_params)
            if "error" in prop_resp:
                logger.error("Proposal error: %s", prop_resp["error"])
                return

            proposal_id = prop_resp.get("proposal", {}).get("id")
            if not proposal_id:
                logger.error("No proposal ID returned")
                return

            # Step 2: Buy contract
            buy_resp = await self.client.buy(proposal_id, size)
            if "error" in buy_resp:
                logger.error("Buy error: %s", buy_resp["error"])
                return

            contract_id = buy_resp.get("buy", {}).get("contract_id", "")
            # API nueva espera contract_id como integer
            try:
                contract_id = int(contract_id)
            except (ValueError, TypeError):
                pass
            entry_price = float(
                buy_resp.get("buy", {}).get("start_time", 0)
            )

            # Create trade record
            trade = PaperTrade(
                entry_time=datetime.now(timezone.utc),
                exit_time=None,
                direction=signal.type.value,
                symbol=self.symbol,
                entry_price=signal.entry_price,
                exit_price=0.0,
                stake_usd=size,
                pnl_usd=0.0,
                contract_id=contract_id,
                signal_score=confidence,
                exit_reason="",
                win=False,
            )
            self.open_trade = trade
            logger.info(
                "Trade OPENED: %s %s @ %.5f, stake=$%.2f, contract=%s",
                trade.direction, trade.symbol, signal.entry_price,
                size, contract_id,
            )

        except Exception as e:
            logger.error("Error executing signal: %s", e)

    async def _check_open_trade_exit(self) -> None:
        """Check if the open trade has completed (TP, SL, or time expiry)."""
        if not self.open_trade:
            return

        try:
            # API nueva: proposal_open_contract sin subscribe
            resp = await self.client._send({
                "proposal_open_contract": 1,
                "contract_id": self.open_trade.contract_id,
            })

            if "error" in resp:
                logger.error("Error checking contract: %s", resp["error"])
                return

            contract = resp.get("proposal_open_contract", {})
            is_sold = contract.get("is_sold", 0) or contract.get("is_expired", 0)

            if is_sold:
                # Trade closed
                exit_price = float(contract.get("exit_spot", contract.get("sell_spot", 0)))
                pnl = float(contract.get("profit", 0))
                exit_reason = "TP" if pnl > 0 else ("SL" if pnl < 0 else "TIME")

                await self._close_trade(self.open_trade, exit_reason, exit_price, pnl)

        except Exception as e:
            logger.error("Error checking open trade: %s", e)

    async def _close_trade(
        self,
        trade: PaperTrade,
        reason: str,
        exit_price: float,
        pnl: float = 0.0,
    ) -> None:
        """Close a trade and record it."""
        trade.exit_time = datetime.now(timezone.utc)
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.pnl_usd = pnl
        trade.win = pnl > 0

        self.trades.append(trade)
        self.current_balance += pnl
        self.risk_manager.record_trade(pnl, self.current_balance)
        self.circuit_breaker.update(
            loss=not trade.win,
            current_balance=self.current_balance,
            starting_balance=self.starting_day_balance,
        )

        # Track drawdown
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance
        dd = (self.peak_balance - self.current_balance) / self.peak_balance if self.peak_balance > 0 else 0
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        # Track daily loss
        daily_loss = (self.starting_day_balance - self.current_balance) / self.starting_day_balance if self.starting_day_balance > 0 else 0
        if daily_loss > self.max_daily_loss:
            self.max_daily_loss = daily_loss

        # Reset daily if needed
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.risk_manager.today and self.risk_manager.today.date != today:
            self.risk_manager.reset_daily(self.current_balance)
            self.starting_day_balance = self.current_balance

        logger.info(
            "Trade CLOSED: %s %s | P&L=$%.2f | Reason=%s | Balance=$%.2f",
            trade.direction, trade.symbol, pnl, reason, self.current_balance,
        )

        self.open_trade = None

    async def _update_balance(self) -> None:
        """Refresh balance from Deriv (skip if already subscribed)."""
        try:
            # Deriv rejects duplicate subscribe — use one-shot (subscribe: 0)
            resp = await self.client._send({"balance": 1, "subscribe": 0})
            bal = float(resp.get("balance", {}).get("balance", self.current_balance))
            if bal != self.current_balance:
                self.current_balance = bal
        except Exception:
            pass

    def _build_report(
        self, started_at: datetime, ended_at: datetime
    ) -> PaperTradingReport:
        """Build final paper trading report."""
        total = len(self.trades)
        wins = sum(1 for t in self.trades if t.win)
        losses = total - wins
        win_rate = wins / total if total > 0 else 0
        total_pnl = sum(t.pnl_usd for t in self.trades)

        report = PaperTradingReport(
            started_at=started_at,
            ended_at=ended_at,
            symbol=self.symbol,
            initial_balance=self.initial_balance,
            final_balance=self.current_balance,
            total_trades=total,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            total_pnl=total_pnl,
            max_drawdown=self.max_drawdown,
            max_daily_loss=self.max_daily_loss,
            trades=self.trades,
        )

        # Evaluate Phase 3 gates
        if total >= 30:
            if total_pnl > 0:
                if self.max_daily_loss < 0.05:
                    report.gate_passed = True
                else:
                    report.gate_failures.append(
                        f"Max daily loss {self.max_daily_loss:.2%} >= 5%"
                    )
            else:
                report.gate_failures.append(
                    f"Not profitable: P&L=${total_pnl:.2f}"
                )
        else:
            report.gate_failures.append(
                f"Insufficient trades: {total} < 30 minimum"
            )

        return report

    def _save_report(self, report: PaperTradingReport) -> None:
        """Save report to JSON file."""
        report_path = Path("reports/paper_trading")
        report_path.mkdir(parents=True, exist_ok=True)
        filename = f"paper_trading_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_path / filename, "w") as f:
            json.dump(report.to_dict(), f, indent=2, default=str)
        logger.info("Report saved to %s", report_path / filename)
