"""Typed Message Bus — Type-safe event-driven communication for trading engine.

Based on NautilusTrader's MessageBus with:
- Typed topics with schema validation
- Pub/Sub for event streaming
- Req/Rep for synchronous operations (order submission)
- Correlation ID tracking
- Backpressure handling
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Type variables for typed topics
T = TypeVar("T", bound=BaseModel)
R = TypeVar("R", bound=BaseModel)


class EventType(str, Enum):
    """Core event types in the trading system."""

    # Market data
    TICK_RECEIVED = "tick_received"
    CANDLE_CLOSED = "candle_closed"

    # Signal & decision
    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_SCORED = "signal_scored"

    # Order flow
    ORDER_SUBMITTED = "order_submitted"
    ORDER_VALIDATED = "order_validated"
    ORDER_REJECTED = "order_rejected"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"

    # Position lifecycle
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    POSITION_UPDATED = "position_updated"

    # Risk
    RISK_BREACH = "risk_breach"
    CIRCUIT_BREAKER_TRIGGERED = "circuit_breaker_triggered"
    DAILY_DRAWDOWN_LIMIT = "daily_drawdown_limit"

    # System
    ENGINE_STARTED = "engine_started"
    ENGINE_STOPPED = "engine_stopped"
    RECONNECT_ATTEMPT = "reconnect_attempt"
    ERROR = "error"


# Typed payload models for each event type
class TickPayload(BaseModel):
    symbol: str
    price: float
    timestamp: int  # ms since epoch
    volume: float | None = None


class CandlePayload(BaseModel):
    symbol: str
    timeframe: int  # seconds
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int  # candle start time
    is_closed: bool = True


class SignalPayload(BaseModel):
    controller_id: str
    symbol: str
    direction: str  # "CALL" or "PUT" / "BUY" or "SELL"
    confidence: float  # 0-1
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    duration: int | None = None  # seconds
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderPayload(BaseModel):
    executor_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    stake: float
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    order_type: str = "market"  # market, limit, stop
    duration: int | None = None
    correlation_id: str


class FillPayload(BaseModel):
    executor_id: str
    order_id: str
    symbol: str
    side: str
    fill_price: float
    fill_size: float
    commission: float = 0.0
    timestamp: int
    correlation_id: str


class PositionPayload(BaseModel):
    executor_id: str
    symbol: str
    side: str
    size: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None


class RiskPayload(BaseModel):
    check_name: str
    severity: str  # "warning" | "critical"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    current_value: float | None = None
    limit_value: float | None = None


# Event envelope with typed payload
@dataclass
class Event(Generic[T]):
    """Standardized event envelope with typed payload."""

    type: EventType
    payload: T
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: str = field(default_factory=lambda: str(uuid4()))
    source: str = ""
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "correlation_id": self.correlation_id,
            "payload": self.payload.model_dump() if isinstance(self.payload, BaseModel) else self.payload,
        }


# Req/Rep for synchronous operations
@dataclass
class Request(Generic[T]):
    """Request message for Req/Rep pattern."""

    method: str
    payload: T
    request_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    timeout: float = 30.0  # seconds


@dataclass
class Response(Generic[R]):
    """Response message for Req/Rep pattern."""

    request_id: str
    success: bool
    payload: R | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# Type aliases
EventHandler = Callable[[Event], Any]
RequestHandler = Callable[[Request], Any]


class TopicRouter:
    """Routes events by topic pattern (supports wildcards)."""

    def __init__(self):
        self._exact_routes: dict[str, list[EventHandler]] = defaultdict(list)
        self._pattern_routes: list[tuple[str, EventHandler]] = []

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Subscribe to a topic (supports wildcards like 'tick.*')."""
        if "*" in topic or "?" in topic:
            self._pattern_routes.append((topic, handler))
        else:
            self._exact_routes[topic].append(handler)

    def unsubscribe(self, topic: str, handler: EventHandler) -> bool:
        """Unsubscribe from a topic."""
        if "*" in topic or "?" in topic:
            try:
                self._pattern_routes.remove((topic, handler))
                return True
            except ValueError:
                return False
        else:
            try:
                self._exact_routes[topic].remove(handler)
                return True
            except (KeyError, ValueError):
                return False

    def get_handlers(self, topic: str) -> list[EventHandler]:
        """Get all handlers matching a topic."""
        handlers = self._exact_routes.get(topic, [])
        # Check pattern routes
        import fnmatch

        for pattern, handler in self._pattern_routes:
            if fnmatch.fnmatch(topic, pattern):
                handlers.append(handler)
        return handlers


