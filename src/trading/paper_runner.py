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


# Symbol-prefix → strategy registry key, used by the ``"auto"`` selector.
# Keep in sync with ``src/trading/strategy_factory.py`` STRATEGY_REGISTRY.
#
# Each instrument family has a strategy that matches its price generation
# process. Using the wrong strategy (e.g. range-break on a volatility index)
# produces ZERO signals because the strategy's assumptions don't match the
# instrument's structure (R_100 follows an Ornstein-Uhlenbeck process; it
# has no channel support/resistance).
#
#   R_100, R_75, R_50, R_25, R_10  → "volatility"  (ATR-band mean reversion)
#   RB100, RB200                    → "breakout"    (channel breakout)
#   BOOM*, CRASH*                   → "drift_boom_crash"
#   STEPT*, STE*                    → "step_index"
#
# ``"auto"`` resolves to one of the above based on ``symbol``.  An explicit
# strategy_name passed in always wins.
def _auto_resolve_strategy(symbol: str) -> str:
    """Map a Deriv synthetic symbol to the matching strategy registry key.

    The order of checks matters: ``RB`` must be checked before ``R_`` (both
    share the ``R`` prefix).  Returns a key that exists in
    ``STRATEGY_REGISTRY``.
    """
    sym = symbol.upper().strip()
    if sym.startswith("RB"):
        return "breakout"          # Range Break indices (RB100, RB200)
    if sym.startswith("R_"):
        return "volatility"        # Volatility indices (R_10..R_100)
    if sym.startswith(("BOOM", "CRASH")):
        return "drift_boom_crash"  # Boom/Crash spike indices
    if sym.startswith(("STEPT", "STE")):
        return "step_index"        # Step indices
    # Unknown family — default to volatility (safest for non-channel indices).
    logger.warning(
        "Symbol %s doesn't match a known family; defaulting to volatility "
        "strategy. Specify strategy_name explicitly to override.",
        symbol,
    )
    return "volatility"

# Cuántas candles históricas para warmup
WARMUP_CANDLES = 500
# Threshold del scorer (optimizado en Fase 1)
# Originally 0.50 — too high for R_100 VolatilityStrategy in live regime.
# Per-factor reachability diagnostic on R_100 (see references/
# score-threshold-reachability-diagnostic-2026-08-02.md) showed live
# scores cluster 0.27-0.45. A 0.50 threshold filters ALL signals → zero
# trades. 0.35 lets through real band-touch signals (score 0.37-0.43)
# while still filtering noise. This default is now propagated to the
# VolatilityStrategy via explicit VolatilityConfig(score_threshold=...)
# in the PaperTradingEngine constructor (see code below).
SCORE_THRESHOLD = 0.35
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
        strategy_name: str = "auto",
    ) -> None:
        self.symbol = symbol
        self.max_trades = max_trades
        self.score_threshold = score_threshold
        # Resolve ``"auto"`` → correct strategy key for this symbol's family.
        # This is the default: each instrument gets the strategy that matches
        # its price-generation process (R_100 → volatility, RB100 → range
        # break, BOOM/CRASH → drift, STEPT → step index).  An explicit
        # strategy_name passed in is honoured verbatim.
        if strategy_name == "auto":
            strategy_name = _auto_resolve_strategy(symbol)
            logger.info("Auto-resolved strategy: %s → %s", symbol, strategy_name)
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

        # Multi-strategy via factory — all strategies (including "breakout")
        # are created through the same named registry so the runner stays
        # symbol/strategy-agnostic.  The "auto" default already resolved to a
        # concrete key above; an explicit strategy_name is passed through
        # unchanged.  RangeBreak gets the scorer/threshold it expects.
        scorer = SignalScorer(entry_threshold=score_threshold)
        if strategy_name == "breakout":
            self.strategy = create_strategy(
                strategy_name,
                symbol=symbol,
                config=RangeBreakConfig(),
                signal_scorer=scorer,
                score_threshold=score_threshold,
            )
        elif strategy_name == "volatility":
            # VolatilityStrategy owns its own score_threshold in config.
            # previously the runner's score_threshold (0.50 by default) was
            # NEVER propagated — the strategy used its own default 0.20, so
            # signals with scores 0.27-0.45 slipped through and produced 8
            # dead-end NO_DATA trades. Pass it explicitly now.
            from src.strategies.volatility import VolatilityConfig
            vol_cfg = VolatilityConfig(score_threshold=score_threshold)
            self.strategy = create_strategy(
                strategy_name, symbol=symbol, config=vol_cfg,
            )
        else:
            # Factory creates any registered strategy: confluence,
            # step_index, drift_boom_crash — they use their own defaults.
            self.strategy = create_strategy(strategy_name, symbol=symbol)
        logger.info(
            "Strategy initialized: %s → %s (symbol=%s, key=%s)",
            strategy_name, self.strategy.__class__.__name__, symbol, strategy_name,
        )

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
                "strategy": self.strategy_name,  # <-- ADDED
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
                "pnl": self.balance - self.starting_balance,
            }
            with open(EQUITY_FILE, "a") as f:
                f.write(json.dumps(equity_point) + "\n")
        except Exception as e:
            logger.debug(f"Could not write realtime state: {e}")
