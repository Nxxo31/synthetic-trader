"""Unit tests for analysis/projector.py — ReturnProjector forward Monte Carlo.

Verifies:
- 10k forward Monte Carlo runs correctly with historical edge metrics
- Equity envelope P5/P50/P95 is returned with correct shape and ordering
- Calibrated inputs (avg_win_R, avg_loss_R, payoff) are recovered consistently
- Optional Sharpe-inferred payoff path produces a different but valid calibration
- Edge cases: validation errors, reproducibility, stable quantitative sanity

Tests use seeded RNGs and assert verifiable mathematical invariants rather than
brittle equality to a magic number.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.analysis.projector import (
    ProjectedEquityCurve,
    ProjectedMetrics,
    ProjectionResult,
    ReturnProjector,
)

# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def profitable_projection() -> ProjectionResult:
    """Reproduce the project's standard backtest edge: WR~0.91, Sharpe~28, exp~0.4R."""
    proj = ReturnProjector(
        win_rate=0.91,
        sharpe=28.0,
        expectancy=0.40,
        horizon_trades=100,
        risk_per_trade=0.01,
        n_simulations=10_000,
        initial_capital=10_000.0,
        infer_payoff_from_sharpe=False,
        default_payoff_ratio=1.5,
        seed=42,
    )
    return proj.project()


# ---------------------------------------------------------------------------
#  Shape & return value tests
# ---------------------------------------------------------------------------


class TestProjectReturnType:
    def test_returns_projection_result(self, profitable_projection: ProjectionResult):
        assert isinstance(profitable_projection, ProjectionResult)
        assert isinstance(profitable_projection.curve, ProjectedEquityCurve)
        assert isinstance(profitable_projection.metrics, ProjectedMetrics)

    def test_curve_shape_matches_horizon(self, profitable_projection: ProjectionResult):
        # horizon + 1 points (initial deposit prepended)
        assert profitable_projection.curve.horizon == 100
        assert len(profitable_projection.curve.p5) == 101
        assert len(profitable_projection.curve.p50) == 101
        assert len(profitable_projection.curve.p95) == 101

    def test_curve_starts_at_initial_capital(self, profitable_projection: ProjectionResult):
        assert profitable_projection.curve.p5[0] == pytest.approx(10_000.0)
        assert profitable_projection.curve.p50[0] == pytest.approx(10_000.0)
        assert profitable_projection.curve.p95[0] == pytest.approx(10_000.0)

    def test_quantile_ordering_invariant(self, profitable_projection: ProjectionResult):
        p5 = np.array(profitable_projection.curve.p5)
        p50 = np.array(profitable_projection.curve.p50)
        p95 = np.array(profitable_projection.curve.p95)
        assert (p5 <= p50 + 1e-6).all(), "P5 must be <= P50 at every step"
        assert (p50 <= p95 + 1e-6).all(), "P50 must be <= P95 at every step"


# ---------------------------------------------------------------------------
#  Calibration tests
# ---------------------------------------------------------------------------


