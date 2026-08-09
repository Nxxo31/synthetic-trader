"""VolatilityStrategyV2 — ATR-band dinámico con vol regime detection (B-1).

Estrategia adaptativa que detecta el régimen de volatilidad del mercado usando
ATR normalizado y adapta su comportamiento por régimen:

- **CALM**   (vol_ratio < 0.8): mean-reversion. Bounce de bandas ATR + confirmación
  RSI (oversold/overbought). Stop wide (1.5× ATR).
- **NORMAL** (0.8 ≤ vol_ratio < 1.3): trend-following. EMA rápida vs lenta + MACD.
  Stop medio (2× ATR).
- **STRESS** (1.3 ≤ vol_ratio < 1.6): trend-following con filtro más estricto
  (mayor score threshold). Stop wide (3× ATR).
- **PANIC**  (vol_ratio ≥ 1.6): sit-out. No se generan señales (riesgo incontrolable).

Vol ratio = ATR(14) / rolling_median(ATR(14), 252)
       → normaliza vol contra su baseline histórico de ~252 candles.

State machine con **persistence days**: una transición de régimen solo se
confirma tras N candles consecutivas en el nuevo régimen (evita flicker):
    CALM=20, NORMAL=15, STRESS=5, PANIC=2.

Exit: ATR trailing stop dinámico — ``trailing_stop = extrema_desde_entry
       − (ATR × multiplier_regime)``. El multiplier varía por régimen.

Implementa la misma interface que el resto de estrategias:
    generate_signal(data) -> Signal
    get_win_probability(signal) -> float

Referencias (research findings B-1):
- ATR: rolling mean de True Range
  (TR = max(high−low, |high−prev_close|, |low−prev_close|))
- Regime state machine con persistence evita señales espurias en transiciones.
- Trailing stop ATR — multiplier mayor en alta vol (evita noise stops).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from src.analysis.indicators import calculate_atr, calculate_ema
from src.strategies.base import Signal, SignalType, Strategy

logger = logging.getLogger(__name__)


class VolatilityRegime(Enum):
    """Régimen de volatilidad detectado.

    ORDEN de calma → pánico: CALM < NORMAL < STRESS < PANIC.
    ``introduce_order`` permite comparar regímenes numéricamente.
    """

    CALM = "CALM"
    NORMAL = "NORMAL"
    STRESS = "STRESS"
    PANIC = "PANIC"

    @property
    def severity(self) -> int:
        """Orden del régimen (0 CALM → 3 PANIC) para comparaciones."""
        return {"CALM": 0, "NORMAL": 1, "STRESS": 2, "PANIC": 3}[self.value]


@dataclass
class VolatilityV2Config:
    """Configuración de VolatilityStrategyV2 (B-1).

    Los thresholds de régimen están en unidades de vol_ratio y los persistence
    days en número de candles consecutivas requeridas para confirmar transición.
    """

    # ATR / bands ----------------------------------------------------------
    atr_period: int = 14
    ema_fast: int = 9
    ema_slow: int = 21
    band_multiplier: float = 2.0  # ATRs alrededor de EMA slow para bands mean-rev
    min_candles: int = 50  # necesita >= 252 para vol_ratio; antes de eso NO_SIGNAL
    max_duration: int = 900  # segundos (15 min — alineado con v1)

    # Vol regime -----------------------------------------------------------
    vol_median_window: int = 252  # rolling median para normalizar ATR
    calm_threshold: float = 0.8   # vol_ratio < calm → CALM
    stress_threshold: float = 1.3  # calm <= vol_ratio < stress → NORMAL
    panic_threshold: float = 1.6   # stress <= vol_ratio < panic → STRESS; >= → PANIC
    # Persistence days (candles consecutivas) por régimen destino:
    persistence: dict[str, int] = field(
        default_factory=lambda: {
            "CALM": 20,
            "NORMAL": 15,
            "STRESS": 5,
            "PANIC": 2,
        }
    )

    # Exit — ATR trailing stop por régimen ---------------------------------
    trail_mult_calm: float = 1.5
    trail_mult_normal: float = 2.0
    trail_mult_stress: float = 3.0
    # PANIC no genera señales → no se usa multiplier

    # Entry — mean-reversion (CALM) ---------------------------------------
    rsi_period: int = 14
    rsi_oversold: float = 30.0   # LONG bounce exige RSI < oversold
    rsi_overbought: float = 70.0  # SHORT bounce exige RSI > overbought
    # Bandas ATR: LONG si close < lower_band; SHORT si close > upper_band
    mr_score_threshold: float = 0.40

    # Entry — trend-following (NORMAL/STRESS) ------------------------------
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    tf_score_threshold: float = 0.45
    stress_score_threshold: float = 0.55  #_STRESS pide más convicción


class VolatilityStrategyV2(Strategy):
    """ATR-band dinámico con vol regime detection (B-1).

    El flujo por candle:

    1. Calcular ATR(14) y vol_ratio = ATR / rolling_median(ATR, 252).
    2. Clasificar régimen crudo por thresholds.
    3. Actualizar el state machine con persistence (la transición solo se
       confirma tras N candles consecutivas en el nuevo régimen).
    4. Generar señal según régimen **confirmado**:
       - CALM   → mean-reversion (band bounce + RSI)
       - NORMAL → trend-following (EMA cross + MACD)
       - STRESS → trend-following (mismo, threshold más alto)
       - PANIC  → sit-out (NO_SIGNAL)
    5. Exit: ATR trailing stop con multiplier por régimen confirmado.
    """

    def __init__(
        self,
        symbol: str = "R_100",
        config: VolatilityV2Config | None = None,
    ) -> None:
        super().__init__("VolatilityV2", symbol)
        self.config = config or VolatilityV2Config()
        # State machine: régimen confirmado actual + contador de persistencia
        self._regime: VolatilityRegime = VolatilityRegime.NORMAL
        self._pending: VolatilityRegime = VolatilityRegime.NORMAL
        self._pending_count: int = 0
        # Tracking de trailing stop (highest/lowest desde entry) por posición abierta
        self._position_extreme: float | None = None
        self._position_dir: SignalType = SignalType.NO_SIGNAL

    # ------------------------------------------------------------------ #
    #  ATR / vol ratio                                                  #
    # ------------------------------------------------------------------ #

    def _compute_vol_ratio(self, data: pd.DataFrame) -> tuple[float, float]:
        """Calcula ATR y vol_ratio = ATR / rolling_median(ATR, 252).

        Returns:
            (atr_value, vol_ratio). Ambos ``nan`` si datos insuficientes.
        """
        atr_series = calculate_atr(data, period=self.config.atr_period)
        if len(atr_series) < self.config.vol_median_window + 1:
            return (np.nan, np.nan)

        atr_now = float(atr_series.iloc[-1])
        # ATR previo al último candle para evitar self-reference en la mediana
        window = atr_series.iloc[-(self.config.vol_median_window + 1):-1].dropna()
        if len(window) < self.config.vol_median_window // 2:
            return (atr_now, np.nan)

        median_atr = float(window.median())
        if median_atr <= 0:
            return (atr_now, np.nan)

        return (atr_now, atr_now / median_atr)

    # ------------------------------------------------------------------ #
    #  Regime state machine                                            #
    # ------------------------------------------------------------------ #

    def _classify(self, vol_ratio: float) -> VolatilityRegime:
        """Clasifica vol_ratio en régimen crudo (sin persistence)."""
        if vol_ratio < self.config.calm_threshold:
            return VolatilityRegime.CALM
        if vol_ratio < self.config.stress_threshold:
            return VolatilityRegime.NORMAL
        if vol_ratio < self.config.panic_threshold:
            return VolatilityRegime.STRESS
        return VolatilityRegime.PANIC

    def _update_state_machine(self, raw: VolatilityRegime) -> VolatilityRegime:
        """Actualiza el state machine con persistence days.

        La transición al régimen ``raw`` solo se confirma cuando se observan
        ``persistence[raw]`` candles consecutivas en ese régimen. Mientras
        tanto el régimen confirmado anterior sigue activo.
        """
        if raw == self._regime:
            # seguir en mismo régimen: reset pending
            if self._pending != self._regime:
                self._pending = self._regime
                self._pending_count = 0
            return self._regime

        # raw != régimen confirmado → acumular pending
        if raw == self._pending:
            self._pending_count += 1
        else:
            self._pending = raw
            self._pending_count = 1

        required = self.config.persistence.get(raw.value, 5)
        if self._pending_count >= required:
            self._regime = raw
            logger.debug(
                "VolV2 régimen → %s (persistence=%d/%d)",
                raw.value, self._pending_count, required,
            )
        return self._regime

    # ------------------------------------------------------------------ #
    #  Indicadores auxiliares (RSI / MACD inline — no toca indicators.py) #
    # ------------------------------------------------------------------ #

    def _compute_rsi(self, close: pd.Series, period: int) -> float:
        """RSI (Wilder) del último candle. ``nan`` si datos insuficientes."""
        if len(close) < period + 1:
            return float("nan")
        delta = close.astype(float).diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        # Wilder smoothing ≈ ewm(alpha=1/period)
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        last_gain = float(avg_gain.iloc[-1])
        last_loss = float(avg_loss.iloc[-1])
        if last_loss == 0:
            return 100.0 if last_gain > 0 else 50.0
        rs = last_gain / last_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _compute_macd(self, close: pd.Series) -> tuple[float, float, float]:
        """MACD(line, signal, histogram) del último candle."""
        fast = self.config.macd_fast
        slow = self.config.macd_slow
        sig = self.config.macd_signal
        if len(close) < slow + sig:
            return (float("nan"), float("nan"), float("nan"))
        ema_fast = close.astype(float).ewm(span=fast, adjust=False).mean()
        ema_slow = close.astype(float).ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=sig, adjust=False).mean()
        hist = macd_line - signal_line
        return (float(macd_line.iloc[-1]), float(signal_line.iloc[-1]),
                float(hist.iloc[-1]))

    def _atr_bands(self, data: pd.DataFrame, atr_val: float) -> tuple[float, float, float]:
        """EMA slow + bandas ATR (mid, upper, lower) para mean-reversion."""
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
        lower: float, atr_val: float,
    ) -> Signal:
        """Mean-reversion para CALM: ATR-band bounce + confirmación RSI.

        LONG si close < lower Y RSI < oversold (oversold bounce).
        SHORT si close > upper Y RSI > overbought (overbought fade).
        Score = band_deviation_score + rsi_confirmation_score.
        """
        cfg = self.config
        last_close = float(data["close"].iloc[-1])
        rsi = self._compute_rsi(data["close"].astype(float), cfg.rsi_period)
        if np.isnan(rsi):
            return self._no_signal(last_close, reason="insufficient_rsi",
                                   regime=self._regime.value, rsi=rsi)

        # Score de desviación de banda (0..0.30)
        if atr_val <= 0:
            dev_score, dist_atr = 0.0, 0.0
        else:
            dist_atr = abs(last_close - mid) / atr_val
            dev_score = 0.0 if dist_atr <= 0.8 else min(0.30, (dist_atr - 0.8) * 0.25)

        if last_close < lower and rsi < cfg.rsi_oversold:
            rsi_score = 0.20  # confirmación RSI
            score = round(min(1.0, dev_score + rsi_score), 4)
            if score < cfg.mr_score_threshold:
                return self._no_signal(
                    last_close, reason="mr_score_below_threshold", score=score,
                    regime=cfg.CALM.value, rsi=rsi)
            entry = last_close
            sl_mult = cfg.trail_mult_calm
            sl = entry - atr_val * sl_mult
            tp = entry + atr_val * cfg.band_multiplier  # target = banda media
            trailing = entry - atr_val * sl_mult
            logger.info(
                "VolV2 CALM LONG: close=%.5f < lower=%.5f (RSI=%.1f, "
                "atr=%.5f, score=%.3f)", last_close, lower, rsi, atr_val, score)
            return self._makelong(entry, sl, tp, score, atr_val, sl_mult,
                                  trailing, mid, upper, lower, rsi=rsi)

        if last_close > upper and rsi > cfg.rsi_overbought:
            rsi_score = 0.20
            score = round(min(1.0, dev_score + rsi_score), 4)
            if score < cfg.mr_score_threshold:
                return self._no_signal(
                    last_close, reason="mr_score_below_threshold", score=score,
                    regime=cfg.CALM.value, rsi=rsi)
            entry = last_close
            sl_mult = cfg.trail_mult_calm
            sl = entry + atr_val * sl_mult
            tp = entry - atr_val * cfg.band_multiplier
            trailing = entry + atr_val * sl_mult
            logger.info(
                "VolV2 CALM SHORT: close=%.5f > upper=%.5f (RSI=%.1f, "
                "atr=%.5f, score=%.3f)", last_close, upper, rsi, atr_val, score)
            return self._makeshort(entry, sl, tp, score, atr_val, sl_mult,
                                   trailing, mid, upper, lower, rsi=rsi)

        return self._no_signal(last_close, reason="no_mr_setup",
                               regime=self._regime.value, rsi=rsi)

    def _trend_following_signal(
        self, data: pd.DataFrame, atr_val: float, regime: VolatilityRegime,
    ) -> Signal:
        """Trend-following para NORMAL/STRESS: EMA cross + MACD histogram.

        LONG si EMA fast > EMA slow Y MACD histogram > 0 (momentum up).
        SHORT si EMA fast < EMA slow Y MACD histogram < 0 (momentum down).
        Score = ema_cross_score (0..0.25) + macd_hist_score (0..0.25).
        STRESS usa threshold más alto (stress_score_threshold).
        """
        cfg = self.config
        close = data["close"].astype(float)
        ema_fast = calculate_ema(close, period=cfg.ema_fast)
        ema_slow = calculate_ema(close, period=cfg.ema_slow)
        if ema_fast.isna().iloc[-1] or ema_slow.isna().iloc[-1]:
            return self._no_signal(float(close.iloc[-1]),
                                   reason="insufficient_ema",
                                   regime=regime.value)

        fast_val = float(ema_fast.iloc[-1])
        slow_val = float(ema_slow.iloc[-1])
        macd_line, signal_line, hist = self._compute_macd(close)
        if np.isnan(hist):
            return self._no_signal(float(close.iloc[-1]),
                                   reason="insufficient_macd", regime=regime.value)

        # Score EMA cross (separación en ATR units)
        if atr_val <= 0:
            ema_score = 0.0
        else:
            sep_atr = abs(fast_val - slow_val) / atr_val
            ema_score = 0.0 if sep_atr <= 0.05 else min(0.25, (sep_atr - 0.05) * 5.0)

        # Score MACD histogram (normalización robusta por ATR)
        if atr_val <= 0:
            macd_score = 0.0
        else:
            hist_atr = abs(hist) / atr_val
            macd_score = 0.0 if hist_atr <= 0.01 else min(0.25, (hist_atr - 0.01) * 25.0)

        threshold = (cfg.stress_score_threshold if regime == VolatilityRegime.STRESS
                     else cfg.tf_score_threshold)
        mult_map = {
            VolatilityRegime.NORMAL: cfg.trail_mult_normal,
            VolatilityRegime.STRESS: cfg.trail_mult_stress,
        }
        sl_mult = mult_map[regime]
        last_close = float(close.iloc[-1])

        if fast_val > slow_val and hist > 0:
            score = round(min(1.0, ema_score + macd_score), 4)
            if score < threshold:
                return self._no_signal(last_close, reason="tf_score_below_threshold",
                                       score=score, regime=regime.value)
            entry = last_close
            sl = entry - atr_val * sl_mult
            tp = entry + atr_val * sl_mult * 1.5  # TP 1.5× el stop ATR (R:1.5)
            trailing = entry - atr_val * sl_mult
            logger.info(
                "VolV2 %s LONG: ema_f=%.5f > ema_s=%.5f (hist=%.6f, "
                "atr=%.5f, score=%.3f)", regime.value, fast_val, slow_val,
                hist, atr_val, score)
            return self._makelong(entry, sl, tp, score, atr_val, sl_mult,
                                  trailing, fast=fast_val, slow=slow_val,
                                  macd_line=macd_line, signal_line=signal_line,
                                  hist=hist)

        if fast_val < slow_val and hist < 0:
            score = round(min(1.0, ema_score + macd_score), 4)
            if score < threshold:
                return self._no_signal(last_close, reason="tf_score_below_threshold",
                                       score=score, regime=regime.value)
            entry = last_close
            sl = entry + atr_val * sl_mult
            tp = entry - atr_val * sl_mult * 1.5
            trailing = entry + atr_val * sl_mult
            logger.info(
                "VolV2 %s SHORT: ema_f=%.5f < ema_s=%.5f (hist=%.6f, "
                "atr=%.5f, score=%.3f)", regime.value, fast_val, slow_val,
                hist, atr_val, score)
            return self._makeshort(entry, sl, tp, score, atr_val, sl_mult,
                                   trailing, fast=fast_val, slow=slow_val,
                                   macd_line=macd_line, signal_line=signal_line,
                                   hist=hist)

        return self._no_signal(last_close, reason="no_tf_setup", regime=regime.value,
                               ema_fast=fast_val, ema_slow=slow_val, macd_hist=hist)

    # ------------------------------------------------------------------ #
    #  Public interface                                                 #
    # ------------------------------------------------------------------ #

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Analiza candles y produce señal adaptada al régimen de volatilidad.

        Args:
            data: DataFrame con columnas ``high`` ``low`` ``close``.

        Returns:
            Signal con entry/SL/TP/confidence y metadata (régimen, vol_ratio,
            trailing_stop). ``NO_SIGNAL`` en PANIC o cuando no hay setup.
        """
        if len(data) < self.config.min_candles:
            return Signal(
                type=SignalType.NO_SIGNAL, symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "insufficient_candles",
                          "candles": len(data)},
            )

        atr_val, vol_ratio = self._compute_vol_ratio(data)
        if np.isnan(vol_ratio) or atr_val <= 0:
            last = float(data["close"].iloc[-1]) if len(data) else 0.0
            return self._no_signal(last, reason="insufficient_vol_data",
                                   regime=self._regime.value)

        raw = self._classify(vol_ratio)
        regime = self._update_state_machine(raw)
        base_meta = {
            "strategy": "volatility_v2",
            "regime": regime.value,
            "raw_regime": raw.value,
            "vol_ratio": round(vol_ratio, 4),
            "atr_value": round(atr_val, 5),
        }

        # SIT-OUT en PANIC -------------------------------------------------
        if regime == VolatilityRegime.PANIC:
            return Signal(
                type=SignalType.NO_SIGNAL, symbol=self.symbol,
                entry_price=float(data["close"].iloc[-1]),
                stop_loss=0, take_profit=0, duration_seconds=0, confidence=0.0,
                metadata={**base_meta, "reason": "panic_sit_out"},
            )

        # CALM → mean-reversion -------------------------------------------
        if regime == VolatilityRegime.CALM:
            mid, upper, lower = self._atr_bands(data, atr_val)
            if np.isnan(mid):
                return self._no_signal(float(data["close"].iloc[-1]),
                                       reason="insufficient_ema_for_bands",
                                       **base_meta)
            sig = self._mean_reversion_signal(data, mid, upper, lower, atr_val)
            sig.metadata = {**base_meta, **sig.metadata}
            return sig

        # NORMAL / STRESS → trend-following -------------------------------
        sig = self._trend_following_signal(data, atr_val, regime)
        sig.metadata = {**base_meta, **sig.metadata}
        return sig

    def get_win_probability(self, signal: Signal) -> float:
        """Probabilidad de win estimada para Kelly sizing.

        Base rate calibrada por tipo de setup:
        - Mean-reversion (régimen CALM): 0.60 (high base, payoff bajo).
        - Trend-following (NORMAL/STRESS): 0.55 (momentum sigue más a menudo).
        Ajustada ±6% por confidence, acotada a [0.48, 0.68].
        """
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0
        regime = (signal.metadata or {}).get("regime", "NORMAL")
        base_prob = 0.60 if regime == "CALM" else 0.55
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.12
        prob = min(0.68, max(0.48, base_prob + adj))
        return round(prob, 4)

    # ------------------------------------------------------------------ #
    #  Helpers — construcción de señales                               #
    # ------------------------------------------------------------------ #

    def _makelong(
        self, entry: float, sl: float, tp: float, score: float,
        atr_val: float, sl_mult: float, trailing: float, **extra: object,
    ) -> Signal:
        return Signal(
            type=SignalType.LONG, symbol=self.symbol,
            entry_price=entry, stop_loss=sl, take_profit=tp,
            duration_seconds=self.config.max_duration, confidence=score,
            metadata={
                "regime": self._regime.value,
                "atr_value": atr_val,
                "trail_multiplier": sl_mult,
                "trailing_stop": trailing,
                **extra,
            },
        )

    def _makeshort(
        self, entry: float, sl: float, tp: float, score: float,
        atr_val: float, sl_mult: float, trailing: float, **extra: object,
    ) -> Signal:
        return Signal(
            type=SignalType.SHORT, symbol=self.symbol,
            entry_price=entry, stop_loss=sl, take_profit=tp,
            duration_seconds=self.config.max_duration, confidence=score,
            metadata={
                "regime": self._regime.value,
                "atr_value": atr_val,
                "trail_multiplier": sl_mult,
                "trailing_stop": trailing,
                **extra,
            },
        )

    @staticmethod
    def _no_signal(price: float, **extra: object) -> Signal:
        return Signal(
            type=SignalType.NO_SIGNAL, symbol="",  # symbol set by caller in dispatch
            entry_price=price, stop_loss=0, take_profit=0,
            duration_seconds=0, confidence=0.0,
            metadata=extra,
        )
