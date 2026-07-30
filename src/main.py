"""Main entry point for the Synthetic Trader bot.

Modos:
  backtest  — corre backtest con datos parquet locales, guarda reporte JSON
  connect   — prueba conexión a Deriv API (PAT + OTP flow)
  paper     — paper trading en cuenta demo (requiere conexión API)

Usage:
    python -m src.main backtest    # default
    python -m src.main connect
    python -m src.main paper
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
from pathlib import Path

import pyarrow.parquet as pq
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from src.connection.deriv_client import DerivClient, DerivConfig
from src.data.collector import DataCollector
from src.strategies.range_break import RangeBreakStrategy, RangeBreakConfig
from src.backtest.engine import BacktestEngine
from src.risk.manager import RiskConfig
from src.analysis.recommender import Recommender

console = Console()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


# ---------------------------------------------------------------------------
# Backtest mode (offline — uses local parquet data)
# ---------------------------------------------------------------------------

def run_backtest_offline(
    parquet_path: str = "data/candles/RB100_candles_60s.parquet",
    initial_capital: float = 10000.0,
) -> None:
    """Run backtest with local parquet data — no API connection needed."""
    console.rule("[bold yellow]Backtest: Range Break Strategy (offline)[/bold yellow]")

    path = Path(parquet_path)
    if not path.exists():
        console.print(f"[red]Data file not found: {path}[/red]")
        console.print("[dim]Run 'python -m src.main connect' first to download data.[/dim]")
        sys.exit(1)

    console.print(f"[dim]Loading data from {path}...[/dim]")
    df = pq.read_table(path).to_pandas()
    console.print(f"[green]✓ Loaded {len(df)} candles[/green]")
    console.print(f"  Date range: {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")
    console.print(f"  Price range: {df['low'].min():.2f} → {df['high'].max():.2f}")

    # Set random seed for reproducible latency simulation
    random.seed(42)

    strategy = RangeBreakStrategy(symbol="RB100", config=RangeBreakConfig())
    risk_config = RiskConfig()
    engine = BacktestEngine(
        strategy,
        risk_config,
        latency_ms_min=100,
        latency_ms_max=500,
        use_dynamic_kelly=True,
        use_circuit_breaker=True,
    )

    result = engine.run(df, initial_capital=initial_capital)

    # Print summary
    console.print(result.summary())

    # Gate evaluation
    if result.gate_passed:
        console.print("[bold green]✓ GATE PASSED — Strategy ready for paper trading[/bold green]")
    else:
        console.print("[bold red]✗ GATE FAILED — Strategy needs adjustment[/bold red]")
        for failure in result.gate_failures:
            console.print(f"  [red]• {failure}[/red]")

    # Print trades table
    if result.trades:
        table = Table(title="Trades", show_lines=False)
        table.add_column("#", style="dim")
        table.add_column("Dir", style="cyan")
        table.add_column("Entry", justify="right")
        table.add_column("Exit", justify="right")
        table.add_column("P&L", justify="right")
        table.add_column("R", justify="right")
        table.add_column("Reason", style="yellow")

        for idx, t in enumerate(result.trades[:20]):  # Show first 20
            pnl_color = "green" if t.pnl >= 0 else "red"
            table.add_row(
                str(idx + 1),
                t.direction,
                f"{t.entry_price:.2f}",
                f"{t.exit_price:.2f}",
                f"[{pnl_color}]${t.pnl:.2f}[/{pnl_color}]",
                f"{t.pnl_pct:.2f}",
                t.exit_reason,
            )
        if len(result.trades) > 20:
            table.add_row("...", "...", "...", "...", "...", "...", f"+{len(result.trades) - 20} more")
        console.print(table)

    # Save report JSON
    report_path = Path("reports/backtest")
    report_path.mkdir(parents=True, exist_ok=True)

    report = result.to_dict()
    report["strategy"] = "RangeBreak"
    report["symbol"] = "RB100"
    report["config"] = {
        "latency_ms_min": 100,
        "latency_ms_max": 500,
        "use_dynamic_kelly": True,
        "use_circuit_breaker": True,
        "circuit_breaker_threshold": 3,
        "daily_drawdown_limit": risk_config.max_daily_drawdown,
        "kelly_fraction": risk_config.kelly_fraction,
    }
    report["circuit_breaker_status"] = (
        engine.circuit_breaker.status() if engine.circuit_breaker else None
    )

    report_file = report_path / "backtest_report.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, default=str)
    console.print(f"\n[green]Report saved to {report_file}[/green]")


# ---------------------------------------------------------------------------
# Connection test mode
# ---------------------------------------------------------------------------

async def test_connection() -> DerivClient:
    """Test connection to Deriv API and verify credentials."""
    config = DerivConfig.from_yaml()
    client = DerivClient(config)

    console.rule("[bold green]Synthetic Trader — Connection Test[/bold green]")

    try:
        await client.connect()
        console.print("[green]✓ Connected to Deriv WebSocket (new API)[/green]")
        console.print(f"  Account: {client.config.account_id} ({'demo' if client.config.is_demo else 'real'})")
        console.print("[green]✓ Authorized via OTP[/green]")

        balance = await client.balance()
        console.print(
            f"  Balance: {balance.get('balance', {}).get('balance', '?')} "
            f"{balance.get('balance', {}).get('currency', '?')}"
        )

        symbols = await client.active_symbols()
        active = symbols.get("active_symbols", [])
        console.print(f"[green]✓ {len(active)} active symbols available[/green]")

        rb_symbols = [s for s in active if "RANGE" in s.get("display_name", "").upper()]
        if rb_symbols:
            console.print(f"[cyan]  Range Break symbols found: {len(rb_symbols)}[/cyan]")
            for s in rb_symbols[:5]:
                console.print(f"    {s['symbol']}: {s['display_name']}")

        return client

    except Exception as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        raise


async def download_data(client: DerivClient, symbol: str = "R_100") -> None:
    """Download historical candle data for backtesting."""
    collector = DataCollector(client)
    console.rule(f"[bold blue]Downloading data for {symbol}[/bold blue]")

    df = await collector.download_candles(symbol=symbol, count=5000, granularity=60)
    console.print(f"[green]✓ Downloaded {len(df)} candles[/green]")
    console.print(f"  Date range: {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    """Main bot entry point."""
    setup_logging()

    mode = sys.argv[1] if len(sys.argv) > 1 else "backtest"

    console.rule("[bold magenta]Synthetic Trader — Deriv Bot v0.2.0[/bold magenta]")
    console.print(f"[dim]Mode: {mode.upper()}[/dim]")
    console.print(f"[dim]Strategy: Range Break (multi-factor scoring + Kelly dinámico + circuit breaker dual)[/dim]")
    console.print()

    try:
        if mode == "backtest":
            run_backtest_offline()

        elif mode == "connect":
            client = await test_connection()
            await download_data(client, symbol="R_100")
            await client.disconnect()
            console.print("\n[green]Done. Bot disconnected.[/green]")

        elif mode == "paper":
            from src.trading.paper_runner import run_paper_trading
            console.print("[yellow]Starting paper trading on Deriv demo account...[/yellow]")
            console.print(f"[dim]Symbol: RB100 | Max trades: 30 | Score threshold: 0.50[/dim]")
            console.print("[bold red]Paper trading ONLY — no real money at risk.[/bold red]")
            console.print()
            await run_paper_trading(symbol="RB100", max_trades=30)

        else:
            console.print(f"[red]Unknown mode: {mode}[/red]")
            console.print("[dim]Usage: python -m src.main [backtest|connect|paper][/dim]")
            sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logging.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())