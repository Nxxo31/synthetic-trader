"""Main entry point for the Synthetic Trader bot."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from src.connection.deriv_client import DerivClient, DerivConfig
from src.data.collector import DataCollector
from src.strategy.range_break import RangeBreakStrategy, RangeBreakConfig
from src.backtest.engine import BacktestEngine
from src.risk.manager import RiskConfig

console = Console()

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


async def test_connection():
    """Test connection to Deriv API and verify credentials."""
    config = DerivConfig.from_yaml()
    client = DerivClient(config)

    console.rule("[bold green]Synthetic Trader — Connection Test[/bold green]")

    try:
        await client.connect()
        console.print("[green]✓ Connected to Deriv WebSocket[/green]")

        if client.is_authorized:
            console.print("[green]✓ Authorized (demo account)[/green]")
            balance = await client.balance()
            console.print(f"  Balance: {balance.get('balance', {}).get('balance', '?')} "
                          f"{balance.get('balance', {}).get('currency', '?')}")
        else:
            console.print("[yellow]⚠ Not authorized (market data only mode)[/yellow]")
            console.print("  Set DERIV_API_TOKEN in .env for trading features")

        symbols = await client.active_symbols()
        active = symbols.get("active_symbols", [])
        console.print(f"[green]✓ {len(active)} active symbols available[/green]")

        # Show Range Break symbols if available
        rb_symbols = [s for s in active if "RANGE" in s.get("display_name", "").upper()]
        if rb_symbols:
            console.print(f"[cyan]  Range Break symbols found: {len(rb_symbols)}[/cyan]")
            for s in rb_symbols[:5]:
                console.print(f"    {s['symbol']}: {s['display_name']}")

        return client

    except Exception as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        raise


async def download_data(client: DerivClient, symbol: str = "R_100"):
    """Download historical candle data for backtesting."""
    collector = DataCollector(client)
    console.rule(f"[bold blue]Downloading data for {symbol}[/bold blue]")

    df = await collector.download_candles(symbol=symbol, count=5000, granularity=60)
    console.print(f"[green]✓ Downloaded {len(df)} candles[/green]")
    console.print(f"  Date range: {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")
    console.print(f"  Price range: {df['low'].min():.4f} → {df['high'].max():.4f}")

    return df


async def run_backtest(data):
    """Run backtest with Range Break strategy."""
    console.rule("[bold yellow]Backtest: Range Break Strategy[/bold yellow]")

    strategy = RangeBreakStrategy(symbol="R_100", config=RangeBreakConfig())
    risk_config = RiskConfig()
    engine = BacktestEngine(strategy, risk_config)

    result = engine.run(data, initial_capital=10000.0)

    console.print(result.summary())

    if result.gate_passed:
        console.print("[bold green]✓ GATE PASSED — Strategy ready for paper trading[/bold green]")
    else:
        console.print("[bold red]✗ GATE FAILED — Strategy needs adjustment[/bold red]")
        for failure in result.gate_failures:
            console.print(f"  [red]• {failure}[/red]")

    return result


async def main():
    """Main bot entry point."""
    setup_logging()

    console.rule("[bold magenta]Synthetic Trader — Deriv Bot v0.1.0[/bold magenta]")
    console.print("[dim]Phase 1: Strategy Design + Backtest[/dim]")
    console.print("[dim]Mode: Paper Trading (demo account only)[/dim]")
    console.print()

    try:
        # Step 1: Connect to Deriv
        client = await test_connection()

        # Step 2: Download historical data
        # Using R_100 as proxy for initial testing (Range Break symbol may differ)
        data = await download_data(client, symbol="R_100")

        # Step 3: Run backtest
        if not data.empty:
            result = await run_backtest(data)

            # Save report
            report_path = Path("reports/backtest")
            report_path.mkdir(parents=True, exist_ok=True)

            import json
            report = {
                "strategy": "RangeBreak",
                "symbol": "R_100",
                "trades": result.total_trades,
                "win_rate": result.win_rate,
                "sharpe": result.sharpe_ratio,
                "max_drawdown": result.max_drawdown,
                "gate_passed": result.gate_passed,
                "gate_failures": result.gate_failures,
            }
            with open(report_path / "backtest_report.json", "w") as f:
                json.dump(report, f, indent=2)
            console.print(f"\n[green]Report saved to {report_path / 'backtest_report.json'}[/green]")
        else:
            console.print("[red]No data downloaded — cannot run backtest[/red]")

        await client.disconnect()
        console.print("\n[green]Done. Bot disconnected.[/green]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logging.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
