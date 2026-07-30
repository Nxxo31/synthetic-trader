"""Parameter sweep for signal scorer entry threshold.

Tests thresholds 0.35-0.70 to find the optimal that maximizes trades
while still passing all backtest gates.
"""
import random
import pyarrow.parquet as pq
from src.strategies.range_break import RangeBreakStrategy, RangeBreakConfig
from src.analysis.signal_scorer import SignalScorer
from src.backtest.engine import BacktestEngine
from src.risk.manager import RiskConfig

# Load data
df = pq.read_table('data/candles/RB100_candles_60s.parquet').to_pandas()
print(f"Loaded {len(df)} candles\n")

# Parameter sweep
thresholds = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
results = []

for threshold in thresholds:
    random.seed(42)  # reproducible latency
    scorer = SignalScorer(entry_threshold=threshold)
    strategy = RangeBreakStrategy(
        symbol="RB100",
        config=RangeBreakConfig(),
        signal_scorer=scorer,
    )
    risk_config = RiskConfig()
    engine = BacktestEngine(
        strategy, risk_config,
        latency_ms_min=100, latency_ms_max=500,
        use_dynamic_kelly=True,
        use_circuit_breaker=True,
    )
    result = engine.run(df, initial_capital=10000.0)

    row = {
        "threshold": threshold,
        "trades": result.total_trades,
        "win_rate": result.win_rate,
        "sharpe": result.sharpe_ratio,
        "max_dd": result.max_drawdown,
        "expectancy": result.expectancy,
        "profit_factor": result.profit_factor,
        "total_pnl": result.total_pnl,
        "gate_passed": result.gate_passed,
    }
    results.append(row)

# Print summary table
print(f"{'Threshold':>10} {'Trades':>7} {'Win Rate':>8} {'Sharpe':>7} {'Max DD':>7} {'Expect':>7} {'PF':>5} {'P&L':>7} {'Gate':>5}")
print("-" * 75)
for r in results:
    print(f"{r['threshold']:>10.2f} {r['trades']:>7} {r['win_rate']:>8.1%} {r['sharpe']:>7.2f} {r['max_dd']:>7.2%} {r['expectancy']:>7.3f} {r['profit_factor']:>5.2f} {r['total_pnl']:>7.2f} {'PASS' if r['gate_passed'] else 'FAIL':>5}")

# Find optimal: passes gates AND maximizes trades
passing = [r for r in results if r["gate_passed"]]
if passing:
    best = max(passing, key=lambda r: r["trades"])
    print(f"\n=== OPTIMAL: threshold={best['threshold']:.2f} ===")
    print(f"  Trades: {best['trades']} | WR: {best['win_rate']:.1%} | Sharpe: {best['sharpe']:.2f} | P&L: ${best['total_pnl']:.2f}")
else:
    print("\nNo threshold passes all gates.")
