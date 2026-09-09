"""Return projector — Monte Carlo forward projection from historical edge metrics.

Unlike the trade-bootstrap Monte Carlo in ``scripts/walk_forward_validation.py``
(which reshuffles observed trade P&Ls to probe *outcome order* risk), this
module performs a **forward Monte Carlo**: it *synthesises* 10,000 new equity
trajectories from the historical edge statistics alone — ``win_rate``,
``sharpe_ratio`` and ``expectancy`` — then reports the P5 / P50 / P95 equity
envelope plus projected tail metrics.

Why forward instead of bootstrap?
    - Bootstrap only describes rearrangements of trades we already saw. It
      cannot answer "what range of outcomes should I expect over the next *N*
      trades given that my edge this far is (wr, sharpe, exp)?"
    - Forward projection samples *new* trade outcomes from a generative model
      calibrated to those three statistics, so the distribution it produces is a
      forward-looking projection, not a permutation.

Generative model (per simulated trade ``i``):
    win_i ~ Bernoulli(win_rate)
    If win:   r_i = +avg_win_R           (in R-multiples)
    If loss:  r_i = -avg_loss_R          (in R-multiples)
    where:
        expectancy_E = win_rate * avg_win_R - (1 - win_rate) * avg_loss_R
        avg_win_R and avg_loss_R are recovered from expectancy_E and a
        payoff_ratio that is derived (when possible) from sharpe_ratio, or
        else falls back to a configurable default payoff ratio (R:R).

    dollar_pnl_i = r_i * risk_per_trade_usd

Sharpe is used to calibrate the payoff ratio only when ``infer_payoff_from_sharpe``
is true and a finite sharpe > 0 is supplied; otherwise ``default_payoff_ratio``
(typical TP/SL R:R, e.g. 1.5:1) is used. Keeping this inference opt-in avoids
the trap of treating a noisy single-period Sharpe estimate as ground truth.

Vectorised entirely with NumPy — the 10k×N_trades matrix is built in one
``np.where`` over a pre-drawn Bernoulli field, then a cumsum gives the equity
matrix. No Python-level per-simulation loop on the hot path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# ─── Defaults (NON-NEGOTIABLE unless caller overrides) ──────────────────────────
DEFAULT_N_SIMULATIONS: int = 10_000
DEFAULT_HORIZON_TRADES: int = 100
DEFAULT_INITIAL_CAPITAL: float = 10_000.0
DEFAULT_RISK_PER_TRADE: float = 0.01      # 1% of capital per trade (fraction)
DEFAULT_PAYOFF_RATIO: float = 1.5         # avg_win_R / avg_loss_R when uninferrable
DEFAULT_CONFIDENCE_LEVELS: tuple[float, ...] = (5.0, 50.0, 95.0)
# Sharpe → volatility mapping (annualised). sqrt(252) converts per-trade vol
# to per-trade Sharpe; inverse gives per-trade std-dev of R returns.
_TRADING_PERIODS_PER_YEAR: int = 252


@dataclass
class ProjectedEquityCurve:
    """Quantile envelope of the forward equity matrix, length ``horizon + 1``.

    Each P**k** vector has ``horizon + 1`` entries (starting capital prepended
    so charts begin at the deposit level) and is the k-th percentile of the
    equity distribution across the ``n_simulations`` paths at each trade step.
    """
    p5: list[float]
    p50: list[float]
    p95: list[float]
    horizon: int
    n_simulations: int

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon,
            "n_simulations": self.n_simulations,
            "p5": self.p5,
            "p50": self.p50,
            "p95": self.p95,
        }


@dataclass
class ProjectedMetrics:
    """Aggregated tail metrics across all forward simulations."""
    n_simulations: int = 0
    horizon_trades: int = 0
    initial_capital: float = 0.0
    risk_per_trade: float = 0.0
    final_equity_median: float = 0.0
    final_equity_p5: float = 0.0
    final_equity_p95: float = 0.0
    final_return_median: float = 0.0
    final_return_p5: float = 0.0
    final_return_p95: float = 0.0
    p_profitable: float = 0.0           # P(final_equity > initial_capital)
    p_ruin: float = 0.0                # P(equity ever dips below ruin_floor)
    max_drawdown_median: float = 0.0
    max_drawdown_p5: float = 0.0       # benign tail
    max_drawdown_p95: float = 0.0      # adverse tail
    sharpe_projected_median: float = 0.0
    sharpe_projected_p5: float = 0.0
    sharpe_projected_p95: float = 0.0
    inputs: dict = field(default_factory=dict)   # echo of the calibrated inputs

    def summary(self) -> str:
        lines = [
            "=== Return Projector — Forward Monte Carlo ===",
            f"  Simulations:        {self.n_simulations}",
            f"  Horizon (trades):   {self.horizon_trades}",
            f"  Initial capital:    ${self.initial_capital:,.2f}",
            f"  Risk per trade:     {self.risk_per_trade:.2%}",
            "",
            "  Final Equity:",
            f"    Median:           ${self.final_equity_median:,.2f}  "
            f"({self.final_return_median:+.2%})",
            f"    P5:               ${self.final_equity_p5:,.2f}  "
            f"({self.final_return_p5:+.2%})",
            f"    P95:              ${self.final_equity_p95:,.2f}  "
            f"({self.final_return_p95:+.2%})",
            "",
            "  Tail probabilities:",
            f"    P(profitable):    {self.p_profitable:.1%}",
            f"    P(ruin):          {self.p_ruin:.1%}",
            "",
            "  Max Drawdown:",
            f"    Median:           {self.max_drawdown_median:.2%}",
            f"    P5:               {self.max_drawdown_p5:.2%}",
            f"    P95:              {self.max_drawdown_p95:.2%}",
            "",
            "  Projected Sharpe:",
            f"    Median:           {self.sharpe_projected_median:.3f}",
            f"    P5:               {self.sharpe_projected_p5:.3f}",
            f"    P95:              {self.sharpe_projected_p95:.3f}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "n_simulations": self.n_simulations,
            "horizon_trades": self.horizon_trades,
            "initial_capital": self.initial_capital,
            "risk_per_trade": self.risk_per_trade,
            "final_equity_median": self.final_equity_median,
            "final_equity_p5": self.final_equity_p5,
            "final_equity_p95": self.final_equity_p95,
            "final_return_median": self.final_return_median,
            "final_return_p5": self.final_return_p5,
            "final_return_p95": self.final_return_p95,
            "p_profitable": self.p_profitable,
            "p_ruin": self.p_ruin,
            "max_drawdown_median": self.max_drawdown_median,
            "max_drawdown_p5": self.max_drawdown_p5,
            "max_drawdown_p95": self.max_drawdown_p95,
            "sharpe_projected_median": self.sharpe_projected_median,
            "sharpe_projected_p5": self.sharpe_projected_p5,
            "sharpe_projected_p95": self.sharpe_projected_p95,
            "inputs": self.inputs,
        }


@dataclass
class ProjectionResult:
    """Full projection output — envelope curve + tail metrics."""
    curve: ProjectedEquityCurve
    metrics: ProjectedMetrics
    seed: int

    def to_dict(self) -> dict:
        return {
            "curve": self.curve.to_dict(),
            "metrics": self.metrics.to_dict(),
            "seed": self.seed,
        }

    def summary(self) -> str:
        return f"{self.metrics.summary()}\n\n  Seed: {self.seed}"


# ─── Helpers ────────────────────────────────────────────────────────────────────


def _calibrate_edge(
    win_rate: float,
    expectancy_r: float,
    sharpe: float | None,
    default_payoff_ratio: float,
    infer_payoff_from_sharpe: bool,
) -> tuple[float, float, float, dict]:
    """Recover (avg_win_R, avg_loss_R, payoff_ratio) from historical edge metrics.

    The expectancy identity:
        E = win_rate * avg_win_R - (1 - win_rate) * avg_loss_R
    Two unknowns (avg_win_R, avg_loss_R), one equation — we need a second
    constraint, the payoff ratio b = avg_win_R / avg_loss_R.

    If ``infer_payoff_from_sharpe`` is True and a finite sharpe > 0 is given,
    we invert the standard Sharpe relation to solve for the per-trade return
    std-dev and back out b analytically (see implementation). Otherwise b
    defaults to ``default_payoff_ratio``.
    """
    if not (0.0 <= win_rate <= 1.0):
        raise ValueError(f"win_rate must be in [0,1], got {win_rate}")
    if not np.isfinite(expectancy_r):
        raise ValueError(f"expectancy must be finite, got {expectancy_r}")

    avg_loss_r_unknown = True
    avg_win_r = float("nan")
    avg_loss_r = float("nan")
    payoff_ratio = float(default_payoff_ratio)
    inference_note = "default_payoff_ratio"

    # Try to infer payoff ratio from Sharpe, if explicitly requested and usable.
    if infer_payoff_from_sharpe and sharpe is not None and np.isfinite(sharpe) and sharpe > 0:
        # Per-trade mean return (in R): μ_R = expectancy_r
        # Annualised Sharpe ≈ (μ_R / σ_R) * sqrt(trades_per_year)
        # ⇒ σ_R = μ_R / sharpe * sqrt(trades_per_year)
        sigma_r = expectancy_r / sharpe * np.sqrt(_TRADING_PERIODS_PER_YEAR)
        if np.isfinite(sigma_r) and sigma_r > 0:
            # For a binary ±R outcome with prob p of +avg_win_R and (1-p) of
            # -avg_loss_R, with the expectancy identity, σ_R² = E[X²] - E[X]²:
            #   p*avg_win_R² + (1-p)*avg_loss_R² - (expectancy_r)²
            # Combine with E = p*avg_win_R - (1-p)*avg_loss_r to solve for b.
            # Using b = avg_win_R / avg_loss_r, after algebra:
            #   b = (σ²+μ² + μ*(1-2p)) / ((p-1)*(σ²+μ²) + μ*(2p-1))
            # Simpler numeric form derivable from standard binary-outcome Sharpe:
            mu = expectancy_r
            p = win_rate
            q = 1.0 - p
            var = sigma_r * sigma_r
            # Σ = p*b² + q (where avg_loss_R=1 unit) — normalise by avg_loss_R.
            # Let avg_loss_R = L, avg_win_R = b*L. Then:
            #   μ = p*b*L - q*L = (p*b - q)*L  ⇒  L = μ / (p*b - q)
            #   E[X²] = p*b²*L² + q*L² = (p*b² + q)*L²
            #   σ² = (p*b² + q)*L² - μ²
            # Solve numerically for b in 1D; bounded to (0, 50] for stability.
            def _neg_var(b_val: float) -> float:
                denom = p * b_val - q
                if abs(denom) < 1e-12:
                    return 1e15
                ell = mu / denom  # avg_loss_R recovered from expectation identity
                ex2 = (p * b_val * b_val + q) * ell * ell
                return abs((ex2 - mu * mu) - var)

            from scipy.optimize import minimize_scalar  # local import; optional dep

            res = minimize_scalar(_neg_var, bounds=(1e-3, 50.0), method="bounded")
            if res.success and np.isfinite(res.x) and res.x > 0:
                payoff_ratio = float(res.x)
                inference_note = "inferred_from_sharpe"
                avg_loss_r_unknown = False

    # Recover avg_win_R and avg_loss_R from expectancy + payoff ratio
    # E = p*b*L - q*L = L*(p*b - q)  ⇒  L = E / (p*b - q)
    if not avg_loss_r_unknown or np.isnan(avg_win_r) or np.isnan(avg_loss_r):
        p = win_rate
        q = 1.0 - p
        denom = p * payoff_ratio - q
        if abs(denom) < 1e-12:
            # Degenerate: p*b == q ⇒ expectancy must be ~0, use symmetric fallback
            avg_loss_r = max(1.0, 1.0 / max(payoff_ratio, 1e-6))
            avg_win_r = payoff_ratio * avg_loss_r
            inference_note = "fallback_symmetric"
        else:
            avg_loss_r = abs(expectancy_r / denom)
            if avg_loss_r <= 0:
                avg_loss_r = 1.0
                avg_win_r = payoff_ratio * avg_loss_r
                inference_note = "fallback_default_L"
            else:
                avg_win_r = payoff_ratio * avg_loss_r

    inputs_echo = {
        "win_rate": float(win_rate),
        "expectancy_r": float(expectancy_r),
        "sharpe": None if sharpe is None else float(sharpe),
        "payoff_ratio": float(payoff_ratio),
        "avg_win_r": float(avg_win_r),
        "avg_loss_r": float(avg_loss_r),
        "inference_source": inference_note,
    }
    return avg_win_r, avg_loss_r, payoff_ratio, inputs_echo


# ─── ReturnProjector ────────────────────────────────────────────────────────────


class ReturnProjector:
    """Forward Monte Carlo projector from historical edge statistics.

    Given a strategy's historical ``win_rate``, ``sharpe_ratio`` and
    ``expectancy`` (in R-multiples), projects ``n_simulations`` (default 10,000)
    forward equity paths over ``horizon_trades`` (default 100) trades and
    returns the P5 / P50 / P95 equity envelope plus tail metrics.

    Parameters
    ----------
    win_rate : float
        Historical win rate ∈ [0, 1].
    sharpe : float | None
        Historical Sharpe ratio (annualised). Used only to infer the payoff
        ratio when ``infer_payoff_from_sharpe=True``; otherwise informational.
    expectancy : float
        Historical expectancy in **R-multiples** (e.g. 0.15R) — i.e. expected
        return per trade expressed in units of risk. This matches the field
        produced by ``BacktestResult.expectancy`` in this project.
    horizon_trades : int
        Number of forward trades to simulate per path. Default 100.
    risk_per_trade : float
        Fraction of current capital risked per trade (Kelly fraction applied).
        Default 1% (matches typical quarter-Kelly behaviour after hard-cap).
    n_simulations : int
        Number of forward paths to simulate. Default 10,000 (the project's
        Monte Carlo "10k sims" standard).
    initial_capital : float
        Starting account size in USD. Default $10,000 (project standard).
    infer_payoff_from_sharpe : bool
        If True and sharpe > 0, derive TP/SL payoff ratio from Sharpe. If
        False (default), use ``default_payoff_ratio`` — safer, since Sharpe
        estimated from a small backtest is noisy.
    default_payoff_ratio : float
        TP/SL ratio (avg_win_R / avg_loss_R) used as fallback. Default 1.5.
    seed : int | None
        NumPy RNG seed for reproducibility. If None, non-deterministic.
    ruin_floor : float
        Fraction of initial capital below which a path is counted as "ruin"
        for ``p_ruin``. Default 0.75 (≤ −25% drawdown).
    """

    def __init__(
        self,
        win_rate: float,
        sharpe: float | None,
        expectancy: float,
        *,
        horizon_trades: int = DEFAULT_HORIZON_TRADES,
        risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
        n_simulations: int = DEFAULT_N_SIMULATIONS,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        infer_payoff_from_sharpe: bool = False,
        default_payoff_ratio: float = DEFAULT_PAYOFF_RATIO,
        seed: int | None = None,
        ruin_floor: float = 0.75,
    ) -> None:
        if horizon_trades < 1:
            raise ValueError(f"horizon_trades must be >= 1, got {horizon_trades}")
        if not (0.0 < risk_per_trade < 1.0):
            raise ValueError(f"risk_per_trade must be in (0,1), got {risk_per_trade}")
        if n_simulations < 1:
            raise ValueError(f"n_simulations must be >= 1, got {n_simulations}")
        if not (0.0 < ruin_floor < 1.0):
            raise ValueError(f"ruin_floor must be in (0,1), got {ruin_floor}")

        self.win_rate = float(win_rate)
        self.sharpe = None if sharpe is None else float(sharpe)
        self.expectancy_r = float(expectancy)
        self.horizon_trades = int(horizon_trades)
        self.risk_per_trade = float(risk_per_trade)
        self.n_simulations = int(n_simulations)
        self.initial_capital = float(initial_capital)
        self.infer_payoff_from_sharpe = bool(infer_payoff_from_sharpe)
        self.default_payoff_ratio = float(default_payoff_ratio)
        self.seed = seed
        self.ruin_floor = float(ruin_floor)

        # Calibrate the generative model up front (cheap, validated eagerly).
        self.avg_win_r, self.avg_loss_r, self.payoff_ratio, self._inputs_echo = (
            _calibrate_edge(
                win_rate=self.win_rate,
                expectancy_r=self.expectancy_r,
                sharpe=self.sharpe,
                default_payoff_ratio=self.default_payoff_ratio,
                infer_payoff_from_sharpe=self.infer_payoff_from_sharpe,
            )
        )

    def project(self) -> ProjectionResult:
        """Run the forward Monte Carlo and return the projection result.

        Vectorised over all simulations in a single NumPy pass — no per-path
        Python loop on the hot path.
        """
        rng = np.random.default_rng(self.seed)

        n_sims = self.n_simulations
        n_tr = self.horizon_trades
        p = self.win_rate
        avg_win = self.avg_win_r
        avg_loss = self.avg_loss_r

        # (a) Draw the Bernoulli outcome field: 1 = win, 0 = loss.
        # Using float comparison for vectorised Bernoulli, robust to rare edge.
        wins_field = (rng.random((n_sims, n_tr)) < p).astype(np.float64)

        # (b) R-multiple per trade: +avg_win on wins, -avg_loss on losses.
        r_matrix = wins_field * avg_win - (1.0 - wins_field) * avg_loss

        # (c) Compounding: dollar P&L per trade = r_i * risk_fraction * capital_i.
        # Implement capital evolution row-wise via cumulative product; we
        # track equity row-by-row: equity_{t+1} = equity_t * (1 + r_t * risk).
        # This is genuine compounding (geometric), which is what real trading
        # does — different from the additive cumsum used by the bootstrap MC.
        multipliers = 1.0 + r_matrix * self.risk_per_trade
        # Equity matrix, shape (n_sims, n_tr+1), col 0 = initial capital.
        # equity[t] = initial_capital * prod(multipliers[1..t])
        # cumprod gives running product of multipliers in columns 1..n_tr;
        # we then scale ONLY those columns by initial_capital (column 0 is
        # already the deposit, never multiply it again).
        equity = np.empty((n_sims, n_tr + 1), dtype=np.float64)
        np.cumprod(multipliers, axis=1, out=equity[:, 1:])
        equity[:, 1:] *= self.initial_capital
        equity[:, 0] = self.initial_capital

        # (d) Quantile envelope across simulations at each step.
        p5_path = np.percentile(equity, 5.0, axis=0)
        p50_path = np.percentile(equity, 50.0, axis=0)
        p95_path = np.percentile(equity, 95.0, axis=0)

        curve = ProjectedEquityCurve(
            p5=p5_path.tolist(),
            p50=p50_path.tolist(),
            p95=p95_path.tolist(),
            horizon=n_tr,
            n_simulations=n_sims,
        )

        # (e) Tail metrics.
        final_equity = equity[:, -1]
        # Per-path return
        final_return = final_equity / self.initial_capital - 1.0

        # Max drawdown per path, vectorised:
        # running_max along axis=1; dd = (peak - equity)/peak
        running_max = np.maximum.accumulate(equity, axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            dd_matrix = np.where(running_max > 0, (running_max - equity) / running_max, 0.0)
        max_dd_per_path = dd_matrix.max(axis=1)

        # Projected Sharpe per path (annualised, risk-free=0):
        # per-trade returns are the multipliers' log returns approximated
        # by simple returns relative to prior equity.
        # ret_t = (equity_t - equity_{t-1}) / equity_{t-1}
        ret_matrix = np.diff(equity, axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            ret_matrix = np.where(equity[:, :-1] > 0, ret_matrix / equity[:, :-1], 0.0)
        mean_ret = ret_matrix.mean(axis=1)
        std_ret = ret_matrix.std(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            sharpe_path = np.where(
                std_ret > 0,
                mean_ret / std_ret * np.sqrt(_TRADING_PERIODS_PER_YEAR),
                0.0,
            )

        # Ruin: path ever dips below ruin_floor * initial_capital
        ruin_threshold = self.ruin_floor * self.initial_capital
        p_ruin = float((equity.min(axis=1) < ruin_threshold).mean())

        metrics = ProjectedMetrics(
            n_simulations=n_sims,
            horizon_trades=n_tr,
            initial_capital=self.initial_capital,
            risk_per_trade=self.risk_per_trade,
            final_equity_median=float(np.median(final_equity)),
            final_equity_p5=float(np.percentile(final_equity, 5.0)),
            final_equity_p95=float(np.percentile(final_equity, 95.0)),
            final_return_median=float(np.median(final_return)),
            final_return_p5=float(np.percentile(final_return, 5.0)),
            final_return_p95=float(np.percentile(final_return, 95.0)),
            p_profitable=float((final_equity > self.initial_capital).mean()),
            p_ruin=p_ruin,
            max_drawdown_median=float(np.median(max_dd_per_path)),
            max_drawdown_p5=float(np.percentile(max_dd_per_path, 5.0)),
            max_drawdown_p95=float(np.percentile(max_dd_per_path, 95.0)),
            sharpe_projected_median=float(np.median(sharpe_path)),
            sharpe_projected_p5=float(np.percentile(sharpe_path, 5.0)),
            sharpe_projected_p95=float(np.percentile(sharpe_path, 95.0)),
            inputs=dict(self._inputs_echo),
        )
        metrics.inputs["n_simulations"] = n_sims
        metrics.inputs["horizon_trades"] = n_tr
        metrics.inputs["risk_per_trade"] = self.risk_per_trade
        metrics.inputs["initial_capital"] = self.initial_capital
        metrics.inputs["infer_payoff_from_sharpe"] = self.infer_payoff_from_sharpe

        seed_val = self.seed if self.seed is not None else -1
        return ProjectionResult(curve=curve, metrics=metrics, seed=seed_val)
