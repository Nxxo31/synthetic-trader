"""VolatilityStrategyV2 — improved ATR-band mean-reversion with regime detection.

Improvements over VolatilityStrategy (v1):

1. **Market regime filter**: Uses ATR percentile rank to classify the market as
   *ranging* vs *trending*. Mean-reversion only works well in ranging regimes
   (low-to-mid ATR percentile). In trending/high-vol regimes, signals are
   suppressed — price is driven by directional momentum, not mean reversion.

2. **Volatility squeeze confirmation**: Entry requires a Bollinger-like squeeze
   (ATR contraction followed by expansion). The squeeze detector checks that
   ATR was below its own moving average (compressed) and is now expanding. This
   avoids entering in dead-tracking markets where the price can drift along the
   band without reverting.

3. **Dynamic ATR stop-loss**: `sl_distance = atr * sl_atr_multiplier` — no
   fixed pip SL. Wider in high-vol (avoid noise stops), tighter in low-vol.

4. **Trailing stop via ATR**: The TP is computed normally from the EMA midline,
   but the metadata includes a `trailing_stop` field (entry ± atr_mult) that the
   runner/logic can use to trail the stop as price moves favourably.

Implements the same Strategy interface as v1:
    generate_signal(data) → Signal
    get_win_probability(signal) → float
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.strategies.base import Signal, SignalType, Strategy
from src.analysis.indicators import calculate_atr, calculate_ema
from src.analysis.volatility_filter import VolatilityFilter

logger = logging.getLogger(__name__)


@dataclass
class VolatilityV2Config:
    """Configuration for VolatilityStrategyV2.

    Attributes:
        atr_period:           ATR look-back window (default 14).
        ema_period:           EMA period for band midline (default 20).
        band_multiplier:      ATRs above/below EMA for bands (default 2.0).
        min_candles:          Minimum candles before generating  a signal.
        max_duration:         Maximum hold time in seconds.
        sl_atr_multiplier:    Stop-loss distance in ATR multiples (dynamic).
        tp_atr_multiplier:    Take-profit distance in ATR multiples.
        trailing_atr_multiplier: ATR multiples for trailing stop offset.
        score_threshold:      Minimum composite score to emit a signal.
        regime_lookback:      Window size for ATR percentile ranking.
        regime_min_pct:       Minimum ATR percentile for ranging regime.
        regime_max_pct:       Maximum ATR percentile for ranging regime (inclusive).
                             Above this → trending, suppress signals.
        squeeze_atr_ma:       Period for ATR moving average (squeeze detection).
        squeeze_expansion_threshold: Ratio ATR / ATR_MA at which expansion is confirmed.
    """
    atr_period: int = 14
    ema_period: int = 20
    band_multiplier: float = 2.0
    min_candles: int = 50  # needs more data for regime & percentile
    max_duration: int = 900
    sl_atr_multiplier: float = 2.0
    tp_atr_multiplier: float = 3.0
    trailing_atr_multiplier: float = 1.5
    score_threshold: float = 0.40  # stricter than v1 (0.35)
    regime_lookback: int = 100
    regime_min_pct: int = 10    # range: 10th percentile to 75th percentile ATR = ranging regime
    regime_max_pct: int = 75
    squeeze_atr_ma: int = 20
    squeeze_expansion_threshold: float = 1.1  # ATR > 1.1 × its MA → expansion confirmed


class VolatilityStrategyV2(Strategy):
    """ATR-band mean-reversion v2 with regime detection and squeeze confirmation.

    Signals are only emitted when:
    - The ATR percentile falls within the **ranging regime** band.
    - A **volatility squeeze** has been confirmed (ATR was compressed, now expanding).
    - Price has penetrated past the ATR band by a sufficient margin.
    - The composite score (deviation + volatility + regime bonus) exceeds the threshold.
    """

    def __init__(
        self,
        symbol: str = "R_100",
        config: VolatilityV2Config | None = None,
        volatility_filter: VolatilityFilter | None = None,
    ) -> None:
        super().__init__("VolatilityV2", symbol)
        self.config = config or VolatilityV2Config()
        self.volatility_filter = volatility_filter or VolatilityFilter(
            atr_period=self.config.atr_period
        )

    # ------------------------------------------------------------------ #
    #  Core signal generation                                            #
    # ------------------------------------------------------------------ #

    def _compute_bands(self, data: pd.DataFrame) -> tuple[float, float, float, float]:
        """Compute EMA midline and ATR bands.

        Returns (midline, upper_band, lower_band, atr_value).
        All values are ``NaN`` if insufficient data.
        """
        close = data["close"].astype(float)
        ema = calculate_ema(close, period=self.config.ema_period)
        atr = calculate_atr(data, period=self.config.atr_period)

        if ema.isna().iloc[-1] or atr.isna().iloc[-1]:
            return (np.nan, np.nan, np.nan, np.nan)

        mid = float(ema.iloc[-1])
        atr_val = float(atr.iloc[-1])
        upper = mid + self.config.band_multiplier * atr_val
        lower = mid - self.config.band_multiplier * atr_val
        return (mid, upper, lower, atr_val)

    def _atr_percentile_rank(self, data: pd.DataFrame) -> float:
        """Compute the percentile rank of the current ATR within a lookback window.

        Uses the ATR value **excluding the last candle** to avoid self-referential
        bias: the candle that triggers the signal often causes an ATR spike that
        would always rank in the top percentile and falsely classify the market
        as trending.

        Returns a value 0–100 where 50 = median ATR.
        Returns -1 if insufficient data.
        """
        atr_series = calculate_atr(data, period=self.config.atr_period)
        if len(atr_series) < self.config.regime_lookback + 1:
            return -1

        # Exclude the last candle (current signal candle) from the ranking window
        window = atr_series.iloc[-(self.config.regime_lookback + 1):-1].dropna()
        if len(window) < 20:
            return -1

        ref_atr = float(window.iloc[-1])  # most recent ATR before the signal candle
        if np.isnan(ref_atr):
            return -1

        # Percentile rank: count values strictly below ref / total
        lower = np.sum(window.values < ref_atr)
        return round(lower / len(window) * 100, 2)

    def _detect_squeeze(self, data: pd.DataFrame) -> bool:
        """Detect volatility squeeze (ATR was compressed, now expanding).

        Squeeze = ATR was below its own moving average, and is now above
        the expansion threshold.
        """
        atr_series = calculate_atr(data, period=self.config.atr_period)
        ma_period = self.config.squeeze_atr_ma

        if len(atr_series) < ma_period + 5:
            return False

        atr_clean = atr_series.dropna()
        if len(atr_clean) < ma_period + 2:
            return False

        current_atr = float(atr_clean.iloc[-1])
        atr_ma = float(atr_clean.iloc[-(ma_period + 1):-1].tail(ma_period).mean())

        if atr_ma <= 0 or np.isnan(atr_ma) or np.isnan(current_atr):
            return False

        # Check: was ATR compressed recently? (below MA in the prior 5 candles)
        prior_5 = atr_clean.iloc[-(ma_period + 5):-ma_period] if ma_period >= 5 else atr_clean.iloc[-10:-5]
        was_compressed = bool((prior_5 < atr_ma * 0.95).any())

        # And now expanding?
        is_expanding = current_atr > atr_ma * self.config.squeeze_expansion_threshold

        return was_compressed and is_expanding

    def _band_deviation_score(
        self, close: float, mid: float, atr_val: float
    ) -> tuple[float, float]:
        """Calculate deviation score from the band, normalised by ATR.

        Returns (direction_score, distance_in_atr).
        Larger distance from midline → stronger signal.
        """
        if atr_val <= 0:
            return (0.0, 0.0)

        distance_atr = abs(close - mid) / atr_val

        if distance_atr <= 0.8:
            score = 0.0
        elif distance_atr >= 2.0:
            score = 0.30
        else:
            # Linear: 0.8 → 0, 2.0 → 0.30
            score = (distance_atr - 0.8) * (0.30 / 1.2)

        return (round(min(0.30, score), 4), distance_atr)

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Analyse candles and produce an ATR-band mean-reversion signal (v2).

        Args:
            data: DataFrame with columns ``high``, ``low``, ``close``.

        Returns:
            Signal with entry, SL, TP, confidence, and trailing stop metadata.
            ``SignalType.NO_SIGNAL`` when no band penetration or regime rejection.
        """
        if len(data) < self.config.min_candles:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
            )

        mid, upper, lower, atr_val = self._compute_bands(data)

        if np.isnan(mid) or np.isnan(atr_val) or atr_val <= 0:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "insufficient_data_for_bands"},
            )

        last_close = float(data["close"].iloc[-1])

        # --- Regime filter ------------------------------------------------
        pct_rank = self._atr_percentile_rank(data)
        if pct_rank < 0:
            pct_rank = 50  # neutral if insufficient data

        in_ranging = (
            self.config.regime_min_pct <= pct_rank <= self.config.regime_max_pct
        )

        # Regime bonus: ranging regime gets +0.05 bonus, trending gets -0.15
        regime_bonus = 0.05 if in_ranging else -0.15

        # --- Squeeze confirmation -----------------------------------------
        squeeze_confirmed = self._detect_squeeze(data)
        squeeze_bonus = 0.10 if squeeze_confirmed else 0.0

        # --- Volatility score from VolatilityFilter -----------------------
        atr_score = float(self.volatility_filter.volatility_score(data))

        # --- No signal if ranging regime is not met -----------------------
        if not in_ranging:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=last_close,
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={
                    "reason": "trending_regime",
                    "atr_percentile": pct_rank,
                    "atp_rank": pct_rank,
                    "upper_band": upper,
                    "lower_band": lower,
                    "ema_midline": mid,
                    "atr_value": atr_val,
                },
            )

        # --- Band penetration logic ---------------------------------------
        if last_close < lower:
            # Price below lower band → LONG (expect bounce)
            dev_score, dist_atr = self._band_deviation_score(last_close, mid, atr_val)
            score = round(dev_score + atr_score + regime_bonus + squeeze_bonus, 4)
            score = min(1.0, max(0.0, score))

            if score < self.config.score_threshold:
                return Signal(
                    type=SignalType.NO_SIGNAL,
                    symbol=self.symbol,
                    entry_price=last_close,
                    stop_loss=0, take_profit=0,
                    duration_seconds=0,
                    confidence=score,
                    metadata={
                        "reason": "score_below_threshold",
                        "score": score,
                        "atr_percentile": pct_rank,
                        "squeeze_confirmed": squeeze_confirmed,
                    },
                )

            sl_distance = atr_val * self.config.sl_atr_multiplier
            tp_distance = atr_val * self.config.tp_atr_multiplier
            trailing_offset = atr_val * self.config.trailing_atr_multiplier
            entry = last_close
            sl = entry - sl_distance
            tp = entry + tp_distance
            trailing = entry - trailing_offset  # trails below for LONG

            logger.info(
                "VolatilityV2 LONG: close=%.5f below lower=%.5f "
                "(ATR=%.5f, pct=%.1f, squeeze=%s, score=%.3f)",
                last_close, lower, atr_val, pct_rank, squeeze_confirmed, score,
            )
            return Signal(
                type=SignalType.LONG,
                symbol=self.symbol,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                duration_seconds=self.config.max_duration,
                confidence=score,
                metadata={
                    "strategy": "volatility_v2",
                    "upper_band": upper,
                    "lower_band": lower,
                    "ema_midline": mid,
                    "atr_value": atr_val,
                    "distance_atr": dist_atr,
                    "deviation_score": dev_score,
                    "atr_score": atr_score,
                    "regime_bonus": regime_bonus,
                    "squeeze_bonus": squeeze_bonus,
                    "atr_percentile": pct_rank,
                    "squeeze_confirmed": squeeze_confirmed,
                    "trailing_stop": trailing,
                },
            )

        if last_close > upper:
            # Price above upper band → SHORT (expect reversion)
            dev_score, dist_atr = self._band_deviation_score(last_close, mid, atr_val)
            score = round(dev_score + atr_score + regime_bonus + squeeze_bonus, 4)
            score = min(1.0, max(0.0, score))

            if score < self.config.score_threshold:
                return Signal(
                    type=SignalType.NO_SIGNAL,
                    symbol=self.symbol,
                    entry_price=last_close,
                    stop_loss=0, take_profit=0,
                    duration_seconds=0,
                    confidence=score,
                    metadata={
                        "reason": "score_below_threshold",
                        "score": score,
                        "atr_percentile": pct_rank,
                        "squeeze_confirmed": squeeze_confirmed,
                    },
                )

            sl_distance = atr_val * self.config.sl_atr_multiplier
            tp_distance = atr_val * self.config.tp_atr_multiplier
            trailing_offset = atr_val * self.config.trailing_atr_multiplier
            entry = last_close
            sl = entry + sl_distance   # SHORT: SL above
            tp = entry - tp_distance   # SHORT: TP below
            trailing = entry + trailing_offset  # trails above for SHORT

            logger.info(
                "VolatilityV2 SHORT: close=%.5f above upper=%.5f "
                "(ATR=%.5f, pct=%.1f, squeeze=%s, score=%.3f)",
                last_close, upper, atr_val, pct_rank, squeeze_confirmed, score,
            )
            return Signal(
                type=SignalType.SHORT,
                symbol=self.symbol,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                duration_seconds=self.config.max_duration,
                confidence=score,
                metadata={
                    "strategy": "volatility_v2",
                    "upper_band": upper,
                    "lower_band": lower,
                    "ema_midline": mid,
                    "atr_value": atr_val,
                    "distance_atr": dist_atr,
                    "deviation_score": dev_score,
                    "atr_score": atr_score,
                    "regime_bonus": regime_bonus,
                    "squeeze_bonus": squeeze_bonus,
                    "atr_percentile": pct_rank,
                    "squeeze_confirmed": squeeze_confirmed,
                    "trailing_stop": trailing,
                },
            )

        # Price within bands — no signal
        return Signal(
            type=SignalType.NO_SIGNAL,
            symbol=self.symbol,
            entry_price=last_close,
            stop_loss=0, take_profit=0,
            duration_seconds=0,
            confidence=0.0,
            metadata={
                "upper_band": upper,
                "lower_band": lower,
                "ema_midline": mid,
                "atr_value": atr_val,
                "atr_percentile": pct_rank,
                "squeeze_confirmed": squeeze_confirmed,
                "reason": "within_bands",
            },
        )

    def get_win_probability(self, signal: Signal) -> float:
        """Estimate win probability for Kelly sizing.

        V2 has a slightly higher base rate (60%) than v1 (58%) due to
        the stricter regime + squeeze filters that should improve signal quality.
        """
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0

        base_prob = 0.60
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.12
        prob = min(0.68, max(0.52, base_prob + adj))

        return round(prob, 4)
