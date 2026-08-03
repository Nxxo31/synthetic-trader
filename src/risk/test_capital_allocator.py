"""Unit tests for risk/capital_allocator.py — capital division and micro-stake calculation.

Tests verify REAL capital split math and delegation to position_size_dynamic.
"""
from __future__ import annotations

import pytest

from src.risk.capital_allocator import (
    CapitalAllocator,
    CapitalAllocatorConfig,
)
from src.risk.manager import RiskConfig, RiskManager

# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_alloc_config():
    """Standard allocator config matching task defaults: 80/20 split."""
    return CapitalAllocatorConfig(
        reserva_pct=0.80,
        superávit_diario_pct=0.20,
        initial_capital=10000.0,
        min_surplus=10.0,
        rebalance_daily=True,
    )


@pytest.fixture
def risk_config():
    """Standard risk config matching production defaults."""
    return RiskConfig(
        max_risk_per_trade=0.03,
        max_daily_drawdown=0.05,
        max_trades_per_day=8,
        circuit_breaker_losses=5,
        kelly_fraction=0.25,
        initial_capital=10000.0,
    )


@pytest.fixture
def risk_manager(risk_config):
    """Fresh RiskManager with daily stats initialized."""
    rm = RiskManager(risk_config)
    rm.reset_daily(10000.0)
    return rm


@pytest.fixture
def allocator(default_alloc_config, risk_manager):
    """CapitalAllocator wired to a RiskManager, reset at $10,000."""
    alloc = CapitalAllocator(default_alloc_config, risk_manager)
    alloc.reset_daily(10000.0)
    return alloc


# ---------------------------------------------------------------------------
#  Config validation tests
# ---------------------------------------------------------------------------


