"""VolatilityStrategyV3 — Volatility with step-index integration (B-2).

Combina detección de régimen de volatilidad (como v2) con el componente
multi-step de Step Index: SL/TP en múltiplos del tamaño de paso conocido,
adaptado por régimen.

Regímenes:
- CALM:   mean-reversion con bandas ATR + paso-size SL/TP (RR 1:1)
- NORMAL: trend-following EMA cross + paso-size SL/TP (RR 1:1.5)
- STRESS: trend-following con filtro más estricto, SL/TP ampliados (RR 1:2)
- PANIC:  sit-out, no señales

Innovación clave: el tamaño de paso no es fijo — se escala con la volatilidad
actual via vol_ratio, haciendo SL/TP más estrechos en calma y más amplios en
estrés, manteniendo la precisión matemática del Step Index.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from src.analysis.indicators import calculate_atr, calculate_ema
from src.strategies.base import Signal, SignalType, Strategy
from src.strategies.step_index import STEP_SIZE_PCT, StepIndexStrategy

logger = logging.getLogger(__name__)


class VolatilityRegime(Enum):
    CALM = "CALM"
    NORMAL = "NORMAL"
    STRESS = "STRESS"
    PANIC = "PANIC"

    @property
    def severity(self) -> int:
        return {"CALM": 0, "NORMAL": 1, "STRESS": 2, "PANIC": 3}[self.value]


@dataclass
class VolatilityV3Config:
    # Vol regime -----------------------------------------------------------
    atr_period: int = 14
    ema_fast: int = 9
    ema_slow: int = 21
    band_multiplier: float = 2.0
    min_candles: int = 50
    max_duration: int = 900
    vol_median_window: int = 252
    calm_threshold: float = 0.8
    stress_threshold: float = 1.3
    panic_threshold: float = 1.6
    persistence: dict[str, int] = field(
        default_factory=lambda: {"CALM": 20, "NORMAL": 15, "STRESS": 5, "PANIC": 2}
    )

    # Step-index integration -----------------------------------------------
    # step_size_pct puede ser None (se inferirá del símbolo) o un valor explícito
    step_size_pct: float | None = None
    # Cómo escalar el paso por régimen (multiplicador sobre el paso base)
    step_scale: dict[str, float] = field(
        default_factory=lambda: {"CALM": 1.0, "NORMAL": 1.0, "STRESS": 1.5, "PANIC": 2.0}
    )

    # Entry rules ----------------------------------------------------------
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    mr_score_threshold: float = 0.40
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    tf_score_threshold: float = 0.45
    stress_score_threshold: float = 0.55


class VolatilityStrategyV3(Strategy):
    """Volatility with step-index integration (B-2).

    Características principales:
    1. Detección de régimen de volatilidad (CALM/NORMAL/STRESS/PANIC) con persistence
    2. SL/TP en múltiplos del tamaño de paso StepIndex, escalado por régimen
    3. Mean-reversion en CALM (bandas ATR + RSI confirmación)
    4. Trend-following en NORMAL/STRESS (EMA cross + MACD)
    5. PANIC → sit-out (NO_SIGNAL)
    6. Step size dinámico: se escala con vol_ratio, no es fijo
    """

    def __init__(
        self,
        symbol: str = "stpRNG",
        config: VolatilityV3Config | None = None,
    ) -> None:
        super().__init__("VolatilityV3", symbol)
        self.config = config or VolatilityV3Config()
        # State machine
        self._regime: VolatilityRegime = VolatilityRegime.NORMAL
        self._pending: VolatilityRegime = VolatilityRegime.NORMAL
        self._pending_count: int = 0
        # Step-index strategy para resolución de paso
        self._step_strategy = StepIndexStrategy(symbol=symbol)

    # ------------------------------------------------------------------ #
    #  ATR / vol ratio                                                  #
    # ------------------------------------------------------------------ #

    def _compute_vol_ratio(self, data: pd.DataFrame) -> tuple[float, float]:
        atr_series = calculate_atr(data, period=self.config.atr_period)
        if len(atr_series) < self.config.vol_median_window + 1:
            return (np.nan, np.nan)
        atr_now = float(atr_series.iloc[-1])
        window = atr_series.iloc[-(self.config.vol_median_window + 1):-1].dropna()
        if len(window) < self.config.vol_median_window // 2:
            return (atr_now, np.nan)
        median_atr = float(window.median())
        if median_atr <= 0:
            return (atr_now, np.nan)
        return (atr_now, atr_now / median_atr)

    def _classify(self, vol_ratio: float) -> VolatilityRegime:
        if vol_ratio < self.config.calm_threshold:
            return VolatilityRegime.CALM
        if vol_ratio < self.config.stress_threshold:
            return VolatilityRegime.NORMAL
        if vol_ratio < self.config.panic_threshold:
            return VolatilityRegime.STRESS
        return VolatilityRegime.PANIC

    def _update_state_machine(self, raw: VolatilityRegime) -> VolatilityRegime:
        if raw == self._regime:
            if self._pending != self._regime:
                self._pending = self._regime
                self._pending_count = 0
            return self._regime
        if raw == self._pending:
            self._pending_count += 1
        else:
            self._pending = raw
            self._pending_count = 1
        required = self.config.persistence.get(raw.value, 5)
        if self._pending_count >= required:
            self._regime = raw
        return self._regime

    # ------------------------------------------------------------------ #
    #  Resolver paso indexado por régimen                                #
    # ------------------------------------------------------------------ #

    def _resolve_step_for_regime(
        self,
        symbol: str,
        regime: VolatilityRegime,
        config_step: float | None,
    ) -> float | None:
        """Resuelve el tamaño de paso ajustado por régimen."""
        # Usar valor explícito de config si está presente
        if config_step is not None and config_step > 0:
            return config_step

        # Inferir del símbolo via STEP_SIZE_PCT
        sym_key = symbol.upper().replace(" ", "")
        base_step = STEP_SIZE_PCT.get(sym_key)
        if base_step is None:
            return None

        # Escalar por régimen
        scale = self.config.step_scale.get(regime.value, 1.0)
        return round(base_step * scale, 8)

    # ------------------------------------------------------------------ #
    #  Indicadores auxiliares (RSI / MACD inline)                       #
    # ------------------------------------------------------------------ #

    def _compute_rsi(self, close: pd.Series, period: int) -> float:
        if len(close) < period + 1:
            return float("nan")
        delta = close.astype(float).diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        last_gain = float(avg_gain.iloc[-1])
        last_loss = float(avg_loss.iloc[-1])
        if last_loss == 0:
            return 100.0 if last_gain > 0 else 50.0
        rs = last_gain / last_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _compute_macd(self, close: pd.Series) -> tuple[float, float, float]:
        fast = self.config.macd_fast
        slow = self.config.macd_slow
        sig = self.config.macd_signal
        if len(close) < slow + sig:
            return (float("nan"), float("nan"), float("nan"))
        ema_fast = close.astype(float).ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=sig, adjust=False).mean()
        hist = macd_line - signal_line
        return (float(macd_line.iloc[-1]), float(signal_line.iloc[-1]),
                float(hist.iloc[-1]))

    def _atr_bands(self, data: pd.DataFrame, atr_val: float) -> tuple[float, float, float]:
        close = data["close"].astype(float)
        ema = calculate_ema(close, period=self.config.ema_slow)
        if ema.isna().iloc[-1]:
            return (float("nan"), float("nan"), float("nan"))
        mid = float(ema.iloc[-1])
        upper = mid + self.config.band_multiplier * atr_val
        lower = mid - self.config.band_multiplier * atr_val
        return (mid, upper, lower)

    # ------------------------------------------------------------------ #
    #  Entry rules por régimen                                         #
    # ------------------------------------------------------------------ #

    def _mean_reversion_signal(
        self, data: pd.DataFrame, mid: float, upper: float,
        lower: float, atr_val: float, regime: VolatilityRegime,
    ) -> Signal:
        cfg = self.config
        last_close = float(data["close"].iloc[-1])
        rsi = self._compute_rsi(data["close"].astype(float), cfg.rsi_period)
        if np.isnan(rsi):
            return self._no_signal(last_close, reason="insufficient_rsi",
                                   regime=regime.value, rsi=rsi)

        if last_close < lower and rsi < cfg.rsi_oversold:
            # Band deviation score
            dist_atr = abs(last_close - mid) / atr_val if atr_val > 0 else 0.0
            dev_score = 0.0 if dist_atr <= 0.8 else min(0.30, (dist_atr - 0.8) * 0.25)
            rsi_score = 0.20
            score = round(min(1.0, dev_score + rsi_score), 4)
            if score < cfg.mr_score_threshold:
                return self._no_signal(last_close, reason="mr_score_below_threshold",
                                       score=score, regime=regime.value, rsi=rsi)
            # SL/TP en paso-size escalado
            step = self._resolve_step_for_regime(self.symbol, regime, cfg.step_size_pct)
            if step is None:
                return self._no_signal(last_close, reason="step_size_unavailable")
            step_value = step * last_close
            sl_distance = step_value * cfg.step_scale[regime.value]
            tp_distance = step_value * cfg.band_multiplier  # target = banda media
            entry = last_close
            sl = entry - sl_distance
            tp = entry + tp_distance
            logger.info(
                "VolV3 CALM LONG: close=%.5f < lower=%.5f (RSI=%.1f, "
                "atr=%.5f, step=%.8f, score=%.3f)", last_close, lower, rsi,
                atr_val, step, score)
            return Signal(
                type=SignalType.LONG, symbol=self.symbol,
                entry_price=entry, stop_loss=sl, take_profit=tp,
                duration_seconds=cfg.max_duration, confidence=score,
                metadata={
                    "strategy": "volatility_v3",
                    "regime": regime.value,
                    "raw_regime": regime.value,
                    "vol_ratio": 0.0,
                    "atr_value": round(atr_val, 5),
                    "step_size": step,
                    "rationale": "mean_reversion",
                },
            )

        if last_close > upper and rsi > cfg.rsi_overbought:
            dist_atr = abs(last_close - mid) / atr_val if atr_val > 0 else 0.0
            dev_score = 0.0 if dist_atr <= 0.8 else min(0.30, (dist_atr - 0.8) * 0.25)
            rsi_score = 0.20
            score = round(min(1.0, dev_score + rsi_score), 4)
            if score < cfg.mr_score_threshold:
                return self._no_signal(last_close, reason="mr_score_below_threshold",
                                       score=score, regime=regime.value, rsi=rsi)
            step = self._resolve_step_for_regime(self.symbol, regime, cfg.step_size_pct)
            if step is None:
                return self._no_signal(last_close, reason="step_size_unavailable")
            step_value = step * last_close
            sl_distance = step_value * cfg.step_scale[regime.value]
            tp_distance = step_value * cfg.band_multiplier
            entry = last_close
            sl = entry + sl_distance
            tp = entry - tp_distance
            logger.info(
                "VolV3 CALM SHORT: close=%.5f > upper=%.5f (RSI=%.1f, "
                "atr=%.5f, step=%.8f, score=%.3f)", last_close, upper, rsi,
                atr_val, step, score)
            return Signal(
                type=SignalType.SHORT, symbol=self.symbol,
                entry_price=entry, stop_loss=sl, take_profit=tp,
                duration_seconds=cfg.max_duration, confidence=score,
                metadata={
                    "strategy": "volatility_v3",
                    "regime": regime.value,
                    "raw_regime": regime.value,
                    "vol_ratio": 0.0,
                    "atr_value": round(atr_val, 5),
                    "step_size": step,
                    "rationale": "mean_reversion",
                },
            )

        return self._no_signal(last_close, reason="no_mr_setup", regime=regime.value, rsi=rsi)

    def _trend_following_signal(
        self, data: pd.DataFrame, atr_val: float, regime: VolatilityRegime,
    ) -> Signal:
        cfg = self.config
        close = data["close"].astype(float)
        ema_fast = calculate_ema(close, period=cfg.ema_fast)
        ema_slow = calculate_ema(close, period=cfg.ema_slow)
        if ema_fast.isna().iloc[-1] or ema_slow.isna().iloc[-1]:
            return self._no_signal(float(close.iloc[-1]),
                                   reason="insufficient_ema", regime=regime.value)

        fast_val = float(ema_fast.iloc[-1])
        slow_val = float(ema_slow.iloc[-1])
        macd_line, signal_line, hist = self._compute_macd(close)
        if np.isnan(hist):
            return self._no_signal(float(close.iloc[-1]),
                                   reason="insufficient_macd", regime=regime.value)

        # Score EMA cross
        if atr_val <= 0:
            ema_score = 0.0
        else:
            sep_atr = abs(fast_val - slow_val) / atr_val
            ema_score = 0.0 if sep_atr <= 0.05 else min(0.25, (sep_atr - 0.05) * 5.0)

        # Score MACD histogram
        if atr_val <= 0:
            macd_score = 0.0
        else:
            hist_atr = abs(hist) / atr_val
            macd_score = 0.0 if hist_atr <= 0.01 else min(0.25, (hist_atr - 0.01) * 25.0)

        threshold = (cfg.stress_score_threshold if regime == VolatilityRegime.STRESS
                     else cfg.tf_score_threshold)
        last_close = float(close.iloc[-1])

        if fast_val > slow_val and hist > 0:
            score = round(min(1.0, ema_score + macd_score), 4)
            if score < threshold:
                return self._no_signal(last_close, reason="tf_score_below_threshold",
                                       score=score, regime=regime.value)
            # SL/TP en paso-size escalado
            step = self._resolve_step_for_regime(self.symbol, regime, cfg.step_size_pct)
            if step is None:
                return self._no_signal(last_close, reason="step_size_unavailable")
            step_value = step * last_close
            sl_distance = step_value * cfg.step_scale[regime.value]
            tp_distance = step_value * cfg.step_scale[regime.value] * 1.5
            trailing = entry - step_value * cfg.step_scale[regime.value] if 'entry' in dir() else last_close
            entry = last_close
            sl = entry - sl_distance
            tp = entry + tp_distance
            logger.info(
                "VolV3 %s LONG: ema_f=%.5f > ema_s=%.5f (hist=%.6f, "
                "atr=%.5f, step=%.8f, score=%.3f)", regime.value, fast_val, slow_val,
                hist, atr_val, step, score)
            return Signal(
                type=SignalType.LONG, symbol=self.symbol,
                entry_price=entry, stop_loss=sl, take_profit=tp,
                duration_seconds=cfg.max_duration, confidence=score,
                metadata={
                    "strategy": "volatility_v3",
                    "regime": regime.value,
                    "raw_regime": regime.value,
                    "vol_ratio": 0.0,
                    "atr_value": round(atr_val, 5),
                    "step_size": step,
                    "rationale": "trend_following",
                },
            )

        if fast_val < slow_val and hist < 0:
            score = round(min(1.0, ema_score + macd_score), 4)
            if score < threshold:
                return self._no_signal(last_close, reason="tf_score_below_threshold",
                                       score=score, regime=regime.value)
            step = self._resolve_step_for_regime(self.symbol, regime, cfg.step_size_pct)
            if step is None:
                return self._no_signal(last_close, reason="step_size_unavailable")
            step_value = step * last_close
            sl_distance = step_value * cfg.step_scale[regime.value]
            tp_distance = step_value * cfg.step_scale[regime.value] * 1.5
            entry = last_close
            sl = entry + sl_distance
            tp = entry - tp_distance
            logger.info(
                "VolV3 %s SHORT: ema_f=%.5f < ema_s=%.5f (hist=%.6f, "
                "atr=%.5f, step=%.8f, score=%.3f)", regime.value, fast_val, slow_val,
                hist, atr_val, step, score)
            return Signal(
                type=SignalType.SHORT, symbol=self.symbol,
                entry_price=entry, stop_loss=sl, take_profit=tp,
                duration_seconds=cfg.max_duration, confidence=score,
                metadata={
                    "strategy": "volatility_v3",
                    "regime": regime.value,
                    "raw_regime": regime.value,
                    "vol_ratio": 0.0,
                    "atr_value": round(atr_val, 5),
                    "step_size": step,
                    "rationale": "trend_following",
                },
            )

        return self._no_signal(last_close, reason="no_tf_setup", regime=regime.value,
                               ema_fast=fast_val, ema_slow=slow_val, macd_hist=hist)

    # ------------------------------------------------------------------ #
    #  Public interface                                                 #
    # ------------------------------------------------------------------ #

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        if len(data) < self.config.min_candles:
            return Signal(
                type=SignalType.NO_SIGNAL, symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "insufficient_candles", "candles": len(data)},
            )

        atr_val, vol_ratio = self._compute_vol_ratio(data)
        if np.isnan(vol_ratio) or atr_val <= 0:
            last = float(data["close"].iloc[-1]) if len(data) else 0.0
            return self._no_signal(last, reason="insufficient_vol_data",
                                   regime=self._regime.value)

        raw = self._classify(vol_ratio)
        regime = self._update_state_machine(raw)
        base_meta = {
            "strategy": "volatility_v3",
            "regime": regime.value,
            "raw_regime": raw.value,
            "vol_ratio": round(vol_ratio, 4),
            "atr_value": round(atr_val, 5),
        }

        if regime == VolatilityRegime.PANIC:
            return Signal(
                type=SignalType.NO_SIGNAL, symbol=self.symbol,
                entry_price=float(data["close"].iloc[-1]),
                stop_loss=0, take_profit=0, duration_seconds=0, confidence=0.0,
                metadata={**base_meta, "reason": "panic_sit_out"},
            )

        if regime == VolatilityRegime.CALM:
            mid, upper, lower = self._atr_bands(data, atr_val)
            if np.isnan(mid):
                return self._no_signal(float(data["close"].iloc[-1]),
                                       reason="insufficient_ema_for_bands",
                                       **base_meta)
            sig = self._mean_reversion_signal(data, mid, upper, lower, atr_val, regime)
            sig.metadata = {**base_meta, **sig.metadata}
            return sig

        sig = self._trend_following_signal(data, atr_val, regime)
        sig.metadata = {**base_meta, **sig.metadata}
        return sig

    def get_win_probability(self, signal: Signal) -> float:
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0
        regime = (signal.metadata or {}).get("regime", "NORMAL")
        # Base probabilities ajustadas por régimen
        base_prob = {"CALM": 0.58, "NORMAL": 0.55, "STRESS": 0.52, "PANIC": 0.0}.get(regime, 0.55)
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.12
        prob = min(0.68, max(0.48, base_prob + adj))
        return round(prob, 4)

    # Helpers estáticos
    @staticmethod
    def _no_signal(price: float, **extra: object) -> Signal:
        return Signal(
            type=SignalType.NO_SIGNAL, symbol="", entry_price=price,
            stop_loss=0, take_profit=0, duration_seconds=0, confidence=0.0,
            metadata=extra,
        )
