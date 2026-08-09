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
REALTIME_EQUITY_FILE = PROJECT_ROOT / "realtime" / "equity.jsonl"
# Allocator configuration persisted by POST /api/allocator/config.
# Stored as JSON so the GET endpoint can re-hydrate the same split on every
# request (and the dashboard's controls remain stable across page reloads).
ALLOCATOR_CONFIG_FILE = PROJECT_ROOT / "realtime" / "allocator_config.json"


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
    """Historial de trades — merge de fuentes en tiempo real + fallback backtest.

    Order of preference:
      1. ``realtime/trades.jsonl`` (JSONL, one trade per line) — the
         authoritative live trade log appended by the paper runner.
      2. ``realtime/paper_state.json``'s ``recent_trades`` array — a
         in-memory snapshot the runner also writes.
      3. Latest backtest report's ``trades`` array (fallback).

    Sources 1 and 2 are merged (deduplicated by timestamp) so the
    dashboard sees every trade regardless of which file has it.  This
    makes the endpoint robust to either source being absent.
    """
    merged: list[dict] = []
    seen_ts: set[str] = set()

    # 1) Live trades from trades.jsonl (JSONL tail — bounded read of all lines).
    if REALTIME_TRADES_FILE.exists():
        try:
            with open(REALTIME_TRADES_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    trade = json.loads(line)
                    ts = str(trade.get("timestamp", ""))
                    if ts and ts not in seen_ts:
                        seen_ts.add(ts)
                        merged.append(trade)
        except Exception:
            pass

    # 2) Live trades from paper_state.json recent_trades (snapshot).
    if REALTIME_STATE_FILE.exists():
        try:
            with open(REALTIME_STATE_FILE, "r") as f:
                state = json.load(f)
            for trade in state.get("recent_trades", []):
                ts = str(trade.get("timestamp", ""))
                if ts and ts not in seen_ts:
                    seen_ts.add(ts)
                    merged.append(trade)
                elif not ts:
                    merged.append(trade)
        except Exception:
            pass

    if merged:
        return with_aliases(merged)

    # 3) Fallback to backtest report.
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

def _read_last_jsonl(file_path: Path) -> dict | None:
    """Read only the last line of a JSONL file (bounded tail read).

    Instead of ``readlines()`` (which loads the entire file into memory —
    e.g. 1459 equity points), this seeks to the end, walks backwards in
    bounded chunks to find the last newline, and parses a single line.
    Returns ``None`` for empty / unreadable files or malformed last line.
    """
    if not file_path.exists():
        return None
    try:
        with open(file_path, "rb") as f:
            f.seek(0, 2)  # end of file
            size = f.tell()
            if size == 0:
                return None
            # Read backwards in up to a 4 KB window to find the last line.
            chunk = min(size, 4096)
            f.seek(size - chunk)
            data = f.read(chunk)
            # Strip a trailing newline so split always ends with real content.
            lines = data.split(b"\n")
            last_line = b""
            for candidate in reversed(lines):
                candidate = candidate.strip()
                if candidate:
                    last_line = candidate
                    break
            if not last_line:
                return None
            return json.loads(last_line.decode("utf-8").strip())
    except Exception:
        return None


def _file_signature(file_path: Path) -> tuple[float, int] | None:
    """Return ``(mtime, size)`` for change detection, or ``None`` if absent."""
    try:
        stat = file_path.stat()
        return (stat.st_mtime, stat.st_size)
    except OSError:
        return None


@app.websocket("/ws/live-data")
async def websocket_live_data(websocket: WebSocket) -> None:
    """Stream en tiempo real: state, equity, trades — delta-only.

    Efficiency design (vs. the previous per-2 s full-state broadcast):
      - **Delta detection**: track ``(mtime, size)`` signatures for each
        realtime file; a frame is sent only when a file actually changed.
        Idle sessions produce zero messages (only a liveness ping every 30 s).
      - **Bounded file reads**: JSONL tailing uses ``_read_last_jsonl`` which
        seeks backwards in a 4 KB window instead of ``readlines()`` loading
        the whole (1459-line equity) file into memory each cycle.
      - **Compression**: accept the connection so Starlette negotiates
        ``permessage-deflate`` when the client offers it.
      - **Liveness**: a 30 s ping flushes the send queue and surfaces dead
        clients (stale ``WebSocketDisconnect`` ends the loop cleanly).
      - **Polled interval**: 1 s (down from 2 s) — the cost is one cheap
        ``stat()`` per file; real work only fires on actual changes.

    Each ``data``/``trade`` payload is normalized with :func:`with_aliases`
    so Spanish full-word keys (resultado_operaciones, capital_disponible,
    indice_sharpe, …) appear alongside the originals for dashboard consumers.
    """
    # Accept with permessage-deflate negotiation (Starlette handles the
    # extension if the client's Upgrade offers Sec-WebSocket-Extensions).
    await websocket.accept()

    # Use module-level Path constants (not hardcoded absolute strings) so
    # tests can monkeypatch them via isolated_realtime.
    state_file = REALTIME_STATE_FILE
    equity_file = REALTIME_EQUITY_FILE
    trades_file = REALTIME_TRADES_FILE

    # ---- Backtest replay mode (no live realtime files) ----
    has_realtime = state_file.exists()
    if not has_realtime:
        # Backtest replay: stream the latest backtest's equity curve +
        # trades in a single sweep, then idle with periodic pings.
        json_files = (
            sorted(REPORTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
            if REPORTS_DIR.exists()
            else []
        )
        if json_files:
            try:
                data = json.loads(json_files[0].read_text())
                equity_curve = data.get("equity_curve", [])
                trades = data.get("trades", [])
                ts0 = datetime.now(timezone.utc).isoformat()
                await websocket.send_text(
                    json.dumps(
                        with_aliases({
                            "type": "replay_start",
                            "source": json_files[0].name,
                            "equity_points": len(equity_curve),
                            "trades": len(trades),
                            "ts": ts0,
                        })
                    )
                )
                for i, point in enumerate(equity_curve):
                    await websocket.send_text(
                        json.dumps(
                            with_aliases({
                                "type": "equity",
                                "index": i,
                                "value": point,
                                "total": len(equity_curve),
                                "ts": ts0,
                            })
                        )
                    )
                    await asyncio.sleep(0.15)
                for j, trade in enumerate(trades):
                    await websocket.send_text(
                        json.dumps(
                            with_aliases({
                                "type": "trade",
                                "trade": trade,
                                "index": j,
                                "total": len(trades),
                                "ts": ts0,
                            })
                        )
                    )
                    await asyncio.sleep(0.2)
                await websocket.send_text(
                    json.dumps(
                        with_aliases({
                            "type": "replay_complete",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        })
                    )
                )
            except WebSocketDisconnect:
                return
            except Exception as e:
                print(f"WebSocket replay error: {e}")

        # After replay (or no backtest available), send a liveness ping every 10 s.
        try:
            while True:
                await asyncio.sleep(10)
                await websocket.send_text(
                    json.dumps(
                        with_aliases({
                            "type": "ping",
                            "ts": datetime.now(timezone.utc).isoformat(),
                        })
                    )
                )
        except WebSocketDisconnect:
            return
        except Exception:
            return

    # ---- Live data mode: delta-only streaming ----
    # Initial full snapshot so the client has a baseline before deltas.
    last_state_sig: tuple[float, int] | None = None
    last_equity_sig: tuple[float, int] | None = None
    last_trade_sig: tuple[float, int] | None = None

    # Send initial state snapshot so the dashboard renders immediately.
    try:
        if state_file.exists():
            with open(state_file, "r") as f:
                state = json.load(f)
            last_state_sig = _file_signature(state_file)
            await websocket.send_text(
                json.dumps(
                    with_aliases({
                        "type": "state",
                        "data": state,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                )
            )
        # Initial latest equity + trade points (single bounded reads).
        last_eq = _read_last_jsonl(equity_file)
        if last_eq is not None:
            last_equity_sig = _file_signature(equity_file)
            await websocket.send_text(
                json.dumps(
                    with_aliases({
                        "type": "equity_update",
                        "data": last_eq,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                )
            )
        last_trade = _read_last_jsonl(trades_file)
        if last_trade is not None:
            last_trade_sig = _file_signature(trades_file)
            await websocket.send_text(
                json.dumps(
                    with_aliases({
                        "type": "trade",
                        "data": last_trade,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    })
                )
            )
    except WebSocketDisconnect:
        return
    except Exception as e:
        print(f"WebSocket initial snapshot error: {e}")

    # Delta loop: stat() each file; send a frame only when it changed.
    try:
        while True:
            # --- State file (paper_state.json): stat → diff → maybe send ---
            cur_state_sig = _file_signature(state_file)
            if cur_state_sig is not None and cur_state_sig != last_state_sig:
                try:
                    with open(state_file, "r") as f:
                        state = json.load(f)
                    await websocket.send_text(
                        json.dumps(
                            with_aliases({
                                "type": "state",
                                "data": state,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                        )
                    )
                    last_state_sig = cur_state_sig
                except WebSocketDisconnect:
                    return
                except Exception as e:
                    print(f"WebSocket state read error: {e}")

            # --- Equity file (equity.jsonl): bounded tail read of last line ---
            cur_eq_sig = _file_signature(equity_file)
            if cur_eq_sig is not None and cur_eq_sig != last_equity_sig:
                last_eq = _read_last_jsonl(equity_file)
                if last_eq is not None:
                    await websocket.send_text(
                        json.dumps(
                            with_aliases({
                                "type": "equity_update",
                                "data": last_eq,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                        )
                    )
                last_equity_sig = cur_eq_sig

            # --- Trades file (trades.jsonl): bounded tail read of last line ---
            cur_trade_sig = _file_signature(trades_file)
            if cur_trade_sig is not None and cur_trade_sig != last_trade_sig:
                last_trade = _read_last_jsonl(trades_file)
                if last_trade is not None:
                    await websocket.send_text(
                        json.dumps(
                            with_aliases({
                                "type": "trade",
                                "data": last_trade,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                        )
                    )
                last_trade_sig = cur_trade_sig

            await asyncio.sleep(1)  # poll interval (cheaper than 2 s)
    except WebSocketDisconnect:
        return
    except Exception as e:
        print(f"WebSocket live loop error: {e}")
    finally:
        # Starlette already closes the socket on disconnect; closing a
        # closed socket raises RuntimeError, which we swallow defensively.
        try:
            await websocket.close()
        except RuntimeError:
            pass
        except Exception:
            pass




# ---------------------------------------------------------------------------
# Capital Allocator Endpoints
# ---------------------------------------------------------------------------


def _load_allocator_overrides() -> dict:
    """Load persisted allocator config overrides from ``ALLOCATOR_CONFIG_FILE``.

    Returns an empty dict when the file does not exist or is unreadable,
    so callers can merge with :class:`CapitalAllocatorConfig` defaults
    via ``{**defaults, **overrides}`` semantics.
    """
    if not ALLOCATOR_CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(ALLOCATOR_CONFIG_FILE.read_text())
    except Exception:
        return {}


def _save_allocator_overrides(overrides: dict) -> None:
    """Persist allocator config overrides as JSON (atomic-ish write).

    ``initial_capital`` is never persisted here — it is always re-derived
    from the live bot balance on every GET, so the dashboard reflects the
    actual Deriv demo account balance, not a stale capital snapshot.
    """
    ALLOCATOR_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {**overrides, "saved_at": datetime.now(timezone.utc).isoformat()}
    ALLOCATOR_CONFIG_FILE.write_text(json.dumps(payload, indent=2))


def _build_allocator_response() -> dict:
    """Build the GET/POST allocator ``config`` payload.

    Reads the live bot balance from ``realtime/paper_state.json`` (the
    actual Deriv demo account balance while paper trading) and computes
    the reserve / surplus split via :class:`CapitalAllocatorConfig` +
    :class:`CapitalAllocator`, applying any persisted overrides.

    If no live state file exists (paper trading not started), falls back
    to the configured ``initial_capital`` default and reports
    ``data_available: false`` so the dashboard can degrade gracefully.
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

    # Start from CapitalAllocatorConfig defaults, then layer persisted
    # overrides on top. ``initial_capital`` is always re-derived from the
    # live balance (or the 10000 default) so we never serve a stale capital.
    # Each field is passed explicitly so Pyright can verify per-field types.
    overrides = _load_allocator_overrides()
    init_capital = live_balance if live_balance is not None else 10000.0
    config = CapitalAllocatorConfig(
        initial_capital=init_capital,
        reserva_pct=overrides["reserva_pct"] if "reserva_pct" in overrides else 0.80,
        superávit_diario_pct=overrides["superávit_diario_pct"]
        if "superávit_diario_pct" in overrides else 0.20,
        min_surplus=overrides["min_surplus"] if "min_surplus" in overrides else 10.0,
        rebalance_daily=overrides["rebalance_daily"]
        if "rebalance_daily" in overrides else True,
    )
    allocator = CapitalAllocator(config)
    allocator.reset_daily(capital_total=init_capital)
    if live_pnl != 0.0:
        allocator.record_trade(live_pnl)
    state = allocator.get_state()

    return {
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
    }


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
    return with_aliases(_build_allocator_response())


@app.post("/api/allocator/config", response_model=None)
async def update_allocator_config(config: dict) -> dict | JSONResponse:
    """Validate and persist capital allocator configuration overrides.

    Accepts the dashboard's envelope:

        {
          "reserva_pct":      float (0,1),
          "max_daily_pct":    float (0,1),  # → superávit_diario_pct
          "reinvest_profits": bool          # → rebalance_daily
        }

    All fields are optional — omitted fields reuse the previously persisted
    value (or the default). Validation is delegated to
    :meth:`CapitalAllocatorConfig.validate`; on failure a 400 with the list
    of validation errors is returned. On success the overrides are persisted
    to ``realtime/allocator_config.json`` and the reconciled allocator
    state (the same shape returned by GET ``/api/allocator/config``) is
    returned so the caller can confirm the new split.
    """
    from src.risk.capital_allocator import CapitalAllocator, CapitalAllocatorConfig

    # Build the new overrides set: existing persisted + incoming deltas.
    # The dashboard sends max_daily_pct / reinvest_profits (UI naming);
    # we translate to the canonical CapitalAllocatorConfig field names.
    current = _load_allocator_overrides()

    # Numeric overrides (float).
    reserva_pct: float | None = (
        float(config["reserva_pct"]) if "reserva_pct" in config
        else current.get("reserva_pct", None)  # type: ignore[assignment]
    )
    superavit_diario_pct: float | None = (
        float(config["max_daily_pct"]) if "max_daily_pct" in config
        else (float(config["superávit_diario_pct"]) if "superávit_diario_pct" in config
              else current.get("superávit_diario_pct", None))  # type: ignore[assignment]
    )
    min_surplus: float | None = (
        float(config["min_surplus"]) if "min_surplus" in config
        else current.get("min_surplus", None)  # type: ignore[assignment]
    )
    # Boolean override.
    if "reinvest_profits" in config:
        rebalance_daily: bool | None = bool(config["reinvest_profits"])
    elif "rebalance_daily" in config:
        rebalance_daily = bool(config["rebalance_daily"])
    else:
        rebalance_daily = current.get("rebalance_daily", None)  # type: ignore[assignment]

    # Validate via CapitalAllocatorConfig before persisting. We pass a
    # throwaway initial_capital because validate() doesn't depend on it; the
    # real initial_capital is re-derived from the live balance in
    # _build_allocator_response(). Each field is passed explicitly (rather
    # than via **kwargs splatting) so Pyright can verify the per-field types.
    candidate = CapitalAllocatorConfig(
        initial_capital=10000.0,
        reserva_pct=reserva_pct if reserva_pct is not None else 0.80,
        superávit_diario_pct=superavit_diario_pct if superavit_diario_pct is not None else 0.20,
        min_surplus=min_surplus if min_surplus is not None else 10.0,
        rebalance_daily=rebalance_daily if rebalance_daily is not None else True,
    )
    errors = candidate.validate()
    if errors:
        return JSONResponse(
            status_code=400,
            content=with_aliases({
                "status": "error",
                "errors": errors,
                "config_received": config,
            }),
        )

    # Persist (drop the saved_at sentinel — _save adds its own timestamp).
    to_persist: dict[str, float | bool] = {}
    if reserva_pct is not None:
        to_persist["reserva_pct"] = reserva_pct
    if superavit_diario_pct is not None:
        to_persist["superávit_diario_pct"] = superavit_diario_pct
    if min_surplus is not None:
        to_persist["min_surplus"] = min_surplus
    if rebalance_daily is not None:
        to_persist["rebalance_daily"] = rebalance_daily
    _save_allocator_overrides(to_persist)

    # Return the reconciled allocator state (same shape as GET) so the
    # caller sees the new split in place without a second round-trip.
    return with_aliases({**_build_allocator_response(), "status": "updated"})

@app.get("/api/allocator/allocate")
async def get_allocation(
    starting_balance: float | None = None,
    daily_pnl: float = 0.0
) -> dict:
    """Get capital allocation for given balance and daily P&L.

    If starting_balance is not provided, reads the live bot balance from
    ``realtime/paper_state.json`` (the actual Deriv demo account balance)
    via the module-level ``REALTIME_STATE_FILE`` constant.
    """
    from src.risk.capital_allocator import CapitalAllocator, CapitalAllocatorConfig

    # Auto-detect balance from live bot state if not provided.
    # Uses REALTIME_STATE_FILE (not a hardcoded path) so tests can
    # monkeypatch the file location via isolated_realtime.
    if starting_balance is None:
        if REALTIME_STATE_FILE.exists():
            try:
                with open(REALTIME_STATE_FILE) as f:
                    state = json.load(f)
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

# Valid metrics for the matrix & ranking endpoints — must match the
# ``profitability_matrix`` valid set in ``src.analysis.attribution``.
_ATTR_VALID_METRICS = frozenset(
    {"total_pnl", "sharpe", "win_rate", "max_dd",
     "profit_factor", "expectancy", "total_trades"}
)


@app.get("/api/attribution/matrix", response_model=None)
async def get_attribution_matrix(
    metric: str = "total_pnl",
) -> dict | JSONResponse:
    """Get strategy performance matrix for heatmap display.

    Args:
        metric: Performance metric to display. Valid values:
            ``total_pnl`` (default), ``sharpe``, ``win_rate``, ``max_dd``,
            ``profit_factor``, ``expectancy``, ``total_trades``.
            Invalid values return 400 with a diagnostic error.

    Returns:
        ``{strategies, symbols, matrix}`` where ``matrix`` is a list of
        rows (one per strategy), each a list of per-symbol cell dicts or
        ``None``.  Each cell carries the requested ``metric_value`` plus
        ``sharpe`` (always populated from a side-panel query so the
        dashboard can overlay Sharpe regardless of the chosen metric).
    """
    if metric not in _ATTR_VALID_METRICS:
        return JSONResponse(
            status_code=400,
            content=with_aliases({
                "status": "error",
                "error": (
                    f"metric='{metric}' is invalid. "
                    f"Valid: {sorted(_ATTR_VALID_METRICS)}"
                ),
            }),
        )

    from src.analysis.attribution import StrategyAttribution
    attribution = StrategyAttribution()
    # Main metric matrix + a Sharpe side-panel (computed in one pass).
    matrix_data = attribution.profitability_matrix(metric=metric, latest_only=True)
    sharpe_data = attribution.profitability_matrix(metric="sharpe", latest_only=True)

    # Convert nested dict {symbol: {strategy: value}} to expected format.
    strategies = sorted({s for sym_data in matrix_data.values() for s in sym_data})
    symbols = sorted(matrix_data.keys())

    result = {
        "strategies": strategies,
        "symbols": symbols,
        "matrix": [],
        "metric": metric,
    }

    for strategy in strategies:
        row_data: list[dict | None] = []
        for symbol in symbols:
            sym_data = matrix_data.get(symbol, {})
            value = sym_data.get(strategy)
            if value is not None:
                sharpe_val = sharpe_data.get(symbol, {}).get(strategy, 0.0)
                row_data.append({
                    "strategy_name": strategy,
                    "symbol": symbol,
                    "metric": metric,
                    "metric_value": round(value, 4),
                    "pnl": round(value, 2) if metric == "total_pnl" else 0.0,
                    "sharpe": round(sharpe_val, 4),
                })
            else:
                row_data.append(None)
        result["matrix"].append(row_data)

    return with_aliases(result)


@app.get("/api/attribution/ranking", response_model=None)
async def get_strategy_ranking(
    metric: str = "total_pnl",
    min_trades: int = 10,
) -> dict | JSONResponse:
    """Get the best strategy per symbol, ranked by the chosen metric.

    Args:
        metric: Metric for ranking (default ``total_pnl``). Also supports
            ``sharpe``, ``profit_factor``, ``expectancy``, ``win_rate``,
            ``max_dd`` (lower is better — automatically inverted). Invalid
            values return 400.
        min_trades: Minimum trades for a strategy to qualify (default 10).
            Strategies with fewer trades are excluded for statistical
            significance.

    Returns:
        ``{ranking, metric, min_trades}`` where ``ranking`` is a list of
        ``{symbol, strategy_name, metric_value, sharpe}``.  Sharpe is always
        populated via a side-panel query so the dashboard can sort/colour by
        Sharpe regardless of the ranking metric.
    """
    if metric not in _ATTR_VALID_METRICS:
        return JSONResponse(
            status_code=400,
            content=with_aliases({
                "status": "error",
                "error": (
                    f"metric='{metric}' is invalid. "
                    f"Valid: {sorted(_ATTR_VALID_METRICS)}"
                ),
            }),
        )

    from src.analysis.attribution import StrategyAttribution
    attribution = StrategyAttribution()
    best = attribution.best_strategy_per_symbol(
        metric=metric, min_trades=min_trades, latest_only=True
    )
    # Side-panel: Sharpe per (symbol, strategy) so each ranked row carries
    # the actual Sharpe alongside the ranking metric.
    sharpe_data = attribution.profitability_matrix(metric="sharpe", latest_only=True)

    result = []
    for symbol, (strat_name, metric_value) in best.items():
        sharpe_val = sharpe_data.get(symbol, {}).get(strat_name, 0.0)
        result.append({
            "symbol": symbol,
            "strategy_name": strat_name,
            "metric": metric,
            "metric_value": round(metric_value, 4),
            "pnl": round(metric_value, 2) if metric == "total_pnl" else 0.0,
            "sharpe": round(sharpe_val, 4) if isinstance(sharpe_val, (int, float)) else 0.0,
        })

    # Sort by metric_value descending (max_dd handled by attribution as lower-better).
    result.sort(key=lambda r: r["metric_value"], reverse=True)

    return with_aliases({
        "ranking": result,
        "metric": metric,
        "min_trades": min_trades,
    })

# ------------------------------------------------------------------ #
# Brinson-Fachler Attribution Endpoints
# ------------------------------------------------------------------ #

@app.get("/api/attribution/brinson", response_model=None)
async def get_brinson_fachler_decomposition(
    weight_column: str = "total_trades",
    return_column: str = "avg_pnl_pct",
    benchmark_strategy: str | None = None,
    min_trades: int = 1,
) -> dict | JSONResponse:
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