class TestCapitalAllocatorConfig:
    """Tests for CapitalAllocatorConfig.validate()."""

    def test_valid_default_config_has_no_errors(self):
        """Default 80/20 split should validate cleanly."""
        config = CapitalAllocatorConfig()
        errors = config.validate()
        assert errors == [], f"Default config should be valid, got: {errors}"

    def test_invalid_split_sum(self):
        """If reserva + superávit ≠ 1.0, validation must flag it."""
        config = CapitalAllocatorConfig(
            reserva_pct=0.70,
            superávit_diario_pct=0.20,  # 0.70 + 0.20 = 0.90 ≠ 1.0
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "1.0" in errors[0]

    def test_out_of_range_percentage(self):
        """Percentages must be in (0, 1)."""
        config = CapitalAllocatorConfig(
            reserva_pct=1.0,  # boundary, not allowed
            superávit_diario_pct=0.0,
        )
        errors = config.validate()
        assert any("reserva_pct" in e for e in errors)
        assert any("superávit_diario_pct" in e for e in errors)

    def test_negative_initial_capital_rejected(self):
        """initial_capital must be positive."""
        config = CapitalAllocatorConfig(initial_capital=-100.0)
        errors = config.validate()
        assert any("initial_capital" in e for e in errors)

    def test_allocator_raises_on_invalid_config(self):
        """CapitalAllocator must raise ValueError on invalid config at construction."""
        bad_config = CapitalAllocatorConfig(
            reserva_pct=0.50,
            superávit_diario_pct=0.30,  # 0.80 ≠ 1.0
        )
        with pytest.raises(ValueError, match="inválida"):
            CapitalAllocator(bad_config)


# ---------------------------------------------------------------------------
#  Capital division tests
# ---------------------------------------------------------------------------


class TestCapitalDivision:
    """Tests for reset_daily() — splitting capital into reserva + superávit."""

    def test_default_split_80_20(self, allocator):
        """Default 80/20 split on $10,000 → reserva $8,000, superávit $2,000."""
        assert allocator.reserva == pytest.approx(8000.0, abs=0.01)
        assert allocator.state.superávit_diario == pytest.approx(2000.0, abs=0.01)
        assert allocator.state.capital_total == pytest.approx(10000.0, abs=0.01)

    def test_custom_split(self, risk_manager):
        """Custom 70/30 split should produce matching amounts."""
        config = CapitalAllocatorConfig(
            reserva_pct=0.70,
            superávit_diario_pct=0.30,
            initial_capital=10000.0,
        )
        alloc = CapitalAllocator(config, risk_manager)
        alloc.reset_daily(10000.0)
        assert alloc.reserva == pytest.approx(7000.0, abs=0.01)
        assert alloc.state.superávit_diario == pytest.approx(3000.0, abs=0.01)

    def test_reserva_plus_surplus_equals_total(self, allocator):
        """reserva + superávit_diario must equal capital_total (conservation)."""
        total = allocator.reserva + allocator.state.superávit_diario
        assert total == pytest.approx(allocator.state.capital_total, abs=0.02)

    def test_min_surplus_enforced(self, risk_manager):
        """If calculated surplus < min_surplus, it's forced to min_surplus."""
        config = CapitalAllocatorConfig(
            reserva_pct=0.80,
            superávit_diario_pct=0.20,
            initial_capital=20.0,  # 20% of $20 = $4 < min_surplus=$10
            min_surplus=10.0,
        )
        alloc = CapitalAllocator(config, risk_manager)
        alloc.reset_daily(20.0)
        assert alloc.state.superávit_diario >= config.min_surplus

    def test_reset_with_explicit_capital(self, allocator):
        """reset_daily(explicit) uses that capital, not the previous state."""
        allocator.reset_daily(5000.0)
        assert allocator.state.capital_total == pytest.approx(5000.0, abs=0.01)
        assert allocator.reserva == pytest.approx(4000.0, abs=0.01)
        assert allocator.state.superávit_diario == pytest.approx(1000.0, abs=0.01)

    def test_reset_increments_capital_with_positive_pnl(self, allocator):
        """With rebalance_daily=True, day 2 uses total + P&L from day 1."""
        # Simulate: day 1 had +$100 P&L
        allocator.record_trade(100.0)
        allocator.reset_daily()  # no explicit capital → uses total + pnl
        assert allocator.state.capital_total == pytest.approx(10100.0, abs=0.01)
        assert allocator.reserva == pytest.approx(8080.0, abs=0.01)
        assert allocator.state.superávit_diario == pytest.approx(2020.0, abs=0.01)

    def test_reset_decrements_capital_with_negative_pnl(self, allocator):
        """With rebalance_daily=True, day 2 reflects previous day's loss."""
        allocator.record_trade(-200.0)
        allocator.reset_daily()
        assert allocator.state.capital_total == pytest.approx(9800.0, abs=0.01)

    def test_no_rebalance_keeps_capital_base(self, risk_manager):
        """With rebalance_daily=False, gains don't grow the capital base."""
        config = CapitalAllocatorConfig(
            reserva_pct=0.80,
            superávit_diario_pct=0.20,
            initial_capital=10000.0,
            rebalance_daily=False,
        )
        alloc = CapitalAllocator(config, risk_manager)
        alloc.reset_daily(10000.0)
        alloc.record_trade(500.0)
        alloc.reset_daily()  # no explicit capital
        # Capital total stays at original despite gain
        assert alloc.state.capital_total == pytest.approx(10000.0, abs=0.01)

    def test_negative_or_zero_capital_deactivates(self, risk_manager):
        """reset_daily with capital ≤ 0 should deactivate the allocator."""
        config = CapitalAllocatorConfig(
            reserva_pct=0.80,
            superávit_diario_pct=0.20,
            initial_capital=10000.0,
        )
        alloc = CapitalAllocator(config, risk_manager)
        alloc.reset_daily(-100.0)
        assert alloc.is_active is False
        assert alloc.calculate_micro_stake(
            win_probability=0.6, win_amount=2.0, loss_amount=1.0,
        ) == 0.0


# ---------------------------------------------------------------------------
#  Micro-stake calculation tests
# ---------------------------------------------------------------------------


class TestMicroStakeCalculation:
    """Tests for calculate_micro_stake() — delegation to position_size_dynamic."""

    def test_micro_stake_positive_when_edge(self, allocator):
        """Valid edge → positive micro-stake (delegated to Kelly)."""
        # p=0.6, b=2.0 → Kelly positive, quarter-Kelly
        stake = allocator.calculate_micro_stake(
            win_probability=0.6,
            win_amount=2.0,
            loss_amount=1.0,
            confidence=1.0,
            volatility_multiplier=1.0,
        )
        assert stake > 0
        # Capital base is superávit ($2000), capped at 3% = $60
        assert stake <= 0.03 * 2000.0

    def test_micro_stake_zero_when_no_edge(self, allocator):
        """No edge (Kelly ≤ 0) → micro-stake is 0.0."""
        # p=0.3, b=1.0 → Kelly negative
        stake = allocator.calculate_micro_stake(
            win_probability=0.3,
            win_amount=1.0,
            loss_amount=1.0,
        )
        assert stake == 0.0

    def test_micro_stake_uses_superavit_as_capital_base(self, allocator):
        """Micro-stake is sized on superávit ($2000), NOT total ($10000)."""
        # Use params where the cap kicks in to verify which capital:
        # cap = max_risk_per_trade × capital_base
        # If capital_base = 2000, cap = 60. If 10000, cap = 300.
        stake = allocator.calculate_micro_stake(
            win_probability=0.9,
            win_amount=5.0,
            loss_amount=1.0,
            confidence=1.0,
        )
        # Kelly is huge → capped at max_risk_per_trade × superávit
        assert stake <= 0.03 * 2000.0 + 0.01  # $60 ± rounding
        assert stake < 0.03 * 10000.0  # Definitely less than $300

    def test_micro_stake_acotado_a_superavit_disponible(self, allocator):
        """Stake cannot exceed the remaining daily surplus."""
        # First stake consumes most of the surplus
        first = allocator.calculate_micro_stake(
            win_probability=0.9,
            win_amount=5.0,
            loss_amount=1.0,
        )
        assert first > 0
        # Record a loss to shrink the available surplus
        allocator.record_trade(-first)
        # Second stake should be ≤ remaining disponible
        disponible_before = allocator.superávit_disponible
        second = allocator.calculate_micro_stake(
            win_probability=0.9,
            win_amount=5.0,
            loss_amount=1.0,
        )
        assert second <= disponible_before + 0.01

    def test_micro_stake_returns_zero_without_risk_manager(self, default_alloc_config):
        """Allocator without a RiskManager must return 0.0 (no Kelly engine)."""
        alloc = CapitalAllocator(default_alloc_config, risk_manager=None)
        alloc.reset_daily(10000.0)
        stake = alloc.calculate_micro_stake(
            win_probability=0.6,
            win_amount=2.0,
            loss_amount=1.0,
        )
        assert stake == 0.0

    def test_micro_stake_zero_when_not_active(self, default_alloc_config, risk_manager):
        """If allocator hasn't been reset_daily()'d, stake is 0.0."""
        alloc = CapitalAllocator(default_alloc_config, risk_manager)
        assert alloc.is_active is False
        stake = alloc.calculate_micro_stake(
            win_probability=0.6,
            win_amount=2.0,
            loss_amount=1.0,
        )
        assert stake == 0.0

    def test_micro_stake_zero_when_superavit_depleted(self, allocator):
        """When superávit_disponible ≤ 0, stake is 0.0."""
        # Force-deplete: record a huge loss
        allocator.record_trade(-3000.0)
        assert allocator.superávit_disponible <= 0
        stake = allocator.calculate_micro_stake(
            win_probability=0.6,
            win_amount=2.0,
            loss_amount=1.0,
        )
        assert stake == 0.0

    def test_confidence_reduces_stake(self, allocator):
        """Lower confidence → smaller or equal stake (passed through to Kelly)."""
        reset_alloc = allocator
        reset_alloc.reset_daily(10000.0)
        full = reset_alloc.calculate_micro_stake(
            win_probability=0.6,
            win_amount=2.0,
            loss_amount=1.0,
            confidence=1.0,
        )
        reset_alloc.reset_daily(10000.0)
        reduced = reset_alloc.calculate_micro_stake(
            win_probability=0.6,
            win_amount=2.0,
            loss_amount=1.0,
            confidence=0.5,
        )
        assert reduced <= full, (
            f"Reduced confidence ({reduced}) should be <= full ({full})"
        )

    def test_volatility_multiplier_reduces_stake(self, allocator):
        """Higher volatility_multiplier → smaller stake (passed through to Kelly)."""
        reset_alloc = allocator
        reset_alloc.reset_daily(10000.0)
        normal = reset_alloc.calculate_micro_stake(
            win_probability=0.51,
            win_amount=1.1,
            loss_amount=1.0,
            confidence=1.0,
            volatility_multiplier=1.0,
        )
        reset_alloc.reset_daily(10000.0)
        high_vol = reset_alloc.calculate_micro_stake(
            win_probability=0.51,
            win_amount=1.1,
            loss_amount=1.0,
            confidence=1.0,
            volatility_multiplier=2.0,
        )
        assert high_vol <= normal // 2 + 1  # roughly half (rounding)

    def test_trades_count_increments(self, allocator):
        """Each successful micro-stake increments trades_count."""
        assert allocator.state.trades_count == 0
        allocator.calculate_micro_stake(
            win_probability=0.6, win_amount=2.0, loss_amount=1.0,
        )
        assert allocator.state.trades_count == 1
        allocator.calculate_micro_stake(
            win_probability=0.6, win_amount=2.0, loss_amount=1.0,
        )
        assert allocator.state.trades_count == 2

    def test_superavit_usado_increments(self, allocator):
        """superávit_usado increases by the stake amount."""
        stake = allocator.calculate_micro_stake(
            win_probability=0.6,
            win_amount=2.0,
            loss_amount=1.0,
        )
        assert allocator.state.superávit_usado == pytest.approx(stake, abs=0.02)

    def test_capital_override_changes_base(self, allocator):
        """capital_override uses the provided capital instead of superávit."""
        # With override=10000, cap = 3% × 10000 = 300 (vs 60 on superávit)
        stake = allocator.calculate_micro_stake(
            win_probability=0.9,
            win_amount=5.0,
            loss_amount=1.0,
            capital_override=10000.0,
        )
        assert stake == pytest.approx(300.0, abs=1.0)


# ---------------------------------------------------------------------------
#  Trade recording tests
# ---------------------------------------------------------------------------


class TestTradeRecording:
    """Tests for record_trade() — updating superávit and P&L."""

    def test_positive_pnl_grows_superavit(self, allocator):
        """A winning trade increases superávit_diario and total_pnl."""
        initial_superavit = allocator.state.superávit_diario
        allocator.record_trade(50.0)
        assert allocator.state.superávit_diario == pytest.approx(
            initial_superavit + 50.0, abs=0.01
        )
        assert allocator.state.total_pnl == pytest.approx(50.0, abs=0.01)

    def test_negative_pnl_shrinks_superavit(self, allocator):
        """A losing trade decreases superávit_diario and total_pnl."""
        initial_superavit = allocator.state.superávit_diario
        allocator.record_trade(-30.0)
        assert allocator.state.superávit_diario == pytest.approx(
            initial_superavit - 30.0, abs=0.01
        )
        assert allocator.state.total_pnl == pytest.approx(-30.0, abs=0.01)

    def test_record_trade_when_not_active_ignored(self, default_alloc_config, risk_manager):
        """record_trade before reset_daily is a no-op (warning logged)."""
        alloc = CapitalAllocator(default_alloc_config, risk_manager)
        alloc.record_trade(100.0)
        assert alloc.state.total_pnl == 0.0


# ---------------------------------------------------------------------------
#  State & config exposure tests
# ---------------------------------------------------------------------------


class TestStateExposure:
    """Tests for get_config(), get_state(), daily_report(), properties."""

    def test_get_config_returns_all_fields(self, allocator):
        """get_config() must expose every config field."""
        config = allocator.get_config()
        assert "reserva_pct" in config
        assert "superávit_diario_pct" in config
        assert "initial_capital" in config
        assert "min_surplus" in config
        assert "rebalance_daily" in config
        assert config["reserva_pct"] == 0.80
        assert config["superávit_diario_pct"] == 0.20

    def test_get_state_returns_complete_snapshot(self, allocator):
        """get_state() must return a complete, serializable snapshot."""
        state = allocator.get_state()
        expected_keys = {
            "config", "date", "capital_total", "reserva", "superávit_diario",
            "superávit_usado", "superávit_disponible", "trades_count",
            "total_pnl", "return_pct", "is_active", "has_risk_manager",
        }
        assert expected_keys.issubset(set(state.keys()))
        assert state["is_active"] is True
        assert state["has_risk_manager"] is True
        assert state["capital_total"] == pytest.approx(10000.0, abs=0.01)
        assert state["reserva"] == pytest.approx(8000.0, abs=0.01)
        assert state["superávit_diario"] == pytest.approx(2000.0, abs=0.01)
        assert state["superávit_disponible"] == pytest.approx(2000.0, abs=0.01)
        assert state["return_pct"] == 0.0

    def test_return_pct_calculation(self, allocator):
        """return_pct = total_pnl / capital_total."""
        allocator.record_trade(100.0)
        state = allocator.get_state()
        expected_return = 100.0 / 10000.0
        assert state["return_pct"] == pytest.approx(expected_return, abs=0.001)

    def test_daily_report_is_alias_of_get_state(self, allocator):
        """daily_report() must return the same structure as get_state()."""
        assert allocator.daily_report() == allocator.get_state()

    def test_properties_expose_state(self, allocator):
        """Convenience properties: superávit_disponible, reserva, is_active."""
        assert allocator.superávit_disponible == allocator.state.superávit_disponible
        assert allocator.reserva == allocator.state.reserva
        assert allocator.is_active == allocator.state.is_active

    def test_has_risk_manager_false_when_none(self, default_alloc_config):
        """get_state has_risk_manager is False when None passed."""
        alloc = CapitalAllocator(default_alloc_config, risk_manager=None)
        alloc.reset_daily(10000.0)
        state = alloc.get_state()
        assert state["has_risk_manager"] is False
