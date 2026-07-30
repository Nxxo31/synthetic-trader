"""Walk-forward validation + Monte Carlo simulation for strategy robustness.

Walk-forward: divides data into N windows, runs backtest on each, checks
if gates pass consistently across ALL windows (not just the full dataset).

Monte Carlo: takes the backtest trade results, shuffles the order 10,000
times, builds a distribution of possible equity curves, and calculates:
  - P(profitable) — probability of ending profitable
  - P(max_dd > threshold) — probability of exceeding drawdown limit
  - Confidence intervals for final P&L, max drawdown, Sharpe

This detects overfitting: if the edge depends on a specific trade sequence,
Monte Carlo will show the strategy is fragile.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from src.strategies.range_break import RangeBreakStrategy, RangeBreakConfig
from src.analysis.signal_scorer import SignalScorer
from src.backtest.engine import BacktestEngine
from src.risk.manager import RiskConfig

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    """Resultado de walk-forward validation."""
    window_results: list[dict] = field(default_factory=list)
    all_windows_pass: bool = False
    avg_sharpe: float = 0.0
    avg_win_rate: float = 0.0
    avg_trades: float = 0.0
    min_trades_any_window: int = 0
    passing_windows: int = 0

    def summary(self) -> str:
        lines = [
            "=== Walk-Forward Validation ===",
            f"  Windows:        {len(self.window_results)}",
            f"  Passing:        {self.passing_windows}/{len(self.window_results)}",
            f"  All pass:       {'YES' if self.all_windows_pass else 'NO'}",
            f"  Avg trades:     {self.avg_trades:.1f}",
            f"  Avg Sharpe:     {self.avg_sharpe:.2f}",
            f"  Avg Win Rate:   {self.avg_win_rate:.1%}",
            f"  Min trades:     {self.min_trades_any_window}",
        ]
        for i, w in enumerate(self.window_results):
            lines.append(
                f"  Window {i+1}: {w['trades']} trades, WR={w['win_rate']:.1%}, "
                f"Sharpe={w['sharpe']:.2f}, Gate={'PASS' if w['gate_passed'] else 'FAIL'}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "window_results": self.window_results,
            "all_windows_pass": self.all_windows_pass,
            "avg_sharpe": self.avg_sharpe,
            "avg_win_rate": self.avg_win_rate,
            "avg_trades": self.avg_trades,
            "min_trades_any_window": self.min_trades_any_window,
            "passing_windows": self.passing_windows,
        }


@dataclass
class MonteCarloResult:
    """Resultado de Monte Carlo simulation."""
    n_simulations: int = 0
    initial_capital: float = 10000.0
    final_equities: list[float] = field(default_factory=list)
    max_drawdowns: list[float] = field(default_factory=list)
    sharpe_ratios: list[float] = field(default_factory=list)
    p_profitable: float = 0.0
    p_dd_exceeds_12pct: float = 0.0
    p_dd_exceeds_5pct: float = 0.0
    median_final_equity: float = 0.0
    pct_5_final_equity: float = 0.0
    pct_95_final_equity: float = 0.0
    median_max_dd: float = 0.0
    pct_5_max_dd: float = 0.0
    pct_95_max_dd: float = 0.0
    median_sharpe: float = 0.0

    def summary(self) -> str:
        lines = [
            "=== Monte Carlo Simulation ===",
            f"  Simulations:        {self.n_simulations}",
            f"  P(profitable):      {self.p_profitable:.1%}",
            f"  P(DD > 5%):         {self.p_dd_exceeds_5pct:.1%}",
            f"  P(DD > 12%):        {self.p_dd_exceeds_12pct:.1%}",
            f"  Final Equity:",
            f"    Median:           ${self.median_final_equity:.2f}",
            f"    5th pct:          ${self.pct_5_final_equity:.2f}",
            f"    95th pct:         ${self.pct_95_final_equity:.2f}",
            f"  Max Drawdown:",
            f"    Median:           {self.median_max_dd:.2%}",
            f"    5th pct:          {self.pct_5_max_dd:.2%}",
            f"    95th pct:         {self.pct_95_max_dd:.2%}",
            f"  Sharpe (median):    {self.median_sharpe:.2f}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "n_simulations": self.n_simulations,
            "p_profitable": self.p_profitable,
            "p_dd_exceeds_5pct": self.p_dd_exceeds_5pct,
            "p_dd_exceeds_12pct": self.p_dd_exceeds_12pct,
            "median_final_equity": self.median_final_equity,
            "pct_5_final_equity": self.pct_5_final_equity,
            "pct_95_final_equity": self.pct_95_final_equity,
            "median_max_dd": self.median_max_dd,
            "pct_5_max_dd": self.pct_5_max_dd,
            "pct_95_max_dd": self.pct_95_max_dd,
            "median_sharpe": self.median_sharpe,
        }


# ---------------------------------------------------------------------------
# Walk-Forward Validation
# ---------------------------------------------------------------------------

def walk_forward(
    data,
    n_windows: int = 5,
    threshold: float = 0.50,
    initial_capital: float = 10000.0,
) -> WalkForwardResult:
    """Divide data into N non-overlapping windows, runs backtest on each.

    Args:
        data: Full candle DataFrame
        n_windows: Number of equal-size windows
        threshold: Signal scorer threshold
        initial_capital: Starting capital per window

    Returns:
        WalkForwardResult with per-window stats
    """
    result = WalkForwardResult()
    window_size = len(data) // n_windows

    for i in range(n_windows):
        start = i * window_size
        end = (i + 1) * window_size if i < n_windows - 1 else len(data)
        window_data = data.iloc[start:end]

        random.seed(42 + i)  # different seed per window
        scorer = SignalScorer(entry_threshold=threshold)
        strategy = RangeBreakStrategy(
            symbol="RB100",
            config=RangeBreakConfig(),
            signal_scorer=scorer,
            score_threshold=threshold,
        )
        engine = BacktestEngine(
            strategy, RiskConfig(),
            latency_ms_min=100, latency_ms_max=500,
            use_dynamic_kelly=True, use_circuit_breaker=True,
        )
        bt_result = engine.run(window_data, initial_capital=initial_capital)

        result.window_results.append({
            "window": i + 1,
            "start_candle": start,
            "end_candle": end,
            "candles": len(window_data),
            "trades": bt_result.total_trades,
            "win_rate": bt_result.win_rate,
            "sharpe": bt_result.sharpe_ratio,
            "max_drawdown": bt_result.max_drawdown,
            "expectancy": bt_result.expectancy,
            "total_pnl": bt_result.total_pnl,
            "gate_passed": bt_result.gate_passed,
            "gate_failures": bt_result.gate_failures,
        })

    # Aggregate
    result.passing_windows = sum(1 for w in result.window_results if w["gate_passed"])
    result.all_windows_pass = result.passing_windows == n_windows
    result.avg_sharpe = float(np.mean([w["sharpe"] for w in result.window_results])) if result.window_results else 0
    result.avg_win_rate = float(np.mean([w["win_rate"] for w in result.window_results])) if result.window_results else 0
    result.avg_trades = float(np.mean([w["trades"] for w in result.window_results])) if result.window_results else 0
    result.min_trades_any_window = min((w["trades"] for w in result.window_results), default=0)

    return result


# ---------------------------------------------------------------------------
# Monte Carlo Simulation
# ---------------------------------------------------------------------------

def monte_carlo(
    trade_pnls: list[float],
    n_simulations: int = 10000,
    initial_capital: float = 10000.0,
) -> MonteCarloResult:
    """Simula 10,000 permutaciones aleatorias del orden de trades.

    Para cada simulación:
    1. Shufflear el orden de P&Ls
    2. Construir equity curve
    3. Calcular max drawdown y Sharpe

    Returns:
        MonteCarloResult con distribuciones y probabilidades
    """
    result = MonteCarloResult()
    result.n_simulations = n_simulations
    result.initial_capital = initial_capital

    if not trade_pnls:
        return result

    pnls = np.array(trade_pnls)
    n_trades = len(pnls)

    final_equities = []
    max_drawdowns = []
    sharpe_ratios = []

    for _ in range(n_simulations):
        shuffled = np.random.permutation(pnls)
        equity = np.cumsum(np.concatenate([[initial_capital], shuffled]))

        # Max drawdown
        peak = np.maximum.accumulate(equity)
        dd = (peak - equity) / peak
        max_dd = float(np.max(dd))

        # Sharpe (simplified)
        if len(equity) > 1 and np.std(np.diff(equity)) > 0:
            sharpe = float(np.mean(np.diff(equity)) / np.std(np.diff(equity)) * np.sqrt(252))
        else:
            sharpe = 0.0

        final_equities.append(float(equity[-1]))
        max_drawdowns.append(max_dd)
        sharpe_ratios.append(sharpe)

    # Calculate distributions
    final_arr = np.array(final_equities)
    dd_arr = np.array(max_drawdowns)
    sharpe_arr = np.array(sharpe_ratios)

    result.p_profitable = float(np.mean(final_arr > initial_capital))
    result.p_dd_exceeds_5pct = float(np.mean(dd_arr > 0.05))
    result.p_dd_exceeds_12pct = float(np.mean(dd_arr > 0.12))

    result.median_final_equity = float(np.median(final_arr))
    result.pct_5_final_equity = float(np.percentile(final_arr, 5))
    result.pct_95_final_equity = float(np.percentile(final_arr, 95))

    result.median_max_dd = float(np.median(dd_arr))
    result.pct_5_max_dd = float(np.percentile(dd_arr, 5))
    result.pct_95_max_dd = float(np.percentile(dd_arr, 95))

    result.median_sharpe = float(np.median(sharpe_arr))

    return result


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_validation(threshold: float = 0.50, n_windows: int = 5, n_mc: int = 10000) -> dict:
    """Run full validation: walk-forward + Monte Carlo."""
    print(f"Loading data...")
    df = pq.read_table("data/candles/RB100_candles_60s.parquet").to_pandas()
    print(f"Loaded {len(df)} candles")

    # 1. Walk-forward
    print(f"\n--- Walk-Forward ({n_windows} windows, threshold={threshold:.2f}) ---")
    wf = walk_forward(df, n_windows=n_windows, threshold=threshold)
    print(wf.summary())

    # 2. Run full backtest to get trade P&Ls for Monte Carlo
    print(f"\n--- Full Backtest (for Monte Carlo) ---")
    random.seed(42)
    scorer = SignalScorer(entry_threshold=threshold)
    strategy = RangeBreakStrategy(
        symbol="RB100",
        config=RangeBreakConfig(),
        signal_scorer=scorer,
        score_threshold=threshold,
    )
    engine = BacktestEngine(
        strategy, RiskConfig(),
        latency_ms_min=100, latency_ms_max=500,
        use_dynamic_kelly=True, use_circuit_breaker=True,
    )
    bt_result = engine.run(df, initial_capital=10000.0)
    print(f"Trades: {bt_result.total_trades}, WR: {bt_result.win_rate:.1%}, Sharpe: {bt_result.sharpe_ratio:.2f}")
    trade_pnls = [t.pnl for t in bt_result.trades]

    # 3. Monte Carlo
    print(f"\n--- Monte Carlo ({n_mc} simulations) ---")
    mc = monte_carlo(trade_pnls, n_simulations=n_mc, initial_capital=10000.0)
    print(mc.summary())

    # 4. Verdict
    print(f"\n=== VEREDICTO ===")
    print(f"  Walk-forward all pass: {'YES' if wf.all_windows_pass else 'NO'} ({wf.passing_windows}/{len(wf.window_results)})")
    print(f"  P(profitable):         {mc.p_profitable:.1%}")
    print(f"  P(DD > 12%):           {mc.p_dd_exceeds_12pct:.1%}")
    robust = (
        wf.all_windows_pass
        and mc.p_profitable > 0.95
        and mc.p_dd_exceeds_12pct < 0.05
    )
    print(f"  Strategy robust:       {'YES' if robust else 'NO — overfitting suspected'}")

    # Save report
    report = {
        "threshold": threshold,
        "walk_forward": wf.to_dict(),
        "monte_carlo": mc.to_dict(),
        "backtest_summary": {
            "trades": bt_result.total_trades,
            "win_rate": bt_result.win_rate,
            "sharpe": bt_result.sharpe_ratio,
            "max_drawdown": bt_result.max_drawdown,
            "expectancy": bt_result.expectancy,
            "total_pnl": bt_result.total_pnl,
            "gate_passed": bt_result.gate_passed,
        },
        "robust": robust,
    }

    report_path = Path("reports/backtest/validation_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved to {report_path}")

    return report


if __name__ == "__main__":
    # Find best threshold from sweep, then validate
    import logging
    logging.basicConfig(level=logging.WARNING)  # quiet

    np.random.seed(42)
    # Best threshold from sweep: 0.50 (80 trades, 92.5% WR, gates pass)
    run_validation(threshold=0.50, n_windows=5, n_mc=10000)
