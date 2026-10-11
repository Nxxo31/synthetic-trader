"""Fase 1 integration test — paper_runner full cycle.

Verifies the restored Controller-risk-executor flow in
``src/trading/paper_runner.py``:

1. One full trading cycle: candles → strategy signal → RiskManager sizing →
   paper execution (TP hit via mocked ticks) → trade recorded → report saved.
2. The daily report is ingested by ``daily_attribution.process_all`` and a
   row lands in ``data/strategies.db`` (tmp copy), closing the loop
   paper_runner → strategies.db that Fase 1 must guarantee.

Run: pytest tests/test_integration_paper_runner.py -v
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.strategies.base import Signal, SignalType
from src.strategies.volatility import VolatilityConfig
from src.trading.paper_runner import PaperTradingEngine


class _MockDerivClient:
    """Minimal DerivClient mock: serves the tick price that triggers TP."""

    def __init__(self, tp_price: float):
        self.tp_price = tp_price
        self.subscribed_to: list[str] = []

    async def subscribe_ticks(self, symbol: str) -> None:
        self.subscribed_to.append(symbol)

    async def ticks_history(self, symbol: str, count: int, style: str, **kw):
        if style == "candles":
            return {"candles": []}  # engine keeps its own preloaded candles
        return {"prices": [self.tp_price]}

    async def reconnect(self, max_attempts: int = 5) -> bool:
        return True


def _make_candles(n: int = 60, base: float = 1000.0) -> pd.DataFrame:
    epochs = pd.RangeIndex(start=0, stop=n * 60, step=60)
    return pd.DataFrame(
        {
            "epoch": epochs,
            "open": [base] * n,
            "high": [base + 1] * n,
            "low": [base - 1] * n,
            "close": [base] * n,
        }
    )


@pytest.mark.asyncio
async def test_paper_runner_full_cycle_tp_win(tmp_path, monkeypatch):
    """One trading loop iteration must produce a WON paper trade and a saved
    report, exercising strategy → RiskManager → CircuitBreaker → Allocator."""
    # Fast-path the sleeps inside _trading_loop / _simulate_paper_trade.
    async def _noop_sleep(*_a, **_kw):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    engine = PaperTradingEngine(
        symbol="R_100",
        max_trades=1,
        strategy_name="volatility",
        score_threshold=0.35,
    )
    engine.report_dir = tmp_path / "paper"
    engine.report_dir.mkdir(parents=True, exist_ok=True)
    engine._signal_cooldown_seconds = 0.0

    entry, sl, tp = 1000.0, 995.0, 1010.0
    calls = {"n": 0}

    def _signal_then_none(_data):
        calls["n"] += 1
        if calls["n"] == 1:
            return Signal(
                type=SignalType.LONG,
                symbol="R_100",
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                duration_seconds=300,
                confidence=0.8,
                metadata={"atr_ratio": 1.0},
            )
        return Signal(
            type=SignalType.NO_SIGNAL,
            symbol="R_100",
            entry_price=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            duration_seconds=0,
            confidence=0.0,
            metadata={},
        )

    monkeypatch.setattr(engine.strategy, "generate_signal", _signal_then_none)
    engine.candles = _make_candles()
    engine.risk_manager.reset_daily(10000.0)
    engine.allocator.reset_daily(capital_total=10000.0)
    engine.is_running = True

    client = _MockDerivClient(tp_price=tp)
    await engine._trading_loop(client)

    assert engine.trades, "expected at least one recorded paper trade"
    trade = engine.trades[0]
    assert trade.status == "WON"
    assert trade.exit_reason == "TP"
    assert engine.balance > 10000.0
    # Risk tracking updated
    assert engine.risk_manager.today is not None
    assert engine.risk_manager.today.trades == 1
    # Allocator got the P&L record
    alloc_state = engine.allocator.get_state()
    assert alloc_state is not None
    # Incremental report written
    report_file = engine.report_dir / "paper_trading_report.json"
    assert report_file.exists()
    data = json.loads(report_file.read_text())
    assert data["total_trades"] == 1
    assert data["wins"] == 1


def test_daily_attribution_imports_report_into_strategies_db(tmp_path):
    """daily_attribution.process_all imports a daily report into strategies.db."""
    from src.analysis.daily_attribution import process_all

    reports_dir = tmp_path / "daily"
    reports_dir.mkdir()
    report = {
        "date": "2026-01-15",
        "symbol": "RB100",
        "strategy": "RangeBreak",
        "total_trades": 3,
        "wins": 2,
        "losses": 1,
        "win_rate": 0.6667,
        "total_pnl": 12.34,
        "sharpe_ratio": 1.1,
        "max_drawdown": 0.02,
        "circuit_halted": False,
    }
    (reports_dir / "2026-01-15.json").write_text(json.dumps(report))

    db_path = tmp_path / "strategies.db"
    summary = process_all(report_dir=reports_dir, db_path=db_path)
    assert summary["errors"] == 0
    assert summary["imported"] == 1

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT s.name, sp.symbol, sp.total_trades, sp.total_pnl "
        "FROM strategy_performance sp JOIN strategies s ON s.id = sp.strategy_id"
    ).fetchone()
    conn.close()
    assert row == ("RangeBreak", "RB100", 3, 12.34)
