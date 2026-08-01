"""FastAPI server for Synthetic Trader — live dashboard +WebSocket + backtest API.

Endpoints:
    GET  /api/health                  — health check
    GET  /api/backtest/results         — lista reportes de backtest
    GET  /api/backtest/latest          — último reporte JSON
    GET  /api/bot/status               — estado del bot (risk, circuit breaker, balance)
    GET  /api/bot/trades               — historial de trades
    GET  /api/market/symbols           — símbolos disponibles
    GET  /api/strategies               — estrategias registradas (factory)
    GET  /api/daily/report             — reporte diario más reciente
    GET  /api/daily/history            — historial de reportes diarios
    WS   /ws/live-data                 — stream en tiempo real (equity, trades, risk)
    GET  /                              — sirve el dashboard HTML

Run:
    uvicorn src.api.server:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "version": "0.2.0"}


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
            results.append({
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
            })
        except Exception:
            continue

    return results


@app.get("/api/backtest/latest")
async def get_latest_backtest() -> JSONResponse:
    """Retorna el reporte de backtest más reciente completo."""
    if not REPORTS_DIR.exists():
        return JSONResponse({"error": "No reports found"}, status_code=404)

    json_files = sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
    if not json_files:
        return JSONResponse({"error": "No reports found"}, status_code=404)

    data = json.loads(json_files[0].read_text())
    return JSONResponse(data)


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
            return {
                "bot_id": "synthetic-trader-001",
                "strategy": state.get("symbol", "RangeBreak"),
                "symbol": state.get("symbol", "RB100"),
                "mode": state.get("mode", "paper"),
                "balance": state.get("balance", 10000.0),
                "pnl": state.get("pnl", 0.0),
                "trades_today": state.get("trades_today", 0),
                "is_halted": state.get("is_halted", False),
                "circuit_breaker": state.get("circuit_breaker", {"consecutive_losses": 0, "is_halted": False}),
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
            }
        except Exception:
            pass  # fallback to backreport
    # Fallback to backtest report
    json_files = sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True) if REPORTS_DIR.exists() else []
    if not json_files:
        return {
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
        }
    data = json.loads(json_files[0].read_text())
    cb = data.get("circuit_breaker_status", {})
    return {
        "bot_id": "synthetic-trader-001",
        "strategy": data.get("strategy", "RangeBreak"),
        "symbol": data.get("symbol", "RB100"),
        "mode": "backtest",
        "balance": data.get("equity_curve", [10000])[-1] if data.get("equity_curve") else 10000,
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
    }


@app.get("/api/bot/trades")
async def get_bot_trades() -> list:
    """Historial de trades del último reporte."""
    json_files = sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True) if REPORTS_DIR.exists() else []
    if not json_files:
        return []

    data = json.loads(json_files[0].read_text())
    return data.get("trades", [])


# ---------------------------------------------------------------------------
# Market info
# ---------------------------------------------------------------------------

@app.get("/api/market/symbols")
async def get_market_symbols() -> list:
    """Símbolos sintéticos disponibles en Deriv."""
    return [
        {"symbol": "R_10", "display": "Volatility 10", "category": "Volatility"},
        {"symbol": "R_25", "display": "Volatility 25", "category": "Volatility"},
        {"symbol": "R_50", "display": "Volatility 50", "category": "Volatility"},
        {"symbol": "R_75", "display": "Volatility 75", "category": "Volatility"},
        {"symbol": "R_100", "display": "Volatility 100", "category": "Volatility"},
        {"symbol": "RB100", "display": "Range Break 100", "category": "Range Break"},
        {"symbol": "BOOM1000", "display": "Boom 1000", "category": "Boom/Crash"},
        {"symbol": "CRASH1000", "display": "Crash 1000", "category": "Boom/Crash"},
        {"symbol": "STEPT10", "display": "Step 10", "category": "Step"},
    ]


@app.get("/api/strategies")
async def list_strategies() -> dict:
    """Lista las estrategias disponibles registradas en el factory.

    Usa ``available_strategies()`` del strategy_factory, así cualquier
    estrategia registrada (incluida Gems) aparece automáticamente.
    """
    from src.trading.strategy_factory import available_strategies

    return {
        "strategies": available_strategies(),
        "count": len(available_strategies()),
    }


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
                return json.loads(report_path.read_text())
            except Exception:
                continue
    
    return {"error": "No daily report found"}


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
    return reports


# ---------------------------------------------------------------------------
# WebSocket — live data stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/live-data")
async def websocket_live_data(websocket: WebSocket) -> None:
    """Stream en tiempo real: equity, trades, risk status.
    
    En modo paper/live: lee de los archivos en tiempo real.
    En modo backtest: envía el último reporte replay-like.
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
                await websocket.send_text(json.dumps({
                    "type": "state",
                    "data": state,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
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
                    await websocket.send_text(json.dumps({
                        "type": "state",
                        "data": state,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }))
                    # Send latest equity point (last line)
                    if equity_file.exists():
                        with open(equity_file, "r") as f:
                            lines = f.readlines()
                            if lines:
                                last_eq = json.loads(lines[-1].strip())
                                await websocket.send_text(json.dumps({
                                    "type": "equity_update",
                                    "data": last_eq,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }))
                    # Send latest trade (last line)
                    if trades_file.exists():
                        with open(trades_file, "r") as f:
                            lines = f.readlines()
                            if lines:
                                last_trade = json.loads(lines[-1].strip())
                                await websocket.send_text(json.dumps({
                                    "type": "trade",
                                    "data": last_trade,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }))
                except WebSocketDisconnect:
                    raise  # Re-raise to exit loop cleanly
                except Exception as e:
                    print(f"Error reading realtime files: {e}")
                await asyncio.sleep(2)
        else:
            # Fallback to backtest replay (original behavior)
            json_files = sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True) if REPORTS_DIR.exists() else []
            if json_files:
                data = json.loads(json_files[0].read_text())
                equity_curve = data.get("equity_curve", [])
                trades = data.get("trades", [])
                # Stream equity curve point by point
                for i, point in enumerate(equity_curve):
                    await websocket.send_text(json.dumps({
                        "type": "equity",
                        "index": i,
                        "value": point,
                        "total": len(equity_curve),
                        "timestamp": datetime.utcnow().isoformat(),
                    }))
                    await asyncio.sleep(0.15)  # 150ms per point for visual effect
                # Stream trades
                for j, trade in enumerate(trades):
                    await websocket.send_text(json.dumps({
                        "type": "trade",
                        "trade": trade,
                        "index": j,
                        "total": len(trades),
                        "timestamp": datetime.utcnow().isoformat(),
                    }))
                    await asyncio.sleep(0.2)
            # After replay, send periodic status updates
            while True:
                await websocket.send_text(json.dumps({
                    "type": "status",
                    "timestamp": datetime.utcnow().isoformat(),
                    "message": "Streaming completed. Waiting for new data...",
                }))
                await asyncio.sleep(10)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await websocket.close()
# ---------------------------------------------------------------------------
# Strategy DB endpoints — backed by the Strategy Histórica DB (SQLite)
#
# Coexists with the factory-based GET /api/strategies above. The DB-backed
# routes manage strategy *versions* (semantic versioning, lineage, rollback),
# while the factory route lists the *implementations* registered in code.
# To avoid colliding with the existing GET /api/strategies handler, the DB
# create endpoint reuses that path with a POST verb. The compare endpoint is
# declared before the parametric {strategy_id} route so FastAPI matches the
# literal path first.
# ---------------------------------------------------------------------------


class StrategyCreate(BaseModel):
    """Request body for POST /api/strategies."""

    name: str
    version: str
    description: str | None = None
    parameters: dict[str, Any] | None = None
    lineage_parent_id: int | None = None
    market_type: str = "synthetic"
    status: str = "active"


def _strategy_service() -> Any:
    """Build a fresh StrategyService per request (cheap, short-lived conn)."""
    from src.db.service import StrategyService

    return StrategyService()


@app.post("/api/strategies")
async def create_strategy(payload: StrategyCreate) -> dict[str, Any]:
    """Create a new strategy record in the BD Histórica.

    Returns 409 on duplicate (name, version); 422 on invalid semver.
    """
    try:
        return _strategy_service().create(
            name=payload.name,
            version=payload.version,
            description=payload.description,
            parameters=payload.parameters,
            lineage_parent_id=payload.lineage_parent_id,
            market_type=payload.market_type,
            status=payload.status,
        )
    except sqlite3.IntegrityError as exc:  # unique violation
        raise HTTPException(
            status_code=409,
            detail=f"Strategy {payload.name!r} v{payload.version!r} already exists",
        ) from exc


@app.get("/api/strategies/compare")
async def compare_strategies(
    a: int = Query(..., description="strategy_a_id"),
    b: int = Query(..., description="strategy_b_id"),
    metric: str = Query("sharpe", description="metric to compare"),
) -> dict[str, Any]:
    """Compare two strategies on a single metric. Records the result."""
    from src.db.service import PerformanceService

    try:
        return PerformanceService().compare(a, b, metric=metric)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/strategies/{strategy_id}")
async def get_strategy_detail(strategy_id: int) -> dict[str, Any]:
    """Return one strategy by id (decoded JSON columns)."""
    row = _strategy_service().get(strategy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return row


@app.get("/api/strategies/{strategy_id}/performance")
async def get_strategy_performance(
    strategy_id: int,
    symbol: str | None = Query(None, description="Filter by symbol"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Return backtest performance history for a strategy."""
    from src.db.service import PerformanceService

    return PerformanceService().history(strategy_id, symbol=symbol, limit=limit)


@app.get("/api/regimes")
async def get_regimes(
    limit: int = Query(50, ge=1, le=500),
    current: bool = Query(False, description="Only return the current regime"),
) -> Any:
    """List recent market regimes or fetch the current one."""
    from src.db.service import RegimeService

    svc = RegimeService()
    if current:
        row = svc.current()
        return row if row is not None else {}
    return svc.history(limit=limit)


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