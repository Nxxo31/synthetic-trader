"""Unit tests for analysis/attribution.py — StrategyAttribution.

Tests verify REAL behaviour with a fresh SQLite database (temp dir), not
tautologies. Each test seeds performance data, then queries matrices and
best-strategy detection to assert the logic is correct.

Covers:
  (a) save_performance — BacktestResult and dict inputs, strategy auto-register
  (b) profitability_matrix — latest_only vs aggregated, multiple metrics
  (c) best_strategy_per_symbol — ranking, min_trades filter, lower-is-better
  (d) dashboard_payload — bundled output shape
"""
from __future__ import annotations

import pytest

from src.analysis.attribution import StrategyAttribution
from src.backtest.engine import BacktestResult, Trade


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def attr(tmp_path):
    """StrategyAttribution backed by a fresh temp DB (no pre-existing data)."""
    return StrategyAttribution(db_path=tmp_path / "test_strategies.db")


@pytest.fixture
def attr_seeded(tmp_path):
    """StrategyAttribution with 3 strategies × 2 symbols pre-seeded.

    Seeded data (latest_only=True, total_pnl metric):

        R_100:  breakout=+250, volatility=-50, confluence=+400   → best=confluence
        RB100:  breakout=-100, volatility=+80,  confluence=-20    → best=volatility
    """
    a = StrategyAttribution(db_path=tmp_path / "seeded.db")

    results = {
        ("breakout", "R_100", 250.0, 0.60, 2.5, 0.08, True, 30),
        ("volatility", "R_100", -50.0, 0.45, 0.3, 0.05, False, 20),
        ("confluence", "R_100", 400.0, 0.70, 3.0, 0.06, True, 25),
        ("breakout", "RB100", -100.0, 0.48, -0.2, 0.10, False, 22),
        ("volatility", "RB100", 80.0, 0.58, 1.8, 0.04, True, 18),
        ("confluence", "RB100", -20.0, 0.52, 0.1, 0.07, False, 28),
    }

    for strat, sym, pnl, wr, sharpe, dd, gate, trades in results:
        result_dict = {
            "total_trades": trades,
            "wins": int(trades * wr),
            "losses": trades - int(trades * wr),
            "win_rate": wr,
            "total_pnl": pnl,
            "avg_pnl": pnl / trades,
            "max_drawdown": dd,
            "sharpe_ratio": sharpe,
            "profit_factor": 2.0 if pnl > 0 else 0.5,
            "expectancy": 0.2 if pnl > 0 else -0.1,
            "gate_passed": gate,
        }
        a.save_performance(
            strategy_name=strat,
            symbol=sym,
            result=result_dict,
        )

    return a


# ---------------------------------------------------------------------------
#  (a) save_performance tests
# ---------------------------------------------------------------------------