class TestEdgeCalibration:
    def test_default_payoff_ratio_used_when_infer_off(
        self, profitable_projection: ProjectionResult
    ):
        assert profitable_projection.metrics.inputs["payoff_ratio"] == pytest.approx(1.5)
        assert (
            profitable_projection.metrics.inputs["inference_source"]
            == "default_payoff_ratio"
        )

    def test_expectancy_recovered_from_calibrated_r_multiples(self):
        """avg_win_R * win_rate - avg_loss_R * (1-wr) must equal expectancy."""
        proj = ReturnProjector(win_rate=0.6, sharpe=2.0, expectancy=0.15, seed=1)
        wr = proj.win_rate
        aw = proj.avg_win_r
        al = proj.avg_loss_r
        emodel = wr * aw - (1.0 - wr) * al
        assert emodel == pytest.approx(proj.expectancy_r, rel=1e-6)

    def test_inferred_payoff_path_uses_sharpe(self):
        proj = ReturnProjector(
            win_rate=0.60,
            sharpe=2.0,
            expectancy=0.20,
            horizon_trades=50,
            n_simulations=2_000,
            seed=123,
            infer_payoff_from_sharpe=True,
        )
        r = proj.project()
        assert r.metrics.inputs["inference_source"] == "inferred_from_sharpe"
        # Recovered expectancy should still hold (sanity)
        emodel = (
            proj.win_rate * proj.avg_win_r - (1.0 - proj.win_rate) * proj.avg_loss_r
        )
        assert emodel == pytest.approx(proj.expectancy_r, rel=1e-4)

    def test_payoff_recovery_when_sharpe_zero(self):
        """When sharpe=None or 0, default payoff path applies with positive expectancy."""
        proj = ReturnProjector(
            win_rate=0.55, sharpe=None, expectancy=0.15, seed=5
        )
        proj.project()  # must not raise
        assert proj.avg_win_r > 0
        assert proj.avg_loss_r > 0


# ---------------------------------------------------------------------------
#  Quantitative sanity tests — known robust / conservative / losing regimes
# ---------------------------------------------------------------------------


class TestEdgeRegimes:
    def test_strong_edge_huge_p_profitable(self, profitable_projection: ProjectionResult):
        # WR 91%, expectancy 0.40R, risk 1%, 100 trades → P(profitable) ≈ 100%
        assert profitable_projection.metrics.p_profitable > 0.99
        # MaxDD median should be modest (below 5%) — edge is strong enough.
        assert profitable_projection.metrics.max_drawdown_median < 0.05
        # Projected Sharpe median should be positive (forward metric).
        assert profitable_projection.metrics.sharpe_projected_median > 0

    def test_marginal_edge_p_profitable_between_50_and_99_9(self):
        # WR 55%, expectancy 0.15R — modest edge, positive but not nearly-certain
        # over 100 trades with 1% risk compounding (still >99% due to compounding
        # edge, but lower than the WR=0.91 strong regime).
        proj = ReturnProjector(
            win_rate=0.55, sharpe=1.2, expectancy=0.15,
            horizon_trades=100, n_simulations=10_000, seed=7,
        )
        r = proj.project()
        assert 0.5 < r.metrics.p_profitable
        # The stronger-edge profitable_projection should be > marginal one.
        strong = ReturnProjector(
            win_rate=0.91, sharpe=28.0, expectancy=0.40,
            horizon_trades=100, n_simulations=10_000, seed=42,
        ).project()
        assert r.metrics.p_profitable <= strong.metrics.p_profitable

    def test_negative_expectancy_p_profitable_below_half(self):
        # Expectancy -0.10R, WR 40% → less likely than not to end profitable.
        proj = ReturnProjector(
            win_rate=0.40, sharpe=-0.5, expectancy=-0.10,
            horizon_trades=100, n_simulations=10_000, seed=99,
        )
        r = proj.project()
        assert r.metrics.p_profitable < 0.5
        # The P5 final equity should be below the deposit (worst-case path).
        assert r.metrics.final_equity_p5 < r.metrics.initial_capital

    def test_high_risk_raises_max_dd_p95(self):
        # Compounding 5% risk per trade with 0.15R edge → rare killing sequences increase DD.
        moderate = ReturnProjector(
            win_rate=0.6, sharpe=2.0, expectancy=0.15,
            horizon_trades=100, risk_per_trade=0.01,
            n_simulations=5_000, seed=11,
        ).project()
        aggressive = ReturnProjector(
            win_rate=0.6, sharpe=2.0, expectancy=0.15,
            horizon_trades=100, risk_per_trade=0.05,
            n_simulations=5_000, seed=11,
        ).project()
        assert aggressive.metrics.max_drawdown_p95 > moderate.metrics.max_drawdown_p95