class TypedMessageBus:
    """Type-safe message bus with Pub/Sub and Req/Rep support."""

    def __init__(self, max_queue_size: int = 10000):
        self._router = TopicRouter()
        self._queues: dict[str, asyncio.Queue] = defaultdict(
            lambda: asyncio.Queue(maxsize=max_queue_size)
        )
        self._wildcard_handlers: list[EventHandler] = []
        self._request_handlers: dict[str, RequestHandler] = {}
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._running = False
        self._dispatch_tasks: dict[str, asyncio.Task] = {}
        self._request_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the dispatch loops."""
        if self._running:
            return
        self._running = True
        # Start dispatch loops for each topic that has subscribers
        # We'll create tasks dynamically as needed
        self._request_task = asyncio.create_task(
            self._request_dispatch_loop(), name="request_dispatch"
        )
        logger.info("TypedMessageBus started")

    async def stop(self) -> None:
        """Stop all dispatch loops."""
        self._running = False
        for task in self._dispatch_tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._request_task:
            self._request_task.cancel()
            try:
                await self._request_task
            except asyncio.CancelledError:
                pass
        logger.info("TypedMessageBus stopped")

    # ===================== Pub/Sub =====================

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Subscribe to a topic."""
        self._router.subscribe(topic, handler)
        logger.debug("Subscribed %s to %s", handler.__name__, topic)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to all events."""
        self._wildcard_handlers.append(handler)

    def unsubscribe(self, topic: str, handler: EventHandler) -> bool:
        """Unsubscribe from a topic."""
        return self._router.unsubscribe(topic, handler)

    # ===================== Req/Rep =====================

    def register_request_handler(self, method: str, handler: RequestHandler) -> None:
        """Register a handler for a request method."""
        self._request_handlers[method] = handler
        logger.debug("Registered request handler for %s", method)

    async def request(self, method: str, payload: BaseModel, timeout: float = 30.0) -> Response:
        """Send a request and wait for response."""
        req = Request(method=method, payload=payload, timeout=timeout)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_requests[req.request_id] = future

        # Publish request internally
        await self._handle_request(req)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending_requests.pop(req.request_id, None)
            return Response(
                request_id=req.request_id,
                success=False,
                error=f"Request timeout after {timeout}s",
            )

    async def _handle_request(self, req: Request) -> None:
        """Handle a request internally."""
        handler = self._request_handlers.get(req.method)
        if not handler:
            self._complete_request(
                req.request_id,
                Response(request_id=req.request_id, success=False, error=f"No handler for {req.method}"),
            )
            return

        try:
            if asyncio.iscoroutinefunction(handler):
                response = await handler(req)
            else:
                response = handler(req)
            self._complete_request(req.request_id, response)
        except Exception as e:
            logger.exception("Request handler failed for %s: %s", req.method, e)
            self._complete_request(
                req.request_id,
                Response(request_id=req.request_id, success=False, error=str(e)),
            )

    def _complete_request(self, request_id: str, response: Response) -> None:
            """Complete a pending request."""
            future = self._pending_requests.pop(request_id, None)
            if future and not future.done():
                future.set_result(response)

    async def _request_dispatch_loop(self) -> None:
        """Dispatch loop for requests (not used - requests handled inline)."""
        while self._running:
            await asyncio.sleep(1)  # Keep alive

    # ===================== Internal Dispatch =====================

    async def _start_dispatch_for_topic(self, topic: str) -> None:
        """Start dispatch loop for a topic if not already running."""
        if topic in self._dispatch_tasks:
            return

        async def dispatch_loop():
            queue = self._queues[topic]
            while self._running:
                try:
                    event = await queue.get()
                    # Call specific subscribers
                    handlers = self._router.get_handlers(topic)
                    for handler in handlers:
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(event)
                            else:
                                handler(event)
                        except Exception as e:
                            logger.exception(
                                "Handler %s failed for event %s: %s",
                                handler.__name__,
                                event.event_id,
                                e,
                            )
                    # Call wildcard subscribers
                    for handler in self._wildcard_handlers:
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(event)
                            else:
                                handler(event)
                        except Exception as e:
                            logger.exception(
                                "Wildcard handler %s failed for event %s: %s",
                                handler.__name__,
                                event.event_id,
                                e,
                            )
                    queue.task_done()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception("Dispatch loop error for %s: %s", topic, e)

        self._dispatch_tasks[topic] = asyncio.create_task(
            dispatch_loop(), name=f"dispatch_{topic.replace('.', '_')}"
        )

    async def publish(self, topic: str, event: Event) -> None:
        """Publish an event to a topic (starts dispatch if needed)."""
        await self._start_dispatch_for_topic(topic)
        queue = self._queues[topic]
        try:
            await queue.put(event)
        except asyncio.QueueFull:
            logger.warning(
                "Queue full for topic %s, dropping event %s",
                topic,
                event.event_id,
            )

    def get_queue_size(self, topic: str) -> int:
        """Get current queue size for monitoring."""
        return self._queues[topic].qsize()


# ===================== Global Instance =====================

_global_typed_bus: TypedMessageBus | None = None


def get_typed_message_bus() -> TypedMessageBus:
    """Get or create the global typed message bus instance."""
    global _global_typed_bus
    if _global_typed_bus is None:
        _global_typed_bus = TypedMessageBus()
    return _global_typed_bus


async def init_typed_message_bus() -> TypedMessageBus:
    """Initialize and start the global typed message bus."""
    bus = get_typed_message_bus()
    await bus.start()
    return bus


async def shutdown_typed_message_bus() -> None:
    """Shutdown the global typed message bus."""
    global _global_typed_bus
    if _global_typed_bus:
        await _global_typed_bus.stop()
        _global_typed_bus = None


# ===================== Convenience Topics =====================

class Topics:
    """Standard topic names for the trading system."""

    # Market data: "market.tick.{symbol}", "market.candle.{symbol}.{timeframe}"
    @staticmethod
    def tick(symbol: str) -> str:
        return f"market.tick.{symbol}"

    @staticmethod
    def candle(symbol: str, timeframe: int) -> str:
        return f"market.candle.{symbol}.{timeframe}"

    # Signals: "signal.{controller_id}.{symbol}"
    @staticmethod
    def signal(controller_id: str, symbol: str) -> str:
        return f"signal.{controller_id}.{symbol}"

    # Orders: "order.{executor_id}.{symbol}"
    @staticmethod
    def order(executor_id: str, symbol: str) -> str:
        return f"order.{executor_id}.{symbol}"

    # Fills: "fill.{executor_id}.{symbol}"
    @staticmethod
    def fill(executor_id: str, symbol: str) -> str:
        return f"fill.{executor_id}.{symbol}"

    # Positions: "position.{executor_id}.{symbol}"
    @staticmethod
    def position(executor_id: str, symbol: str) -> str:
        return f"position.{executor_id}.{symbol}"

    # Risk: "risk.{check_name}"
    @staticmethod
    def risk(check_name: str) -> str:
        return f"risk.{check_name}"

    # System: "system.{event}"
    @staticmethod
    def system(event: str) -> str:
        return f"system.{event}"

    # Request methods
    ORDER_SUBMIT = "order.submit"
    ORDER_CANCEL = "order.cancel"
    POSITION_CLOSE = "position.close"
    ACCOUNT_INFO = "account.info"


if __name__ == "__main__":
    # Test the typed message bus
    async def test():
        bus = TypedMessageBus()

        # Test pub/sub
        received = []

        async def handler(event: Event):
            received.append(event)
            print(f"Received: {event.type} - {event.payload}")

        bus.subscribe("market.tick.R_50", handler)
        await bus.start()

        # Publish typed event
        tick = TickPayload(symbol="R_50", price=123.45, timestamp=1234567890)
        await bus.publish(Topics.tick("R_50"), Event(type=EventType.TICK_RECEIVED, payload=tick))

        await asyncio.sleep(0.1)
        print(f"Total received: {len(received)}")

        # Test req/rep
        async def order_handler(req: Request) -> Response:
            return Response(request_id=req.request_id, success=True, payload={"order_id": "12345"})

        bus.register_request_handler(Topics.ORDER_SUBMIT, order_handler)

        order = OrderPayload(
            executor_id="test",
            symbol="R_50",
            side="BUY",
            stake=1.0,
            correlation_id="corr-123",
        )
        response = await bus.request(Topics.ORDER_SUBMIT, order)
        print(f"Request response: {response}")

        await bus.stop()

    asyncio.run(test())
