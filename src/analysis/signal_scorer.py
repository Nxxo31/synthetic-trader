"""Signal scorer — combina penetración, volumen y volatilidad en score 0-1.

Multi-factor scoring system for breakout quality:

    score = penetration_score (0-0.4)    # qué tan profundo es el breakout
          + volume_score     (0-0.3)     # confirmación de volumen
          + volatility_score (0-0.2)     # expansión de volatilidad

Línea de decisión:
    - score >= ENTRY_THRESHOLD (default 0.6) → señal válida para entrar
    - score < threshold → NO_SIGNAL (no hay suficiente confluencia)

El score también alimenta el Kelly dinámico como medida de confianza.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from src.analysis.range_detector import ChannelResult, RangeDetector
from src.analysis.volatility_filter import VolatilityFilter
from src.analysis.volume_analyzer import VolumeAnalyzer

logger = logging.getLogger(__name__)

# Pesos de cada factor en el score final
WEIGHT_PENETRATION: float = 0.4
WEIGHT_VOLUME: float = 0.3
WEIGHT_VOLATILITY: float = 0.2

# Threshold por defecto para considerar una señal válida
DEFAULT_ENTRY_THRESHOLD: float = 0.6


@dataclass(frozen=True)
class ScoreBreakdown:
    """Desglose del score multi-factor para auditoría."""
    penetration_score: float
    volume_score: float
    volatility_score: float
    total_score: float
    direction: str       # "LONG", "SHORT", "NONE"
    passes_threshold: bool

    def __str__(self) -> str:
        return (
            f"Score: {self.total_score:.3f} "
            f"(pen={self.penetration_score:.3f} + "
            f"vol={self.volume_score:.3f} + "
            f"atr={self.volatility_score:.3f}) "
            f"dir={self.direction} "
            f"pass={'YES' if self.passes_threshold else 'NO'}"
        )


class SignalScorer:
    """Combina 3 factores en un score 0-1 de confluencia.

    Args:
        range_detector: detector de canal (default RangeDetector)
        volume_analyzer: analizador de volumen (default VolumeAnalyzer)
        volatility_filter: filtro de volatilidad (default VolatilityFilter)
        entry_threshold: score mínimo para entrar (default 0.6)
    """

    def __init__(
        self,
        range_detector: RangeDetector | None = None,
        volume_analyzer: VolumeAnalyzer | None = None,
        volatility_filter: VolatilityFilter | None = None,
        entry_threshold: float = DEFAULT_ENTRY_THRESHOLD,
    ) -> None:
        self.range_detector = range_detector or RangeDetector()
        self.volume_analyzer = volume_analyzer or VolumeAnalyzer()
        self.volatility_filter = volatility_filter or VolatilityFilter()
        self.entry_threshold = entry_threshold

    def score(self, data: pd.DataFrame) -> ScoreBreakdown:
        """Calcula el score multi-factor para la última candle.

        Args:
            data: DataFrame con columns high, low, close (y opcionalmente volume)

        Returns:
            ScoreBreakdown con el score desglosado y dirección de la señal
        """
        if len(data) < 2:
            return ScoreBreakdown(0.0, 0.0, 0.0, 0.0, "NONE", False)

        # Factor 1: penetración del breakout
        channel = self.range_detector.detect(data)
        last_close = float(data.iloc[-1]["close"])
        pen_score, direction = self.range_detector.penetration_depth(last_close, channel)

        # Factor 2: confirmación de volumen
        vol_score = self.volume_analyzer.volume_score(data)

        # Factor 3: expansión de volatilidad (ATR ratio)
        atr_score = self.volatility_filter.volatility_score(data)

        total = round(pen_score + vol_score + atr_score, 4)
        total = min(1.0, max(0.0, total))
        passes = total >= self.entry_threshold and direction != "NONE"

        logger.debug(
            "SignalScorer: pen=%.3f vol=%.3f atr=%.3f total=%.3f dir=%s pass=%s",
            pen_score, vol_score, atr_score, total, direction, passes,
        )

        return ScoreBreakdown(
            penetration_score=pen_score,
            volume_score=vol_score,
            volatility_score=atr_score,
            total_score=total,
            direction=direction,
            passes_threshold=passes,
        )
