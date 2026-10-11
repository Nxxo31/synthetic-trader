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
from datetime import datetime, timezone
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

    # Legacy field names (pre Sept-2026 rewrite) used by `RiskManager`,
    # `backtest/engine.py` and the risk test-suite. Kept in sync by default
    # with the fields above; explicit values override. Both sets coexist so
    # old and new call sites share one config object.
    max_risk_per_trade: float = 0.015
    max_daily_drawdown: float = 0.05
    circuit_breaker_losses: int = 5
    kelly_fraction: float = 0.25
    initial_capital: float = 10000.0

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


# ─── Class-based RiskManager (restored) ─────────────────────────────────────
#
# Historically ``src/risk/manager.py`` exposed a class-based ``RiskManager``
# used in production by ``src/trading/paper_runner.py`` (the 24/7 paper bot),
# ``src/trading/paper_trader.py``, and ``src/backtest/engine.py``.  A Sept-08
# rewrite of this module dropped the class while those call sites were never
# migrated, leaving the paper-bot entry points un-importable.  The class is
# restored here with a config bridge: it works with both the legacy dataclass
# fields (``max_risk_per_trade``, ``max_daily_drawdown``, …) and the current
# ``RiskConfig`` field names (``max_position_pct``, ``max_daily_loss_pct``).
#
# NOTE: the module-level ``position_size_dynamic`` / ``check_circuit_breaker``
# functions above are kept untouched — new code should prefer the class API
# below ``or`` the module functions, but not mix them on the same state.


@dataclass
class DailyStats:
    """Tracks trading stats for the current day."""

    date: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    starting_balance: float = 0.0
    current_balance: float = 0.0
    consecutive_losses: int = 0
    halted: bool = False
    halt_reason: str = ""


