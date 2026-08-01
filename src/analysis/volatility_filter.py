"""Volatility filter — ATR ratio vs ATR moving average.

Computes the Average True Range (ATR) and compares it to its own moving
average to detect volatility expansion vs contraction. Breakouts that
occur during volatility expansion (ATR > ATR_MA) are higher quality than
those in compressed volatility.

For Range Break Index synthetic instruments, ATR provides the primary
volatility signal since real volume is absent.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class VolatilityFilter:
    """Filtro de volatilidad basado en ATR ratio.

    Args:
        atr_period: período del ATR (default 14)
        ma_period: período de la MA del ATR (default 20)
    """

    def __init__(self, atr_period: int = 14, ma_period: int = 20) -> None:
        self.atr_period = atr_period
        self.ma_period = ma_period

    def atr(self, data: pd.DataFrame) -> pd.Series:
        """Calcula el Average True Range (ATR) como serie.

        ATR = media móvil del True Range, donde:
            TR = max(high-low, |high-prev_close|, |low-prev_close|)

        Args:
            data: DataFrame con columns high, low, close

        Returns:
            Serie de ATR (NaN al inicio hasta tener datos suficientes)
        """
        high = data["high"].astype(float)
        low = data["low"].astype(float)
        close = data["close"].astype(float)

        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        tr_frame = pd.concat([tr1, tr2, tr3], axis=1)
        tr: pd.Series = pd.Series(tr_frame.max(axis=1))
        atr = tr.rolling(window=self.atr_period, min_periods=1).mean()

        return pd.Series(atr)

    def atr_ratio(self, data: pd.DataFrame) -> float:
        """Ratio ATR_actual / ATR_media.

        Un ratio > 1 indica volatilidad en expansión (favorable para breakouts).
        Un ratio < 1 indica volatilidad en contracción.

        Args:
            data: DataFrame con columns high, low, close

        Returns:
            Ratio ATR/ATR_MA (1.0 = neutral). Retorna 1.0 si no hay datos suficientes.
        """
        atr_series = self.atr(data)

        if len(atr_series) < self.ma_period + 1:
            return 1.0

        recent_atr = float(atr_series.iloc[-1])
        avg_atr = float(atr_series.iloc[-(self.ma_period + 1):-1].mean())

        if avg_atr <= 0 or np.isnan(avg_atr) or np.isnan(recent_atr):
            return 1.0

        ratio = recent_atr / avg_atr
        logger.debug("ATR ratio: %.3f (recent=%.4f, avg=%.4f)", ratio, recent_atr, avg_atr)
        return ratio

    def volatility_score(self, data: pd.DataFrame) -> float:
        """Score de volatilidad en rango 0-0.2.

        - ratio >= 1.3 → score 0.2 (fuerte expansión de volatilidad)
        - ratio >= 1.0 → score proporcional entre 0.1 y 0.2
        - ratio < 1.0  → score proporcional, mínimo 0 (ratio 0.7 → 0.0)

        Args:
            data: DataFrame con columns high, low, close

        Returns:
            Score 0-0.2
        """
        ratio = self.atr_ratio(data)

        if ratio >= 1.3:
            score = 0.2
        elif ratio >= 1.0:
            # 1.0 → 0.1, 1.3 → 0.2 (lineal)
            score = 0.1 + (ratio - 1.0) * (1 / 3)
        else:
            # 0.7 → 0.0, 1.0 → 0.1 (lineal)
            score = max(0.0, (ratio - 0.7) * (1 / 3))

        return round(min(0.2, score), 4)

    def current_atr(self, data: pd.DataFrame) -> float:
        """Retorna el valor actual del ATR (útil para SL/TP dinámicos).

        Args:
            data: DataFrame con columns high, low, close

        Returns:
            Valor actual del ATR. Retorna 0.0 si no hay datos.
        """
        atr_series = self.atr(data)
        val = float(atr_series.iloc[-1])
        return val if not np.isnan(val) else 0.0