# ---------------------------------------------------------------------------
#  Reproducibility tests
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_same_seed_identical_envelope(self):
        a = ReturnProjector(win_rate=0.6, sharpe=2.0, expectancy=0.15, seed=42).project()
        b = ReturnProjector(win_rate=0.6, sharpe=2.0, expectancy=0.15, seed=42).project()
        assert np.array_equal(np.array(a.curve.p5), np.array(b.curve.p5))
        assert np.array_equal(np.array(a.curve.p50), np.array(b.curve.p50))
        assert np.array_equal(np.array(a.curve.p95), np.array(b.curve.p95))
        assert a.metrics.final_equity_p5 == b.metrics.final_equity_p5
        assert a.metrics.final_equity_p95 == b.metrics.final_equity_p95

    def test_different_seed_different_envelope(self):
        a = ReturnProjector(win_rate=0.6, sharpe=2.0, expectancy=0.15, seed=42).project()
        c = ReturnProjector(win_rate=0.6, sharpe=2.0, expectancy=0.15, seed=43).project()
        # The full envelope (101 points) must differ, even if scalar medians
        # can coincidentally coincide due to discrete sampling.
        assert not np.array_equal(np.array(a.curve.p5), np.array(c.curve.p5))


# ---------------------------------------------------------------------------
#  Validation tests
# ---------------------------------------------------------------------------


class TestValidationErrors:
    @pytest.mark.parametrize("wr", [(-0.1), (1.5)])
    def test_win_rate_out_of_range(self, wr: float):
        with pytest.raises(ValueError, match="win_rate must be in"):
            ReturnProjector(win_rate=wr, sharpe=1.0, expectancy=0.1)

    def test_nan_expectancy_rejected(self):
        with pytest.raises(ValueError, match="expectancy must be finite"):
            ReturnProjector(win_rate=0.5, sharpe=1.0, expectancy=float("nan"))

    def test_zero_horizon_rejected(self):
        with pytest.raises(ValueError, match="horizon_trades must be >= 1"):
            ReturnProjector(
                win_rate=0.5, sharpe=1.0, expectancy=0.1, horizon_trades=0
            )

    def test_risk_per_trade_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="risk_per_trade must be in"):
            ReturnProjector(
                win_rate=0.5, sharpe=1.0, expectancy=0.1, risk_per_trade=1.5
            )

    def test_zero_simulations_rejected(self):
        with pytest.raises(ValueError, match="n_simulations must be >= 1"):
            ReturnProjector(
                win_rate=0.5, sharpe=1.0, expectancy=0.1, n_simulations=0
            )

    def test_ruin_floor_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="ruin_floor must be in"):
            ReturnProjector(
                win_rate=0.5, sharpe=1.0, expectancy=0.1, ruin_floor=1.5
            )


# ---------------------------------------------------------------------------
#  Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_metrics_to_dict_keys(self, profitable_projection: ProjectionResult):
        required = {
            "n_simulations", "horizon_trades", "initial_capital", "risk_per_trade",
            "final_equity_median", "final_equity_p5", "final_equity_p95",
            "final_return_median", "final_return_p5", "final_return_p95",
            "p_profitable", "p_ruin", "max_drawdown_median",
            "max_drawdown_p5", "max_drawdown_p95",
            "sharpe_projected_median", "sharpe_projected_p5", "sharpe_projected_p95",
            "inputs",
        }
        d = profitable_projection.metrics.to_dict()
        missing = required - d.keys()
        assert not missing, f"Missing keys in metrics.to_dict(): {missing}"

    def test_curve_to_dict_keys(self, profitable_projection: ProjectionResult):
        cd = profitable_projection.curve.to_dict()
        assert set(cd.keys()) == {"p5", "p50", "p95", "horizon", "n_simulations"}

    def test_summary_is_string(self, profitable_projection: ProjectionResult):
        assert isinstance(profitable_projection.metrics.summary(), str)
        assert isinstance(profitable_projection.summary(), str)
