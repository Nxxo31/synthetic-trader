"""Deriv WebSocket client wrapper using python_deriv_api."""
from __future__ import annotations

import asyncio
import os
import logging
from dataclasses import dataclass
from typing import Any

import yaml
import websockets

logger = logging.getLogger(__name__)


@dataclass
class DerivConfig:
    """Connection configuration for Deriv API."""
    endpoint: str
    app_id: int
    api_token: str | None
    is_demo: bool

    @classmethod
    def from_yaml(cls, path: str = "config/deriv.yaml") -> "DerivConfig":
        with open(path) as f:
            cfg = yaml.safe_load(f)
        deriv = cfg["deriv"]
        token = os.environ.get("DERIV_API_TOKEN") or deriv.get("api_token")
        app_id = int(os.environ.get("DERIV_APP_ID") or deriv["app_id"])
        return cls(
            endpoint=deriv["endpoint"],
            app_id=app_id,
            api_token=token,
            is_demo=deriv.get("is_demo", True),
        )

    @property
    def ws_url(self) -> str:
        return f"{self.endpoint}?app_id={self.app_id}"


class DerivClient:
    """
    Async WebSocket client for Deriv API.

    Handles:
    - Connection with auto-reconnect
    - authorize (if token provided)
    - balance subscription
    - ticks subscription
    - ticks_history (historical data)
    - active_symbols
    - proposal + buy + sell (trading)
    """

    def __init__(self, config: DerivConfig):
        self.config = config
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connected = False
        self._authorized = False
        self._req_id = 0
        self._pending: dict[int, asyncio.Future[dict]] = {}

    async def connect(self) -> None:
        """Establish WebSocket connection to Deriv."""
        logger.info("Connecting to %s (app_id=%d, demo=%s)",
                     self.config.ws_url, self.config.app_id, self.config.is_demo)
        self._ws = await websockets.connect(self.config.ws_url, ping_interval=30)
        self._connected = True
        logger.info("Connected to Deriv WebSocket")

        if self.config.api_token:
            await self.authorize(self.config.api_token)

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False
        self._authorized = False
        logger.info("Disconnected from Deriv")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_authorized(self) -> bool:
        return self._authorized

    async def _send(self, request: dict) -> dict:
        """Send a request and wait for response."""
        if not self._ws:
            raise RuntimeError("Not connected. Call connect() first.")

        self._req_id += 1
        req_id = self._req_id
        request["req_id"] = req_id
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        import json
        await self._ws.send(json.dumps(request))
        logger.debug("Sent: %s", request)

        # Simple response loop — in production, use a dedicated reader task
        msg = await self._ws.recv()
        response = json.loads(msg)
        logger.debug("Recv: %s", {k: v for k, v in response.items() if k != "echo_req"})

        if response.get("error"):
            raise DerivAPIError(response["error"]["message"])

        return response

    async def authorize(self, token: str) -> dict:
        """Authorize session with API token."""
        response = await self._send({"authorize": token})
        self._authorized = True
        logger.info("Authorized. Account: %s, Balance: %s %s",
                     response.get("loginid", "?"),
                     response.get("balance", "?"),
                     response.get("currency", "?"))
        return response

    async def balance(self) -> dict:
        """Get account balance."""
        return await self._send({"balance": 1, "subscribe": 1})

    async def active_symbols(self) -> dict:
        """Get list of active trading symbols."""
        return await self._send({"active_symbols": "full"})

    async def ticks_history(
        self,
        symbol: str,
        count: int = 1000,
        style: str = "candles",
        granularity: int = 60,
        end: str = "latest",
    ) -> dict:
        """
        Fetch historical data.

        Args:
            symbol: e.g. "R_100" or "RDBR100"
            count: number of ticks/candles (max 10000)
            style: "ticks" or "candles"
            granularity: seconds per candle (only for style="candles")
            end: "latest" or Unix timestamp
        """
        req: dict[str, Any] = {
            "ticks_history": symbol,
            "count": count,
            "style": style,
            "end": end,
        }
        if style == "candles":
            req["granularity"] = granularity
        return await self._send(req)

    async def proposal(self, params: dict) -> dict:
        """Get price proposal for a contract."""
        req = {"proposal": 1}
        req.update(params)
        return await self._send(req)

    async def buy(self, proposal_id: str, price: float) -> dict:
        """Buy a contract."""
        return await self._send({"buy": proposal_id, "price": price})

    async def sell(self, contract_id: str, price: float = 0) -> dict:
        """Sell an open contract."""
        return await self._send({"sell": contract_id, "price": price})

    async def portfolio(self) -> dict:
        """Get open contracts."""
        return await self._send({"portfolio": 1})

    async def profit_table(self, limit: int = 50) -> dict:
        """Get profit history."""
        return await self._send({"profit_table": 1, "limit": limit, "sort": "DESC"})


class DerivAPIError(Exception):
    """Deriv API error."""
    pass
