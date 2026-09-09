"""Volume analyzer — volume ratio vs moving average.

Synthetic indices on Deriv often lack a true `volume` column in candle
data. When volume is unavailable, this analyzer falls back to using the
candle range (high - low) as a proxy for activity, which correlates with
tick count / activity for synthetic instruments.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


class VolumeAnalyzer:
    """Analiza volumen relativo a su media móvil.

    Args:
        window_size: período de la MA del volumen (default 20)
    """

    def __init__(self, window_size: int = 20) -> None:
        self.window_size = window_size

    def volume_ratio(self, data: pd.DataFrame) -> float:
        """Calcula el ratio volumen_actual / volumen_medio.

        Un ratio > 1 indica volumen por encima del promedio (confirmación
        de breakout). Un ratio < 1 indica volumen débil.

        Si no hay columna `volume`, usa (high - low) como proxy de actividad.

        Args:
            data: DataFrame con columns high, low y opcionalmente volume

        Returns:
            Ratio de volumen (>= 0). 1.0 = volumen promedio.
        """
        series = self._get_volume_series(data)
        if series is None or len(series) < self.window_size:
            return 1.0  # neutral — no confirmation but no rejection either

        recent = series.iloc[-1]
        avg = series.iloc[-(self.window_size + 1):-1].mean()

        if avg <= 0:
            return 1.0

        ratio = float(recent / avg)
        logger.debug("Volume ratio: %.3f (recent=%.4f, avg=%.4f)", ratio, recent, avg)
        return ratio

    def volume_score(self, data: pd.DataFrame) -> float:
        """Score de volumen en rango 0-0.3.

        - ratio >= 1.5 → score 0.3 (fuerte confirmación)
        - ratio >= 1.0 → score proporcional entre 0.15 y 0.3
        - ratio < 1.0  → score proporcional, mínimo 0 (ratio 0.5 → 0.0)

        Args:
            data: DataFrame con OHLCV

        Returns:
            Score 0-0.3
        """
        ratio = self.volume_ratio(data)

        if ratio >= 1.5:
            score = 0.3
        elif ratio >= 1.0:
            # 1.0 → 0.15, 1.5 → 0.3 (lineal)
            score = 0.15 + (ratio - 1.0) * 0.3
        else:
            # 0.5 → 0.0, 1.0 → 0.15 (lineal)
            score = max(0.0, (ratio - 0.5) * 0.3)

        return round(min(0.3, score), 4)

    @staticmethod
    def _get_volume_series(data: pd.DataFrame) -> pd.Series | None:
        """Obtiene la serie de volumen real o proxy (high - low)."""
        if "volume" in data.columns:
            vol = data["volume"]
            last_vol = float(vol.iloc[-1])
            if not bool(vol.isna().all()) and last_vol != 0:
                return vol.astype(float)
        # Proxy: rango del candle como medida de actividad
        if "high" in data.columns and "low" in data.columns:
            return (data["high"] - data["low"]).astype(float)
        return None
