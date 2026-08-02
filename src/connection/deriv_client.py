"""Deriv API client — new REST + WebSocket hybrid API (developers.deriv.com).

Authentication flow:
    1. REST POST /trading/v1/options/accounts/{accountId}/otp
       Headers: Deriv-App-ID, Authorization: Bearer {PAT}
       → Returns WebSocket URL with OTP query param
    2. Connect WebSocket to that URL (OTP valid 120s, single use)
    3. Send JSON messages: ticks, proposal, buy, sell, etc.

For read-only market data, use the public WebSocket (no auth needed):
    wss://api.derivws.com/trading/v1/options/ws/public
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml
import websockets

logger = logging.getLogger(__name__)


@dataclass
class DerivConfig:
    """Connection configuration for the new Deriv API."""
    api_base_url: str          # https://api.derivws.com
    app_id: str                # alphanumeric PAT app ID
    api_token: str             # PAT token (pat_...)
    demo_account: str          # DOT...
    real_account: str          # ROT...
    is_demo: bool
    otp_lifetime: int          # seconds (120 default)
    ws_demo_path: str           # /trading/v1/options/ws/demo
    ws_real_path: str           # /trading/v1/options/ws/real
    ws_public_path: str        # /trading/v1/options/ws/public
    otp_path: str              # /trading/v1/options/accounts

    @classmethod
    def from_yaml(cls, path: str = "config/deriv.yaml") -> "DerivConfig":
        # Load project .env (overrides any global .env from Hermes shell)
        try:
            from dotenv import load_dotenv
            env_path = Path(__file__).resolve().parent.parent.parent / ".env"
            load_dotenv(env_path, override=True)
        except ImportError:
            pass  # python-dotenv optional; env vars may be set externally

        with open(path) as f:
            cfg = yaml.safe_load(f)
        d = cfg["deriv"]
        token = os.environ.get("DERIV_API_TOKEN")
        if not token:
            raise ValueError("DERIV_API_TOKEN not set in .env or environment")
        app_id = os.environ.get("DERIV_APP_ID") or d["app_id"]
        return cls(
            api_base_url=d["api_base_url"],
            app_id=app_id,
            api_token=token,
            demo_account=d["demo_account"],
            real_account=d["real_account"],
            is_demo=d.get("is_demo", True),
            otp_lifetime=d.get("otp_lifetime", 120),
            ws_demo_path=d["ws_demo_path"],
            ws_real_path=d["ws_real_path"],
            ws_public_path=d["ws_public_path"],
            otp_path=d["otp_path"],
        )

    @property
    def account_id(self) -> str:
        """Active account ID based on demo/real mode."""
        return self.demo_account if self.is_demo else self.real_account

    @property
    def ws_base_url(self) -> str:
        """WebSocket base URL (api.derivws.com uses wss)."""
        return self.api_base_url.replace("https://", "wss://")

    @property
    def public_ws_url(self) -> str:
        """Public WebSocket URL (no auth, market data only)."""
        return f"{self.ws_base_url}{self.ws_public_path}"


class DerivClient:
    """
    Async client for the new Deriv API (REST + WebSocket hybrid).

    Flow:
        1. connect() → requests OTP via REST → opens WebSocket
        2. subscribe to ticks, balance, etc.
        3. trading: proposal → buy → sell

    The OTP is single-use and valid for 120 seconds. The client handles
    reconnection by requesting a new OTP if the WebSocket drops.
    """

    def __init__(self, config: DerivConfig):
        self.config = config
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connected = False
        self._authorized = False
        self._req_id = 0
        self._otp_url: str | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._subscriptions: dict[str, asyncio.Future[dict]] = {}

    # ── REST: OTP flow ──────────────────────────────────────────────

    def _rest_headers(self) -> dict[str, str]:
        """Headers for REST API calls."""
        return {
            "Deriv-App-ID": self.config.app_id,
            "Authorization": f"Bearer {self.config.api_token}",
            "Content-Type": "application/json",
        }

    def _request_otp(self) -> str:
        """
        Request OTP from REST API. Returns full WebSocket URL with OTP.

        OTP is valid for 120 seconds and single-use only.
        """
        url = f"{self.config.api_base_url}{self.config.otp_path}/{self.config.account_id}/otp"
        logger.info("Requesting OTP for account %s...", self.config.account_id)

        r = requests.post(url, headers=self._rest_headers(), timeout=10)

        if r.status_code != 200:
            raise DerivAPIError(f"OTP request failed: {r.status_code} {r.text}")

        data = r.json()
        ws_url = data.get("data", {}).get("url")
        if not ws_url:
            raise DerivAPIError(f"No WebSocket URL in OTP response: {data}")

        logger.info("OTP obtained, WebSocket URL ready (valid %ds)", self.config.otp_lifetime)
        return ws_url

    def get_accounts(self) -> list[dict]:
        """
        List all trading accounts (REST GET).

        Returns list of {account_id, balance, currency, group, status, account_type}.
        """
        url = f"{self.config.api_base_url}{self.config.otp_path}"
        r = requests.get(url, headers=self._rest_headers(), timeout=10)

        if r.status_code != 200:
            raise DerivAPIError(f"Accounts request failed: {r.status_code} {r.text}")

        return r.json().get("data", [])

    def reset_demo_balance(self) -> bool:
        """Reset demo account balance to $10,000 (REST POST)."""
        url = f"{self.config.api_base_url}{self.config.otp_path}/{self.config.demo_account}/reset-demo-balance"
        r = requests.post(url, headers=self._rest_headers(), timeout=10)
        return r.status_code == 200

    # ── WebSocket connection ────────────────────────────────────────

    async def connect(self) -> None:
        """
        Full connection flow:
            1. Request OTP via REST
            2. Connect WebSocket to OTP URL
            3. Start background reader task
        """
        self._otp_url = self._request_otp()

        logger.info("Connecting to WebSocket...")
        self._ws = await websockets.connect(self._otp_url, ping_interval=30)
        self._connected = True
        self._authorized = True  # OTP auth is implicit in the URL

        # Start background reader for subscription messages
        self._reader_task = asyncio.create_task(self._reader_loop())

        logger.info("Connected to Deriv WebSocket (account: %s, demo: %s)",
                     self.config.account_id, self.config.is_demo)

    async def connect_public(self) -> None:
        """
        Connect to public WebSocket (no auth, market data only).
        Use for read-only data: ticks, candles, active symbols.
        """
        logger.info("Connecting to public WebSocket (no auth)...")
        self._ws = await websockets.connect(self.config.public_ws_url, ping_interval=30)
        self._connected = True
        self._authorized = False

        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info("Connected to public Deriv WebSocket")

    async def disconnect(self) -> None:
        """Close WebSocket connection and cleanup."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._connected = False
        self._authorized = False
        self._pending.clear()
        self._subscriptions.clear()
        logger.info("Disconnected from Deriv")

    async def reconnect(self, max_attempts: int = 5) -> bool:
        """Reconnect with exponential backoff (1,2,4,8,...,max 60s).

        Requests a fresh OTP (the previous one is single-use and expired)
        and opens a new WebSocket.  This preserves the same DerivClient
        instance so callers (e.g. paper_runner) keep their reference.

        Returns True on success, False after ``max_attempts`` consecutive
        failures — caller should then halt cleanly and alert the dashboard.
        """
        # Tear down any half-open state first (idempotent).
        try:
            await self.disconnect()
        except Exception as e:
            logger.debug("disconnect during reconnect raised: %s", e)

        delay = 1.0
        max_delay = 60.0
        for attempt in range(1, max_attempts + 1):
            logger.warning(
                "Reconnect attempt %d/%d (backoff %.0fs)...",
                attempt, max_attempts, delay,
            )
            try:
                # connect() raises on OTP/WS failure; on success we're live.
                await self.connect()
                logger.info("Reconnected to Deriv on attempt %d", attempt)
                return True
            except Exception as e:
                logger.error("Reconnect attempt %d failed: %s", attempt, e)
                if attempt < max_attempts:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2.0, max_delay)

        logger.critical(
            "Reconnect failed after %d attempts — giving up", max_attempts,
        )
        return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_authorized(self) -> bool:
        return self._authorized

    # ── WebSocket message handling ─────────────────────────────────

    async def _reader_loop(self) -> None:
        """Background task that reads WebSocket messages and resolves futures."""
        while self._connected and self._ws:
            try:
                msg = await self._ws.recv()
                response = json.loads(msg)
                req_id = response.get("req_id")

                # Resolve pending request future
                if req_id and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if not future.done():
                        if response.get("error"):
                            future.set_exception(
                                DerivAPIError(response["error"]["message"])
                            )
                        else:
                            future.set_result(response)
                else:
                    # Subscription message — log it
                    msg_type = response.get("msg_type", "unknown")
                    logger.debug("Subscription message: %s", msg_type)

            except websockets.ConnectionClosed:
                logger.warning("WebSocket connection closed")
                self._connected = False
                break
            except Exception as e:
                logger.error("Reader loop error: %s", e)
                break

    async def _send(self, request: dict) -> dict:
        """Send a request and wait for response (matched by req_id)."""
        if not self._ws or not self._connected:
            raise RuntimeError("Not connected. Call connect() first.")

        self._req_id += 1
        req_id = self._req_id
        request["req_id"] = req_id

        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._ws.send(json.dumps(request))
        logger.debug("Sent: %s", {k: v for k, v in request.items() if k != "authorize"})

        # Wait with timeout
        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise DerivAPIError(f"Request timed out (req_id={req_id})")

    # ── API methods (same interface as before, new transport) ───────

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

    async def subscribe_ticks(self, symbol: str) -> dict:
        """Subscribe to live ticks for a symbol."""
        return await self._send({"ticks": symbol, "subscribe": 1})

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

    async def ping(self) -> dict:
        """Send ping to keep connection alive."""
        return await self._send({"ping": 1})


class DerivAPIError(Exception):
    """Deriv API error."""
    pass
