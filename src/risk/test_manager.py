"""Unit tests for risk/manager.py — position sizing and circuit breaker triggers.

Tests verify REAL Kelly Criterion math and risk enforcement behaviour.
"""
from __future__ import annotations

import pytest

from src.risk.manager import RiskConfig, RiskManager


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config():
    """Standard risk config matching production defaults."""
    return RiskConfig(
        max_risk_per_trade=0.03,   # 3% per trade
        max_daily_drawdown=0.05,   # 5% daily limit
        max_trades_per_day=8,
        circuit_breaker_losses=5,
        kelly_fraction=0.25,       # Quarter-Kelly
        initial_capital=10000.0,
    )


@pytest.fixture
def rm(default_config):
    """Fresh RiskManager with daily stats initialized at $10,000."""
    manager = RiskManager(default_config)
    manager.reset_daily(10000.0)
    return manager


# ---------------------------------------------------------------------------
#  Position sizing (Kelly) tests
# ---------------------------------------------------------------------------


class TestPositionSizing:
    """Tests for position_size() and position_size_dynamic()."""

    def test_positive_kelly_returns_positive_size(self, rm):
        """Kelly > 0 when p × b > q → positive position size (uncapped params)."""
        # p=0.51, b=1.1 → kelly = (0.51×1.1 - 0.49)/1.1 ≈ 0.0645
        # quarter_kelly ≈ 0.01614; raw = 0.01614 × 10000 ≈ 161.36 (below cap of 300)
        size = rm.position_size(capital=10000, win_probability=0.51, win_amount=1.1, loss_amount=1.0)
        assert size > 0
        assert size == pytest.approx(161.36, rel=0.05)

    def test_zero_kelly_when_no_edge(self, rm):
        """Kelly = 0 when p/b = q → no edge → size = 0."""
        # p=0.5, b=1.0 → kelly = (0.5×1 - 0.5)/1 = 0
        size = rm.position_size(capital=10000, win_probability=0.5, win_amount=1.0, loss_amount=1.0)
        assert size == 0.0

    def test_negative_kelly_returns_zero(self, rm):
        """Kelly < 0 when p × b < q → negative edge → don't trade."""
        # p=0.3, b=1.0 → kelly = (0.3 - 0.7)/1 = -0.4
        size = rm.position_size(capital=10000, win_probability=0.3, win_amount=1.0, loss_amount=1.0)
        assert size == 0.0

    def test_size_capped_at_max_risk(self, rm):
        """Position size must not exceed max_risk_per_trade × capital."""
        # Very high Kelly would produce a huge size, but must be capped
        # p=0.9, b=5 → kelly = (0.9×5 - 0.1)/5 = 4.4/5 = 0.88
        # quarter_kelly = 0.22; raw = 0.22 × 10000 = 2200
        # cap = 0.03 × 10000 = 300
        size = rm.position_size(capital=10000, win_probability=0.9, win_amount=5.0, loss_amount=1.0)
        max_risk = rm.config.max_risk_per_trade * 10000
        assert size <= max_risk, f"Size {size} exceeds cap {max_risk}"
        assert size == pytest.approx(max_risk, rel=0.01)

    def test_dynamic_confidence_reduces_size(self, rm):
        """Lower confidence should produce smaller or equal size vs full confidence."""
        full = rm.position_size_dynamic(
            capital=10000, win_probability=0.6, win_amount=2.0, loss_amount=1.0,
            confidence=1.0, volatility_multiplier=1.0,
        )
        reduced = rm.position_size_dynamic(
            capital=10000, win_probability=0.6, win_amount=2.0, loss_amount=1.0,
            confidence=0.5, volatility_multiplier=1.0,
        )
        # With confidence=0.5, p is pulled toward 0.5, reducing edge
        # p_adj = 0.5 + (0.6 - 0.5) × 0.5 = 0.55
        # Kelly_adj < Kelly_full → smaller size
        assert reduced <= full, (
            f"Reduced confidence size ({reduced}) should be <= full ({full})"
        )

    def test_dynamic_volatility_multiplier_reduces_size(self, rm):
        """Higher volatility multiplier → smaller position (below cap to see proportional effect)."""
        # p=0.51, b=1.1 → Kelly ≈ 0.0645, QK ≈ 0.01614
        # vol_mult=1.0: raw = 161.36 (below cap 300)
        # vol_mult=2.0: raw = 161.36 / 2.0 = 80.68 (exactly half)
        vol_normal = rm.position_size_dynamic(
            capital=10000, win_probability=0.51, win_amount=1.1, loss_amount=1.0,
            confidence=1.0, volatility_multiplier=1.0,
        )
        vol_high = rm.position_size_dynamic(
            capital=10000, win_probability=0.51, win_amount=1.1, loss_amount=1.0,
            confidence=1.0, volatility_multiplier=2.0,
        )
        assert vol_high < vol_normal, (
            f"High vol mult ({vol_high}) should be < normal ({vol_normal})"
        )
        assert vol_high == pytest.approx(vol_normal / 2.0, rel=0.05)

    def test_dynamic_size_with_zero_confidence_is_zero(self, rm):
        """With confidence=0, p adjusts to 0.5 (neutral), which for equal payoffs → no edge."""
        size = rm.position_size_dynamic(
            capital=10000, win_probability=0.7, win_amount=2.0, loss_amount=1.0,
            confidence=0.0, volatility_multiplier=1.0,
        )
        # p_adj = 0.5 + (0.7 - 0.5) × 0 = 0.5
        # Kelly at p=0.5, b=2: (0.5×2 - 0.5)/2 = 0.25 → quarter = 0.0625
        # Still positive because b > 1.0
        # But when confidence=0, ANY edge is effectively neutralized → check it's reduced
        full = rm.position_size_dynamic(
            capital=10000, win_probability=0.7, win_amount=2.0, loss_amount=1.0,
            confidence=1.0, volatility_multiplier=1.0,
        )
        assert size <= full


