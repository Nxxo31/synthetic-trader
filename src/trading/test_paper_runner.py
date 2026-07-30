"""Unit tests for trading/paper_runner.py — _write_realtime_state and _write_realtime_trade.

Tests verify that the realtime state methods correctly:
1. Write JSON files to the realtime/ directory
2. Produce valid JSON with expected fields
3. Append equity points to the equity.jsonl file
4. Append trades to the trades.jsonl file

The PaperTradingEngine __init__ pulls in heavy dependencies (Telegram, Deriv client).
To keep these unit tests focused, we create minimal sub-engine instances via
`__new__` (bypassing __init__) and set only the attributes the tested methods need.
Then we override the REALTIME file paths to write to an isolated tmpdirname.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.trading import paper_runner
from src.trading.paper_runner import PaperTradingEngine


# ---------------------------------------------------------------------------
#  Helpers — create a minimal engine without running __init__
# ---------------------------------------------------------------------------


def _make_minimal_engine(tmpdir: Path) -> PaperTradingEngine:
    """Create a PaperTradingEngine without calling __init__.

    Set just enough attributes for _write_realtime_state / _write_realtime_trade
    to work, and redirect file paths to the tmpdir.
    """
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine.symbol = "R_100"
    engine.balance = 10250.0
    engine.starting_balance = 10000.0
    engine.today_trades = [
        {"timestamp": "2025-01-01T00:00:00Z", "direction": "LONG", "pnl": 50.0, "stake": 100.0},
    ]

    # Minimal circuit breaker mock — can_trade() returns (True, "OK")
    # and status() returns a dict
    class _FakeCB:
        def can_trade(self):
            return (True, "OK")

        def status(self):
            return {"consecutive_losses": 0, "is_halted": False}

    engine.circuit_breaker = _FakeCB()

    return engine


@pytest.fixture
def realtime_files(tmp_path):
    """Patch the module-level REALTIME file paths to use a temp dir."""
    state_file = str(tmp_path / "paper_state.json")
    equity_file = str(tmp_path / "equity.jsonl")
    trades_file = str(tmp_path / "trades.jsonl")

    with mock.patch.object(paper_runner, "REALTIME_STATE_FILE", state_file), \
         mock.patch.object(paper_runner, "EQUITY_FILE", equity_file), \
         mock.patch.object(paper_runner, "TRADES_FILE", trades_file):
        yield {
            "state": state_file,
            "equity": equity_file,
            "trades": trades_file,
        }


# ---------------------------------------------------------------------------
#  Test _write_realtime_state
# ---------------------------------------------------------------------------


class TestWriteRealtimeState:
    """Tests for PaperTradingEngine._write_realtime_state()."""

    def test_state_file_created_with_valid_json(self, realtime_files, tmp_path):
        """_write_realtime_state should create paper_state.json with valid JSON."""
        engine = _make_minimal_engine(tmp_path)
        engine._write_realtime_state()

        assert os.path.exists(realtime_files["state"])
        with open(realtime_files["state"]) as f:
            state = json.load(f)

        assert state["mode"] == "paper"
        assert state["symbol"] == "R_100"
        assert state["balance"] == 10250.0
        assert state["pnl"] == 250.0  # 10250 - 10000
        assert state["trades_today"] == 1
        assert "last_update" in state
        assert " circuit_breaker" not in state  # dict spelling check
        assert "circuit_breaker" in state

    def test_state_file_contains_recent_trades(self, realtime_files, tmp_path):
        """State JSON should include the last 5 trades from today_trades."""
        engine = _make_minimal_engine(tmp_path)
        engine.today_trades = [
            {"direction": "LONG", "pnl": i} for i in range(10)
        ]
        engine._write_realtime_state()

        with open(realtime_files["state"]) as f:
            state = json.load(f)

        assert "recent_trades" in state
        assert len(state["recent_trades"]) == 5  # last 5
        # Most recent is index 9
        assert state["recent_trades"][-1]["pnl"] == 9

    def test_state_pnl_changes_with_balance(self, realtime_files, tmp_path):
        """Changing balance should change the pnl field."""
        engine = _make_minimal_engine(tmp_path)

        engine.balance = 10000.0
        engine._write_realtime_state()
        with open(realtime_files["state"]) as f:
            state1 = json.load(f)
        assert state1["pnl"] == 0.0

        engine.balance = 9500.0
        engine._write_realtime_state()
        with open(realtime_files["state"]) as f:
            state2 = json.load(f)
        assert state2["pnl"] == -500.0
        assert state2["pnl"] < state1["pnl"]

    def test_equity_file_appended_on_state_write(self, realtime_files, tmp_path):
        """_write_realtime_state should append a line to equity.jsonl."""
        engine = _make_minimal_engine(tmp_path)
        engine._write_realtime_state()

        assert os.path.exists(realtime_files["equity"])
        lines = Path(realtime_files["equity"]).read_text().strip().split("\n")
        assert len(lines) == 1
        point = json.loads(lines[0])
        assert point["equity"] == 10250.0
        assert point["pnl"] == 250.0
        assert "timestamp" in point


# ---------------------------------------------------------------------------
#  Test _write_realtime_trade
# ---------------------------------------------------------------------------


class TestWriteRealtimeTrade:
    """Tests for PaperTradingEngine._write_realtime_trade()."""

    def test_trade_file_created_and_appended(self, realtime_files, tmp_path):
        """_write_realtime_trade should append JSONL line to trades.jsonl."""
        engine = _make_minimal_engine(tmp_path)
        trade = {"timestamp": "2025-01-01T00:00:00Z", "direction": "LONG", "pnl": 50.0}
        engine._write_realtime_trade(trade)

        assert os.path.exists(realtime_files["trades"])
        lines = Path(realtime_files["trades"]).read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["direction"] == "LONG"
        assert record["pnl"] == 50.0

    def test_multiple_trades_appended_in_order(self, realtime_files, tmp_path):
        """Each call to _write_realtime_trade should append, not overwrite."""
        engine = _make_minimal_engine(tmp_path)
        trades = [
            {"direction": "LONG", "pnl": 50.0},
            {"direction": "SHORT", "pnl": -30.0},
            {"direction": "LONG", "pnl": 100.0},
        ]
        for t in trades:
            engine._write_realtime_trade(t)

        lines = Path(realtime_files["trades"]).read_text().strip().split("\n")
        assert len(lines) == 3
        assert json.loads(lines[0])["direction"] == "LONG"
        assert json.loads(lines[1])["direction"] == "SHORT"
        assert json.loads(lines[2])["pnl"] == 100.0

    def test_trade_file_is_valid_jsonl(self, realtime_files, tmp_path):
        """Each line of trades.jsonl must parse as valid JSON."""
        engine = _make_minimal_engine(tmp_path)
        import random
        for i in range(5):
            trade = {
                "timestamp": f"2025-01-01T00:0{i}:00Z",
                "direction": random.choice(["LONG", "SHORT"]),
                "pnl": float(i * 10 - 20),
                "entry_price": 100.0 + i,
            }
            engine._write_realtime_trade(trade)

        lines = Path(realtime_files["trades"]).read_text().strip().split("\n")
        for line in lines:
            record = json.loads(line)  # Should not raise
            assert "direction" in record
            assert "pnl" in record
