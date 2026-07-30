"""Compatibility shim — modules moved to src.strategies.

This package is kept only for backwards compatibility with imports of the
form ``from src.strategy.base import ...`` or
``from src.strategy.range_break import ...``. New code should import from
``src.strategies`` instead.

Deprecation: this shim can be removed once all callers are migrated.
"""
from src.strategies.base import (  # noqa: F401
    Signal,
    SignalType,
    Strategy,
)
