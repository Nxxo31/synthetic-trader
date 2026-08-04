"""FastAPI server for Synthetic Trader — live dashboard +WebSocket + backtest API."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from src.api._aliases import with_aliases

app = FastAPI(
    title="Synthetic Trader API",
    version="0.2.0",
    description="Multi-factor scoring + Kelly dinámico + circuit breaker dual",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).parent.parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports" / "backtest"
DAILY_REPORTS_DIR = PROJECT_ROOT / "reports" / "daily"
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
REALTIME_STATE_FILE = PROJECT_ROOT / "realtime" / "paper_state.json"
REALTIME_TRADES_FILE = PROJECT_ROOT / "realtime" / "trades.jsonl"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict:
    return with_aliases({"status": "ok", "timestamp": datetime.utcnow().isoformat(), "version": "0.2.0"})


# ---------------------------------------------------------------------------
# Backtest endpoints
# ---------------------------------------------------------------------------

@app.get("/api/backtest/results")
async def get_backtest_results() -> list:
    """Lista todos los reportes de backtest disponibles."""
    if not REPORTS_DIR.exists():
        return []

    results: list = []
    for filename in sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True):
        try:
            data = json.loads(filename.read_text())
            results.append(
                {
                    "filename": filename.name,
                    "strategy": data.get("strategy", "unknown"),
                    "symbol": data.get("symbol", "unknown"),
                    "total_trades": data.get("total_trades", 0),
                    "win_rate": data.get("win_rate", 0),
                    "sharpe_ratio": data.get("sharpe_ratio", 0),
                    "max_drawdown": data.get("max_drawdown", 0),
                    "total_pnl": data.get("total_pnl", 0),
                    "profit_factor": data.get("profit_factor", 0),
                    "expectancy": data.get("expectancy", 0),
                    "gate_passed": data.get("gate_passed", False),
                    "gate_failures": data.get("gate_failures", []),
                }
            )
        except Exception:
            continue

    return with_aliases(results)


@app.get("/api/backtest/latest")
async def get_latest_backtest() -> JSONResponse:
    """Retorna el reporte de backtest más reciente completo."""
    if not REPORTS_DIR.exists():
        return JSONResponse({"error": "No reports found"}, status_code=404)

    json_files = sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
    if not json_files:
        return JSONResponse({"error": "No reports found"}, status_code=404)

    data = json.loads(json_files[0].read_text())
    return JSONResponse(with_aliases(data))


# ---------------------------------------------------------------------------
# Bot status (simulated — will be wired to live bot when paper trading runs)
# ---------------------------------------------------------------------------

@app.get("/api/bot/status")
async def get_bot_status() -> dict:
    """Estado actual del bot — lee del estado en tiempo real si está disponible,
    sino del último reporte de backtest."""
    # Prefer real-time state if exists (paper trading)
    if os.path.exists(REALTIME_STATE_FILE):
        try:
            with open(REALTIME_STATE_FILE, "r") as f:
                state = json.load(f)
            # Ensure expected fields
            return with_aliases({
                "bot_id": "synthetic-trader-001",
                "strategy": state.get("strategy", state.get("symbol", "RangeBreak")),
                "symbol": state.get("symbol", "RB100"),
                "mode": state.get("mode", "paper"),
                "balance": state.get("balance", 10000.0),
                "pnl": state.get("pnl", 0.0),
                "trades_today": state.get("trades_today", 0),
                "is_halted": state.get("is_halted", False),
                "circuit_breaker": state.get(
                    "circuit_breaker", {"consecutive_losses": 0, "is_halted": False}
                ),
                "kpi": {
                    "sharpe_ratio": 0.0,
                    "win_rate": 0.0,
                    "max_drawdown": 0.0,
                    "profit_factor": 0.0,
                    "expectancy": 0.0,
                },
                "gate_passed": False,
                "gate_failures": [],
                "last_update": state.get("last_update", datetime.utcnow().isoformat()),
            })
        except Exception:
            pass  # fallback to backreport

    # Fallback to backtest report
    json_files = (
        sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
        if REPORTS_DIR.exists()
        else []
    )
    if not json_files:
        return with_aliases({
            "bot_id": "synthetic-trader-001",
            "strategy": "RangeBreak",
            "symbol": "RB100",
            "mode": "idle",
            "balance": 10000.0,
            "pnl": 0.0,
            "trades_today": 0,
            "is_halted": False,
            "circuit_breaker": {"consecutive_losses": 0, "is_halted": False},
            "kpi": {
                "sharpe_ratio": 0.0,
                "win_rate": 0.0,
                "max_drawdown": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
            },
            "gate_passed": False,
            "gate_failures": [],
            "last_update": datetime.utcnow().isoformat(),
        })
    data = json.loads(json_files[0].read_text())
    cb = data.get("circuit_breaker_status", {})
    return with_aliases({
        "bot_id": "synthetic-trader-001",
        "strategy": data.get("strategy", "RangeBreak"),
        "symbol": data.get("symbol", "RB100"),
        "mode": "backtest",
        "balance": data.get("equity_curve", [10000])[-1]
        if data.get("equity_curve")
        else 10000,
        "pnl": data.get("total_pnl", 0.0),
        "trades_today": data.get("total_trades", 0),
        "is_halted": cb.get("is_halted", False),
        "circuit_breaker": cb,
        "kpi": {
            "sharpe_ratio": data.get("sharpe_ratio", 0),
            "win_rate": data.get("win_rate", 0),
            "max_drawdown": data.get("max_drawdown", 0),
            "profit_factor": data.get("profit_factor", 0),
            "expectancy": data.get("expectancy", 0),
        },
        "gate_passed": data.get("gate_passed", False),
        "gate_failures": data.get("gate_failures", []),
        "last_update": datetime.utcnow().isoformat(),
    })


@app.get("/api/bot/trades")
async def get_bot_trades() -> list:
    """Historial de trades — prioriza datos en tiempo real (paper_state.json),
    fallback al último reporte de backtest."""
    # 1) Live trades from paper_state.json (realtime)
    if os.path.exists(REALTIME_STATE_FILE):
        try:
            with open(REALTIME_STATE_FILE, "r") as f:
                state = json.load(f)
            live_trades = state.get("recent_trades", [])
            if live_trades:
                return with_aliases(live_trades)
        except Exception:
            pass

    # 2) Fallback to backtest report
    json_files = (
        sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
        if REPORTS_DIR.exists()
        else []
    )
    if not json_files:
        return []

    data = json.loads(json_files[0].read_text())
    return with_aliases(data.get("trades", []))


# ---------------------------------------------------------------------------
# Market info
# ---------------------------------------------------------------------------

@app.get("/api/market/symbols")
async def get_market_symbols() -> list:
    """Símbolos sintéticos disponibles en Deriv."""
    return with_aliases([
        {"symbol": "R_10", "display": "Volatility 10", "category": "Volatility"},
        {"symbol": "R_25", "display": "Volatility 25", "category": "Volatility"},
        {"symbol": "R_50", "display": "Volatility 50", "category": "Volatility"},
        {"symbol": "R_75", "display": "Volatility 75", "category": "Volatility"},
        {"symbol": "R_100", "display": "Volatility 100", "category": "Volatility"},
        {"symbol": "RB100", "display": "Range Break 100", "category": "Range Break"},
        {"symbol": "BOOM1000", "display": "Boom 1000", "category": "Boom/Crash"},
        {"symbol": "CRASH1000", "display": "Crash 1000", "category": "Boom/Crash"},
        {"symbol": "STEPT10", "display": "Step 10", "category": "Step"},
    ])


# ---------------------------------------------------------------------------
# Daily reports
# ---------------------------------------------------------------------------

@app.get("/api/daily/report")
async def get_daily_report() -> dict:
    """Returns the most recent daily report (today or yesterday)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    for date_str in [today, yesterday]:
        report_path = DAILY_REPORTS_DIR / f"{date_str}.json"
        if report_path.exists():
            try:
                return with_aliases(json.loads(report_path.read_text()))
            except Exception:
                continue

    return with_aliases({"error": "No daily report found"})


