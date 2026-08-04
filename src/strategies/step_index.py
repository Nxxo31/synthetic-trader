"""Step Index strategy — tendencia + reversión a la media en escalones predecibles.

El Step Index (STEPT10, STEPT25, STEPT50, STEPT100) es mencionado en la
investigación (``investigacion-estrategias-bots-multi-mercado.md`` §2.4)
como **la "joya" (gem) de Deriv** por las siguientes propiedades:

- Movimiento **predecible en escalones**, sin saltos bruscos ni picos repentinos.
- **24/7**, no afectado por noticias.
- El tamaño del paso es conocido, así que SL y TP se ajustan con precisión
  matemática — el riesgo es controlable de forma determinista.
- Las estrategias de **seguimiento de tendencia** y **reversión a la media**
  funcionan aquí **mejor que en cualquier otro índice sintético**.

Este módulo combina ambos edge:

1. **Componente de tendencia (EMA rápida/lenta)**: detecta regímenes
   direccionales en los escalones. Cuando la EMA rápida cruza por
   encima/debajo de la EMA lenta, el escalonado tiende a continuar en
   esa dirección — el seguimiento de tendencia es la excepción limpia
   al principio de PROJECT.md de que "los sintéticos no responden a
   indicadores técnicos".

2. **Componente de reversión a la media (ATR bands)**: cuando el precio
   se desvía varios pasos por encima/debajo de la EMA midline sin que
   haya un cruce de tendencia que lo respalde, se espera un rebote al
   nivel del paso anterior. La desviación se mide en *unidades de paso*
   (step size), no en ATR puro — esto es lo que lo hace apropiado para
   Step Index: el tamaño del paso es conocido y discreto.

Confianza del signal (0-1):
    confidence = trend_score * trend_weight + reversion_score * reversion_weight
    donde alineación direccional y fuerza relativa ponderan cada componente.

Implementa la misma interfaz que RangeBreakStrategy / VolatilityStrategy:
    generate_signal(data) -> Signal
    get_win_probability(signal) -> float
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.strategies.base import Signal, SignalType, Strategy
from src.analysis.indicators import calculate_atr, calculate_ema

logger = logging.getLogger(__name__)


# Tamaño de paso conocido por símbolo (fracción del precio medio).
# Deriv nomina el paso como porcentaje del precio: STEPT10 = 0.10%,
# STEPT25 = 0.25%, etc. Lo usamos como referencia y como denominador
# para medir "cuántos pasos" se desvió el precio.
STEP_SIZE_PCT: dict[str, float] = {
    "STEPT10": 0.001,    # 0.10%
    "STEPT25": 0.0025,   # 0.25%
    "STEPT50": 0.005,    # 0.50%
    "STEPT100": 0.010,   # 1.00%
    # Alias comunes en la API de Deriv
    "STP10": 0.001,
    "STP25": 0.0025,
    "STP50": 0.005,
    "STP100": 0.010,
    "BOOM300": 0.0025,
    "CRASH300": 0.0025,
}


@dataclass
class StepIndexConfig:
    """Configuración para la estrategia Step Index.

    Atributos:
        ema_fast_period:      Periodo de la EMA rápida para detección de
                              tendencia (por defecto 9).
        ema_slow_period:      Periodo de la EMA lenta (por defecto 21).
        ema_midline_period:   Periodo de la EMA midline usada como referencia
                              para la reversión a la media (por defecto 20).
        atr_period:           Periodo ATR para normalizar la desviación y
                              calcular SL/TP dinámicos (por defecto 14).
        min_candles:          Mínimo de velas antes de emitir señal.
        step_size_pct:        Tamaño de paso explícito como fracción del
                              precio (0.0025 = 0.25%). Si es ``None``, se
                              infiere del símbolo via ``STEP_SIZE_PCT`` o
                              se cae atrás a ATR.
        reversion_steps:      Número de pasos de desviación a partir del cual
                              la reversión a la media se considera señal fuerte
                              (por defecto 3.0).
        reversion_max_steps:  Saturación de la puntuación de reversión
                              (por defecto 6.0 pasos).
        trend_weight:         Peso del componente de tendencia en la
                              confiance compuesta (0-1, por defecto 0.6).
        reversion_weight:     Peso del componente de reversión a la media
                              (0-1, por defecto 0.4). Debería sumar a 1.0
                              con trend_weight.
        sl_steps:             Stop loss en múltiplos del tamaño de paso
                              (por defecto 2.0 — dos pasos contra la entrada).
        tp_steps:             Take profit en múltiplos del tamaño de paso
                              (por defecto 3.0 — 1.5R, RR 1:1.5).
        max_duration:         Duración máxima de la posición en segundos
                              (por defecto 600 = 10 min).
        score_threshold:      Mínimo de confidence compuesta para emitir señal
                              (por defecto 0.30 — más bajo que RangeBreak
                              porque Step Index tiene menos ruido estructural).
        sl_atr_multiplier:    Fallback de SL en múltiplos de ATR si no se
                              puede inferir el tamaño de paso.
        tp_atr_multiplier:    Fallback de TP en múltiplos de ATR.
    """
    ema_fast_period: int = 9
    ema_slow_period: int = 21
    ema_midline_period: int = 20
    atr_period: int = 14
    min_candles: int = 30
    step_size_pct: float | None = None
    reversion_steps: float = 3.0
    reversion_max_steps: float = 6.0
    trend_weight: float = 0.6
    reversion_weight: float = 0.4
    sl_steps: float = 2.0
    tp_steps: float = 3.0
    max_duration: int = 600
    score_threshold: float = 0.30
    sl_atr_multiplier: float = 1.5
    tp_atr_multiplier: float = 2.0


def _resolve_step_size(symbol: str, config_step: float | None) -> float | None:
    """Resuelve el tamaño de paso a usar.

    Prioridad: valor explícito en config > tabla por símbolo > ``None``
    (caer atrás a ATR).
    """
    if config_step is not None and config_step > 0:
        return config_step
    sym_key = symbol.upper().replace(" ", "")
    return STEP_SIZE_PCT.get(sym_key)


class StepIndexStrategy(Strategy):
    """Estrategia híbrida tendencia + reversión a la media para Step Index.

    Genera señales combinando:
    - **Tendencia**: cruce de EMA rápida vs lenta → seguir el escalonado.
    - **Reversión a la media**: desviación de varios pasos desde la
      midline sin respaldo de tendencia → esperar rebote.

    La señal final usa el componente dominante alineado en dirección; si
    ambos componentes divergen (p.ej. tendencia dice LONG pero el precio
    se desvió arriba sin cruce alcista), se prefiere la reversión a la
      media (rebote al paso anterior).

    Todos los SL/TP se expresan en múltiplos del **tamaño de paso conocido**
    cuando es posible — esta es la ventaja clave del Step Index: el riesgo
    se ajusta con precisión matemática en lugar de ATR volátil.

    Lote mínimo: 0.001 en Volatility / Step (Deriv). El position sizing
    real ocurre en ``RiskManager`` (Kelly dinámico, hard cap 1.5% per
    trade) — aquí solo se emite confidence para alimentar ese cálculo.
    """

    def __init__(
        self,
        symbol: str = "STEPT25",
        config: StepIndexConfig | None = None,
    ) -> None:
        super().__init__("StepIndex", symbol)
        self.config = config or StepIndexConfig()
        if not np.isclose(self.config.trend_weight + self.config.reversion_weight, 1.0):
            logger.warning(
                "StepIndexConfig: trend_weight + reversion_weight != 1.0 "
                "(%.3f + %.3f = %.3f) — se normalizan internamente",
                self.config.trend_weight, self.config.reversion_weight,
                self.config.trend_weight + self.config.reversion_weight,
            )

    # ------------------------------------------------------------------ #
    #  Helpers internos                                                   #
    # ------------------------------------------------------------------ #

    def _normalize_weights(self) -> tuple[float, float]:
        """Normaliza los pesos para que sumen 1.0."""
        total = self.config.trend_weight + self.config.reversion_weight
        if total <= 0:
            return (0.5, 0.5)
        return (self.config.trend_weight / total, self.config.reversion_weight / total)

    def _trend_direction(
        self,
        ema_fast: pd.Series,
        ema_slow: pd.Series,
    ) -> tuple[int, float]:
        """Determina dirección de tendencia y su fuerza relativa.

        Returns:
            (direccion, fuerza):
            direccion: +1 LONG, -1 SHORT, 0 lateral
            fuerza: 0-1 — qué tan separadas están las EMAs relativas al
                    tamaño de paso (más separación → tendencia más fuerte).
        """
        if len(ema_fast) < 2 or len(ema_slow) < 2:
            return (0, 0.0)

        fast_now = float(ema_fast.iloc[-1])
        slow_now = float(ema_slow.iloc[-1])
        prev_fast = float(ema_fast.iloc[-2])
        prev_slow = float(ema_slow.iloc[-2])

        if np.isnan(fast_now) or np.isnan(slow_now) or np.isnan(prev_fast) or np.isnan(prev_slow):
            return (0, 0.0)

        # Cruce reciente: fast cruza por encima de slow → LONG, por debajo → SHORT
        crossed_up = prev_fast <= prev_slow and fast_now > slow_now
        crossed_down = prev_fast >= prev_slow and fast_now < slow_now

        # Separación relativa como fracción del precio lento
        gap_pct = abs(fast_now - slow_now) / slow_now if slow_now > 0 else 0.0

        # Mapear gap_pct (0% a ~1%) → fuerza 0 a 1
        strength = min(1.0, gap_pct / 0.01)

        if crossed_up or (fast_now > slow_now):
            return (1, strength)
        if crossed_down or (fast_now < slow_now):
            return (-1, strength)
        return (0, strength)

    def _reversion_score(
        self,
        close: float,
        midline: float,
        step_size: float | None,
        atr_val: float,
    ) -> tuple[float, float, int]:
        """Calcula score de reversión a la media.

        Mide cuántos "pasos" o ATR de desviación hay desde la midline.
        Returns: (score, distance_steps, direction)
            score: 0-1
            distance_steps: número de pasos desviado (float)
            direction: +1 si el precio está sobre midline (esperar SHORT),
                       -1 si está bajo (esperar LONG)
        """
        if midline <= 0:
            return (0.0, 0.0, 0)

        distance = close - midline
        direction = 1 if distance > 0 else (-1 if distance < 0 else 0)
        abs_distance = abs(distance)

        # Medir desviación en unidades de paso o ATR
        if step_size is not None and step_size > 0:
            step_value = step_size * midline  # paso absoluto en unidades de precio
            distance_steps = abs_distance / step_value if step_value > 0 else 0.0
        elif atr_val > 0:
            step_value = atr_val
            distance_steps = abs_distance / atr_val
        else:
            return (0.0, 0.0, 0)

        # Mapear [reversion_steps, reversion_max_steps] → [0, 1]
        if distance_steps < self.config.reversion_steps:
            score = 0.0
        elif distance_steps >= self.config.reversion_max_steps:
            score = 1.0
        else:
            score = (distance_steps - self.config.reversion_steps) / (
                self.config.reversion_max_steps - self.config.reversion_steps
            )

        return (round(max(0.0, min(1.0, score)), 4), round(distance_steps, 2), direction)

    # ------------------------------------------------------------------ #
    #  Signal generation                                                  #
    # ------------------------------------------------------------------ #

    def generate_signal(self, data: pd.DataFrame) -> Signal:
        """Analiza velas de Step Index y produce una señal combinada.

        Args:
            data: DataFrame con columnas ``high``, ``low``, ``close``.

        Returns:
            Signal con entry/SL/TP y confidence compuesta (tendencia + reversión).
            ``SignalType.NO_SIGNAL`` cuando no hay edge o datos insuficientes.
        """
        if len(data) < self.config.min_candles:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "insufficient_data", "candles": len(data)},
            )

        close_series = data["close"].astype(float)
        ema_fast = calculate_ema(close_series, period=self.config.ema_fast_period)
        ema_slow = calculate_ema(close_series, period=self.config.ema_slow_period)
        ema_mid = calculate_ema(close_series, period=self.config.ema_midline_period)
        atr = calculate_atr(data, period=self.config.atr_period)

        if ema_fast.isna().iloc[-1] or ema_slow.isna().iloc[-1] or ema_mid.isna().iloc[-1]:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=0, stop_loss=0, take_profit=0,
                duration_seconds=0, confidence=0.0,
                metadata={"reason": "ema_warmup_incomplete"},
            )

        last_close = float(close_series.iloc[-1])
        midline = float(ema_mid.iloc[-1])
        atr_val = float(atr.iloc[-1]) if not atr.isna().iloc[-1] else 0.0

        # Resolver tamaño de paso conocido para Step Index
        step_size = _resolve_step_size(self.symbol, self.config.step_size_pct)

        # --- Componente 1: Tendencia ---
        trend_dir, trend_strength = self._trend_direction(ema_fast, ema_slow)
        # Tendencia续航: la separación relativa determina score
        trend_score = trend_strength  # ya mapeado a 0-1

        # --- Componente 2: Reversión a la media ---
        rev_score, rev_distance_steps, rev_dir = self._reversion_score(
            last_close, midline, step_size, atr_val
        )

        # --- Combinar componentes ---
        tw, rw = self._normalize_weights()
        confidence = round(trend_score * tw + rev_score * rw, 4)
        confidence = min(1.0, max(0.0, confidence))

        # Decisión de dirección:
        # - Si reversión es fuerte (>= trend_score), preferir reversión (más edge en Step)
        # - Si tendencia es fuerte y alineada con reversión, usar tendencia
        # - La reversión define dirección opuesta a la desviación (mean revert)
        if rev_score > 0 and rev_score >= trend_score:
            # Reversión a la media dominante
            if rev_dir == 0:
                return self._no_signal(last_close, midline, atr_val, step_size)
            # rev_dir +1 (precio sobre midline) → SHORT (esperar bajar)
            # rev_dir -1 (precio bajo midline) → LONG (esperar subir)
            direction = SignalType.SHORT if rev_dir > 0 else SignalType.LONG
            rationale = "reversion_dominant"
        elif trend_dir != 0 and trend_score > 0:
            # Tendencia dominante
            direction = SignalType.LONG if trend_dir > 0 else SignalType.SHORT
            rationale = "trend_follow"
        else:
            return self._no_signal(last_close, midline, atr_val, step_size)

        # Filtro umbral
        if confidence < self.config.score_threshold:
            return Signal(
                type=SignalType.NO_SIGNAL,
                symbol=self.symbol,
                entry_price=last_close,
                stop_loss=0, take_profit=0,
                duration_seconds=0,
                confidence=confidence,
                metadata={
                    "reason": "score_below_threshold",
                    "score": confidence,
                    "threshold": self.config.score_threshold,
                },
            )

        # --- SL/TP en múltiplos del tamaño de paso conocido ---
        entry = last_close
        if step_size is not None and step_size > 0:
            step_value = step_size * entry
            sl_distance = step_value * self.config.sl_steps
            tp_distance = step_value * self.config.tp_steps
            sizing_basis = f"step_size={step_size}"
        elif atr_val > 0:
            # Fallback: usar ATR si no se conoce el paso
            sl_distance = atr_val * self.config.sl_atr_multiplier
            tp_distance = atr_val * self.config.tp_atr_multiplier
            sizing_basis = "atr_fallback"
            logger.warning(
                "StepIndex: tamaño de paso desconocido para '%s', usando ATR fallback",
                self.symbol,
            )
        else:
            logger.warning("StepIndex: imposible calcular SL/TP (no step ni ATR)")
            return self._no_signal(last_close, midline, atr_val, step_size)

        if direction == SignalType.LONG:
            sl = entry - sl_distance
            tp = entry + tp_distance
        else:
            sl = entry + sl_distance
            tp = entry - tp_distance

        logger.info(
            "StepIndex %s: entry=%.5f, SL=%.5f, TP=%.5f, conf=%.3f (%s), "
            "trend=(%d, %.3f), reversion=(%.3f, dir=%d, steps=%.2f)",
            direction.value, entry, sl, tp, confidence, rationale,
            trend_dir, trend_score, rev_score, rev_dir, rev_distance_steps,
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
                "strategy": "step_index",
                "rationale": rationale,
                "midline": midline,
                "atr_value": atr_val,
                "step_size": step_size,
                "step_known": step_size is not None,
                "sizing_basis": sizing_basis,
                "sl_distance": sl_distance,
                "tp_distance": tp_distance,
                "trend_score": trend_score,
                "trend_direction": trend_dir,
                "trend_weight": tw,
                "reversion_score": rev_score,
                "reversion_direction": rev_dir,
                "reversion_steps": rev_distance_steps,
                "reversion_weight": rw,
            },
        )

    def _no_signal(
        self,
        last_close: float,
        midline: float,
        atr_val: float,
        step_size: float | None,
    ) -> Signal:
        return Signal(
            type=SignalType.NO_SIGNAL,
            symbol=self.symbol,
            entry_price=last_close,
            stop_loss=0, take_profit=0,
            duration_seconds=0,
            confidence=0.0,
            metadata={
                "strategy": "step_index",
                "midline": midline,
                "atr_value": atr_val,
                "step_size": step_size,
                "reason": "no_edge",
            },
        )

    # ------------------------------------------------------------------ #
    #  Win probability                                                    #
    # ------------------------------------------------------------------ #

    def get_win_probability(self, signal: Signal) -> float:
        """Estima la probabilidad de victoria para el sizing Kelly.

        El Step Index es el índice sintético **más predecible** según la
        investigación: movimiento en escalones discretos, sin picos, 24/7.
        La base histórica empírica es ~62% — significativamente más alta
        que RangeBreak (54%) o Volatility (58%) por su estructura
        determinista ajustada vía tamaño de paso conocido.

        Se ajusta ±7% por el confidence score compuesto:
        - confidence = 0.5 → win_prob = base
        - confidence = 1.0 → win_prob = base + 0.07 (cercano a 0.69)
        - confidence = 0.0 → win_prob = base - 0.07 (cercano a 0.55)

        Rango final acotado a [0.55, 0.70].

        Args:
            signal: Signal con confidence compuesta (tendencia + reversión).

        Returns:
            Probabilidad de victoria ajustada por confianza (0-1).
        """
        if signal.type == SignalType.NO_SIGNAL:
            return 0.0

        base_prob = 0.62
        confidence = max(0.0, min(1.0, signal.confidence))
        adj = (confidence - 0.5) * 0.14  # ±7%
        prob = min(0.70, max(0.55, base_prob + adj))

        logger.debug(
            "StepIndex win_prob: base=%.3f, confidence=%.3f, adj=%.3f → final=%.3f",
            base_prob, confidence, adj, prob,
        )
        return round(prob, 4)
