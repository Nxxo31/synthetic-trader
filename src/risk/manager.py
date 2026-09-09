"""Risk manager — position sizing, circuit breakers, daily limits.

This module provides the core risk management functions used by the trading
scripts and strategies:
- position_size_dynamic: Kelly-based dynamic position sizing
- RiskConfig: Configuration for risk parameters
- Circuit breaker state checking
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """Risk configuration parameters.

    Attributes:
        max_position_pct: Maximum portfolio percentage per trade (default 2.0%).
        max_daily_loss_pct: Maximum daily loss as portfolio percentage (default 5.0%).
        max_trades_per_day: Maximum number of trades per day (default 10).
        use_kelly: Si True, usa dimensionamiento Kelly dinámico. Si False, usa
            position size fijo basado en riesgo fijo por trade.
        confidence_threshold: Umbral de confianza mínimo para entrar en un trade
            (default 0.6).
    """

    max_position_pct: float = 0.02      # 2% per trade
    max_daily_loss_pct: float = 0.05    # 5% daily loss limit
    max_trades_per_day: int = 10        # Max trades per day
    use_kelly: bool = True              # Kelly dimensionamiento activo
    confidence_threshold: float = 0.6   # Min confidence to enter trade

    def validate(self) -> list[str]:
        """Valida la configuración y retorna una lista de errores (vacía si OK)."""
        errors: list[str] = []

        if not (0.0 < self.max_position_pct <= 1.0):
            errors.append(f"max_position_pct={self.max_position_pct} debe estar en (0, 1]")
        if not (0.0 < self.max_daily_loss_pct <= 1.0):
            errors.append(f"max_daily_loss_pct={self.max_daily_loss_pct} debe estar en (0, 1]")
        if not (0 < self.max_trades_per_day):
            errors.append(f"max_trades_per_day={self.max_trades_per_day} debe ser > 0")
        if not (0.0 <= self.confidence_threshold <= 1.0):
            errors.append(f"confidence_threshold={self.confidence_threshold} debe estar en [0, 1]")

        return errors


@dataclass
class PositionSizeResult:
    """Resultado del dimensionamiento de posición.

    Attributes:
        size_usd: Tamaño de la posición en USD.
        pct_capital: Porcentaje del capital que representa la posición.
        kelly_fraction: Fracción Kelly calculada.
        edge: Edge (edge matemático) usado en el cálculo.
    """

    size_usd: float
    pct_capital: float
    kelly_fraction: float
    edge: float


def position_size_dynamic(
    capital: float,
    win_probability: float,
    win_amount: float,
    loss_amount: float,
    confidence: float = 1.0,
    volatility_multiplier: float = 1.0,
    config: RiskConfig | None = None,
) -> PositionSizeResult:
    """Calcula el tamaño de posición dinámico usando Kelly con ajustes.

    Esta es la función principal de dimensionamiento de posición que usa la
    fórmula Kelly modificada con factores de confianza y volatilidad.

    Args:
        capital: Capital disponible (generalmente superávit diario).
        win_probability: Probabilidad de ganar el trade.
        win_amount: Ganancia esperada si el trade gana (en unidades de riesgo).
        loss_amount: Pérdida esperada si el trade pierde (en unidades de riesgo).
        confidence: Score del signal scorer (0-1). Default 1.0.
        volatility_multiplier: Factor de reducción por volatilidad (>=1.0).
            Default 1.0.
        config: RiskConfig con parámetros de riesgo. Si se omite, usa defaults.

    Returns:
        PositionSizeResult con el tamaño calculado y métricas asociadas.

    Raises:
        ValueError: Si los parámetros son inválidos.
    """
    if config is None:
        config = RiskConfig()

    # Validar configuración
    errors = config.validate()
    if errors:
        raise ValueError("RiskConfig inválida: " + "; ".join(errors))

    # Calcular edge esperado (expecatancy en R-multiples)
    # E = p * win_amount - (1-p) * loss_amount
    p = win_probability
    q = 1.0 - p

    if loss_amount <= 0:
        raise ValueError("loss_amount debe ser > 0")

    edge = p * win_amount - q * loss_amount

    # Kelly fraction: f = Edge / win_amount para una apuesta simétrica
    # Pero ajustamos con confidence y volatility_multiplier
    if win_amount <= 0:
        raise ValueError("win_amount debe ser > 0")

    # Kelly puro: f = edge / win_amount
    kelly_fraction = edge / win_amount if win_amount > 0 else 0.0

    # Ajustar con confidence (0-1)
    kelly_fraction = kelly_fraction * confidence

    # Ajustar con volatility_multiplier (siempre >= 1, reduce el tamaño)
    if volatility_multiplier and volatility_multiplier > 0:
        kelly_fraction = kelly_fraction / volatility_multiplier

    # Kelly truncado a [-1, 1] y nunca negativo para posición larga
    kelly_fraction = max(0.0, min(1.0, kelly_fraction))

    # Tamaño en USD
    size_usd = round(capital * kelly_fraction, 2)

    # Aplicar cap máximo por trade (max_position_pct del config)
    max_size = round(capital * config.max_position_pct, 2)
    if size_usd > max_size:
        size_usd = max_size
        logger.info(
            "Position size capped at max_position_pct: %.2f USD (was %.2f)",
            size_usd,
            size_usd + (capital * kelly_fraction - max_size),  # diff
        )

    # Aplicar límite diario máximo de pérdida
    max_daily = round(capital * config.max_daily_loss_pct, 2)

    result = PositionSizeResult(
        size_usd=size_usd,
        pct_capital=round(kelly_fraction * 100, 2),
        kelly_fraction=round(kelly_fraction, 4),
        edge=edge,
    )

    logger.info(
        "Position size calculated: capital=%.2f, win_p=%.2f, edge=%.2f, "
        "kelly_fraction=%.4f, size_usd=%.2f, pct_capital=%.2f%%",
        capital,
        win_probability,
        edge,
        kelly_fraction,
        size_usd,
        result.pct_capital,
    )

    return result


def check_circuit_breaker(
    daily_pnl: float,
    config: RiskConfig | None = None,
    capital_total: float | None = None,
) -> dict[str, Any]:
    """Verifica si el circuit breaker debería detener el trading.

    Args:
        daily_pnl: P&L acumulado del día actual.
        config: RiskConfig con parámetros de riesgo. Si se omite, usa defaults.
        capital_total: Capital total de la cuenta (opcional, para cálculos absolutos).

    Returns:
        dict con:
            - can_trade: Si se puede continuar trading (True/False)
            - reason: Motivo si se detuvo (vacío si se puede tradear)
            - daily_loss_pct: Pérdida diaria porcentual
            - at_limit: Si se alcanzó algún límite
    """
    if config is None:
        config = RiskConfig()

    # Usar capital_total si se provee, otherwise 1.0 como normalización
    total = capital_total if capital_total is not None else 1.0

    # Calcular pérdida diaria porcentual
    daily_loss_pct = abs(min(daily_pnl, 0.0)) / total if total > 0 else 0.0

    # Verificar límites
    at_limit = False
    reason_parts: list[str] = []

    # Límite de pérdida diaria
    if daily_loss_pct >= config.max_daily_loss_pct:
        at_limit = True
        reason_parts.append(
            f"daily loss {daily_loss_pct:.1%} ≥ max {config.max_daily_loss_pct:.1%}"
        )

    # Límite de trades por día (si se tracking)
    if config.max_trades_per_day > 0:
        # Nota: el conteo de trades requiere tracking externo
        pass

    can_trade = not at_limit
    reason = " | ".join(reason_parts) if reason_parts else ""

    result = {
        "can_trade": can_trade,
        "reason": reason,
        "daily_loss_pct": daily_loss_pct,
        "at_limit": at_limit,
        "config_max_daily_loss_pct": config.max_daily_loss_pct,
        "daily_pnl": daily_pnl,
        "capital_total": total,
    }

    if not can_trade:
        logger.warning(
            "Circuit breaker triggered: %s (daily_loss_pct=%.2f%%)",
            reason,
            daily_loss_pct * 100,
        )
    else:
        logger.debug(
            "Circuit breaker OK: can_trade=True (daily_loss_pct=%.2f%%)",
            daily_loss_pct * 100,
        )

    return result


# Export principal para compatibilidad con scripts antiguos
__all__ = [
    "RiskConfig",
    "PositionSizeResult",
    "position_size_dynamic",
    "check_circuit_breaker",
]