class TestSavePerformance:
    """Tests for StrategyAttribution.save_performance()."""

    def test_save_with_dict_inserts_row(self, attr):
        """save_performance with a dict result should insert exactly one row."""
        result = {
            "total_trades": 10,
            "win_rate": 0.60,
            "sharpe_ratio": 2.0,
            "max_drawdown": 0.05,
            "total_pnl": 150.0,
            "profit_factor": 2.5,
            "expectancy": 0.3,
            "gate_passed": True,
        }
        row_id = attr.save_performance("breakout", "R_100", result)

        assert row_id > 0, "Should return a positive row ID"

    def test_save_with_backtest_result_object(self, attr):
        """save_performance should accept a BacktestResult dataclass."""
        trades = [
            Trade(
                entry_time=1000, exit_time=1060, direction="LONG",
                entry_price=100.0, exit_price=102.0, stop_loss=99.0,
                take_profit=102.0, pnl=2.0, pnl_pct=0.02,
                duration=60, win=True, exit_reason="TP",
            ),
            Trade(
                entry_time=2000, exit_time=2060, direction="SHORT",
                entry_price=100.0, exit_price=99.0, stop_loss=101.0,
                take_profit=99.0, pnl=1.0, pnl_pct=0.01,
                duration=60, win=True, exit_reason="TP",
            ),
        ]
        bt = BacktestResult(
            total_trades=2, wins=2, losses=0, win_rate=1.0,
            total_pnl=3.0, avg_pnl=1.5, max_drawdown=0.02,
            sharpe_ratio=3.0, profit_factor=10.0, expectancy=0.5,
            trades=trades, gate_passed=True,
        )

        row_id = attr.save_performance("breakout", "RB100", bt)
        assert row_id > 0

    def test_unregistered_strategy_auto_inserts(self, attr):
        """A strategy name not in the strategies table should be auto-registered."""
        result = {"total_pnl": 10.0, "win_rate": 0.5}
        attr.save_performance("my_custom_strategy", "R_50", result)

        # Query the DB to verify the strategy was created
        import sqlite3
        conn = sqlite3.connect(str(attr.db_path))
        row = conn.execute(
            "SELECT name FROM strategies WHERE name = 'my_custom_strategy'"
        ).fetchone()
        conn.close()
        assert row is not None, "Custom strategy should be auto-registered"

    def test_repeat_strategy_reuses_same_id(self, attr):
        """Saving twice with the same strategy name should reuse the strategy_id."""
        result1 = {"total_pnl": 10.0}
        result2 = {"total_pnl": 20.0}

        id1 = attr.save_performance("breakout", "R_100", result1)
        id2 = attr.save_performance("breakout", "R_50", result2)

        # Both should reference the same strategy_id
        import sqlite3
        conn = sqlite3.connect(str(attr.db_path))
        strategy_ids = conn.execute(
            "SELECT DISTINCT strategy_id FROM strategy_performance"
        ).fetchall()
        conn.close()
        assert len(strategy_ids) == 1, "Same strategy should map to one strategy_id"

    def test_gate_passed_stored_as_integer(self, attr):
        """gate_passed=True should be stored as 1, False as 0 in DB."""
        result_pass = {"total_pnl": 10.0, "gate_passed": True, "total_trades": 10}
        result_fail = {"total_pnl": 5.0, "gate_passed": False, "total_trades": 10}

        attr.save_performance("breakout", "R_100", result=result_pass)
        attr.save_performance("volatility", "R_100", result=result_fail)

        import sqlite3
        conn = sqlite3.connect(str(attr.db_path))
        values = conn.execute(
            "SELECT gate_passed FROM strategy_performance ORDER BY id"
        ).fetchall()
        conn.close()
        assert values[0][0] == 1
        assert values[1][0] == 0

    def test_default_backtest_date_is_today(self, attr):
        """If no backtest_date is provided, it should default to today's UTC date."""
        from datetime import datetime, timezone
        result = {"total_pnl": 10.0, "total_trades": 5}
        attr.save_performance("breakout", "R_100", result)

        expected = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        import sqlite3
        conn = sqlite3.connect(str(attr.db_path))
        row = conn.execute(
            "SELECT backtest_date FROM strategy_performance"
        ).fetchone()
        conn.close()
        assert row[0] == expected

    def test_explicit_backtest_date(self, attr):
        """An explicit backtest_date should be stored as-is."""
        result = {"total_pnl": 10.0, "total_trades": 5}
        attr.save_performance(
            "breakout", "R_100", result, backtest_date="2026-01-15"
        )
        import sqlite3
        conn = sqlite3.connect(str(attr.db_path))
        row = conn.execute(
            "SELECT backtest_date FROM strategy_performance"
        ).fetchone()
        conn.close()
        assert row[0] == "2026-01-15"

    def test_invalid_result_type_raises(self, attr):
        """A non-dict, non-BacktestResult, non-dataclass should raise TypeError."""
        with pytest.raises(TypeError, match="must be BacktestResult, dict, or dataclass"):
            attr.save_performance("breakout", "R_100", "not-a-result")  # type: ignore

    def test_total_trades_override(self, attr):
        """The total_trades parameter should override the value in result."""
        result = {"total_pnl": 10.0, "total_trades": 5}
        attr.save_performance(
            "breakout", "R_100", result, total_trades=99
        )
        import sqlite3
        conn = sqlite3.connect(str(attr.db_path))
        row = conn.execute(
            "SELECT total_trades FROM strategy_performance"
        ).fetchone()
        conn.close()
        assert row[0] == 99


