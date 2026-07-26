from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import time
import json
import os
from datetime import datetime
from src.connection.deriv_client import DerivClient  # Import for future use

app = FastAPI(title="Synthetic Trader API", version="0.1.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/bots")
async def get_bots():
    # Dummy endpoint - return empty list for now
    return []

@app.get("/api/bots/{bot_id}/status")
async def get_bot_status(bot_id: str):
    # Dummy endpoint - return mock bot status
    return {
        "bot_id": bot_id,
        "balance": 1000.0,
        "pnl": 0.0,
        "trades_today": 0,
        "is_halted": False,
        "last_update": datetime.utcnow().isoformat()
    }

@app.get("/api/backtest/results")
async def get_backtest_results():
    # List reports from /reports/backtest/
    reports_dir = "/home/sebas/proyectos/synthetic-trader/reports/backtest"
    if not os.path.exists(reports_dir):
        return []
    
    results = []
    for filename in os.listdir(reports_dir):
        if filename.endswith(".json"):
            try:
                with open(os.path.join(reports_dir, filename), 'r') as f:
                    data = json.load(f)
                    results.append({
                        "filename": filename,
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
                        "trades": data.get("trades", []),
                        "equity_curve": data.get("equity_curve", []),
                        "initial_capital": data.get("initial_capital", 10000.0),
                    })
            except Exception:
                continue
    
    results.sort(key=lambda x: x.get("total_pnl", 0), reverse=True)
    return results

@app.websocket("/ws/live-data")
async def websocket_live_data(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Send a ping every 5 seconds
            await websocket.send_text(json.dumps({
                "type": "ping",
                "timestamp": datetime.utcnow().isoformat()
            }))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await websocket.close()

# Import asyncio for the websocket sleep
import asyncio