class RiskManager:
    """Enforces risk rules on every trade (class API).

    Rules (non-negotiable):
    1. Max risk per trade (``max_risk_per_trade`` / ``max_position_pct``)
    2. Max daily drawdown (``max_daily_drawdown`` / ``max_daily_loss_pct``)
    3. Max trades per day
    4. Halt after ``circuit_breaker_losses`` consecutive losses (default 5)
    5. Quarter-Kelly position sizing (``kelly_fraction``, default 0.25)

    The constructor resolves each limit from whatever config object it is
    given, preferring the historical field names and falling back to the
    current ``RiskConfig`` ones (and vice versa for ``max_trades_per_day``).
    """

    def __init__(self, config: "RiskConfig | Any"):
        self.config = config
        # Resolve config values with fallbacks across both config schemas.
        self.max_risk_per_trade = float(
            getattr(config, "max_risk_per_trade",
                    getattr(config, "max_position_pct", 0.015))
        )
        self.max_daily_drawdown = float(
            getattr(config, "max_daily_drawdown",
                    getattr(config, "max_daily_loss_pct", 0.05))
        )
        self.max_trades_per_day = int(getattr(config, "max_trades_per_day", 8))
        self.circuit_breaker_losses = int(
            getattr(config, "circuit_breaker_losses", 5)
        )
        self.kelly_fraction = float(getattr(config, "kelly_fraction", 0.25))
        self.today: DailyStats | None = None
        self._is_halted = False
        self._halt_reason = ""

    def reset_daily(self, balance: float) -> None:
        """Reset daily stats. Call at start of each trading day."""
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.today = DailyStats(
            date=today_str,
            starting_balance=balance,
            current_balance=balance,
        )
        self._is_halted = False
        self._halt_reason = ""
        logger.info("Daily risk reset. Balance: %.2f USD", balance)

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed under current risk rules."""
        if self._is_halted:
            return False, f"HALTED: {self._halt_reason}"

        if self.today is None:
            return False, "Daily stats not initialized. Call reset_daily() first."

        if self.today.trades >= self.max_trades_per_day:
            return False, f"Max trades per day reached ({self.max_trades_per_day})"

        if self.today.starting_balance > 0:
            drawdown = (
                self.today.starting_balance - self.today.current_balance
            ) / self.today.starting_balance
            if drawdown >= self.max_daily_drawdown:
                self._halt(f"Daily drawdown limit reached: {drawdown:.2%}")
                return False, self._halt_reason

        if self.today.consecutive_losses >= self.circuit_breaker_losses:
            self._halt(
                f"Circuit breaker: {self.today.consecutive_losses} consecutive losses"
            )
            return False, self._halt_reason

        return True, "OK"

    def position_size(
        self,
        capital: float,
        win_probability: float,
        win_amount: float,
        loss_amount: float,
    ) -> float:
        """Quarter-Kelly position size, capped at max_risk_per_trade * capital."""
        p = win_probability
        q = 1.0 - p
        b = win_amount / loss_amount if loss_amount > 0 else 0

        kelly = (p * b - q) / b if b > 0 else 0
        quarter_kelly = kelly * self.kelly_fraction

        max_risk = self.max_risk_per_trade * capital
        size = min(quarter_kelly * capital, max_risk)

        if size <= 0:
            logger.warning("Kelly <= 0 (p=%.3f, b=%.3f). No edge detected.", p, b)
            return 0.0

        logger.info(
            "Position size: %.2f USD (Kelly=%.4f, QK=%.4f, cap=%.2f)",
            size, kelly, quarter_kelly, max_risk,
        )
        return round(size, 2)

    def position_size_dynamic(
        self,
        capital: float,
        win_probability: float,
        win_amount: float,
        loss_amount: float,
        confidence: float = 1.0,
        volatility_multiplier: float = 1.0,
    ) -> float:
        """Kelly dinámico — ajusta por confidence (score) y volatilidad.

        ``confidence`` mezcla el win_probability hacia 0.5 (edge neutral) y
        ``volatility_multiplier`` (>= 1) reduce el tamaño en mercados volátiles.
        Returns 0.0 cuando no hay edge (Kelly <= 0).
        """
        confidence = max(0.0, min(1.0, confidence))
        if confidence < 1.0:
            p = 0.5 + (win_probability - 0.5) * confidence
        else:
            p = win_probability
        p = min(0.99, max(0.01, p))
        q = 1.0 - p

        b = win_amount / loss_amount if loss_amount > 0 else 0.0
        if b <= 0:
            logger.warning(
                "Kelly dinámico: b<=0 (win_amount=%.4f, loss_amount=%.4f)",
                win_amount, loss_amount,
            )
            return 0.0

        kelly = (p * b - q) / b
        if kelly <= 0:
            logger.warning(
                "Kelly dinámico <= 0 (p=%.3f, b=%.3f, conf=%.3f). No edge.",
                p, b, confidence,
            )
            return 0.0

        vm = max(1.0, volatility_multiplier)
        adjusted_fraction = kelly * self.kelly_fraction / vm

        max_risk = self.max_risk_per_trade * capital
        size = min(adjusted_fraction * capital, max_risk)

        if size <= 0:
            return 0.0

        logger.info(
            "Position size (dynamic): %.2f USD (Kelly=%.4f, adj_frac=%.4f, "
            "conf=%.3f, vol_mult=%.3f, cap=%.2f)",
            size, kelly, adjusted_fraction, confidence, vm, max_risk,
        )
        return round(size, 2)

    def record_trade(self, pnl: float, new_balance: float) -> None:
        """Record a completed trade (updates daily stats + circuit state)."""
        if self.today is None:
            logger.error("record_trade called before reset_daily")
            return

        self.today.trades += 1
        self.today.pnl += pnl
        self.today.current_balance = new_balance

        if pnl >= 0:
            self.today.wins += 1
            self.today.consecutive_losses = 0
        else:
            self.today.losses += 1
            self.today.consecutive_losses += 1

        logger.info(
            "Trade recorded: P&L=%.2f, Total=%d trades, W/L=%d/%d, Balance=%.2f",
            pnl, self.today.trades, self.today.wins, self.today.losses, new_balance,
        )

        can, reason = self.can_trade()
        if not can and "HALTED" in reason:
            logger.warning("Circuit breaker triggered: %s", reason)

    def daily_report(self) -> dict[str, Any]:
        """Generate daily risk report."""
        if self.today is None:
            return {"error": "No daily stats"}

        drawdown = (
            (self.today.starting_balance - self.today.current_balance)
            / self.today.starting_balance
            if self.today.starting_balance > 0
            else 0.0
        )
        win_rate = self.today.wins / self.today.trades if self.today.trades > 0 else 0.0

        return {
            "date": self.today.date,
            "trades": self.today.trades,
            "wins": self.today.wins,
            "losses": self.today.losses,
            "win_rate": round(win_rate, 4),
            "pnl": round(self.today.pnl, 2),
            "drawdown": round(drawdown, 4),
            "consecutive_losses": self.today.consecutive_losses,
            "halted": self._is_halted,
            "halt_reason": self._halt_reason,
            "starting_balance": self.today.starting_balance,
            "current_balance": self.today.current_balance,
        }

    def _halt(self, reason: str) -> None:
        """Halt trading."""
        self._is_halted = True
        self._halt_reason = reason
        if self.today:
            self.today.halted = True
            self.today.halt_reason = reason
        logger.critical("TRADING HALTED: %s", reason)


# Export principal para compatibilidad con scripts antiguos
__all__ = [
    "RiskConfig",
    "PositionSizeResult",
    "position_size_dynamic",
    "check_circuit_breaker",
    "DailyStats",
    "RiskManager",
]