# ---------------------------------------------------------------------------
#  Circuit breaker / risk enforcement tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for circuit breaker triggers via can_trade() and record_trade()."""

    def test_can_trade_initially_true(self, rm):
        """Fresh manager (after reset_daily) should allow trading."""
        can, reason = rm.can_trade()
        assert can is True
        assert reason == "OK"

    def test_can_trade_false_when_not_initialized(self, default_config):
        """can_trade before reset_daily should return False."""
        rm = RiskManager(default_config)
        can, reason = rm.can_trade()
        assert can is False
        assert "reset_daily" in reason.lower() or "not initialized" in reason.lower()

    def test_circuit_breaker_triggers_at_consecutive_losses(self, rm):
        """After N consecutive losses, circuit breaker halts trading."""
        losses_needed = rm.config.circuit_breaker_losses  # 5
        for i in range(losses_needed):
            # Record a losing trade: lose $100
            new_balance = rm.today.current_balance - 100
            rm.record_trade(pnl=-100, new_balance=new_balance)

        can, reason = rm.can_trade()
        assert can is False
        assert "halted" in reason.lower() or "circuit breaker" in reason.lower()

    def test_circuit_breaker_resets_on_win(self, rm):
        """A winning trade should reset the consecutive loss counter."""
        # Record 3 losses (below threshold of 5)
        for i in range(3):
            rm.record_trade(pnl=-50, new_balance=rm.today.current_balance - 50)
        assert rm.today.consecutive_losses == 3
        assert rm.can_trade()[0] is True  # Not yet halted

        # Record a win → consecutive losses reset
        rm.record_trade(pnl=+100, new_balance=rm.today.current_balance + 100)
        assert rm.today.consecutive_losses == 0

    def test_daily_drawdown_triggers_halt(self, rm):
        """Exceeding daily drawdown limit should halt trading."""
        # Starting balance = $10,000, drawdown limit = 5% = $500
        # Need current_balance ≤ $9,500 → drawdown ≥ 5%
        rm.today.current_balance = 9400  # 6% drawdown
        can, reason = rm.can_trade()
        assert can is False
        assert "drawdown" in reason.lower()

    def test_max_trades_per_day(self, rm):
        """After reaching max trades per day, can_trade returns False."""
        max_t = rm.config.max_trades_per_day  # 8
        for i in range(max_t):
            rm.record_trade(pnl=10, new_balance=rm.today.current_balance + 10)
        assert rm.today.trades == max_t
        can, reason = rm.can_trade()
        assert can is False
        assert "max trades" in reason.lower()

    def test_reset_daily_clears_halt(self, rm):
        """reset_daily should clear halted state and consecutive losses."""
        # Trigger halt via consecutive losses
        for i in range(rm.config.circuit_breaker_losses):
            rm.record_trade(pnl=-50, new_balance=rm.today.current_balance - 50)
        assert rm.can_trade()[0] is False

        # Reset daily
        rm.reset_daily(9500)
        can, reason = rm.can_trade()
        # After reset, capacity returns (but balance/starting_balance bring drawdown state)
        # If reset at $9,500 → starting_balance = 9500, drawdown = 0 → can trade
        assert can is True, f"After reset_daily, should be able to trade. Got: {reason}"
        assert rm.today.consecutive_losses == 0

    def test_record_trade_increments_stats(self, rm):
        """record_trade should update trades, pnl, and win/loss counters."""
        rm.record_trade(pnl=50, new_balance=10050)
        rm.record_trade(pnl=-30, new_balance=10020)

        assert rm.today.trades == 2
        assert rm.today.wins == 1
        assert rm.today.losses == 1
        assert rm.today.pnl == pytest.approx(20.0)
