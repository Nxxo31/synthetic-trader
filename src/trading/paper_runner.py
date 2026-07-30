"""Paper trading engine — ejecuta la estrategia en cuenta demo Deriv.

Flujo:
    1. Conecta a Deriv (cuenta demo $10,000)
    2. Baja 5000 candles históricas para warmup del strategy
    3. Se subscribe a ticks live del símbolo
    4. Acumula ticks en candles de 1 minuto
    5. Cuando hay nuevas candles, genera signal
    6. Si signal es válida (score ≥ threshold) y circuit breaker OK:
       a. Calcula stake con Kelly dinámico
       b. Envía proposal a Deriv
       c. Ejecuta buy con stake
       d. Monitorea hasta TP/SL/time exit
       e. Registra trade en JSON
    7. Reporta estado al dashboard via WebSocket

Usage:
    python -m src.main paper
    o
    python -m src.trading.paper_runner --symbol RB100
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.connection.deriv_client import DerivClient, DerivConfig, DerivAPIError
from src.strategies.base import Signal, SignalType
from src.strategies.range_break import RangeBreakStrategy, RangeBreakConfig
from src.analysis.signal_scorer import SignalScorer
from src.risk.manager import RiskManager, RiskConfig
from src.risk.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from src.analysis.recommender import Recommender

logger = logging.getLogger(__name__)

# Cuántas candles históricas para warmup
WARMUP_CANDLES = 500
# Threshold del scorer (optimizado en Fase 1)
SCORE_THRESHOLD = 0.50
# Granularidad de candles (segundos)
CANDLE_GRANULARITY = 60

# Rutas de archivos en tiempo real para compartir estado con el API y WebSocket
REALTIME_STATE_FILE = "/home/sebas/proyectos/synthetic-trader/realtime/paper_state.json"
EQUITY_FILE = "/home/sebas/proyectos/synthetic-trader/realtime/equity.jsonl"
TRADES_FILE = "/home/sebas/proyectos/synthetic-trader/realtime/trades.jsonl"


@dataclass
class PaperTrade:
    """Registro de un trade paper ejecutado."""
    timestamp: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    stake: float
    confidence: float
    score: float
    contract_id: str = ""
    exit_price: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""
    duration_seconds: int = 0
    status: str = "OPEN"  # OPEN, WON, LOST, TIMEOUT

    def to_dict(self) -> dict:
        return asdict(self)


class PaperTradingEngine:
    """Ejecuta paper trading en cuenta demo Deriv.

    Args:
        symbol: Símbolo a tradear (default RB100)
        max_trades: Máximo número de trades antes de parar (default 30)
    """

    def __init__(
        self,
        symbol: str = "RB100",
        max_trades: int = 30,
        score_threshold: float = SCORE_THRESHOLD,
    ) -> None:
        self.symbol = symbol
        self.max_trades = max_trades
        self.score_threshold = score_threshold

        # Componentes del sistema
        self.risk_manager = RiskManager(RiskConfig())
        self.circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            consecutive_losses_threshold=3,
            daily_drawdown_threshold=0.05,
        ))
        # Telegram notifier (opcional, desde .env)
        from src.notifications.telegram import TelegramNotifier
        self.telegram = TelegramNotifier.from_env()
        # Daily reporter
        from src.trading.daily_reporter import DailyReporter
        self.daily_reporter = DailyReporter(telegram=self.telegram)
        # Daily tracking
        from datetime import datetime, timezone
        self.last_reset_date = datetime.now(timezone.utc).date()
        self.starting_balance_daily = 10000.0
        self.today_trades: list[dict[str, Any]] = []
        scorer = SignalScorer(entry_threshold=score_threshold)
        self.strategy = RangeBreakStrategy(
            symbol=symbol,
            config=RangeBreakConfig(),
            signal_scorer=scorer,
            score_threshold=score_threshold,
        )
        self.recommender = Recommender(capital=10000.0)

        # Estado
        self.candles: pd.DataFrame = pd.DataFrame()
        self.trades: list[PaperTrade] = []
        self.current_position: PaperTrade | None = None
        self.balance: float = 10000.0
        self.starting_balance: float = 10000.0
        self.is_running = False

        # Reporte
        self.report_dir = Path("reports/paper")
        self.report_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, client: DerivClient) -> None:
        """Ejecuta paper trading hasta alcanzar max_trades o halt.

        Args:
            client: DerivClient ya conectado (demo)
        """
        logger.info("=== Paper Trading Start ===")
        logger.info("Symbol: %s | Max trades: %d | Score threshold: %.2f",
                     self.symbol, self.max_trades, self.score_threshold)

        # Reset daily risk
        self.risk_manager.reset_daily(self.balance)

        # 1. Warmup: download historical candles
        logger.info("Downloading %d candles for warmup...", WARMUP_CANDLES)
        from src.data.collector import DataCollector
        collector = DataCollector(client)
        self.candles = await collector.download_candles(
            symbol=self.symbol,
            count=WARMUP_CANDLES,
            granularity=CANDLE_GRANULARITY,
        )
        logger.info("Warmup complete: %d candles", len(self.candles))

        # 2. Get current balance
        bal = await client.balance()
        self.balance = float(bal.get("balance", {}).get("balance", 10000.0))
        self.starting_balance = self.balance
        self.starting_balance_daily = self.balance  # Track daily starting balance
        self.recommender.capital = self.balance
        logger.info("Demo balance: $%.2f", self.balance)

        self.is_running = True

        # 3. Main loop: subscribe to ticks, aggregate candles, generate signals
        await self._trading_loop(client)

        # 4. Generate final daily report if there are trades for today
        if self.today_trades:
            self._generate_daily_report()

        # 5. Save final report
        self._save_report()

        # 6. Clear realtime state file when done
        try:
            if os.path.exists(REALTIME_STATE_FILE):
                os.remove(REALTIME_STATE_FILE)
        except Exception:
            pass

    def _write_realtime_state(self) -> None:
        """Write current state to a JSON file for the API/dashboard to read."""
        try:
            state = {
                "mode": "paper",
                "symbol": self.symbol,
                "balance": self.balance,
                "pnl": self.balance - self.starting_balance,
                "trades_today": len(self.today_trades),
                "is_halted": not self.circuit_breaker.can_trade()[0],
                "circuit_breaker": self.circuit_breaker.status(),
                "last_update": datetime.now(timezone.utc).isoformat(),
                "recent_trades": self.today_trades[-5:] if self.today_trades else [],
            }
            # Ensure directory exists
            os.makedirs(os.path.dirname(REALTIME_STATE_FILE), exist_ok=True)
            with open(REALTIME_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
                
            # Also write equity point for charting
            equity_point = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "equity": self.balance,
                "pnl": self.balance - self.starting_balance
            }
            with open(EQUITY_FILE, "a") as f:
                f.write(json.dumps(equity_point) + "\n")
                
        except Exception as e:
            logger.debug(f"Could not write realtime state: {e}")
            
    def _write_realtime_trade(self, trade: dict) -> None:
        """Write a trade to the trades JSONL file for WebSocket streaming."""
        try:
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(trade) + "\n")
        except Exception as e:
            logger.debug(f"Could not write realtime trade: {e}")

    async def _trading_loop(self, client: DerivClient) -> None:
        """Loop principal: acumula candles y genera signals."""
        tick_count = 0
        last_candle_time = 0
        current_candle: dict[str, Any] = {}

        # Subscribe to ticks
        logger.info("Subscribing to ticks for %s...", self.symbol)
        tick_response = await client.subscribe_ticks(self.symbol)
        logger.info("Tick subscription active")

        while self.is_running and len(self.trades) < self.max_trades:
            # Check daily rollover (generate report if new day UTC)
            self._check_daily_rollover()

            # Check if we're halted
            cb_ok, cb_reason = self.circuit_breaker.can_trade()
            if not cb_ok:
                logger.warning("Circuit breaker: %s. Waiting 60s...", cb_reason)
                await asyncio.sleep(60)
                continue

            # In real implementation, this would process live ticks
            # For now, we simulate by downloading fresh candles periodically
            logger.info("Paper trade %d/%d | Balance: $%.2f | P&L: $%.2f",
                        len(self.trades) + 1, self.max_trades,
                        self.balance, self.balance - self.starting_balance)

            # Download latest candles
            try:
                fresh = await client.ticks_history(
                    symbol=self.symbol,
                    count=50,
                    style="candles",
                    granularity=CANDLE_GRANULARITY,
                )
                new_candles = fresh.get("candles", [])
                if new_candles:
                    new_df = pd.DataFrame(new_candles)
                    new_df["epoch"] = pd.to_numeric(new_df["epoch"])
                    new_df["datetime"] = pd.to_datetime(new_df["epoch"], unit="s")
                    # Append to existing candles (deduplicate by epoch)
                    self.candles = pd.concat([self.candles, new_df]).drop_duplicates(
                        subset=["epoch"]
                    ).sort_values("epoch").reset_index(drop=True)
            except Exception as e:
                logger.error("Error fetching candles: %s", e)
                await asyncio.sleep(60)
                continue

            # Generate signal
            if len(self.candles) >= self.strategy.config.min_channel_ticks + 1:
                signal = self.strategy.generate_signal(self.candles)

                if signal.type != SignalType.NO_SIGNAL:
                    await self._execute_signal(client, signal)

            # Update real-time state file for dashboard/API
            self._write_realtime_state()

            # Wait before next check (1 minute candle = check every 60s)
            # Use shorter interval for responsiveness
            await asyncio.sleep(10)

        self.is_running = False
        logger.info("Paper trading complete: %d trades executed", len(self.trades))

    async def _execute_signal(self, client: DerivClient, signal: Signal) -> None:
        """Ejecuta una signal en la cuenta demo."""
        # Check risk manager
        can, reason = self.risk_manager.can_trade()
        if not can:
            logger.info("Trade skipped: %s", reason)
            return

        # Calculate position size
        win_prob = self.strategy.get_win_probability(signal)
        win_amount = abs(signal.take_profit - signal.entry_price)
        loss_amount = abs(signal.entry_price - signal.stop_loss)

        # Get ATR ratio from signal metadata for volatility multiplier
        atr_ratio = signal.metadata.get("atr_ratio", 1.0)
        vol_mult = 1.0 + max(0.0, (atr_ratio - 1.0))

        size = self.risk_manager.position_size_dynamic(
            self.balance, win_prob, win_amount, loss_amount,
            confidence=signal.confidence,
            volatility_multiplier=vol_mult,
        )

        if size <= 0:
            logger.info("No edge — Kelly returned 0")
            return

        # Generate recommendation
        rec = self.recommender.generate_recommendation(
            signal, signal.confidence, size, self.circuit_breaker, self.balance
        )
        logger.info("RECOMMENDATION: %s", rec)

        # Create paper trade record
        trade = PaperTrade(
            timestamp=datetime.now(timezone.utc).isoformat(),
            direction=signal.type.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            stake=size,
            confidence=signal.confidence,
            score=signal.confidence,
        )

        # In paper trading, we simulate the trade outcome
        # (in live: send proposal → buy → monitor → sell)
        # For demo, we use the historical candle data to simulate
        pnl, exit_reason, exit_price = await self._simulate_paper_trade(
            client, signal, size
        )

        trade.pnl = pnl
        trade.exit_reason = exit_reason
        trade.exit_price = exit_price
        trade.status = "WON" if pnl > 0 else "LOST"
        trade.duration_seconds = 60  # approximate

        self.balance += pnl
        self.risk_manager.record_trade(pnl, self.balance)
        self.circuit_breaker.update(
            loss=pnl < 0,
            current_balance=self.balance,
            starting_balance=self.starting_balance,
        )

        self.trades.append(trade)
        self.today_trades.append(trade.to_dict())  # Track for daily report
        self._write_realtime_trade(trade.to_dict())  # Write to realtime trades file
        logger.info(
            "Trade #%d: %s %s | Entry: %.2f | Exit: %.2f | P&L: $%.4f | %s",
            len(self.trades), trade.direction, self.symbol,
            trade.entry_price, trade.exit_price, pnl, trade.status
        )

        # Save trade to JSON incrementally
        self._save_report()

    async def _simulate_paper_trade(
        self, client: DerivClient, signal: Signal, size: float
    ) -> tuple[float, str, float]:
        """Simula el resultado del paper trade usando candles live.

        En paper trading real, enviaríamos proposal+buy a Deriv.
        Para esta implementación de validación, usamos las candles
        más recientes para estimar el outcome.
        """
        # Fetch next few candles to see if TP or SL was hit
        fresh = await client.ticks_history(
            symbol=self.symbol,
            count=20,
            style="candles",
            granularity=CANDLE_GRANULARITY,
        )
        candles = fresh.get("candles", [])

        max_candles = signal.duration_seconds // CANDLE_GRANULARITY

        for candle in candles[:max_candles]:
            high = float(candle["high"])
            low = float(candle["low"])
            close = float(candle["close"])

            if signal.type == SignalType.LONG:
                if low <= signal.stop_loss:
                    pnl = -size * (abs(signal.entry_price - signal.stop_loss) / signal.entry_price)
                    return pnl, "SL", signal.stop_loss
                if high >= signal.take_profit:
                    pnl = size * (abs(signal.take_profit - signal.entry_price) / signal.entry_price)
                    return pnl, "TP", signal.take_profit

            elif signal.type == SignalType.SHORT:
                if high >= signal.stop_loss:
                    pnl = -size * (abs(signal.stop_loss - signal.entry_price) / signal.entry_price)
                    return pnl, "SL", signal.stop_loss
                if low <= signal.take_profit:
                    pnl = size * (abs(signal.entry_price - signal.take_profit) / signal.entry_price)
                    return pnl, "TP", signal.take_profit

        # Time exit
        if candles:
            close = float(candles[-1]["close"])
            if signal.type == SignalType.LONG:
                pnl = size * ((close - signal.entry_price) / signal.entry_price)
            else:
                pnl = size * ((signal.entry_price - close) / signal.entry_price)
            return pnl, "TIME", close

        return 0.0, "NO_DATA", signal.entry_price

    def _check_daily_rollover(self) -> None:
        """Verifica si cambió el día UTC → genera reporte del día anterior."""
        now = datetime.now(timezone.utc)
        today = now.date()
        if today > self.last_reset_date:
            logger.info("Nuevo día UTC detectado: %s (anterior: %s)", today, self.last_reset_date)
            self._generate_daily_report()
            # Reset daily counters
            self.last_reset_date = today
            self.starting_balance_daily = self.balance
            self.today_trades = []

    def _generate_daily_report(self) -> None:
        """Genera el reporte diario usando DailyReporter y envía a Telegram."""
        yesterday = self.last_reset_date.strftime("%Y-%m-%d")
        logger.info("Generando reporte diario para %s (%d trades)...", yesterday, len(self.today_trades))

        self.daily_reporter.generate_report(
            date=yesterday,
            starting_balance=self.starting_balance_daily,
            ending_balance=self.balance,
            trades=self.today_trades,
            circuit_breaker_status=self.circuit_breaker.status(),
            risk_report=self.risk_manager.daily_report(),
        )

    def _save_report(self) -> None:
        """Guarda el reporte de paper trading en JSON."""
        wins = sum(1 for t in self.trades if t.pnl > 0)
        losses = len(self.trades) - wins
        total_pnl = sum(t.pnl for t in self.trades)
        win_rate = wins / len(self.trades) if self.trades else 0

        report = {
            "mode": "paper",
            "symbol": self.symbol,
            "starting_balance": self.starting_balance,
            "current_balance": self.balance,
            "total_trades": len(self.trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 4),
            "circuit_breaker_status": self.circuit_breaker.status(),
            "risk_report": self.risk_manager.daily_report(),
            "trades": [t.to_dict() for t in self.trades],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "score_threshold": self.score_threshold,
        }

        report_file = self.report_dir / "paper_trading_report.json"
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info("Paper trading report saved to %s", report_file)


async def run_paper_trading(
    symbol: str = "RB100",
    max_trades: int = 30,
) -> None:
    """Entry point para paper trading.

    Args:
        symbol: Símbolo a tradear
        max_trades: Máximo trades antes de parar
    """
    config = DerivConfig.from_yaml()
    client = DerivClient(config)

    try:
        await client.connect()
        logger.info("Connected to Deriv (demo: %s)", config.is_demo)

        engine = PaperTradingEngine(
            symbol=symbol,
            max_trades=max_trades,
        )
        await engine.run(client)

    except KeyboardInterrupt:
        logger.info("Paper trading interrupted by user")
    except Exception as e:
        logger.error("Paper trading error: %s", e)
        raise
    finally:
        await client.disconnect()
        logger.info("Disconnected from Deriv")


if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    sym = sys.argv[1] if len(sys.argv) > 1 else "RB100"
    max_t = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    asyncio.run(run_paper_trading(symbol=sym, max_trades=max_t))