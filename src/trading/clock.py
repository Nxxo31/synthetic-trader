"""Clock Abstraction — Deterministic time for backtest, real-time for live.

Based on NautilusTrader's Clock pattern:
- TestClock: Advances time programmatically (deterministic backtest)
- LiveClock: Real-time wall clock (paper/live trading)
- Same engine code works with both → backtest-live parity
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from threading import Lock
from typing import Any


class ClockType(str, Enum):
    """Clock type enumeration."""

    TEST = "test"
    LIVE = "live"


class Clock(ABC):
    """Abstract base clock for time management."""

    @property
    @abstractmethod
    def now_ns(self) -> int:
        """Current time in nanoseconds since epoch."""
        pass

    @property
    @abstractmethod
    def now_ms(self) -> int:
        """Current time in milliseconds since epoch."""
        pass

    @property
    @abstractmethod
    def now(self) -> datetime:
        """Current time as timezone-aware datetime."""
        pass

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Sleep for specified seconds (blocking)."""
        pass

    @abstractmethod
    async def async_sleep(self, seconds: float) -> None:
        """Async sleep for specified seconds."""
        pass

    @property
    @abstractmethod
    def clock_type(self) -> ClockType:
        """Return clock type."""
        pass


class LiveClock(Clock):
    """Real-time wall clock for paper/live trading."""

    @property
    def now_ns(self) -> int:
        return time.time_ns()

    @property
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    @property
    def now(self) -> datetime:
        return datetime.fromtimestamp(time.time(), tz=UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    async def async_sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    @property
    def clock_type(self) -> ClockType:
        return ClockType.LIVE


class TestClock(Clock):
    """Deterministic clock for backtesting.

    Time only advances when explicitly told to.
    Allows precise control over time in simulations.
    """

    def __init__(self, start_time_ns: int | None = None):
        self._time_ns = start_time_ns or time.time_ns()
        self._lock = Lock()
        self._scheduled_callbacks: dict[int, list[Callable]] = {}
        self._next_callback_time: int | None = None

    @property
    def now_ns(self) -> int:
        with self._lock:
            return self._time_ns

    @property
    def now_ms(self) -> int:
        with self._lock:
            return self._time_ns // 1_000_000

    @property
    def now(self) -> datetime:
        with self._lock:
            return datetime.fromtimestamp(self._time_ns / 1_000_000_000, tz=UTC)

    def sleep(self, seconds: float) -> None:
        """Advance clock by specified seconds (blocking)."""
        self.advance(seconds)

    async def async_sleep(self, seconds: float) -> None:
        """Advance clock by specified seconds (async)."""
        self.advance(seconds)
        await asyncio.sleep(0)  # Yield control

    def advance(self, seconds: float) -> None:
        """Advance clock by specified seconds and execute callbacks."""
        advance_ns = int(seconds * 1_000_000_000)
        with self._lock:
            self._time_ns += advance_ns
            self._execute_due_callbacks()

    def advance_to(self, target_ns: int) -> None:
        """Advance clock to a specific timestamp."""
        with self._lock:
            if target_ns <= self._time_ns:
                return
            self._time_ns = target_ns
            self._execute_due_callbacks()

    def schedule_callback(self, delay_seconds: float, callback: Callable) -> int:
        """Schedule a callback to run after delay_seconds.

        Returns callback ID for cancellation.
        """
        execute_at = self.now_ns + int(delay_seconds * 1_000_000_000)
        callback_id = id(callback)

        with self._lock:
            if execute_at not in self._scheduled_callbacks:
                self._scheduled_callbacks[execute_at] = []
            self._scheduled_callbacks[execute_at].append(callback)
            self._update_next_callback()

        return callback_id

    def cancel_callback(self, callback_id: int) -> bool:
        """Cancel a scheduled callback."""
        with self._lock:
            for callbacks in self._scheduled_callbacks.values():
                for i, cb in enumerate(callbacks):
                    if id(cb) == callback_id:
                        callbacks.pop(i)
                        self._update_next_callback()
                        return True
            return False

    def _execute_due_callbacks(self) -> None:
        """Execute all callbacks due at or before current time."""
        due_times = [t for t in self._scheduled_callbacks if t <= self._time_ns]
        for execute_at in due_times:
            callbacks = self._scheduled_callbacks.pop(execute_at, [])
            for callback in callbacks:
                try:
                    callback()
                except Exception as e:
                    # Log but don't break other callbacks
                    print(f"TestClock callback error: {e}")
        self._update_next_callback()

    def _update_next_callback(self) -> None:
        """Update next callback time for optimization."""
        if self._scheduled_callbacks:
            self._next_callback_time = min(self._scheduled_callbacks.keys())
        else:
            self._next_callback_time = None

    @property
    def clock_type(self) -> ClockType:
        return ClockType.TEST

    def reset(self, new_time_ns: int | None = None) -> None:
        """Reset clock to new time."""
        with self._lock:
            self._time_ns = new_time_ns or time.time_ns()
            self._scheduled_callbacks.clear()
            self._next_callback_time = None


class ClockFactory:
    """Factory for creating clocks based on environment."""

    @staticmethod
    def create(environment: str) -> Clock:
        """Create clock based on environment string."""
        env = environment.lower()
        if env in ("backtest", "test"):
            return TestClock()
        elif env in ("sandbox", "paper", "live"):
            return LiveClock()
        else:
            raise ValueError(f"Unknown environment: {environment}")

    @staticmethod
    def create_from_config(config: Any) -> Clock:
        """Create clock from ConfigManager."""
        env = config.environment.value if hasattr(config.environment, 'value') else str(config.environment)
        return ClockFactory.create(env)


# Timer for periodic tasks
@dataclass
class Timer:
    """Periodic timer that works with any Clock."""

    interval_seconds: float
    callback: Callable
    clock: Clock
    name: str = ""
    _running: bool = field(default=False, init=False)
    _task: asyncio.Task | None = field(default=None, init=False)

    async def start(self) -> None:
        """Start the timer."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name=f"timer_{self.name}")

    async def stop(self) -> None:
        """Stop the timer."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        """Main timer loop."""
        while self._running:
            start = self.clock.now_ns
            try:
                if asyncio.iscoroutinefunction(self.callback):
                    await self.callback()
                else:
                    self.callback()
            except Exception as e:
                print(f"Timer {self.name} error: {e}")

            # Calculate next execution time
            elapsed_ns = self.clock.now_ns - start
            elapsed_s = elapsed_ns / 1_000_000_000
            sleep_s = max(0, self.interval_seconds - elapsed_s)

            if sleep_s > 0:
                await self.clock.async_sleep(sleep_s)


# Context manager for clock
class ClockContext:
    """Context manager for setting global clock."""

    _global_clock: Clock | None = None

    @classmethod
    def set_clock(cls, clock: Clock) -> Clock:
        """Set global clock, return previous."""
        prev = cls._global_clock
        cls._global_clock = clock
        return prev

    @classmethod
    def get_clock(cls) -> Clock:
        """Get global clock, create LiveClock if none."""
        if cls._global_clock is None:
            cls._global_clock = LiveClock()
        return cls._global_clock

    @classmethod
    def reset(cls) -> None:
        """Reset global clock."""
        cls._global_clock = None


def get_clock() -> Clock:
    """Get the global clock."""
    return ClockContext.get_clock()


def set_clock(clock: Clock) -> Clock:
    """Set the global clock."""
    return ClockContext.set_clock(clock)


if __name__ == "__main__":
    # Test LiveClock
    print("=== LiveClock ===")
    live = LiveClock()
    print(f"now: {live.now}")
    print(f"now_ms: {live.now_ms}")

    # Test TestClock
    print("\n=== TestClock ===")
    test = TestClock(1_700_000_000_000_000_000)  # Fixed start time
    print(f"start: {test.now}")

    # Advance time
    test.advance(10)
    print(f"after +10s: {test.now}")

    test.advance(5)
    print(f"after +5s: {test.now}")

    # Test scheduled callback
    executed = []

    def callback():
        executed.append(test.now)

    test.schedule_callback(2.0, callback)
    test.advance(1.0)
    print(f"after +1s (callback not yet): executed={len(executed)}")

    test.advance(2.0)
    print(f"after +2s (callback should fire): executed={len(executed)}")

    # Test Timer
    print("\n=== Timer with TestClock ===")
    async def test_timer():
        counter = [0]
        test_clock = TestClock()
        timer = Timer(interval_seconds=1.0, callback=lambda: counter.__setitem__(0, counter[0]+1), clock=test_clock, name="test")

        await timer.start()
        test_clock.advance(3.5)
        await timer.stop()
        print(f"Counter after 3.5s: {counter[0]}")  # Should be 3

    asyncio.run(test_timer())
