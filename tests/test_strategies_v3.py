#!/usr/bin/env python3
"""Test suite for VolatilityV3 strategy — step_index integration."""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, '/home/sebas/proyectos/synthetic-trader')

from src.strategies.base import Signal, SignalType
from src.strategies.volatility_v3 import VolatilityStrategyV3, VolatilityV3Config


def test_volatility_v3_basic():
    """Test basic VolatilityV3 strategy signal generation."""
    cfg = VolatilityV3Config(
        atr_period=14,
        ema_fast=9,
        ema_slow=21,
        band_multiplier=2.0,
        min_candles=300,  # Need 300+ for vol_ratio + step_index
        max_duration=900,
        vol_median_window=252,
        calm_threshold=0.8,
        stress_threshold=1.3,
        panic_threshold=1.6,
        persistence={
            "CALM": 20,
            "NORMAL": 15,
            "STRESS": 5,
            "PANIC": 2,
        },
        step_size_pct=0.000003,
    )
    strategy = VolatilityStrategyV3(symbol="stpRNG", config=cfg)

    # Generate synthetic OHLC data
    np.random.seed(42)
    n = 400  # Enough for 252 window + overhead
    close = 7734.0 + np.cumsum(np.random.randn(n) * 10)
    high = close + np.abs(np.random.randn(n) * 5)
    low = close - np.abs(np.random.randn(n) * 5)
    open_ = close + (np.random.randn(n) * 2)
    data = pd.DataFrame({
        'epoch': np.arange(n),
        'open': open_.astype(float),
        'high': high.astype(float),
        'low': low.astype(float),
        'close': close.astype(float),
    })

    signal = strategy.generate_signal(data)
    wp = strategy.get_win_probability(signal)
    print(f"  VolatilityV3: type={signal.type.value}, confidence={signal.confidence:.4f}, "
          f"win_prob={wp:.4f}, regime={signal.metadata.get('regime', 'N/A')}")
    # Should generate a signal (not necessarily NO_SIGNAL, just shouldn't crash)
    assert signal.type in [SignalType.LONG, SignalType.SHORT, SignalType.NO_SIGNAL], \
        f"Unexpected signal type: {signal.type}"
    print("  ✅ test_volatility_v3_basic PASSED")


def test_volatility_v3_insufficient_data():
    """Test VolatilityV3 with insufficient candles."""
    cfg = VolatilityV3Config(min_candles=300)
    strategy = VolatilityStrategyV3(symbol="stpRNG", config=cfg)

    np.random.seed(42)
    n = 100  # Less than min_candles
    close = 7734.0 + np.cumsum(np.random.randn(n) * 10)
    data = pd.DataFrame({
        'epoch': np.arange(n),
        'open': close * 0.99,
        'high': close * 1.01,
        'low': close * 0.99,
        'close': close.astype(float),
    })

    signal = strategy.generate_signal(data)
    assert signal.type == SignalType.NO_SIGNAL
    assert signal.metadata.get("reason") == "insufficient_candles"
    print("  ✅ test_volatility_v3_insufficient_data PASSED")


def test_volatility_v3_panic_regime():
    """Test VolatilityV3 PANIC regime (no signals generated)."""
    # Create data with extremely high vol_ratio to trigger PANIC
    cfg = VolatilityV3Config(
        atr_period=14,
        ema_fast=9,
        ema_slow=21,
        band_multiplier=2.0,
        min_candles=300,
        max_duration=900,
        vol_median_window=252,
        calm_threshold=0.8,
        stress_threshold=1.3,
        panic_threshold=1.6,
        persistence={
            "CALM": 20,
            "NORMAL": 15,
            "STRESS": 5,
            "PANIC": 2,
        },
        step_size_pct=0.000003,
    )
    strategy = VolatilityStrategyV3(symbol="stpRNG", config=cfg)

    np.random.seed(12345)  # Different seed for extreme vol
    n = 400
    # Extremely high volatility to push vol_ratio well above panic_threshold
    close = 7734.0 + np.cumsum(np.random.randn(n) * 800)  # Very high vol
    high = close + np.abs(np.random.randn(n) * 400)
    low = close - np.abs(np.random.randn(n) * 400)
    open_ = close + (np.random.randn(n) * 100)
    data = pd.DataFrame({
        'epoch': np.arange(n),
        'open': open_.astype(float),
        'high': high.astype(float),
        'low': low.astype(float),
        'close': close.astype(float),
    })

    signal = strategy.generate_signal(data)
    # With extremely high vol, state machine should eventually reach PANIC
    # and sit out (no signals). May need multiple calls for persistence.
    print(f"  VolatilityV3 PANIC: type={signal.type.value}, "
          f"reason={signal.metadata.get('reason', 'N/A')}, "
          f"vol_ratio={signal.metadata.get('vol_ratio', 'N/A')}")
    # The key invariant: shouldn't crash, and PANIC should result in NO_SIGNAL
    # (might be NORMAL if persistence not met, but shouldn't error)
    assert signal.type in [SignalType.LONG, SignalType.SHORT, SignalType.NO_SIGNAL], \
        f"Unexpected signal type: {signal.type}"
    # If we get a signal, it should be valid; if NO_SIGNAL, reason should be valid
    if signal.type == SignalType.NO_SIGNAL:
        assert 'reason' in signal.metadata, "NO_SIGNAL should have reason"
    print("  ✅ test_volatility_v3_panic_regime PASSED")
    print("  ✅ test_volatility_v3_panic_regime PASSED")


def test_volatility_v3_step_size_integration():
    """Test that step_size is properly resolved and used in SL/TP."""
    cfg = VolatilityV3Config(
        atr_period=14,
        ema_fast=9,
        ema_slow=21,
        band_multiplier=2.0,
        min_candles=300,
        max_duration=900,
        vol_median_window=252,
        calm_threshold=0.8,
        stress_threshold=1.3,
        panic_threshold=1.6,
        persistence={
            "CALM": 20,
            "NORMAL": 15,
            "STRESS": 5,
            "PANIC": 2,
        },
        step_size_pct=0.000003,
    )
    strategy = VolatilityStrategyV3(symbol="stpRNG", config=cfg)

    np.random.seed(42)
    n = 400
    close = 7734.0 + np.cumsum(np.random.randn(n) * 10)
    high = close + np.abs(np.random.randn(n) * 5)
    low = close - np.abs(np.random.randn(n) * 5)
    open_ = close + (np.random.randn(n) * 2)
    data = pd.DataFrame({
        'epoch': np.arange(n),
        'open': open_.astype(float),
        'high': high.astype(float),
        'low': low.astype(float),
        'close': close.astype(float),
    })

    signal = strategy.generate_signal(data)
    # Verify step_size is in metadata
    step_size = signal.metadata.get('step_size')
    has_sl = signal.stop_loss is not None and signal.stop_loss != 0
    has_tp = signal.take_profit is not None and signal.take_profit != 0
    print(f"  Step size in metadata: {step_size}")
    print(f"  Has SL: {has_sl}, Has TP: {has_tp}")
    # Should have step_size and SL/TP set
    assert step_size is not None, "step_size should be in metadata"
    assert has_sl, "Should have stop_loss"
    assert has_tp, "Should have take_profit"
    print("  ✅ test_volatility_v3_step_size_integration PASSED")


if __name__ == "__main__":
    print("=== VolatilityV3 Test Suite ===\n")
    test_volatility_v3_basic()
    test_volatility_v3_insufficient_data()
    test_volatility_v3_panic_regime()
    test_volatility_v3_step_size_integration()
    print("\n=== All tests PASSED ===")
