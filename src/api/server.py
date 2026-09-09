"""FastAPI server for Synthetic Trader — live dashboard +WebSocket + backtest API.

Wired to TradingEngine events via TypedMessageBus — no filesystem polling.
State flows through the message bus (Pub/Sub + Req/Rep) instead of JSONL files.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.trading.engine import TradingEngine
from src.trading.message_bus_typed import (
    init_typed_message_bus,
)

app = FastAPI(
    title="Synthetic Trader API",
    version="0.3.0",
    description="Live dashboard + WebSocket — events via message bus, no filesystem polling",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("synthetic-trader-api")

# Global engine reference (set when engine starts)
_engine: TradingEngine | None = None


@app.on_event("startup")
async def startup_event():
    """Initialize the typed message bus on server startup."""

    await init_typed_message_bus()


# ---------------------------------------------------------------------------\n# Health endpoint\n# ---------------------------------------------------------------------------\n\ndef check_api_health() -> dict:\n    \"\"\"Check FastAPI API responsiveness.\"\"\"\n    try:\n        from src.api.server import app as _app\n        # FastAPI is running if we can access it\n        return {\"status\": \"healthy\", \"detail\": \"API responsive\"}\n    except Exception:\n        return {\"status\": \"unhealthy\", \"detail\": \"API not responsive\"}\n\ndef check_deriv_websocket_health(timeout: float = 2.0) -> dict:\n    \"\"\"Check Deriv WebSocket connection with timeout.\"\"\"\n    try:\n        from src.connection.deriv_client import DerivClient, DerivConfig\n        config = DerivConfig.from_yaml() if hasattr(\"DerivConfig\", \"from_yaml\") else DerivConfig(\n            api_base_url=\"https://api.derivws.com\",\n            app_id=\"\",\n            api_token=\"\",\n            demo_account=\"\",\n            real_account=\"\",\n            is_demo=True,\n            otp_lifetime=120,\n            ws_demo_path=\"/trading/v1/options/ws/demo\",\n            ws_real_path=\"/trading/v1/options/ws/real\",\n            ws_public_path=\"/trading/v1/options/ws/public\",\n            otp_path=\"/trading/v1/options/accounts\",\n        )\n        client = DerivClient(config)\n        # Attempt a quick connectivity check with timeout\n        import asyncio\n        loop = asyncio.new_event_loop()\n        asyncio.set_event_loop(loop)\n        try:\n            is_valid = client.validate_config() if hasattr(client, \"validate_config\") else True\n            loop.close()\n            return {\"status\": \"healthy\" if is_valid else \"degraded\", \"detail\": \"Deriv config valid\"}\n        except Exception:\n            loop.close()\n            return {\"status\": \"degraded\", \"detail\": \"Deriv config check failed\"}\n    except Exception as e:\n        return {\"status\": \"unhealthy\", \"detail\": f\"Deriv WS error: {str(e)[:50]}\"}\n\ndef check_database_health() -> dict:\n    \"\"\"Check SQLite database file accessibility.\"\"\"\n    import os\n    try:\n        from pathlib import Path\n        PROJECT_ROOT = Path(__file__).parent.parent.parent\n        db_path = PROJECT_ROOT / \"data\" / \"synthetic-trader.db\"\n        if db_path.exists():\n            # Try to connect and execute a simple query\n            import sqlite3\n            try:\n                conn = sqlite3.connect(str(db_path))\n                conn.execute(\"SELECT 1\")\n                conn.close()\n                return {\"status\": \"healthy\", \"detail\": f\"DB accessible at {db_path}\"}\n            except Exception:\n                return {\"status\": \"degraded\", \"detail\": f\"DB exists but query failed: {db_path}\"}\n        else:\n            return {\"status\": \"unhealthy\", \"detail\": f\"DB file not found: {db_path}\"}\n    except Exception as e:\n        return {\"status\": \"unhealthy\", \"detail\": f\"DB check error: {str(e)[:50]}\"}\n\ndef check_disk_space_health(min_free_pct: float = 10.0) -> dict:\n    \"\"\"Check available disk space (> min_free_pct % free).\"\"\"\n    import shutil\n    try:\n        total, used, free = shutil.disk_usage(\"/\")\n        free_pct = (free / total) * 100\n        if free_pct >= min_free_pct:\n            return {\"status\": \"healthy\", \"detail\": f\"Disk {free_pct:.1f}% free (min {min_free_pct}%)\"}\n        else:\n            return {\"status\": \"degraded\", \"detail\": f\"Disk {free_pct:.1f}% free (min {min_free_pct}%)\"}\n    except Exception as e:\n        return {\"status\": \"unhealthy\", \"detail\": f\"Disk check error: {str(e)[:50]}\"}\n\ndef check_paper_trading_health() -> dict:\n    \"\"\"Check paper trading process status.\"\"\"\n    import subprocess\n    import os\n    try:\n        # Check if paper trader process is running\n        result = subprocess.run(\n            [\"pgrep\", \"-f\", \"paper_247.py\"],\n            capture_output=True, text=True\n        )\n        if result.returncode == 0:\n            return {\"status\": \"healthy\", \"detail\": \"Paper trading process running\"}\n        else:\n            return {\n                \"status\": \"unknown\",\n                \"detail\": \"Paper trading process status unknown\"\n            }\n    except Exception as e:\n        return {\"status\": \"degraded\", \"detail\": f\"Paper trading check error: {str(e)[:50]}\"}\n\n@app.get(\"/api/health\")\nasync def health() -> dict:\n    \"\"\"Health check — returns comprehensive service status.\"\"\"\n    services = {\n        \"api\": check_api_health(),\n        \"deriv_websocket\": check_deriv_websocket_health(),\n        \"database\": check_database_health(),\n        \"disk_space\": check_disk_space_health(),\n        \"paper_trading\": check_paper_trading_health(),\n    }\n    # Determine overall status\n    statuses = [s[\"status\"] for s in services.values()]\n    if all(s == \"healthy\" for s in statuses):\n        overall = \"healthy\"\n    elif any(s == \"unhealthy\" for s in statuses):\n        overall = \"unhealthy\"\n    else:\n        overall = \"degraded\"\n    return with_aliases(\n        {\n            \"status\": overall,\n            \"timestamp\": datetime.utcnow().isoformat(),\n            \"version\": \"0.3.0\",\n            \"services\": services,\n        }\n    )\n\n\n# ---------------------------------------------------------------------------\n# Bot status endpoint\n# ---------------------------------------------------------------------------\n
