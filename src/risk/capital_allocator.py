"""Capital Allocator — divide el capital en reserva y superávit diario, calcula micro-stakes.

Estrategia de gestión de capital en dos buckets:

  1. **Reserva** (default 80%): Capital protegido que NUNCA se arriesga en un día.
     Sobrevive a drawdowns y rebuilds. Solo se toca al final del día para reequilibrar.

  2. **Superávit diario** (default 20%): Capital operativo del día. Es el único
     capital que se arriesga en trades. Dentro de este bucket, cada trade usa
     position_size_dynamic (Kelly dinámico) para determinar el micro-stake.

Beneficio:
  - Limita la exposición diaria máxima al superávit, no al capital total.
  - El RiskManager sigue aplicando sus caps (1.5%-3% per trade) dentro del superávit.
  - Protege la reserva de rachas malas en un solo día.

Uso típico:

    allocator = CapitalAllocator(config, risk_manager)
    allocator.reset_daily(capital_total=10000.0)
    stake = allocator.calculate_micro_stake(
        win_probability=0.6, win_amount=2.0, loss_amount=1.0,
        confidence=0.8, volatility_multiplier=1.2,
    )
    allocator.record_trade(pnl=-50.0)
    allocator.daily_report()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.risk.manager import RiskManager

logger = logging.getLogger(__name__)

__all__ = [
    "CapitalAllocatorConfig",
    "CapitalAllocatorState",
    "CapitalAllocator",
]


@dataclass
class CapitalAllocatorConfig:
    """Configuración del Capital Allocator.

    Args:
        reserva_pct: Fracción del capital total reservada (no arriesgada). Default 0.80.
        superávit_diario_pct: Fracción del capital destinada al trading diario.
            Default 0.20.
        initial_capital: Capital base para inicialización (si no se pasa explícito).
        min_surplus: Superávit mínimo absoluto en USD. Si el cálculo da menos,
            se fuerza a este mínimo (defensa contra capital total muy bajo).
        rebalance_daily: Si True, al llamar reset_daily() el superávit se
            recalcula desde el capital total actual (reserva puede crecer con
            ganancias). Si False, el superávit del día siempre es el 20% del
            initial_capital (más conservador).
    """

    reserva_pct: float = 0.80
    superávit_diario_pct: float = 0.20
    initial_capital: float = 10000.0
    min_surplus: float = 10.0
    rebalance_daily: bool = True

    def validate(self) -> list[str]:
        """Valida la configuración y retorna una lista de errores (vacía si OK).

        Reglas:
          - reserva_pct + superávit_diario_pct deben sumar 1.0
          - Ambos deben estar en (0, 1)
          - initial_capital y min_surplus deben ser positivos
        """
        errors: list[str] = []
        total = self.reserva_pct + self.superávit_diario_pct
        if abs(total - 1.0) > 1e-6:
            errors.append(
                f"reserva_pct ({self.reserva_pct}) + superávit_diario_pct "
                f"({self.superávit_diario_pct}) = {total:.4f} ≠ 1.0"
            )
        for name, val in [
            ("reserva_pct", self.reserva_pct),
            ("superávit_diario_pct", self.superávit_diario_pct),
        ]:
            if not (0.0 < val < 1.0):
                errors.append(f"{name}={val} debe estar en (0, 1)")
        if self.initial_capital <= 0:
            errors.append(f"initial_capital={self.initial_capital} debe ser > 0")
        if self.min_surplus < 0:
            errors.append(f"min_surplus={self.min_surplus} debe ser >= 0")
        return errors


@dataclass
class CapitalAllocatorState:
    """Estado interno del Capital Allocator para el día actual.

    Attributes:
        date: Fecha del día en formato YYYY-MM-DD (UTC).
        capital_total: Capital total gestionado (reserva + superávit operacional).
        reserva: Capital reservado (protegido, no arriesgado).
        superávit_diario: Capital disponible para trading del día.
        superávit_usado: Superávit ya comprometido en trades abiertos o perdidos.
        trades_count: Número de micro-stakes calculados en el día.
        total_pnl: P&L acumulado del día (sobre el superávit).
        is_active: Si el allocator está listo para calcular stakes.
    """

    date: str = ""
    capital_total: float = 0.0
    reserva: float = 0.0
    superávit_diario: float = 0.0
    superávit_usado: float = 0.0
    trades_count: int = 0
    total_pnl: float = 0.0
    is_active: bool = False

    def reset(
        self,
        capital_total: float,
        reserva: float,
        superávit_diario: float,
        date: str,
    ) -> None:
        """Reinicia el estado para un nuevo día de trading."""
        self.date = date
        self.capital_total = capital_total
        self.reserva = reserva
        self.superávit_diario = superávit_diario
        self.superávit_usado = 0.0
        self.trades_count = 0
        self.total_pnl = 0.0
        self.is_active = True

    @property
    def superávit_disponible(self) -> float:
        """Superávit que aún no se ha arriesgado: superávit_diario - superávit_usado."""
        disponible = self.superávit_diario - self.superávit_usado
        return max(0.0, round(disponible, 2))


class CapitalAllocator:
    """Gestiona la división del capital y el cálculo de micro-stakes diarios.

    El allocator divide el capital total en dos buckets:
      - **Reserva** (default 80%): nunca se arriesga intradía.
      - **Superávit diario** (default 20%): único capital expuesto a trading.

    Dentro del superávit, delega el dimensionamiento de cada trade a
    ``RiskManager.position_size_dynamic`` (Kelly dinámico con confidence y
    volatility_multiplier).

    Args:
        config: Configuración del allocator (porcentajes, capital inicial, etc.).
        risk_manager: Instancia de RiskManager con position_size_dynamic().
            Si se omite, se debe asignar antes de calcular micro-stakes.

    Raises:
        ValueError: Si la configuración es inválida (reserva + superávit ≠ 1.0).
    """

    def __init__(
        self,
        config: CapitalAllocatorConfig,
        risk_manager: RiskManager | None = None,
    ) -> None:
        errors = config.validate()
        if errors:
            raise ValueError("CapitalAllocatorConfig inválida: " + "; ".join(errors))

        self.config = config
        self.risk_manager = risk_manager
        self.state = CapitalAllocatorState()

    # ------------------------------------------------------------------ #
    #  Lifecycle — reset diario
    # ------------------------------------------------------------------ #

    def reset_daily(self, capital_total: float | None = None) -> None:
        """Reinicia el allocator para un nuevo día de trading.

        Divide ``capital_total`` en reserva y superávit según los porcentajes
        configurados. Si ``rebalance_daily=True``, el superávit se recalcula
        desde el capital actual (la reserva crece con las ganancias).

        Args:
            capital_total: Capital total actual. Si se omite, usa:
                - ``config.initial_capital`` si el allocator no está activo (primera init).
                - capital total del día anterior + P&L si ``rebalance_daily=True``.
                - capital total del día anterior (sin P&L) si ``rebalance_daily=False``.
        """
        if capital_total is None:
            if not self.state.is_active:
                capital_total = self.config.initial_capital
            elif self.config.rebalance_daily:
                # Usar capital total del día anterior (+ P&L acumulado)
                capital_total = self.state.capital_total + self.state.total_pnl
            else:
                # Sin rebalance: el capital base no cambia, P&L no afecta el total
                capital_total = self.state.capital_total

        capital_total = round(capital_total, 2)
        if capital_total <= 0:
            logger.warning(
                "reset_daily: capital_total=%.2f <= 0, no se puede reiniciar", capital_total
            )
            self.state.is_active = False
            return

        reserva = round(capital_total * self.config.reserva_pct, 2)
        superávit = round(capital_total * self.config.superávit_diario_pct, 2)

        # Asegurar superávit mínimo absoluto
        if superávit < self.config.min_surplus:
            superávit = min(self.config.min_surplus, capital_total)
            reserva = round(capital_total - superávit, 2)

        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        self.state.reset(
            capital_total=capital_total,
            reserva=reserva,
            superávit_diario=superávit,
            date=date_str,
        )

        logger.info(
            "Capital Allocator reset diario — Total: %.2f | Reserva: %.2f (%.0f%%) | "
            "Superávit: %.2f (%.0f%%)",
            capital_total, reserva, self.config.reserva_pct * 100,
            superávit, self.config.superávit_diario_pct * 100,
        )

    # ------------------------------------------------------------------ #
    #  Micro-stake calculation
    # ------------------------------------------------------------------ #

    def calculate_micro_stake(
        self,
        win_probability: float,
        win_amount: float,
        loss_amount: float,
        confidence: float = 1.0,
        volatility_multiplier: float = 1.0,
        capital_override: float | None = None,
    ) -> float:
        """Calcula el micro-stake diario usando position_size_dynamic del RiskManager.

        El capital base para el Kelly es el **superávit disponible** del día,
        no el capital total. Esto acota automáticamente la exposición diaria.

        Args:
            win_probability: Probabilidad base de ganar.
            win_amount: Ganancia esperada si el trade gana.
            loss_amount: Pérdida esperada si el trade pierde.
            confidence: Score del signal scorer (0-1). Default 1.0.
            volatility_multiplier: Factor de reducción por volatilidad (>=1.0).
                Default 1.0.
            capital_override: Si se provee, usa este capital en lugar del
                superávit disponible. Útil para escenarios de testing donde se
                quiere dimensionar sobre un capital distinto.

        Returns:
            Tamaño de micro-stake en USD, acotado al superávit disponible.
            0.0 si no hay edge, no hay superávit, o el RiskManager falta.
        """
        if not self.state.is_active:
            logger.warning("calculate_micro_stake: allocator no activo (reset_daily no llamado)")
            return 0.0

        if self.risk_manager is None:
            logger.error("calculate_micro_stake: risk_manager no asignado")
            return 0.0

        capital_base = (
            self.state.superávit_disponible
            if capital_override is None
            else float(capital_override)
        )

        if capital_base <= 0:
            logger.warning(
                "calculate_micro_stake: superávit disponible=%.2f <= 0, no se puede size",
                self.state.superávit_disponible,
            )
            return 0.0

        # Delegar al position_size_dynamic del RiskManager
        raw_size = self.risk_manager.position_size_dynamic(
            capital=capital_base,
            win_probability=win_probability,
            win_amount=win_amount,
            loss_amount=loss_amount,
            confidence=confidence,
            volatility_multiplier=volatility_multiplier,
        )

        # Acotar al superávit disponible (no exceder lo que queda del día)
        if capital_override is None and raw_size > self.state.superávit_disponible:
            raw_size = round(self.state.superávit_disponible, 2)
            logger.info(
                "Micro-stake acotado a superávit disponible: %.2f", raw_size
            )

        if raw_size <= 0:
            return 0.0

        # Snapshot del disponible antes de marcar el uso (para logging)
        disponible_antes = self.state.superávit_disponible

        # Marcar el superávit como usado
        self.state.superávit_usado = round(
            self.state.superávit_usado + raw_size, 2
        )
        self.state.trades_count += 1

        logger.info(
            "Micro-stake %d: %.2f USD (capital_base=%.2f, disponible: %.2f → %.2f)",
            self.state.trades_count,
            raw_size,
            capital_base,
            disponible_antes,
            self.state.superávit_disponible,
        )

        return raw_size

    # ------------------------------------------------------------------ #
    #  Trade recording
    # ------------------------------------------------------------------ #

    def record_trade(self, pnl: float) -> None:
        """Registra el resultado de un trade y ajusta el superávit usado.

        Los P&L positivos liberan superávit (se puede arriesgar más);
        los negativos consumen superávit (se reduce lo disponible).

        Al arriesgar un stake, el superávit_usado ya se incrementó en
        ``calculate_micro_stake``. Al cerrar el trade:
          - El stake sale de superávit_usado (se libera).
          - El P&L se suma al superávit_diario (crece con ganancias,
            mengua con pérdidas) y a total_pnl.

        Args:
            pnl: Resultado del trade en USD (positivo = ganancia).
        """
        if not self.state.is_active:
            logger.warning("record_trade: allocator no activo, ignorando P&L=%.2f", pnl)
            return

        # El superávit_diario se actualiza con el P&L neto del trade
        self.state.superávit_diario = round(self.state.superávit_diario + pnl, 2)
        self.state.total_pnl = round(self.state.total_pnl + pnl, 2)

        logger.info(
            "Trade registrado: P&L=%.2f | Superávit: %.2f | Disponible: %.2f | "
            "Total P&L día: %.2f",
            pnl,
            self.state.superávit_diario,
            self.state.superávit_disponible,
            self.state.total_pnl,
        )

    # ------------------------------------------------------------------ #
    #  Configuration & state exposure
    # ------------------------------------------------------------------ #

    def get_config(self) -> dict:
        """Retorna la configuración actual como diccionario serializable."""
        return {
            "reserva_pct": self.config.reserva_pct,
            "superávit_diario_pct": self.config.superávit_diario_pct,
            "initial_capital": self.config.initial_capital,
            "min_surplus": self.config.min_surplus,
            "rebalance_daily": self.config.rebalance_daily,
        }

    def get_state(self) -> dict:
        """Retorna el estado actual del allocator como diccionario serializable.

        Incluye:
          - Configuración (de get_config)
          - Estado del día: capital_total, reserva, superávit, disponible, usado
          - Métricas: trades_count, total_pnl, return_pct
          - Flags: is_active, has_risk_manager
        """
        state = self.state
        return_pct = 0.0
        if state.capital_total > 0:
            return_pct = state.total_pnl / state.capital_total

        return {
            # Config
            "config": self.get_config(),
            # State
            "date": state.date,
            "capital_total": round(state.capital_total, 2),
            "reserva": round(state.reserva, 2),
            "superávit_diario": round(state.superávit_diario, 2),
            "superávit_usado": round(state.superávit_usado, 2),
            "superávit_disponible": state.superávit_disponible,
            "trades_count": state.trades_count,
            "total_pnl": state.total_pnl,
            "return_pct": round(return_pct, 6),
            "is_active": state.is_active,
            "has_risk_manager": self.risk_manager is not None,
        }

    def daily_report(self) -> dict:
        """Alias semántico de get_state() para compatibilidad con RiskManager.

        Devuelve el snapshot completo del día para logging / dashboard.
        """
        return self.get_state()

    @property
    def superávit_disponible(self) -> float:
        """Shortcut: superávit disponible para nuevos trades."""
        return self.state.superávit_disponible

    @property
    def reserva(self) -> float:
        """Shortcut: capital en reserva (protegido)."""
        return self.state.reserva

    @property
    def is_active(self) -> bool:
        """Shortcut: si el allocator está activo y listo para calcular stakes."""
        return self.state.is_active
