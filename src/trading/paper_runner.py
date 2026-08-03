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
from src.risk.capital_allocator import CapitalAllocator, CapitalAllocatorConfig
from src.analysis.recommender import Recommender
from src.trading.strategy_factory import create_strategy

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

# Consecutive reconnect failures before a clean halt + dashboard alert.
MAX_RECONNECT_FAILURES_GLOBAL = 5


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
        strategy_name: str = "breakout",
    ) -> None:
        self.symbol = symbol
        self.max_trades = max_trades
        self.score_threshold = score_threshold
        self.strategy_name = strategy_name

        # Componentes del sistema
        self.risk_manager = RiskManager(RiskConfig())
        self.circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            consecutive_losses_threshold=3,
            daily_drawdown_threshold=0.05,
        ))
        # Capital Allocator — divide reserva(80%) + superávit(20%)
        self.allocator = CapitalAllocator(
            config=CapitalAllocatorConfig(),
            risk_manager=self.risk_manager,
        )
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

        # Multi-strategy via factory — uses create_strategy() registry
        scorer = SignalScorer(entry_threshold=score_threshold)
        if strategy_name == "breakout":
            self.strategy = RangeBreakStrategy(
                symbol=symbol,
                config=RangeBreakConfig(),
                signal_scorer=scorer,
                score_threshold=score_threshold,
            )
        else:
            # Factory creates any registered strategy: volatility, confluence,
            # step_index, drift_boom_crash
            self.strategy = create_strategy(strategy_name, symbol=symbol)
            logger.info("Using factory strategy: %s → %s", strategy_name, self.strategy.__class__.__name__)

        self.recommender = Recommender(capital=10000.0)

        # Estado
        self.candles: pd.DataFrame = pd.DataFrame()
        self.trades: list[PaperTrade] = []
        self.current_position: PaperTrade | None = None
        self.balance: float = 10000.0
        self.starting_balance: float = 10000.0
        self.is_running = False
        # --- reconnect / stale-data robustness (see _trading_loop) ---
        # Last known-good candles used when a fetch fails so the strategy
        # still gets validated data (with stale_data=True flag) instead of
        # None/empty.  Reduces false NO_SIGNAL on transient WS drops.
        self._last_valid_candles: pd.DataFrame = pd.DataFrame()
        # Consecutive failed reconnect attempts.  When this reaches 5 we
        # halt cleanly and push an alert to the dashboard via paper_state.json.
        self._reconnect_failures: int = 0

        # Reporte
        self.report_dir = Path("reports/paper")
        self.report_dir.mkdir(parents=True, exist_ok=True)

        # Cooldown entre señales para evitar trades duplicados en la misma vela
        self._last_signal_time: float = 0.0
        self._signal_cooldown_seconds: float = 120.0  # 2 min entre señales
        # Timestamp de la última candle procesada para no procesar la misma dos veces
        self._last_candle_epoch: int = 0

    def _get_min_required_candles(self) -> int:
        """Get minimum candles required for the current strategy."""
        if hasattr(self.strategy, 'config'):
            config = self.strategy.config
            # Different strategies have different config attribute names
            if hasattr(config, 'min_channel_ticks'):
                return config.min_channel_ticks          # RangeBreakStrategy
            elif hasattr(config, 'min_candles'):
                return config.min_candles                # VolatilityStrategy, MeanReversionStrategy
            elif hasattr(config, 'lookback_window'):
                return config.lookback_window            # Other strategies (if any)
        return 20  # fallback


    def _get_min_required_candles(self) -> int:
        """Get minimum candles required for the current strategy.

        Different strategies expose different attributes on their config
        object (min_channel_ticks, min_candles, lookback_window).  This
        helper normalises them so the trading loop never guesses wrong and
        crashes on a missing attribute.
        """
        if hasattr(self.strategy, 'config'):
            config = self.strategy.config
            if hasattr(config, 'min_channel_ticks'):
                return config.min_channel_ticks          # RangeBreakStrategy
            elif hasattr(config, 'min_candles'):
                return config.min_candles                # VolatilityStrategy
            elif hasattr(config, 'lookback_window'):
                return config.lookback_window
        return 20  # safe fallback

    async def run(self, client: DerivClient) -> None:
        """Ejecuta paper trading hasta alcanzar max_trades o halt.

        Args:
            client: DerivClient ya conectado (demo)
        """
        logger.info("=== Paper Trading Start ===")
        logger.info("Symbol: %s | Strategy: %s | Max trades: %d | Score threshold: %.2f",
                     self.symbol, self.strategy_name, self.max_trades, self.score_threshold)

        # Reset daily risk
        self.risk_manager.reset_daily(self.balance)
        # Initialize Capital Allocator for the day
        self.allocator.reset_daily(capital_total=self.balance)

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
        """Loop principal: acumula candles y genera signals.

        Robustez (v0.4.1+):
          * try/except por iteración — un error nunca mata el loop.
          * Al detectar "Not connected" llama ``client.reconnect()`` con
            backoff exponencial (gestión transparente de re-OTP, ya que el
            OTP de Deriv es single-use y expira en 120s).
          * 5 fallos consecutivos de reconexión → halt limpio + alerta.
          * Si fetch candles falla pero existe un df previo válido, la
            estrategia recibe ese df con stale_data=True (no None/empty).
        """
        # Subscribe to ticks (no-op failure here is fatal — lets run() raise).
        logger.info("Subscribing to ticks for %s...", self.symbol)
        await client.subscribe_ticks(self.symbol)
        logger.info("Tick subscription active")

        while self.is_running and len(self.trades) < self.max_trades:
            try:
                # Check daily rollover (generate report if new day UTC)
                self._check_daily_rollover()

                # Check if we're halted
                cb_ok, cb_reason = self.circuit_breaker.can_trade()
                if not cb_ok:
                    logger.warning("Circuit breaker: %s. Waiting 60s...", cb_reason)
                    await asyncio.sleep(60)
                    continue

                logger.info("Paper trade %d/%d | Balance: $%.2f | P&L: $%.2f",
                            len(self.trades) + 1, self.max_trades,
                            self.balance, self.balance - self.starting_balance)

                # Download latest candles (may raise if WS dropped).
                stale_data = False
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
                        self.candles = pd.concat([self.candles, new_df]).drop_duplicates(
                            subset=["epoch"]
                        ).sort_values("epoch").reset_index(drop=True)
                    # Fetch succeeded → this is a known-good snapshot.
                    if not self.candles.empty:
                        self._last_valid_candles = self.candles.copy()
                except (RuntimeError, ConnectionError, OSError, DerivAPIError) as e:
                    # WebSocket dropped or credentials expired (OTP 120s).
                    # Attempt transparent reconnection before giving up.
                    logger.warning("Fetch failed (likely WS drop): %s — reconnecting...", e)
                    reconnected = await self._reconnect_or_halt(client)
                    if not reconnected:
                        break  # halt already issued
                    # Connection restored but we couldn't fetch this round:
                    # fall back to last known-good candles (stale).
                    if not self._last_valid_candles.empty:
                        stale_data = True
                        self.candles = self._last_valid_candles.copy()
                        logger.info("Using last-known-good candles (stale_data=True): %d rows",
                                    len(self.candles))
                    else:
                        logger.warning("No prior candles and fetch failed — skipping iteration")
                        await asyncio.sleep(10)
                        continue
                except Exception as e:
                    # Non-connection error (parsing, etc.) — log and continue.
                    logger.error("Unexpected error fetching candles: %s", e)
                    if not self._last_valid_candles.empty:
                        stale_data = True
                        self.candles = self._last_valid_candles.copy()
                    await asyncio.sleep(10)
                    continue

                # Generate signal only if we have validated data to work with.
                if self.candles is not None and not self.candles.empty and \
                   len(self.candles) >= self._get_min_required_candles() + 1:
                    if stale_data:
                        logger.info("Generating signal on STALE data (no live fetch this round)")

                    # Cooldown check — no ejecutar señal si estamos en cooldown
                    import time
                    now_ts = time.time()
                    if now_ts - self._last_signal_time < self._signal_cooldown_seconds:
                        remaining = self._signal_cooldown_seconds - (now_ts - self._last_signal_time)
                        logger.debug("Cooldown activo: %.0fs restantes", remaining)
                    else:
                        signal = self.strategy.generate_signal(self.candles)

                        if signal.type != SignalType.NO_SIGNAL:
                            self._last_signal_time = now_ts
                            await self._execute_signal(client, signal)
                else:
                    logger.debug("INSUFFICIENT_CANDLES: have %d, need %d",
                                 len(self.candles) if self.candles is not None else 0,
                                 self._get_min_required_candles() + 1)

                # Update real-time state file for dashboard/API
                self._write_realtime_state()

            except Exception as e:
                # Per-iteration safety net: never let a single error kill the
                # trading loop.  Log, back off, keep going.
                logger.error("Trading loop iteration error (continuing): %s", e, exc_info=True)
                # Heuristic: if it looks connection-related, try reconnect.
                if "connect" in str(e).lower() or "not connected" in str(e).lower():
                    reconnected = await self._reconnect_or_halt(client)
                    if not reconnected:
                        break
            finally:
                # Wait before next check (1 minute candle = check every 60s)
                # Use shorter interval for responsiveness.
                await asyncio.sleep(10)

        self.is_running = False
        logger.info("Paper trading complete: %d trades executed", len(self.trades))

    async def _reconnect_or_halt(self, client: DerivClient) -> bool:
        """Attempt ``client.reconnect()`` with exponential backoff (handled
        inside DerivClient.reconnect).  If the full multi-attempt cycle fails,
        halt cleanly and alert the dashboard.

        Returns True if the client is (now) connected, False if it gave up
        and the loop should stop.
        """
        # DerivClient.reconnect does the 5-attempt exponential backoff itself.
        ok = await client.reconnect(max_attempts=MAX_RECONNECT_FAILURES_GLOBAL)
        if ok:
            self._reconnect_failures = 0
            # Re-subscribe to ticks on the fresh WebSocket (old sub died with WS).
            try:
                await client.subscribe_ticks(self.symbol)
                logger.info("Re-subscribed to ticks after reconnect")
            except Exception as e:
                logger.warning("Re-subscribe after reconnect failed: %s", e)
            return True

        self._reconnect_failures = MAX_RECONNECT_FAILURES_GLOBAL
        logger.critical("Reconnect cycle failed after %d attempts — HALTING",
                        MAX_RECONNECT_FAILURES_GLOBAL)
        await self._halt_and_alert("reconnect_failed")
        return False

    async def _halt_and_alert(self, reason: str) -> None:
        """Halt trading cleanly and push an alert to the dashboard state file."""
        self.is_running = False
        try:
            state = {
                "mode": "paper",
                "symbol": self.symbol,
                "is_halted": True,
                "halt_reason": reason,
                "alert": (
                    f"Bot halted: {reason}. "
                    f"Reconnect failed {self._reconnect_failures} times. "
                    "Manual intervention required."
                ),
                "last_update": datetime.now(timezone.utc).isoformat(),
                "balance": self.balance,
                "pnl": self.balance - self.starting_balance,
                "trades_today": len(self.today_trades),
            }
            os.makedirs(os.path.dirname(REALTIME_STATE_FILE), exist_ok=True)
            with open(REALTIME_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            logger.error("Alert written to %s: %s", REALTIME_STATE_FILE, reason)
        except Exception as e:
            logger.error("Could not write halt alert: %s", e)

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
        # Record P&L in Capital Allocator for surplus tracking
        self.allocator.record_trade(pnl)

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
        """Simula el resultado del paper trade usando candles LIVE futuras.

        Espera a que lleguen nuevas candles DESPUÉS de la entrada (no usa
        candles históricas que ya vio la estrategia). Simula TP/SL/time
        exit basándose en el precio real que llega via WebSocket.
        """
        import time
        entry_epoch = time.time()
        max_duration = signal.duration_seconds  # e.g. 900s = 15 min
        check_interval = 5  # check every 5 seconds
        elapsed = 0

        while elapsed < max_duration:
            try:
                fresh = await client.ticks_history(
                    symbol=self.symbol,
                    count=5,
                    style="ticks",
                )
                ticks = fresh.get("prices", [])
                if ticks:
                    # Use the latest tick price
                    current_price = float(ticks[-1])
                    
                    # Update realtime state during trade monitoring
                    self.balance  # touch
                    self._write_realtime_state()

                    if signal.type == SignalType.LONG:
                        if current_price <= signal.stop_loss:
                            pnl = -size * (abs(signal.entry_price - signal.stop_loss) / signal.entry_price)
                            return pnl, "SL", signal.stop_loss
                        if current_price >= signal.take_profit:
                            pnl = size * (abs(signal.take_profit - signal.entry_price) / signal.entry_price)
                            return pnl, "TP", signal.take_profit

                    elif signal.type == SignalType.SHORT:
                        if current_price >= signal.stop_loss:
                            pnl = -size * (abs(signal.stop_loss - signal.entry_price) / signal.entry_price)
                            return pnl, "SL", signal.stop_loss
                        if current_price <= signal.take_profit:
                            pnl = size * (abs(signal.entry_price - signal.take_profit) / signal.entry_price)
                            return pnl, "TP", signal.take_profit
            except Exception as e:
                logger.warning("Tick fetch failed during trade simulation: %s", e)

            await asyncio.sleep(check_interval)
            elapsed += check_interval

        # Time exit — use current price from latest tick
        try:
            fresh = await client.ticks_history(
                symbol=self.symbol, count=1, style="ticks"
            )
            ticks = fresh.get("prices", [])
            if ticks:
                close = float(ticks[-1])
                if signal.type == SignalType.LONG:
                    pnl = size * ((close - signal.entry_price) / signal.entry_price)
                else:
                    pnl = size * ((signal.entry_price - close) / signal.entry_price)
                return pnl, "TIME", close
        except Exception:
            pass

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
            "strategy_name": self.strategy_name,
            "strategy_class": self.strategy.__class__.__name__,
            "starting_balance": self.starting_balance,
            "current_balance": self.balance,
            "total_trades": len(self.trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 4),
            "circuit_breaker_status": self.circuit_breaker.status(),
            "risk_report": self.risk_manager.daily_report(),
            "allocator_state": self.allocator.get_state(),
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
    strategy_name: str = "breakout",
) -> None:
    """Entry point para paper trading.

    Args:
        symbol: Símbolo a tradear
        max_trades: Máximo trades antes de parar
        strategy_name: Nombre de estrategia (breakout, volatility, confluence,
                       step_index, drift_boom_crash)
    """
    config = DerivConfig.from_yaml()
    client = DerivClient(config)

    try:
        # Initial connect with a small retry — if the very first OTP request
        # fails (e.g. transient REST 5xx), we still want to come up rather
        # than crash the bot on a single hiccup.
        if not await client.reconnect(max_attempts=3):
            raise RuntimeError("Initial connection to Deriv failed after 3 attempts")
        logger.info("Connected to Deriv (demo: %s)", config.is_demo)

        engine = PaperTradingEngine(
            symbol=symbol,
            max_trades=max_trades,
            strategy_name=strategy_name,
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
    strat = sys.argv[3] if len(sys.argv) > 3 else "breakout"

    asyncio.run(run_paper_trading(symbol=sym, max_trades=max_t, strategy_name=strat))