@app.get("/api/daily/history")
async def get_daily_history(limit: int = 30) -> list:
    """Returns historical daily reports (without trades for efficiency)."""
    if not DAILY_REPORTS_DIR.exists():
        return []

    reports: list = []
    for report_file in sorted(DAILY_REPORTS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(report_file.read_text())
            # Remove trades for efficiency in history view
            data.pop("trades", None)
            reports.append(data)
        except Exception:
            continue
    return with_aliases(reports)


# ---------------------------------------------------------------------------
# WebSocket — live data stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/live-data")
async def websocket_live_data(websocket: WebSocket) -> None:
    """Stream en tiempo real: equity, trades, risk status.

    En modo paper/live: lee de los archivos en tiempo real.
    En modo backtest: envía el último reporte replay-like.

    Cada payload ``data``/``trade`` se normaliza con :func:`with_aliases`
    para añadir claves JSON completas en español (resultado_operaciones,
    capital_disponible, indice_sharpe, …) manteniendo las originales.
    """
    await websocket.accept()
    try:
        # Check if realtime files exist (paper trading active)
        realtime_state = Path("/home/sebas/proyectos/synthetic-trader/realtime/paper_state.json")
        equity_file = Path("/home/sebas/proyectos/synthetic-trader/realtime/equity.jsonl")
        trades_file = Path("/home/sebas/proyectos/synthetic-trader/realtime/trades.jsonl")

        if realtime_state.exists() and equity_file.exists() and trades_file.exists():
            # Stream real-time data: send initial state, then tail the files
            # Send current state
            try:
                with open(realtime_state, "r") as f:
                    state = json.load(f)
                await websocket.send_text(
                    json.dumps(
                        with_aliases({
                            "type": "state",
                            "data": state,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    )
                )
            except Exception:
                pass

            # We'll send periodic updates by reading the files every 2 seconds
            # For simplicity, we'll just send the latest state every 2 seconds
            # In a more advanced version, we could use file watching or seek to end of files.
            while True:
                try:
                    # Send state
                    with open(realtime_state, "r") as f:
                        state = json.load(f)
                    await websocket.send_text(
                        json.dumps(
                            with_aliases({
                "type": "state",
                            "data": state,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    )
                )
                    # Send latest equity point (last line)
                    if equity_file.exists():
                        with open(equity_file, "r") as f:
                            lines = f.readlines()
                            if lines:
                                last_eq = json.loads(lines[-1].strip())
                                await websocket.send_text(
                                    json.dumps(
                                        with_aliases({
                                            "type": "equity_update",
                                            "data": last_eq,
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                        })
                                    )
                                )
                    # Send latest trade (last line)
                    if trades_file.exists():
                        with open(trades_file, "r") as f:
                            lines = f.readlines()
                            if lines:
                                last_trade = json.loads(lines[-1].strip())
                                await websocket.send_text(
                                    json.dumps(
                                        with_aliases({
                                            "type": "trade",
                                            "data": last_trade,
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                        })
                                    )
                                )
                except WebSocketDisconnect:
                    raise  # Re-raise to exit loop cleanly
                except Exception as e:
                    print(f"Error reading realtime files: {e}")
                await asyncio.sleep(2)
        else:
            # Fallback to backtest replay (original behavior)
            json_files = (
                sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
                if REPORTS_DIR.exists()
                else []
            )
            if json_files:
                data = json.loads(json_files[0].read_text())
                equity_curve = data.get("equity_curve", [])
                trades = data.get("trades", [])
                # Stream equity curve point by point
                for i, point in enumerate(equity_curve):
                    await websocket.send_text(
                        json.dumps(
                            with_aliases({
                                "type": "equity",
                                "index": i,
                                "value": point,
                                "total": len(equity_curve),
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                        )
                    )
                    await asyncio.sleep(0.15)  # 150ms per point for visual effect
                # Stream trades
                for j, trade in enumerate(trades):
                    await websocket.send_text(
                        json.dumps(
                            with_aliases({
                                "type": "trade",
                                "trade": trade,
                                "index": j,
                                "total": len(trades),
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                        )
                    )
                    await asyncio.sleep(0.2)
            # After replay, send periodic status updates
            while True:
                await websocket.send_text(
                    json.dumps(
                        with_aliases({
                            "type": "status",
                            "timestamp": datetime.utcnow().isoformat(),
                            "message": "Streaming completed. Waiting for new data...",
                        })
                    )
                )
                await asyncio.sleep(10)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await websocket.close()




# ---------------------------------------------------------------------------
# Capital Allocator Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/allocator/config")
async def get_allocator_config() -> dict:
    """Get current capital allocator configuration.

    Reads the live bot balance from ``realtime/paper_state.json`` (the actual
    Deriv demo account balance while paper trading) and computes the
    reserve / surplus split via :class:`CapitalAllocatorConfig` +
    :class:`CapitalAllocator`.

    If no live state file exists (paper trading not started), falls back to
    the configured ``initial_capital`` default and reports ``data_available:
    false`` so the dashboard can degrade gracefully.
    """
    from src.risk.capital_allocator import CapitalAllocator, CapitalAllocatorConfig

    # Live balance from realtime state (actual Deriv demo account balance)
    live_balance: float | None = None
    live_pnl: float = 0.0
    data_available = False
    if REALTIME_STATE_FILE.exists():
        try:
            with open(REALTIME_STATE_FILE, "r") as f:
                state = json.load(f)
            live_balance = float(state.get("balance", 0.0))
            live_pnl = float(state.get("pnl", 0.0))
            data_available = True
        except Exception:
            live_balance = None

    # Build the allocator config — uses CapitalAllocatorConfig defaults
    # (reserva_pct=0.80, superávit_diario_pct=0.20, initial_capital=10000.0)
    config = CapitalAllocatorConfig(
        initial_capital=live_balance if live_balance is not None else 10000.0,
    )
    allocator = CapitalAllocator(config)
    allocator.reset_daily(
        capital_total=live_balance if live_balance is not None else config.initial_capital
    )
    if live_pnl != 0.0:
        allocator.record_trade(live_pnl)
    state = allocator.get_state()

    return with_aliases({
        "data_available": data_available,
        "capital_total": state["capital_total"],
        "reserva": state["reserva"],
        "superávit_diario": state["superávit_diario"],
        "superávit_disponible": state["superávit_disponible"],
        "superávit_usado": state["superávit_usado"],
        "reserva_pct": config.reserva_pct,
        "superávit_diario_pct": config.superávit_diario_pct,
        "live_balance": live_balance if live_balance is not None else config.initial_capital,
        "live_pnl": live_pnl,
        "trades_today": state["trades_count"],
        "total_pnl": state["total_pnl"],
        "return_pct": state["return_pct"],
        "is_active": state["is_active"],
        "rebalance_daily": config.rebalance_daily,
        "min_surplus": config.min_surplus,
    })

@app.post("/api/allocator/config")
async def update_allocator_config(config: dict) -> dict:
    """Update capital allocator configuration."""
    # For now, just acknowledge - in production would validate and persist
    return with_aliases({
        "status": "updated", 
        "config": config,
        "note": "Configuration updated in memory only (not persisted)"
    })

@app.get("/api/allocator/allocate")
async def get_allocation(
    starting_balance: float | None = None,
    daily_pnl: float = 0.0
) -> dict:
    """Get capital allocation for given balance and daily P&L.

    If starting_balance is not provided, reads the live bot balance from
    realtime/paper_state.json (the actual Deriv demo account balance).
    """
    from src.risk.capital_allocator import CapitalAllocator, CapitalAllocatorConfig

    # Auto-detect balance from live bot state if not provided
    if starting_balance is None:
        import json as _json
        import os as _os
        state_path = "/home/sebas/proyectos/synthetic-trader/realtime/paper_state.json"
        if _os.path.exists(state_path):
            try:
                with open(state_path) as f:
                    state = _json.load(f)
                starting_balance = float(state.get("balance", 10000.0))
                daily_pnl = float(state.get("pnl", 0.0))
            except Exception:
                starting_balance = 10000.0
        else:
            starting_balance = 10000.0

    config = CapitalAllocatorConfig(
        reserva_pct=0.80,
        superávit_diario_pct=0.20,
        initial_capital=starting_balance
    )
    allocator = CapitalAllocator(config)
    allocator.reset_daily(capital_total=starting_balance)
    if daily_pnl != 0:
        allocator.record_trade(daily_pnl)
    state = allocator.get_state()
    return with_aliases({
        "reserve": state["reserva"],
        "daily_surplus": state["superávit_disponible"],
        "reinvestable": max(0.0, daily_pnl) if daily_pnl > 0 else 0.0,
        "total_available": state["reserva"] + state["superávit_disponible"],
        "micro_stake_size": 0.0,
        "micro_stakes_count": 5,
        "live_balance": starting_balance,
        "live_pnl": daily_pnl,
        "allocation_pct": {
            "reserve": state["reserva"] / starting_balance * 100 if starting_balance > 0 else 0,
            "surplus": state["superávit_disponible"] / starting_balance * 100 if starting_balance > 0 else 0
        }
    })

# ---------------------------------------------------------------------------
# Strategy Attribution Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/attribution/matrix")
async def get_attribution_matrix() -> dict:
    """Get strategy performance matrix for heatmap display."""
    from src.analysis.attribution import StrategyAttribution
    attribution = StrategyAttribution()
    matrix_data = attribution.profitability_matrix(metric="total_pnl", latest_only=True)
    
    # Convert nested dict {symbol: {strategy: value}} to expected format
    strategies = list({s for sym_data in matrix_data.values() for s in sym_data})
    symbols = list(matrix_data.keys())
    
    result = {
        "strategies": strategies,
        "symbols": symbols,
        "matrix": []
    }
    
    for i, strategy in enumerate(strategies):
        row_data = []
        for j, symbol in enumerate(symbols):
            sym_data = matrix_data.get(symbol, {})
            value = sym_data.get(strategy, None)
            if value is not None:
                row_data.append({
                    "strategy_name": strategy,
                    "symbol": symbol,
                    "pnl": round(value, 2) if isinstance(value, (int, float)) else 0.0,
                    "sharpe": 0.0,  # Would need separate query for Sharpe
                })
            else:
                row_data.append(None)
        result["matrix"].append(row_data)
    
    return with_aliases(result)

@s.app.get("/api/attribution/ranking")
async def get_strategy_ranking() -> dict:
    """Get strategies ranked by average Sharpe ratio."""
    from src.analysis.attribution import StrategyAttribution
    attribution = StrategyAttribution()
    best = attribution.best_strategy_per_symbol(metric="total_pnl", latest_only=True)
    
    result = []
    for symbol, (strat_name, metric_value) in best.items():
        result.append({
            "symbol": symbol,
            "strategy_name": strat_name,
            "pnl": round(metric_value, 2),
            "sharpe": 0.0  # Would need separate query
        })
    
    return with_aliases({"ranking": result})

# ------------------------------------------------------------------ #
# Brinson-Fachler Attribution Endpoints
# ------------------------------------------------------------------ #

@app.get("/api/attribution/brinson")
async def get_brinson_fachler_decomposition(
    weight_column: str = "total_trades",
    return_column: str = "avg_pnl_pct",
    benchmark_strategy: str | None = None,
    min_trades: int = 1,
) -> dict:
    """
    Brinson-Fachler performance attribution decomposition.
    
    Decomposes excess return into allocation, selection, and interaction effects.
    
    Args:
        weight_column: Column for weights ("total_trades" or "total_pnl_abs")
        return_column: Column for returns ("avg_pnl_pct", "win_rate", "sharpe", "expectancy")
        benchmark_strategy: Optional strategy name to use as benchmark within each symbol
        min_trades: Minimum trades required for a cell to be included
        
    Returns:
        Brinson-Fachler result with three effects and per-symbol breakdown
    """
    from fastapi.responses import JSONResponse
    from src.analysis.attribution import StrategyAttribution
    
    attribution = StrategyAttribution()
    try:
        result = attribution.brinson_fachler_decomposition(
            weight_column=weight_column,
            return_column=return_column,
            benchmark_strategy=benchmark_strategy,
            min_trades=min_trades,
        )
        return with_aliases(result.to_dict())
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content=with_aliases({"error": str(e)})
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=with_aliases({"error": f"Internal server error: {str(e)}"})
        )

# ---------------------------------------------------------------------------
# Return Projection Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/projection/equity")
async def get_equity_projection(
    days: int = 7,
    surplus: float = 200.0
) -> dict:
    """
    Get Monte Carlo equity projection from real strategy metrics.

    Reads the best strategy's ``win_rate``, ``sharpe_ratio`` and ``expectancy``
    from ``data/strategies.db`` via :class:`StrategyAttribution`, then runs
    a forward Monte Carlo projection with :class:`ReturnProjector`.

    If no performance rows exist in the DB yet (paper trading just started,
    no backtest persisted), returns zeros with ``data_available: false`` so
    the dashboard can degrade gracefully instead of showing fake numbers.

    Args:
        days: Number of days to project (informational; the projector uses
            ``horizon_trades`` based on typical trades-per-day).
        surplus: Starting surplus capital to project from.
    """
    from src.analysis.attribution import StrategyAttribution
    from src.analysis.projector import ReturnProjector

    # --- 1. Fetch real strategy metrics from strategies.db ---
    attribution = StrategyAttribution()

    # Use the best strategy per symbol (by total_pnl, latest only) to pick
    # the strategy with the strongest historical edge for the projection.
    win_rate = 0.0
    sharpe: float | None = None
    expectancy = 0.0
    strategy_name: str | None = None
    data_available = False

    try:
        best_per_symbol = attribution.best_strategy_per_symbol(
            metric="total_pnl", latest_only=True
        )
    except Exception:
        best_per_symbol = {}

    if best_per_symbol:
        # Pick the best strategy across all symbols by total_pnl.
        # best_per_symbol: {symbol: (strategy_name, total_pnl)}
        best_symbol, (strategy_name, best_pnl) = max(
            best_per_symbol.items(), key=lambda kv: kv[1][1]
        )
        _ = best_symbol  # noqa: F841  (kept for log clarity)

        # Fetch full metrics for that strategy×symbol (latest row).
        try:
            with attribution._connect() as conn:  # noqa: SLF001
                row = conn.execute(
                    """
                    SELECT sp.win_rate, sp.sharpe, sp.expectancy,
                           sp.profit_factor, sp.total_trades
                    FROM strategy_performance sp
                    JOIN strategies s ON s.id = sp.strategy_id
                    WHERE s.name = ? AND sp.symbol = ?
                    ORDER BY sp.backtest_date DESC, sp.id DESC
                    LIMIT 1
                    """,
                    (strategy_name, best_symbol),
                ).fetchone()
        except Exception:
            row = None

        if row is not None:
            win_rate = float(row["win_rate"] or 0.0)
            sharpe_val = row["sharpe"]
            sharpe = float(sharpe_val) if sharpe_val is not None else None
            expectancy = float(row["expectancy"] or 0.0)
            # Require meaningful data: at least some trades and a non-zero
            # win rate, otherwise the projection is meaningless.
            total_trades = int(row["total_trades"] or 0)
            if total_trades > 0 and 0.0 < win_rate < 1.0:
                data_available = True

    # --- 2. No-data case: return zeros with the flag ---
    if not data_available:
        return with_aliases({
            "data_available": False,
            "strategy": strategy_name,
            "config": {
                "days": days,
                "surplus": surplus,
            },
            "metrics_used": {
                "win_rate": 0.0,
                "sharpe": 0.0,
                "expectancy": 0.0,
            },
            "projection": {
                "equity_p5": [],
                "equity_p50": [],
                "equity_p95": [],
                "final_value_p5": 0.0,
                "final_value_p50": 0.0,
                "final_value_p95": 0.0,
                "return_p5": 0.0,
                "return_p50": 0.0,
                "return_p95": 0.0,
                "max_dd_p5": 0.0,
                "max_dd_p50": 0.0,
                "max_dd_p95": 0.0,
                "prob_profit": 0.0,
                "sharpe_estimate": 0.0,
            },
        })

    # --- 3. Run the projection with real metrics ---
    # ReturnProjector requires win_rate (0-1), sharpe (float|None),
    # and expectancy (R-multiples) as positional args.
    # Project `surplus` capital over a horizon proportional to ``days``.
    horizon_trades = max(10, days * 10)  # ~10 trades/day heuristic

    try:
        projector = ReturnProjector(
            win_rate=win_rate,
            sharpe=sharpe,
            expectancy=expectancy,
            horizon_trades=horizon_trades,
            initial_capital=surplus,
        )
        result = projector.project()

        curve_dict = result.curve.to_dict()
        metrics_dict = result.metrics.to_dict()

        return with_aliases({
            "data_available": True,
            "strategy": strategy_name,
            "config": {
                "days": days,
                "surplus": surplus,
                "seed": result.seed,
                "horizon_trades": horizon_trades,
            },
            "metrics_used": {
                "win_rate": round(win_rate, 4),
                "sharpe": round(sharpe, 4) if sharpe is not None else 0.0,
                "expectancy": round(expectancy, 4),
            },
            "projection": {
                "equity_p5": [round(x, 2) for x in curve_dict.get("p5", [])],
                "equity_p50": [round(x, 2) for x in curve_dict.get("p50", [])],
                "equity_p95": [round(x, 2) for x in curve_dict.get("p95", [])],
                "final_value_p5": round(metrics_dict.get("final_equity_p5", 0.0), 2),
                "final_value_p50": round(metrics_dict.get("final_equity_median", 0.0), 2),
                "final_value_p95": round(metrics_dict.get("final_equity_p95", 0.0), 2),
                "return_p5": round(metrics_dict.get("final_return_p5", 0.0) * 100, 2),
                "return_p50": round(metrics_dict.get("final_return_median", 0.0) * 100, 2),
                "return_p95": round(metrics_dict.get("final_return_p95", 0.0) * 100, 2),
                "max_dd_p5": round(metrics_dict.get("max_drawdown_p5", 0.0) * 100, 2),
                "max_dd_p50": round(metrics_dict.get("max_drawdown_median", 0.0) * 100, 2),
                "max_dd_p95": round(metrics_dict.get("max_drawdown_p95", 0.0) * 100, 2),
                "prob_profit": round(metrics_dict.get("p_profitable", 0.0) * 100, 2),
                "sharpe_estimate": round(metrics_dict.get("sharpe_projected_median", 0.0), 2),
            },
        })
    except Exception as e:
        # If the projector fails on real data (e.g. degenerate expectancy),
        # report the error transparently rather than fabricating mock data.
        return with_aliases({
            "data_available": False,
            "strategy": strategy_name,
            "error": f"Projection failed with real metrics: {e}",
            "metrics_used": {
                "win_rate": round(win_rate, 4),
                "sharpe": round(sharpe, 4) if sharpe is not None else 0.0,
                "expectancy": round(expectancy, 4),
            },
            "config": {"days": days, "surplus": surplus},
            "projection": {
                "equity_p5": [], "equity_p50": [], "equity_p95": [],
                "final_value_p5": 0.0, "final_value_p50": 0.0, "final_value_p95": 0.0,
                "return_p5": 0.0, "return_p50": 0.0, "return_p95": 0.0,
                "max_dd_p5": 0.0, "max_dd_p50": 0.0, "max_dd_p95": 0.0,
                "prob_profit": 0.0, "sharpe_estimate": 0.0,
            },
        })

# ---------------------------------------------------------------------------
# Serve dashboards
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard() -> HTMLResponse:
    """Sirve el dashboard de backtest estático."""
    dashboard_path = DASHBOARD_DIR / "live_dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(dashboard_path.read_text())
    return HTMLResponse("<h1>Dashboard not found. Run build first.</h1>", status_code=404)


@app.get("/dashboard/backtest", response_class=HTMLResponse)
async def serve_backtest_dashboard() -> HTMLResponse:
    """Sirve el dashboard de backtest."""
    dashboard_path = DASHBOARD_DIR / "backtest_dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(dashboard_path.read_text())
    return HTMLResponse("<h1>Backtest dashboard not found.</h1>", status_code=404)