# ---------------------------------------------------------------------------
#  (b) profitability_matrix tests
# ---------------------------------------------------------------------------


class TestProfitabilityMatrix:
    """Tests for StrategyAttribution.profitability_matrix()."""

    def test_matrix_shape_and_values(self, attr_seeded):
        """Matrix should be {symbol: {strategy: pnl}} with correct values."""
        matrix = attr_seeded.profitability_matrix(metric="total_pnl")

        assert "R_100" in matrix
        assert "RB100" in matrix
        assert matrix["R_100"]["breakout"] == pytest.approx(250.0)
        assert matrix["R_100"]["confluence"] == pytest.approx(400.0)
        assert matrix["RB100"]["volatility"] == pytest.approx(80.0)

    def test_matrix_sharpe_metric(self, attr_seeded):
        """Matrix with metric='sharpe' should return Sharpe values."""
        matrix = attr_seeded.profitability_matrix(metric="sharpe")

        assert matrix["R_100"]["breakout"] == pytest.approx(2.5)
        assert matrix["R_100"]["volatility"] == pytest.approx(0.3)

    def test_matrix_invalid_metric_raises(self, attr_seeded):
        """An invalid metric name should raise ValueError."""
        with pytest.raises(ValueError, match="is invalid"):
            attr_seeded.profitability_matrix(metric="nonexistent_metric")

    def test_matrix_empty_db(self, attr):
        """An empty DB should return an empty matrix."""
        matrix = attr.profitability_matrix()
        assert matrix == {}

    def test_matrix_aggregated_not_latest(self, attr):
        """When latest_only=False, multiple entries should be aggregated (SUM for pnl)."""
        result1 = {"total_pnl": 100.0, "total_trades": 10}
        result2 = {"total_pnl": 200.0, "total_trades": 10}
        attr.save_performance("breakout", "R_100", result1, backtest_date="2026-01-01")
        attr.save_performance("breakout", "R_100", result2, backtest_date="2026-01-02")

        # latest_only=True → last entry (id=2)
        matrix_latest = attr.profitability_matrix(metric="total_pnl", latest_only=True)
        assert matrix_latest["R_100"]["breakout"] == pytest.approx(200.0)

        # latest_only=False → sum
        matrix_agg = attr.profitability_matrix(metric="total_pnl", latest_only=False)
        assert matrix_agg["R_100"]["breakout"] == pytest.approx(300.0)


# ---------------------------------------------------------------------------
#  (c) best_strategy_per_symbol tests
# ---------------------------------------------------------------------------


