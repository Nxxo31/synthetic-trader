"""Circuit breaker dual — protege contra rachas de pérdidas y drawdown diario.

Implementa dos triggers independientes:
  1. Pérdidas consecutivas (umbral configurable, default 3)
     - Cooldown progresivo: 2 pérdidas → 30 min, 3 pérdidas → 60 min,
       4+ pérdidas → 60 min + 5 min por pérdida extra
  2. Drawdown diario (umbral configurable, default 5%)
     - Halt inmediato al alcanzar el límite

El circuito se reinicia automáticamente al cambio de día (UTC).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import DailyStats

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerConfig:
    """Configuración del circuit breaker dual."""
    consecutive_losses_threshold: int = 3  # Trigger 1: pérdidas consecutivas
    daily_drawdown_threshold: float = 0.05  # Trigger 2: 5% drawdown diario
    # Cooldown en minutos basado en pérdidas consecutivas
    cooldown_base_minutes: int = 30  # base para 2 pérdidas
    cooldown_increment_minutes: int = 30  # extra por pérdida adicional (3→ +30min)
    cooldown_max_minutes: int = 60  # máximo de cooldown


@dataclass
class CircuitBreakerState:
    """Estado interno del circuit breaker."""
    last_loss_time: datetime | None = None
    consecutive_losses: int = 0
    is_halted: bool = False
    halt_reason: str = ""
    halt_until: datetime | None = None  # expiración del cooldown
    last_reset_date: str = ""  # YYYY-MM-DD para reset diario


class CircuitBreaker:
    """Circuit breaker dual para protección de trading.

    Args:
        config: configuración del circuit breaker
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitBreakerState()
        self._maybe_reset_daily()

    def _now(self) -> datetime:
        """Current UTC time (allow mocking in tests)."""
        return datetime.now(timezone.utc)

    def _today_str(self) -> str:
        """Fecha actual en formato YYYY-MM-DD (UTC)."""
        return self._now().strftime("%Y-%m-%d")

    def _maybe_reset_daily(self) -> None:
        """Reset diario automático al cambio de fecha."""
        today = self._today_str()
        if self.state.last_reset_date != today:
            logger.info("Circuit breaker: daily reset (new day: %s)", today)
            self.state.last_reset_date = today
            self.state.consecutive_losses = 0
            self.state.last_loss_time = None
            self.state.is_halted = False
            self.state.halt_reason = ""
            self.state.halt_until = None

    def _calculate_cooldown_minutes(self, consecutive_losses: int) -> int:
        """Calcula minutos de cooldown basado en pérdidas consecutivas.

        Args:
            consecutive_losses: número de pérdidas consecutivas actuales

        Returns:
            Minutos de cooldown (0 si no aplica)
        """
        if consecutive_losses < 2:
            return 0
        if consecutive_losses == 2:
            return self.config.cooldown_base_minutes
        # 3+ pérdidas: base + incremento por cada pérdida extra
        extra = consecutive_losses - 2
        minutes = self.config.cooldown_base_minutes + (
            extra * self.config.cooldown_increment_minutes
        )
        return min(minutes, self.config.cooldown_max_minutes)

    def update(
        self,
        loss: bool,
        current_balance: float,
        starting_balance: float,
    ) -> None:
        """Actualiza el estado tras un trade cerrado.

        Args:
            loss: True si el trade fue perdedor
            current_balance: balance actual después del trade
            starting_balance: balance inicial del día
        """
        self._maybe_reset_daily()

        if loss:
            self.state.consecutive_losses += 1
            self.state.last_loss_time = self._now()
            logger.warning(
                "Consecutive losses: %d (threshold: %d)",
                self.state.consecutive_losses,
                self.config.consecutive_losses_threshold,
            )
        else:
            # Reset contador de pérdidas consecutivas en win
            if self.state.consecutive_losses > 0:
                logger.info(
                    "Win after %d consecutive losses — resetting counter",
                    self.state.consecutive_losses,
                )
                self.state.consecutive_losses = 0
                self.state.last_loss_time = None

        # Evaluar triggers
        self._evaluate_triggers(current_balance, starting_balance)

    def _evaluate_triggers(
        self, current_balance: float, starting_balance: float
    ) -> None:
        """Evalúa ambos triggers y actualiza estado de halt."""
        # Reset previo
        was_halted = self.state.is_halted
        self.state.is_halted = False
        self.state.halt_reason = ""
        self.state.halt_until = None

        # Trigger 1: pérdidas consecutivas
        if self.state.consecutive_losses >= self.config.consecutive_losses_threshold:
            cooldown_min = self._calculate_cooldown_minutes(
                self.state.consecutive_losses
            )
            if self.state.last_loss_time is not None:
                cooldown_until = self.state.last_loss_time + timedelta(
                    minutes=cooldown_min
                )
                if self._now() < cooldown_until:
                    self.state.is_halted = True
                    self.state.halt_reason = (
                        f"Circuit breaker: {self.state.consecutive_losses} "
                        f"consecutive losses (cooldown {cooldown_min} min)"
                    )
                    self.state.halt_until = cooldown_until
                    logger.warning(
                        "HALTED: %s (until %s UTC)",
                        self.state.halt_reason,
                        self.state.halt_until.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    return  # Prioridad al cooldown por pérdidas consecutivas

        # Trigger 2: drawdown diario
        if starting_balance > 0:
            drawdown = (starting_balance - current_balance) / starting_balance
            if drawdown >= self.config.daily_drawdown_threshold:
                self.state.is_halted = True
                self.state.halt_reason = (
                    f"Daily drawdown limit: {drawdown:.2%} >= "
                    f"{self.config.daily_drawdown_threshold:.2%}"
                )
                logger.warning("HALTED: %s", self.state.halt_reason)
                return

        # Si salimos de halt, loggear transición
        if was_halted and not self.state.is_halted:
            logger.info("Circuit breaker: RESUMED trading (conditions cleared)")

    def can_trade(self) -> tuple[bool, str]:
        """Determina si se permite trading bajo las reglas actuales.

        Returns:
            Tupla (allowed: bool, reason: str)
        """
        self._maybe_reset_daily()

        if self.state.is_halted:
            # Si es un halt inmediato (p.ej. drawdown), halt_until es None
            if self.state.halt_until is not None:
                # Verificar si expiró el cooldown
                if self._now() >= self.state.halt_until:
                    logger.info(
                        "Circuit breaker: cooldown expired (%s UTC)",
                        self.state.halt_until.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    self.state.is_halted = False
                    self.state.halt_reason = ""
                    self.state.halt_until = None
                else:
                    remaining = (
                        self.state.halt_until - self._now()
                    ).total_seconds() / 60
                    return (
                        False,
                        f"HALTED: {self.state.halt_reason} "
                        f"(resumes in {max(0, int(remaining))} min)",
                    )
            else:
                # Halt inmediato (drawdown) — no hay cooldown, permanece hasta reset diario
                return False, f"HALTED: {self.state.halt_reason}"

        return True, "OK"

    def status(self) -> dict:
        """Retorna el estado actual para monitoreo/logging."""
        self._maybe_reset_daily()
        return {
            "consecutive_losses": self.state.consecutive_losses,
            "last_loss_time": (
                self.state.last_loss_time.isoformat()
                if self.state.last_loss_time
                else None
            ),
            "is_halted": self.state.is_halted,
            "halt_reason": self.state.halt_reason,
            "halt_until": (
                self.state.halt_until.isoformat()
                if self.state.halt_until
                else None
            ),
            "cooldown_minutes": (
                int((self.state.halt_until - self._now()).total_seconds() / 60)
                if self.state.is_halted and self.state.halt_until is not None
                else 0
            ),
            "today": self.state.last_reset_date,
        }