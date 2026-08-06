#!/usr/bin/env python3
"""Quick integration test for CapitalAllocator + RiskManager."""

import sys
from pathlib import Path

# Add project root to sys.path so `import src...` works regardless of cwd
_sys_path = str(Path(__file__).resolve().parent.parent / "src")
if _sys_path not in sys.path:
    sys.path.insert(0, _sys_path)

from risk.capital_allocator import CapitalAllocator, CapitalAllocatorConfig
from risk.manager import RiskConfig, RiskManager

def main():
    print("=== Integration test: CapitalAllocator + RiskManager ===")

    # Configs
    alloc_config = CapitalAllocatorConfig(
        reserva_pct=0.80,
        superávit_diario_pct=0.20,
        initial_capital=10000.0,
        min_surplus=10.0,
        rebalance_daily=True,
    )
    risk_config = RiskConfig(
        max_risk_per_trade=0.03,
        max_daily_drawdown=0.05,
        max_trades_per_day=8,
        circuit_breaker_losses=5,
        kelly_fraction=0.25,
        initial_capital=10000.0,
    )

    # Objects
    risk_manager = RiskManager(risk_config)
    allocator = CapitalAllocator(alloc_config, risk_manager)

    # Day 1 initialization
    allocator.reset_daily(10000.0)
    print(f"Day 1 start: Total=${allocator.state.capital_total:,.2f}")
    print(f"  Reserva: ${allocator.reserva:,.2f} ({alloc_config.reserva_pct*100:.0f}%)")
    print(f"  Superávit: ${allocator.state.superávit_diario:,.2f} ({alloc_config.superávit_diario_pct*100:.0f}%)")
    print(f"  Disponible: ${allocator.superávit_disponible:,.2f}")

    # Simulate 3 trades: win, win, loss
    trades = [
        (0.60, 2.0, 1.0, 0.8, 1.0),  # win_prob, win_amt, loss_amt, conf, vol_mult
        (0.55, 1.5, 1.0, 0.7, 1.2),
        (0.50, 1.0, 1.0, 0.6, 1.5),  # edge ≈ 0 → stake 0
    ]

    for i, (wp, wa, la, conf, vol) in enumerate(trades, 1):
        stake = allocator.calculate_micro_stake(wp, wa, la, conf, vol)
        print(f"\nTrade {i}:")
        print(f"  Stake calculated: ${stake:,.2f}")
        if stake > 0:
            # Simular resultado: ganamos los dos primeros, perdemos el tercero
            pnl = stake * (wa if i < 3 else -la)  # win first two, lose third
            allocator.record_trade(pnl)
            print(f"  Trade result: P&L = ${pnl:,.2f}")
        else:
            print(f"  No trade (stake=0)")

        print(f"  Superávit después: ${allocator.state.superávit_diario:,.2f}")
        print(f"  Disponible: ${allocator.superávit_disponible:,.2f}")
        print(f"  Trades hoy: {allocator.state.trades_count}")

    # End of day report
    print("\n=== End of Day 1 Report ===")
    report = allocator.daily_report()
    for k, v in report.items():
        if k != "config":
            print(f"  {k}: {v}")

    # Day 2: rebalance should grow capital with P&L
    print("\n=== Day 2 Reset (with rebalance) ===")
    allocator.reset_daily()  # no explicit arg → uses previous total + P&L
    print(f"Day 2 start: Total=${allocator.state.capital_total:,.2f}")
    print(f"  Reserva: ${allocator.reserva:,.2f}")
    print(f"  Superávit: ${allocator.state.superávit_diario:,.2f}")
    print(f"  Disponible: ${allocator.superávit_disponible:,.2f}")

    print("\n✅ Integration test completed successfully.")

if __name__ == "__main__":
    main()