class TestBestStrategyPerSymbol:
    """Tests for StrategyAttribution.best_strategy_per_symbol()."""

    def test_best_pnl_r_100_is_confluence(self, attr_seeded):
        """For R_100, best strategy by total_pnl should be confluence (400)."""
        best = attr_seeded.best_strategy_per_symbol(metric="total_pnl")
        assert best["R_100"][0] == "confluence"
        assert best["R_100"][1] == pytest.approx(400.0)

    def test_best_pnl_rb100_is_volatility(self, attr_seeded):
        """For RB100, best strategy by total_pnl should be volatility (80)."""
        best = attr_seeded.best_strategy_per_symbol(metric="total_pnl")
        assert best["RB100"][0] == "volatility"
        assert best["RB100"][1] == pytest.approx(80.0)

    def test_best_by_sharpe(self, attr_seeded):
        """Best by Sharpe should pick the strategy with the highest Sharpe."""
        best = attr_seeded.best_strategy_per_symbol(metric="sharpe")
        # R_100: breakout=2.5, volatility=0.3, confluence=3.0 → confluence
        assert best["R_100"][0] == "confluence"
        assert best["R_100"][1] == pytest.approx(3.0)

    def test_best_min_trades_filter_excludes(self, attr):
        """Strategies with fewer than min_trades should be excluded from best."""
        result_good = {"total_pnl": 500.0, "total_trades": 5}
        result_ok = {"total_pnl": 100.0, "total_trades": 50}
        attr.save_performance("high_pnl_few_trades", "R_100", result_good)
        attr.save_performance("lower_pnl_many_trades", "R_100", result_ok)

        # min_trades=10 → high_pnl_few_trades (5 trades) excluded
        best = attr.best_strategy_per_symbol(metric="total_pnl", min_trades=10)
        assert best["R_100"][0] == "lower_pnl_many_trades"

    def test_best_min_trades_no_qualifiers(self, attr):
        """If no strategy meets min_trades, the symbol should be absent."""
        result = {"total_pnl": 100.0, "total_trades": 3}
        attr.save_performance("breakout", "R_100", result)

        best = attr.best_strategy_per_symbol(metric="total_pnl", min_trades=10)
        assert "R_100" not in best

    def test_best_max_dd_is_lower_is_better(self, attr):
        """For max_dd metric, the strategy with the LOWEST drawdown should win."""
        result_high_dd = {
            "total_pnl": 500.0, "max_drawdown": 0.15, "total_trades": 30,
        }
        result_low_dd = {
            "total_pnl": 100.0, "max_drawdown": 0.02, "total_trades": 30,
        }
        attr.save_performance("risky", "R_100", result_high_dd)
        attr.save_performance("conservative", "R_100", result_low_dd)

        best = attr.best_strategy_per_symbol(metric="max_dd")
        assert best["R_100"][0] == "conservative"
        assert best["R_100"][1] == pytest.approx(0.02)

    def test_best_empty_db(self, attr):
        """An empty DB should return an empty dict."""
        best = attr.best_strategy_per_symbol()
        assert best == {}


# ---------------------------------------------------------------------------
#  (d) dashboard_payload tests
# ---------------------------------------------------------------------------


class TestDashboardPayload:
    """Tests for StrategyAttribution.dashboard_payload()."""

    def test_payload_has_expected_keys(self, attr_seeded):
        """Payload should have matrix, best_per_symbol, metric, min_trades, generated_at."""
        payload = attr_seeded.dashboard_payload()

        for key in ("matrix", "best_per_symbol", "metric", "min_trades", "generated_at"):
            assert key in payload, f"Payload missing key '{key}'"

    def test_payload_matrix_and_best_consistent(self, attr_seeded):
        """The matrix and best_per_symbol in the payload should be consistent."""
        payload = attr_seeded.dashboard_payload(metric="total_pnl")
        matrix = payload["matrix"]
        best = payload["best_per_symbol"]

        # The best strategy for R_100 in the matrix should match
        best_strat, best_val = best["R_100"]
        assert matrix["R_100"][best_strat] == pytest.approx(best_val)

    def test_payload_metric_and_min_trades_echo(self, attr_seeded):
        """Passed metric and min_trades should be echoed in the payload."""
        payload = attr_seeded.dashboard_payload(metric="sharpe", min_trades=15)
        assert payload["metric"] == "sharpe"
        assert payload["min_trades"] == 15

    def test_payload_is_json_serializable(self, attr_seeded):
        """The entire payload should be JSON-serialisable for API responses."""
        import json
        payload = attr_seeded.dashboard_payload()
        # Should not raise
        json_str = json.dumps(payload)
        # Round-trip
        restored = json.loads(json_str)
        assert "matrix" in restored
        assert "best_per_symbol" in restored
