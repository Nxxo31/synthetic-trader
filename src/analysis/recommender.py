"""Recommendations engine — genera strings legibles para análisis y dashboard.

Convierte signals, scores, position sizes y estado del circuit breaker en
recomendaciones de texto claras para el trader o el dashboard.

Formato:
    SEÑAL LONG R_100 | Score: 0.78 | Entrada: 572.59 | SL: 566.42 | TP: 578.76
    | Stake: $2.50 (0.25% Kelly) | Confianza: ALTA | Circuit Breaker: OK
"""
from __future__ import annotations

import logging
from typing import Any

from src.strategies.base import Signal, SignalType
from src.risk.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


def confidence_label(score: float) -> str:
    """Convierte un score 0-1 en etiqueta de confianza."""
    if score >= 0.8:
        return "ALTA"
    if score >= 0.6:
        return "MEDIA"
    if score >= 0.4:
        return "BAJA"
    return "MUY_BAJA"


def stake_percentage(stake: float, capital: float) -> str:
    """Calcula el porcentaje del stake relativo al capital."""
    if capital <= 0:
        return "0.00%"
    return f"{(stake / capital) * 100:.2f}%"


class Recommender:
    """Genera recomendaciones legibles a partir de signals y estado del sistema.

    Args:
        capital: Capital actual de la cuenta (para calcular porcentajes)
    """

    def __init__(self, capital: float = 10000.0) -> None:
        self.capital = capital

    def generate_recommendation(
        self,
        signal: Signal,
        score: float,
        size: float,
        circuit_breaker: CircuitBreaker | None = None,
        capital: float | None = None,
    ) -> str:
        """Genera una recomendación legible para una señal dada.

        Args:
            signal: Signal generada por la estrategia
            score: Multi-factor score (0-1)
            size: Position size calculado por Kelly
            circuit_breaker: Circuit breaker state (optional)
            capital: Override del capital (opcional, default self.capital)

        Returns:
            String formateado con toda la info de la recomendación
        """
        cap = capital if capital is not None else self.capital

        # No signal → no recommendation
        if signal.type == SignalType.NO_SIGNAL:
            reason = signal.metadata.get("reason", "no_breakout") if signal.metadata else "no_breakout"
            return f"_sin_señal | {reason} | Score: {score:.2f}"

        # Signal direction
        direction = signal.type.value  # "LONG" or "SHORT"

        # Confidence label
        conf = confidence_label(score)

        # Stake percentage
        pct = stake_percentage(size, cap) if size > 0 else "0.00%"

        # Circuit breaker status
        if circuit_breaker is not None:
            cb_can, _ = circuit_breaker.can_trade()
            cb_status = "OK" if cb_can else "HALTED"
        else:
            cb_status = "N/A"

        # Format the recommendation string
        rec = (
            f"SEÑAL {direction} {signal.symbol} | "
            f"Score: {score:.2f} | "
            f"Entrada: {signal.entry_price:.2f} | "
            f"SL: {signal.stop_loss:.2f} | "
            f"TP: {signal.take_profit:.2f} | "
            f"Stake: ${size:.2f} ({pct} Kelly) | "
            f"Confianza: {conf} | "
            f"Circuit Breaker: {cb_status}"
        )

        logger.info("Recomendación: %s", rec)
        return rec

    def generate_batch(
        self,
        signals: list[tuple[Signal, float, float]],
        circuit_breaker: CircuitBreaker | None = None,
    ) -> list[str]:
        """Genera recomendaciones para un lote de signals.

        Args:
            signals: Lista de tuplas (signal, score, size)
            circuit_breaker: Circuit breaker state (optional)

        Returns:
            Lista de strings de recomendación
        """
        return [
            self.generate_recommendation(sig, score, size, circuit_breaker)
            for sig, score, size in signals
        ]


def generate_recommendation(
    signal: Signal,
    score: float,
    size: float,
    circuit_breaker: CircuitBreaker | None = None,
    capital: float = 10000.0,
) -> str:
    """Función de conveniencia — genera una recomendación sin instanciar Recommender.

    Args:
        signal: Signal generada por la estrategia
        score: Multi-factor score (0-1)
        size: Position size calculado por Kelly
        circuit_breaker: Circuit breaker state (optional)
        capital: Capital de la cuenta

    Returns:
        String formateado con la recomendación
    """
    rec = Recommender(capital=capital)
    return rec.generate_recommendation(signal, score, size, circuit_breaker)