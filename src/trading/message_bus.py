"""Message Bus — event-driven communication backbone for trading engine.

Provides a simple asyncio-based pub/sub system for decoupling:
- Controllers (signal generation)
- Executors (trade execution)
- RiskEngine (validation)
- Portfolio (aggregation)
- Dashboard (streaming)
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


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


@dataclass
class Event:
    """Standardized event envelope."""

    type: EventType
    payload: dict[str, Any]
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
            "payload": self.payload,
        }


# Type alias for event handlers
EventHandler = Callable[[Event], Any]


class MessageBus:
    """Async pub/sub message bus using asyncio.Queue for backpressure."""

    def __init__(self, max_queue_size: int = 10000):
        self._queues: dict[EventType, asyncio.Queue] = defaultdict(
            lambda: asyncio.Queue(maxsize=max_queue_size)
        )
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._wildcard_subscribers: list[EventHandler] = []
        self._running = False
        self._dispatch_tasks: dict[EventType, asyncio.Task] = {}

    async def start(self) -> None:
        """Start the dispatch loop for all event types."""
        if self._running:
            return
        self._running = True
        for event_type in EventType:
            self._dispatch_tasks[event_type] = asyncio.create_task(
                self._dispatch_loop(event_type), name=f"dispatch_{event_type.value}"
            )
        logger.info("MessageBus started")

    async def stop(self) -> None:
        """Stop the dispatch loop."""
        self._running = False
        for task in self._dispatch_tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("MessageBus stopped")

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Subscribe a handler to a specific event type."""
        self._subscribers[event_type].append(handler)
        logger.debug("Subscribed %s to %s", handler.__name__, event_type.value)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe a handler to ALL events (wildcard)."""
        self._wildcard_subscribers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> bool:
        """Unsubscribe a handler. Returns True if removed."""
        try:
            self._subscribers[event_type].remove(handler)
            return True
        except ValueError:
            return False

    async def publish(self, event: Event) -> None:
        """Publish an event to the queue (non-blocking if queue not full)."""
        try:
            await self._queues[event.type].put(event)
        except asyncio.QueueFull:
            logger.warning(
                "Queue full for event type %s, dropping event %s",
                event.type.value,
                event.event_id,
            )

    async def publish_sync(self, event: Event) -> None:
        """Publish and wait for all handlers to complete (for critical events)."""
        await self.publish(event)
        # Wait a tick for handlers to process
        await asyncio.sleep(0)

    async def _dispatch_loop(self, event_type: EventType) -> None:
        """Internal dispatch loop for a single event type."""
        queue = self._queues[event_type]
        while self._running:
            try:
                event = await queue.get()
                # Call specific subscribers
                for handler in self._subscribers[event_type]:
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
                for handler in self._wildcard_subscribers:
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
                logger.exception("Dispatch loop error for %s: %s", event_type.value, e)

    def get_queue_size(self, event_type: EventType) -> int:
        """Get current queue size for monitoring."""
        return self._queues[event_type].qsize()


# Global instance for easy access
_global_bus: MessageBus | None = None


def get_message_bus() -> MessageBus:
    """Get or create the global message bus instance."""
    global _global_bus
    if _global_bus is None:
        _global_bus = MessageBus()
    return _global_bus


async def init_message_bus() -> MessageBus:
    """Initialize and start the global message bus."""
    bus = get_message_bus()
    await bus.start()
    return bus


async def shutdown_message_bus() -> None:
    """Shutdown the global message bus."""
    global _global_bus
    if _global_bus:
        await _global_bus.stop()
        _global_bus = None
