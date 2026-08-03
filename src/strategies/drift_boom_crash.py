"""Drift Boom/Crash strategy — operar a favor del drift estructural.

Los índices **Boom** y **Crash** de Deriv tienen un drift estadístico
**estructural por construcción** (ver ``investigacion-estrategias-bots-multi-
mercado.md`` §2.2 y ``research-crypto-operator-deriv.md`` §6.2):

- **Crash** deriva hacia **arriba** entre caídas repentinas (crashes).
  Operar a favor del drift = **comprar (LONG)** después de un crash reciente.
- **Boom** deriva hacia **abajo** entre picos repentinos (booms).
  Operar a favor del drift = **vender (SHORT)** después de un boom reciente.

El drift no es predecible en términos de *cuándo* ocurre el siguiente spike,
pero la **dirección estadística** entre spikes es positiva por diseño del
índice. La gestión del riesgo comes from operar **justo después** de un
spike reciente (zona segura temporal) — reduce la probabilidad de que otro
spike venga de inmediato.

Parámetros recomendados de la investigación (``§2.2``):

| Parámetro     | Valor recomendado                                              |
|---------------|----------------------------------------------------------------|
| Instrumento   | Crash 1000 o Boom 500 (menos margen para spikes)            |
| Lote          | 0.20 (mínimo en Deriv para Boom/Crash)                       |
| Dirección     | Crash → LONG (drift alcista); Boom → SHORT (drift bajista)  |
| Entrada       | Tras un crash/pico reciente (zona segura temporal)         |
| TP            | Antes de la zona media del siguiente pico esperado           |
| SL            | 100-150 ticks (~$2-$3 de riesgo en cuenta $10)              |
| Frecuencia    | 1-3 operaciones/día                                          |

Esta estrategia:

1. **Detecta el drift estructural** vía regresión lineal de los retornos
   recientes — la pendiente confirmada nos dice que el drift está activo.
2. **Detecta el spike reciente** (crash en Crash o boom en Boom) via
   outlier en los retornos — un movimiento > N desviaciones estándar en
   sentido **opuesto** al drift, seguido de estabilización. Entrar aquí
   es la "zona segura temporal".
3. **Genera señal a favor del drift** (LONG en Crash, SHORT en Boom) y
   **rechaza señales contra drift** (no vender en Crash aunque el precio
   suba rápido — esperar al spike siguiente y operar el rebote).
4. **SL/TP** en *ticks* derivados de ATR aproximado al tamaño de un
   tick del Boom/Crash, dentro del rango recomendado (100-150).

Implementa la misma interfaz que RangeBreakStrategy / VolatilityStrategy:
    generate_signal(data) -> Signal
    get_win_probability(signal) -> float

Lote mínimo y position sizing se gestionan en ``RiskManager`` (Kelly
dinámico, hard cap 1.5% per trade). Aquí solo se emite confidence.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from src.strategies.base import Signal, SignalType, Strategy
from src.analysis.indicators import calculate_atr

logger = logging.getLogger(__name__)


# Drift direccional por familia de símbolo.
# Crash → LONG (drift alcista estructural)
# Boom  → SHORT (drift bajista estructural)
# El "drift bias" se interpreta como: +1 LONG, -1 SHORT, 0 desconocido.
def _symbol_drift_bias(symbol: str) -> int:
    s = symbol.upper().strip()
    if s.startswith("CRASH"):
        return 1   # LONG — deriva hacia arriba
    if s.startswith("BOOM"):
        return -1  # SHORT — deriva hacia abajo
    return 0


# Familia Boom/Crash soportada (para validación de símbolo).
BOOM_CRASH_SYMBOLS: frozenset[str] = frozenset({
    "BOOM300N", "BOOM500", "BOOM1000", "BOOM1000W",
    "CRASH300N", "CRASH500", "CRASH1000", "CRASH1000W",
})


@dataclass
class DriftBoomCrashConfig:
    """Configuración para la estrategia Drift Boom/Crash.

    Atributos:
        drift_window:               Ventana (velas) para estimar la pendiente
                                    del drift vía regresión lineal (por defecto
                                    50). Debe ser grande para captar la tendencia
                                    estructural por encima del ruido de los
                                    spikes.
        spike_detection_window:     Ventana para detectar el spike reciente
                                    (por defecto 20). Miramos los últimos N
                                    retornos para encontrar el outlier.
        spike_sigma_threshold:      Múltiplo de desviación estándar para
                                    clasificar un retorno como spike (por
                                    defecto 3.0 — tres sigmas).
        min_candles:                 Mínimo de velas antes de emitir señal
                                    (por defecto 60 — más alto que Step Index
                                    porque necesitamos window de drift + detección).
        sl_ticks:                   Stop loss en ticks absolutos (por defecto
                                    120 — dentro del rango recomendado 100-150).
        tp_ticks:                    Take profit en ticks absolutos (por defecto
                                    180 — RR ~1.5:1, "antes del siguiente pico").
        tick_value:                 Valor en precio de un tick. Si es ``None``,
                                    se infiere de ATR (atr / 100 ≈ valor de tick
                                    típico en Boom/Crash).
        max_duration:               Duración máxima de la posición en segundos
                                    (por defecto 1200 = 20 min — más largo que
                                    otras estrategias porque el drift tarda en
                                    materializarse).
        cooldown_after_spike:       Velas de enfriamiento después de detectar
                                    el spike antes de entrar (por defecto 2 —
                                    dejar estabilizar).
        score_threshold:            Mínimo de confidence compuesta para emitir
                                    señal (por defecto 0.35).
        require_drift_confirmation: Si True (por defecto), require que la
                                    pendiente del drift confirmare la dirección
                                    estructural esperada del símbolo. Si False,
                                    confía ciegamente en la dirección por familia.
    """
    drift_window: int = 50
    spike_detection_window: int = 20
    spike_sigma_threshold: float = 3.0
    min_candles: int = 60
    sl_ticks: float = 120.0
    tp_ticks: float = 180.0
    tick_value: float | None = None
    max_duration: int = 1200
    cooldown_after_spike: int = 2
    score_threshold: float = 0.35
    require_drift_confirmation: bool = True


class DriftBoomCrashStrategy(Strategy):
    """Estrategia de drift direccional para índices Boom/Crash de Deriv.

    Entrada:
    - Después de un spike reciente (crash en Crash o boom en Boom),
      en la dirección del drift estructural: LONG en Crash, SHORT en Boom.
    - Solo entra si el drift medido por regresión está alineado con la
      dirección esperada del símbolo (a menos que
      ``require_drift_confirmation=False``).

    Salida (triple barrier estilo Hummingbot, ver investigación §6.1):
    - SL: 120 ticks por defecto (dentro del rango 100-150 recomendado).
    - TP: 180 ticks por defecto (RR ~1.5:1 — "antes del siguiente pico").
    - Tiempo: 20 min máximo.

    Motivos de NO_SIGNAL:
    - No hay drift direccional confirmado o va contra la estructura esperada.
    - No se detectó spike reciente → no hay zona segura temporal.
    - Enfriamiento post-spike activo.
    - Confidence compuesta por debajo del umbral.

    Lote mínimo en Deriv: 0.20 para Boom/Crash. El position sizing actual
    ocurre en ``RiskManager.position_size_dynamic`` con hard cap 1.5% per
    trade — esta estrategia solo emite confidence para alimentar ese cálculo.
    """

    def __init__(
        self,
        symbol: str = "CRASH1000",
        config: DriftBoomCrashConfig | None = None,
    ) -> None:
        super().__init__("DriftBoomCrash", symbol)
        self.config = config or DriftBoomCrashConfig()
        self._drift_bias = _symbol_drift_bias(symbol)
        if self._drift_bias == 0:
            logger.warning(
                "DriftBoomCrashStrategy: símbolo '%s' no reconocido como "
                "Boom/Crash — drift bias desconocido, estrategia operará "
                "neutramente hasta confirmación de drift",
                symbol,
            )

    # ------------------------------------------------------------------ #
    #  Helpers internos                                                   #
    # ------------------------------------------------------------------ #

    def _estimate_drift(
        self,
        closes: pd.Series,
        window: int,
    ) -> tuple[float, float]:
        """Estima la pendiente del drift por regresión lineal simple.

        Returns:
            (slope, r_squared):
            slope: pendiente de la regresión lineal sobre los retornos
                   logarítmicos recientes. Positiva → drift alcista,
                   negativa → drift bajista. Magnitud relativa al precio.
            r_squared: bondad de ajuste (0-1). Alta = drift limpio.
        """
        if len(closes) < window:
            window = len(closes)

        recent = closes.iloc[-window:].astype(float).dropna()
        n = len(recent)
        if n < 5:
            return (0.0, 0.0)

        # Retornos logarítmicos — estables para regresión
        log_returns = np.log(recent / recent.shift(1)).dropna()
        if len(log_returns) < 5:
            return (0.0, 0.0)

        x = np.arange(len(log_returns), dtype=float)
        y = log_returns.values

        # Regresión lineal simple (OLS): y = a + b*x
        x_mean = x.mean()
        y_mean = y.mean()
        x_dev = x - x_mean
        y_dev = y - y_mean

        ss_xx = (x_dev ** 2).sum()
        if ss_xx == 0:
            return (0.0, 0.0)

        slope = (x_dev * y_dev).sum() / ss_xx
        # r²
        ss_yy = (y_dev ** 2).sum()
        ss_res = ((y - (y_mean + slope * x_dev)) ** 2).sum()
        r_sq = 1.0 - (ss_res / ss_yy) if ss_yy > 0 else 0.0
        r_sq = max(0.0, min(1.0, float(r_sq)))

        return (float(slope), r_sq)

    def _detect_recent_spike(
        self,
        closes: pd.Series,
        window: int,
        sigma_threshold: float,
        expected_spike_direction: int,
    ) -> tuple[bool, int, float, float]:
        """Detecta si hubo un spike reciente en la dirección esperada.

        Un "spike" es un retorno outlier (> sigma_threshold desviaciones
        estándar) en sentido **opuesto** al drift. En Crash (drift LONG),
        un spike es un movimiento brusco hacia abajo (crash). En Boom
        (drift SHORT), un spike es un movimiento brusco hacia arriba.

        Args:
            expected_spike_direction: +1 si el spike esperado es hacia
                                      arriba (Boom), -1 si hacia abajo
                                      (Crash). Equivalente a -drift_bias.

        Returns:
            (spike_detected, bars_since_spike, spike_magnitude_sigma,
             returns_std):
            bars_since_spike: índice relativo (0 = vela actual) del último
                              spike detectado. -1 si ninguno.
        """
        if len(closes) < window + 2:
            return (False, -1, 0.0, 0.0)

        recent = closes.iloc[-(window + 1):].astype(float)
        returns = recent.pct_change().dropna()

        if len(returns) < 5:
            return (False, -1, 0.0, 0.0)

        std = float(returns.std())
        if std == 0 or np.isnan(std):
            return (False, -1, 0.0, 0.0)

        # Buscar el spike más reciente en la dirección esperada
        # (movimiento contrario al drift)
        signed_returns = returns.values * expected_spike_direction
        threshold = sigma_threshold * std

        # Iterar de más reciente a más viejo
        spike_idx = -1
        spike_mag = 0.0
        for i in range(len(signed_returns) - 1, -1, -1):
            r = signed_returns[i]
            if r > threshold:
                # Índice relativo desde la última vela disponible
                bars_since = len(signed_returns) - 1 - i
                spike_idx = bars_since
                spike_mag = float(r / std)
                break

        if spike_idx < 0:
            return (False, -1, 0.0, std)

        return (True, spike_idx, spike_mag, std)

    # ------------------------------------------------------------------ #
    #  Signal generation                                                  #
    # ------------------------------------------------------------------ #

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Analiza velas de Boom/Crash y produce una señal a favor del drift.

        Args:
            data: DataFrame con columnas ``high``, ``low``, ``close``.

        Returns:
            Signal con entry/SL/TP y confidence compuesta (drift + spike).
            ``SignalType.NO_SIGNAL`` cuando no hay drift, no hay spike
            reciente, o el símbolo no es Boom/Crash válido.
        """
        if len(data) < self.config.min_candles:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "insufficient_data", "candles": len(data)},
            )

        closes = data["close"].astype(float)

        # --- 1. Estimar drift estructural ---
        slope, r_sq = self._estimate_drift(closes, self.config.drift_window)

        # Dirección del drift medida vs esperada
        measured_drift_dir = 1 if slope > 0 else (-1 if slope < 0 else 0)
        drift_aligned = (self._drift_bias == 0) or (measured_drift_dir == self._drift_bias)

        if self.config.require_drift_confirmation and not drift_aligned:
            logger.debug(
                "DriftBoomCrash: drift medido (%d, slope=%.2e) no alineado con "
                "bias esperado (%d) — señal omitida",
                measured_drift_dir, slope, self._drift_bias,
            )
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=float(closes.iloc[-1]),
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=0.0,
                metadata={
                    "reason": "drift_not_aligned",
                    "drift_bias": self._drift_bias,
                    "measured_drift_dir": measured_drift_dir,
                    "slope": slope,
                    "r_squared": r_sq,
                },
            )

        # --- 2. Score de drift (confianza en la tendencia estructural) ---
        # Combinar magnitud de slope (normalizada) con r²
        # Magnitud típica de drift es muy pequeña — usamos escala log
        slope_strength = min(1.0, abs(slope) * 1e6)  # heurística
        drift_score = round(slope_strength * (0.5 + 0.5 * r_sq), 4)
        drift_score = max(0.0, min(1.0, drift_score))

        # --- 3. Detectar spike reciente ---
        # Esperamos spikes en dirección opuesta al drift:
        # Crash (drift LONG) → spikes hacia abajo → expected_spike_dir = -1
        # Boom  (drift SHORT) → spikes hacia arriba → expected_spike_dir = +1
        if self._drift_bias != 0:
            expected_spike_dir = -self._drift_bias
        else:
            # Si no sabemos el bias, esperamos spike en dirección opuesta
            # al drift medido
            expected_spike_dir = -measured_drift_dir if measured_drift_dir != 0 else 0

        spike_detected, bars_since, spike_mag, returns_std = self._detect_recent_spike(
            closes,
            self.config.spike_detection_window,
            self.config.spike_sigma_threshold,
            expected_spike_dir,
        )

        if not spike_detected:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=float(closes.iloc[-1]),
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=drift_score,
                metadata={
                    "reason": "no_recent_spike",
                    "drift_bias": self._drift_bias,
                    "slope": slope,
                    "r_squared": r_sq,
                    "drift_score": drift_score,
                },
            )

        # Enfriamiento post-spike
        if bars_since < self.config.cooldown_after_spike:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=float(closes.iloc[-1]),
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=drift_score,
                metadata={
                    "reason": "spike_cooldown",
                    "bars_since_spike": bars_since,
                    "cooldown_required": self.config.cooldown_after_spike,
                    "spike_magnitude_sigma": spike_mag,
                },
            )

        # --- 4. Score de spike (calidad de la zona segura temporal) ---
        # Mayor sigma del spike → más extremo → mejor zona segura (si
        # ha pasado suficiente cooldown). Map [3σ, 8σ] → [0.5, 1.0].
        spike_score = (spike_mag - 3.0) / 5.0
        spike_score = max(0.5, min(1.0, spike_score))

        # --- 5. Confidence compuesta ---
        # Drift aporta 60% (estructura), spike aporta 40% (timing)
        confidence = round(drift_score * 0.6 + spike_score * 0.4, 4)
        confidence = max(0.0, min(1.0, confidence))

        if confidence < self.config.score_threshold:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=float(closes.iloc[-1]),
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=confidence,
                metadata={
                    "reason": "score_below_threshold",
                    "score": confidence,
                    "threshold": self.config.score_threshold,
                    "drift_score": drift_score,
                    "spike_score": spike_score,
                },
            )

        # --- 6. Dirección de la señal = a favor del drift ---
        if self._drift_bias != 0:
            direction = SignalType.LONG if self._drift_bias > 0 else SignalType.SHORT
        elif measured_drift_dir != 0:
            direction = SignalType.LONG if measured_drift_dir > 0 else SignalType.SHORT
        else:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=float(closes.iloc[-1]),
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=confidence,
                metadata={"reason": "direction_indeterminate"},
            )

        # --- 7. SL/TP en ticks ---
        atr_val = float(calculate_atr(data, period=14).iloc[-1])
        if np.isnan(atr_val):
            atr_val = 0.0

        tick_val = self.config.tick_value
        if tick_val is None or tick_val <= 0:
            # Inferir del ATR: Boom/Crash típicamente ~100 ticks ≈ ATR
            # diario; usamos atr/100 como proxy del tick value
            if atr_val > 0:
                tick_val = atr_val / 100.0
            else:
                # Último recurso: 1 pip estándar
                tick_val = 0.0001
                logger.warning(
                    "DriftBoomCrash: tick_value indeducible, usando 0.0001"
                )

        sl_distance = tick_val * self.config.sl_ticks
        tp_distance = tick_val * self.config.tp_ticks

        entry = float(closes.iloc[-1])
        if direction == SignalType.LONG:
            sl = entry - sl_distance
            tp = entry + tp_distance
        else:
            sl = entry + sl_distance
            tp = entry - tp_distance

        logger.info(
            "DriftBoomCrash %s: entry=%.5f, SL=%.5f, TP=%.5f, conf=%.3f, "
            "drift=(slope=%.2e, r²=%.3f, score=%.3f), "
            "spike=(bars_since=%d, mag=%.2fσ, score=%.3f)",
            direction.value, entry, sl, tp, confidence,
            slope, r_sq, drift_score,
            bars_since, spike_mag, spike_score,
        )

        return Signal(
            type=direction,
            symbol=self.symbol,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            duration_seconds=self.config.max_duration,
            confidence=confidence,
            metadata={
                "strategy": "drift_boom_crash",
                "drift_bias": self._drift_bias,
                "measured_drift_dir": measured_drift_dir,
                "drift_aligned": drift_aligned,
                "slope": slope,
                "r_squared": r_sq,
                "drift_score": drift_score,
                "spike_detected": spike_detected,
                "bars_since_spike": bars_since,
                "spike_magnitude_sigma": spike_mag,
                "spike_score": spike_score,
                "tick_value": tick_val,
                "sl_ticks": self.config.sl_ticks,
                "tp_ticks": self.config.tp_ticks,
                "sl_distance": sl_distance,
                "tp_distance": tp_distance,
                "atr_value": atr_val,
                "instrument_family": "crash" if self._drift_bias > 0 else (
                    "boom" if self._drift_bias < 0 else "unknown"
                ),
                # Recordatorio de la regla de lote mínimo Deriv
                "min_lot": 0.20,
            },
        )

    # ------------------------------------------------------------------ #
    #  Win probability                                                    #
    # ------------------------------------------------------------------ #

    def get_win_probability(self, signal: Signal) -> float:
        """Estima la probabilidad de victoria para el sizing Kelly.

        Operar a favor del drift estructural post-spike tiene una base
        empírica alta (~60%) según la investigación.
        Se ajusta ±6% por confidence:
        - confidence = 0.5 → win_prob = base
        - confidence = 1.0 → win_prob = base + 0.06 ( próximo a 0.66)
        - confidence = 0.0 → win_prob = base - 0.06 ( próximo a 0.54)

        Rango final acotado a [0.52, 0.68].

        Args:
            signal: Signal con confidence compuesta (drift + spike timing).

        Returns:
            Probabilidad de victoria ajustada por confianza (0-1).
        """
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0

        base_prob = 0.60
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.12  # ±6%
        prob = min(0.68, max(0.52, base_prob + adj))

        logger.debug(
            "DriftBoomCrash win_prob: base=%.3f, confidence=%.3f, adj=%.3f → final=%.3f",
            base_prob, confidence, adj, prob,
        )
        return round(prob, 4)